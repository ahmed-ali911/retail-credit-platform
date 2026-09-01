from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.product import (
    ProductCreate,
    ProductListItem,
    ProductOut,
    StockAdjustmentRequest,
)
from app.services import config_service as cfg
from app.services.audit import record_event
from app.services.config_service import ConfigService

router = APIRouter(prefix="/products", tags=["products"])

_DIRECTORY_ROLES = (
    UserRole.sales_employee,
    UserRole.credit_officer,
    UserRole.credit_manager,
    UserRole.finance_officer,
    UserRole.admin,
)
_STOCK_ADJUST_ROLES = (UserRole.finance_officer, UserRole.admin)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    opening_stock = data.pop("stock_quantity", None)
    if opening_stock is None:
        opening_stock = ConfigService(db).get_int(cfg.KEY_DEFAULT_INITIAL_STOCK)
    product = Product(**data, stock_quantity=opening_stock)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=list[ProductListItem])
def list_products(
    db: Session = Depends(get_db),
    search: str | None = Query(default=None, max_length=100),
    _: User = Depends(require_roles(*_DIRECTORY_ROLES)),
):
    """Step 10 product directory. `search` (the only parameter) does a partial,
    case-insensitive match on name OR category; omitted → every product (the
    Inventory screen needs the full list and there is no other list endpoint)."""
    stmt = select(Product).order_by(Product.name)
    if search and search.strip():
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.category.ilike(like))
        )
    return db.execute(stmt).scalars().all()


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/{product_id}/stock-adjustment", response_model=ProductOut)
def adjust_stock(
    product_id: int,
    payload: StockAdjustmentRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_STOCK_ADJUST_ROLES)),
):
    """Step 10 — privileged manual stock correction (restock / write-down).

    Not maker-checker gated: this is a stock count, not a financial transaction.
    (Judgment call, not confirmed policy — see the README business-decision
    register; Finance may want it gated later.)
    """
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    before = product.stock_quantity
    after = before + payload.delta
    if after < product.reserved_quantity:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Adjustment would drop stock_quantity to {after}, below the "
                f"reserved quantity of {product.reserved_quantity}."
            ),
        )

    product.stock_quantity = after
    record_event(
        db,
        user_id=actor.id,
        action="stock_adjustment",
        entity_type="Product",
        entity_id=product.id,
        before={"stock_quantity": before},
        after={
            "stock_quantity": after,
            "delta": payload.delta,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(product)
    return product
