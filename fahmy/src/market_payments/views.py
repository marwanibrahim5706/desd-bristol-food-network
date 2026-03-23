from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import can_use_customer_checkout
from market_alerts.models import Notification
from market_orders.models import (
    Order as MarketOrder,
    OrderItem as MarketOrderItem,
    ProducerSubOrder,
)
from .models import Cart, CartItem, Order, OrderItem, Payment
from market_products.models import Product


def _get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def _group_cart_items_by_producer(cart):
    grouped = {}
    for item in cart.items.select_related("product", "product__producer").order_by(
        "product__producer__business_name", "product__producer__username", "product__name"
    ):
        producer = item.product.producer
        bucket = grouped.setdefault(
            producer.id,
            {
                "producer": producer,
                "producer_name": getattr(producer, "business_name", "") or getattr(producer, "username", ""),
                "items": [],
                "subtotal_amount": Decimal("0.00"),
            },
        )
        bucket["items"].append(item)
        bucket["subtotal_amount"] += item.line_total

    sections = list(grouped.values())
    for section in sections:
        section["subtotal"] = f"{section['subtotal_amount']:.2f}"
    return sections


def _get_single_producer(cart):
    sections = _group_cart_items_by_producer(cart)
    if len(sections) == 1:
        return sections[0]["producer"]
    return None


def _find_matching_market_order(payment_order):
    payment_signature = sorted(
        (item.product_name, str(item.unit_price), item.quantity)
        for item in payment_order.items.all()
    )

    candidates = list(
        MarketOrder.objects.filter(
            customer=payment_order.user,
            total_amount=payment_order.subtotal,
            commission_total=payment_order.commission,
            created_at__gte=payment_order.created_at - timedelta(minutes=1),
            created_at__lte=payment_order.created_at + timedelta(minutes=5),
        )
        .prefetch_related("producer_suborders__items")
        .order_by("-created_at", "-id")
    )

    if not candidates:
        return None

    if not payment_signature:
        return min(
            candidates,
            key=lambda order: abs((order.created_at - payment_order.created_at).total_seconds()),
        )

    exact_matches = []
    for candidate in candidates:
        candidate_signature = sorted(
            (item.product_name, str(item.unit_price), item.quantity)
            for suborder in candidate.producer_suborders.all()
            for item in suborder.items.all()
        )
        if candidate_signature == payment_signature:
            exact_matches.append(candidate)

    if exact_matches:
        return min(
            exact_matches,
            key=lambda order: abs((order.created_at - payment_order.created_at).total_seconds()),
        )

    return min(
        candidates,
        key=lambda order: abs((order.created_at - payment_order.created_at).total_seconds()),
    )


def _minimum_delivery_datetime():
    return timezone.now() + timedelta(hours=48)


def _ensure_customer_access(user):
    if not can_use_customer_checkout(user):
        raise PermissionDenied("Customer access required.")


def _get_customer_order_queryset(user):
    return (
        Order.objects.filter(user=user)
        .prefetch_related("items", "payments")
        .order_by("-created_at", "-id")
    )


def _get_market_order_for_history(payment_order):
    return _find_matching_market_order(payment_order)


@login_required
def cart_page(request):
    _ensure_customer_access(request.user)
    cart = _get_or_create_cart(request.user)
    producer_sections = _group_cart_items_by_producer(cart)
    return render(
        request,
        "market_payments/cart.html",
        {"cart": cart, "producer_sections": producer_sections},
    )


@login_required
def order_history_page(request):
    _ensure_customer_access(request.user)

    orders = []
    for order in _get_customer_order_queryset(request.user):
        latest_payment = order.payments.order_by("-created_at", "-id").first()
        market_order = _get_market_order_for_history(order)
        orders.append(
            {
                "order": order,
                "latest_payment": latest_payment,
                "market_order": market_order,
                "item_count": sum(item.quantity for item in order.items.all()),
            }
        )

    return render(
        request,
        "market_payments/order_history.html",
        {"orders": orders},
    )


@login_required
def add_to_cart(request, product_id):
    _ensure_customer_access(request.user)
    if request.method != "POST":
        return redirect("market_payments:cart")

    product = get_object_or_404(Product, id=product_id, is_active=True)
    cart = _get_or_create_cart(request.user)

    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        defaults={"quantity": 1},
    )

    if not created:
        item.quantity += 1
        item.save()

    messages.success(request, f"{product.name} added to cart.")
    return redirect(request.POST.get("next") or "/")


@login_required
def reorder_order(request, order_id):
    _ensure_customer_access(request.user)
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    order = get_object_or_404(
        Order.objects.filter(user=request.user).prefetch_related("items", "payments"),
        id=order_id,
    )
    market_order = _get_market_order_for_history(order)
    if market_order is None:
        messages.error(request, "This order cannot be reordered because its original items are unavailable.")
        return redirect("market_payments:order_history")

    market_items = list(
        market_order.producer_suborders.prefetch_related("items__product").all()
    )

    requested_items = []
    unavailable = []
    for suborder in market_items:
        for item in suborder.items.all():
            product = item.product
            if (not product.is_active) or product.stock_quantity <= 0:
                unavailable.append(item.product_name)
                continue
            quantity = min(item.quantity, product.stock_quantity)
            requested_items.append((product, quantity, item.quantity))
            if quantity < item.quantity:
                unavailable.append(
                    f"{item.product_name} (requested {item.quantity}, only {product.stock_quantity} available)"
                )

    if not requested_items:
        messages.error(request, "No items from this order are currently available to reorder.")
        return redirect("market_payments:order_history")

    cart = _get_or_create_cart(request.user)
    added_count = 0
    for product, quantity, _original_quantity in requested_items:
        cart_item, _created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": 0},
        )
        cart_item.quantity += quantity
        cart_item.save(update_fields=["quantity"])
        added_count += quantity

    if unavailable:
        messages.warning(
            request,
            "Reorder completed with adjustments. Unavailable items: " + ", ".join(unavailable),
        )
    else:
        messages.success(request, f"Reordered {added_count} item(s) into your cart.")

    return redirect("market_payments:cart")

@login_required
def payment_page(request):
    _ensure_customer_access(request.user)
    cart = _get_or_create_cart(request.user)
    producer = _get_single_producer(cart)
    producer_sections = _group_cart_items_by_producer(cart)

    items = [
        {
            "name": i.product.name,
            "quantity": i.quantity,
            "line_total": f"{i.line_total:.2f}",
        }
        for i in cart.items.select_related("product")
    ]

    min_delivery_at = timezone.localtime(_minimum_delivery_datetime()).strftime("%Y-%m-%dT%H:%M")

    return render(
        request,
        "market_payments/payment.html",
        {
            "items": items,
            "subtotal": f"{cart.subtotal:.2f}",
            "commission": f"{cart.commission:.2f}",
            "total": f"{cart.total:.2f}",
            "payment_status": "PENDING",
            "delivery_address": request.user.address or "",
            "customer_phone": request.user.phone or "",
            "min_delivery_at": min_delivery_at,
            "producer_name": getattr(producer, "business_name", "") or getattr(producer, "username", ""),
            "producer_sections": producer_sections,
            "single_producer_only": producer is not None,
        },
    )

@login_required
def pay_now(request):
    _ensure_customer_access(request.user)
    if request.method != "POST":
        return redirect("market_payments:payment")

    cart = _get_or_create_cart(request.user)

    if cart.items.count() == 0:
        messages.error(request, "Please add an item to your cart before continuing.")
        return redirect("market_payments:cart")

    producer_sections = _group_cart_items_by_producer(cart)

    delivery_address = (request.POST.get("delivery_address") or request.user.address or "").strip()
    customer_phone = (request.POST.get("customer_phone") or request.user.phone or "").strip()
    payment_method = (request.POST.get("payment_method") or "demo").strip() or "demo"

    if not delivery_address:
        messages.error(request, "Please provide a delivery address.")
        return redirect("market_payments:payment")

    delivery_dates = {}
    for section in producer_sections:
        producer = section["producer"]
        delivery_date_raw = (
            request.POST.get(f"delivery_date_{producer.id}")
            or request.POST.get("delivery_date")
            or ""
        ).strip()
        if not delivery_date_raw:
            messages.error(
                request,
                f"Please choose a delivery date and time for {section['producer_name']}.",
            )
            return redirect("market_payments:payment")

        try:
            delivery_date = datetime.fromisoformat(delivery_date_raw)
        except ValueError:
            messages.error(request, f"Delivery date format is invalid for {section['producer_name']}.")
            return redirect("market_payments:payment")

        if timezone.is_naive(delivery_date):
            delivery_date = timezone.make_aware(delivery_date, timezone.get_current_timezone())

        if delivery_date < _minimum_delivery_datetime():
            messages.error(
                request,
                f"Delivery date for {section['producer_name']} must be at least 48 hours from now.",
            )
            return redirect("market_payments:payment")

        delivery_dates[producer.id] = delivery_date

    # Validate stock before creating the order
    for ci in cart.items.select_related("product"):
        if not ci.product.is_active:
            messages.error(request, f"{ci.product.name} is no longer available.")
            return redirect("market_payments:cart")

        if ci.quantity > ci.product.stock_quantity:
            messages.error(
                request,
                f"Not enough stock for {ci.product.name}. Available: {ci.product.stock_quantity}."
            )
            return redirect("market_payments:cart")

    with transaction.atomic():
        request.user.address = delivery_address
        request.user.phone = customer_phone
        request.user.save(update_fields=["address", "phone"])

        order = Order.objects.create(
            user=request.user,
            subtotal=cart.subtotal,
            commission=cart.commission,
            total=cart.total,
        )

        market_order = MarketOrder.objects.create(
            customer=request.user,
            status=MarketOrder.Status.CREATED,
            total_amount=cart.subtotal,
            commission_total=cart.commission,
            delivery_address=delivery_address,
            customer_phone=customer_phone,
        )

        cart_items = list(cart.items.select_related("product", "product__producer"))
        suborders_by_producer_id = {}
        section_subtotals = {
            section["producer"].id: section["subtotal_amount"]
            for section in producer_sections
        }
        total_subtotal = cart.subtotal or Decimal("1.00")

        for section in producer_sections:
            producer = section["producer"]
            subtotal = section_subtotals[producer.id]
            if producer.id == producer_sections[-1]["producer"].id:
                commission_amount = market_order.commission_total - sum(
                    sub.commission_amount for sub in suborders_by_producer_id.values()
                )
            else:
                commission_amount = (
                    market_order.commission_total * subtotal / total_subtotal
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            producer_suborder = ProducerSubOrder.objects.create(
                order=market_order,
                producer=producer,
                status=ProducerSubOrder.Status.PENDING,
                delivery_date=delivery_dates[producer.id],
                subtotal=subtotal,
                commission_amount=commission_amount,
                producer_payout_amount=(subtotal - commission_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            )
            suborders_by_producer_id[producer.id] = producer_suborder

        for ci in cart_items:
            OrderItem.objects.create(
                order=order,
                product_name=ci.product.name,
                unit_price=ci.product.price,
                quantity=ci.quantity,
            )
            MarketOrderItem.objects.create(
                suborder=suborders_by_producer_id[ci.product.producer_id],
                product=ci.product,
                product_name=ci.product.name,
                unit_price=ci.product.price,
                quantity=ci.quantity,
            )

            ci.product.stock_quantity -= ci.quantity
            if ci.product.stock_quantity <= 0:
                ci.product.stock_quantity = 0
                ci.product.is_active = False
            ci.product.save()

        payment = Payment.objects.create(
            order=order,
            status=Payment.Status.PAID,
            provider=payment_method,
        )

        for section in producer_sections:
            producer = section["producer"]
            first_item = section["items"][0]
            Notification.objects.create(
                user=producer,
                product=first_item.product,
                type=Notification.Type.NEW_ORDER,
                message=f"New order #{market_order.id} from {request.user.username}.",
            )

    cart.items.all().delete()

    messages.success(request, f"Payment successful. Receipt #{payment.id} generated.")
    return redirect("market_payments:receipt", payment_id=payment.id)

@login_required
def clear_cart(request):
    _ensure_customer_access(request.user)
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    cart.items.all().delete()
    messages.info(request, "Cart cleared.")
    return redirect("market_payments:cart")

@login_required
def receipt_page(request, payment_id: int):
    _ensure_customer_access(request.user)
    payment = get_object_or_404(
        Payment.objects.select_related("order").prefetch_related("order__items"),
        id=payment_id,
        order__user=request.user,
    )
    order = payment.order
    market_order = _find_matching_market_order(order)

    items = []
    for it in order.items.all():
        items.append(
            {
                "name": it.product_name,
                "quantity": it.quantity,
                "unit_price": f"{it.unit_price:.2f}",
                "line_total": f"{it.line_total:.2f}",
            }
        )

    producer_sections = []
    if market_order is not None:
        for suborder in market_order.producer_suborders.select_related("producer").prefetch_related("items").all():
            producer_sections.append(
                {
                    "producer_name": getattr(suborder.producer, "business_name", "") or getattr(suborder.producer, "username", ""),
                    "delivery_date": timezone.localtime(suborder.delivery_date).strftime("%Y-%m-%d %H:%M"),
                    "subtotal": f"{suborder.subtotal:.2f}",
                    "commission": f"{suborder.commission_amount:.2f}",
                    "payout": f"{suborder.producer_payout_amount:.2f}",
                    "items": list(suborder.items.all()),
                }
            )

    return render(
        request,
        "market_payments/receipt.html",
        {
            "payment": payment,
            "order": order,
            "items": items,
            "market_order": market_order,
            "producer_sections": producer_sections,
        },
    )
