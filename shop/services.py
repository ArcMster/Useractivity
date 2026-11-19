"""Pricing, ordering and notification logic. Prices are always computed here,
never trusted from the browser."""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import CustomDesignRequest, Offer, Order, OrderItem, Product, SiteSettings

log = logging.getLogger("shop")
MAX_QTY_PER_LINE = 20
MAX_LINES = 30


def find_live_coupon(code: str):
    code = (code or "").strip().upper()
    if not code:
        return None
    return Offer.objects.filter(Offer.live_q(), coupon_code=code).first()


def price_cart(raw_items, coupon_code=""):
    """Validate cart lines and return a priced summary.

    raw_items: list of dicts, either
      {"type": "product", "slug": str, "colour": str, "size": str, "quantity": int}
      {"type": "custom", "token": str}
    """
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError({"items": "Your cart is empty."})
    if len(raw_items) > MAX_LINES:
        raise ValidationError({"items": f"A cart can hold at most {MAX_LINES} lines."})

    lines, errors = [], []
    slugs = {i.get("slug") for i in raw_items if isinstance(i, dict) and i.get("type", "product") == "product"}
    products = {
        p.slug: p
        for p in Product.objects.filter(slug__in=slugs, is_active=True).prefetch_related("colours", "sizes", "images")
    }

    seen_tokens = set()
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "Invalid line."})
            continue
        kind = item.get("type", "product")
        if kind == "custom":
            token = str(item.get("token", ""))
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            design = CustomDesignRequest.objects.select_related("tee_style", "colour", "size").filter(token=token).first() if _is_uuid(token) else None
            if not design:
                errors.append({"index": index, "error": "Custom design not found."})
                continue
            if design.status != CustomDesignRequest.APPROVED:
                errors.append({"index": index, "error": f"Design {design.reference} is {design.get_status_display().lower()}, not approved yet."})
                continue
            unit = design.unit_price
            lines.append(
                {
                    "type": "custom",
                    "token": str(design.token),
                    "design": design,
                    "name": f"Custom {design.tee_style.name} tee ({design.get_placement_display()} print)",
                    "colour": design.colour.name,
                    "size": design.size.name,
                    "quantity": design.quantity,
                    "unit_price": unit,
                    "line_total": unit * design.quantity,
                }
            )
            continue

        product = products.get(item.get("slug"))
        if not product:
            errors.append({"index": index, "error": "This product is no longer available."})
            continue
        if not product.in_stock:
            errors.append({"index": index, "error": f"{product.name} is out of stock."})
            continue
        try:
            qty = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            qty = 0
        if qty < 1 or qty > MAX_QTY_PER_LINE:
            errors.append({"index": index, "error": f"Quantity must be between 1 and {MAX_QTY_PER_LINE}."})
            continue
        colour, size = item.get("colour", ""), item.get("size", "")
        colour_names = {c.name for c in product.colours.all()}
        size_names = {s.name for s in product.sizes.all()}
        if colour_names and colour not in colour_names:
            errors.append({"index": index, "error": f"Pick a valid colour for {product.name}."})
            continue
        if size_names and size not in size_names:
            errors.append({"index": index, "error": f"Pick a valid size for {product.name}."})
            continue
        lines.append(
            {
                "type": "product",
                "slug": product.slug,
                "product": product,
                "name": product.name,
                "colour": colour,
                "size": size,
                "quantity": qty,
                "unit_price": product.price,
                "line_total": product.price * qty,
            }
        )

    if errors:
        raise ValidationError({"items": errors})

    subtotal = sum((line["line_total"] for line in lines), Decimal("0"))
    offer, coupon_message = None, ""
    discount = Decimal("0")
    if coupon_code:
        offer = find_live_coupon(coupon_code)
        if not offer:
            coupon_message = "This coupon is not valid right now."
        elif subtotal < offer.min_order_amount:
            coupon_message = f"Add items worth Rs {offer.min_order_amount - subtotal:.0f} more to use this coupon."
            offer = None
        else:
            discount = offer.discount_for(subtotal)
            coupon_message = f"{offer.coupon_code} applied."

    site = SiteSettings.load()
    shipping = site.shipping_for(subtotal - discount)
    total = subtotal - discount + shipping
    return {
        "lines": lines,
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "total": total,
        "offer": offer,
        "coupon_message": coupon_message,
    }


def public_quote(quote):
    """Strip model objects so the quote can be returned as JSON."""
    return {
        "lines": [
            {k: (str(v) if isinstance(v, Decimal) else v) for k, v in line.items() if k not in ("product", "design")}
            for line in quote["lines"]
        ],
        "subtotal": str(quote["subtotal"]),
        "discount": str(quote["discount"]),
        "shipping": str(quote["shipping"]),
        "total": str(quote["total"]),
        "coupon_code": quote["offer"].coupon_code if quote["offer"] else "",
        "coupon_message": quote["coupon_message"],
    }


@transaction.atomic
def create_order(customer: dict, raw_items, coupon_code="", payment_method="cod") -> Order:
    quote = price_cart(raw_items, coupon_code)
    if coupon_code and not quote["offer"]:
        raise ValidationError({"coupon_code": quote["coupon_message"] or "Invalid coupon."})

    order = Order.objects.create(
        **customer,
        subtotal=quote["subtotal"],
        discount=quote["discount"],
        shipping=quote["shipping"],
        total=quote["total"],
        coupon_code=quote["offer"].coupon_code if quote["offer"] else "",
        payment_method=payment_method,
    )
    for line in quote["lines"]:
        OrderItem.objects.create(
            order=order,
            product=line.get("product"),
            custom_design=line.get("design"),
            name=line["name"],
            colour=line["colour"],
            size=line["size"],
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            line_total=line["line_total"],
        )
        design = line.get("design")
        if design:
            # Guard against the same approved design being ordered twice at once.
            updated = CustomDesignRequest.objects.filter(pk=design.pk, status=CustomDesignRequest.APPROVED).update(
                status=CustomDesignRequest.ORDERED, updated_at=timezone.now()
            )
            if not updated:
                raise ValidationError({"items": f"Design {design.reference} has already been ordered."})

    transaction.on_commit(lambda: notify_order_placed(order))
    return order


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Notifications (console email by default; never break the request)
# ---------------------------------------------------------------------------
def _send(subject, body, to):
    to = [address for address in to if address]
    if not to:
        return
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    except Exception:  # noqa: BLE001 - mail problems must not fail an order
        log.exception("Could not send email: %s", subject)


def status_link(design: CustomDesignRequest) -> str:
    return f"{settings.STOREFRONT_URL}/design-status.html?token={design.token}"


def notify_design_submitted(design: CustomDesignRequest):
    site = SiteSettings.load()
    _send(
        f"We received your custom tee design {design.reference}",
        f"Hi {design.customer_name},\n\nThanks for your design. Our team will review it and update you soon.\n"
        f"Track it here: {status_link(design)}\n\n{site.store_name}",
        [design.customer_email],
    )
    _send(
        f"New custom design to review: {design.reference}",
        f"{design.customer_name} ({design.customer_phone}) submitted a {design.tee_style.name} tee, "
        f"{design.colour.name}, size {design.size.name}, {design.get_placement_display()} print, qty {design.quantity}.\n"
        "Review it in Django admin under Custom design requests.",
        [settings.STORE_NOTIFY_EMAIL or site.contact_email],
    )


def notify_design_reviewed(design: CustomDesignRequest):
    site = SiteSettings.load()
    messages = {
        CustomDesignRequest.APPROVED: f"Good news, your design is approved at Rs {design.unit_price} per tee. You can now add it to your cart and order.",
        CustomDesignRequest.CHANGES: "We need a few changes before we can print your design.",
        CustomDesignRequest.REJECTED: "Sorry, we cannot print this design.",
    }
    if design.status not in messages:
        return
    note = f"\n\nNote from our team: {design.admin_note}" if design.admin_note else ""
    _send(
        f"Update on your custom design {design.reference}",
        f"Hi {design.customer_name},\n\n{messages[design.status]}{note}\n\nView it here: {status_link(design)}\n\n{site.store_name}",
        [design.customer_email],
    )


def notify_order_placed(order: Order):
    site = SiteSettings.load()
    lines = "\n".join(f"- {i.quantity} x {i.name} ({i.colour} {i.size}) Rs {i.line_total}" for i in order.items.all())
    _send(
        f"Order {order.number} received",
        f"Hi {order.customer_name},\n\nThanks for your order.\n\n{lines}\n\nTotal: Rs {order.total}\n\n{site.store_name}",
        [order.customer_email],
    )
    _send(
        f"New order {order.number}",
        f"{order.customer_name} ({order.customer_phone}) placed an order for Rs {order.total}.\n\n{lines}",
        [settings.STORE_NOTIFY_EMAIL or site.contact_email],
    )
