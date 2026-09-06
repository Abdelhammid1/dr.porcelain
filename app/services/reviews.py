"""خدمة تقييمات المنتجات (Epic 6 من تذكرة تحسينات المنتج).

- purchase eligibility: العميل مشترى منتج فعليًا لو له فاتورة POSTED (وليست RETURNED)
  تحتوي متغيرًا من هذا المنتج. المصدر: SalesInvoice + SalesInvoiceLine
  (يغطي البيع النقدي المباشر من POS وأيضًا الطلبات الأونلاين التي تم تسليمها
  عبر hook Phase 8 اللي بيولّد SalesInvoice تلقائيًا).
- كل عميل يقيّم منتجًا مرة واحدة (UniqueConstraint).
- كل تقييم يبدأ `is_approved=False` — أدمن يوافق أو يرفض.
"""
from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.party import Party, PartyType
from app.models.product import Product, ProductVariant
from app.models.review import ProductReview
from app.models.sales import InvoiceStatus, SalesInvoice, SalesInvoiceLine


class ReviewError(ValueError):
    pass


def customer_has_purchased_product(customer_id: int, product_id: int) -> bool:
    """يفحص ما إذا كان للعميل فاتورة POSTED/PARTIAL_RETURNED تحتوي منتجًا مطابقًا."""
    q = (
        db.session.query(SalesInvoiceLine.id)
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_id)
        .join(ProductVariant, ProductVariant.id == SalesInvoiceLine.variant_id)
        .filter(SalesInvoice.customer_id == customer_id)
        .filter(SalesInvoice.status != InvoiceStatus.RETURNED)
        .filter(ProductVariant.product_id == product_id)
        .limit(1)
    )
    return q.first() is not None


def submit_review(*, customer_id: int, product_id: int, rating: int,
                  comment: str | None = None) -> ProductReview:
    """يُنشئ مراجعة جديدة (بحالة قيد المراجعة) بعد التحقق من الأهلية."""
    # فحص الـ rating
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ReviewError("التقييم يجب أن يكون رقمًا من 1 إلى 5.")
    if rating < 1 or rating > 5:
        raise ReviewError("التقييم يجب أن يكون من 1 إلى 5 نجوم.")

    # فحص العميل
    customer = db.session.get(Party, customer_id)
    if customer is None or customer.type != PartyType.CUSTOMER:
        raise ReviewError("العميل غير موجود.")

    # فحص المنتج
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        raise ReviewError("المنتج غير موجود.")

    # فحص الشراء الفعلي
    if not customer_has_purchased_product(customer.id, product.id):
        raise ReviewError("لا يمكنك تقييم منتج لم تشتره من قبل.")

    # فحص عدم التكرار
    existing = (
        db.session.query(ProductReview)
        .filter_by(customer_id=customer.id, product_id=product.id)
        .first()
    )
    if existing is not None:
        raise ReviewError("سبق أن قيّمت هذا المنتج.")

    comment_clean = (comment or "").strip() or None

    review = ProductReview(
        product_id=product.id,
        customer_id=customer.id,
        rating=rating,
        comment=comment_clean,
        is_approved=False,
    )
    db.session.add(review)
    db.session.flush()
    return review


def approve_review(review_id: int) -> ProductReview:
    review = db.session.get(ProductReview, review_id)
    if review is None:
        raise ReviewError("المراجعة غير موجودة.")
    review.is_approved = True
    db.session.flush()
    return review


def reject_review(review_id: int) -> None:
    """يحذف المراجعة (رفض نهائي)."""
    review = db.session.get(ProductReview, review_id)
    if review is None:
        return
    db.session.delete(review)
    db.session.flush()


def average_rating(product_id: int) -> tuple[Decimal, int]:
    """يُرجِع (متوسط, عدد) للمراجعات المعتمدة فقط."""
    approved = (
        db.session.query(ProductReview)
        .filter_by(product_id=product_id, is_approved=True)
        .all()
    )
    if not approved:
        return Decimal("0"), 0
    total = sum(Decimal(r.rating) for r in approved)
    return (total / Decimal(len(approved))).quantize(Decimal("0.1")), len(approved)
