from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Cart, CartItem, Order, OrderItem, Payment, Product


def _get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


@login_required
def cart_page(request):
    cart = _get_or_create_cart(request.user)
    return render(request, "market_payments/cart.html", {"cart": cart})


@login_required
def add_demo_item(request):
    # create a demo product if it doesn't exist
    demo, _ = Product.objects.get_or_create(
        name="Demo Item",
        defaults={"price": Decimal("50.00")},
    )

    cart = _get_or_create_cart(request.user)
    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=demo,
        defaults={"quantity": 1},
    )
    if not created:
        item.quantity += 1
        item.save()

    messages.success(request, "Added Demo Item to cart.")
    return redirect("market_payments:cart")


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
        messages.error(request, "Your cart is empty.")
        return redirect("market_payments:cart")

    # create order
    order = Order.objects.create(
        user=request.user,
        subtotal=cart.subtotal,
        commission=cart.commission,
        total=cart.total,
    )

    # copy cart items to order items
    for ci in cart.items.select_related("product"):
        OrderItem.objects.create(
            order=order,
            product_name=ci.product.name,
            unit_price=ci.product.price,
            quantity=ci.quantity,
        )

    # create ONE payment record
    payment = Payment.objects.create(
        order=order,
        status=Payment.Status.PAID,
        provider="demo",
    )

    # clear cart
    cart.items.all().delete()

    messages.success(request, f"Payment successful. Receipt #{payment.id} generated.")
    return redirect("market_payments:receipt", payment_id=payment.id)


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