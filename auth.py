from fastapi import Request
from fastapi.responses import RedirectResponse

from db import get_db

try:
    from werkzeug.security import check_password_hash
except ImportError:
    check_password_hash = None


def get_current_user(request: Request):
    try:
        session_data = request.session
    except Exception:
        return None

    if "user_id" not in session_data:
        return None
    session_user_id = session_data["user_id"]

    conn = get_db()
    try:
        user = conn.execute(
            """
            SELECT id, username, full_name, role, is_active, created_at
            FROM users
            WHERE id = ?
            """,
            (session_user_id,),
        ).fetchone()
    finally:
        conn.close()

    if not user:
        session_data.clear()
        return None

    if not user["is_active"]:
        session_data.clear()
        return None

    return user


def require_login(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return user


def require_role(request: Request, allowed_roles):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    allowed = set(allowed_roles or [])
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user["role"] not in allowed and not is_admin(user):
        return access_denied_response()
    return user


def is_admin(user) -> bool:
    return bool(user and user["role"] == "admin")


def is_partner(user) -> bool:
    return bool(user and user["role"] == "partner")


def is_employee(user) -> bool:
    return bool(user and user["role"] == "employee")


def is_owner(user) -> bool:
    return bool(user and user["role"] == "owner")


def is_tenant(user) -> bool:
    return bool(user and user["role"] == "tenant")


def get_user_by_id(user_id: int):
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute(
        """
        SELECT id, username, full_name, role, is_active, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    ).fetchone()
    conn.close()
    return user


def password_matches(stored_password: str, provided_password: str) -> bool:
    if stored_password == provided_password:
        return True

    if check_password_hash and stored_password:
        hash_markers = ("pbkdf2:", "scrypt:", "argon2:", "bcrypt:")
        if any(str(stored_password).startswith(marker) for marker in hash_markers):
            try:
                return check_password_hash(stored_password, provided_password)
            except Exception:
                return False

    return False
