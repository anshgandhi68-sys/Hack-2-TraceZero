# TraceZero: the autonomous immune system for production code

When production code crashes, TraceZero raises a reversible shield in milliseconds so users stop seeing 500s. Two AI agents then fight over the fix. The patch is proven against real recent traffic, sibling services get pre-patched for the same bug, and the fix is hot-deployed with no restart. A PR with a full postmortem is the last step.

```
crash ─► DETECT ─► SHIELD (~250ms) ─► RED TEST ─► SURGEON ⚔ BREAKER ─► SHADOW REPLAY ─► VACCINATE TWINS ─► VERIFIED ─► HOT-DEPLOY ─► PR
```

## What happens on stage

1. **Live traffic.** The War Room sends real requests to `/shop/checkout` every 1.5s. The heartbeat chart stays green.
2. **Inject chaos.** The bugs are already in `store_logic.py`. Chaos changes the *shape of the traffic* to wake them up:
   - **Bug 1, Mobile v4.2 rollout:** empty carts → `ZeroDivisionError` in `average_unit_price()`
   - **Bug 2, Partner API:** `"coupon_code": null` → `AttributeError` in `normalize_coupon()`
3. **Instant Shield.** The first crash returns a red 500. Every request after it gets a graceful safe-mode `202` (amber). Operators can lift or re-arm a shield with one click.
4. **Red test.** The exact crashing payload becomes a pytest. It fails in an isolated temp-dir sandbox.
5. **Surgeon vs Breaker.** The Surgeon writes a minimal patch. The Breaker writes adversarial tests to break it. pytest referees. In round 1 the Breaker wins with `qty: 0` lines or a numeric coupon, and the Surgeon fixes the whole class of bad input in round 2.
6. **Shadow traffic replay.** The last 15 successful real requests plus the crash requests are replayed against the patch. Outputs are compared byte-for-byte with what production actually returned, giving a score like **16/16 passed, 0 regressions**.
7. **Twin-bug vaccination.** An AST scanner finds the same anti-pattern in `refund_logic.py`, which has never crashed. The twin is patched, checked against poison input, and staged in the same PR.
8. **Approve & hot-deploy.** A human clicks once. The module is swapped in memory with zero downtime, the shield retires, and the chart goes green.
9. **Open GitHub PR.** The PR body is a full postmortem: timeline, the battle rounds, replay confidence, vaccinated twins and the regression tests that were added.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000. Wait about 10s so the shadow buffer can fill, then press **Inject chaos bug 1**.

## Modes

| Mode | How | What drives the agents |
|---|---|---|
| Playbook (default without a key) | no `ANTHROPIC_API_KEY`, or `TRACEZERO_MODE=demo` | Deterministic playbook. It works offline and never fails on stage. |
| Live | `ANTHROPIC_API_KEY` set (`TRACEZERO_MODE=auto`) | Claude (`claude-opus-5-5`) writes the patches, tests and twin vaccines. If a model call fails, that step falls back to the playbook. |

Copy `.env.example` to `.env` to configure. A real PR needs `GITHUB_TOKEN` + `GITHUB_REPO`; without them the PR modal shows a simulated PR.

## Files

| File | Role |
|---|---|
| `main.py` | FastAPI: `/shop/*` target API behind the TraceZero immune layer, `/api/*` control plane + SSE, `/` UI |
| `agent_engine.py` | Healing pipeline: event bus, shields, Surgeon/Breaker, pytest sandbox, shadow replay, AST twin scanner, hot-swap runtime, PR builder |
| `store_logic.py` | Target checkout service with 2 latent bugs |
| `refund_logic.py` | Sibling service with the twin bugs |
| `index.html` | Single-file War Room UI (Tailwind + Chart.js + Lucide via CDN) |

## Design notes

- **Patches are hot-swapped in memory** and never written to disk. `uvicorn --reload` therefore never restarts mid-demo, and **Reset demo** restores the original buggy code at any time.
- **The pytest sandbox** uses `tempfile.TemporaryDirectory`, `sys.executable -m pytest` and junit XML, with `subprocess.run` in a worker thread. It behaves the same on macOS, Linux and Windows PowerShell, where asyncio subprocesses break under uvicorn's reload loop.
- **Shadow replay is meaningful** because pricing logic is pure: identical input must give identical output, so any diff counts as a regression.
