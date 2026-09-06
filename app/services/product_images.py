"""خدمة إدارة صور المنتجات (Epic 1 من تذكرة تحسينات المنتج).

المخطط:
- الملف يُحفَظ على القرص: `<UPLOAD_FOLDER>/products/<pid>/<uuid>.<ext>`
- الـ `file_path` في DB مسار نسبي داخل `app/static/` مثل `uploads/products/12/ab.jpg`
  حتى `{{ url_for('static', filename=img.file_path) }}` يعمل مباشرة في القوالب.
- أول صورة تُرفَع لأي منتج تُعلَّم `is_primary=True` تلقائيًا.
- حذف الـ primary مع بقاء صور أخرى → أول واحدة في الترتيب تصبح primary.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.image import ProductImage
from app.models.product import Product


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_BYTES = 8 * 1024 * 1024  # 8MB — نفس MAX_CONTENT_LENGTH


class ImageError(ValueError):
    pass


def _ext_of(filename: str) -> str:
    if "." not in (filename or ""):
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _uploads_root() -> Path:
    """المسار المطلق لـ `app/static/uploads`."""
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    return upload_folder


def _product_dir(product_id: int) -> Path:
    return _uploads_root() / "products" / str(product_id)


def _relative_static_path(abs_path: Path) -> str:
    """يحوّل المسار المطلق للملف لمسار نسبي داخل `app/static/`."""
    static_root = Path(current_app.static_folder)
    return str(abs_path.relative_to(static_root)).replace(os.sep, "/")


def save_product_image(product_id: int, file_storage: FileStorage) -> ProductImage:
    """يحفظ ملفًا واحدًا كصورة للمنتج، ويسجّله في DB."""
    if file_storage is None or not file_storage.filename:
        raise ImageError("لم يتم اختيار ملف.")

    product = db.session.get(Product, product_id)
    if product is None:
        raise ImageError("المنتج غير موجود.")

    ext = _ext_of(file_storage.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ImageError(
            f"صيغة الملف غير مدعومة ({ext or '؟'}). المسموح: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # فحص الحجم (Flask يفرض MAX_CONTENT_LENGTH لكن هنا فحص إضافي على الملف الواحد)
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_BYTES:
        raise ImageError(f"حجم الصورة أكبر من {MAX_BYTES // (1024 * 1024)} ميجا.")

    # اسم فريد + مجلد المنتج
    product_dir = _product_dir(product_id)
    product_dir.mkdir(parents=True, exist_ok=True)
    # secure_filename فقط لضمان أن الامتداد يبقى منظفًا؛ الاسم النهائي نولّده uuid.
    safe_ext = secure_filename(f"x.{ext}").rsplit(".", 1)[-1] or ext
    new_name = f"{uuid.uuid4().hex}.{safe_ext}"
    dest = product_dir / new_name
    file_storage.save(str(dest))

    # أول صورة → primary تلقائيًا؛ ترتيب العرض في آخر القائمة
    existing_count = db.session.query(ProductImage).filter_by(product_id=product_id).count()
    is_primary = existing_count == 0

    img = ProductImage(
        product_id=product_id,
        file_path=_relative_static_path(dest),
        display_order=existing_count,
        is_primary=is_primary,
    )
    db.session.add(img)
    db.session.flush()
    return img


def delete_product_image(image_id: int) -> None:
    """يحذف الصورة من DB + الملف من القرص. لو المحذوف primary والصور الأخرى موجودة،
    أول صورة متبقية (حسب display_order) تصبح primary."""
    img = db.session.get(ProductImage, image_id)
    if img is None:
        return
    product_id = img.product_id
    was_primary = img.is_primary

    # احذف من القرص (لا تفشل لو الملف مفقود)
    try:
        abs_path = Path(current_app.static_folder) / img.file_path
        if abs_path.exists():
            abs_path.unlink()
    except OSError:
        pass

    db.session.delete(img)
    db.session.flush()

    if was_primary:
        remaining = (
            db.session.query(ProductImage)
            .filter_by(product_id=product_id)
            .order_by(ProductImage.display_order, ProductImage.id)
            .first()
        )
        if remaining is not None:
            remaining.is_primary = True
            db.session.flush()


def set_primary(image_id: int) -> ProductImage:
    """يجعل هذه الصورة الرئيسية، ويلغي primary من باقي صور نفس المنتج."""
    img = db.session.get(ProductImage, image_id)
    if img is None:
        raise ImageError("الصورة غير موجودة.")
    (
        db.session.query(ProductImage)
        .filter(ProductImage.product_id == img.product_id, ProductImage.id != img.id)
        .update({"is_primary": False}, synchronize_session=False)
    )
    img.is_primary = True
    db.session.flush()
    return img


def reorder_images(product_id: int, ordered_ids: list[int]) -> None:
    """يعيد ترتيب display_order حسب قائمة الـ IDs المُعطاة."""
    images = {
        img.id: img
        for img in db.session.query(ProductImage).filter_by(product_id=product_id).all()
    }
    for idx, img_id in enumerate(ordered_ids):
        if img_id in images:
            images[img_id].display_order = idx
    db.session.flush()
