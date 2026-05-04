from django.test.runner import DiscoverRunner


class AppLabelTestRunner(DiscoverRunner):
    """
    Ensure `python src/manage.py test` runs the marketplace app suites.

    The repository keeps Django apps under `src/`, so bare unittest discovery
    from the Compose workdir can miss them unless app labels are supplied.
    """

    default_app_labels = [
        "accounts",
        "market_accounts",
        "market_products",
        "market_cart",
        "market_orders",
        "market_payments",
        "market_finance",
        "market_alerts",
    ]

    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or self.default_app_labels, **kwargs)
