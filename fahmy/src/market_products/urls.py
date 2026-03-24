from django.urls import path
from . import views

urlpatterns = [
    path("", views.discovery, name="discovery"),
    path("discover/", views.discovery, name="discovery_alt"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/review/", views.submit_review, name="submit_review"),
    path("producer/products/", views.producer_product_list, name="producer_product_list"),
    path("producer/products/add/", views.producer_product_create, name="producer_product_create"),
    path("producer/products/<int:pk>/edit/", views.producer_product_edit, name="producer_product_edit"),
]
