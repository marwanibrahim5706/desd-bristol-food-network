from django.urls import path

from . import views

app_name = "market_finance"

urlpatterns = [
    path("admin/dashboard/", views.admin_finance_dashboard, name="admin_finance_dashboard"),
    path("admin/dashboard/reports/", views.admin_finance_reports, name="admin_finance_reports"),
    path("admin/dashboard/settlements/", views.admin_finance_settlements, name="admin_finance_settlements"),
    path("admin/dashboard/exports/", views.admin_finance_exports, name="admin_finance_exports"),
    path("admin/dashboard/export/", views.export_admin_finance_csv, name="export_admin_finance_csv"),
    path("admin/dashboard/export/excel/", views.export_admin_finance_excel, name="export_admin_finance_excel"),
    path("admin/dashboard/export/pdf/", views.export_admin_finance_pdf, name="export_admin_finance_pdf"),
    path("admin/dashboard/orders/<int:order_id>/", views.admin_order_finance_detail, name="admin_order_finance_detail"),
    path("admin/dashboard/settlements/generate/", views.generate_settlement, name="generate_settlement"),
    path("admin/dashboard/settlements/<int:settlement_id>/paid/", views.mark_settlement_paid, name="mark_settlement_paid"),
    path("producer/settlements/", views.producer_settlements_dashboard, name="producer_settlements_dashboard"),
    path("settlements/export/", views.export_settlement_csv, name="export_settlement_csv"),
    path("recurring/", views.recurring_orders_dashboard, name="recurring_orders_dashboard"),
    path("recurring/from-order/<int:order_id>/", views.create_recurring_order_from_order, name="create_recurring_order_from_order"),
    path("recurring/<int:recurring_order_id>/next-instance/", views.update_recurring_order_next_instance, name="update_recurring_order_next_instance"),
    path("recurring/<int:recurring_order_id>/run/", views.run_recurring_order_now, name="run_recurring_order_now"),
]
