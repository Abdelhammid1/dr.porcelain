from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField(
        "اسم المستخدم",
        validators=[DataRequired(message="مطلوب"), Length(min=3, max=64)],
        render_kw={"autocomplete": "username", "dir": "ltr"},
    )
    password = PasswordField(
        "كلمة المرور",
        validators=[DataRequired(message="مطلوب"), Length(min=4, max=128)],
        render_kw={"autocomplete": "current-password"},
    )
    remember = BooleanField("تذكرني")
    submit = SubmitField("دخول")
