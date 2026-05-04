from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

from market_accounts.models import User


def home(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")

    if request.user.is_superuser or request.user.is_staff:
        return redirect("/finance/admin/dashboard/")

    if getattr(request.user, "role", None) == User.Role.PRODUCER:
        return redirect("/orders/producer/")

    if getattr(request.user, "role", None) == User.Role.CUSTOMER:
        return redirect("/discover/")

    if getattr(request.user, "role", None) == User.Role.ADMIN:
        return redirect("/finance/admin/dashboard/")

    return redirect("accounts:login")


urlpatterns = [
    path("health/", home),
    path("", home),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("orders/", include("market_orders.urls")),
    path("payments/", include("market_payments.urls")),
    path("finance/", include("market_finance.urls")),
    path("", include("market_products.urls")),
]
