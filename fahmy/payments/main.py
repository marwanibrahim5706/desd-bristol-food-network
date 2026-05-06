from decimal import Decimal, ROUND_HALF_UP
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

TWOPLACES = Decimal("0.01")
COMMISSION_RATE = Decimal("0.05")
MARKETPLACE_URL = os.getenv("MARKETPLACE_URL", "http://localhost:8000")

app = FastAPI(title="Payments Service", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def quantize_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


class ProducerLine(BaseModel):
    producer_id: int | None = None
    subtotal: Decimal = Field(..., ge=0)


class CalculateRequest(BaseModel):
    subtotal: Decimal = Field(..., ge=0)
    producer_subtotals: list[ProducerLine] = Field(default_factory=list)


class PayoutRequest(BaseModel):
    settlement_id: int
    producer_id: int
    producer_name: str = ""
    week_start: str
    week_end: str
    amount: Decimal = Field(..., gt=0)
    currency: str = "GBP"


@app.get("/health")
def health():
    return {"status": "ok", "service": "payments"}


def redirect_to_marketplace() -> RedirectResponse:
    return RedirectResponse(url=MARKETPLACE_URL, status_code=303)


@app.get("/")
def root():
    return redirect_to_marketplace()


@app.get("/api/payments/health")
def api_health():
    return health()


@app.post("/api/payments/calculate")
def calculate(payload: CalculateRequest):
    subtotal = quantize_money(payload.subtotal)
    commission_amount = quantize_money(subtotal * COMMISSION_RATE)
    producer_payout_amount = quantize_money(subtotal - commission_amount)

    producer_breakdown = []
    for line in payload.producer_subtotals:
        producer_subtotal = quantize_money(line.subtotal)
        producer_commission = quantize_money(producer_subtotal * COMMISSION_RATE)
        producer_payout = quantize_money(producer_subtotal - producer_commission)
        producer_breakdown.append(
            {
                "producer_id": line.producer_id,
                "subtotal": str(producer_subtotal),
                "commission_amount": str(producer_commission),
                "producer_payout_amount": str(producer_payout),
            }
        )

    return JSONResponse(
        {
            "subtotal": str(subtotal),
            "commission_amount": str(commission_amount),
            "producer_payout_amount": str(producer_payout_amount),
            "producer_breakdown": producer_breakdown,
        }
    )


@app.post("/api/payouts/send")
def send_payout(payload: PayoutRequest):
    """
    External payout API endpoint for producer settlement payments.
    """
    amount = quantize_money(payload.amount)
    reference = f"PAYOUT-{payload.week_start.replace('-', '')}-{payload.producer_id:04d}-{payload.settlement_id:04d}"
    return JSONResponse(
        {
            "success": True,
            "reference": reference,
            "message": "Payout instruction accepted by external payout API.",
            "provider": "external_payout_api",
            "amount": str(amount),
            "currency": payload.currency,
        }
    )


@app.get("/pay/{payment_id}", response_class=HTMLResponse)
def pay_page(
    request: Request,
    payment_id: int,
    order_id: int | None = Query(None),
    subtotal: str | None = Query(None),
    commission_amount: str | None = Query(None),
    total_payable: str | None = Query(None),
    customer_name: str = Query(""),
    success_url: str | None = Query(None),
    cancel_url: str | None = Query(None),
):
    if not all([order_id, subtotal, commission_amount, total_payable, success_url, cancel_url]):
        return redirect_to_marketplace()

    return templates.TemplateResponse(
        "payment_page.html",
        {
            "request": request,
            "payment_id": payment_id,
            "order_id": order_id,
            "subtotal": subtotal,
            "commission_amount": commission_amount,
            "total_payable": total_payable,
            "customer_name": customer_name,
            "success_url": success_url,
            "cancel_url": cancel_url,
        },
    )


@app.post("/pay/{payment_id}")
def pay_submit(
    payment_id: int,
    success_url: str = Form(...),
    cancel_url: str = Form(...),
    payment_method: str = Form("visa_debit"),
    cardholder_name: str = Form(""),
    card_number: str = Form(...),
    expiry: str = Form(...),
    cvv: str = Form(...),
    action: str = Form("pay"),
):
    # Mock payment handling; no raw card details are persisted.
    if action == "cancel":
        return RedirectResponse(url=f"/cancel/{payment_id}?{urlencode({'return_url': cancel_url})}", status_code=303)

    last_four = "".join(ch for ch in card_number if ch.isdigit())[-4:] or "0000"
    transaction_reference = f"mock-{payment_id}-{last_four}"
    query = urlencode(
        {
            "return_url": success_url,
            "transaction_reference": transaction_reference,
            "payment_method": payment_method,
        }
    )
    return RedirectResponse(url=f"/success/{payment_id}?{query}", status_code=303)


@app.get("/success/{payment_id}")
def success(
    payment_id: int,
    return_url: str = Query(...),
    transaction_reference: str = Query(...),
    payment_method: str = Query("visa_debit"),
):
    separator = "&" if "?" in return_url else "?"
    return RedirectResponse(
        url=f"{return_url}{separator}{urlencode({'transaction_reference': transaction_reference, 'payment_method': payment_method})}",
        status_code=303,
    )


@app.get("/cancel/{payment_id}")
def cancel(payment_id: int, return_url: str = Query(...)):
    return RedirectResponse(url=return_url, status_code=303)


@app.get("/api/payments/settlements/sample")
def sample_settlement():
    return {
        "week_start": "2026-03-23",
        "week_end": "2026-03-29",
        "total_orders_value": "150.00",
        "total_commission": "7.50",
        "total_payout": "142.50",
        "status": "GENERATED",
    }
