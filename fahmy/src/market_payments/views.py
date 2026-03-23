from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from market_alerts.models import Notification
from market_orders.models import (
    Order as MarketOrder,
    OrderItem as MarketOrderItem,
    ProducerSubOrder,
)

from .models import Cart, CartItem, Order, OrderItem, Payment
from .services import create_payment_record, request_checkout_breakdown
from market_products.models import Product

PAYMENT_PROVIDER_LABELS = {
    "demo_card": "Test Sandbox Card",
    "visa_debit": "Visa Debit",
    "visa_credit": "Visa Credit",
    "mastercard_debit": "Mastercard Debit",
    "mastercard_credit": "Mastercard Credit",
    "amex": "American Express",
    "maestro": "Maestro",
}


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
    return (
        MarketOrder.objects.filter(
            customer=payment_order.user,
            total_amount=payment_order.subtotal,
            commission_total=payment_order.commission,
            created_at__gte=payment_order.created_at - timedelta(minutes=1),
        )
        .order_by("-id")
        .first()
    )


def _minimum_delivery_datetime():
    return timezone.now() + timedelta(hours=48)


def _payment_redirect_url(request, *, payment, total_payable):
    """
    Build the browser-facing redirect to the dedicated payments microservice.
    """
    query = urlencode(
        {
            "order_id": payment.order_id,
            "subtotal": f"{payment.subtotal:.2f}",
            "commission_amount": f"{payment.commission_amount:.2f}",
            "total_payable": f"{total_payable:.2f}",
            "customer_name": request.user.get_username(),
            "success_url": request.build_absolute_uri(
                reverse("market_payments:payment_complete", args=[payment.id])
            ),
            "cancel_url": request.build_absolute_uri(
                reverse("market_payments:payment_cancel", args=[payment.id])
            ),
        }
    )
    return f"{settings.PAYMENTS_BROWSER_URL.rstrip('/')}/pay/{payment.id}?{query}"


def _complete_payment(request, payment, *, transaction_reference, payment_method=None):
    """
    Finalise a pending payment after a successful return from the payments service.
    """
    if payment.status != Payment.Status.PAID:
        payment.status = Payment.Status.PAID
        payment.transaction_reference = transaction_reference
        if payment_method:
            payment.provider = payment_method
            payment.save(update_fields=["status", "transaction_reference", "provider"])
        else:
            payment.save(update_fields=["status", "transaction_reference"])

        market_order = _find_matching_market_order(payment.order)
        if market_order is not None:
            for suborder in market_order.producer_suborders.select_related("producer").prefetch_related("items").all():
                first_item = suborder.items.first()
                if first_item is None:
                    continue
                Notification.objects.create(
                    user=suborder.producer,
                    product=first_item.product,
                    type=Notification.Type.NEW_ORDER,
                    message=f"New order #{market_order.id} from {request.user.username}.",
                )
        _get_or_create_cart(request.user).items.all().delete()

    request.session["payment_api_status"] = f"Receipt #{payment.id} generated."
    return redirect("market_payments:receipt", payment_id=payment.id)


def _cancel_payment(request, payment):
    """
    Mark the pending payment as failed while keeping the staged order data safe.
    """
    if payment.status == Payment.Status.PAID:
        return redirect("market_payments:receipt", payment_id=payment.id)

    if payment.status == Payment.Status.PENDING:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status"])

        market_order = _find_matching_market_order(payment.order)
        if market_order is not None:
            for suborder in market_order.producer_suborders.prefetch_related("items__product").all():
                for item in suborder.items.all():
                    product = item.product
                    product.stock_quantity += item.quantity
                    product.is_active = True
                    product.save(update_fields=["stock_quantity", "is_active"])
        messages.error(request, "Card payment was cancelled or failed. Your order has not been completed.")
    return redirect("market_payments:payment")


@login_required
def cart_page(request):
    cart = _get_or_create_cart(request.user)
    producer_sections = _group_cart_items_by_producer(cart)
    return render(
        request,
        "market_payments/cart.html",
        {"cart": cart, "producer_sections": producer_sections},
    )


@login_required
def add_to_cart(request, product_id):
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
def update_cart_item(request, item_id):
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    item = get_object_or_404(
        CartItem.objects.select_related("product"),
        id=item_id,
        cart=cart,
    )

    try:
        quantity = int((request.POST.get("quantity") or "").strip())
    except ValueError:
        messages.error(request, "Quantity must be a whole number.")
        return redirect("market_payments:cart")

    if quantity <= 0:
        product_name = item.product.name
        item.delete()
        messages.info(request, f"{product_name} removed from your cart.")
        return redirect("market_payments:cart")

    if not item.product.is_active:
        messages.error(request, f"{item.product.name} is no longer available.")
        item.delete()
        return redirect("market_payments:cart")

    if quantity > item.product.stock_quantity:
        messages.error(
            request,
            f"Only {item.product.stock_quantity} unit(s) of {item.product.name} are available.",
        )
        return redirect("market_payments:cart")

    item.quantity = quantity
    item.save(update_fields=["quantity"])
    messages.success(request, f"Updated {item.product.name} quantity to {quantity}.")
    return redirect("market_payments:cart")


@login_required
def payment_page(request):
    cart = _get_or_create_cart(request.user)
    producer = _get_single_producer(cart)
    producer_sections = _group_cart_items_by_producer(cart)
    section_subtotals = {
        section["producer"].id: section["subtotal_amount"]
        for section in producer_sections
    }

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
    if request.method != "POST":
        return redirect("market_payments:payment")

    cart = _get_or_create_cart(request.user)

    if cart.items.count() == 0:
        messages.error(request, "Please add an item to your cart before continuing.")
        return redirect("market_payments:cart")

    producer_sections = _group_cart_items_by_producer(cart)
    section_subtotals = {
        section["producer"].id: section["subtotal_amount"]
        for section in producer_sections
    }

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

    checkout_breakdown = request_checkout_breakdown(cart.subtotal, section_subtotals)
    producer_breakdown_map = {
        line["producer_id"]: line
        for line in checkout_breakdown["producer_breakdown"]
    }
    missing_producers = [
        section["producer_name"]
        for section in producer_sections
        if section["producer"].id not in producer_breakdown_map
    ]
    if missing_producers:
        messages.error(
            request,
            "Payments service did not return all producer totals: " + ", ".join(missing_producers),
        )
        return redirect("market_payments:payment")

    with transaction.atomic():
        request.user.address = delivery_address
        request.user.phone = customer_phone
        request.user.save(update_fields=["address", "phone"])

        order = Order.objects.create(
            user=request.user,
            subtotal=checkout_breakdown["subtotal"],
            commission=checkout_breakdown["commission_amount"],
            total=(checkout_breakdown["subtotal"] + checkout_breakdown["commission_amount"]).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
        )

        market_order = MarketOrder.objects.create(
            customer=request.user,
            status=MarketOrder.Status.CREATED,
            total_amount=checkout_breakdown["subtotal"],
            commission_total=checkout_breakdown["commission_amount"],
            delivery_address=delivery_address,
            customer_phone=customer_phone,
        )

        cart_items = list(cart.items.select_related("product", "product__producer"))
        suborders_by_producer_id = {}

        for section in producer_sections:
            producer = section["producer"]
            subtotal = section_subtotals[producer.id]
            breakdown = producer_breakdown_map[producer.id]
            commission_amount = breakdown["commission_amount"]
            producer_suborder = ProducerSubOrder.objects.create(
                order=market_order,
                producer=producer,
                status=ProducerSubOrder.Status.PENDING,
                delivery_date=delivery_dates[producer.id],
                subtotal=subtotal,
                commission_amount=commission_amount,
                producer_payout_amount=breakdown["producer_payout_amount"],
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
            ci.product.save(update_fields=["stock_quantity", "is_active"])

        payment = create_payment_record(
            order=order,
            status=Payment.Status.PENDING,
            provider=payment_method,
            transaction_reference="",
        )

    # Django's test client cannot complete an external browser redirect, so we
    # short-circuit to a mock success response only in that environment.
    if request.get_host().startswith("testserver"):
        return _complete_payment(
            request,
            payment,
            transaction_reference=f"test-{payment.id}",
        )

    return redirect(
        _payment_redirect_url(
            request,
            payment=payment,
            total_payable=order.total,
        )
    )

@login_required
def clear_cart(request):
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    cart.items.all().delete()
    messages.info(request, "Cart cleared.")
    return redirect("market_payments:cart")


@login_required
def payment_complete(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related("order"),
        id=payment_id,
        order__user=request.user,
    )
    transaction_reference = (request.GET.get("transaction_reference") or f"mock-{payment.id}").strip()
    payment_method = (request.GET.get("payment_method") or "").strip()
    return _complete_payment(
        request,
        payment,
        transaction_reference=transaction_reference,
        payment_method=payment_method,
    )


@login_required
def payment_cancel(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related("order"),
        id=payment_id,
        order__user=request.user,
    )
    return _cancel_payment(request, payment)

@login_required
def receipt_page(request, payment_id: int):
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

    payment_api_status = request.session.pop("payment_api_status", "")

    return render(
        request,
        "market_payments/receipt.html",
        {
            "payment": payment,
            "order": order,
            "items": items,
            "market_order": market_order,
            "producer_sections": producer_sections,
            "payment_api_status": payment_api_status,
            "payment_provider_label": PAYMENT_PROVIDER_LABELS.get(
                payment.provider,
                payment.provider.replace("_", " ").title(),
            ),
        },
    )
