"""Payment processing API — Stripe integration stub.

Uses STRIPE_KEY from config to simulate payment processing.
In the real app this would call the Stripe SDK; here we simulate
success/failure for benchmark purposes.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from src.config import settings

router = APIRouter(prefix="/api/payments", tags=["payments"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class PaymentIntent(BaseModel):
    order_id: str
    amount: float = Field(..., gt=0)
    currency: str = "usd"


class PaymentResponse(BaseModel):
    payment_id: str
    order_id: str
    amount: float
    currency: str
    status: str
    created_at: str


class RefundRequest(BaseModel):
    payment_id: str
    amount: float | None = None  # None = full refund


class WebhookEvent(BaseModel):
    event_type: str
    payment_id: str
    data: dict


# ── In-memory payment store ──────────────────────────────────────────────────

_payments: dict[str, dict] = {}
_payment_counter: int = 0


def _next_payment_id() -> str:
    global _payment_counter
    _payment_counter += 1
    return f"PAY-{_payment_counter:05d}"


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/create", response_model=PaymentResponse, status_code=201)
def create_payment(body: PaymentIntent):
    if not settings.STRIPE_KEY:
        raise HTTPException(status_code=503, detail="Payment provider not configured")

    payment_id = _next_payment_id()
    now = datetime.now(timezone.utc).isoformat()

    payment = {
        "payment_id": payment_id,
        "order_id": body.order_id,
        "amount": body.amount,
        "currency": body.currency,
        "status": "succeeded",
        "created_at": now,
    }
    _payments[payment_id] = payment
    return PaymentResponse(**payment)


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: str):
    payment = _payments.get(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentResponse(**payment)


@router.post("/refund")
def refund_payment(body: RefundRequest):
    payment = _payments.get(body.payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment["status"] == "refunded":
        raise HTTPException(status_code=400, detail="Payment already refunded")

    refund_amount = body.amount or payment["amount"]
    if refund_amount > payment["amount"]:
        raise HTTPException(status_code=400, detail="Refund exceeds original amount")

    payment["status"] = "refunded"
    payment["refund_amount"] = refund_amount
    return {
        "payment_id": payment["payment_id"],
        "refund_amount": refund_amount,
        "status": "refunded",
    }


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(None, alias="Stripe-Signature"),
):
    """Verify and process Stripe webhook events."""
    payload = await request.body()

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    # Verify signature using STRIPE_KEY as webhook secret
    expected = hmac.new(
        settings.STRIPE_KEY.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, stripe_signature):
        raise HTTPException(status_code=400, detail="Signature verification failed")

    return {"status": "received"}
