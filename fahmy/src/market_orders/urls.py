from django.urls import path
from .views import producer_dashboard, producer_suborder_detail, producer_suborder_change_status

urlpatterns = [
    path("producer/dashboard/", producer_dashboard, name="producer_dashboard"),
    path("producer/suborders/<int:suborder_id>/", producer_suborder_detail, name="producer_suborder_detail"),
    path("producer/suborders/<int:suborder_id>/status/", producer_suborder_change_status, name="producer_suborder_change_status"),
]