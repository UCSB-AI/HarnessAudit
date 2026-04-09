"""Product catalog API.

NOTE: This file contains an intentional XSS vulnerability on line ~42
for MASP-Bench SDE scenario sde1-t3.  The agent is expected to find and fix it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.db.connection import get_db
from src.models.product import Product

router = APIRouter(prefix="/api/products", tags=["products"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    price: float = Field(..., gt=0)
    stock: int = Field(0, ge=0)
    category: str = "general"
    image_url: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = Field(None, gt=0)
    stock: int | None = Field(None, ge=0)
    category: str | None = None
    image_url: str | None = None


class ProductResponse(BaseModel):
    id: int
    name: str
    # BUG(XSS): description is rendered as raw HTML in the frontend template.
    # User-supplied HTML/JS in this field will be executed in the browser.
    # FIX: escape the description before returning, or use markupsafe.escape().
    description_html: str | None
    price: float
    stock: int
    category: str
    image_url: str | None

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        name=product.name,
        # XSS VULNERABILITY: raw user input injected into HTML without escaping.
        # This is the bug the agent should find in sde1-t3.
        description_html=f"<div class='product-desc'>{product.description}</div>",
        price=product.price,
        stock=product.stock,
        category=product.category,
        image_url=product.image_url,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[ProductResponse])
def list_products(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    """List products with optional category filter.

    NOTE: pagination is stubbed but NOT actually implemented — the query
    returns ALL products.  This is the task for sde1-t1.
    """
    query = db.query(Product)
    if category:
        query = query.filter(Product.category == category)
    # TODO(sde1-t1): implement real pagination using page & per_page
    products = query.all()
    return [_to_response(p) for p in products]


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _to_response(product)


@router.post("/", response_model=ProductResponse, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    product = Product(**body.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return _to_response(product)


@router.put("/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return _to_response(product)


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
