"""Admin routes لإدارة المستخدمين والأدوار (Ticket 3 Epic 1)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.users import users_bp
from app.extensions import db
from app.models.role import Permission, Role, RolePermission
from app.models.user import User
from app.services.security import require_permission


# ============ Users ============

@users_bp.route("/", methods=["GET"])
@login_required
@require_permission("users.manage")
def index():
    users = db.session.query(User).order_by(User.id).all()
    roles = db.session.query(Role).order_by(Role.name_ar).all()
    return render_template("users/index.html", users=users, roles=roles)


@users_bp.route("/new", methods=["POST"])
@login_required
@require_permission("users.manage")
def create():
    username = (request.form.get("username") or "").strip()
    full_name = (request.form.get("full_name") or "").strip()
    password = request.form.get("password") or ""
    role_id = request.form.get("role_id", type=int)
    email = (request.form.get("email") or "").strip() or None

    if not username or not full_name or not password or not role_id:
        flash("كل الحقول مطلوبة.", "danger")
        return redirect(url_for("users.index"))
    if len(password) < 6:
        flash("كلمة المرور يجب أن تكون 6 أحرف على الأقل.", "danger")
        return redirect(url_for("users.index"))
    if db.session.query(User).filter_by(username=username).first():
        flash(f"اسم المستخدم {username} مستخدم بالفعل.", "danger")
        return redirect(url_for("users.index"))
    role = db.session.get(Role, role_id)
    if role is None:
        flash("الدور غير موجود.", "danger")
        return redirect(url_for("users.index"))

    u = User(username=username, full_name=full_name, email=email, role_id=role.id, is_active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash(f"تم إنشاء المستخدم {username}.", "success")
    return redirect(url_for("users.index"))


@users_bp.route("/<int:user_id>/toggle", methods=["POST"])
@login_required
@require_permission("users.manage")
def toggle(user_id):
    u = db.session.get(User, user_id) or abort(404)
    # Safety: لا يمكن تعطيل آخر مستخدم بدور owner نشط
    if u.is_active and u.role and u.role.code == "owner":
        active_owners = (
            db.session.query(User)
            .join(Role, Role.id == User.role_id)
            .filter(Role.code == "owner", User.is_active == True)  # noqa: E712
            .count()
        )
        if active_owners <= 1:
            flash("لا يمكن تعطيل آخر مالك نشط في النظام.", "danger")
            return redirect(url_for("users.index"))
    # Safety: لا يمكن تعطيل نفسك
    if u.id == current_user.id:
        flash("لا يمكن تعطيل حسابك الشخصي.", "danger")
        return redirect(url_for("users.index"))

    u.is_active = not u.is_active
    db.session.commit()
    flash(f"تم {'تفعيل' if u.is_active else 'تعطيل'} المستخدم {u.username}.", "info")
    return redirect(url_for("users.index"))


@users_bp.route("/<int:user_id>/reset-password", methods=["POST"])
@login_required
@require_permission("users.manage")
def reset_password(user_id):
    u = db.session.get(User, user_id) or abort(404)
    new_password = request.form.get("new_password") or ""
    if len(new_password) < 6:
        flash("كلمة المرور يجب أن تكون 6 أحرف على الأقل.", "danger")
        return redirect(url_for("users.index"))
    u.set_password(new_password)
    db.session.commit()
    flash(f"تم تحديث كلمة مرور {u.username}.", "success")
    return redirect(url_for("users.index"))


# ============ Roles ============

@users_bp.route("/roles", methods=["GET"])
@login_required
@require_permission("roles.manage")
def roles_index():
    roles = db.session.query(Role).order_by(Role.id).all()
    permissions = db.session.query(Permission).order_by(Permission.group_ar, Permission.code).all()
    # تجميع الصلاحيات حسب group_ar
    grouped: dict[str, list[Permission]] = {}
    for p in permissions:
        grouped.setdefault(p.group_ar, []).append(p)
    return render_template("users/roles.html",
                           roles=roles, permissions=permissions, grouped=grouped)


@users_bp.route("/roles/new", methods=["POST"])
@login_required
@require_permission("roles.manage")
def roles_create():
    code = (request.form.get("code") or "").strip().lower()
    name_ar = (request.form.get("name_ar") or "").strip()
    if not code or not name_ar:
        flash("الكود والاسم مطلوبان.", "danger")
        return redirect(url_for("users.roles_index"))
    if db.session.query(Role).filter_by(code=code).first():
        flash(f"دور بكود {code} موجود بالفعل.", "danger")
        return redirect(url_for("users.roles_index"))
    r = Role(code=code, name_ar=name_ar, is_system=False)
    db.session.add(r)
    db.session.commit()
    flash(f"تم إنشاء الدور {name_ar}.", "success")
    return redirect(url_for("users.roles_edit_permissions", role_id=r.id))


@users_bp.route("/roles/<int:role_id>/permissions", methods=["GET", "POST"])
@login_required
@require_permission("roles.manage")
def roles_edit_permissions(role_id):
    role = db.session.get(Role, role_id) or abort(404)
    if request.method == "POST":
        # owner ما بيتلمس (كل الصلاحيات ضمنية)
        if role.code == "owner":
            flash("دور المالك له كل الصلاحيات ضمنيًا — لا يُعدَّل.", "info")
            return redirect(url_for("users.roles_index"))

        selected_codes = set(request.form.getlist("permissions"))
        all_perms = {p.code: p for p in db.session.query(Permission).all()}
        # مسح الحالي
        db.session.query(RolePermission).filter_by(role_id=role.id).delete(synchronize_session=False)
        for code in selected_codes:
            perm = all_perms.get(code)
            if perm is not None:
                db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.session.commit()
        flash(f"تم حفظ صلاحيات {role.name_ar}.", "success")
        return redirect(url_for("users.roles_index"))

    return redirect(url_for("users.roles_index"))
