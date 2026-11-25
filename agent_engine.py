"""TraceZero self-healing engine.

Pipeline for every new production crash fingerprint:

  0. DETECT     locate the faulting frame, fingerprint it, open an incident
  1. SHIELD     register a reversible guardrail in < 1s (no more 500s)
  2. REPRODUCE  turn the crashing request into a red pytest
  3. BATTLE     Surgeon agent patches, Breaker agent attacks, pytest referees
                in an isolated temp dir (up to 3 rounds)
  4. REPLAY     replay recent real traffic + the crash against the patch and
                diff outputs against what production actually returned
  5. VACCINATE  AST-scan sibling services for the same anti-pattern and
                pre-patch the twins
  6. VERIFIED   a human approves a hot-deploy (no restart), the shield retires,
                and TraceZero opens a PR with a full postmortem.

Runs against Claude when ANTHROPIC_API_KEY is set (TRACEZERO_MODE=auto|live).
Without a key, or with TRACEZERO_MODE=demo, it uses a deterministic playbook
so the stage demo can't fail on network or API problems.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import difflib
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import types
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROTECTED_MODULES = ["store_logic", "refund_logic"]

MODEL = os.getenv("TRACEZERO_MODEL", "claude-opus-5-5")
MODE = os.getenv("TRACEZERO_MODE", "auto").lower()  # auto | live | demo
PACE = float(os.getenv("TRACEZERO_PACE", "1.0"))  # theatrical delay multiplier
BASELINE_MTTR_MIN = float(os.getenv("TRACEZERO_BASELINE_MTTR_MIN", "45"))
AUTO_DEPLOY = os.getenv("TRACEZERO_AUTODEPLOY", "0") == "1"
MAX_ROUNDS = 3


async def beat(seconds: float) -> None:
    if PACE > 0:
        await asyncio.sleep(seconds * PACE)


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:4].upper()}"


# --------------------------------------------------------------------------
# Event bus (fan-out to every SSE subscriber, with history for late joiners)
# --------------------------------------------------------------------------
class EventBus:
    def __init__(self) -> None:
        self.history: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.seq = 0

    def publish(self, type_: str, **data) -> dict:
        self.seq += 1
        event = {"seq": self.seq, "ts": time.time(), "type": type_, **data}
        self.history.append(event)
        if len(self.history) > 600:
            self.history = self.history[-600:]
        for queue in list(self.subscribers):
            queue.put_nowait(event)
        return event

    def subscribe(self) -> tuple[asyncio.Queue, list[dict]]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue, list(self.history)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)


# --------------------------------------------------------------------------
# Runtime: the live, hot-swappable copies of the protected modules.
# Patches are applied in memory, so nothing on disk changes mid-demo and
# uvicorn --reload never restarts the server underneath the SSE stream.
# --------------------------------------------------------------------------
class Runtime:
    def __init__(self) -> None:
        self.original = {
            name: (BASE_DIR / f"{name}.py").read_text(encoding="utf-8")
            for name in PROTECTED_MODULES
        }
        self.sources: dict[str, str] = {}
        self.modules: dict[str, types.ModuleType] = {}
        self.reset()

    @staticmethod
    def build(name: str, source: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__file__ = str(BASE_DIR / f"{name}.py")
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        return module

    def reset(self) -> None:
        self.sources = dict(self.original)
        self.modules = {n: self.build(n, s) for n, s in self.sources.items()}

    def module(self, name: str) -> types.ModuleType:
        return self.modules[name]

    def hot_swap(self, name: str, source: str) -> None:
        module = self.build(name, source)  # raises before swapping if broken
        self.modules[name] = module
        self.sources[name] = source


# --------------------------------------------------------------------------
# Source helpers
# --------------------------------------------------------------------------
def find_function(source: str, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def function_source(source: str, name: str) -> str:
    node = find_function(source, name)
    if node is None:
        return ""
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1: node.end_lineno])


def replace_function(source: str, name: str, new_function: str) -> str:
    node = find_function(source, name)
    if node is None:
        raise ValueError(f"function {name} not found")
    lines = source.splitlines(keepends=True)
    block = new_function.strip("\n") + "\n"
    return "".join(lines[: node.lineno - 1]) + block + "".join(lines[node.end_lineno:])


def extract_code(text: str) -> str:
    match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip("\n")


def extract_function(code: str, name: str) -> str | None:
    """Pull exactly one top-level def <name> out of model output."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    lines = code.splitlines()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return None


def side_by_side(before: str, after: str) -> list[dict]:
    left, right = before.splitlines(), after.splitlines()
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(None, left, right)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"l": left[i1 + k], "r": right[j1 + k], "kind": "eq"})
        else:
            for a, b in zip_longest(range(i1, i2), range(j1, j2)):
                rows.append({
                    "l": left[a] if a is not None else None,
                    "r": right[b] if b is not None else None,
                    "kind": tag,
                })
    return rows


def unified(before: str, after: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


def describe_payload(payload: dict) -> str:
    items = payload.get("items") or []
    units = sum(int(i.get("qty", 0)) for i in items if isinstance(i, dict))
    coupon = payload.get("coupon_code", "")
    coupon_label = "null coupon" if coupon is None else (coupon.strip() or "no coupon") if isinstance(coupon, str) else repr(coupon)
    return f"{len(items)} lines / {units} units / {coupon_label}"


# --------------------------------------------------------------------------
# Isolated pytest runner (works on macOS, Linux and Windows PowerShell):
#  - plain subprocess.run in a worker thread; asyncio subprocesses break on
#    Windows' Selector event loop that uvicorn --reload uses
#  - sys.executable so the venv's pytest is used
#  - junit XML for per-test results instead of scraping stdout
# --------------------------------------------------------------------------
def run_pytest_sync(modules: dict[str, str], test_source: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="tracezero_", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        for name, source in modules.items():
            (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
        (tmp_path / "test_tracezero.py").write_text(test_source, encoding="utf-8")
        junit = tmp_path / "report.xml"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=tmp, PYTHONIOENCODING="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", "test_tracezero.py"]
        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "tests": [], "passed": 0, "failed": 0, "output": "pytest timed out"}

        tests = []
        if junit.exists():
            for case in ET.parse(junit).getroot().iter("testcase"):
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                message = ""
                if failure is not None:
                    message = (failure.get("message") or failure.text or "").strip().splitlines()
                    message = message[0][:240] if message else "failed"
                tests.append({"name": case.get("name"), "passed": failure is None, "message": message})
        passed = sum(1 for t in tests if t["passed"])
        output = (proc.stdout + proc.stderr)[-3000:]
        return {
            "ok": bool(tests) and passed == len(tests) and proc.returncode == 0,
            "tests": tests, "passed": passed, "failed": len(tests) - passed, "output": output,
        }


# --------------------------------------------------------------------------
# Twin-bug scanner (pure AST, no model needed)
# --------------------------------------------------------------------------
STR_METHODS = {"strip", "lstrip", "rstrip", "upper", "lower", "title", "split",
               "replace", "startswith", "endswith", "encode", "casefold"}


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _guarded(fn: ast.FunctionDef, names: set[str]) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)) and _mentions(node.test, names):
            return True
    return False


def classify(exc: BaseException) -> str:
    if isinstance(exc, ZeroDivisionError):
        return "unguarded-division"
    if isinstance(exc, AttributeError) and "NoneType" in str(exc):
        return "null-dereference"
    return "unknown"


PATTERN_LABEL = {
    "unguarded-division": "Unguarded division by a value that can be zero",
    "null-dereference": "String method called on a value that can be None",
    "unknown": "Unclassified failure",
}


def scan_twins(sources: dict[str, str], pattern: str, exclude: tuple[str, str]) -> list[dict]:
    findings = []
    for module, source in sources.items():
        tree = ast.parse(source)
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if (module, fn.name) == exclude:
                continue
            params = {a.arg for a in fn.args.args}
            hit = None
            if pattern == "null-dereference":
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in STR_METHODS:
                        base = node.func.value
                        while isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
                            base = base.func.value
                        if isinstance(base, ast.Name) and base.id in params and not _guarded(fn, {base.id}):
                            hit = (node, f"`{base.id}` can be None, but `{ast.unparse(node)}` runs with no guard")
                            break
            elif pattern == "unguarded-division":
                for node in ast.walk(fn):
                    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv)):
                        if isinstance(node.right, ast.Constant):
                            continue
                        names = {n.id for n in ast.walk(node.right) if isinstance(n, ast.Name)} - {"sum", "len", "int", "float"}
                        if names and _guarded(fn, names):
                            continue
                        hit = (node, f"divides by `{ast.unparse(node.right)}`, which is zero for empty input")
                        break
            if hit:
                node, reason = hit
                findings.append({
                    "id": short_id("TWN"),
                    "module": module,
                    "file": f"{module}.py",
                    "function": fn.name,
                    "line": node.lineno,
                    "arity": len(fn.args.args),
                    "reason": reason,
                    "pattern": pattern,
                    "source": function_source(source, fn.name),
                    "patch": None,
                    "vaccinated": False,
                })
    return findings


# --------------------------------------------------------------------------
# Deterministic playbook (offline mode + fallback when the model misbehaves)
# Round 1 patches are deliberately naive so the Breaker wins a round.
# --------------------------------------------------------------------------
PLAYBOOK = {
    "average_unit_price": {
        "diagnosis": "average_unit_price() divides by total units without a guard, so a cart with 0 units raises ZeroDivisionError.",
        "patches": [
            '''def average_unit_price(items):
    """Average price per unit; drives the premium-basket free-shipping perk."""
    if not items:
        return 0.0
    total_units = sum(int(item["qty"]) for item in items)
    return subtotal(items) / total_units''',
            '''def average_unit_price(items):
    """Average price per unit; drives the premium-basket free-shipping perk."""
    total_units = sum(int(item["qty"]) for item in items)
    if total_units <= 0:
        return 0.0
    return subtotal(items) / total_units''',
        ],
        "breaker": '''import store_logic


def test_breaker_empty_cart_returns_zero():
    assert store_logic.average_unit_price([]) == 0.0


def test_breaker_zero_quantity_lines():
    # "Saved for later" lines arrive with qty 0: the cart is non-empty but has no units.
    items = [{"sku": "SKU-MUG", "qty": 0}, {"sku": "SKU-CAP", "qty": 0}]
    assert store_logic.average_unit_price(items) == 0.0


def test_breaker_happy_path_unchanged():
    items = [{"sku": "SKU-HOODIE", "qty": 1}, {"sku": "SKU-CAP", "qty": 1}]
    assert store_logic.average_unit_price(items) == 41.5
''',
    },
    "normalize_coupon": {
        "diagnosis": "normalize_coupon() calls .strip() on coupon_code with no None check, so a partner sending null raises AttributeError.",
        "patches": [
            '''def normalize_coupon(code):
    """Partners send coupon codes in mixed case with stray whitespace."""
    if code is None:
        return ""
    return code.strip().upper()''',
            '''def normalize_coupon(code):
    """Partners send coupon codes in mixed case with stray whitespace."""
    if code is None:
        return ""
    return str(code).strip().upper()''',
        ],
        "breaker": '''import store_logic


def test_breaker_null_coupon_is_ignored():
    assert store_logic.normalize_coupon(None) == ""
    result = store_logic.checkout({"items": [{"sku": "SKU-CAP", "qty": 1}], "coupon_code": None})
    assert result["discount"] == 0.0


def test_breaker_numeric_coupon_from_partner_api():
    # Partner APIs serialize numeric promo codes as JSON numbers.
    assert store_logic.normalize_coupon(12345) == "12345"


def test_breaker_messy_code_still_normalizes():
    assert store_logic.normalize_coupon("  launch10 ") == "LAUNCH10"
''',
    },
}

TWIN_PLAYBOOK = {
    "normalize_reason_code": '''def normalize_reason_code(code):
    """Support agents type reason codes by hand: ' damaged ' -> 'DAMAGED'."""
    if code is None:
        return ""
    return str(code).strip().upper()''',
    "average_refund_per_unit": '''def average_refund_per_unit(lines):
    """Used by the fraud model to flag unusually expensive refunds."""
    total_units = sum(line["qty"] for line in lines)
    if total_units <= 0:
        return 0.0
    return sum(line["amount"] for line in lines) / total_units''',
}


# --------------------------------------------------------------------------
# Claude client
# --------------------------------------------------------------------------
class Claude:
    def __init__(self) -> None:
        self.client = None
        self.last_error = ""
        has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        if MODE == "live" or (MODE == "auto" and has_key):
            try:
                from anthropic import AsyncAnthropic
                self.client = AsyncAnthropic()
            except Exception as exc:  # pragma: no cover
                self.last_error = str(exc)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def ask(self, system: str, prompt: str, max_tokens: int = 6000) -> str | None:
        if not self.client:
            return None
        kwargs = dict(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": "low"},  # speed matters on stage
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            try:
                response = await asyncio.wait_for(
                    self.client.beta.messages.create(
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs),
                    timeout=120)
            except TypeError:  # older SDK without `fallbacks`
                response = await asyncio.wait_for(self.client.messages.create(**kwargs), timeout=120)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            return None
        if response.stop_reason == "refusal":
            self.last_error = "model declined the request"
            return None
        return "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")


SURGEON_SYSTEM = """You are SURGEON, TraceZero's production hotfix agent.
You get a Python module, the function that crashed in production, the traceback,
and the request that triggered it. Write the smallest correct fix.

Rules:
- Keep the exact function name and signature. Keep its docstring.
- Behavior for every input that already worked must stay byte-for-byte identical;
  real traffic will be replayed and diffed against production.
- Handle the whole class of bad input, not just the one value in the crash.
- No new imports. You may call other functions defined in the module.

Reply with one line starting with `ROOT CAUSE:` (one sentence), then the complete
fixed function in a single ```python block. Nothing else."""

BREAKER_SYSTEM = """You are BREAKER, TraceZero's adversarial QA agent.
SURGEON just patched a production function. Your job: find inputs that still crash
it or change its behavior on valid input.

Write exactly 3 pytest test functions, named test_breaker_*, in one ```python block.
- Start with `import store_logic` and call `store_logic.<function>(...)` directly.
- Only use SKUs that exist in CATALOG; carts are lists of {"sku": str, "qty": int}.
- Target realistic edge cases from messy production traffic (types, zeros,
  whitespace, missing values), not absurd ones.
- Assertions must be correct for ANY reasonable fix: assert no crash, that valid
  inputs return exactly what the original function returned, and for bad input the
  neutral value the patch clearly intends (e.g. 0.0 or "").
No prose outside the code block."""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Incident:
    id: str
    fingerprint: str
    pattern: str
    exc_type: str
    message: str
    module: str
    function: str
    line: int
    traceback: str
    route: str
    payload: dict
    created_at: float
    status: str = "detected"
    crash_count: int = 1
    crash_payloads: list = field(default_factory=list)
    engine: str = "playbook"
    diagnosis: str = ""
    shield_id: str | None = None
    shielded_at: float | None = None
    verified_at: float | None = None
    deployed_at: float | None = None
    original_fn: str = ""
    patch: str | None = None
    diff_rows: list = field(default_factory=list)
    unified_diff: str = ""
    repro_test: str = ""
    breaker_tests: str = ""
    rounds: list = field(default_factory=list)
    replay: dict | None = None
    twins: list = field(default_factory=list)
    pr: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
class TraceZero:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.runtime = Runtime()
        self.claude = Claude()
        self.shadow: deque = deque(maxlen=15)
        self.shields: dict[str, dict] = {}
        self.incidents: dict[str, Incident] = {}
        self.by_fingerprint: dict[str, str] = {}
        self.tasks: set[asyncio.Task] = set()
        self.stats = self._fresh_stats()

    @staticmethod
    def _fresh_stats() -> dict:
        return {"requests": 0, "ok": 0, "shielded": 0, "errors": 0}

    @property
    def mode(self) -> dict:
        if self.claude.enabled:
            return {"engine": "live", "label": f"LIVE · {MODEL}"}
        return {"engine": "playbook", "label": "PLAYBOOK · offline"}

    # ---------------- traffic hooks (called by the /shop middleware) -------
    def record_success(self, route: str, payload: dict, response: dict) -> None:
        self.stats["requests"] += 1
        self.stats["ok"] += 1
        self.shadow.append({"route": route, "payload": copy.deepcopy(payload),
                            "response": copy.deepcopy(response), "ts": time.time()})

    def shield_for(self, exc: BaseException) -> dict | None:
        fingerprint, *_ = self.locate(exc)
        shield = self.shields.get(fingerprint)
        if shield and shield["active"]:
            shield["hits"] += 1
            self.stats["requests"] += 1
            self.stats["shielded"] += 1
            return shield
        return None

    @staticmethod
    def locate(exc: BaseException) -> tuple[str, str, str, int, str]:
        frames = traceback.extract_tb(exc.__traceback__)
        target = frames[-1] if frames else None
        for frame in frames:
            if Path(frame.filename).stem in PROTECTED_MODULES:
                target = frame
        module = Path(target.filename).stem if target else "unknown"
        function = target.name if target else "unknown"
        line = target.lineno if target else 0
        fingerprint = f"{type(exc).__name__}@{module}.{function}"
        return fingerprint, module, function, line, (target.line or "") if target else ""

    def report_crash(self, exc: BaseException, payload: dict, route: str) -> Incident:
        self.stats["requests"] += 1
        self.stats["errors"] += 1
        fingerprint, module, function, line, code_line = self.locate(exc)

        existing_id = self.by_fingerprint.get(fingerprint)
        if existing_id and self.incidents[existing_id].status not in ("deployed",):
            incident = self.incidents[existing_id]
            incident.crash_count += 1
            if len(incident.crash_payloads) < 3 and payload not in incident.crash_payloads:
                incident.crash_payloads.append(copy.deepcopy(payload))
            return incident

        incident = Incident(
            id=short_id("INC"), fingerprint=fingerprint, pattern=classify(exc),
            exc_type=type(exc).__name__, message=str(exc), module=module,
            function=function, line=line,
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2500:],
            route=route, payload=copy.deepcopy(payload), created_at=time.time(),
            crash_payloads=[copy.deepcopy(payload)], engine=self.mode["engine"],
        )
        self.incidents[incident.id] = incident
        self.by_fingerprint[fingerprint] = incident.id
        self.emit(incident, "incident.detected", "detect", "danger",
                  f"{incident.exc_type} in {module}.{function}()",
                  f"{route} → HTTP 500 · line {line}: {code_line.strip()} · {str(exc)}",
                  incident=incident.to_dict())
        task = asyncio.create_task(self.heal(incident))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return incident

    # ---------------- event helper ------------------------------------------
    def emit(self, _inc: Incident | None, type_: str, stage: str, level: str,
             title: str, detail: str = "", **data) -> None:
        self.bus.publish(type_, incident_id=_inc.id if _inc else None,
                         stage=stage, level=level, title=title, detail=detail, **data)

    # ---------------- the pipeline ------------------------------------------
    async def heal(self, inc: Incident) -> None:
        try:
            await self._shield(inc)
            if not await self._reproduce(inc):
                return
            if not await self._battle(inc):
                return
            await self._replay(inc)
            await self._vaccinate(inc)
            inc.status = "verified"
            inc.verified_at = time.time()
            self.emit(inc, "incident.verified", "verified", "success",
                      "Fix verified: awaiting human approval to hot-deploy",
                      f"Time to verified fix: {inc.verified_at - inc.created_at:.1f}s · "
                      f"replay {inc.replay['passed']}/{inc.replay['total']} · "
                      f"{sum(1 for t in inc.twins if t['vaccinated'])} twin(s) vaccinated",
                      incident=inc.to_dict())
            if AUTO_DEPLOY:
                await self.deploy(inc.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            inc.status = "escalated"
            self.emit(inc, "incident.escalated", "battle", "danger",
                      "Engine error: escalated to on-call, shield stays up",
                      f"{type(exc).__name__}: {exc}", incident=inc.to_dict())

    async def _shield(self, inc: Incident) -> None:
        await beat(0.25)
        shield = {
            "id": short_id("SHD"), "fingerprint": inc.fingerprint, "incident_id": inc.id,
            "route": inc.route, "target": f"{inc.module}.{inc.function}",
            "exc_type": inc.exc_type, "created_at": time.time(), "hits": 0, "active": True,
            "fallback": "Safe-mode checkout: accept order (202), finalize pricing async",
        }
        self.shields[inc.fingerprint] = shield
        inc.shield_id = shield["id"]
        inc.shielded_at = shield["created_at"]
        inc.status = "shielded"
        self.emit(inc, "shield.up", "shield", "warn",
                  f"Instant Shield {shield['id']} armed in {(inc.shielded_at - inc.created_at) * 1000:.0f} ms",
                  f"Requests that hit {inc.exc_type} in {shield['target']}() now get a graceful "
                  f"safe-mode response instead of HTTP 500. Reversible with one click.",
                  shield=shield)

    async def _reproduce(self, inc: Incident) -> bool:
        current = self.runtime.sources[inc.module]
        inc.original_fn = function_source(current, inc.function)
        inc.repro_test = (
            f"import {inc.module}\n\n\n"
            f"def test_repro_{inc.id.replace('-', '_').lower()}():\n"
            f"    # Exact production payload that crashed {inc.route}\n"
            f"    payload = {inc.payload!r}\n"
            f"    result = {inc.module}.checkout(payload)\n"
            f"    assert isinstance(result, dict)\n"
            f"    assert result[\"total\"] >= 0\n"
        )
        self.emit(inc, "stage", "reproduce", "info", "Triage: fault localized",
                  f"{PATTERN_LABEL.get(inc.pattern)} in {inc.module}.py:{inc.line} → {inc.function}()",
                  code=inc.original_fn)
        await beat(0.4)
        result = await asyncio.to_thread(run_pytest_sync, dict(self.runtime.sources), inc.repro_test)
        reproduced = not result["ok"]
        self.emit(inc, "test.repro", "reproduce", "danger" if reproduced else "warn",
                  "RED: crash reproduced in an isolated sandbox" if reproduced
                  else "Could not reproduce in sandbox: continuing with traceback only",
                  (result["tests"][0]["message"] if result["tests"] else result["output"][-200:]),
                  test=inc.repro_test, result=result)
        return True

    async def _surgeon(self, inc: Incident, round_no: int, feedback: str) -> tuple[str | None, str, str]:
        """Return (function_source, source_label, diagnosis)."""
        playbook = PLAYBOOK.get(inc.function)
        if self.claude.enabled:
            prompt = (
                f"MODULE {inc.module}.py:\n```python\n{self.runtime.sources[inc.module]}\n```\n\n"
                f"CRASHING FUNCTION: {inc.function}\n\nTRACEBACK:\n{inc.traceback}\n\n"
                f"CRASHING REQUEST to {inc.route}:\n{inc.payload!r}\n"
            )
            if feedback:
                prompt += f"\nYOUR PREVIOUS PATCH WAS BROKEN BY THESE TESTS:\n{feedback}\nFix the patch so they pass.\n"
            text = await self.claude.ask(SURGEON_SYSTEM, prompt)
            if text:
                fn = extract_function(extract_code(text), inc.function)
                diag = re.search(r"ROOT CAUSE:\s*(.+)", text)
                if fn:
                    return fn, "claude", diag.group(1).strip() if diag else ""
            self.emit(inc, "agent.fallback", "battle", "warn", "Surgeon: model unavailable, using playbook",
                      self.claude.last_error or "model returned no usable function")
        await beat(1.3)
        if playbook:
            patches = playbook["patches"]
            return patches[min(round_no - 1, len(patches) - 1)], "playbook", playbook["diagnosis"]
        return None, "none", ""

    async def _breaker(self, inc: Incident, patch: str) -> tuple[str, str]:
        playbook = PLAYBOOK.get(inc.function)
        if self.claude.enabled:
            prompt = (
                f"MODULE {inc.module}.py (before the patch):\n```python\n{self.runtime.sources[inc.module]}\n```\n\n"
                f"SURGEON'S PATCHED FUNCTION:\n```python\n{patch}\n```\n\n"
                f"The original crash: {inc.exc_type}: {inc.message}\nTry to break the patch."
            )
            text = await self.claude.ask(BREAKER_SYSTEM, prompt, max_tokens=4000)
            if text:
                code = extract_code(text)
                try:
                    tree = ast.parse(code)
                    if any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body):
                        return code + "\n", "claude"
                except SyntaxError:
                    pass
            self.emit(inc, "agent.fallback", "battle", "warn", "Breaker: model unavailable, using playbook",
                      self.claude.last_error or "model returned no usable tests")
        await beat(1.1)
        if playbook:
            return playbook["breaker"], "playbook"
        return f"import {inc.module}\n", "none"

    async def _battle(self, inc: Incident) -> bool:
        inc.status = "healing"
        feedback = ""
        for round_no in range(1, MAX_ROUNDS + 1):
            self.emit(inc, "agent.thinking", "battle", "surgeon", f"Round {round_no}: Surgeon is operating…",
                      "Reading the traceback, the module and the crashing request", agent="surgeon", round=round_no)
            patch, source, diagnosis = await self._surgeon(inc, round_no, feedback)
            if not patch:
                inc.status = "escalated"
                self.emit(inc, "incident.escalated", "battle", "danger",
                          "Surgeon has no safe patch: escalated to on-call, shield stays up", "",
                          incident=inc.to_dict())
                return False
            if diagnosis and not inc.diagnosis:
                inc.diagnosis = diagnosis
            inc.patch = patch
            inc.diff_rows = side_by_side(inc.original_fn, patch)
            self.emit(inc, "patch.proposed", "battle", "surgeon", f"Round {round_no}: Surgeon proposes a patch",
                      diagnosis or "", agent="surgeon", round=round_no, source=source,
                      patch=patch, original=inc.original_fn, diff_rows=inc.diff_rows,
                      file=f"{inc.module}.py", function=inc.function)

            if round_no == 1:
                self.emit(inc, "agent.thinking", "battle", "breaker", "Breaker is hunting for edge cases…",
                          "Writing adversarial pytest cases against the patch", agent="breaker", round=round_no)
                inc.breaker_tests, bsource = await self._breaker(inc, patch)
                names = re.findall(r"def (test_\w+)", inc.breaker_tests)
                self.emit(inc, "breaker.tests", "battle", "breaker",
                          f"Breaker fires {len(names)} adversarial tests", ", ".join(names),
                          agent="breaker", round=round_no, source=bsource, tests=inc.breaker_tests)

            patched_sources = dict(self.runtime.sources)
            patched_sources[inc.module] = replace_function(self.runtime.sources[inc.module], inc.function, patch)
            self.emit(inc, "stage", "battle", "info", f"Round {round_no}: pytest referee running in temp sandbox",
                      "repro test + Breaker's tests against the patched module", round=round_no)
            result = await asyncio.to_thread(run_pytest_sync, patched_sources, inc.repro_test + "\n\n" + inc.breaker_tests)
            for test in result["tests"]:
                await beat(0.25)
                self.emit(inc, "test.result", "battle", "success" if test["passed"] else "danger",
                          f"{'PASS' if test['passed'] else 'FAIL'}  {test['name']}", test["message"],
                          round=round_no, test=test)
            winner = "surgeon" if result["ok"] else "breaker"
            inc.rounds.append({"round": round_no, "source": source, "passed": result["passed"],
                               "failed": result["failed"], "winner": winner})
            if result["ok"]:
                self.emit(inc, "round.result", "battle", "success",
                          f"Round {round_no}: Surgeon wins · {result['passed']}/{len(result['tests'])} green",
                          "Breaker could not break the patch", round=round_no, winner=winner)
                return True
            failing = [t for t in result["tests"] if not t["passed"]]
            feedback = "\n".join(f"- {t['name']}: {t['message']}" for t in failing)
            if not result["tests"]:
                feedback = result["output"][-1200:]
            self.emit(inc, "round.result", "battle", "breaker",
                      f"Round {round_no}: Breaker wins · {len(failing)} test(s) broke the patch",
                      feedback[:400], round=round_no, winner=winner)
            await beat(0.6)

        inc.status = "escalated"
        self.emit(inc, "incident.escalated", "battle", "danger",
                  f"No patch survived {MAX_ROUNDS} rounds: escalated to on-call, shield stays up",
                  "", incident=inc.to_dict())
        return False

    async def _replay(self, inc: Incident) -> None:
        patched = replace_function(self.runtime.sources[inc.module], inc.function, inc.patch)
        module = Runtime.build(inc.module, patched)
        traffic = list(self.shadow)
        self.emit(inc, "stage", "replay", "info", "Shadow Traffic Replay",
                  f"Replaying {len(traffic)} recent real requests + {len(inc.crash_payloads)} crash request(s) "
                  f"against the patch and diffing against what production returned")
        passed = regressions = 0
        entries = [("shadow", e["payload"], e["response"]) for e in traffic] + \
                  [("crash", p, None) for p in inc.crash_payloads]
        for index, (kind, payload, expected) in enumerate(entries, 1):
            try:
                output = module.checkout(copy.deepcopy(payload))
                if kind == "shadow":
                    ok = output == expected
                    note = "identical to production" if ok else "OUTPUT CHANGED vs production"
                else:
                    ok = isinstance(output, dict) and output.get("total", -1) >= 0
                    note = f"was HTTP 500 → now 200 · total ${output.get('total')}" if ok else "still failing"
            except Exception as exc:
                ok, note = False, f"{type(exc).__name__}: {exc}"
            passed += ok
            regressions += (not ok and kind == "shadow")
            await beat(0.09)
            self.emit(inc, "replay.request", "replay", "success" if ok else "danger",
                      f"#{index:02d} {kind.upper()} · {describe_payload(payload)}", note,
                      index=index, kind=kind, ok=ok)
        total = len(entries)
        inc.replay = {"passed": passed, "total": total, "regressions": regressions,
                      "confidence": round(100 * passed / total, 1) if total else 0.0}
        self.emit(inc, "replay.summary", "replay", "success" if passed == total else "danger",
                  f"Confidence {inc.replay['confidence']}% · {passed}/{total} passed · {regressions} regressions",
                  "Byte-for-byte identical on real traffic; crash requests now succeed",
                  replay=inc.replay)

    async def _vaccinate(self, inc: Incident) -> None:
        self.emit(inc, "stage", "vaccinate", "info", "Twin-Bug Immunity Scan",
                  f"AST-scanning {', '.join(f'{m}.py' for m in PROTECTED_MODULES)} for: {PATTERN_LABEL.get(inc.pattern)}")
        await beat(0.6)
        twins = scan_twins(dict(self.runtime.sources), inc.pattern, (inc.module, inc.function))
        if not twins:
            self.emit(inc, "twin.none", "vaccinate", "success", "No twin vulnerabilities found", "")
            return
        for twin in twins:
            self.emit(inc, "twin.found", "vaccinate", "warn",
                      f"Twin vulnerability: {twin['file']}:{twin['line']} {twin['function']}()",
                      twin["reason"] + " · has not crashed in production yet", twin=twin)
        results = await asyncio.gather(*(self._vaccinate_one(inc, twin) for twin in twins))
        inc.twins = list(results)

    async def _vaccinate_one(self, inc: Incident, twin: dict) -> dict:
        patch = None
        if self.claude.enabled:
            prompt = (
                f"A production crash in {inc.module}.{inc.function}() was fixed with:\n```python\n{inc.patch}\n```\n\n"
                f"The same bug pattern ({PATTERN_LABEL.get(inc.pattern)}) exists in {twin['file']}:\n"
                f"```python\n{twin['source']}\n```\n\nFull module:\n```python\n{self.runtime.sources[twin['module']]}\n```\n"
                f"Fix {twin['function']} the same way."
            )
            text = await self.claude.ask(SURGEON_SYSTEM, prompt)
            if text:
                patch = extract_function(extract_code(text), twin["function"])
        if not patch:
            await beat(0.8)
            patch = TWIN_PLAYBOOK.get(twin["function"])
        if not patch:
            return twin
        try:
            patched = replace_function(self.runtime.sources[twin["module"]], twin["function"], patch)
            module = Runtime.build(twin["module"], patched)
            poison = None if twin["pattern"] == "null-dereference" else []
            getattr(module, twin["function"])(*([poison] * max(twin["arity"], 1)))
            twin["vaccinated"] = True
        except Exception as exc:
            twin["vaccinated"] = False
            twin["reason"] += f" · vaccine failed: {type(exc).__name__}"
        twin["patch"] = patch
        twin["diff_rows"] = side_by_side(twin["source"], patch)
        self.emit(inc, "twin.vaccinated" if twin["vaccinated"] else "twin.failed", "vaccinate",
                  "success" if twin["vaccinated"] else "danger",
                  f"{'Vaccinated' if twin['vaccinated'] else 'Vaccine failed'}: {twin['function']}()",
                  "Pre-patched and staged with the hotfix" if twin["vaccinated"] else twin["reason"], twin=twin)
        return twin

    # ---------------- human actions ----------------------------------------
    async def deploy(self, incident_id: str) -> Incident:
        inc = self.incidents[incident_id]
        if inc.status != "verified":
            raise ValueError(f"incident is {inc.status}, not verified")
        self.runtime.hot_swap(inc.module, replace_function(self.runtime.sources[inc.module], inc.function, inc.patch))
        for twin in inc.twins:
            if twin.get("vaccinated") and twin.get("patch") and find_function(self.runtime.sources[twin["module"]], twin["function"]):
                self.runtime.hot_swap(twin["module"], replace_function(self.runtime.sources[twin["module"]], twin["function"], twin["patch"]))
        shield = self.shields.get(inc.fingerprint)
        if shield:
            shield["active"] = False
            shield["retired"] = True
        inc.status = "deployed"
        inc.deployed_at = time.time()
        mttr = inc.deployed_at - inc.created_at
        self.emit(inc, "incident.deployed", "deployed", "success",
                  f"Hot-deployed with zero downtime · MTTR {mttr:.1f}s",
                  f"{inc.module}.{inc.function}() swapped in memory; shield {inc.shield_id} retired; "
                  f"{sum(1 for t in inc.twins if t.get('vaccinated'))} twin(s) immunized",
                  incident=inc.to_dict(), mttr=mttr)
        return inc

    def set_shield(self, shield_id: str, active: bool) -> dict:
        for shield in self.shields.values():
            if shield["id"] == shield_id:
                if shield.get("retired"):
                    raise ValueError("shield already retired by a deployed fix")
                shield["active"] = active
                self.bus.publish("shield.toggled", incident_id=shield["incident_id"], stage="shield",
                                 level="warn" if active else "danger",
                                 title=f"Shield {shield_id} {'re-armed' if active else 'lifted by operator'}",
                                 detail="" if active else "Traffic hits the raw bug again", shield=shield)
                return shield
        raise KeyError(shield_id)

    def mttr_summary(self) -> dict:
        deployed = [i for i in self.incidents.values() if i.deployed_at]
        actual = sum(i.deployed_at - i.created_at for i in deployed)
        saved = sum(max(0.0, BASELINE_MTTR_MIN * 60 - (i.deployed_at - i.created_at)) for i in deployed)
        shield_times = [(i.shielded_at - i.created_at) for i in self.incidents.values() if i.shielded_at]
        return {
            "deployed": len(deployed), "actual_seconds": actual, "saved_seconds": saved,
            "baseline_minutes": BASELINE_MTTR_MIN,
            "avg_shield_ms": (1000 * sum(shield_times) / len(shield_times)) if shield_times else None,
        }

    async def reset(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        self.runtime.reset()
        self.shields.clear()
        self.incidents.clear()
        self.by_fingerprint.clear()
        self.shadow.clear()
        self.stats = self._fresh_stats()
        self.bus.history.clear()
        self.bus.publish("system.reset", stage="system", level="info",
                         title="Demo reset: original buggy code restored", detail="")

    # ---------------- pull request -----------------------------------------
    def pr_payload(self, inc: Incident) -> dict:
        changes = [{"path": f"{inc.module}.py", "module": inc.module, "function": inc.function, "patch": inc.patch}]
        changes += [{"path": t["file"], "module": t["module"], "function": t["function"], "patch": t["patch"]}
                    for t in inc.twins if t.get("vaccinated")]
        diff = ""
        for change in changes:
            before = self.runtime.original[change["module"]]
            # Fold every change to the same file into one diff
            after = before
            for other in changes:
                if other["module"] == change["module"]:
                    after = replace_function(after, other["function"], other["patch"])
            chunk = unified(before, after, change["path"])
            if chunk not in diff:
                diff += chunk
        title = f"fix({inc.module}): guard {inc.function}() against {inc.pattern} [{inc.id}]"
        tests = (inc.repro_test + "\n" + inc.breaker_tests)
        test_names = re.findall(r"def (test_\w+)", tests)
        t = lambda ts: f"{ts - inc.created_at:+.1f}s" if ts else "n/a"
        twin_lines = "\n".join(
            f"- `{tw['file']}:{tw['line']}` `{tw['function']}()`: {tw['reason']} → "
            f"{'vaccinated in this PR' if tw.get('vaccinated') else 'NOT fixed, needs review'}"
            for tw in inc.twins) or "- none found"
        rounds = "\n".join(
            f"| {r['round']} | {r['source']} | {r['passed']} | {r['failed']} | {r['winner']} |" for r in inc.rounds)
        body = f"""## TraceZero autonomous fix · {inc.id}

**Incident:** `{inc.exc_type}: {inc.message}` in `{inc.module}.{inc.function}()` (line {inc.line})
**Route:** `{inc.route}` · **Crashes before shield:** {inc.crash_count}
**Root cause:** {inc.diagnosis or PATTERN_LABEL.get(inc.pattern)}

### Timeline
| Step | T+ |
|---|---|
| Crash detected | +0.0s |
| Instant Shield armed | {t(inc.shielded_at)} |
| Fix verified | {t(inc.verified_at)} |
| Hot-deployed | {t(inc.deployed_at)} |

### Surgeon vs Breaker
| Round | Engine | Passed | Failed | Winner |
|---|---|---|---|---|
{rounds}

Tests ({len(test_names)}): {", ".join(f"`{n}`" for n in test_names)}

### Shadow Traffic Replay
**{inc.replay['passed']}/{inc.replay['total']} passed · {inc.replay['regressions']} regressions · confidence {inc.replay['confidence']}%**
Recent real requests were replayed against the patch and diffed byte-for-byte against production responses.

### Twin-Bug Vaccination
{twin_lines}

### Regression tests added
```python
{tests.strip()}
```

---
🤖 Opened autonomously by **TraceZero**: the immune system for production code.
"""
        return {"title": title, "body": body, "diff": diff, "changes": changes,
                "branch": f"tracezero/{inc.id.lower()}"}

    async def open_pr(self, incident_id: str) -> dict:
        inc = self.incidents[incident_id]
        if inc.status not in ("verified", "deployed"):
            raise ValueError("fix is not verified yet")
        pr = self.pr_payload(inc)
        token, repo_name = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPO")
        if token and repo_name:
            try:
                pr["url"] = await asyncio.to_thread(self._github_pr, token, repo_name, pr)
                pr["simulated"] = False
            except Exception as exc:
                pr["simulated"] = True
                pr["error"] = f"GitHub API failed: {type(exc).__name__}: {exc}"[:300]
        else:
            pr["simulated"] = True
            pr["error"] = "Set GITHUB_TOKEN and GITHUB_REPO to open a real PR."
        inc.pr = {k: v for k, v in pr.items() if k != "changes"}
        self.emit(inc, "pr.opened", "deployed", "success",
                  "Pull request opened" if not pr["simulated"] else "Pull request drafted (simulated)",
                  pr.get("url") or pr["title"], pr=inc.pr)
        return pr

    @staticmethod
    def _github_pr(token: str, repo_name: str, pr: dict) -> str:
        from github import Auth, Github
        from github.GithubException import UnknownObjectException

        prefix = os.getenv("GITHUB_PATH_PREFIX", "").strip("/")
        gh = Github(auth=Auth.Token(token))
        repo = gh.get_repo(repo_name)
        base = repo.default_branch
        branch = f"{pr['branch']}-{int(time.time())}"
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=repo.get_branch(base).commit.sha)
        by_path: dict[str, list[dict]] = {}
        for change in pr["changes"]:
            by_path.setdefault(change["path"], []).append(change)
        for path, changes in by_path.items():
            full_path = f"{prefix}/{path}" if prefix else path
            try:
                current = repo.get_contents(full_path, ref=branch)
                text = current.decoded_content.decode("utf-8")
                for change in changes:
                    text = replace_function(text, change["function"], change["patch"])
                repo.update_file(full_path, pr["title"], text, current.sha, branch=branch)
            except UnknownObjectException:
                text = (BASE_DIR / path).read_text(encoding="utf-8")
                for change in changes:
                    text = replace_function(text, change["function"], change["patch"])
                repo.create_file(full_path, pr["title"], text, branch=branch)
        return repo.create_pull(title=pr["title"], body=pr["body"], head=branch, base=base).html_url
