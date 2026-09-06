"""خدمة قائمة المفضلة (Ticket 2 Epic 4)."""
from __future__ import annotations

from app.extensions import db
from app.models.product import Product
from app.models.wishlist import WishlistItem


class WishlistError(ValueError):
    pass


def add(customer_id: int, product_id: int) -> WishlistItem:
    product = db.session.get(Product, product_id)
    if product is None:
        raise WishlistError("المنتج غير موجود.")
    existing = (
        db.session.query(WishlistItem)
        .filter_by(customer_id=customer_id, product_id=product_id)
        .first()
    )
    if existing is not None:
        return existing
    item = WishlistItem(customer_id=customer_id, product_id=product_id)
    db.session.add(item)
    db.session.flush()
    return item


def remove(customer_id: int, product_id: int) -> None:
    (
        db.session.query(WishlistItem)
        .filter_by(customer_id=customer_id, product_id=product_id)
        .delete(synchronize_session=False)
    )
    db.session.flush()


def list_items(customer_id: int) -> list[WishlistItem]:
    return (
        db.session.query(WishlistItem)
        .filter_by(customer_id=customer_id)
        .order_by(WishlistItem.id.desc())
        .all()
    )


def count(customer_id: int) -> int:
    if not customer_id:
        return 0
    return db.session.query(WishlistItem).filter_by(customer_id=customer_id).count()


def contains(customer_id: int, product_id: int) -> bool:
    if not customer_id:
        return False
    return (
        db.session.query(WishlistItem.id)
        .filter_by(customer_id=customer_id, product_id=product_id)
        .first()
        is not None
    )
