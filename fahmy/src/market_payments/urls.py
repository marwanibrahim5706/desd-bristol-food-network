from django.urls import path
from . import views

app_name = "market_payments"

urlpatterns = [
    path("", views.cart_page, name="home"),  # 👈 add this
    path("cart/", views.cart_page, name="cart"),
    path("cart/add-demo/", views.add_demo_item, name="add_demo_item"),
    path("payment/", views.payment_page, name="payment"),
    path("pay-now/", views.pay_now, name="pay_now"),
    path("receipt/<int:payment_id>/", views.receipt_page, name="receipt"),
]