from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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


_PRODUCT_CSV_FIELDS = [
    "id",
    "name",
    "category",
    "cash_price",
    "installment_eligible",
    "stock_quantity",
    "reserved_quantity",
    "available_quantity",
]


@router.get("", response_model=list[ProductListItem])
def list_products(
    db: Session = Depends(get_db),
    search: str | None = Query(default=None, max_length=100),
    status: str = Query(default="all"),
    format: str | None = Query(default=None),
    _: User = Depends(require_roles(*_DIRECTORY_ROLES)),
):
    """Step 10 product directory. `search` (optional) does a partial,
    case-insensitive match on name OR category; omitted → every product.
    `format=csv|xlsx|pdf` returns the rows as a download.

    Step 12 note: `status` accepts only `all`. `Product` has no
    active/inactive field today, and `installment_eligible` is a different
    concept — conflating them would be misleading — so no product-status field
    was invented. The param exists for API symmetry with the customer
    directory and as the wiring point if a real status is added later."""
    if status != "all":
        raise HTTPException(
            status_code=422,
            detail="products have no status field yet; only status=all is supported",
        )
    stmt = select(Product).order_by(Product.name)
    if search and search.strip():
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.category.ilike(like))
        )
    rows = db.execute(stmt).scalars().all()
    if format:
        from app.services import reports as reports_service

        if format not in reports_service.EXPORT_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=f"format must be one of {reports_service.EXPORT_FORMATS}",
            )
        data = [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category.value,
                "cash_price": float(p.cash_price),
                "installment_eligible": p.installment_eligible,
                "stock_quantity": p.stock_quantity,
                "reserved_quantity": p.reserved_quantity,
                "available_quantity": p.available_quantity,
            }
            for p in rows
        ]
        content, media_type, ext = reports_service.export(
            format, _PRODUCT_CSV_FIELDS, data, title="Product directory"
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="products.{ext}"'
            },
        )
    return rows


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
