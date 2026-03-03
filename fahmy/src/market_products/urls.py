from django.urls import path
from . import views

urlpatterns = [
    path("", views.discovery, name="discovery"),               # /
    path("discover/", views.discovery, name="discovery_alt"),  # /discover/
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
]