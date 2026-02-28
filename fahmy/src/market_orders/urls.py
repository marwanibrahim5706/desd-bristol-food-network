from django.urls import path
from .views import producer_dashboard, producer_suborder_detail

urlpatterns = [
    path("producer/dashboard/", producer_dashboard, name="producer_dashboard"),
    path("producer/suborders/<int:suborder_id>/", producer_suborder_detail, name="producer_suborder_detail"),
]