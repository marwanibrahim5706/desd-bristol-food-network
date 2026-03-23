import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from .models import Payment

TWOPLACES = Decimal("0.01")
COMMISSION_RATE = Decimal("0.05")
PAYOUT_RATE = Decimal("0.95")


def quantize_money(value):
    """
    Centralise money rounding so reporting and persistence stay consistent.
    """
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def calculate_commission_breakdown(subtotal):
    """
    Return platform commission and producer payout for a given subtotal.

    This keeps the 5% / 95% rule in one place for tests and demo explanation.
    """
    subtotal = quantize_money(subtotal)
    commission_amount = quantize_money(subtotal * COMMISSION_RATE)
    producer_payout_amount = quantize_money(subtotal - commission_amount)
    return {
        "subtotal": subtotal,
        "commission_amount": commission_amount,
        "producer_payout_amount": producer_payout_amount,
    }


def calculate_checkout_breakdown(subtotal, producer_subtotals):
    """
    Build the full checkout money breakdown locally.

    This is also used as a safe fallback if the payments microservice is
    temporarily unavailable during development or tests.
    """
    total_breakdown = calculate_commission_breakdown(subtotal)
    producer_breakdown = []
    for producer_id, producer_subtotal in producer_subtotals.items():
        breakdown = calculate_commission_breakdown(producer_subtotal)
        producer_breakdown.append(
            {
                "producer_id": int(producer_id),
                **breakdown,
            }
        )

    return {
        **total_breakdown,
        "producer_breakdown": producer_breakdown,
        "source": "fallback",
    }


def request_checkout_breakdown(subtotal, producer_subtotals, *, use_fallback=True):
    """
    Ask the payments microservice for the final commission and payout values.

    The Django app uses the service result during checkout, but falls back to
    the same local calculation in environments where the microservice is not
    running yet.
    """
    payload = {
        "subtotal": str(quantize_money(subtotal)),
        "producer_subtotals": [
            {"producer_id": int(producer_id), "subtotal": str(quantize_money(producer_subtotal))}
            for producer_id, producer_subtotal in producer_subtotals.items()
        ],
    }
    endpoint = f"{settings.PAYMENTS_SERVICE_URL.rstrip('/')}/api/payments/calculate"
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=5) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        if not use_fallback:
            raise
        return calculate_checkout_breakdown(subtotal, producer_subtotals)

    return {
        "subtotal": quantize_money(parsed["subtotal"]),
        "commission_amount": quantize_money(parsed["commission_amount"]),
        "producer_payout_amount": quantize_money(parsed["producer_payout_amount"]),
        "producer_breakdown": [
            {
                "producer_id": int(line["producer_id"]),
                "subtotal": quantize_money(line["subtotal"]),
                "commission_amount": quantize_money(line["commission_amount"]),
                "producer_payout_amount": quantize_money(line["producer_payout_amount"]),
            }
            for line in parsed.get("producer_breakdown", [])
        ],
        "source": "payments_api",
    }


def create_payment_record(*, order, provider="demo", status=Payment.Status.PAID, transaction_reference=""):
    """
    Persist a payment snapshot for the checkout event.

    We call this explicitly from the checkout flow rather than a signal so the
    team can see exactly where payment persistence happens during order creation.
    """
    breakdown = calculate_commission_breakdown(order.subtotal)
    return Payment.objects.create(
        order=order,
        subtotal=breakdown["subtotal"],
        commission_amount=breakdown["commission_amount"],
        producer_payout_amount=breakdown["producer_payout_amount"],
        status=status,
        provider=provider,
        transaction_reference=transaction_reference,
    )
