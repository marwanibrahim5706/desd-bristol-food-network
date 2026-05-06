from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import can_use_customer_checkout
from market_alerts.models import Notification
from market_orders.models import (
    Order as MarketOrder,
    OrderItem as MarketOrderItem,
    ProducerSubOrder,
)

from .models import Cart, CartItem, Order, OrderItem, Payment
from .services import create_payment_record, request_checkout_breakdown
from market_products.models import FavouriteRecipe, Product
from market_products.models import Review as ProductReview

PAYMENT_PROVIDER_LABELS = {
    "demo_card": "Visa Debit",
    "visa_debit": "Visa Debit",
    "visa_credit": "Visa Credit",
    "mastercard_debit": "Mastercard Debit",
    "mastercard_credit": "Mastercard Credit",
    "amex": "American Express",
    "maestro": "Maestro",
}

def _is_bread_history_item(name):
    return (name or "").strip().lower() == "bread"


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


def _get_cart_allergens(cart):
    allergens = []
    for item in cart.items.select_related("product"):
        raw_allergens = (item.product.allergens or "").strip()
        if not raw_allergens:
            continue
        for allergen in raw_allergens.split(","):
            allergen_name = allergen.strip()
            if allergen_name and allergen_name not in allergens:
                allergens.append(allergen_name)
    return allergens


def _reset_checkout_allergen_confirmation(request):
    request.session.pop("checkout_allergens_confirmed", None)


def _get_favourite_recipe_cards(user, *, limit=3):
    if not user.is_authenticated:
        return []

    favourites = (
        FavouriteRecipe.objects.filter(customer=user)
        .select_related("recipe", "recipe__producer")
        .prefetch_related("recipe__products")
        .order_by("-created_at")[:limit]
    )

    cards = []
    for favourite in favourites:
        recipe = favourite.recipe
        products = list(recipe.products.all())
        image_url = recipe.image_url or next(
            (product.image_url for product in products if product.image_url),
            "",
        )
        cards.append(
            {
                "id": recipe.id,
                "title": recipe.title,
                "producer_name": getattr(recipe.producer, "business_name", "") or getattr(recipe.producer, "username", ""),
                "description": recipe.description,
                "image_url": image_url,
                "seasonal_tag": recipe.get_seasonal_tag_display(),
                "product_count": len(products),
            }
        )
    return cards


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
        {
            "cart": cart,
            "producer_sections": producer_sections,
            "cart_allergens": _get_cart_allergens(cart),
            "favourite_recipes": _get_favourite_recipe_cards(request.user),
        },
    )


@login_required
def confirm_checkout(request):
    _ensure_customer_access(request.user)
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    if cart.items.count() == 0:
        messages.error(request, "Please add an item to your cart before continuing.")
        return redirect("market_payments:cart")

    if request.POST.get("confirm_allergens") != "on":
        messages.error(
            request,
            "Please confirm that you have reviewed the allergen information for every item in your cart before proceeding to checkout.",
        )
        return redirect("market_payments:cart")

    request.session["checkout_allergens_confirmed"] = True
    return redirect("market_payments:payment")


@login_required
def order_history_page(request):
    _ensure_customer_access(request.user)

    orders = []
    for order in _get_customer_order_queryset(request.user):
        payment_items = list(order.items.all())
        if any(_is_bread_history_item(item.product_name) for item in payment_items):
            continue

        latest_payment = order.payments.order_by("-created_at", "-id").first()
        market_order = _get_market_order_for_history(order)
        reviewable_items = []
        if market_order is not None:
            for suborder in market_order.producer_suborders.prefetch_related("items__product").all():
                for item in suborder.items.all():
                    if _is_bread_history_item(item.product_name):
                        continue
                    already_reviewed = ProductReview.objects.filter(
                        user=request.user,
                        product=item.product,
                    ).exists()
                    reviewable_items.append(
                        {
                            "product": item.product,
                            "suborder": suborder,
                            "can_review": suborder.status == ProducerSubOrder.Status.DELIVERED and not already_reviewed,
                            "already_reviewed": already_reviewed,
                            "is_delivered": suborder.status == ProducerSubOrder.Status.DELIVERED,
                        }
                    )
        orders.append(
            {
                "order": order,
                "latest_payment": latest_payment,
                "market_order": market_order,
                "item_count": sum(item.quantity for item in payment_items),
                "items": payment_items,
                "reviewable_items": reviewable_items,
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
    if product.stock_quantity <= 0:
        messages.error(request, f"{product.name} is currently out of stock.")
        return redirect(request.POST.get("next") or "market_payments:cart")
    if not product.is_currently_in_season():
        messages.error(request, f"{product.name} is out of season. {product.seasonal_range_display}.")
        return redirect(request.POST.get("next") or "market_payments:cart")

    cart = _get_or_create_cart(request.user)
    _reset_checkout_allergen_confirmation(request)

    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        defaults={"quantity": 1},
    )

    if not created:
        if item.quantity + 1 > product.stock_quantity:
            messages.error(
                request,
                f"Only {product.stock_quantity} unit(s) of {product.name} are available.",
            )
            return redirect(request.POST.get("next") or "market_payments:cart")
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
        _reset_checkout_allergen_confirmation(request)
        messages.info(request, f"{product_name} removed from your cart.")
        return redirect("market_payments:cart")

    if not item.product.is_active:
        messages.error(request, f"{item.product.name} is no longer available.")
        item.delete()
        _reset_checkout_allergen_confirmation(request)
        return redirect("market_payments:cart")
    if not item.product.is_currently_in_season():
        messages.error(request, f"{item.product.name} is out of season. {item.product.seasonal_range_display}.")
        item.delete()
        _reset_checkout_allergen_confirmation(request)
        return redirect("market_payments:cart")

    if quantity > item.product.stock_quantity:
        messages.error(
            request,
            f"Only {item.product.stock_quantity} unit(s) of {item.product.name} are available.",
        )
        return redirect("market_payments:cart")

    item.quantity = quantity
    item.save(update_fields=["quantity"])
    return redirect("market_payments:cart")


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
            if (not product.is_active) or product.stock_quantity <= 0 or not product.is_currently_in_season():
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

    if cart.items.count() > 0 and not request.session.get("checkout_allergens_confirmed"):
        messages.warning(
            request,
            "Please confirm that you have reviewed the allergen information for every item in your cart before proceeding to checkout.",
        )
        return redirect("market_payments:cart")

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
            "favourite_recipes": _get_favourite_recipe_cards(request.user),
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

    if not request.session.get("checkout_allergens_confirmed"):
        messages.error(
            request,
            "Please confirm that you have reviewed the allergen information for every item in your cart before proceeding to checkout.",
        )
        return redirect("market_payments:cart")

    producer_sections = _group_cart_items_by_producer(cart)
    section_subtotals = {
        section["producer"].id: section["subtotal_amount"]
        for section in producer_sections
    }

    delivery_address = (request.POST.get("delivery_address") or request.user.address or "").strip()
    customer_phone = (request.POST.get("customer_phone") or request.user.phone or "").strip()
    payment_method = (request.POST.get("payment_method") or "visa_debit").strip() or "visa_debit"

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
        if not ci.product.is_currently_in_season():
            messages.error(request, f"{ci.product.name} is out of season. {ci.product.seasonal_range_display}.")
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
            if ci.product.is_low_stock:
                Notification.objects.get_or_create(
                    user=ci.product.producer,
                    product=ci.product,
                    type=Notification.Type.LOW_STOCK,
                    is_resolved=False,
                    defaults={
                        "message": (
                            f"Low Stock Alert: {ci.product.name} - "
                            f"Only {ci.product.stock_quantity} unit(s) remaining."
                        )
                    },
                )

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
            transaction_reference=f"demo-{payment.order_id}",
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
    _ensure_customer_access(request.user)
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    cart.items.all().delete()
    _reset_checkout_allergen_confirmation(request)
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
            "favourite_recipes": _get_favourite_recipe_cards(request.user),
        },
    )
