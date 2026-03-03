from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

def home(_request):
    return HttpResponse("Home OK (customer landing page)")

urlpatterns = [
    # keep the health/home check but don’t steal the homepage
    path("health/", home),

    path("admin/", admin.site.urls),

    # teammate routes
    path("accounts/", include("accounts.urls")),
    path("orders/", include("market_orders.urls")),
    path("payments/", include("market_payments.urls")),

    # ✅ your discovery + product detail as homepage
    path("", include("market_products.urls")),
]