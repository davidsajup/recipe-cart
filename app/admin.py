"""Catalogue CRUD. If ADMIN_TOKEN is set, writes need an `X-Admin-Token` header."""
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .db import ProductRow, get_session
from .schemas import ProductIn, ProductOut

router = APIRouter(prefix="/products", tags=["catalogue"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        return  # open (local dev)
    if x_admin_token is None or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Admin token required or incorrect.")


def _out(row: ProductRow) -> ProductOut:
    return ProductOut(id=row.id, name=row.name, pack_size=row.pack_size, unit=row.unit, price=row.price)


def _get(session: Session, product_id: int) -> ProductRow:
    row = session.get(ProductRow, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return row


def _commit(session: Session, name: str) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"A product named '{name}' already exists.")


@router.get("", response_model=list[ProductOut])
def list_products(session: Session = Depends(get_session)) -> list[ProductOut]:
    return [_out(r) for r in session.exec(select(ProductRow).order_by(ProductRow.name)).all()]


@router.post("", response_model=ProductOut, status_code=201, dependencies=[Depends(require_admin)])
def create_product(body: ProductIn, session: Session = Depends(get_session)) -> ProductOut:
    row = ProductRow(**body.model_dump())
    session.add(row)
    _commit(session, body.name)
    session.refresh(row)
    return _out(row)


@router.put("/{product_id}", response_model=ProductOut, dependencies=[Depends(require_admin)])
def update_product(product_id: int, body: ProductIn, session: Session = Depends(get_session)) -> ProductOut:
    row = _get(session, product_id)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    session.add(row)
    _commit(session, body.name)
    session.refresh(row)
    return _out(row)


@router.delete("/{product_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_product(product_id: int, session: Session = Depends(get_session)) -> Response:
    session.delete(_get(session, product_id))
    session.commit()
    return Response(status_code=204)
