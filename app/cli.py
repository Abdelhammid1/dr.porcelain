"""أوامر Flask CLI للتهيئة الأولية بعد إنشاء قاعدة البيانات وتشغيل الـ migrations."""
from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext

from app.extensions import db


def register_cli_commands(app: Flask) -> None:
    app.cli.add_command(init_all_command)
    app.cli.add_command(init_settings_command)
    app.cli.add_command(init_permissions_command)
    app.cli.add_command(init_roles_command)
    app.cli.add_command(init_coa_command)
    app.cli.add_command(create_owner_command)
    app.cli.add_command(installments_mark_overdue_command)
    app.cli.add_command(vendor_payments_mark_overdue_command)


@click.command("installments-mark-overdue")
@with_appcontext
def installments_mark_overdue_command():
    """يفحص كل الأقساط ويُعلِّم المتأخرة (يُشغَّل يوميًا)."""
    from app.services.installments import mark_overdue_installments
    count = mark_overdue_installments()
    db.session.commit()
    click.secho(f"✓ تم تحديث {count} قسط إلى OVERDUE.", fg="green")


@click.command("vendor-payments-mark-overdue")
@with_appcontext
def vendor_payments_mark_overdue_command():
    """يفحص كل أسطر جداول سداد الموردين ويُعلِّم المتأخرة (يُشغَّل يوميًا)."""
    from app.services.vendor_payments import mark_overdue_vendor_lines
    count = mark_overdue_vendor_lines()
    db.session.commit()
    click.secho(f"✓ تم تحديث {count} دفعة إلى OVERDUE.", fg="green")


@click.command("init-all")
@click.option("--owner-username", default="admin", show_default=True)
@click.option("--owner-password", default=None, help="لو مش متحدد هيطلبه تفاعليًا.")
@click.option("--owner-name", default="مالك المتجر", show_default=True)
@with_appcontext
def init_all_command(owner_username, owner_password, owner_name):
    """تهيئة كاملة: صلاحيات + أدوار + إعدادات + دليل حسابات + حساب المالك."""
    click.echo("→ تهيئة الصلاحيات…")
    _seed_permissions()
    click.echo("→ تهيئة الأدوار…")
    _seed_roles()
    click.echo("→ تهيئة إعدادات المتجر…")
    _seed_settings()
    click.echo("→ تهيئة دليل الحسابات…")
    created, skipped = _seed_coa()
    click.echo(f"   أُنشِئ {created} حساب، تخطّى {skipped}.")
    click.echo("→ إنشاء مستخدم المالك…")
    if owner_password is None:
        owner_password = click.prompt(
            "كلمة مرور المالك", hide_input=True, confirmation_prompt=True
        )
    _create_owner(owner_username, owner_password, owner_name)
    db.session.commit()
    click.secho("✓ اكتملت التهيئة بنجاح.", fg="green")


@click.command("init-coa")
@with_appcontext
def init_coa_command():
    """حقن دليل الحسابات الافتراضي."""
    created, skipped = _seed_coa()
    db.session.commit()
    click.secho(f"✓ دليل الحسابات جاهز — أُنشِئ {created}، تخطّى {skipped}.", fg="green")


def _seed_coa():
    from seeds.chart_of_accounts import seed_chart_of_accounts

    return seed_chart_of_accounts(db.session)


@click.command("init-settings")
@with_appcontext
def init_settings_command():
    """حقن إعدادات المتجر الافتراضية (لا يستبدل قيمًا موجودة)."""
    _seed_settings()
    db.session.commit()
    click.secho("✓ الإعدادات جاهزة.", fg="green")


@click.command("init-permissions")
@with_appcontext
def init_permissions_command():
    """حقن كل الصلاحيات المتاحة في النظام."""
    _seed_permissions()
    db.session.commit()
    click.secho("✓ الصلاحيات جاهزة.", fg="green")


@click.command("init-roles")
@with_appcontext
def init_roles_command():
    """حقن الأدوار الافتراضية (owner/accountant/cashier)."""
    _seed_permissions()  # تأكد أن الصلاحيات موجودة
    _seed_roles()
    db.session.commit()
    click.secho("✓ الأدوار جاهزة.", fg="green")


@click.command("create-owner")
@click.option("--username", prompt=True)
@click.option("--full-name", prompt="الاسم الكامل")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@with_appcontext
def create_owner_command(username, full_name, password):
    """إنشاء مستخدم بدور owner."""
    _create_owner(username, password, full_name)
    db.session.commit()
    click.secho(f"✓ تم إنشاء المستخدم {username}", fg="green")


# --------- المُنفِّذات الفعلية (يمكن استدعاؤها من tests أيضًا) ---------

def _seed_permissions() -> None:
    from app.models.role import Permission
    from seeds.permissions import PERMISSIONS

    existing = {p.code for p in db.session.query(Permission).all()}
    for spec in PERMISSIONS:
        if spec["code"] in existing:
            continue
        db.session.add(Permission(**spec))


def _seed_roles() -> None:
    from app.models.role import Permission, Role
    from seeds.roles import ROLE_DEFAULTS

    perm_map = {p.code: p for p in db.session.query(Permission).all()}

    for spec in ROLE_DEFAULTS:
        role = db.session.query(Role).filter_by(code=spec["code"]).one_or_none()
        if role is None:
            role = Role(
                code=spec["code"],
                name_ar=spec["name_ar"],
                description=spec.get("description"),
                is_system=spec.get("is_system", True),
            )
            db.session.add(role)
            db.session.flush()

        # ندمج الصلاحيات (لا نحذف صلاحيات مضافة يدويًا)
        current = {p.code for p in role.permissions}
        for code in spec.get("permissions", []):
            if code in current:
                continue
            perm = perm_map.get(code)
            if perm is not None:
                role.permissions.append(perm)


def _seed_settings() -> None:
    from app.models.setting import SETTING_DEFAULTS, Setting

    existing = {s.key for s in db.session.query(Setting).all()}
    for key, spec in SETTING_DEFAULTS.items():
        if key in existing:
            continue
        db.session.add(
            Setting(
                key=key,
                value=spec["value"],
                dtype=spec["dtype"],
                label_ar=spec.get("label_ar"),
                group_ar=spec.get("group_ar"),
            )
        )


def _create_owner(username: str, password: str, full_name: str) -> None:
    from app.models.role import Role
    from app.models.user import User

    owner_role = db.session.query(Role).filter_by(code="owner").one_or_none()
    if owner_role is None:
        raise click.ClickException("دور owner مش موجود. شغّل init-roles الأول.")

    if db.session.query(User).filter_by(username=username).first():
        click.secho(f"! المستخدم {username} موجود بالفعل — لن يُنشأ من جديد.", fg="yellow")
        return

    user = User(
        username=username,
        full_name=full_name,
        role_id=owner_role.id,
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
