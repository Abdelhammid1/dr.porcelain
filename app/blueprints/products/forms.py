from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateTimeLocalField,
    DecimalField,
    FieldList,
    FormField,
    HiddenField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class CategoryForm(FlaskForm):
    name_ar = StringField("اسم التصنيف", validators=[DataRequired(), Length(max=160)])
    parent_id = SelectField("التصنيف الأب", coerce=int, validators=[Optional()])
    display_order = IntegerField("ترتيب العرض", default=0, validators=[Optional()])
    is_active = BooleanField("نشط", default=True)
    # ملاحظة: حقل الصورة (`image`) لا نُصرّح به على WTForms عمدًا — بنقرأه
    # مباشرة من `request.files.get("image")` في الـ route عشان يمر بالـ
    # multipart بغير ما يخترق WTForms له فيه.
    submit = SubmitField("حفظ")


class ProductForm(FlaskForm):
    """نموذج بيانات المنتج (بدون المتغيرات — هيتم إدارتها بواجهة ديناميكية)."""

    name_ar = StringField("اسم المنتج", validators=[DataRequired(), Length(max=200)])
    category_id = SelectField("التصنيف", coerce=int, validators=[DataRequired(message="اختر تصنيفًا")])
    brand = StringField("العلامة التجارية", validators=[Optional(), Length(max=120)])
    unit = StringField("الوحدة", default="قطعة", validators=[Optional(), Length(max=20)])
    default_price = DecimalField(
        "سعر البيع الافتراضي",
        places=3,
        rounding=None,
        default=0,
        validators=[NumberRange(min=0, message="السعر لا يمكن أن يكون سالبًا")],
    )
    tax_rate_override = DecimalField(
        "نسبة الضريبة % (اختياري)",
        places=3,
        rounding=None,
        validators=[Optional(), NumberRange(min=0, max=100)],
    )
    description = TextAreaField("الوصف", validators=[Optional()], render_kw={"rows": 3})
    is_active = BooleanField("نشط", default=True)

    # Epic 2 — عرض فقط: وقت انتهاء العرض للعداد التنازلي
    offer_ends_at = DateTimeLocalField(
        "ينتهي العرض في (اختياري)",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )

    # Epic 3 — مواصفات
    origin_country = StringField("بلد المنشأ (اختياري)",
                                 validators=[Optional(), Length(max=80)])
    piece_count = IntegerField("عدد القطع (اختياري)",
                               validators=[Optional(), NumberRange(min=1)])

    submit = SubmitField("حفظ")


class VariantForm(FlaskForm):
    class Meta:
        csrf = False  # حقول متعددة على نفس الصفحة — CSRF يأتي من النموذج الأب

    sku = StringField("SKU (اختياري)", validators=[Optional(), Length(max=80)], render_kw={"dir": "ltr"})
    color = StringField("اللون", validators=[Optional(), Length(max=60)])
    barcode = StringField("الباركود (يُولَّد تلقائيًا لو ترك فارغ)", validators=[Optional(), Length(max=40)], render_kw={"dir": "ltr"})
    price = DecimalField("السعر", places=3, rounding=None, validators=[Optional(), NumberRange(min=0)])
    compare_at_price = DecimalField(
        "السعر قبل الخصم (اختياري — يظهر مشطوبًا)",
        places=3, rounding=None,
        validators=[Optional(), NumberRange(min=0)],
    )
    reorder_level = DecimalField("الحد الأدنى للتنبيه", places=3, rounding=None, validators=[Optional(), NumberRange(min=0)])
    is_active = BooleanField("نشط", default=True)
