"""TraceZero: the autonomous immune system for production code.

Run:  uvicorn main:app --reload          (then open http://127.0.0.1:8000)

  /shop/*  the target e-commerce API, wrapped by the TraceZero immune layer
  /api/*   TraceZero control plane: SSE stream, chaos, shields, deploy, PRs
  /        the War Room UI
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before agent_engine reads env vars

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from agent_engine import MODEL, TraceZero  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="TraceZero", version="1.0")
tz = TraceZero()

CHAOS = {
    "empty_cart": {
        "id": "empty_cart",
        "name": "Mobile v4.2 rollout",
        "story": "New iOS build fires checkout on app resume with an empty cart",
        "expected": "ZeroDivisionError",
        "active": False,
    },
    "null_coupon": {
        "id": "null_coupon",
        "name": "Partner API integration",
        "story": "Affiliate partner sends \"coupon_code\": null",
        "expected": "AttributeError",
        "active": False,
    },
}


# --------------------------------------------------------------------------
# TraceZero immune layer: wraps every /shop route.
#   success          -> record into the shadow-traffic buffer
#   shielded crash   -> graceful safe-mode response (amber)
#   new crash        -> 500 + open incident + start the healing pipeline
# --------------------------------------------------------------------------
def immune(route: str):
    def decorator(handler):
        # No functools.wraps: FastAPI would read the inner signature and
        # treat `payload` as the body param instead of passing `request`.
        async def wrapper(request: Request):
            try:
                payload = await request.json()
            except Exception:
                raise HTTPException(400, "body must be JSON")
            started = time.perf_counter()
            try:
                result = handler(payload)
            except Exception as exc:  # the whole point
                latency = round((time.perf_counter() - started) * 1000, 2)
                shield = tz.shield_for(exc)
                if shield:
                    return JSONResponse(
                        status_code=202,
                        headers={"X-TraceZero": "shielded", "X-TraceZero-Shield": shield["id"]},
                        content={
                            "status": "accepted_safe_mode",
                            "order_id": f"ORD-{uuid.uuid4().hex[:8].upper()}",
                            "message": "Order accepted in safe mode; final pricing will be confirmed by email.",
                            "shield": shield["id"],
                            "incident": shield["incident_id"],
                            "latency_ms": latency,
                        },
                    )
                incident = tz.report_crash(exc, payload, route)
                return JSONResponse(
                    status_code=500,
                    headers={"X-TraceZero": "error"},
                    content={"error": type(exc).__name__, "detail": str(exc),
                             "incident": incident.id, "latency_ms": latency},
                )
            tz.record_success(route, payload, result)
            return JSONResponse(
                headers={"X-TraceZero": "ok"},
                content={"order_id": f"ORD-{uuid.uuid4().hex[:8].upper()}", **result,
                         "latency_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
        wrapper.__name__ = handler.__name__
        return wrapper
    return decorator


# ------------------------------ /shop -------------------------------------
@app.post("/shop/checkout")
@immune("POST /shop/checkout")
def shop_checkout(payload: dict):
    return tz.runtime.module("store_logic").checkout(payload)


@app.post("/shop/refund")
@immune("POST /shop/refund")
def shop_refund(payload: dict):
    return tz.runtime.module("refund_logic").process_refund(payload)


@app.get("/shop/catalog")
def shop_catalog():
    return tz.runtime.module("store_logic").CATALOG


# ------------------------------ /api --------------------------------------
@app.get("/api/stream")
async def stream(request: Request):
    queue, history = tz.bus.subscribe()

    async def events():
        try:
            yield "retry: 2000\n\n"
            for event in history:
                yield f"data: {json.dumps(event, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            tz.bus.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/state")
def state():
    return {
        "mode": tz.mode,
        "model": MODEL,
        "stats": tz.stats,
        "mttr": tz.mttr_summary(),
        "shadow_buffer": len(tz.shadow),
        "chaos": list(CHAOS.values()),
        "shields": list(tz.shields.values()),
        "incidents": [i.to_dict() for i in tz.incidents.values()],
    }


@app.post("/api/chaos/{bug}")
def inject_chaos(bug: str, enable: bool = True):
    if bug not in CHAOS:
        raise HTTPException(404, f"unknown chaos bug {bug}")
    CHAOS[bug]["active"] = enable
    c = CHAOS[bug]
    tz.bus.publish("chaos.toggled", stage="chaos", level="danger" if enable else "info",
                   title=f"Chaos {'injected' if enable else 'stopped'}: {c['name']}",
                   detail=c["story"], chaos=c)
    return c


@app.post("/api/shields/{shield_id}/{action}")
def toggle_shield(shield_id: str, action: str):
    if action not in ("lift", "arm"):
        raise HTTPException(400, "action must be lift or arm")
    try:
        return tz.set_shield(shield_id, active=(action == "arm"))
    except KeyError:
        raise HTTPException(404, "shield not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/incidents/{incident_id}/deploy")
async def deploy(incident_id: str):
    if incident_id not in tz.incidents:
        raise HTTPException(404, "incident not found")
    try:
        return (await tz.deploy(incident_id)).to_dict()
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/incidents/{incident_id}/pr")
async def open_pr(incident_id: str):
    if incident_id not in tz.incidents:
        raise HTTPException(404, "incident not found")
    try:
        pr = await tz.open_pr(incident_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {k: v for k, v in pr.items() if k != "changes"}


@app.post("/api/reset")
async def reset():
    for c in CHAOS.values():
        c["active"] = False
    await tz.reset()
    return {"ok": True}


# ------------------------------ website -----------------------------------
PAGES = {
    "/": "index.html",
    "/warroom": "warroom.html",
    "/how-it-works": "how-it-works.html",
    "/store": "store.html",
    "/about": "about.html",
}


def _page(filename: str):
    def handler():
        return FileResponse(BASE_DIR / "pages" / filename, headers={"Cache-Control": "no-cache"})
    handler.__name__ = f"page_{filename.split('.')[0].replace('-', '_')}"
    return handler


for route, filename in PAGES.items():
    app.get(route, include_in_schema=False)(_page(filename))

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
