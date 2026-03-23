from market_payments.models import Settlement as PaymentSettlement


class Settlement(PaymentSettlement):
    class Meta:
        proxy = True
        verbose_name = "Settlement"
        verbose_name_plural = "Settlements"

