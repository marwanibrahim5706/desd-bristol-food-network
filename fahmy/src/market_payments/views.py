from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render


from .models import Cart, CartItem, Order, OrderItem, Payment
from market_products.models import Product


def _get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


@login_required
def cart_page(request):
    cart = _get_or_create_cart(request.user)
    return render(request, "market_payments/cart.html", {"cart": cart})


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
def payment_page(request):
    cart = _get_or_create_cart(request.user)

    items = []
    for i in cart.items.select_related("product"):
        items.append(
            {
                "name": i.product.name,
                "quantity": i.quantity,
                "line_total": f"{i.line_total:.2f}",
            }
        )

    return render(
        request,
        "market_payments/payment.html",
        {
            "items": items,
            "subtotal": f"{cart.subtotal:.2f}",
            "commission": f"{cart.commission:.2f}",
            "total": f"{cart.total:.2f}",
            "payment_status": "PENDING",
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

    order = Order.objects.create(
        user=request.user,
        subtotal=cart.subtotal,
        commission=cart.commission,
        total=cart.total,
    )

    for ci in cart.items.select_related("product"):
        OrderItem.objects.create(
            order=order,
            product_name=ci.product.name,
            unit_price=ci.product.price,
            quantity=ci.quantity,
        )

        # Reduce stock after successful order creation
        ci.product.stock_quantity -= ci.quantity

        # Optional: deactivate product when stock reaches 0
        if ci.product.stock_quantity <= 0:
            ci.product.stock_quantity = 0
            ci.product.is_active = False

        ci.product.save()

    payment = Payment.objects.create(
        order=order,
        status=Payment.Status.PAID,
        provider="demo",
    )

    cart.items.all().delete()

    messages.success(request, f"Payment successful. Receipt #{payment.id} generated.")
    return redirect("market_payments:receipt", payment_id=payment.id)

@login_required
def clear_cart(request):
    if request.method != "POST":
        return redirect("market_payments:cart")

    cart = _get_or_create_cart(request.user)
    cart.items.all().delete()
    messages.info(request, "Cart cleared.")
    return redirect("market_payments:cart")

@login_required
def receipt_page(request, payment_id: int):
    payment = get_object_or_404(
        Payment.objects.select_related("order").prefetch_related("order__items"),
        id=payment_id,
        order__user=request.user,
    )
    order = payment.order

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

    return render(
        request,
        "market_payments/receipt.html",
        {
            "payment": payment,
            "order": order,
            "items": items,
        },
    )