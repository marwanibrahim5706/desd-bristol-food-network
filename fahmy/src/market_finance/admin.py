from django.contrib import admin

from .models import RecurringOrder, Settlement


admin.site.register(Settlement)
admin.site.register(RecurringOrder)
