from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "market_payments"

urlpatterns = [
    path("", views.cart_page, name="home"),
    path("cart/", views.cart_page, name="cart"),
    path("admin/dashboard/", RedirectView.as_view(url="/finance/admin/dashboard/", permanent=False)),
    path("admin/dashboard/settlements/generate/", RedirectView.as_view(url="/finance/admin/dashboard/", permanent=False)),
    path("admin/dashboard/settlements/<int:settlement_id>/paid/", RedirectView.as_view(url="/finance/admin/dashboard/", permanent=False)),
    path("orders/", views.order_history_page, name="order_history"),
    path("orders/<int:order_id>/reorder/", views.reorder_order, name="reorder_order"),
    path("payment/", views.payment_page, name="payment"),
    path("cart/confirm-checkout/", views.confirm_checkout, name="confirm_checkout"),
    path("pay-now/", views.pay_now, name="pay_now"),
    path("payment/complete/<int:payment_id>/", views.payment_complete, name="payment_complete"),
    path("payment/cancel/<int:payment_id>/", views.payment_cancel, name="payment_cancel"),
    path("receipt/<int:payment_id>/", views.receipt_page, name="receipt"),
    path("clear-cart/", views.clear_cart, name="clear_cart"),
    path("cart/add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
    path("cart/item/<int:item_id>/update/", views.update_cart_item, name="update_cart_item"),
]
