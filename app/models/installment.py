"""نموذج البيع بالتقسيط.

كل خطة تقسيط (InstallmentPlan) مرتبطة بفاتورة بيع (SalesInvoice) واحدة (one-to-one).
الخطة تحتوي على مجموعة أسطر جدول سداد (InstallmentScheduleLine)، وكل عملية تحصيل
تُسجَّل كـ InstallmentCollection (يمكن أن يحمل السطر تحصيلات جزئية متعددة).

قواعد:
- الفاتورة تُسلَّم فورًا (بغض النظر عن قيمة المقدم).
- الجدول يُبنى تلقائيًا: مبلغ الأصل - المقدم ÷ عدد الأقساط، بتواريخ متباعدة حسب frequency.
- كل قسط status: pending / paid / partial / overdue.
- الخطة status: active / completed / cancelled.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.extensions import db
from app.models.base import TimestampMixin


MONEY = Numeric(18, 3)


class InstallmentFrequency(str, enum.Enum):
    MONTHLY = "monthly"
    BIWEEKLY = "biweekly"       # كل أسبوعين
    WEEKLY = "weekly"


class InstallmentLineStatus(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"


class InstallmentPlanStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class InstallmentPlan(db.Model, TimestampMixin):
    __tablename__ = "installment_plans"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # INST-2026-000001

    sales_invoice_id = Column(
        Integer, ForeignKey("sales_invoices.id", ondelete="RESTRICT"),
        unique=True, nullable=False, index=True,
    )
    customer_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)

    total_amount = Column(MONEY, nullable=False)       # إجمالي الفاتورة (بعد الخصم والضريبة)
    down_payment = Column(MONEY, nullable=False, default=0)
    financed_amount = Column(MONEY, nullable=False)    # total - down_payment (المبلغ الذي يُقسَّط)
    installments_count = Column(Integer, nullable=False)
    frequency = Column(
        Enum(InstallmentFrequency, name="installment_frequency",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=InstallmentFrequency.MONTHLY,
    )
    start_date = Column(Date, nullable=False)          # تاريخ استحقاق أول قسط

    status = Column(
        Enum(InstallmentPlanStatus, name="installment_plan_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=InstallmentPlanStatus.ACTIVE, index=True,
    )
    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # علاقات
    sales_invoice = relationship("SalesInvoice", lazy="joined")
    customer = relationship("Party", lazy="joined")
    schedule = relationship(
        "InstallmentScheduleLine",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InstallmentScheduleLine.number",
    )
    collections = relationship(
        "InstallmentCollection",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InstallmentCollection.id",
    )
    created_by = relationship("User")

    # ---------- Helpers ----------

    @property
    def total_paid(self) -> Decimal:
        """إجمالي المسدَّد من الأقساط (لا يشمل المقدم)."""
        return sum((Decimal(str(l.paid_amount or 0)) for l in self.schedule), Decimal("0"))

    @property
    def total_remaining(self) -> Decimal:
        return Decimal(str(self.financed_amount)) - self.total_paid

    @property
    def paid_installments_count(self) -> int:
        return sum(1 for l in self.schedule if l.status == InstallmentLineStatus.PAID)

    @property
    def overdue_lines(self) -> list["InstallmentScheduleLine"]:
        return [l for l in self.schedule if l.status == InstallmentLineStatus.OVERDUE]

    def __repr__(self) -> str:
        return f"<InstallmentPlan {self.doc_number} {self.status.value}>"


class InstallmentScheduleLine(db.Model):
    __tablename__ = "installment_schedule_lines"

    id = Column(Integer, primary_key=True)
    plan_id = Column(
        Integer, ForeignKey("installment_plans.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    number = Column(Integer, nullable=False)           # 1, 2, 3, ...
    due_date = Column(Date, nullable=False, index=True)
    amount = Column(MONEY, nullable=False)             # المستحق على هذا القسط
    paid_amount = Column(MONEY, nullable=False, default=0)  # المسدَّد فعلًا
    status = Column(
        Enum(InstallmentLineStatus, name="installment_line_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=InstallmentLineStatus.PENDING, index=True,
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)

    plan = relationship("InstallmentPlan", back_populates="schedule")

    @property
    def remaining(self) -> Decimal:
        return Decimal(str(self.amount)) - Decimal(str(self.paid_amount or 0))

    def __repr__(self) -> str:
        return f"<Installment #{self.number} plan={self.plan_id} {self.status.value}>"


class InstallmentCollection(db.Model, TimestampMixin):
    __tablename__ = "installment_collections"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # RCV-2026-... (reuses customer_receipt seq)
    plan_id = Column(
        Integer, ForeignKey("installment_plans.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    schedule_line_id = Column(
        Integer, ForeignKey("installment_schedule_lines.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    collection_date = Column(Date, nullable=False)
    amount = Column(MONEY, nullable=False)
    method = Column(String(20), nullable=False, default="cash")  # cash / bank
    bank_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    memo = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    plan = relationship("InstallmentPlan", back_populates="collections")
    schedule_line = relationship("InstallmentScheduleLine")
    bank_account = relationship("Account", foreign_keys=[bank_account_id])
    created_by = relationship("User")
