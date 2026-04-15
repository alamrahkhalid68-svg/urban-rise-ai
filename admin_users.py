from datetime import datetime
from urllib.parse import quote
import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from access_control import normalize_access_value
from admin_users_helpers import (
    COMPANY_PAGE_CONFIG,
    get_allowed_roles_for_company,
    get_allowed_sections_for_company,
    get_company_label,
    get_company_page_config,
    get_general_partner_company_options,
    get_role_label,
    get_section_label,
    user_matches_company_scope,
)
from auth import require_role
from db import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_safe_redirect_target(redirect_to: str = "", fallback: str = "/admin/users") -> str:
    clean_redirect = (redirect_to or "").strip()
    if clean_redirect.startswith("/admin/users"):
        return clean_redirect
    return fallback


def build_redirect_url(base_path: str, message: str = "", error: str = "") -> str:
    if error:
        return f"{base_path}?error={quote(error)}"
    if message:
        return f"{base_path}?message={quote(message)}"
    return base_path


def sync_owner_property_access(conn, user_id: int, role: str, property_id: int | None):
    conn.execute("DELETE FROM user_property_access WHERE user_id = ?", (user_id,))
    if role == "owner" and property_id:
        conn.execute(
            "INSERT INTO user_property_access (user_id, property_id) VALUES (?, ?)",
            (user_id, property_id),
        )


def sync_tenant_user_link(conn, user_id: int, role: str, tenant_record_id: int | None):
    conn.execute("DELETE FROM user_tenant_access WHERE user_id = ?", (user_id,))
    conn.execute("UPDATE property_tenants SET user_id = NULL WHERE user_id = ?", (user_id,))

    if role == "tenant" and tenant_record_id:
        conn.execute("DELETE FROM user_tenant_access WHERE tenant_id = ?", (tenant_record_id,))
        conn.execute("UPDATE property_tenants SET user_id = NULL WHERE id = ?", (tenant_record_id,))
        conn.execute(
            "INSERT INTO user_tenant_access (user_id, tenant_id) VALUES (?, ?)",
            (user_id, tenant_record_id),
        )
        conn.execute(
            "UPDATE property_tenants SET user_id = ? WHERE id = ?",
            (user_id, tenant_record_id),
        )


def sync_user_investment_project_access(conn, user_id: int, role: str, project_ids: list[int] | None):
    conn.execute("DELETE FROM user_investment_project_access WHERE user_id = ?", (user_id,))
    if normalize_access_value(role) != "project_manager":
        return

    clean_project_ids = []
    for project_id in project_ids or []:
        if project_id and project_id not in clean_project_ids:
            clean_project_ids.append(project_id)

    for project_id in clean_project_ids:
        existing_project = conn.execute(
            "SELECT 1 FROM investment_projects WHERE id = ? LIMIT 1",
            (project_id,),
        ).fetchone()
        if not existing_project:
            continue
        conn.execute(
            "INSERT INTO user_investment_project_access (user_id, project_id) VALUES (?, ?)",
            (user_id, project_id),
        )


def sync_user_company_access(
    conn,
    user_id: int,
    role: str,
    company: str = "",
    section: str = "",
    companies: list[str] | None = None,
):
    conn.execute("DELETE FROM user_company_access WHERE user_id = ?", (user_id,))
    clean_role = normalize_access_value(role)
    clean_company = normalize_access_value(company)
    clean_section = normalize_access_value(section)
    clean_companies = [
        normalize_access_value(item)
        for item in (companies or [])
        if normalize_access_value(item)
    ]

    if clean_role == "partner":
        for company_name in dict.fromkeys(clean_companies):
            conn.execute(
                "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
                (user_id, company_name, ""),
            )
        return

    if clean_role == "employee" and clean_section == "inventory":
        conn.execute(
            "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
            (user_id, "all", ""),
        )
        conn.execute(
            "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
            (user_id, "all", "inventory"),
        )
        return

    if clean_role == "employee" and clean_company:
        conn.execute(
            "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
            (user_id, clean_company, ""),
        )
        if clean_section:
            conn.execute(
                "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
                (user_id, clean_company, clean_section),
            )
        return

    if clean_role == "project_manager" and clean_company and clean_section:
        conn.execute(
            "INSERT INTO user_company_access (user_id, company, section) VALUES (?, ?, ?)",
            (user_id, clean_company, clean_section),
        )


def load_admin_users_data():
    conn = get_db()
    try:
        users = conn.execute(
            """
            SELECT id, username, full_name, role, is_active, created_at
            FROM users
            ORDER BY id DESC
            """
        ).fetchall()
        properties = conn.execute(
            "SELECT id, name FROM property_properties ORDER BY name, id"
        ).fetchall()
        tenant_records = conn.execute(
            """
            SELECT
                pt.id,
                pt.user_id,
                pt.name AS tenant_name,
                pp.name AS property_name,
                pu.name AS unit_name
            FROM property_tenants pt
            LEFT JOIN property_properties pp ON pp.id = pt.property_id
            LEFT JOIN property_units pu ON pu.id = pt.unit_id
            ORDER BY pp.name, pu.name, pt.name, pt.id
            """
        ).fetchall()
        investment_projects = conn.execute(
            """
            SELECT id, name, location
            FROM investment_projects
            ORDER BY name, id
            """
        ).fetchall()
        owner_links = {
            row["user_id"]: row["property_id"]
            for row in conn.execute(
                "SELECT user_id, property_id FROM user_property_access ORDER BY id"
            ).fetchall()
        }
        tenant_links = {
            row["user_id"]: row["tenant_id"]
            for row in conn.execute(
                "SELECT user_id, tenant_id FROM user_tenant_access ORDER BY id"
            ).fetchall()
        }
        if not tenant_links:
            tenant_links = {
                row["user_id"]: row["id"]
                for row in conn.execute(
                    "SELECT id, user_id FROM property_tenants WHERE user_id IS NOT NULL ORDER BY id"
                ).fetchall()
            }
        company_access_rows: dict[int, list[dict[str, str]]] = {}
        for row in conn.execute(
            "SELECT user_id, company, section FROM user_company_access ORDER BY id"
        ).fetchall():
            company_access_rows.setdefault(row["user_id"], []).append(
                {
                    "company": normalize_access_value(row["company"] or ""),
                    "section": normalize_access_value(row["section"] or ""),
                }
            )
        investment_project_links: dict[int, list[int]] = {}
        for row in conn.execute(
            "SELECT user_id, project_id FROM user_investment_project_access ORDER BY id"
        ).fetchall():
            investment_project_links.setdefault(row["user_id"], []).append(row["project_id"])
    finally:
        conn.close()

    return users, properties, tenant_records, investment_projects, owner_links, tenant_links, company_access_rows, investment_project_links


def build_property_items(properties):
    return [
        {"id": prop["id"], "name": prop["name"] or f"ملك #{prop['id']}"}
        for prop in properties
    ]


def build_tenant_items(tenant_records):
    tenant_items = []
    for tenant in tenant_records:
        tenant_name = tenant["tenant_name"] or f"مستأجر #{tenant['id']}"
        property_name = tenant["property_name"] or "بدون ملك"
        tenant_label = f"{tenant_name} - {property_name}"
        if tenant["unit_name"]:
            tenant_label += f" - {tenant['unit_name']}"
        tenant_items.append({"id": tenant["id"], "label": tenant_label})
    return tenant_items


def build_investment_project_items(investment_projects):
    return [
        {
            "id": project["id"],
            "name": project["name"] or f"مشروع #{project['id']}",
            "label": (
                f"{project['name'] or ('مشروع #' + str(project['id']))}"
                + (f" - {project['location']}" if project["location"] else "")
            ),
        }
        for project in investment_projects
    ]


def build_user_item(user, current_user, owner_links, tenant_links, access_rows, selected_project_ids=None, investment_project_items=None):
    selected_company = ""
    selected_section = ""
    selected_companies = []
    for row in access_rows:
        if row["company"] and not selected_company:
            selected_company = row["company"]
        if row["company"] and row["company"] not in selected_companies:
            selected_companies.append(row["company"])
        if row["section"] and not selected_section:
            selected_company = row["company"] or selected_company
            selected_section = row["section"]

    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "role_label": get_role_label(user["role"]),
        "full_name": user["full_name"] or "",
        "selected_property_id": owner_links.get(user["id"]),
        "selected_tenant_id": tenant_links.get(user["id"]),
        "selected_company": selected_company,
        "selected_company_label": get_company_label(selected_company),
        "selected_companies": selected_companies,
        "selected_companies_labels": ", ".join(get_company_label(item) for item in selected_companies if item) or "-",
        "selected_section": selected_section,
        "selected_section_label": get_section_label(selected_company, selected_section),
        "selected_project_ids": selected_project_ids or [],
        "selected_projects_labels": ", ".join(
            item["name"]
            for item in (investment_project_items or [])
            if item["id"] in set(selected_project_ids or [])
        ) or "-",
        "can_delete": bool(current_user and current_user["id"] != user["id"]),
    }


def render_admin_users_landing(request: Request, current_user, message: str = "", error_message: str = ""):
    from main import HOME_BUTTON

    company_cards = [
        {
            "key": key,
            "label": config["label"],
            "title": config["title"],
            "description": config["description"],
            "href": f"/admin/users/{key}",
        }
        for key, config in COMPANY_PAGE_CONFIG.items()
        if key in {"works", "realestate", "logistics", "general"}
    ]

    return templates.TemplateResponse(
        request,
        "admin_users_landing.html",
        {
            "request": request,
            "home_button": HOME_BUTTON,
            "current_user": current_user,
            "company_cards": company_cards,
            "message": message,
            "error_message": error_message,
        },
    )


def render_company_users_page(
    request: Request,
    current_user,
    page_company: str,
    users,
    properties,
    tenant_records,
    investment_projects,
    owner_links,
    tenant_links,
    company_access_rows,
    investment_project_links,
    message: str = "",
    error_message: str = "",
):
    from main import HOME_BUTTON

    page_config = get_company_page_config(page_company)
    allowed_roles = get_allowed_roles_for_company(page_company)
    allowed_sections = get_allowed_sections_for_company(page_company)
    property_items = build_property_items(properties)
    tenant_items = build_tenant_items(tenant_records)
    investment_project_items = build_investment_project_items(investment_projects)

    scoped_users = []
    for user in users:
        access_rows = company_access_rows.get(user["id"], [])
        if user_matches_company_scope(user, access_rows, page_company):
            scoped_users.append(
                build_user_item(
                    user,
                    current_user,
                    owner_links,
                    tenant_links,
                    access_rows,
                    selected_project_ids=investment_project_links.get(user["id"], []),
                    investment_project_items=investment_project_items,
                )
            )

    return templates.TemplateResponse(
        request,
        "admin_users_company.html",
        {
            "request": request,
            "home_button": HOME_BUTTON,
            "current_user": current_user,
            "page_company": page_company,
            "page_config": page_config,
            "users": scoped_users,
            "properties": property_items,
            "tenant_records": tenant_items,
            "investment_projects": investment_project_items,
            "allowed_roles": allowed_roles,
            "allowed_sections": allowed_sections,
            "partner_company_options": get_general_partner_company_options(),
            "message": message,
            "error_message": error_message,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request):
    current_user = require_role(request, {"admin"})
    if isinstance(current_user, RedirectResponse) or isinstance(current_user, HTMLResponse):
        return current_user
    return render_admin_users_landing(
        request=request,
        current_user=current_user,
        message=request.query_params.get("message", ""),
        error_message=request.query_params.get("error", ""),
    )


@router.get("/admin/users/{company_key}", response_class=HTMLResponse)
def admin_company_users_page(company_key: str, request: Request):
    current_user = require_role(request, {"admin"})
    if isinstance(current_user, RedirectResponse) or isinstance(current_user, HTMLResponse):
        return current_user

    page_company = normalize_access_value(company_key)
    if page_company not in COMPANY_PAGE_CONFIG:
        return RedirectResponse(
            url=build_redirect_url("/admin/users", error="الصفحة المطلوبة غير متاحة"),
            status_code=303,
        )

    users, properties, tenant_records, investment_projects, owner_links, tenant_links, company_access_rows, investment_project_links = load_admin_users_data()
    return render_company_users_page(
        request=request,
        current_user=current_user,
        page_company=page_company,
        users=users,
        properties=properties,
        tenant_records=tenant_records,
        investment_projects=investment_projects,
        owner_links=owner_links,
        tenant_links=tenant_links,
        company_access_rows=company_access_rows,
        investment_project_links=investment_project_links,
        message=request.query_params.get("message", ""),
        error_message=request.query_params.get("error", ""),
    )


def validate_user_form(role: str, company: str, section: str, redirect_target: str, companies: list[str] | None = None):
    from main import AUTH_ROLES

    clean_role = normalize_access_value(role)
    clean_company = normalize_access_value(company)
    clean_section = normalize_access_value(section)
    clean_companies = [
        normalize_access_value(item)
        for item in (companies or [])
        if normalize_access_value(item)
    ]

    if clean_role not in AUTH_ROLES:
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="الدور غير مدعوم"),
            status_code=303,
        )

    if clean_role == "partner" and not clean_companies:
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="يرجى اختيار شركة واحدة على الأقل للشريك"),
            status_code=303,
        )

    if clean_role == "employee" and clean_section != "inventory" and (not clean_company or not clean_section):
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="يرجى اختيار الشركة والقسم للحساب"),
            status_code=303,
        )

    if clean_role == "project_manager" and (clean_company != "realestate" or clean_section != "active_projects"):
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="يرجى اختيار التطوير العقاري مع قسم المشاريع النشطة لمدير المشروع"),
            status_code=303,
        )

    if redirect_target == "/admin/users/works":
        if clean_role != "employee":
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="صفحة المقاولات مخصصة للموظفين فقط"),
                status_code=303,
            )
        if clean_company != "works":
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="هذه الصفحة مخصصة لمستخدمي المقاولات فقط"),
                status_code=303,
            )

    if redirect_target == "/admin/users/realestate":
        if clean_role not in {"employee", "project_manager", "owner", "tenant"}:
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="صفحة العقار مخصصة للموظف والمالك والمستأجر فقط"),
                status_code=303,
            )
        if clean_role in {"employee", "project_manager"} and clean_company != "realestate":
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="هذه الصفحة مخصصة لمستخدمي العقار فقط"),
                status_code=303,
            )
    if redirect_target == "/admin/users/general":
        if clean_role == "partner" and not clean_companies:
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="يرجى تحديد شركات الشريك"),
                status_code=303,
            )
        if clean_role == "employee" and clean_section != "inventory":
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="صفحة الإدارة العامة تخصص الموظف للمستودع فقط"),
                status_code=303,
            )

    if clean_role == "employee" and clean_section == "inventory":
        clean_company = "all"

    if clean_company in COMPANY_PAGE_CONFIG:
        allowed_roles = set(get_allowed_roles_for_company(clean_company))
        if clean_role not in allowed_roles:
            return RedirectResponse(
                url=build_redirect_url(redirect_target, error="هذا الدور غير متاح في هذه الصفحة"),
                status_code=303,
            )
        if clean_role in {"employee", "project_manager"}:
            allowed_sections = {value for value, _ in get_allowed_sections_for_company(clean_company)}
            if clean_section not in allowed_sections:
                return RedirectResponse(
                    url=build_redirect_url(redirect_target, error="القسم المحدد غير متاح لهذه الشركة"),
                    status_code=303,
                )
    return None


@router.post("/admin/users/create")
def admin_create_user(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form(""),
    property_id: str = Form(""),
    tenant_record_id: str = Form(""),
    company: str = Form(""),
    section: str = Form(""),
    companies: list[str] = Form([]),
    investment_project_ids: list[str] = Form([]),
    redirect_to: str = Form("/admin/users"),
):
    current_user = require_role(request, {"admin"})
    if isinstance(current_user, RedirectResponse) or isinstance(current_user, HTMLResponse):
        return current_user

    redirect_target = get_safe_redirect_target(redirect_to)
    clean_username = (username or "").strip()
    clean_password = (password or "").strip()
    clean_role = normalize_access_value(role)
    selected_property_id = int(property_id) if str(property_id or "").strip().isdigit() else None
    selected_tenant_id = int(tenant_record_id) if str(tenant_record_id or "").strip().isdigit() else None
    selected_company = normalize_access_value(company)
    selected_section = normalize_access_value(section)
    selected_companies = [normalize_access_value(item) for item in (companies or []) if normalize_access_value(item)]
    selected_project_ids = [int(item) for item in (investment_project_ids or []) if str(item).strip().isdigit()]

    if not clean_username or not clean_password:
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="يرجى تعبئة اسم المستخدم وكلمة المرور"),
            status_code=303,
        )

    validation_response = validate_user_form(clean_role, selected_company, selected_section, redirect_target, selected_companies)
    if validation_response:
        return validation_response

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO users (username, password, full_name, role, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (clean_username, clean_password, clean_username, clean_role, datetime.now().isoformat()),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        sync_owner_property_access(conn, user_id, clean_role, selected_property_id)
        sync_tenant_user_link(conn, user_id, clean_role, selected_tenant_id)
        sync_user_company_access(conn, user_id, clean_role, selected_company, selected_section, selected_companies)
        sync_user_investment_project_access(conn, user_id, clean_role, selected_project_ids)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="اسم المستخدم مستخدم مسبقاً"),
            status_code=303,
        )
    finally:
        conn.close()

    return RedirectResponse(
        url=build_redirect_url(redirect_target, message="تم إنشاء المستخدم بنجاح"),
        status_code=303,
    )


@router.post("/admin/users/update/{user_id}")
def admin_update_user(
    user_id: int,
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form(""),
    property_id: str = Form(""),
    tenant_record_id: str = Form(""),
    company: str = Form(""),
    section: str = Form(""),
    companies: list[str] = Form([]),
    investment_project_ids: list[str] = Form([]),
    redirect_to: str = Form("/admin/users"),
):
    current_user = require_role(request, {"admin"})
    if isinstance(current_user, RedirectResponse) or isinstance(current_user, HTMLResponse):
        return current_user

    redirect_target = get_safe_redirect_target(redirect_to)
    clean_username = (username or "").strip()
    clean_password = (password or "").strip()
    clean_role = normalize_access_value(role)
    selected_property_id = int(property_id) if str(property_id or "").strip().isdigit() else None
    selected_tenant_id = int(tenant_record_id) if str(tenant_record_id or "").strip().isdigit() else None
    selected_company = normalize_access_value(company)
    selected_section = normalize_access_value(section)
    selected_companies = [normalize_access_value(item) for item in (companies or []) if normalize_access_value(item)]
    selected_project_ids = [int(item) for item in (investment_project_ids or []) if str(item).strip().isdigit()]

    if not clean_username:
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="اسم المستخدم مطلوب"),
            status_code=303,
        )

    validation_response = validate_user_form(clean_role, selected_company, selected_section, redirect_target, selected_companies)
    if validation_response:
        return validation_response

    conn = get_db()
    target_user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target_user:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="المستخدم غير موجود"),
            status_code=303,
        )

    try:
        if clean_password:
            conn.execute(
                "UPDATE users SET username = ?, password = ?, role = ? WHERE id = ?",
                (clean_username, clean_password, clean_role, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET username = ?, role = ? WHERE id = ?",
                (clean_username, clean_role, user_id),
            )
        sync_owner_property_access(conn, user_id, clean_role, selected_property_id)
        sync_tenant_user_link(conn, user_id, clean_role, selected_tenant_id)
        sync_user_company_access(conn, user_id, clean_role, selected_company, selected_section, selected_companies)
        sync_user_investment_project_access(conn, user_id, clean_role, selected_project_ids)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="اسم المستخدم مستخدم مسبقاً"),
            status_code=303,
        )
    finally:
        conn.close()

    return RedirectResponse(
        url=build_redirect_url(redirect_target, message="تم تحديث المستخدم"),
        status_code=303,
    )


@router.post("/admin/users/delete/{user_id}")
def admin_delete_user(
    user_id: int,
    request: Request,
    redirect_to: str = Form("/admin/users"),
):
    current_user = require_role(request, {"admin"})
    if isinstance(current_user, RedirectResponse) or isinstance(current_user, HTMLResponse):
        return current_user

    redirect_target = get_safe_redirect_target(redirect_to)
    if current_user["id"] == user_id:
        return RedirectResponse(
            url=build_redirect_url(redirect_target, error="لا يمكن حذف المستخدم الحالي"),
            status_code=303,
        )

    conn = get_db()
    conn.execute("DELETE FROM user_property_access WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM user_tenant_access WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM user_company_access WHERE user_id = ?", (user_id,))
    conn.execute("UPDATE property_tenants SET user_id = NULL WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(redirect_target, message="تم حذف المستخدم"),
        status_code=303,
    )
