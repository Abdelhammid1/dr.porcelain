"""المنتجات ومتغيراتها (Variants) — كل SKU يمثل وحدة تخزين مستقلة.

القرارات:
- المنتج (Product) هو الوحدة العليا للعرض في المتجر (اسم، وصف، صور، سعر أساسي).
- المتغير (ProductVariant) هو الوحدة الفعلية للمخزون والبيع (SKU + باركود + سعر تكلفة/بيع).
- المنتج بدون Variants يُعامَل بمتغير افتراضي يُنشأ تلقائيًا (variant.name = "افتراضي").
- المتغيرات يمكن أن تختلف في اللون فقط (الطلب الحالي)؛ البنية تسمح بأبعاد إضافية لاحقًا.
- الباركود يُولَّد تلقائيًا (EAN-13) لو Setting products.autogenerate_barcode=true.
- تكلفة المتغير: `avg_cost` (Weighted Average) — تُعاد الحسبة عند كل شراء.
- الرصيد: `stock_qty` يُحدَّث تلقائيًا من InventoryMovement.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
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

    category = relationship("Category", lazy="joined")
    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductVariant.id",
    )

    @property
    def total_stock(self) -> Decimal:
        """إجمالي رصيد المخزون عبر كل المتغيرات."""
        return sum((Decimal(v.stock_qty or 0) for v in self.variants), Decimal("0"))

    @property
    def default_variant(self):
        """المتغير الافتراضي (لو المنتج بلا variants حقيقية)."""
        return self.variants[0] if self.variants else None

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

    price = Column(MONEY, nullable=False, default=0)          # سعر البيع لهذا المتغير
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

    def __repr__(self) -> str:
        return f"<Variant {self.sku} qty={self.stock_qty}>"
