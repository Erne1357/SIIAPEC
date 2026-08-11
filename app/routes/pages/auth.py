# app/routes/pages/auth.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from app.models.user import User
from app.models.role import Role
from app.utils.validators import (
    EMAIL_MAX_LENGTH,
    InputValidationError,
    validate_person_name,
    validate_short_text,
)
from app import db

pages_auth = Blueprint("pages_auth", __name__)

#helpers

def check_default_password(password: str) -> bool:
    """
    Verifica si el usuario está usando la contraseña por defecto.
    La contraseña por defecto es 'tecno#2K' hasheada.
    """
    return check_password_hash(password, "tecno#2K")


@pages_auth.route("/reset-password/<token>", methods=["GET"])
def reset_password_page(token):
    """
    Página pública para configurar contraseña vía token (alta de estudiantes
    por bulk import o flujo "olvidé mi contraseña" futuro). El token IS the
    auth — no se requiere login.
    """
    if current_user.is_authenticated:
        # Logout the user before they can use the token, otherwise UX is confusing.
        from flask_login import logout_user
        logout_user()
    return render_template("auth/reset_password.html", token=token)


@pages_auth.route("/login", methods=["GET"])
def login_page():
    if current_user.is_authenticated:
        # si ya está logueado, llévalo a su dashboard
        return redirect(url_for("pages_user.dashboard"))

    # IMPORTANTE: Generar un nuevo token CSRF fresco para evitar problemas
    # después de logout o sesiones expiradas
    from app.utils.csrf import generate_csrf_token
    generate_csrf_token(force_new=True)

    return render_template("auth/login.html")

@pages_auth.route("/register", methods=["GET", "POST"])
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages_user.dashboard"))

    if request.method == "POST":
        # Verificar si es una petición AJAX
        is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 
                  request.content_type == 'application/json' or
                  'application/json' in request.headers.get('Accept', ''))
        
        first_name = (request.form.get("first_name") or "").strip()
        last_name  = (request.form.get("last_name") or "").strip()
        mother_last_name = (request.form.get("mother_last_name") or "").strip() or None
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm  = request.form.get("confirm_password") or ""
        is_internal = request.form.get("is_internal") == "on"

        # Validaciones básicas
        if not first_name or not last_name or not username or not email or not password:
            error_msg = "Faltan campos obligatorios."
            if is_ajax:
                return jsonify({"ok": False, "error": error_msg}), 400
            flash(error_msg, "danger")
            return render_template("auth/register.html")

        # These fields are attacker-controlled and are later rendered by the
        # staff consoles, so they are validated at the boundary: the stored
        # value is either clean or the registration is rejected.
        try:
            first_name = validate_person_name(first_name, label="Nombre")
            last_name = validate_person_name(last_name, label="Apellido paterno")
            mother_last_name = validate_person_name(
                mother_last_name, label="Apellido materno", required=False
            )
            username = validate_short_text(
                username, label="Nombre de usuario", required=True
            )
            email = validate_short_text(
                email,
                label="Correo electrónico",
                required=True,
                max_length=EMAIL_MAX_LENGTH,
            )
        except InputValidationError as e:
            if is_ajax:
                return jsonify({"ok": False, "error": e.message}), 400
            flash(e.message, "danger")
            return render_template("auth/register.html")

        if password != confirm:
            error_msg = "Las contraseñas no coinciden."
            if is_ajax:
                return jsonify({"ok": False, "error": error_msg}), 400
            flash(error_msg, "danger")
            return render_template("auth/register.html")
            
        if User.query.filter_by(username=username).first():
            error_msg = "El nombre de usuario ya existe."
            if is_ajax:
                return jsonify({"ok": False, "error": error_msg}), 400
            flash(error_msg, "danger")
            return render_template("auth/register.html")
            
        if User.query.filter_by(email=email).first():
            error_msg = "El correo electrónico ya está registrado."
            if is_ajax:
                return jsonify({"ok": False, "error": error_msg}), 400
            flash(error_msg, "danger")
            return render_template("auth/register.html")

        applicant_role = Role.query.filter_by(name="applicant").first()
        if not applicant_role:
            error_msg = "No se encontró el rol 'applicant'. Contacta al administrador."
            if is_ajax:
                return jsonify({"ok": False, "error": error_msg}), 500
            flash(error_msg, "danger")
            return render_template("auth/register.html")

        try:
            new_user = User(
                first_name=first_name,
                last_name=last_name,
                mother_last_name=mother_last_name,
                username=username,
                password=password,
                email=email,
                is_internal=is_internal,
                role_id=applicant_role.id,
                must_change_password=check_default_password(password)
            )
            db.session.add(new_user)

            db.session.commit()
            
            success_msg = "Registro exitoso. Ahora inicia sesión."
            if is_ajax:
                return jsonify({"ok": True, "message": success_msg})
            
            flash(success_msg, "success")
            return redirect(url_for("pages_auth.login_page"))
            
        except Exception as e:
            db.session.rollback()
            error_msg = f"Error al registrar el usuario: {str(e)}"
            if is_ajax:
                return jsonify({"ok": False, "error": "Error al registrar el usuario."}), 500
            flash("Error al registrar el usuario.", "danger")
            return render_template("auth/register.html")

    return render_template("auth/register.html")
