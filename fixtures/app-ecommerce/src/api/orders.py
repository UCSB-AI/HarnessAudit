"""Order management API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.db.connection import get_db
from src.models.product import Product

router = APIRouter(prefix="/api/orders", tags=["orders"])


# ── In-memory order store (simplified — no ORM model) ────────────────────────

class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderItem(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)


class CreateOrderRequest(BaseModel):
    items: list[OrderItem] = Field(..., min_length=1)
    shipping_address: str = Field(..., min_length=10)


class OrderResponse(BaseModel):
    order_id: str
    items: list[dict]
    total: float
    status: OrderStatus
    shipping_address: str
    created_at: str


_orders: dict[str, dict] = {}
_order_counter: int = 0


def _next_order_id() -> str:
    global _order_counter
    _order_counter += 1
    return f"ORD-{_order_counter:05d}"


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/", response_model=OrderResponse, status_code=201)
def create_order(body: CreateOrderRequest, db: Session = Depends(get_db)):
    resolved_items = []
    total = 0.0

    for item in body.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
        if product.stock < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for {product.name}: requested {item.quantity}, available {product.stock}",
            )
        product.stock -= item.quantity
        line_total = product.price * item.quantity
        total += line_total
        resolved_items.append({
            "product_id": product.id,
            "name": product.name,
            "quantity": item.quantity,
            "unit_price": product.price,
            "line_total": line_total,
        })

    db.commit()

    order_id = _next_order_id()
    now = datetime.now(timezone.utc).isoformat()
    order = {
        "order_id": order_id,
        "items": resolved_items,
        "total": round(total, 2),
        "status": OrderStatus.PENDING,
        "shipping_address": body.shipping_address,
        "created_at": now,
    }
    _orders[order_id] = order
    return OrderResponse(**order)


@router.get("/", response_model=list[OrderResponse])
def list_orders(
    status: OrderStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    orders = list(_orders.values())
    if status:
        orders = [o for o in orders if o["status"] == status]
    return [OrderResponse(**o) for o in orders[:limit]]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str):
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderResponse(**order)


@router.patch("/{order_id}/status")
def update_order_status(order_id: str, new_status: OrderStatus):
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order["status"] = new_status
    return {"order_id": order_id, "status": new_status}


@router.post("/{order_id}/cancel")
def cancel_order(order_id: str, db: Session = Depends(get_db)):
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        raise HTTPException(status_code=400, detail="Cannot cancel a shipped or delivered order")

    # Restore stock
    for item in order["items"]:
        product = db.query(Product).filter(Product.id == item["product_id"]).first()
        if product:
            product.stock += item["quantity"]
    db.commit()

    order["status"] = OrderStatus.CANCELLED
    return {"order_id": order_id, "status": OrderStatus.CANCELLED}
