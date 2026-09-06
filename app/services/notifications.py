"""خدمة الإشعارات الداخلية (Ticket 4 Epic 2)."""
from __future__ import annotations

from app.extensions import db
from app.models.notification import Notification


def create(*, title: str, body: str | None = None, link: str | None = None,
           notification_type: str = "general",
           user_id: int | None = None) -> Notification:
    n = Notification(
        user_id=user_id,
        title=title,
        body=body,
        link=link,
        notification_type=notification_type,
        is_read=False,
    )
    db.session.add(n)
    db.session.flush()
    return n


def visible_for(user_id: int, unread_only: bool = False, limit: int = 50) -> list[Notification]:
    """الإشعارات التي يراها هذا المستخدم = العامة (user_id IS NULL) + الخاصة به."""
    q = db.session.query(Notification).filter(
        db.or_(Notification.user_id.is_(None), Notification.user_id == user_id)
    )
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    return q.order_by(Notification.id.desc()).limit(limit).all()


def count_unread_for(user_id: int) -> int:
    return (
        db.session.query(Notification)
        .filter(db.or_(Notification.user_id.is_(None), Notification.user_id == user_id))
        .filter(Notification.is_read == False)  # noqa: E712
        .count()
    )


def mark_read(notification_id: int) -> None:
    n = db.session.get(Notification, notification_id)
    if n is not None:
        n.is_read = True
        db.session.flush()


def mark_all_read(user_id: int) -> int:
    """يعلّم كل الإشعارات المرئية لهذا المستخدم كمقروءة. يرجع العدد المُحدَّث."""
    q = db.session.query(Notification).filter(
        db.or_(Notification.user_id.is_(None), Notification.user_id == user_id)
    ).filter(Notification.is_read == False)  # noqa: E712
    count = q.count()
    q.update({"is_read": True}, synchronize_session=False)
    db.session.flush()
    return count
