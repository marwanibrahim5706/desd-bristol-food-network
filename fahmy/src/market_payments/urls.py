from django.urls import path
from . import views

app_name = "market_payments"

urlpatterns = [
    path("", views.cart_page, name="home"),  # 👈 add this
    path("cart/", views.cart_page, name="cart"),
    path("orders/", views.order_history_page, name="order_history"),
    path("orders/<int:order_id>/reorder/", views.reorder_order, name="reorder_order"),
    path("payment/", views.payment_page, name="payment"),
    path("pay-now/", views.pay_now, name="pay_now"),
    path("receipt/<int:payment_id>/", views.receipt_page, name="receipt"),
    path("clear-cart/", views.clear_cart, name="clear_cart"),
    path("cart/add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
]
