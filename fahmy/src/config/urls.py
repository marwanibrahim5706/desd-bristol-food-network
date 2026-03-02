from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

def home(_request):
    return HttpResponse("Home OK (customer landing page)")

urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("orders/", include("market_orders.urls")),
    path("payments/", include("market_payments.urls")),
]