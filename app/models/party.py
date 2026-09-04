"""نموذج الأطراف (عملاء/موردون) — تعريف مبدئي.

يوسَّع في Epic 1.2 بإضافة عناوين وحقول أعمال أخرى. الآن نحتاج الجدول
حتى يتمكن Account.party_id من الإشارة إليه.
"""
from __future__ import annotations

import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import TimestampMixin


class PartyType(str, enum.Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    # قد نضيف EMPLOYEE لاحقًا للسلفيات


class Party(db.Model, TimestampMixin):
    __tablename__ = "parties"

    id = Column(Integer, primary_key=True)
    type = Column(
        Enum(PartyType, name="party_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    code = Column(String(40), unique=True, nullable=False, index=True)  # مثال: C-000123 / V-000045
    name_ar = Column(String(200), nullable=False, index=True)
    phone = Column(String(32), nullable=True, index=True)
    email = Column(String(160), nullable=True)
    address = Column(Text, nullable=True)
    tax_number = Column(String(40), nullable=True)  # للفواتير الضريبية للموردين B2B
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # Customer Portal (Phase 7) — يُستخدَم فقط للعملاء لتسجيل الدخول لمتجرهم.
    # nullable: العميل الذي أُنشئ من الأدمن (بدون كلمة مرور) يقدر ينشئ حساب لاحقًا.
    password_hash = Column(String(255), nullable=True)
    last_portal_login_at = Column(DateTime(timezone=True), nullable=True)

    # الحساب الفرعي المرتبط بهذا الطرف (1200-xxx للعملاء، 2100-xxx للموردين)
    account = relationship(
        "Account",
        primaryjoin="Account.party_id == Party.id",
        uselist=False,
        lazy="joined",
        viewonly=True,  # يُدار من Party service بشكل صريح
    )

    # -------- Customer portal password --------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    @property
    def has_portal_account(self) -> bool:
        return bool(self.password_hash)

    def __repr__(self) -> str:
        return f"<Party {self.type.value} {self.code} {self.name_ar}>"
