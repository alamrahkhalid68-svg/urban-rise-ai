from fastapi import Request
from fastapi.responses import RedirectResponse

from auth import (
    get_current_user,
    get_user_by_id,
    is_admin,
    is_employee,
    is_owner,
    is_partner,
    is_project_manager,
    is_tenant,
)
from db import get_db


def normalize_access_value(value: str = "") -> str:
    return (value or "").strip().lower()


def get_user_tenant_access_ids(user_id: int) -> list[int]:
    if not user_id:
        return []

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT tenant_id
            FROM (
                SELECT tenant_id
                FROM user_tenant_access
                WHERE user_id = ?
                UNION
                SELECT id AS tenant_id
                FROM property_tenants
                WHERE user_id = ?
            )
            WHERE tenant_id IS NOT NULL
            ORDER BY tenant_id
            """,
            (user_id, user_id),
        ).fetchall()
        return [row["tenant_id"] for row in rows]
    finally:
        conn.close()


def get_primary_tenant_id(user_id: int) -> int | None:
    tenant_ids = get_user_tenant_access_ids(user_id)
    return tenant_ids[0] if tenant_ids else None


def get_user_company_access_rows(user_id: int):
    if not user_id:
        return []
    conn = get_db()
    try:
        return conn.execute(
            """
            SELECT company, COALESCE(TRIM(section), '') AS section
            FROM user_company_access
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()


def get_employee_allowed_sections(user_id: int, company: str) -> set[str]:
    clean_company = normalize_access_value(company)
    allowed_sections: set[str] = set()
    for row in get_user_company_access_rows(user_id):
        row_company = normalize_access_value(row["company"] or "")
        row_section = normalize_access_value(row["section"] or "")
        if row_section and (row_company == clean_company or row_company == "all"):
            allowed_sections.add(row_section)
    return allowed_sections


def get_accessible_property_ids(user_id: int) -> list[int]:
    user = get_user_by_id(user_id)
    if not user or not user["is_active"]:
        return []
    if is_admin(user) or (is_employee(user) or is_partner(user) or is_project_manager(user)) and user_has_company_access(user_id, "realestate"):
        conn = get_db()
        rows = conn.execute("SELECT id FROM property_properties ORDER BY id").fetchall()
        conn.close()
        return [row["id"] for row in rows]

    if is_owner(user):
        conn = get_db()
        rows = conn.execute("SELECT property_id FROM user_property_access WHERE user_id = ? ORDER BY property_id", (user_id,)).fetchall()
        conn.close()
        return [row["property_id"] for row in rows]

    if is_tenant(user):
        tenant_ids = get_user_tenant_access_ids(user_id)
        if not tenant_ids:
            return []

        placeholders = ", ".join("?" for _ in tenant_ids)
        conn = get_db()
        rows = conn.execute(
            f"SELECT DISTINCT property_id FROM property_tenants WHERE id IN ({placeholders}) ORDER BY property_id",
            tenant_ids,
        ).fetchall()
        conn.close()
        return [row["property_id"] for row in rows if row["property_id"]]

    return []


def user_has_company_access(user_id: int, company: str, section: str | None = None) -> bool:
    user = get_user_by_id(user_id)
    if not user or not user["is_active"]:
        return False
    if is_admin(user):
        return True

    conn = get_db()
    try:
        if is_employee(user) or is_partner(user) or is_project_manager(user):
            clean_company = normalize_access_value(company)
            clean_section = normalize_access_value(section or "")
            if section:
                if clean_company == "all":
                    row = conn.execute(
                        """
                        SELECT 1
                        FROM user_company_access
                        WHERE user_id = ?
                          AND LOWER(TRIM(COALESCE(section, ''))) = ?
                        LIMIT 1
                        """,
                        (user_id, clean_section),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT 1
                        FROM user_company_access
                        WHERE user_id = ?
                          AND LOWER(TRIM(COALESCE(section, ''))) = ?
                          AND LOWER(TRIM(COALESCE(company, ''))) IN (?, 'all')
                        LIMIT 1
                        """,
                        (user_id, clean_section, clean_company),
                    ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM user_company_access
                    WHERE user_id = ?
                      AND LOWER(TRIM(COALESCE(company, ''))) = ?
                      AND (section IS NULL OR TRIM(section) = '')
                    LIMIT 1
                    """,
                    (user_id, clean_company),
                ).fetchone()
            return bool(row)

        if is_owner(user) and company == "realestate":
            return bool(conn.execute("SELECT 1 FROM user_property_access WHERE user_id = ? LIMIT 1", (user_id,)).fetchone())

        if is_tenant(user) and company == "realestate":
            return bool(get_user_tenant_access_ids(user_id))

        return False
    finally:
        conn.close()


def user_has_property_access(user_id: int, property_id: int) -> bool:
    user = get_user_by_id(user_id)
    if not user or not user["is_active"] or not property_id:
        return False
    if is_admin(user):
        return True

    if not is_owner(user):
        return True

    conn = get_db()
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM user_property_access WHERE user_id = ? AND property_id = ? LIMIT 1",
                (user_id, property_id),
            ).fetchone()
        )
    finally:
        conn.close()


def user_has_tenant_access(user_id: int, tenant_id: int) -> bool:
    user = get_user_by_id(user_id)
    if not user or not user["is_active"] or not tenant_id:
        return False
    if is_admin(user):
        return True

    if not is_tenant(user):
        return True

    return tenant_id in set(get_user_tenant_access_ids(user_id))


def ensure_company_access(request: Request, company: str, section: str | None = None, message: str = "ليس لديك صلاحية الوصول إلى هذه الصفحة"):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user):
        return user
    if not user_has_company_access(user["id"], company, section):
        return access_denied_response(message)
    return user


def ensure_employee_section_access(
    request: Request,
    company: str,
    section: str,
    message: str = "ليس لديك صلاحية الوصول إلى هذه الصفحة",
):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user):
        return user
    if not is_employee(user):
        return ensure_company_access(request, company, message=message)
    if not user_has_company_access(user["id"], company, section):
        return access_denied_response(message)
    return user


def ensure_employee_any_section_access(
    request: Request,
    company: str,
    sections,
    message: str = "ليس لديك صلاحية الوصول إلى هذه الصفحة",
):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user):
        return user
    if not is_employee(user):
        return ensure_company_access(request, company, message=message)
    if any(user_has_company_access(user["id"], company, section) for section in (sections or [])):
        return user
    return access_denied_response(message)


def ensure_property_access(request: Request, property_id: int, message: str = "ليس لديك صلاحية الوصول"):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user):
        return user
    if is_tenant(user):
        return access_denied_response(message, back_url="/client-maintenance")
    if is_owner(user):
        if not user_has_property_access(user["id"], property_id):
            return access_denied_response(message, back_url="/property-management")
        return user
    if is_employee(user) or is_partner(user):
        if not user_has_company_access(user["id"], "realestate"):
            return access_denied_response(message, back_url="/")
        return user
    return access_denied_response(message, back_url="/")


def ensure_tenant_access(request: Request, tenant_id: int, message: str = "ليس لديك صلاحية الوصول"):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user) or not is_tenant(user):
        return user
    if not user_has_tenant_access(user["id"], tenant_id):
        return access_denied_response(message, back_url="/client-maintenance")
    return user


def ensure_request_belongs_to_tenant(request: Request, request_id: int, denied_message: str = "ليس لديك صلاحية الوصول"):
    from main import access_denied_response

    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_admin(user) or not is_tenant(user):
        return user

    conn = get_db()
    maintenance_request = conn.execute(
        "SELECT tenant_id FROM maintenance_requests WHERE id = ?",
        (request_id,)
    ).fetchone()
    conn.close()

    if not maintenance_request or not maintenance_request["tenant_id"]:
        return access_denied_response(denied_message, back_url="/client-maintenance")

    return ensure_tenant_access(request, maintenance_request["tenant_id"], denied_message)
