"""NovaMart refunds service.

Written by a different team, months earlier, but it carries the same two
anti-patterns as store_logic.py. Nothing has crashed here yet; TraceZero's
Twin-Bug Vaccination finds these before production traffic does.
"""

REFUND_POLICIES = {
    "DAMAGED": 1.00,     # full refund
    "WRONG_ITEM": 1.00,
    "CHANGED_MIND": 0.85,  # 15% restocking fee
    "LATE": 0.50,
}

DEFAULT_POLICY = 0.0


def normalize_reason_code(code):
    """Support agents type reason codes by hand: ' damaged ' -> 'DAMAGED'."""
    return code.strip().upper()


def average_refund_per_unit(lines):
    """Used by the fraud model to flag unusually expensive refunds."""
    return sum(line["amount"] for line in lines) / sum(line["qty"] for line in lines)


def process_refund(order):
    """order = {"reason_code": str, "lines": [{"amount": float, "qty": int}]}"""
    reason = normalize_reason_code(order.get("reason_code", ""))
    lines = order.get("lines", [])
    rate = REFUND_POLICIES.get(reason, DEFAULT_POLICY)
    gross = round(sum(line["amount"] for line in lines), 2)
    return {
        "reason": reason,
        "rate": rate,
        "gross": gross,
        "refund": round(gross * rate, 2),
        "avg_per_unit": round(average_refund_per_unit(lines), 2),
        "fraud_review": average_refund_per_unit(lines) > 100,
    }
