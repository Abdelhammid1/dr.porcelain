"""جدول دفعات المورد + سجل السدادات.

Phase 4 يضيف لفواتير الشراء الآجلة (CREDIT) القدرة على تحديد جدول سداد مرن —
مبالغ وتواريخ مختلفة (بعكس التقسيط الذي يقسم بالتساوي).

- الفاتورة الأصلية تظل تسجل AP بكامل القيمة على المورد (لا تغيير في القيد الأولي).
- الجدول (VendorPaymentSchedule) يضم أسطر متعددة (VendorPaymentScheduleLine)
  كل واحد له تاريخ استحقاق ومبلغ.
- كل عملية سداد تُسجَّل كـ VendorPayment، تُنشِئ قيدًا: D 2100-VVV / C 1010 أو 1020.
- الحالة تُحدَّث تلقائيًا: paid / partial / overdue.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
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


class VendorScheduleLineStatus(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"


class VendorScheduleStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VendorPaymentSchedule(db.Model, TimestampMixin):
    __tablename__ = "vendor_payment_schedules"

    id = Column(Integer, primary_key=True)
    purchase_invoice_id = Column(
        Integer, ForeignKey("purchase_invoices.id", ondelete="RESTRICT"),
        unique=True, nullable=False, index=True,
    )
    vendor_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)

    total_amount = Column(MONEY, nullable=False)
    status = Column(
        Enum(VendorScheduleStatus, name="vendor_schedule_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=VendorScheduleStatus.ACTIVE, index=True,
    )
    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    purchase_invoice = relationship("PurchaseInvoice", lazy="joined")
    vendor = relationship("Party", lazy="joined")
    lines = relationship(
        "VendorPaymentScheduleLine",
        back_populates="schedule",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="VendorPaymentScheduleLine.number",
    )
    payments = relationship(
        "VendorPayment",
        back_populates="schedule",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="VendorPayment.id",
    )
    created_by = relationship("User")

    @property
    def total_paid(self) -> Decimal:
        return sum((Decimal(str(l.paid_amount or 0)) for l in self.lines), Decimal("0"))

    @property
    def total_remaining(self) -> Decimal:
        return Decimal(str(self.total_amount)) - self.total_paid

    @property
    def overdue_lines(self) -> list["VendorPaymentScheduleLine"]:
        return [l for l in self.lines if l.status == VendorScheduleLineStatus.OVERDUE]

    def __repr__(self) -> str:
        return f"<VendorPaymentSchedule invoice={self.purchase_invoice_id} status={self.status.value}>"


class VendorPaymentScheduleLine(db.Model):
    __tablename__ = "vendor_payment_schedule_lines"

    id = Column(Integer, primary_key=True)
    schedule_id = Column(
        Integer, ForeignKey("vendor_payment_schedules.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    number = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    amount = Column(MONEY, nullable=False)
    paid_amount = Column(MONEY, nullable=False, default=0)
    status = Column(
        Enum(VendorScheduleLineStatus, name="vendor_schedule_line_status",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=VendorScheduleLineStatus.PENDING, index=True,
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)

    schedule = relationship("VendorPaymentSchedule", back_populates="lines")

    @property
    def remaining(self) -> Decimal:
        return Decimal(str(self.amount)) - Decimal(str(self.paid_amount or 0))


class VendorPayment(db.Model, TimestampMixin):
    __tablename__ = "vendor_payments"

    id = Column(Integer, primary_key=True)
    doc_number = Column(String(40), unique=True, nullable=False, index=True)  # PAY-2026-000001
    vendor_id = Column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True)

    # يمكن أن يخصّ سطر جدول محدد، أو دفعة عامة على المورد (schedule_line_id = None)
    schedule_id = Column(
        Integer, ForeignKey("vendor_payment_schedules.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    schedule_line_id = Column(
        Integer, ForeignKey("vendor_payment_schedule_lines.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    purchase_invoice_id = Column(
        Integer, ForeignKey("purchase_invoices.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    payment_date = Column(Date, nullable=False)
    amount = Column(MONEY, nullable=False)
    method = Column(String(20), nullable=False, default="cash")  # cash / bank
    bank_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    memo = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    vendor = relationship("Party", lazy="joined")
    schedule = relationship("VendorPaymentSchedule", back_populates="payments", foreign_keys=[schedule_id])
    schedule_line = relationship("VendorPaymentScheduleLine", foreign_keys=[schedule_line_id])
    purchase_invoice = relationship("PurchaseInvoice", foreign_keys=[purchase_invoice_id])
    bank_account = relationship("Account", foreign_keys=[bank_account_id])
    created_by = relationship("User")
