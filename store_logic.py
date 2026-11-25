"""NovaMart pricing & checkout engine.

The target production service TraceZero protects. It looks clean and passes
every happy-path request, but it hides two latent bugs that only surface
when real-world traffic changes shape:

  1. average_unit_price()  -> ZeroDivisionError when a cart has no units
     (triggered when the new mobile release sends empty carts).
  2. normalize_coupon()    -> AttributeError when coupon_code is null
     (triggered when a partner integration sends "coupon_code": null).

Every function here is pure and deterministic, which is what lets TraceZero
replay shadow traffic and compare outputs byte-for-byte.
"""

CATALOG = {
    "SKU-HOODIE": {"name": "Neon Hoodie", "price": 59.0},
    "SKU-CAP": {"name": "Glitch Cap", "price": 24.0},
    "SKU-MUG": {"name": "Stack Trace Mug", "price": 14.5},
    "SKU-KEYS": {"name": "Mech Keyboard", "price": 129.0},
    "SKU-STICK": {"name": "Sticker Pack", "price": 6.0},
}

COUPONS = {"LAUNCH10": 0.10, "VIP20": 0.20, "HACKER15": 0.15}

TAX_RATE = 0.08
PREMIUM_BASKET_AVG = 50.0  # avg unit price that unlocks free shipping
STANDARD_SHIPPING = 4.99


def line_items(items):
    """Resolve cart lines against the catalog."""
    lines = []
    for item in items:
        product = CATALOG[item["sku"]]
        qty = int(item["qty"])
        lines.append({
            "sku": item["sku"],
            "name": product["name"],
            "unit_price": product["price"],
            "qty": qty,
            "line_total": round(product["price"] * qty, 2),
        })
    return lines


def subtotal(items):
    """Sum of price * qty across the cart."""
    return round(sum(CATALOG[i["sku"]]["price"] * int(i["qty"]) for i in items), 2)


def average_unit_price(items):
    """Average price per unit; drives the premium-basket free-shipping perk."""
    total_units = sum(int(item["qty"]) for item in items)
    return subtotal(items) / total_units


def normalize_coupon(code):
    """Partners send coupon codes in mixed case with stray whitespace."""
    return code.strip().upper()


def coupon_discount(subtotal_amount, code):
    """Return (discount_amount, applied_code_or_None)."""
    normalized = normalize_coupon(code)
    rate = COUPONS.get(normalized, 0.0)
    return round(subtotal_amount * rate, 2), (normalized if rate else None)


def shipping_fee(items):
    if average_unit_price(items) >= PREMIUM_BASKET_AVG:
        return 0.0
    return STANDARD_SHIPPING


def checkout(payload):
    """Price a cart. payload = {"items": [{"sku", "qty"}], "coupon_code": str}"""
    items = payload.get("items", [])
    code = payload.get("coupon_code", "")

    sub = subtotal(items)
    discount, applied = coupon_discount(sub, code)
    shipping = shipping_fee(items)
    tax = round((sub - discount) * TAX_RATE, 2)
    total = round(sub - discount + shipping + tax, 2)

    return {
        "lines": line_items(items),
        "subtotal": sub,
        "discount": discount,
        "coupon_applied": applied,
        "shipping": shipping,
        "tax": tax,
        "total": total,
        "currency": "USD",
    }
