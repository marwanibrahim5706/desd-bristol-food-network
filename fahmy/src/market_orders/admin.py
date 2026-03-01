from django.contrib import admin
from .models import ProducerSubOrder

@admin.register(ProducerSubOrder)
class ProducerSubOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "producer", "status", "delivery_date")
    list_filter = ("status",)
    search_fields = ("id", "producer__username", "order__id")