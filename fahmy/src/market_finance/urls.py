from django.urls import path

from . import views

app_name = "market_finance"

urlpatterns = [
    path("admin/dashboard/", views.admin_finance_dashboard, name="admin_finance_dashboard"),
    path("admin/dashboard/export/", views.export_admin_finance_csv, name="export_admin_finance_csv"),
    path("admin/dashboard/settlements/generate/", views.generate_settlement, name="generate_settlement"),
    path("admin/dashboard/settlements/<int:settlement_id>/paid/", views.mark_settlement_paid, name="mark_settlement_paid"),
    path("producer/settlements/", views.producer_settlements_dashboard, name="producer_settlements_dashboard"),
    path("settlements/export/", views.export_settlement_csv, name="export_settlement_csv"),
]
