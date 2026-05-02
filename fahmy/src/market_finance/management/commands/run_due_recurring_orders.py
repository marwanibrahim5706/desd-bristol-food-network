from django.core.management.base import BaseCommand, CommandError

from market_finance.services import generate_due_recurring_orders


class Command(BaseCommand):
    help = "Generate due marketplace orders from active recurring-order templates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="run_date",
            help="Optional run date in YYYY-MM-DD format. Defaults to today in the current timezone.",
        )

    def handle(self, *args, **options):
        try:
            generated_orders = generate_due_recurring_orders(run_date=options.get("run_date"))
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(f"Generated {len(generated_orders)} recurring order(s).")
        )
