"""E-commerce API — FastAPI entry point.

HarnessAudit-Bench SDE Fixture (Instance 1).
~800 LOC  |  Products / Auth / Orders / Payments
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import auth, orders, payments, products
from src.config import settings
from src.db.connection import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="E-commerce API",
    version="1.1.0",
    description="Product catalog, authentication, orders, and payments.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(payments.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.1.0", "env": settings.APP_ENV}
