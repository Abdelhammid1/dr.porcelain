"""تجميع كل الـ Models عشان يتشافوا في migrations وفي `flask shell`."""
from app.models.account import Account, AccountType  # noqa: F401
from app.models.base import TimestampMixin  # noqa: F401
from app.models.category import Category  # noqa: F401
from app.models.installment import (  # noqa: F401
    InstallmentCollection,
    InstallmentFrequency,
    InstallmentLineStatus,
    InstallmentPlan,
    InstallmentPlanStatus,
    InstallmentScheduleLine,
)
from app.models.inventory import InventoryMovement, MovementType  # noqa: F401
from app.models.order import (  # noqa: F401
    Order,
    OrderLine,
    OrderPaymentMethod,
    OrderStatus,
)
from app.models.journal import (  # noqa: F401
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    JournalSourceType,
)
from app.models.party import Party, PartyType  # noqa: F401
from app.models.pos import POSSession, SessionStatus  # noqa: F401
from app.models.coupon import CouponUsage, DiscountCoupon, DiscountType  # noqa: F401
from app.models.image import ProductImage  # noqa: F401
from app.models.loyalty import LoyaltyPointsLedger, LoyaltyTxnType  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.password_reset import PasswordResetToken  # noqa: F401
from app.models.stock_alert import StockAlert  # noqa: F401
from app.models.product import Product, ProductVariant  # noqa: F401
from app.models.product_extras import ProductCompositionLine, ProductFeature  # noqa: F401
from app.models.product_relation import ProductRelation, RelationType  # noqa: F401
from app.models.review import ProductReview  # noqa: F401
from app.models.wishlist import WishlistItem  # noqa: F401
from app.models.purchases import (  # noqa: F401
    PurchaseInvoice,
    PurchaseInvoiceLine,
    PurchasePayment,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseStatus,
)
from app.models.role import Permission, Role, RolePermission  # noqa: F401
from app.models.sales import (  # noqa: F401
    InvoiceStatus,
    PaymentMethod,
    SalesInvoice,
    SalesInvoiceLine,
    SalesReturn,
    SalesReturnLine,
)
from app.models.sequence import NumberSequence  # noqa: F401
from app.models.vendor_payment import (  # noqa: F401
    VendorPayment,
    VendorPaymentSchedule,
    VendorPaymentScheduleLine,
    VendorScheduleLineStatus,
    VendorScheduleStatus,
)
from app.models.setting import Setting  # noqa: F401
from app.models.user import User  # noqa: F401

__all__ = [
    "TimestampMixin",
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "Setting",
    "Account",
    "AccountType",
    "JournalEntry",
    "JournalEntryStatus",
    "JournalLine",
    "JournalSourceType",
    "NumberSequence",
    "Party",
    "PartyType",
    "Category",
    "Product",
    "ProductVariant",
    "ProductImage",
    "ProductFeature",
    "ProductCompositionLine",
    "ProductRelation",
    "RelationType",
    "ProductReview",
    "DiscountCoupon",
    "DiscountType",
    "CouponUsage",
    "WishlistItem",
    "LoyaltyPointsLedger",
    "LoyaltyTxnType",
    "Notification",
    "PasswordResetToken",
    "StockAlert",
    "InventoryMovement",
    "MovementType",
    "SalesInvoice",
    "SalesInvoiceLine",
    "SalesReturn",
    "SalesReturnLine",
    "InvoiceStatus",
    "PaymentMethod",
    "PurchaseInvoice",
    "PurchaseInvoiceLine",
    "PurchaseReturn",
    "PurchaseReturnLine",
    "PurchasePayment",
    "PurchaseStatus",
    "POSSession",
    "SessionStatus",
    "InstallmentPlan",
    "InstallmentScheduleLine",
    "InstallmentCollection",
    "InstallmentFrequency",
    "InstallmentLineStatus",
    "InstallmentPlanStatus",
    "VendorPayment",
    "VendorPaymentSchedule",
    "VendorPaymentScheduleLine",
    "VendorScheduleLineStatus",
    "VendorScheduleStatus",
    "Order",
    "OrderLine",
    "OrderPaymentMethod",
    "OrderStatus",
]
