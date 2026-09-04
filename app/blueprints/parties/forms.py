from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp


PHONE_REGEX = r"^[+0-9\-\s()]{6,20}$"


class PartyForm(FlaskForm):
    """نموذج مشترك للعميل/المورد. نوع الطرف يُمرَّر من الـ route."""

    name_ar = StringField(
        "الاسم",
        validators=[DataRequired(message="الاسم مطلوب"), Length(max=200)],
    )
    phone = StringField(
        "رقم الهاتف",
        validators=[
            Optional(),
            Regexp(PHONE_REGEX, message="رقم هاتف غير صحيح"),
            Length(max=32),
        ],
        render_kw={"dir": "ltr", "placeholder": "01xxxxxxxxx"},
    )
    email = StringField(
        "البريد الإلكتروني",
        validators=[Optional(), Email(message="بريد غير صحيح"), Length(max=160)],
        render_kw={"dir": "ltr"},
    )
    address = TextAreaField(
        "العنوان", validators=[Optional(), Length(max=1000)], render_kw={"rows": 2}
    )
    tax_number = StringField(
        "الرقم الضريبي (اختياري)",
        validators=[Optional(), Length(max=40)],
        render_kw={"dir": "ltr"},
    )
    notes = TextAreaField(
        "ملاحظات", validators=[Optional(), Length(max=2000)], render_kw={"rows": 2}
    )
    is_active = BooleanField("نشط", default=True)
    submit = SubmitField("حفظ")
