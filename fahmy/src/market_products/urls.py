from django.urls import path
from . import views

urlpatterns = [
    path("", views.discovery, name="discovery"),               # /
    path("discover/", views.discovery, name="discovery_alt"),  # /discover/
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/review/", views.submit_review, name="submit_review"),
]
