"""المنتجات ومتغيراتها (Variants) — كل SKU يمثل وحدة تخزين مستقلة.

القرارات:
- المنتج (Product) هو الوحدة العليا للعرض في المتجر (اسم، وصف، صور، سعر أساسي).
- المتغير (ProductVariant) هو الوحدة الفعلية للمخزون والبيع (SKU + باركود + سعر تكلفة/بيع).
- المنتج بدون Variants يُعامَل بمتغير افتراضي يُنشأ تلقائيًا (variant.name = "افتراضي").
- المتغيرات يمكن أن تختلف في اللون فقط (الطلب الحالي)؛ البنية تسمح بأبعاد إضافية لاحقًا.
- الباركود يُولَّد تلقائيًا (EAN-13) لو Setting products.autogenerate_barcode=true.
- تكلفة المتغير: `avg_cost` (Weighted Average) — تُعاد الحسبة عند كل شراء.
- الرصيد: `stock_qty` يُحدَّث تلقائيًا من InventoryMovement.

**قاعدة ذهبية (Epic 2 من تذكرة تحسينات المنتج):**
`compare_at_price` على ProductVariant عرض فقط — السعر الفعلي المدفوع في القيد
المحاسبي هو `price` دائمًا. لا يُقرأ `compare_at_price` من طبقة الخدمة المحاسبية
(`app/services/sales.py`, `app/services/orders.py`, `app/services/order_accounting.py`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


MONEY = Numeric(18, 3)
QTY = Numeric(18, 3)  # الكمية أيضًا بـ 3 خانات للسماح بـ نصف كيلو، ربع لتر، إلخ.


class Product(db.Model, TimestampMixin):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name_ar = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category_id = Column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    brand = Column(String(120), nullable=True)
    unit = Column(String(20), nullable=True, default="قطعة")  # قطعة/كجم/لتر/متر
    # النسبة الضريبية على مستوى المنتج — لو None نستخدم Setting العامة tax.default_rate.
    tax_rate_override = Column(MONEY, nullable=True)

    # سعر البيع الافتراضي (يُنسَخ للمتغيرات التي لا تحدد سعرًا خاصًا)
    default_price = Column(MONEY, nullable=False, default=0)

    is_active = Column(Boolean, nullable=False, default=True)

    # Epic 2 — عرض فقط (وقت انتهاء العرض للعداد التنازلي)
    offer_ends_at = Column(DateTime(timezone=True), nullable=True)

    # Epic 3 — مواصفات المنتج (اختيارية، عرض فقط)
    origin_country = Column(String(80), nullable=True)
    piece_count = Column(Integer, nullable=True)

    category = relationship("Category", lazy="joined")
    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductVariant.id",
    )
    images = relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductImage.display_order, ProductImage.id",
    )
    features = relationship(
        "ProductFeature",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductFeature.display_order, ProductFeature.id",
    )
    composition = relationship(
        "ProductCompositionLine",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductCompositionLine.display_order, ProductCompositionLine.id",
    )
    relations = relationship(
        "ProductRelation",
        foreign_keys="ProductRelation.product_id",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductRelation.display_order, ProductRelation.id",
    )
    reviews = relationship(
        "ProductReview",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductReview.created_at.desc()",
    )

    @property
    def total_stock(self) -> Decimal:
        """إجمالي رصيد المخزون عبر كل المتغيرات."""
        return sum((Decimal(v.stock_qty or 0) for v in self.variants), Decimal("0"))

    @property
    def default_variant(self):
        """المتغير الافتراضي (لو المنتج بلا variants حقيقية)."""
        return self.variants[0] if self.variants else None

    @property
    def primary_image(self):
        """الصورة الرئيسية للعرض في كارت المنتج/المعرض. None لو مافيش صور."""
        if not self.images:
            return None
        for img in self.images:
            if img.is_primary:
                return img
        return self.images[0]

    @property
    def offer_is_live(self) -> bool:
        """هل عرض الوقت المحدد لا يزال ساريًا؟ (Epic 2 — عرض فقط)."""
        if self.offer_ends_at is None:
            return False
        # عالجنا كلا حالتي timezone-aware و naive حتى لا تنكسر لو SQLite
        end = self.offer_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end > datetime.now(timezone.utc)

    @property
    def approved_reviews(self):
        """قائمة المراجعات المعتمدة فقط (للعرض للعموم)."""
        return [r for r in self.reviews if r.is_approved]

    @property
    def avg_rating(self) -> Decimal:
        """متوسط تقييم المراجعات المعتمدة (0 لو لا توجد)."""
        approved = self.approved_reviews
        if not approved:
            return Decimal("0")
        total = sum(Decimal(r.rating) for r in approved)
        return (total / Decimal(len(approved))).quantize(Decimal("0.1"))

    @property
    def review_count(self) -> int:
        return len(self.approved_reviews)

    def __repr__(self) -> str:
        return f"<Product {self.id} {self.name_ar}>"


class ProductVariant(db.Model, TimestampMixin):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_variant_sku"),
        UniqueConstraint("barcode", name="uq_variant_barcode"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    sku = Column(String(80), nullable=False, index=True)
    barcode = Column(String(40), nullable=True, index=True)

    # الأبعاد المميزة للمتغير — حاليًا اللون فقط، البنية قابلة للتوسع.
    color = Column(String(60), nullable=True)
    variant_name = Column(String(120), nullable=True)  # مثال: "أحمر" أو "افتراضي"

    price = Column(MONEY, nullable=False, default=0)          # سعر البيع الفعلي (المستخدم في القيود)
    # قاعدة ذهبية: compare_at_price للعرض فقط — سعر مشطوب قبل الخصم.
    # لا يُقرأ من services/sales.py, services/orders.py, services/order_accounting.py.
    compare_at_price = Column(MONEY, nullable=True)
    avg_cost = Column(MONEY, nullable=False, default=0)       # تكلفة متوسط مرجّح (تُحدَّث تلقائيًا)
    stock_qty = Column(QTY, nullable=False, default=0)        # الرصيد الحالي
    reorder_level = Column(QTY, nullable=False, default=0)    # الحد الأدنى للتنبيه

    is_active = Column(Boolean, nullable=False, default=True)

    product = relationship("Product", back_populates="variants")

    @property
    def display_name(self) -> str:
        """اسم للعرض في الفواتير: `اسم المنتج - اللون`."""
        base = self.product.name_ar if self.product else ""
        if self.color:
            return f"{base} - {self.color}"
        if self.variant_name and self.variant_name != "افتراضي":
            return f"{base} - {self.variant_name}"
        return base

    @property
    def is_low_stock(self) -> bool:
        return Decimal(self.stock_qty or 0) <= Decimal(self.reorder_level or 0)

    @property
    def is_on_sale(self) -> bool:
        """هل توجد شارة خصم صالحة؟ (`compare_at_price > price`)."""
        if self.compare_at_price is None:
            return False
        try:
            return Decimal(str(self.compare_at_price)) > Decimal(str(self.price))
        except Exception:
            return False

    @property
    def savings_amount(self) -> Decimal:
        """قيمة التوفير المطلقة (compare_at_price - price)، 0 لو ليس ساري."""
        if not self.is_on_sale:
            return Decimal("0")
        return (Decimal(str(self.compare_at_price)) - Decimal(str(self.price))).quantize(
            Decimal("0.001")
        )

    @property
    def discount_percent(self) -> Decimal:
        """نسبة الخصم % (`(compare - price) / compare * 100`)، 0 لو ليس ساري."""
        if not self.is_on_sale:
            return Decimal("0")
        cmp = Decimal(str(self.compare_at_price))
        if cmp <= 0:
            return Decimal("0")
        pct = (cmp - Decimal(str(self.price))) / cmp * Decimal("100")
        return pct.quantize(Decimal("0.1"))

    def __repr__(self) -> str:
        return f"<Variant {self.sku} qty={self.stock_qty}>"
