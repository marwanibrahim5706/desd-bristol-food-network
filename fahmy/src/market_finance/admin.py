from django.contrib import admin

from .models import RecurringOrder, Settlement


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "producer",
        "week_start",
        "week_end",
        "gross_sales",
        "commission_amount",
        "payout_amount",
        "status",
        "payout_reference",
    )
    list_filter = ("status", "week_start")
    search_fields = ("producer__username", "producer__business_name", "payout_reference")
    filter_horizontal = ("included_suborders",)


@admin.register(RecurringOrder)
class RecurringOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "status", "frequency", "next_run_date", "last_run_at")
    list_filter = ("status", "frequency", "next_run_date")
    search_fields = ("customer__username", "customer__email")
