# -*- coding: utf-8 -*-
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from auth import get_current_user, get_user_by_id, is_admin, is_employee, is_owner, is_partner, is_project_manager, is_tenant, password_matches, require_login, require_role
from admin_users import router as admin_users_router
from access_control import ensure_company_access, ensure_employee_any_section_access, ensure_employee_section_access, ensure_property_access, ensure_request_belongs_to_tenant, ensure_tenant_access, get_accessible_property_ids, get_employee_allowed_sections, get_primary_tenant_id, get_user_company_access_rows, get_user_tenant_access_ids, normalize_access_value, user_has_company_access, user_has_property_access, user_has_tenant_access
from fastapi.staticfiles import StaticFiles
from db import get_db
from html import escape
import json
import logging
import os
import re
import shutil
import sqlite3
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, time, timedelta
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote
import arabic_reshaper
from arabic_reshaper import reshape
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

app = FastAPI()
app.include_router(admin_users_router)
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("urbanrise")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("URBANRISE_SESSION_SECRET", "urban-rise-ai-internal-session-secret"),
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="static"), name="static")

AUTH_ROLES = {"admin", "partner", "employee", "project_manager", "owner", "tenant"}
PROTECTED_ROUTE_PREFIXES = (
    "/portal",
    "/company",
    "/inventory",
    "/projects",
    "/project",
    "/analyze-project",
    "/new-project",
    "/save-project",
    "/quotes",
    "/quote",
    "/new-quote",
    "/save-quote",
    "/contracts",
    "/contract",
    "/employees",
    "/new-employee",
    "/save-employee",
    "/realestate-development",
    "/development-project",
    "/new-development-project",
    "/new-development-unit",
    "/edit-development-unit",
    "/property-management",
    "/property-properties",
    "/property-details",
    "/property-units",
    "/property-tenants",
    "/property-rental-contracts",
    "/property-maintenance",
    "/property-supervisors",
    "/property-revenue",
    "/property-expenses",
    "/maintenance-management",
    "/client-maintenance",
    "/realestate-investment",
    "/investment-projects",
    "/investment-project",
    "/new-investment-project",
    "/investment-units",
    "/investment-tenants",
    "/investment-contracts",
    "/investment-income",
    "/investment-expenses",
    "/investment-employees",
    "/equipment",
    "/new-equipment",
    "/save-logistics-equipment",
    "/edit-project",
    "/edit-property",
    "/edit-contract",
    "/edit-unit",
    "/edit-property-unit",
    "/edit-property-tenant",
    "/edit-property-rental-contract",
    "/edit-property-maintenance",
    "/edit-property-expense",
    "/edit-project-expense",
    "/edit-project-daily",
    "/edit-project-equipment",
    "/edit-project-supplier",
    "/edit-logistics-equipment",
    "/delete-property",
    "/convert-to-contract",
    "/add-item",
)

# ======================
# زر الرئيسية العام
# ======================
HOME_BUTTON = """
<div class="system-topbar">
    <a href="/" class="glass-btn home-button">الرئيسية</a>
    <div id="global-auth-controls" class="system-topbar-auth"></div>
</div>
<script>
(function () {
    const authControls = document.getElementById("global-auth-controls");
    if (!authControls) {
        return;
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    fetch("/session-info", { credentials: "same-origin" })
        .then((response) => response.ok ? response.json() : { logged_in: false })
        .then((data) => {
            if (data.logged_in) {
                authControls.innerHTML =
                    '<span class="glass-btn" style="pointer-events:none;">مرحباً، ' + escapeHtml(data.username || "") + '</span>' +
                    '<a href="/logout" class="glass-btn back-btn">تسجيل الخروج</a>';
            } else {
                authControls.innerHTML = '<a href="/login" class="glass-btn">تسجيل الدخول</a>';
            }
        })
        .catch(() => {
            authControls.innerHTML = '<a href="/login" class="glass-btn">تسجيل الدخول</a>';
        });
})();
</script>
"""


def wants_json_response(request: Request) -> bool:
    path = request.url.path or "/"
    accept = (request.headers.get("accept") or "").lower()
    content_type = (request.headers.get("content-type") or "").lower()
    return (
        path.startswith("/ai/")
        or path == "/session-info"
        or "application/json" in accept
        or "application/json" in content_type
    )


def safe_error_response(request: Request, exc: Exception, status_code: int = 500):
    logger.exception("Unhandled application error on %s %s", request.method, request.url.path, exc_info=exc)
    if wants_json_response(request):
        return JSONResponse(
            {
                "ok": False,
                "error": "حدث خطأ غير متوقع أثناء معالجة الطلب.",
            },
            status_code=status_code,
        )
    return HTMLResponse(
        f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark" dir="rtl">
{HOME_BUTTON}
<div class="dashboard" style="max-width:760px;">
    <div class="inventory-panel inventory-table-panel" style="padding:32px;text-align:center;">
        <h1 class="system-title" style="margin-bottom:12px;">حدث خطأ غير متوقع</h1>
        <div class="inventory-note" style="margin:18px 0;">تعذر إكمال الطلب الحالي. يمكنك المحاولة مرة أخرى أو الرجوع للصفحة السابقة.</div>
        <a href="/" class="glass-btn back-btn">⬅ الرئيسية</a>
    </div>
</div>
""",
        status_code=status_code,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return safe_error_response(request, exc, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if wants_json_response(request):
        return JSONResponse(
            {
                "ok": False,
                "error": "البيانات المرسلة غير مكتملة أو غير صحيحة.",
                "details": exc.errors(),
            },
            status_code=422,
        )
    return HTMLResponse(
        f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark" dir="rtl">
{HOME_BUTTON}
<div class="dashboard" style="max-width:760px;">
    <div class="inventory-panel inventory-table-panel" style="padding:32px;text-align:center;">
        <h1 class="system-title" style="margin-bottom:12px;">تعذر تنفيذ الطلب</h1>
        <div class="inventory-note" style="margin:18px 0;">البيانات المطلوبة غير مكتملة أو غير صحيحة.</div>
        <a href="/" class="glass-btn back-btn">⬅ الرئيسية</a>
    </div>
</div>
""",
        status_code=422,
    )

# ======================
# قاعدة البيانات
# ======================

MAINTENANCE_VISIT_SLOTS = [
    ("08:00", "11:00"),
    ("11:00", "14:00"),
    ("14:00", "17:00"),
]


def detect_maintenance_issue_category(maintenance_type: str = "", description: str = "") -> str:
    text = f"{maintenance_type or ''} {description or ''}".lower()

    electricity_keywords = ["كهرباء", "الكهرباء مفصولة", "ماس", "قاطع", "التماس", "فيوز", "كهربائي"]
    water_keywords = ["تسريب", "ماء", "مياه", "سباكة", "تهريب", "مغسلة", "أنبوب"]

    if any(keyword in text for keyword in electricity_keywords):
        return "electricity"
    if any(keyword in text for keyword in water_keywords):
        return "water"
    return "general"


def get_property_responsible_person(conn, property_id: int) -> str:
    supervisor = conn.execute(
        """
        SELECT supervisor_name
        FROM property_supervisors
        WHERE property_id = ? AND supervisor_name IS NOT NULL AND TRIM(supervisor_name) != ''
        ORDER BY id DESC
        LIMIT 1
        """,
        (property_id,)
    ).fetchone()
    return supervisor["supervisor_name"] if supervisor else ""


def parse_scheduled_datetime(value: str):
    if not value:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_value, fmt)
        except ValueError:
            continue
    return None


def scheduled_date_has_explicit_time(value: str) -> bool:
    if not value:
        return False
    raw_value = str(value).strip()
    return " " in raw_value or "T" in raw_value


def normalize_scheduled_date_value(value: str) -> str:
    parsed = parse_scheduled_datetime(value)
    if not parsed:
        return value or ""
    if scheduled_date_has_explicit_time(value):
        return parsed.strftime("%Y-%m-%d %H:%M")
    return parsed.strftime("%Y-%m-%d")


def format_scheduled_datetime_for_input(value: str) -> str:
    parsed = parse_scheduled_datetime(value)
    if not parsed:
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M")


def build_visit_timing_message(start_dt: datetime, end_dt: datetime) -> str:
    return (
        f"تم جدولة زيارة الصيانة يوم {start_dt.strftime('%Y-%m-%d')} "
        f"من الساعة {start_dt.strftime('%H:%M')} إلى {end_dt.strftime('%H:%M')}"
    )


def find_next_available_maintenance_slot(conn, current_request_id: int = 0, start_from: datetime | None = None):
    if start_from is None:
        start_from = datetime.now()

    rows = conn.execute(
        """
        SELECT id, scheduled_date
        FROM maintenance_requests
        WHERE scheduled_date IS NOT NULL
          AND TRIM(scheduled_date) != ''
          AND status != 'cancelled'
          AND id != ?
        """,
        (current_request_id,)
    ).fetchall()

    daily_data = {}
    for row in rows:
        parsed = parse_scheduled_datetime(row["scheduled_date"])
        if not parsed:
            continue

        day_key = parsed.strftime("%Y-%m-%d")
        day_info = daily_data.setdefault(day_key, {"count": 0, "explicit": set()})
        day_info["count"] += 1

        slot_label = parsed.strftime("%H:%M")
        if slot_label in {slot[0] for slot in MAINTENANCE_VISIT_SLOTS}:
            day_info["explicit"].add(slot_label)

    current_day = start_from.date()
    while True:
        day_key = current_day.strftime("%Y-%m-%d")
        day_info = daily_data.setdefault(day_key, {"count": 0, "explicit": set()})

        if day_info["count"] < len(MAINTENANCE_VISIT_SLOTS):
            reserved_slots = set(day_info["explicit"])
            unknown_count = max(day_info["count"] - len(day_info["explicit"]), 0)

            if unknown_count:
                for slot_start, _ in MAINTENANCE_VISIT_SLOTS:
                    if slot_start in reserved_slots:
                        continue
                    reserved_slots.add(slot_start)
                    unknown_count -= 1
                    if unknown_count == 0:
                        break

            for slot_start, slot_end in MAINTENANCE_VISIT_SLOTS:
                slot_start_dt = datetime.combine(current_day, time.fromisoformat(slot_start))
                if current_day == start_from.date() and slot_start_dt < start_from:
                    continue
                if slot_start in reserved_slots:
                    continue
                slot_end_dt = datetime.combine(current_day, time.fromisoformat(slot_end))
                return slot_start_dt, slot_end_dt

        current_day += timedelta(days=1)
        start_from = datetime.combine(current_day, time(hour=0, minute=0))


def update_maintenance_request_record(
    conn,
    request_id: int,
    assigned_to: str,
    priority: str,
    status: str,
    estimated_cost,
    actual_cost,
    scheduled_date: str,
    admin_notes: str,
    final_report: str,
):
    request = conn.execute("SELECT * FROM maintenance_requests WHERE id = ?", (request_id,)).fetchone()
    if not request:
        return False

    completed_date = request["completed_date"]
    if status == "completed" and not completed_date:
        completed_date = datetime.now().strftime("%Y-%m-%d")

    conn.execute(
        """
        UPDATE maintenance_requests
        SET assigned_to = ?, priority = ?, status = ?, estimated_cost = ?, actual_cost = ?,
            scheduled_date = ?, admin_notes = ?, final_report = ?, updated_at = ?, completed_date = ?
        WHERE id = ?
        """,
        (
            assigned_to,
            priority,
            status,
            estimated_cost,
            actual_cost,
            normalize_scheduled_date_value(scheduled_date),
            admin_notes,
            final_report,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            completed_date,
            request_id
        )
    )
    return True

# ======================
# إنشاء الجداول (مرة وحدة عند التشغيل)
# ======================

conn = get_db()

# الموظفين
conn.execute("""
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    role TEXT,
    company TEXT
)
""")

# عروض الأسعار
conn.execute("""
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    client TEXT,
    status TEXT,
    client_id TEXT,
    client_address TEXT,
    project_location TEXT,
    duration TEXT
)
""")

# بنود عروض الأسعار
conn.execute("""
CREATE TABLE IF NOT EXISTS quote_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    description TEXT,
    qty REAL,
    unit_price REAL
)
""")

# دفعات عرض السعر
conn.execute("""
CREATE TABLE IF NOT EXISTS quote_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    title TEXT,
    percentage REAL
)
""")

# العقود
conn.execute("""
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    quote_id INTEGER,
    status TEXT,
    source_type TEXT,
    manual_project_id INTEGER
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS contract_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    uploaded_at TEXT
)
""")

# المشاريع
conn.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    name TEXT,
    client TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT,
    contract_id INTEGER,
    project_type TEXT,
    work_type TEXT,
    finish_level TEXT,
    area REAL,
    contract_value REAL
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS project_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT,
    amount REAL,
    date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS project_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    report TEXT,
    workers INTEGER,
    date TEXT,
    attachment_path TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS project_equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    name TEXT,
    qty INTEGER,
    status TEXT,
    date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS project_suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    name TEXT,
    material TEXT,
    phone TEXT,
    date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    location TEXT,
    units INTEGER,
    status TEXT,
    assigned_user_id INTEGER
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    name TEXT,
    type TEXT,
    rent REAL,
    status TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id INTEGER,
    name TEXT,
    phone TEXT,
    id_number TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER,
    unit_id INTEGER,
    rent REAL,
    payment_type TEXT,
    start_date TEXT,
    end_date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT,
    amount REAL,
    date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS investment_employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    name TEXT,
    role TEXT,
    phone TEXT
)
""")

# إدارة الأملاك
conn.execute("""
CREATE TABLE IF NOT EXISTS property_properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    location TEXT,
    property_type TEXT,
    status TEXT,
    notes TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    name TEXT,
    type TEXT,
    rent REAL,
    status TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    property_id INTEGER,
    unit_id INTEGER,
    name TEXT,
    phone TEXT,
    id_number TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_rent_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    unit_id INTEGER,
    tenant_id INTEGER,
    rent REAL,
    start_date TEXT,
    end_date TEXT,
    status TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS contract_installments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER,
    amount REAL,
    due_date TEXT,
    status TEXT,
    created_at TEXT,
    paid_at TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_supervisors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    supervisor_name TEXT,
    phone TEXT,
    notes TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_maintenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    unit_id INTEGER,
    title TEXT,
    description TEXT,
    cost REAL,
    date TEXT,
    status TEXT,
    supervisor_name TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS maintenance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    unit_id INTEGER,
    tenant_id INTEGER,
    request_source TEXT,
    maintenance_type TEXT,
    title TEXT,
    description TEXT,
    priority TEXT,
    status TEXT,
    estimated_cost REAL,
    actual_cost REAL,
    assigned_to TEXT,
    scheduled_date TEXT,
    completed_date TEXT,
    admin_notes TEXT,
    client_notes TEXT,
    final_report TEXT,
    created_at TEXT,
    updated_at TEXT,
    image_path TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS property_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER,
    unit_id INTEGER,
    maintenance_request_id INTEGER,
    expense_type TEXT,
    category TEXT,
    amount REAL,
    expense_date TEXT,
    vendor_or_payee TEXT,
    notes TEXT,
    created_at TEXT
)
""")

try:
    conn.execute("ALTER TABLE property_properties ADD COLUMN property_type TEXT")
except sqlite3.OperationalError:
    pass

try:
    conn.execute("ALTER TABLE property_properties ADD COLUMN notes TEXT")
except sqlite3.OperationalError:
    pass

try:
    conn.execute("ALTER TABLE property_tenants ADD COLUMN user_id INTEGER")
except sqlite3.OperationalError:
    pass

try:
    conn.execute("ALTER TABLE property_tenants ADD COLUMN tenant_type TEXT")
except sqlite3.OperationalError:
    pass

try:
    conn.execute("ALTER TABLE contract_installments ADD COLUMN paid_at TEXT")
except sqlite3.OperationalError:
    pass

for statement in [
    "ALTER TABLE projects ADD COLUMN project_type TEXT",
    "ALTER TABLE projects ADD COLUMN work_type TEXT",
    "ALTER TABLE projects ADD COLUMN finish_level TEXT",
    "ALTER TABLE projects ADD COLUMN area REAL",
    "ALTER TABLE projects ADD COLUMN contract_value REAL",
    "ALTER TABLE projects ADD COLUMN assigned_user_id INTEGER",
    "ALTER TABLE contracts ADD COLUMN source_type TEXT",
    "ALTER TABLE contracts ADD COLUMN manual_project_id INTEGER",
    "ALTER TABLE investment_projects ADD COLUMN assigned_user_id INTEGER",
    "ALTER TABLE maintenance_requests ADD COLUMN request_source TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN maintenance_type TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN priority TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN estimated_cost REAL",
    "ALTER TABLE maintenance_requests ADD COLUMN actual_cost REAL",
    "ALTER TABLE maintenance_requests ADD COLUMN assigned_to TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN scheduled_date TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN completed_date TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN admin_notes TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN client_notes TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN final_report TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN created_at TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN updated_at TEXT",
    "ALTER TABLE maintenance_requests ADD COLUMN image_path TEXT",
    "ALTER TABLE project_daily ADD COLUMN attachment_path TEXT",
]:
    try:
        conn.execute(statement)
    except sqlite3.OperationalError:
        pass

for statement in [
    "ALTER TABLE project_expenses ADD COLUMN category TEXT",
    "ALTER TABLE project_expenses ADD COLUMN other_type TEXT",
    "ALTER TABLE project_expenses ADD COLUMN vendor TEXT",
    "ALTER TABLE project_expenses ADD COLUMN payment_method TEXT",
    "ALTER TABLE project_expenses ADD COLUMN payment_status TEXT",
    "ALTER TABLE project_expenses ADD COLUMN invoice_reference TEXT",
    "ALTER TABLE project_expenses ADD COLUMN attachment_path TEXT",
    "ALTER TABLE project_expenses ADD COLUMN notes TEXT",
]:
    try:
        conn.execute(statement)
    except sqlite3.OperationalError:
        pass

# تطوير عقاري (مرحلة 1)
conn.execute("""
CREATE TABLE IF NOT EXISTS development_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    location TEXT,
    total_units INTEGER,
    status TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS development_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    name TEXT,
    type TEXT,
    price REAL,
    status TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS development_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT,
    amount REAL,
    date TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS development_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    unit_id INTEGER,
    price REAL,
    date TEXT
)
""")

# معدات اللوجستيات
conn.execute("""
CREATE TABLE IF NOT EXISTS logistics_equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    name TEXT,
    type TEXT,
    quantity INTEGER,
    status TEXT,
    location TEXT,
    purchase_date TEXT,
    cost REAL,
    date_added TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT,
    unit TEXT,
    quantity INTEGER DEFAULT 0,
    min_quantity INTEGER DEFAULT 0
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    type TEXT,
    quantity INTEGER,
    project_id INTEGER,
    company TEXT,
    date TEXT,
    notes TEXT,
    employee_name TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS user_property_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS user_tenant_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS user_company_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company TEXT NOT NULL,
    section TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS user_investment_project_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL
)
""")

try:
    conn.execute("ALTER TABLE inventory ADD COLUMN min_quantity INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    conn.execute("ALTER TABLE inventory_transactions ADD COLUMN employee_name TEXT")
except sqlite3.OperationalError:
    pass

users_exist = conn.execute(
    "SELECT id FROM users LIMIT 1"
).fetchone()
if not users_exist:
    conn.execute(
        """
        INSERT INTO users (username, password, full_name, role, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("admin", "admin123", "Admin", "admin", 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
else:
    existing_admin = conn.execute(
        "SELECT id FROM users WHERE username = 'admin' AND role = 'admin' LIMIT 1"
    ).fetchone()
    if not existing_admin:
        conn.execute(
            """
            INSERT INTO users (username, password, full_name, role, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("admin", "admin123", "Admin", "admin", 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
    else:
        conn.execute(
            "UPDATE users SET password = ?, is_active = 1 WHERE id = ?",
            ("admin123", existing_admin["id"])
        )

conn.commit()
conn.close()


@app.get("/session-info")
def session_info(request: Request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return JSONResponse({"logged_in": False, "username": "", "role": ""})
    return JSONResponse(
        {
            "logged_in": True,
            "username": user["username"] or "",
            "role": user["role"] or "",
        }
    )


def access_denied_response(message: str = "ليس لديك صلاحية الوصول إلى هذه الصفحة", back_url: str = "/") -> HTMLResponse:
    safe_message = escape(message)
    safe_back_url = escape(back_url or "/")
    return HTMLResponse(
        f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark" dir="rtl">
{HOME_BUTTON}
<div class="dashboard" style="max-width:760px;">
    <div class="inventory-panel inventory-table-panel" style="padding:32px;text-align:center;">
        <h1 class="system-title" style="margin-bottom:12px;">تم رفض الوصول</h1>
        <div class="inventory-note" style="margin:18px 0;">{safe_message}</div>
        <a href="{safe_back_url}" class="glass-btn back-btn">⬅ رجوع</a>
    </div>
</div>
""",
        status_code=403,
    )


def get_first_company_project_id(company: str) -> int | None:
    clean_company = normalize_access_value(company)
    if not clean_company:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM projects WHERE company = ? ORDER BY id ASC LIMIT 1",
            (clean_company,),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_arabic_digits(value: str) -> str:
    translation = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    return (value or "").translate(translation)


UPLOADS_DIR = os.path.join("static", "uploads")

PROJECT_EXPENSE_CATEGORIES = [
    "مواد",
    "أجور",
    "مقاول باطن",
    "نقل",
    "معدات",
    "وقود",
    "صيانة",
    "مصروف إداري",
    "أخرى",
]

PROJECT_EXPENSE_PAYMENT_METHODS = ["نقد", "تحويل", "عهدة"]
PROJECT_EXPENSE_PAYMENT_STATUSES = ["مدفوع", "غير مدفوع"]
PROJECT_EXPENSE_NAME_SUGGESTIONS = {
    "مواد": ["اسمنت", "حديد", "بلاط", "دهان"],
    "أجور": ["عامل", "مشرف", "مهندس", "فني"],
    "مقاول باطن": ["مقاول عظم", "مقاول تشطيب", "مقاول كهرباء", "مقاول سباكة"],
    "نقل": ["نقل مواد", "تحميل", "تفريغ", "مشوار توريد"],
    "معدات": ["إيجار معدة", "صيانة معدة", "قطع غيار", "تشغيل معدة"],
    "وقود": ["ديزل", "بنزين", "تعبئة معدة", "تعبئة مركبة"],
    "صيانة": ["صيانة معدة", "صيانة موقع", "إصلاح أعطال", "تبديل قطعة"],
    "مصروف إداري": ["قرطاسية", "اتصالات", "رسوم", "ضيافة"],
    "أخرى": ["مصروف متنوع", "خدمة طارئة", "احتياج خاص"],
}
PROJECT_EXPENSE_CATEGORY_THEMES = {
    "مواد": "materials",
    "أجور": "labor",
    "مقاول باطن": "subcontractor",
    "نقل": "transport",
    "معدات": "equipment",
    "وقود": "fuel",
    "صيانة": "maintenance",
    "مصروف إداري": "admin",
    "أخرى": "other",
}
PDF_REPORT_FONT_NAME = "UrbanRiseArabic"
PDF_REPORT_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
PDF_REPORT_FONT_CANDIDATES = [
    os.path.join(PDF_REPORT_FONT_DIR, "NotoNaskhArabic-Regular.ttf"),
    os.path.join(PDF_REPORT_FONT_DIR, "Amiri-Regular.ttf"),
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\trado.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
WORKS_COMPANY_PROFILE = {
    "name": "أوربان رايز ووركس",
    "commercial_register": "1009136954",
    "mobile": "0576080955",
    "city": "الرياض",
    "address": "طريق أبو بكر الصديق - النرجس - الرياض",
    "representative": "م. فهد بن عبدالله آل عمره",
    "national_number": "7040109964",
}


def save_project_daily_attachment(attachment: UploadFile | None) -> str:
    if not attachment or not getattr(attachment, "filename", ""):
        return ""

    original_name = os.path.basename(str(attachment.filename or "")).strip()
    if not original_name:
        return ""

    _, ext = os.path.splitext(original_name)
    safe_ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:10]
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.splitext(original_name)[0]).strip("_") or "attachment"
    unique_name = f"project_daily_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_stem}{safe_ext}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    file_path = os.path.join(UPLOADS_DIR, unique_name)

    attachment.file.seek(0)
    file_bytes = attachment.file.read()
    if not file_bytes:
        return ""

    with open(file_path, "wb") as output_file:
        output_file.write(file_bytes)

    return f"/static/uploads/{unique_name}"


def save_project_expense_attachment(attachment: UploadFile | None) -> str:
    if not attachment or not getattr(attachment, "filename", ""):
        return ""

    original_name = os.path.basename(str(attachment.filename or "")).strip()
    if not original_name:
        return ""

    _, ext = os.path.splitext(original_name)
    safe_ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:10]
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.splitext(original_name)[0]).strip("_") or "expense"
    unique_name = f"project_expense_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_stem}{safe_ext}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    file_path = os.path.join(UPLOADS_DIR, unique_name)

    attachment.file.seek(0)
    file_bytes = attachment.file.read()
    if not file_bytes:
        return ""

    with open(file_path, "wb") as output_file:
        output_file.write(file_bytes)

    return f"/static/uploads/{unique_name}"


def delete_project_daily_attachment_file(attachment_path: str) -> None:
    if not attachment_path:
        return

    normalized_path = str(attachment_path).replace("\\", "/")
    expected_prefix = "/static/uploads/"
    if not normalized_path.startswith(expected_prefix):
        return

    local_relative_path = normalized_path.lstrip("/").replace("/", os.sep)
    local_path = os.path.abspath(local_relative_path)
    uploads_root = os.path.abspath(UPLOADS_DIR)
    if not local_path.startswith(uploads_root):
        return

    if os.path.exists(local_path):
        try:
            os.remove(local_path)
        except OSError:
            pass


def build_project_expense_category_options(selected_value: str = "") -> str:
    options = ['<option value="">اختر التصنيف</option>']
    for category in PROJECT_EXPENSE_CATEGORIES:
        selected = "selected" if selected_value == category else ""
        options.append(f'<option value="{escape(category)}" {selected}>{escape(category)}</option>')
    return "".join(options)


def project_expense_category_theme(category: str) -> str:
    return PROJECT_EXPENSE_CATEGORY_THEMES.get((category or "").strip(), "other")


def build_project_expense_category_selector(selected_value: str = "") -> str:
    chips = []
    for category in PROJECT_EXPENSE_CATEGORIES:
        is_selected = selected_value == category
        theme = project_expense_category_theme(category)
        chips.append(
            f"""
            <button
                type="button"
                class="expense-category-chip{' is-selected' if is_selected else ''}"
                data-category-value="{escape(category)}"
                data-theme="{escape(theme)}"
                aria-pressed="{str(is_selected).lower()}"
            >
                {escape(category)}
            </button>
            """
        )
    return f"""
    <div class="expense-category-selector" id="expense-category-selector">
        {''.join(chips)}
    </div>
    """


def build_project_expense_name_suggestion_buttons() -> str:
    buttons = []
    for category, suggestions in PROJECT_EXPENSE_NAME_SUGGESTIONS.items():
        buttons_html = "".join(
            f'<button type="button" class="expense-suggestion-chip" data-suggestion="{escape(item)}">{escape(item)}</button>'
            for item in suggestions
        )
        buttons.append(
            f'<div class="expense-suggestion-group" data-category="{escape(category)}" hidden>{buttons_html}</div>'
        )
    return "".join(buttons)


def build_project_supplier_datalist(suppliers) -> str:
    names = []
    seen = set()
    for supplier in suppliers:
        name = (supplier["name"] or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(f'<option value="{escape(name)}"></option>')
    return "".join(names)


def project_expense_category_label(expense) -> str:
    category = (expense["category"] or "").strip() if "category" in expense.keys() else ""
    other_type = (expense["other_type"] or "").strip() if "other_type" in expense.keys() else ""
    if category == "أخرى" and other_type:
        return f"أخرى - {other_type}"
    return category or "-"


def build_project_expense_category_badge(expense) -> str:
    category = (expense["category"] or "").strip() if "category" in expense.keys() else ""
    label = project_expense_category_label(expense)
    theme = project_expense_category_theme(category)
    return f'<span class="expense-badge expense-badge-category" data-theme="{escape(theme)}">{escape(label)}</span>' if label != "-" else "-"


def build_project_expense_payment_status_badge(status: str) -> str:
    cleaned_status = (status or "").strip()
    if not cleaned_status:
        return "-"
    theme = "paid" if cleaned_status == "مدفوع" else "unpaid"
    return f'<span class="expense-badge expense-badge-status" data-status="{escape(theme)}">{escape(cleaned_status)}</span>'


def get_pdf_report_font_name() -> str:
    try:
        pdfmetrics.getFont(PDF_REPORT_FONT_NAME)
        return PDF_REPORT_FONT_NAME
    except KeyError:
        pass

    for font_path in PDF_REPORT_FONT_CANDIDATES:
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont(PDF_REPORT_FONT_NAME, font_path))
            return PDF_REPORT_FONT_NAME
    return "Helvetica"


def format_arabic_pdf_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def fix_arabic_text(text):
    if not text:
        return ""
    try:
        reshaped = reshape(str(text))
        return get_display(reshaped)
    except:
        return text


def build_quote_description_paragraph_text(value, font_name: str, font_size: float, max_width: float) -> str:
    text = str(value or "").strip()
    if not text:
        return format_arabic_pdf_text("-")

    wrapped_lines = []
    source_lines = text.splitlines() or [text]

    def append_wrapped_line(line_text: str) -> None:
        normalized = " ".join(str(line_text or "").split())
        if not normalized:
            wrapped_lines.append("")
            return

        words = normalized.split(" ")
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
                continue

            if current:
                wrapped_lines.append(current)

            if pdfmetrics.stringWidth(word, font_name, font_size) <= max_width:
                current = word
                continue

            chunk = ""
            for char in word:
                candidate_chunk = f"{chunk}{char}"
                if chunk and pdfmetrics.stringWidth(candidate_chunk, font_name, font_size) > max_width:
                    wrapped_lines.append(chunk)
                    chunk = char
                else:
                    chunk = candidate_chunk
            current = chunk

        if current:
            wrapped_lines.append(current)

    for source_line in source_lines:
        append_wrapped_line(source_line)

    formatted_lines = [escape(format_arabic_pdf_text(line)) if line else "" for line in wrapped_lines]
    return "<br/>".join(formatted_lines) or escape(format_arabic_pdf_text("-"))


def decimal_from_value(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def format_percentage_display(value) -> str:
    amount = decimal_from_value(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integral_amount = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amount == integral_amount:
        return f"{integral_amount}%"
    normalized = format(amount.normalize(), "f").rstrip("0").rstrip(".")
    return f"{normalized}%"


def calculate_percentage_amount(total, percentage) -> Decimal:
    total_decimal = decimal_from_value(total)
    percentage_decimal = decimal_from_value(percentage)
    return (total_decimal * percentage_decimal / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_simple_arabic_table(data, col_widths, header_background="#dfeaf3", body_row_backgrounds=None):
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="RIGHT")
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_background)),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if body_row_backgrounds:
        style_commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), body_row_backgrounds))
    table.setStyle(TableStyle(style_commands))
    return table


def draw_works_contract_pdf_frame(canvas, doc):
    canvas.saveState()
    canvas.setFont(get_pdf_report_font_name(), 10)
    canvas.setFillColor(colors.HexColor("#44576b"))
    canvas.setStrokeColor(colors.HexColor("#d7dee7"))
    footer_y = 12 * mm
    line_y = footer_y + 7 * mm
    canvas.line(doc.leftMargin, line_y, doc.pagesize[0] - doc.rightMargin, line_y)
    canvas.setFont(get_pdf_report_font_name(), 8.5)
    canvas.drawCentredString(
        doc.pagesize[0] / 2,
        footer_y,
        format_arabic_pdf_text(
            f"الرقم الوطني الموحد {WORKS_COMPANY_PROFILE['national_number']} / {WORKS_COMPANY_PROFILE['address']} / جوال {WORKS_COMPANY_PROFILE['mobile']}"
        ),
    )
    canvas.restoreState()


def build_project_expenses_report_pdf(project, expenses, contract_total: float, company: str = "") -> tuple[str, str]:
    os.makedirs("pdfs", exist_ok=True)
    file_name = f"project_expenses_report_{project['id']}.pdf"
    file_path = os.path.join("pdfs", file_name)
    font_name = get_pdf_report_font_name()

    total_expenses = sum(safe_float(expense["amount"]) for expense in expenses)
    remaining = contract_total - total_expenses
    spend_ratio = (total_expenses / contract_total * 100) if contract_total > 0 else 0.0
    total_paid = sum(
        safe_float(expense["amount"])
        for expense in expenses
        if (expense["payment_status"] or "").strip() == "مدفوع"
    )
    total_unpaid = sum(
        safe_float(expense["amount"])
        for expense in expenses
        if (expense["payment_status"] or "").strip() == "غير مدفوع"
    )
    notes_list = [
        (expense["notes"] or "").strip()
        for expense in expenses
        if "notes" in expense.keys() and (expense["notes"] or "").strip()
    ]
    general_notes = " | ".join(notes_list[:4])
    report_company_name = "أوربان رايز ووركس" if normalize_access_value(company) == "works" else "مجموعة أوربان رايز"

    doc = SimpleDocTemplate(
        file_path,
        pagesize=landscape(A4),
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ExpenseReportTitle",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#0f2940"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "ExpenseReportSubtitle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=14,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#4b5f73"),
    )
    section_style = ParagraphStyle(
        "ExpenseReportSection",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#11466b"),
        spaceAfter=8,
        spaceBefore=8,
    )
    body_style = ParagraphStyle(
        "ExpenseReportBody",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=14,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#1e293b"),
    )

    story = [
        Paragraph(format_arabic_pdf_text(report_company_name), title_style),
        Paragraph(
            format_arabic_pdf_text(
                f"تقرير مصروفات المشروع | اسم المشروع: {project['name'] or ('مشروع رقم ' + str(project['id']))} | تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ),
            subtitle_style,
        ),
        Spacer(1, 8),
        Paragraph(format_arabic_pdf_text("الملخص المالي"), section_style),
    ]

    summary_data = [
        [
            Paragraph(format_arabic_pdf_text("نسبة الصرف"), body_style),
            Paragraph(format_arabic_pdf_text("المتبقي"), body_style),
            Paragraph(format_arabic_pdf_text("إجمالي المصروفات"), body_style),
            Paragraph(format_arabic_pdf_text("قيمة العقد"), body_style),
        ],
        [
            Paragraph(format_arabic_pdf_text(f"{spend_ratio:.1f}%"), body_style),
            Paragraph(format_arabic_pdf_text(f"{format_currency(remaining)} ريال"), body_style),
            Paragraph(format_arabic_pdf_text(f"{format_currency(total_expenses)} ريال"), body_style),
            Paragraph(format_arabic_pdf_text(f"{format_currency(contract_total)} ريال"), body_style),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[55 * mm, 60 * mm, 65 * mm, 60 * mm], hAlign="RIGHT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f7")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7dee7")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.extend([summary_table, Spacer(1, 10), Paragraph(format_arabic_pdf_text("تفاصيل المصروفات"), section_style)])

    table_rows = [[
        Paragraph(format_arabic_pdf_text("التاريخ"), body_style),
        Paragraph(format_arabic_pdf_text("البيان"), body_style),
        Paragraph(format_arabic_pdf_text("التصنيف"), body_style),
        Paragraph(format_arabic_pdf_text("المورد"), body_style),
        Paragraph(format_arabic_pdf_text("طريقة الدفع"), body_style),
        Paragraph(format_arabic_pdf_text("حالة السداد"), body_style),
        Paragraph(format_arabic_pdf_text("المبلغ"), body_style),
    ]]

    for expense in expenses:
        table_rows.append([
            Paragraph(format_arabic_pdf_text(expense["date"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(expense["title"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(project_expense_category_label(expense)), body_style),
            Paragraph(format_arabic_pdf_text(expense["vendor"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(expense["payment_method"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(expense["payment_status"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(f"{format_currency(safe_float(expense['amount']))} ريال"), body_style),
        ])

    if len(table_rows) == 1:
        table_rows.append([
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("لا توجد مصروفات مسجلة"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("0 ريال"), body_style),
        ])

    expenses_table = Table(
        table_rows,
        colWidths=[30 * mm, 58 * mm, 42 * mm, 42 * mm, 35 * mm, 32 * mm, 33 * mm],
        repeatRows=1,
        hAlign="RIGHT",
    )
    expenses_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfeaf3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#14344f")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.extend([expenses_table, Spacer(1, 10), Paragraph(format_arabic_pdf_text("مؤشرات إضافية"), section_style)])

    extra_notes_lines = [
        f"عدد القيود: {len(expenses)}",
        f"إجمالي المدفوع: {format_currency(total_paid)} ريال",
        f"إجمالي غير المدفوع: {format_currency(total_unpaid)} ريال",
    ]
    if general_notes:
        extra_notes_lines.append(f"ملاحظات عامة: {general_notes}")
    else:
        extra_notes_lines.append("ملاحظات عامة: لا توجد ملاحظات إضافية")

    for line in extra_notes_lines:
        story.append(Paragraph(format_arabic_pdf_text(line), body_style))
        story.append(Spacer(1, 3))

    doc.build(story)
    return file_path, file_name


def build_quote_report_pdf(quote, items, payments, company: str = "") -> tuple[str, str]:
    os.makedirs("pdfs", exist_ok=True)
    file_name = f"quote_report_{quote['id']}.pdf"
    file_path = os.path.join("pdfs", file_name)
    font_name = get_pdf_report_font_name()
    report_company_name = "أوربان رايز ووركس" if normalize_access_value(company) == "works" else "مجموعة أوربان رايز"

    total = 0.0
    for item in items:
        total += safe_float(item["qty"]) * safe_float(item["unit_price"])

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("QuoteReportTitle", parent=styles["Heading1"], fontName=font_name, fontSize=18, leading=24, alignment=TA_RIGHT, textColor=colors.HexColor("#0f2940"))
    subtitle_style = ParagraphStyle("QuoteReportSubtitle", parent=styles["Normal"], fontName=font_name, fontSize=10, leading=14, alignment=TA_RIGHT, textColor=colors.HexColor("#4b5f73"))
    section_style = ParagraphStyle("QuoteReportSection", parent=styles["Heading2"], fontName=font_name, fontSize=13, leading=18, alignment=TA_RIGHT, textColor=colors.HexColor("#11466b"), spaceAfter=6, spaceBefore=6)
    body_style = ParagraphStyle("QuoteReportBody", parent=styles["Normal"], fontName=font_name, fontSize=10, leading=16, alignment=TA_RIGHT, textColor=colors.HexColor("#1e293b"))
    items_section_style = ParagraphStyle("QuoteReportItemsSection", parent=section_style, keepWithNext=True)
    description_style = ParagraphStyle("QuoteReportDescription", parent=body_style, leading=14, alignment=TA_RIGHT, wordWrap="RTL")
    centered_style = ParagraphStyle("QuoteReportCentered", parent=body_style, alignment=1, fontSize=15, leading=20, textColor=colors.HexColor("#102235"))
    intro_text = "السلام عليكم ورحمة الله وبركاته، نفيدكم بتقديم عرض السعر التالي حسب البيانات والبنود الموضحة أدناه."
    footer_text = "هذا العرض صالح للاعتماد وفق البنود والأسعار الموضحة أعلاه، ويسعدنا خدمتكم والإجابة عن أي استفسار."

    story = [
        Paragraph(format_arabic_pdf_text(report_company_name), title_style),
        Paragraph(
            format_arabic_pdf_text(
                f"عرض سعر | رقم العرض: {quote['id']} | تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ),
            subtitle_style,
        ),
        Spacer(1, 8),
        Paragraph(format_arabic_pdf_text("عرض سعر"), centered_style),
        Spacer(1, 5),
        Paragraph(format_arabic_pdf_text(intro_text), body_style),
        Spacer(1, 8),
        Paragraph(format_arabic_pdf_text("بيانات العميل والمشروع"), section_style),
    ]

    summary_table = Table([
        [
            Paragraph(format_arabic_pdf_text("تاريخ العرض"), body_style),
            Paragraph(format_arabic_pdf_text("مدة التنفيذ"), body_style),
            Paragraph(format_arabic_pdf_text("موقع المشروع"), body_style),
            Paragraph(format_arabic_pdf_text("اسم العميل"), body_style),
        ],
        [
            Paragraph(format_arabic_pdf_text(datetime.now().strftime("%Y-%m-%d")), body_style),
            Paragraph(format_arabic_pdf_text(quote["duration"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(quote["project_location"] or "-"), body_style),
            Paragraph(format_arabic_pdf_text(quote["client"] or "-"), body_style),
        ],
    ], colWidths=[45 * mm, 45 * mm, 85 * mm, 65 * mm], hAlign="RIGHT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f7")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7dee7")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.extend([summary_table, Spacer(1, 10), Paragraph(format_arabic_pdf_text("بنود عرض السعر"), items_section_style)])

    description_col_width = 130 * mm
    items_rows = [[
        Paragraph(format_arabic_pdf_text("الإجمالي"), body_style),
        Paragraph(format_arabic_pdf_text("سعر الوحدة"), body_style),
        Paragraph(format_arabic_pdf_text("الكمية"), body_style),
        Paragraph(format_arabic_pdf_text("الوصف"), body_style),
    ]]
    for item in items:
        line_total = safe_float(item["qty"]) * safe_float(item["unit_price"])
        items_rows.append([
            Paragraph(format_arabic_pdf_text(f"{format_currency(line_total)} ريال"), body_style),
            Paragraph(format_arabic_pdf_text(f"{format_currency(safe_float(item['unit_price']))} ريال"), body_style),
            Paragraph(format_arabic_pdf_text(format_currency(safe_float(item["qty"]))), body_style),
            Paragraph(
                build_quote_description_paragraph_text(item["description"] or "-", font_name, description_style.fontSize, description_col_width - 8),
                description_style,
            ),
        ])
    if len(items_rows) == 1:
        items_rows.append([
            Paragraph(format_arabic_pdf_text("0 ريال"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("لا توجد بنود"), body_style),
        ])
    items_rows.append([
        Paragraph(format_arabic_pdf_text(f"{format_currency(total)} ريال"), body_style),
        Paragraph(format_arabic_pdf_text(""), body_style),
        Paragraph(format_arabic_pdf_text(""), body_style),
        Paragraph(format_arabic_pdf_text("الإجمالي"), body_style),
    ])
    items_table = Table(
        items_rows,
        colWidths=[40 * mm, 40 * mm, 30 * mm, description_col_width],
        repeatRows=1,
        splitByRow=1,
        splitInRow=1,
        hAlign="RIGHT",
    )
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfeaf3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef4f8")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (3, 0), (3, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([items_table, Spacer(1, 10), Paragraph(format_arabic_pdf_text("جدول الدفعات"), section_style)])

    payment_rows = [[
        Paragraph(format_arabic_pdf_text("المبلغ"), body_style),
        Paragraph(format_arabic_pdf_text("النسبة"), body_style),
        Paragraph(format_arabic_pdf_text("اسم الدفعة"), body_style),
    ]]
    for payment in payments:
        amount = calculate_percentage_amount(total, payment["percentage"])
        payment_rows.append([
            Paragraph(format_arabic_pdf_text(f"{format_currency(float(amount))} ريال"), body_style),
            Paragraph(format_arabic_pdf_text(format_percentage_display(payment["percentage"])), body_style),
            Paragraph(format_arabic_pdf_text(payment["title"] or "-"), body_style),
        ])
    if len(payment_rows) == 1:
        payment_rows.append([
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("-"), body_style),
            Paragraph(format_arabic_pdf_text("لا توجد دفعات"), body_style),
        ])
    payments_table = Table(payment_rows, colWidths=[55 * mm, 45 * mm, 110 * mm], repeatRows=1, hAlign="RIGHT")
    payments_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfeaf3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cfd8e3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([
        payments_table,
        Spacer(1, 10),
        Paragraph(format_arabic_pdf_text(footer_text), body_style),
        Spacer(1, 8),
        Paragraph(format_arabic_pdf_text("بيانات التواصل: 0566005668 | kalamrah505@gmail.com | الرياض - حي النرجس - طريق أبو بكر الصديق"), subtitle_style),
    ])

    doc = SimpleDocTemplate(file_path, pagesize=landscape(A4), rightMargin=16 * mm, leftMargin=16 * mm, topMargin=16 * mm, bottomMargin=14 * mm)
    doc.build(story)
    return file_path, file_name


def build_contract_report_pdf(contract, quote, items, payments, company: str = "", project=None) -> tuple[str, str]:
    os.makedirs("pdfs", exist_ok=True)
    file_name = f"contract_report_{contract['id']}.pdf"
    file_path = os.path.join("pdfs", file_name)
    font_name = get_pdf_report_font_name()
    company_profile = WORKS_COMPANY_PROFILE
    total = sum(safe_float(item["qty"]) * safe_float(item["unit_price"]) for item in items)
    contract_number = f"25/{int(contract['id']):04d}"
    issue_date = datetime.now().strftime("%d-%m-%Y")
    project_name = ""
    if project and "name" in project.keys():
        project_name = (project["name"] or "").strip()
    project_name = project_name or ""
    project_location = (quote["project_location"] or "").strip() or ""
    client_name = (quote["client"] or "").strip() or ""
    client_id = (quote["client_id"] or "").strip() or ""
    client_address = (quote["client_address"] or "").strip() or ""
    duration_text = (quote["duration"] or "").strip() or ""

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ContractReportTitle", parent=styles["Heading1"], fontName=font_name, fontSize=18, leading=24, alignment=TA_RIGHT, textColor=colors.HexColor("#213547"), spaceAfter=4)
    subtitle_style = ParagraphStyle("ContractReportSubtitle", parent=styles["Normal"], fontName=font_name, fontSize=9.5, leading=14, alignment=TA_RIGHT, textColor=colors.HexColor("#6b7280"))
    body_style = ParagraphStyle("ContractReportBody", parent=styles["Normal"], fontName=font_name, fontSize=10.5, leading=18, alignment=TA_RIGHT, textColor=colors.HexColor("#2b3440"))
    section_style = ParagraphStyle("ContractReportSection", parent=body_style, fontSize=11.5, leading=19, textColor=colors.HexColor("#1f2937"), spaceBefore=4, spaceAfter=0)
    centered_style = ParagraphStyle("ContractReportCentered", parent=body_style, alignment=1, fontSize=18, leading=26, textColor=colors.HexColor("#1f2d3d"))
    small_label_style = ParagraphStyle("ContractReportSmallLabel", parent=body_style, fontSize=9.2, leading=13, textColor=colors.HexColor("#7a6851"))
    card_value_style = ParagraphStyle("ContractReportCardValue", parent=body_style, fontSize=11, leading=17, textColor=colors.HexColor("#243445"))

    contract_page_width = 166 * mm
    contract_column_width = 62 * mm

    def contract_paragraph(text, style, width=contract_page_width):
        return Paragraph(
            build_quote_description_paragraph_text(text, font_name, style.fontSize, width),
            style,
        )

    scope_story = []
    if items:
        for index, item in enumerate(items, start=1):
            description = (item["description"] or "").strip() or ""
            line_total = safe_float(item["qty"]) * safe_float(item["unit_price"])
            scope_story.append(contract_paragraph(f"{index}. {description}", body_style))
            scope_story.append(contract_paragraph(f"بمبلغ: {format_currency(line_total)} ريال", body_style))
    else:
        scope_story.append(contract_paragraph("1. ", body_style))
        scope_story.append(contract_paragraph("بمبلغ: ", body_style))

    payment_story = []
    if payments:
        for payment in payments:
            amount = calculate_percentage_amount(total, payment["percentage"])
            title = (payment["title"] or "").strip()
            if title:
                payment_text = f"- {title} بنسبة ({format_percentage_display(payment['percentage'])}) بمبلغ {format_currency(float(amount))} ريال"
            else:
                payment_text = f"- دفعة بنسبة ({format_percentage_display(payment['percentage'])}) بمبلغ {format_currency(float(amount))} ريال"
            payment_story.append(contract_paragraph(payment_text, body_style))
    else:
        payment_story.append(contract_paragraph("- ", body_style))

    header_band = Table([[
        contract_paragraph(company_profile["name"], title_style, 53 * mm),
        contract_paragraph("عقد تنفيذ أعمال مقاولات عامة", centered_style, 83 * mm),
    ]], colWidths=[65 * mm, 95 * mm], hAlign="RIGHT")
    header_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5efe6")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d6c4ab")),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, colors.HexColor("#ddcfba")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    meta_cards = Table([
        [
            [
                contract_paragraph("رقم العقد", small_label_style, 66 * mm),
                Spacer(1, 5),
                contract_paragraph(contract_number, card_value_style, 66 * mm),
            ],
            [
                contract_paragraph("التاريخ", small_label_style, 66 * mm),
                Spacer(1, 5),
                contract_paragraph(issue_date, card_value_style, 66 * mm),
            ],
        ]
    ], colWidths=[78 * mm, 78 * mm], hAlign="RIGHT")
    meta_cards.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbf8f3")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#e2d6c5")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#ede3d6")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    def build_section_heading(text: str):
        heading = Table([[contract_paragraph(text, section_style, contract_page_width - 12)]], colWidths=[166 * mm], hAlign="RIGHT")
        heading.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4eee7")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d8c9b5")),
            ("LINEAFTER", (0, 0), (0, 0), 3, colors.HexColor("#b79a75")),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return heading

    parties_table = Table([
        [
            [
                contract_paragraph("الطرف الثاني", section_style, contract_column_width),
                Spacer(1, 4),
                contract_paragraph(f"السيد/ {client_name}، بموجب هوية رقم {client_id}،", body_style, contract_column_width),
                contract_paragraph(f"وعنوانه: {client_address}،", body_style, contract_column_width),
                contract_paragraph("ويشار إليه فيما بعد بـ \"الطرف الثاني\" أو \"العميل\".", body_style, contract_column_width),
                Spacer(1, 4),
                contract_paragraph(f"المشروع: {project_name or ''}", subtitle_style, contract_column_width),
                contract_paragraph(f"الموقع: {project_location or ''}", subtitle_style, contract_column_width),
            ],
            [
                contract_paragraph("الطرف الأول", section_style, contract_column_width),
                Spacer(1, 4),
                contract_paragraph(f"شركة {company_profile['name']}، سجل تجاري رقم {company_profile['commercial_register']}،", body_style, contract_column_width),
                contract_paragraph(f"وعنوانها: {company_profile['address']}،", body_style, contract_column_width),
                contract_paragraph(f"ويمثلها في هذا العقد {company_profile['representative']}،", body_style, contract_column_width),
                contract_paragraph("ويشار إليها فيما بعد بـ \"الطرف الأول\" أو \"المقاول\".", body_style, contract_column_width),
                Spacer(1, 4),
                contract_paragraph(f"الرقم الوطني الموحد: {company_profile['national_number']}", subtitle_style, contract_column_width),
                contract_paragraph(f"جوال: {company_profile['mobile']}", subtitle_style, contract_column_width),
            ],
        ]
    ], colWidths=[82 * mm, 82 * mm], hAlign="RIGHT")
    parties_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fcfaf7")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#ddd1c0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ece2d7")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    contract_value_card = Table([[
        [
            contract_paragraph("القيمة الإجمالية", small_label_style, contract_page_width - 16),
            Spacer(1, 4),
            contract_paragraph(f"{format_currency(total)} ريال سعودي فقط لا غير", card_value_style, contract_page_width - 16),
        ]
    ]], colWidths=[166 * mm], hAlign="RIGHT")
    contract_value_card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f2eb")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d8c9b5")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))

    story = [
        header_band,
        Spacer(1, 8),
        meta_cards,
        Spacer(1, 10),
        parties_table,
        Spacer(1, 8),
        contract_paragraph("وقد قرر الطرفان وهما بكامل أهليتهما الشرعية والنظامية التعاقد وفق البنود التالية:", body_style),
        Spacer(1, 8),
        build_section_heading("أولاً: التمهيد"),
        Spacer(1, 4),
        contract_paragraph("يعد هذا التمهيد جزءاً لا يتجزأ من هذا العقد ومكملاً له في جميع بنوده.", body_style),
        contract_paragraph(f"وحيث أن الطرف الثاني يرغب في تنفيذ أعمال مقاولات عامة في الموقع الكائن في {project_location or ''}،", body_style),
        contract_paragraph("وحيث أن الطرف الأول يمتلك الخبرة والكفاءة لتنفيذ هذه الأعمال،", body_style),
        contract_paragraph("فقد تم الاتفاق على ما يلي:", body_style),
        Spacer(1, 8),
        build_section_heading("ثانياً: نطاق العمل"),
        Spacer(1, 4),
    ]

    story.extend(scope_story)
    story.extend([
        Spacer(1, 8),
        build_section_heading("ثالثاً: مدة التنفيذ"),
        Spacer(1, 4),
        contract_paragraph(f"1. مدة تنفيذ الأعمال هي ({duration_text}) تبدأ من تاريخ استلام الموقع.", body_style),
        contract_paragraph("2. في حال تأخر الطرف الثاني في تسليم الموقع أو الدفعات، تمدد المدة تلقائياً.", body_style),
        Spacer(1, 8),
        build_section_heading("رابعاً: قيمة العقد والدفعات"),
        Spacer(1, 4),
        contract_value_card,
        Spacer(1, 6),
        contract_paragraph("2. يتم السداد كالتالي:", body_style),
    ])
    story.extend(payment_story)
    story.extend([
        contract_paragraph("3. يتم السداد بموجب تحويل بنكي إلى حساب الطرف الأول لدى بنك الإنماء رقم (SA5405000068207285292000).", body_style),
        Spacer(1, 8),
        build_section_heading("خامساً: الشروط العامة"),
        Spacer(1, 4),
        contract_paragraph("1. تنفيذ الأعمال وفق المتطلبات المتفق عليها.", body_style),
        contract_paragraph("2. مسؤولية سلامة العاملين على الطرف الأول.", body_style),
        contract_paragraph("3. يحق للمقاول وضع لوحة تعريفية في الموقع.", body_style),
        Spacer(1, 8),
        build_section_heading("سادساً: مسؤوليات الطرفين"),
        Spacer(1, 4),
        contract_paragraph("مسؤوليات الطرف الأول (المقاول):", body_style),
        contract_paragraph("1. توفير العمالة والمعدات اللازمة.", body_style),
        contract_paragraph("2. المحافظة على نظافة الموقع.", body_style),
        contract_paragraph("3. تسليم الأعمال في الوقت المحدد.", body_style),
        Spacer(1, 4),
        contract_paragraph("مسؤوليات الطرف الثاني (العميل):", body_style),
        contract_paragraph("1. تسليم الموقع جاهز للعمل.", body_style),
        contract_paragraph("2. سداد الدفعات في وقتها.", body_style),
        Spacer(1, 8),
        build_section_heading("سابعاً: الاستلام"),
        Spacer(1, 4),
        contract_paragraph("1. يتم إشعار العميل بموعد التسليم.", body_style),
        contract_paragraph("2. يجب الاستلام خلال 3 أيام من الإشعار.", body_style),
        contract_paragraph("3. في حال عدم الحضور يعتبر المشروع مستلماً حكماً.", body_style),
        contract_paragraph("4. يحق للمقاول توثيق التسليم.", body_style),
        Spacer(1, 8),
        build_section_heading("ثامناً: الأحكام العامة"),
        Spacer(1, 4),
        contract_paragraph("1. يخضع العقد لأنظمة المملكة العربية السعودية.", body_style),
        contract_paragraph(f"2. في حال النزاع يتم حله ودياً، وإن تعذر يحال للمحكمة التجارية ب{company_profile['city']}.", body_style),
        contract_paragraph("3. حرر العقد من نسختين لكل طرف نسخة.", body_style),
        Spacer(1, 10),
        contract_paragraph("والله ولي التوفيق", centered_style),
        Spacer(1, 10),
        contract_paragraph(f"حرر في مدينة {company_profile['city']} بتاريخ: ........ الموافق .... / .... / .... هـ", body_style),
        Spacer(1, 18),
    ])

    signature_table = Table([
        [
            [
                contract_paragraph("الطرف الثاني", section_style, 60 * mm),
                Spacer(1, 10),
                contract_paragraph("الاسم:", body_style, 60 * mm),
                Spacer(1, 18),
                contract_paragraph("التوقيع:", body_style, 60 * mm),
            ],
            [
                contract_paragraph("الطرف الأول", section_style, 60 * mm),
                Spacer(1, 10),
                contract_paragraph(company_profile["name"], body_style, 60 * mm),
                contract_paragraph(f"الاسم: {company_profile['representative']}", body_style, 60 * mm),
                Spacer(1, 10),
                contract_paragraph("التوقيع:", body_style, 60 * mm),
            ],
        ]
    ], colWidths=[80 * mm, 80 * mm], hAlign="CENTER")
    signature_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fcfaf7")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#ddd1c0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ece2d7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.extend([signature_table])

    doc = SimpleDocTemplate(file_path, pagesize=A4, rightMargin=22 * mm, leftMargin=22 * mm, topMargin=24 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=draw_works_contract_pdf_frame, onLaterPages=draw_works_contract_pdf_frame)
    return file_path, file_name


def save_maintenance_image(image: UploadFile | None) -> str:
    if not image or not getattr(image, "filename", ""):
        return ""

    original_name = os.path.basename(str(image.filename or "")).strip()
    if not original_name:
        return ""

    _, ext = os.path.splitext(original_name)
    safe_ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:10]
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.splitext(original_name)[0]).strip("_") or "maintenance"
    unique_name = f"maintenance_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_stem}{safe_ext}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    file_path = os.path.join(UPLOADS_DIR, unique_name)

    image.file.seek(0)
    file_bytes = image.file.read()
    if not file_bytes:
        return ""

    with open(file_path, "wb") as output_file:
        output_file.write(file_bytes)

    return f"/static/uploads/{unique_name}"


def cascade_delete_project_records(project_id: int, company: str = "") -> None:
    conn = get_db()
    try:
        project = conn.execute(
            "SELECT id, contract_id FROM projects WHERE id = ? AND company = ?",
            (project_id, company),
        ).fetchone()
        if not project:
            return

        daily_attachments = conn.execute(
            "SELECT attachment_path FROM project_daily WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        for row in daily_attachments:
            delete_project_daily_attachment_file(row["attachment_path"] or "")

        conn.execute("DELETE FROM project_daily WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project_expenses WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project_equipment WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project_suppliers WHERE project_id = ?", (project_id,))

        contract_id = project["contract_id"]
        quote_id = None
        if contract_id:
            contract = conn.execute(
                "SELECT quote_id FROM contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
            if contract:
                quote_id = contract["quote_id"]
            conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))

        if quote_id:
            conn.execute("DELETE FROM quote_items WHERE quote_id = ?", (quote_id,))
            conn.execute("DELETE FROM quote_payments WHERE quote_id = ?", (quote_id,))
            conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))

        conn.execute("DELETE FROM projects WHERE id = ? AND company = ?", (project_id, company))
        conn.commit()
    finally:
        conn.close()


def parse_project_duration_days(project_row, quote_row) -> int:
    start_date = (project_row["start_date"] or "").strip() if project_row else ""
    end_date = (project_row["end_date"] or "").strip() if project_row else ""

    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            return max((end - start).days, 0)
        except ValueError:
            pass

    duration_text = normalize_arabic_digits((quote_row["duration"] or "").strip() if quote_row else "")
    digits = "".join(ch for ch in duration_text if ch.isdigit())
    return int(digits) if digits else 0


def format_currency(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def build_project_financial_snapshot(conn, project_row):
    contract = None
    quote = None
    items = []

    if project_row["contract_id"]:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE id = ?",
            (project_row["contract_id"],)
        ).fetchone()

    if contract and contract["quote_id"]:
        quote = conn.execute(
            "SELECT * FROM quotes WHERE id = ?",
            (contract["quote_id"],)
        ).fetchone()
        if quote:
            items = conn.execute(
                "SELECT * FROM quote_items WHERE quote_id = ?",
                (quote["id"],)
            ).fetchall()

    expenses = conn.execute(
        "SELECT * FROM project_expenses WHERE project_id = ?",
        (project_row["id"],)
    ).fetchall()

    contract_value = sum(safe_float(item["qty"]) * safe_float(item["unit_price"]) for item in items)
    if contract_value <= 0 and "contract_value" in project_row.keys():
        contract_value = safe_float(project_row["contract_value"])
    total_expenses = sum(safe_float(expense["amount"]) for expense in expenses)
    profit = contract_value - total_expenses
    profit_percentage = (profit / contract_value * 100) if contract_value > 0 else 0.0
    duration_days = parse_project_duration_days(project_row, quote)
    area = safe_float(project_row["area"]) if project_row["area"] not in (None, "") else 0.0
    price_per_m2 = (contract_value / area) if area > 0 else 0.0
    cost_per_m2 = (total_expenses / area) if area > 0 else 0.0
    profit_per_m2 = (profit / area) if area > 0 else 0.0

    return {
        "project_id": project_row["id"],
        "name": project_row["name"] or f"مشروع رقم {project_row['id']}",
        "company": project_row["company"] or "",
        "status": project_row["status"] or "-",
        "project_type": (project_row["project_type"] or "").strip() if "project_type" in project_row.keys() else "",
        "work_type": (project_row["work_type"] or "").strip() if "work_type" in project_row.keys() else "",
        "finish_level": (project_row["finish_level"] or "").strip() if "finish_level" in project_row.keys() else "",
        "area": area,
        "contract_value": contract_value,
        "total_expenses": total_expenses,
        "profit": profit,
        "profit_percentage": profit_percentage,
        "price_per_m2": price_per_m2,
        "cost_per_m2": cost_per_m2,
        "profit_per_m2": profit_per_m2,
        "duration_days": duration_days,
        "duration_text": (quote["duration"] or "") if quote else "",
    }


def is_structured_works_snapshot(snapshot) -> bool:
    return (
        normalize_access_value(snapshot.get("company", "")) == "works"
        and bool(snapshot.get("project_type"))
        and bool(snapshot.get("work_type"))
    )


def is_primary_structured_match(current_snapshot, candidate_snapshot) -> bool:
    return (
        candidate_snapshot["project_type"] == current_snapshot["project_type"]
        and candidate_snapshot["work_type"] == current_snapshot["work_type"]
        and candidate_snapshot["finish_level"] == current_snapshot["finish_level"]
    )


def is_relaxed_structured_match(current_snapshot, candidate_snapshot) -> bool:
    return (
        candidate_snapshot["project_type"] == current_snapshot["project_type"]
        and candidate_snapshot["work_type"] == current_snapshot["work_type"]
    )


def similarity_sort_key(current_snapshot, candidate_snapshot):
    current_area = safe_float(current_snapshot.get("area"))
    candidate_area = safe_float(candidate_snapshot.get("area"))
    current_price_per_m2 = safe_float(current_snapshot.get("price_per_m2"))
    candidate_price_per_m2 = safe_float(candidate_snapshot.get("price_per_m2"))

    area_diff = abs(candidate_area - current_area) if current_area > 0 and candidate_area > 0 else float("inf")
    price_per_m2_diff = (
        abs(candidate_price_per_m2 - current_price_per_m2)
        if current_area > 0 and candidate_area > 0 and current_price_per_m2 > 0 and candidate_price_per_m2 > 0
        else float("inf")
    )
    contract_diff = abs(safe_float(candidate_snapshot.get("contract_value")) - safe_float(current_snapshot.get("contract_value")))

    return (area_diff, price_per_m2_diff, contract_diff, candidate_snapshot.get("project_id", 0))


def rank_similar_project_snapshots(current_snapshot, candidates):
    return sorted(candidates, key=lambda item: similarity_sort_key(current_snapshot, item))


def append_ranked_unique_similar_projects(current_snapshot, existing, candidates):
    existing_ids = {item["project_id"] for item in existing}
    ranked_candidates = rank_similar_project_snapshots(
        current_snapshot,
        [item for item in candidates if item["project_id"] not in existing_ids],
    )
    existing.extend(ranked_candidates)
    return existing


def find_similar_project_snapshots(conn, current_snapshot):
    rows = conn.execute(
        """
        SELECT * FROM projects
        WHERE company = ? AND id != ?
        ORDER BY id DESC
        """,
        (current_snapshot["company"], current_snapshot["project_id"])
    ).fetchall()

    candidate_snapshots = [build_project_financial_snapshot(conn, row) for row in rows]
    similar = []

    if is_structured_works_snapshot(current_snapshot):
        strong_matches = [
            snapshot for snapshot in candidate_snapshots
            if is_primary_structured_match(current_snapshot, snapshot)
        ]
        append_ranked_unique_similar_projects(current_snapshot, similar, strong_matches)

        if len(similar) < 3:
            relaxed_matches = [
                snapshot for snapshot in candidate_snapshots
                if is_relaxed_structured_match(current_snapshot, snapshot)
            ]
            append_ranked_unique_similar_projects(current_snapshot, similar, relaxed_matches)

        if len(similar) < 3:
            append_ranked_unique_similar_projects(current_snapshot, similar, candidate_snapshots)

        return rank_similar_project_snapshots(current_snapshot, similar)

    min_value = current_snapshot["contract_value"] * 0.7
    max_value = current_snapshot["contract_value"] * 1.3
    for row in rows:
        snapshot = build_project_financial_snapshot(conn, row)
        if current_snapshot["contract_value"] <= 0:
            if snapshot["contract_value"] <= 0:
                similar.append(snapshot)
            continue
        if min_value <= snapshot["contract_value"] <= max_value:
            similar.append(snapshot)
    return similar


def generate_local_project_analysis(snapshot, similar_projects, summary):
    profit_percentage = snapshot["profit_percentage"]
    contract_value = snapshot["contract_value"]
    average_profit_percentage = summary["average_profit_percentage"]
    average_price_per_m2 = summary["average_price_per_m2"]
    average_cost_per_m2 = summary["average_cost_per_m2"]
    price_gap = profit_percentage - average_profit_percentage
    current_price_per_m2 = snapshot["price_per_m2"]
    current_cost_per_m2 = snapshot["cost_per_m2"]
    current_profit_per_m2 = snapshot["profit_per_m2"]

    pricing_signal = "طبيعي"
    if average_price_per_m2 > 0 and current_price_per_m2 > 0:
        if current_price_per_m2 <= average_price_per_m2 * 0.9:
            pricing_signal = "منخفض"
        elif current_price_per_m2 >= average_price_per_m2 * 1.1:
            pricing_signal = "مرتفع"

    cost_signal = "طبيعي"
    if average_cost_per_m2 > 0 and current_cost_per_m2 > 0:
        if current_cost_per_m2 >= average_cost_per_m2 * 1.1:
            cost_signal = "مرتفع"
        elif current_cost_per_m2 <= average_cost_per_m2 * 0.9:
            cost_signal = "منخفض"

    if contract_value <= 0:
        success_status = "المشروع لا يمكن تقييمه ماليًا بدقة لأن قيمة العقد غير متوفرة."
        pricing_status = "لا يمكن الحكم على مناسبة السعر قبل تسجيل قيمة العقد."
    elif profit_percentage >= 20:
        success_status = "المشروع يبدو ناجحًا ماليًا بهامش ربح جيد."
        pricing_status = "السعر يبدو مناسبًا ويدعم ربحية مريحة."
    elif profit_percentage >= 8:
        success_status = "المشروع مقبول ونتيجته المالية مستقرة حتى الآن."
        pricing_status = "السعر قريب من المناسب، لكن يحتاج ضبط أفضل للمصروفات."
    elif profit_percentage >= 0:
        success_status = "المشروع على الحد الأدنى من النجاح وربحيته ضعيفة."
        pricing_status = "السعر منخفض نسبيًا مقارنة بالمصروفات الحالية."
    else:
        success_status = "المشروع غير ناجح حاليًا لأن المصروفات تجاوزت قيمة الربح المتوقع."
        pricing_status = "السعر غير مناسب على الأرجح أو أن التنفيذ أعلى تكلفة من المتوقع."

    if similar_projects:
        if cost_signal == "مرتفع" and pricing_signal == "منخفض":
            comparison_status = "المقارنة تشير إلى مشكلة مزدوجة: تكلفة التنفيذ لكل متر مرتفعة مع تسعير أقل من المشاريع المشابهة."
        elif cost_signal == "مرتفع":
            comparison_status = "المقارنة تشير إلى أن المشكلة الأساسية هي ارتفاع التكلفة لكل متر مربع مقارنة بالمشاريع المشابهة."
        elif pricing_signal == "منخفض":
            comparison_status = "المقارنة تشير إلى أن المشكلة الأساسية هي انخفاض التسعير لكل متر مربع مقارنة بالمشاريع المشابهة."
        elif price_gap >= 5:
            comparison_status = "أداء المشروع أفضل من متوسط المشاريع المشابهة في نفس الشركة."
        elif price_gap <= -5:
            comparison_status = "أداء المشروع أقل من متوسط المشاريع المشابهة ويحتاج تدخلًا سريعًا."
        else:
            comparison_status = "أداء المشروع قريب من متوسط المشاريع المشابهة مع مؤشرات تشغيلية طبيعية."
    else:
        comparison_status = "لا توجد مشاريع مشابهة كافية للمقارنة الدقيقة، لذلك التقييم مبني على بيانات المشروع الحالية فقط."

    if similar_projects and average_price_per_m2 > 0 and average_cost_per_m2 > 0:
        pricing_status += (
            f" متوسط سعر البيع للمتر في المشاريع المشابهة هو {format_currency(average_price_per_m2)} ريال"
            f" مقابل {format_currency(current_price_per_m2)} ريال لهذا المشروع،"
            f" ومتوسط تكلفة المتر {format_currency(average_cost_per_m2)} ريال"
            f" مقابل {format_currency(current_cost_per_m2)} ريال."
        )

    recommendations = []
    if snapshot["total_expenses"] > snapshot["contract_value"] * 0.8 and snapshot["contract_value"] > 0:
        recommendations.append("مراجعة بنود المصروفات الأعلى تكلفة فورًا قبل أي التزام إضافي.")
    if cost_signal == "مرتفع":
        recommendations.append("تحليل تكلفة التنفيذ لكل متر مربع لتحديد البنود التي رفعت تكلفة المشروع عن المشاريع المشابهة.")
    if pricing_signal == "منخفض":
        recommendations.append("مراجعة تسعير المتر المربع لأن السعر الحالي أقل من متوسط المشاريع المشابهة.")
    if profit_percentage < average_profit_percentage and similar_projects:
        recommendations.append("مقارنة تسعير المشروع الحالي بأفضل مشروع مشابه لاستخراج فرق الهامش.")
    if snapshot["duration_days"] > 0 and snapshot["duration_days"] >= 180:
        recommendations.append("مراقبة مدة التنفيذ لأن طول المشروع قد يرفع المصروفات غير المباشرة.")
    if not recommendations:
        recommendations.append("الاستمرار في متابعة المصروفات أسبوعيًا للحفاظ على الهامش الحالي.")
        recommendations.append("الاستفادة من نمط التسعير الحالي في المشاريع القادمة المشابهة.")

    metric_summary = []
    if current_price_per_m2 > 0:
        metric_summary.append(f"سعر المتر: {format_currency(current_price_per_m2)} ريال")
    if current_cost_per_m2 > 0:
        metric_summary.append(f"تكلفة المتر: {format_currency(current_cost_per_m2)} ريال")
    if current_profit_per_m2 != 0:
        metric_summary.append(f"ربح المتر: {format_currency(current_profit_per_m2)} ريال")

    lines = [
        f"هل المشروع ناجح أو لا: {success_status}",
        f"هل السعر مناسب: {pricing_status}",
        f"مقارنة مع مشاريع سابقة: {comparison_status}",
    ]
    if metric_summary:
        lines.append(f"مؤشرات المتر المربع: {' | '.join(metric_summary)}")
    lines.extend([
        "توصيات بسيطة:",
        *[f"- {item}" for item in recommendations[:3]],
    ])

    return "\n".join(lines)


def generate_project_analysis_text(snapshot, similar_projects, summary):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return generate_local_project_analysis(snapshot, similar_projects, summary)

    payload = {
        "model": os.getenv("OPENAI_PROJECT_ANALYSIS_MODEL", "gpt-4.1-mini"),
        "input": [
            {
                "role": "system",
                "content": (
                    "أنت محلل مشاريع إنشائية. أجب بالعربية فقط وبأسلوب واضح ومختصر. "
                    "يجب أن تغطي: هل المشروع ناجح أو لا، هل السعر مناسب، مقارنة مع مشاريع سابقة، وتوصيات بسيطة."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_project": snapshot,
                        "similar_projects_count": len(similar_projects),
                        "summary": summary,
                        "similar_projects": similar_projects[:8],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    try:
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = (body.get("output_text") or "").strip()
        if text:
            return text
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass

    return generate_local_project_analysis(snapshot, similar_projects, summary)


def render_project_analysis_block(snapshot, similar_projects, summary, analysis_text):
    best_project = summary["best_project"]
    worst_project = summary["worst_project"]

    def project_label(project_item, empty_label):
        if not project_item:
            return empty_label
        return (
            f"{escape(project_item['name'])} "
            f"(ربح {format_currency(project_item['profit'])} ريال | "
            f"{project_item['profit_percentage']:.1f}%)"
        )

    analysis_html = "<br>".join(
        escape(line).replace("- ", "&bull; ")
        for line in analysis_text.splitlines()
        if line.strip()
    )

    return f"""
    <div class="inventory-note" style="margin:22px 0;padding:22px;border-radius:18px;background:rgba(15,23,42,0.78);border:1px solid rgba(245,158,11,0.28);text-align:right;line-height:1.9;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px;">
            <h3 style="margin:0;color:#f8fafc;">تحليل المشروع بالذكاء</h3>
            <span style="color:#fbbf24;font-size:14px;">عدد المشاريع المشابهة: {len(similar_projects)}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px;">
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>قيمة العقد</strong><br>{format_currency(snapshot['contract_value'])} ريال</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>إجمالي المصروفات</strong><br>{format_currency(snapshot['total_expenses'])} ريال</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>الربح</strong><br>{format_currency(snapshot['profit'])} ريال</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>نسبة الربح</strong><br>{snapshot['profit_percentage']:.1f}%</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>مدة المشروع</strong><br>{snapshot['duration_days']} يوم</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>متوسط ربح المشاريع المشابهة</strong><br>{format_currency(summary['average_profit'])} ريال | {summary['average_profit_percentage']:.1f}%</div>
        </div>
        <div style="margin-bottom:16px;">
            <strong>أفضل مشروع مشابه:</strong> {project_label(best_project, "لا يوجد")}<br>
            <strong>أضعف مشروع مشابه:</strong> {project_label(worst_project, "لا يوجد")}
        </div>
        <div style="background:rgba(255,255,255,0.05);padding:16px;border-radius:14px;color:#e5e7eb;">
            {analysis_html}
        </div>
    </div>
    """


def normalize_expense_item_name(name: str) -> str:
    raw_name = (name or "").strip()
    if not raw_name:
        return "بنود أخرى"

    normalized = normalize_arabic_digits(raw_name).lower()
    for source, target in (
        ("أ", "ا"),
        ("إ", "ا"),
        ("آ", "ا"),
        ("ة", "ه"),
        ("ى", "ي"),
        ("ؤ", "و"),
        ("ئ", "ي"),
    ):
        normalized = normalized.replace(source, target)

    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\b(شراء|توريد|مورد|ماده|مواد|بند|اعمال|مصروف|مصروفات|تكلفه|تكلفة|ال)\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    grouped_keywords = [
        ("حديد", ["حديد", "تسليح"]),
        ("اسمنت", ["اسمنت", "اسمنتيه"]),
        ("خرسانة", ["خرسانه", "خرسانة", "كونكريت", "صب"]),
        ("بلك", ["بلك", "بلوك", "طابوق"]),
        ("رمل", ["رمل"]),
        ("بحص", ["بحص"]),
        ("دهان", ["دهان", "بويه", "بوية"]),
        ("كهرباء", ["كهرباء", "كيابل", "كيبل", "اسلاك", "اسلاك", "مفاتيح"]),
        ("سباكة", ["سباكه", "سباكة", "مواسير", "انابيب", "تمديد"]),
        ("جبس", ["جبس", "جبسيه", "جبسية"]),
        ("المنيوم", ["المنيوم", "الومنيوم", "المنيوم"]),
        ("خشب", ["خشب", "نجاره", "نجارة"]),
        ("عزل", ["عزل"]),
        ("سيراميك", ["سيراميك", "بورسلان", "بلاط"]),
        ("ابواب", ["ابواب", "باب"]),
        ("نوافذ", ["نوافذ", "شبابيك", "شباك"]),
        ("تكييف", ["تكييف", "مكيف", "مكيفات"]),
    ]
    for canonical, keywords in grouped_keywords:
        if any(keyword in normalized for keyword in keywords):
            return canonical

    tokens = [token for token in normalized.split() if len(token) > 1]
    if not tokens:
        return "بنود أخرى"
    return " ".join(tokens[:2])


def build_project_expense_item_analysis(expenses):
    grouped = {}
    total_expenses = 0.0

    for expense in expenses:
        amount = safe_float(expense["amount"])
        if amount <= 0:
            continue
        total_expenses += amount
        raw_title = (expense["title"] or "").strip()
        normalized_name = normalize_expense_item_name(raw_title)

        if normalized_name not in grouped:
            grouped[normalized_name] = {
                "normalized_name": normalized_name,
                "display_name": raw_title or normalized_name,
                "total_amount": 0.0,
                "count": 0,
                "latest_date": "",
                "titles": set(),
            }

        item = grouped[normalized_name]
        item["total_amount"] += amount
        item["count"] += 1
        if raw_title:
            item["titles"].add(raw_title)
        expense_date = (expense["date"] or "").strip()
        if expense_date and expense_date > item["latest_date"]:
            item["latest_date"] = expense_date

    grouped_items = sorted(
        grouped.values(),
        key=lambda item: (-item["total_amount"], item["normalized_name"])
    )

    for item in grouped_items:
        item["percentage"] = (item["total_amount"] / total_expenses * 100) if total_expenses > 0 else 0.0
        item["titles"] = sorted(item["titles"])

    top_items = grouped_items[:5]
    review_item = top_items[0] if top_items else None

    if not grouped_items:
        summary_text = "لا توجد مصروفات كافية لتحليل البنود داخل هذا المشروع حتى الآن."
    elif len(grouped_items) == 1:
        summary_text = "مصروفات المشروع مركزة بالكامل تقريبًا في بند واحد، لذلك يلزم التأكد من دقة تسجيل هذا البند ومراجعته دوريًا."
    else:
        top_share = sum(item["percentage"] for item in top_items[:3])
        summary_text = (
            f"المصروفات موزعة على {len(grouped_items)} بندًا رئيسيًا، "
            f"وأعلى 3 بنود تمثل نحو {top_share:.1f}% من إجمالي تكلفة المشروع."
        )

    return {
        "total_expenses": total_expenses,
        "grouped_items": grouped_items,
        "top_items": top_items,
        "review_item": review_item,
        "summary_text": summary_text,
    }


def render_project_expense_item_analysis_block(project, analysis):
    grouped_items = analysis["grouped_items"]
    rows = ""
    for item in grouped_items:
        latest_date_html = f"<br><span style='color:#94a3b8;font-size:12px;'>آخر تاريخ: {escape(item['latest_date'])}</span>" if item["latest_date"] else ""
        rows += f"""
        <tr>
            <td>{escape(item['normalized_name'])}</td>
            <td>{format_currency(item['total_amount'])} ريال</td>
            <td>{item['percentage']:.1f}%</td>
            <td>{item['count']}</td>
            <td>{escape('، '.join(item['titles'][:3])) if item['titles'] else '-' }{latest_date_html}</td>
        </tr>
        """

    top_items_html = "<br>".join(
        f"{index + 1}. {escape(item['normalized_name'])}: {format_currency(item['total_amount'])} ريال ({item['percentage']:.1f}%)"
        for index, item in enumerate(analysis["top_items"])
    ) or "لا توجد بنود متكررة حتى الآن."

    review_item = analysis["review_item"]
    review_html = (
        f"{escape(review_item['normalized_name'])} لأنه يمثل {review_item['percentage']:.1f}% من إجمالي المصروفات"
        f" بقيمة {format_currency(review_item['total_amount'])} ريال."
        if review_item else
        "لا يوجد بند يحتاج مراجعة حاليًا لعدم توفر مصروفات كافية."
    )

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">
    <h1>تحليل البنود</h1>
    <p>المشروع: {escape(project['name'] or f"مشروع رقم {project['id']}")}</p>

    <div class="inventory-note" style="margin:22px 0;padding:22px;border-radius:18px;background:rgba(15,23,42,0.78);border:1px solid rgba(245,158,11,0.28);text-align:right;line-height:1.9;">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px;">
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>إجمالي المصروفات</strong><br>{format_currency(analysis['total_expenses'])} ريال</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>عدد البنود المجمعة</strong><br>{len(grouped_items)}</div>
            <div style="background:rgba(255,255,255,0.04);padding:14px;border-radius:14px;"><strong>أكثر بند يحتاج مراجعة</strong><br>{escape(review_item['normalized_name']) if review_item else 'لا يوجد'}</div>
        </div>

        <div style="margin-bottom:16px;">
            <strong>أعلى البنود تكلفة:</strong><br>
            {top_items_html}
        </div>

        <div style="margin-bottom:16px;">
            <strong>ملخص توزيع المصروفات:</strong><br>
            {escape(analysis['summary_text'])}
        </div>

        <div style="margin-bottom:16px;">
            <strong>أكثر بند يحتاج مراجعة:</strong><br>
            {review_html}
        </div>

        <table border="1" style="width:100%;text-align:center;background:rgba(255,255,255,0.03);">
            <tr>
                <th>البند</th>
                <th>المجموع</th>
                <th>النسبة من الإجمالي</th>
                <th>عدد التكرارات</th>
                <th>أسماء السجلات</th>
            </tr>
            {rows if rows else "<tr><td colspan='5'>لا توجد مصروفات مسجلة لهذا المشروع</td></tr>"}
        </table>
    </div>

    <a href="/project/{project['id']}?company=works" class="glass-btn back-btn">⬅ رجوع للمشروع</a>
</div>
"""


def is_works_company(company: str) -> bool:
    return normalize_access_value(company) == "works"


def is_works_expenses_only_user(user, company: str) -> bool:
    if not user or not is_employee(user) or not is_works_company(company):
        return False
    return get_employee_allowed_sections(user["id"], company) == {"expenses"}


def is_works_expenses_suppliers_employee(user, company: str) -> bool:
    return is_works_expenses_only_user(user, company)


def is_works_daily_log_only_employee(user, company: str) -> bool:
    if not user or not is_employee(user) or not is_works_company(company):
        return False
    return get_employee_allowed_sections(user["id"], company) == {"daily_log"}


def is_works_partner_user(user, company: str) -> bool:
    if not user or not is_partner(user) or not is_works_company(company):
        return False
    return user_has_company_access(user["id"], "works")


def get_realestate_employee_sections(user) -> set[str]:
    if not user or not is_employee(user):
        return set()
    return get_employee_allowed_sections(user["id"], "realestate")


def is_realestate_property_accounts_employee(user) -> bool:
    sections = get_realestate_employee_sections(user)
    return bool({"property_accounts", "expenses"} & sections)


def is_realestate_maintenance_employee(user) -> bool:
    sections = get_realestate_employee_sections(user)
    return "maintenance" in sections and not bool({"property_accounts", "expenses"} & sections)


def get_realestate_landing_url(user) -> str:
    if is_realestate_maintenance_employee(user):
        return "/maintenance-management"
    if is_realestate_property_accounts_employee(user):
        return "/property-management"
    return "/company/realestate"


def ensure_realestate_property_management_access(request: Request, property_id: int = 0):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if property_id:
        access_result = ensure_property_access(request, property_id)
    else:
        access_result = ensure_company_access(request, "realestate")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_employee(access_result) and is_realestate_maintenance_employee(access_result):
        return access_denied_response(
            "ليس لديك صلاحية الوصول إلى هذا القسم",
            back_url="/maintenance-management",
        )
    return access_result


def ensure_realestate_maintenance_access(request: Request, property_id: int = 0):
    if property_id:
        access_result = ensure_property_access(request, property_id)
    else:
        access_result = ensure_company_access(request, "realestate", "maintenance")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_owner(access_result):
        return access_denied_response(
            "هذه الصفحة مخصصة لإدارة الصيانة فقط",
            back_url="/property-management",
        )
    if is_employee(access_result) and not is_realestate_maintenance_employee(access_result):
        return access_denied_response(
            "ليس لديك صلاحية الوصول إلى هذا القسم",
            back_url="/property-management",
        )
    return access_result


def ensure_realestate_supervisors_access(request: Request, property_id: int = 0):
    access_result = ensure_realestate_property_management_access(request, property_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_owner(access_result):
        return access_denied_response(
            "هذه الصفحة غير متاحة للمالك",
            back_url=f"/property-management/{property_id}" if property_id else "/property-management",
        )
    if is_employee(access_result):
        return access_denied_response(
            "ليس لديك صلاحية الوصول إلى بيانات المشرف",
            back_url=f"/property-management/{property_id}" if property_id else "/property-management",
        )
    return access_result


def ensure_realestate_write_access(request: Request, property_id: int = 0, area: str = "property", back_url: str = "/property-management"):
    access_result = ensure_property_access(request, property_id) if property_id else ensure_company_access(request, "realestate")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_owner(access_result):
        return access_denied_response(
            "صلاحية المالك للعرض فقط",
            back_url=back_url,
        )
    if is_employee(access_result):
        if area == "maintenance":
            if not is_realestate_maintenance_employee(access_result):
                return access_denied_response(
                    "ليس لديك صلاحية تنفيذ هذا الإجراء",
                    back_url=back_url,
                )
            return access_result
        if is_realestate_maintenance_employee(access_result):
            return access_denied_response(
                "موظف الصيانة يصل إلى صفحات الصيانة فقط",
                back_url="/maintenance-management",
            )
    return access_result


def realestate_owner_read_only(user) -> bool:
    return bool(user) and is_owner(user)


def get_owner_accessible_property_set(user) -> set[int]:
    if not realestate_owner_read_only(user):
        return set()
    return set(get_accessible_property_ids(user["id"]))


def filter_rows_by_property_scope(rows, property_ids: set[int], field_name: str = "property_id"):
    if not property_ids:
        return rows
    return [row for row in rows if row[field_name] in property_ids]


def ensure_realestate_owner_dashboard_only(access_result, property_id: int = 0):
    if realestate_owner_read_only(access_result):
        return access_denied_response(
            "تم دمج هذه الصفحة داخل لوحة المالك الموحدة",
            back_url=f"/property-management/{property_id}" if property_id else "/property-management",
        )
    return access_result


CONTRACT_ATTACHMENT_SOURCE_WORKS = "works_contract"
CONTRACT_ATTACHMENT_SOURCE_PROPERTY = "property_contract"
CONTRACT_ATTACHMENT_SOURCE_INVESTMENT = "investment_contract"
CONTRACT_RECORD_SOURCE_QUOTE = "quote_conversion"
CONTRACT_RECORD_SOURCE_MANUAL_PROJECT = "manual_project"
CONTRACT_ATTACHMENT_UPLOAD_DIR = os.path.join("static", "uploads", "contracts")


def get_contract_record_source(contract_row: sqlite3.Row | None) -> str:
    if not contract_row:
        return CONTRACT_RECORD_SOURCE_QUOTE
    if "source_type" in contract_row.keys():
        return (contract_row["source_type"] or "").strip() or CONTRACT_RECORD_SOURCE_QUOTE
    return CONTRACT_RECORD_SOURCE_QUOTE


def get_contract_attachment_back_url(source_type: str, company: str = "", property_id: int = 0, project_id: int = 0) -> str:
    if source_type == CONTRACT_ATTACHMENT_SOURCE_WORKS:
        return f"/contracts?company={quote(str(company or ''))}"
    if source_type == CONTRACT_ATTACHMENT_SOURCE_PROPERTY:
        return f"/property-rental-contracts?property_id={property_id}"
    if source_type == CONTRACT_ATTACHMENT_SOURCE_INVESTMENT:
        return f"/investment-contracts?project_id={project_id}"
    return "/"


def get_contract_attachment_rows(conn, source_type: str, contract_ids) -> dict[int, list[sqlite3.Row]]:
    if not contract_ids:
        return {}
    unique_ids = sorted({int(contract_id) for contract_id in contract_ids if contract_id})
    if not unique_ids:
        return {}
    placeholders = ", ".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM contract_attachments
        WHERE source_type = ? AND contract_id IN ({placeholders})
        ORDER BY id DESC
        """,
        [source_type, *unique_ids],
    ).fetchall()
    attachments_map: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        attachments_map.setdefault(row["contract_id"], []).append(row)
    return attachments_map


def render_contract_attachments_html(
    attachments,
    source_type: str,
    contract_id: int,
    is_admin_user: bool = False,
    company: str = "",
    property_id: int = 0,
    project_id: int = 0,
) -> str:
    link_items = ""
    for attachment in attachments or []:
        safe_name = escape(attachment["file_name"] or "ملف مرفق")
        link_items += (
            f'<div><span>{safe_name}</span> '
            f'<a href="/contract-attachments/{attachment["id"]}" class="action-btn">عرض / تنزيل</a></div>'
        )

    if not link_items:
        link_items = '<div class="inventory-note" style="margin:0;">لا توجد مرفقات</div>'

    upload_form = ""
    if is_admin_user:
        upload_form = f"""
        <form action="/upload-contract-attachment" method="post" enctype="multipart/form-data" style="margin-top:10px;">
            <input type="hidden" name="source_type" value="{source_type}">
            <input type="hidden" name="contract_id" value="{contract_id}">
            <input type="hidden" name="company" value="{escape(company)}">
            <input type="hidden" name="property_id" value="{property_id}">
            <input type="hidden" name="project_id" value="{project_id}">
            <input type="file" name="attachment_file" required>
            <button type="submit" class="action-btn">إضافة مرفق</button>
        </form>
        """

    return f'<div style="display:flex;flex-direction:column;gap:8px;">{link_items}{upload_form}</div>'


def get_contract_attachment_context(conn, source_type: str, contract_id: int):
    if source_type == CONTRACT_ATTACHMENT_SOURCE_WORKS:
        contract = conn.execute(
            "SELECT id, company FROM contracts WHERE id = ?",
            (contract_id,),
        ).fetchone()
        if not contract:
            return None
        return {
            "source_type": source_type,
            "contract_id": contract_id,
            "company": contract["company"] or "",
            "property_id": 0,
            "project_id": 0,
        }
    if source_type == CONTRACT_ATTACHMENT_SOURCE_PROPERTY:
        contract = conn.execute(
            "SELECT id, property_id FROM property_rent_contracts WHERE id = ?",
            (contract_id,),
        ).fetchone()
        if not contract:
            return None
        return {
            "source_type": source_type,
            "contract_id": contract_id,
            "company": "",
            "property_id": contract["property_id"] or 0,
            "project_id": 0,
        }
    if source_type == CONTRACT_ATTACHMENT_SOURCE_INVESTMENT:
        contract = conn.execute(
            """
            SELECT investment_contracts.id, investment_units.project_id
            FROM investment_contracts
            JOIN investment_units ON investment_units.id = investment_contracts.unit_id
            WHERE investment_contracts.id = ?
            """,
            (contract_id,),
        ).fetchone()
        if not contract:
            return None
        return {
            "source_type": source_type,
            "contract_id": contract_id,
            "company": "",
            "property_id": 0,
            "project_id": contract["project_id"] or 0,
        }
    return None


def ensure_contract_attachment_access(
    request: Request,
    source_type: str,
    contract_id: int,
    require_admin: bool = False,
):
    conn = get_db()
    try:
        context = get_contract_attachment_context(conn, source_type, contract_id)
    finally:
        conn.close()

    if not context:
        return None, HTMLResponse("<h2>العقد غير موجود</h2>", status_code=404)

    if source_type == CONTRACT_ATTACHMENT_SOURCE_WORKS:
        access_result = ensure_company_access(request, context["company"])
    elif source_type == CONTRACT_ATTACHMENT_SOURCE_PROPERTY:
        access_result = ensure_realestate_property_management_access(request, context["property_id"])
    elif source_type == CONTRACT_ATTACHMENT_SOURCE_INVESTMENT:
        access_result = ensure_investment_project_access(request, context["project_id"])
    else:
        return context, HTMLResponse("<h2>نوع المرفق غير مدعوم</h2>", status_code=400)

    if not isinstance(access_result, sqlite3.Row):
        return context, access_result

    if require_admin and not is_admin(access_result):
        return context, access_denied_response(
            "رفع المرفقات متاح للمشرف فقط",
            back_url=get_contract_attachment_back_url(
                source_type,
                company=context["company"],
                property_id=context["property_id"],
                project_id=context["project_id"],
            ),
        )

    return context, access_result


def get_works_expenses_landing_url() -> str:
    return "/projects?company=works"


def deny_works_expenses_summary_access():
    return access_denied_response(
        "ليس لديك صلاحية الوصول إلى هذه الصفحة",
        back_url=get_works_expenses_landing_url(),
    )


def ensure_works_expenses_suppliers_scope(user, project_id: int, company: str):
    if not is_works_expenses_suppliers_employee(user, company):
        return user
    if not project_id:
        return RedirectResponse(url=get_works_expenses_landing_url(), status_code=303)
    return RedirectResponse(
        url=f"/project-expenses?project_id={project_id}&company={company}",
        status_code=303,
    )


def ensure_works_daily_log_scope(user, project_id: int, company: str):
    if not is_works_daily_log_only_employee(user, company):
        return user
    if not project_id:
        return RedirectResponse(url="/projects?company=works", status_code=303)
    return RedirectResponse(
        url=f"/project-daily?project_id={project_id}&company={company}",
        status_code=303,
    )


def ensure_not_works_partner_write(user, company: str):
    if is_works_partner_user(user, company):
        return access_denied_response(
            "صلاحية الشريك في المقاولات للعرض فقط",
            back_url=f"/company/{company}",
        )
    return user



def is_active_projects_project_manager(user) -> bool:
    return bool(user) and is_project_manager(user) and user_has_company_access(user["id"], "realestate", "active_projects")


def get_assigned_investment_project_ids(user_id: int) -> list[int]:
    if not user_id:
        return []
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT project_id
            FROM user_investment_project_access
            WHERE user_id = ?
            ORDER BY project_id ASC
            """,
            (user_id,),
        ).fetchall()
        return [row["project_id"] for row in rows]
    finally:
        conn.close()


def get_investment_project_manager_options():
    conn = get_db()
    try:
        return conn.execute(
            """
            SELECT DISTINCT users.id, COALESCE(NULLIF(TRIM(users.full_name), ''), users.username) AS display_name
            FROM users
            LEFT JOIN user_company_access access ON access.user_id = users.id
            WHERE users.role = 'project_manager'
              AND users.is_active = 1
              AND LOWER(TRIM(COALESCE(access.company, ''))) = 'realestate'
              AND LOWER(TRIM(COALESCE(access.section, ''))) = 'active_projects'
            ORDER BY display_name COLLATE NOCASE ASC, users.id ASC
            """
        ).fetchall()
    finally:
        conn.close()


def build_investment_project_manager_select(selected_user_id: int | None = None) -> str:
    options = ['<option value="">بدون تعيين</option>']
    for user in get_investment_project_manager_options():
        display_name = escape(user["display_name"] or f"مستخدم #{user['id']}")
        selected_attr = " selected" if selected_user_id == user["id"] else ""
        options.append(f'<option value="{user["id"]}"{selected_attr}>{display_name}</option>')
    return "".join(options)


def get_investment_projects_for_user(user):
    conn = get_db()
    try:
        if is_active_projects_project_manager(user):
            project_ids = get_assigned_investment_project_ids(user["id"])
            if not project_ids:
                return []
            placeholders = ", ".join("?" for _ in project_ids)
            return conn.execute(
                f"SELECT * FROM investment_projects WHERE id IN ({placeholders}) ORDER BY id DESC",
                project_ids,
            ).fetchall()
        return conn.execute("SELECT * FROM investment_projects ORDER BY id DESC").fetchall()
    finally:
        conn.close()


def ensure_investment_project_access(request: Request, project_id: int, back_url: str = "/investment-projects"):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not project_id:
        return RedirectResponse(url=back_url, status_code=303)
    if is_active_projects_project_manager(user) and project_id not in set(get_assigned_investment_project_ids(user["id"])):
        return access_denied_response("ليس لديك صلاحية الوصول إلى هذا المشروع", back_url=back_url)
    return user


def ensure_investment_project_management_access(request: Request, back_url: str = "/investment-projects"):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if is_active_projects_project_manager(user):
        return access_denied_response("مدير المشروع لا يمكنه إنشاء أو تعديل هيكل المشاريع", back_url=back_url)
    return user

def get_role_landing_url(user) -> str:
    if not user:
        return "/login"

    if is_admin(user):
        return "/portal"

    if is_tenant(user):
        return "/client-maintenance"

    if is_owner(user):
        property_ids = get_accessible_property_ids(user["id"])
        if len(property_ids) == 1:
            return f"/property-management/{property_ids[0]}"
        return "/property-management"

    if is_employee(user):
        access_rows = get_user_company_access_rows(user["id"])
        fallback_company = ""
        for row in access_rows:
            company = normalize_access_value(row["company"] or "")
            section = normalize_access_value(row["section"] or "")
            if company and not section and not fallback_company:
                fallback_company = company
            if section == "property_accounts" and company in {"realestate", "all"}:
                return "/property-management"
            if section == "maintenance" and company in {"realestate", "all"}:
                return "/maintenance-management"
            if section == "daily_log" and company in {"works", "all"}:
                return "/projects?company=works"
            if section == "expenses" and company in {"works", "all"}:
                return "/projects?company=works"
        if fallback_company:
            if fallback_company == "realestate":
                return get_realestate_landing_url(user)
            return f"/company/{fallback_company}"

    if is_active_projects_project_manager(user):
        project_ids = get_assigned_investment_project_ids(user["id"])
        if len(project_ids) == 1:
            return f"/investment-project/{project_ids[0]}"
        return "/investment-projects"

    if is_partner(user) and user_has_company_access(user["id"], "works"):
        return "/company/works"

    return "/portal"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if user:
        return RedirectResponse(url=get_role_landing_url(user), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": "",
            "username": "",
        },
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    clean_username = (username or "").strip()
    clean_password = (password or "").strip()

    if not clean_username or not clean_password:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "يرجى تعبئة اسم المستخدم وكلمة المرور",
                "username": clean_username,
            },
            status_code=400,
        )

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (clean_username,),
        ).fetchone()
    finally:
        conn.close()

    if not user or user["role"] not in AUTH_ROLES or not password_matches(user["password"] or "", clean_password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "بيانات الدخول غير صحيحة أو أن الحساب غير مفعل",
                "username": clean_username,
            },
            status_code=401,
        )

    session_data = request.scope.get("session")
    if not isinstance(session_data, dict):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "تعذر تهيئة الجلسة الحالية، حاول مرة أخرى",
                "username": clean_username,
            },
            status_code=500,
        )

    session_data["user_id"] = user["id"]
    session_data["username"] = user["username"]
    session_data["role"] = user["role"]
    session_data["full_name"] = user["full_name"] or ""

    return RedirectResponse(url=get_role_landing_url(user), status_code=303)


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    try:
        path = request.url.path or "/"

        if path.startswith("/static") or path in {"/login", "/logout"}:
            return await call_next(request)

        request.state.current_user = None
        session_available = isinstance(request.scope.get("session"), dict)
        if session_available:
            request.state.current_user = get_current_user(request)

        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in PROTECTED_ROUTE_PREFIXES):
            if session_available and not request.state.current_user:
                return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)


@app.get("/logout")
def logout(request: Request):
    session_data = request.scope.get("session")
    if isinstance(session_data, dict):
        session_data.clear()
    return RedirectResponse(url="/login", status_code=303)


def render_internal_portal(user) -> HTMLResponse:
    admin_users_button = ""
    if is_admin(user):
        admin_users_button = '<div style="margin:20px 0 28px;"><a href="/admin/users" class="glass-btn gold-text">تسجيل مستخدم جديد</a></div>'
    return HTMLResponse(
        content=f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    
<h1 class="system-title">Urban Rise AI</h1>
    <p>اختر الشركة للدخول إلى نظامها</p>
    {admin_users_button}
    <div class="companies">
        <a href="/company/works" class="company-card works">
            <h2>Urban Rise Works</h2>
            <p>المقاولات</p>
        </a>
        <a href="/company/realestate" class="company-card realestate">
            <h2>Urban Rise</h2>
            <p>التطوير والاستثمار العقاري</p>
        </a>
        <a href="/company/logistics" class="company-card logistics">
            <h2>Urban Rise Logistics</h2>
            <p>اللوجستيات</p>
        </a>
    </div>
</div>
""",
        media_type="text/html; charset=utf-8",
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if user:
        landing_url = get_role_landing_url(user)
        return RedirectResponse(url=landing_url, status_code=303)
    return templates.TemplateResponse(
        request,
        "public_home.html",
        {
            "request": request,
        },
    )


@app.get("/portal", response_class=HTMLResponse)
def internal_portal(request: Request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return render_internal_portal(user)


def inventory_company_label(company: str) -> str:
    labels = {
        "works": "المقاولات",
        "realestate": "التطوير العقاري",
        "logistics": "اللوجستيات",
    }
    return labels.get(company, company or "المستودع المركزي")


# ======================
# المستودع المركزي
# ======================

@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, project_id: int = 0, item_id: int = 0, message: str = "", error: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_section_access(request, "all", "inventory")
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    elif not is_admin(user):
        has_inventory_scope = any(
            user_has_company_access(user["id"], company)
            for company in ("works", "realestate", "logistics")
        )
        if not has_inventory_scope:
            return access_denied_response()
    conn = get_db()

    inventory_items = conn.execute(
        "SELECT * FROM inventory ORDER BY item_name COLLATE NOCASE ASC"
    ).fetchall()
    projects = conn.execute(
        "SELECT id, name, company FROM projects ORDER BY id DESC"
    ).fetchall()
    recent_transactions = conn.execute(
        """
        SELECT
            inventory_transactions.*,
            inventory.item_name,
            inventory.unit,
            projects.name AS project_name
        FROM inventory_transactions
        LEFT JOIN inventory ON inventory.id = inventory_transactions.item_id
        LEFT JOIN projects ON projects.id = inventory_transactions.project_id
        ORDER BY inventory_transactions.id DESC
        LIMIT 12
        """
    ).fetchall()
    latest_withdrawals = conn.execute(
        """
        SELECT tx.item_id, tx.employee_name, tx.date
        FROM inventory_transactions tx
        INNER JOIN (
            SELECT item_id, MAX(id) AS max_id
            FROM inventory_transactions
            WHERE type = 'out'
            GROUP BY item_id
        ) latest ON latest.max_id = tx.id
        """
    ).fetchall()
    linked_project = None
    if project_id:
        linked_project = conn.execute(
            "SELECT id, name, company FROM projects WHERE id = ?",
            (project_id,)
        ).fetchone()
    conn.close()

    if project_id and not linked_project:
        error = "project_missing"
        project_id = 0

    message_map = {
        "stock_added": "تمت إضافة المخزون بنجاح",
        "stock_withdrawn": "تم تسجيل السحب بنجاح",
    }
    error_map = {
        "project_missing": "المشروع المحدد غير موجود",
        "item_missing": "الصنف المحدد غير موجود",
        "quantity_invalid": "يجب إدخال كمية أكبر من صفر",
        "quantity_unavailable": "الكمية المطلوبة غير متوفرة في المستودع",
    }

    selected_item_id = item_id
    if not selected_item_id and inventory_items:
        selected_item_id = inventory_items[0]["id"]

    latest_withdrawal_map = {row["item_id"]: row for row in latest_withdrawals}
    item_rows = ""
    for item in inventory_items:
        is_low_stock = item["quantity"] <= item["min_quantity"]
        row_class = ' class="inventory-row-low"' if is_low_stock else ""
        min_badge = '<span class="inventory-alert">تنبيه: الكمية منخفضة</span>' if is_low_stock else '<span class="inventory-status-ok">مستقر</span>'
        last_withdrawal = latest_withdrawal_map.get(item["id"])
        last_withdrawal_text = "-"
        if last_withdrawal:
            employee_name = last_withdrawal["employee_name"] or "غير محدد"
            last_withdrawal_text = f"{employee_name}<br><small>{last_withdrawal['date'] or '-'}</small>"
        item_rows += f"""
        <tr{row_class}>
            <td>{item['item_name']}</td>
            <td>{item['unit'] or '-'}</td>
            <td>{item['quantity']}</td>
            <td>
                <a href="/inventory?item_id={item['id']}" class="glass-btn gold-text">إضافة</a>
                <a href="/inventory?item_id={item['id']}&project_id={project_id if project_id else 0}#withdraw-form" class="glass-btn gold-text">سحب</a>
            </td>
            <td>{min_badge}</td>
            <td>{last_withdrawal_text}</td>
        </tr>
        """

    project_options = '<option value="">اختر مشروعًا</option>'
    for project in projects:
        selected = "selected" if project_id and project["id"] == project_id else ""
        project_options += (
            f'<option value="{project["id"]}" {selected}>'
            f"{project['name']} - {inventory_company_label(project['company'])}"
            "</option>"
        )

    item_options = ""
    for item in inventory_items:
        selected = "selected" if item["id"] == selected_item_id else ""
        item_options += (
            f'<option value="{item["id"]}" {selected}>'
            f"{item['item_name']} ({item['quantity']} {item['unit'] or ''})"
            "</option>"
        )

    transactions_rows = ""
    for tx in recent_transactions:
        tx_type = "إضافة" if tx["type"] == "in" else "سحب"
        tx_project = tx["project_name"] if tx["project_name"] else "-"
        tx_employee = tx["employee_name"] if tx["employee_name"] else "-"
        tx_company = inventory_company_label(tx["company"])
        transactions_rows += f"""
        <tr>
            <td>{tx['item_name'] or '-'}</td>
            <td>{tx_type}</td>
            <td>{tx['quantity']} {tx['unit'] or ''}</td>
            <td>{tx_project}</td>
            <td>{tx_employee}</td>
            <td>{tx_company}</td>
            <td>{tx['date'] or '-'}</td>
        </tr>
        """

    linked_project_html = ""
    if linked_project:
        linked_project_html = (
            f'<div class="inventory-note">السحب الحالي مرتبط بالمشروع: '
            f'<strong>{linked_project["name"]}</strong> - '
            f'{inventory_company_label(linked_project["company"])}</div>'
        )

    feedback_html = ""
    if message:
        feedback_html += f'<div class="inventory-note success-note">{message_map.get(message, message)}</div>'
    if error:
        feedback_html += f'<div class="inventory-note error-note">{error_map.get(error, error)}</div>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script>
function toggleNewTenantFields(mode) {{
    const select = document.getElementById(`contract-tenant-select-${{mode}}`);
    const fields = document.getElementById(`contract-new-tenant-fields-${{mode}}`);
    if (!select || !fields) return;
    const isNewTenant = select.value === "__new__";
    fields.style.display = isNewTenant ? "block" : "none";
    fields.querySelectorAll("input, select").forEach((field) => {{
        if (field.name === "new_tenant_name") {{
            field.required = isNewTenant;
        }}
    }});
}}
</script>
<body class="system-dark owner-page">
{HOME_BUTTON}
<div class="dashboard">
    <h1>المستودع المركزي</h1>
    <p>إدارة المخزون الموحد وربطه مباشرة بالمشاريع</p>

    {feedback_html}
    {linked_project_html}

    <div class="inventory-layout">
        <div class="inventory-panel">
            <h3>إضافة مخزون</h3>
            <form action="/inventory/add" method="post">
                <label>اسم الصنف</label>
                <input type="text" name="item_name" value="" required>

                <label>الوحدة</label>
                <input type="text" name="unit" value="" placeholder="كرتون / متر / قطعة" required>

                <label>الكمية</label>
                <input type="number" name="quantity" min="1" required>

                <label>الحد الأدنى</label>
                <input type="number" name="min_quantity" min="0" value="0">

                <button type="submit" class="glass-btn gold-text">إضافة للمخزون</button>
            </form>
        </div>

        <div class="inventory-panel" id="withdraw-form">
            <h3>سحب من المستودع</h3>
            <form action="/inventory/withdraw" method="post">
                <label>الصنف</label>
                <select name="item_id" required>
                    {item_options if item_options else '<option value="">لا توجد أصناف</option>'}
                </select>

                <label>الكمية</label>
                <input type="number" name="quantity" min="1" required>

                <label>المشروع</label>
                <select name="project_id" required>
                    {project_options}
                </select>

                <label>اسم الموظف</label>
                <input type="text" name="employee_name" placeholder="اسم الموظف المسؤول عن السحب" required>

                <button type="submit" class="glass-btn gold-text">تسجيل السحب</button>
            </form>
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <div class="inventory-table-head">
            <h3>الأصناف الحالية</h3>
            <a href="/inventory" class="glass-btn back-btn">⬅ رجوع</a>
        </div>

        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الصنف</th>
                <th>الوحدة</th>
                <th>الكمية الحالية</th>
                <th>الإجراءات</th>
                <th>الحالة</th>
                <th>آخر سحب</th>
            </tr>
            {item_rows if item_rows else "<tr><td colspan='6'>لا توجد أصناف في المستودع</td></tr>"}
        </table>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>آخر الحركات</h3>
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الصنف</th>
                <th>النوع</th>
                <th>الكمية</th>
                <th>المشروع</th>
                <th>الموظف</th>
                <th>الشركة</th>
                <th>التاريخ</th>
            </tr>
            {transactions_rows if transactions_rows else "<tr><td colspan='7'>لا توجد حركات حتى الآن</td></tr>"}
        </table>
    </div>

    <br>
    <a href="/" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/inventory/add")
def inventory_add(
    request: Request,
    item_name: str = Form(...),
    unit: str = Form(...),
    quantity: int = Form(...),
    min_quantity: int = Form(0),
):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_section_access(request, "all", "inventory")
        if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
            return access_result
    elif not is_admin(user):
        has_inventory_scope = any(
            user_has_company_access(user["id"], company)
            for company in ("works", "realestate", "logistics")
        )
        if not has_inventory_scope:
            return access_denied_response()
    conn = get_db()

    existing_item = conn.execute(
        "SELECT * FROM inventory WHERE item_name = ? AND unit = ?",
        (item_name.strip(), unit.strip())
    ).fetchone()

    if existing_item:
        conn.execute(
            "UPDATE inventory SET quantity = quantity + ?, min_quantity = ? WHERE id = ?",
            (quantity, min_quantity, existing_item["id"])
        )
        item_id = existing_item["id"]
    else:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO inventory (item_name, unit, quantity, min_quantity) VALUES (?, ?, ?, ?)",
            (item_name.strip(), unit.strip(), quantity, min_quantity)
        )
        item_id = cur.lastrowid

    conn.execute(
        """
        INSERT INTO inventory_transactions (item_id, type, quantity, project_id, company, date, notes, employee_name)
        VALUES (?, 'in', ?, NULL, ?, DATE('now'), ?, NULL)
        """,
        (item_id, quantity, "central", "إضافة للمخزون")
    )

    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/inventory?item_id={item_id}&message=stock_added", status_code=303)


@app.post("/inventory/withdraw")
def inventory_withdraw(
    request: Request,
    item_id: int = Form(...),
    quantity: int = Form(...),
    project_id: int = Form(...),
    employee_name: str = Form(...),
):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_section_access(request, "all", "inventory")
        if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
            return access_result
    elif not is_admin(user):
        has_inventory_scope = any(
            user_has_company_access(user["id"], company)
            for company in ("works", "realestate", "logistics")
        )
        if not has_inventory_scope:
            return access_denied_response()
    conn = get_db()

    item = conn.execute(
        "SELECT * FROM inventory WHERE id = ?",
        (item_id,)
    ).fetchone()
    project = conn.execute(
        "SELECT id, name, company FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not item:
        conn.close()
        return RedirectResponse(url="/inventory?error=item_missing", status_code=303)

    if not project:
        conn.close()
        return RedirectResponse(url=f"/inventory?item_id={item_id}&error=project_missing", status_code=303)

    if quantity <= 0:
        conn.close()
        return RedirectResponse(
            url=f"/inventory?project_id={project_id}&item_id={item_id}&error=quantity_invalid",
            status_code=303
        )

    if item["quantity"] < quantity:
        conn.close()
        return RedirectResponse(
            url=f"/inventory?project_id={project_id}&item_id={item_id}&error=quantity_unavailable",
            status_code=303
        )

    conn.execute(
        "UPDATE inventory SET quantity = quantity - ? WHERE id = ?",
        (quantity, item_id)
    )
    conn.execute(
        """
        INSERT INTO inventory_transactions (item_id, type, quantity, project_id, company, date, notes, employee_name)
        VALUES (?, 'out', ?, ?, ?, DATE('now'), ?, ?)
        """,
        (
            item_id,
            quantity,
            project_id,
            project["company"],
            f"سحب للمشروع {project['name']}",
            employee_name.strip()
        )
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/inventory?project_id={project_id}&item_id={item_id}&message=stock_withdrawn",
        status_code=303
    )

# ======================
# صفحة الشركة
# ======================

@app.get("/company/{company}", response_class=HTMLResponse)
def company_page(request: Request, company: str):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_active_projects_project_manager(user):
        return RedirectResponse(url=get_role_landing_url(user), status_code=303)
    if is_employee(user) and company == "works":
        access_result = ensure_employee_any_section_access(request, company, {"daily_log", "expenses"})
    else:
        access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_owner(access_result) and company == "realestate":
        return RedirectResponse(url=get_role_landing_url(access_result), status_code=303)
    allowed_sections = get_employee_allowed_sections(access_result["id"], company) if is_employee(access_result) else set()
    if is_works_expenses_only_user(access_result, company):
        return RedirectResponse(url=get_works_expenses_landing_url(), status_code=303)
    if is_works_daily_log_only_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    is_works_partner_read_only = is_works_partner_user(access_result, company)

    if company == "realestate":
        if is_employee(access_result):
            return RedirectResponse(url=get_realestate_landing_url(access_result), status_code=303)
        return f"""

        <meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>Urban Rise</h1>
<p>التطوير والاستثمار العقاري</p>

<div class="companies">

<a href="/realestate-development" class="company-card realestate">
<h2>التطوير العقاري</h2>
</a>

<a href="/realestate-investment" class="company-card realestate">
<h2>الاستثمار العقاري</h2>
</a>

<a href="/property-management" class="company-card realestate">
<h2>إدارة الأملاك</h2>
</a>

<a href="/inventory" class="company-card realestate">
<h2>المستودع</h2>
</a>

</div>

<br>

<a href="/" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

    names = {
        "works": "Urban Rise Works",
        "logistics": "Urban Rise Logistics"
    }

    arabic = {
        "works": "المقاولات",
        "logistics": "اللوجستيات"
    }

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script>
function toggleNewTenantFields(mode) {{
    const select = document.getElementById(`contract-tenant-select-${{mode}}`);
    const fields = document.getElementById(`contract-new-tenant-fields-${{mode}}`);
    if (!select || !fields) return;
    const isNewTenant = select.value === "__new__";
    fields.style.display = isNewTenant ? "block" : "none";
    fields.querySelectorAll("input, select").forEach((field) => {{
        if (field.name === "new_tenant_name") {{
            field.required = isNewTenant;
        }}
    }});
}}
</script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
<h1>{names.get(company, "")}</h1>
<p>{arabic.get(company, "")}</p>

<div class="companies">

<a href="/projects?company={company}" class="company-card {company}">
<h2>المشاريع</h2>
</a>

<a href="/quotes?company={company}" class="company-card {company}">
<h2>عروض الأسعار</h2>
</a>

<a href="/employees?company={company}" class="company-card {company}">
<h2>الموظفين</h2>
</a>

<a href="/contracts?company={company}" class="company-card {company}">
<h2>العقود</h2>
</a>

{f'<a href="/equipment?company={company}" class="company-card {company}"><h2>المعدات</h2></a>' if company == 'logistics' else ''}

</div>

{"<div class='inventory-note' style='margin-top:18px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_works_partner_read_only else ""}

<br>
<a href="/" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

# ======================
# المشاريع
# ======================

@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, company: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_any_section_access(request, company, {"daily_log", "expenses"})
    else:
        access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)
    is_works_expenses_scope_user = is_works_expenses_suppliers_employee(access_result, company)
    is_works_daily_scope_user = is_works_daily_log_only_employee(access_result, company)
    conn = get_db()
    projects = conn.execute(
        "SELECT * FROM projects WHERE company = ?",
        (company,)
    ).fetchall()
    conn.close()

    rows = ""
    for p in projects:
        row_actions = "-"
        if False:
            row_actions = (
                f'<a href="/edit-project/{p["id"]}?company={company}" class="action-btn">تعديل</a>'
                f'<a href="/delete-project/{p["id"]}?company={company}" class="action-btn" onclick="return confirm(\'هل تريد حذف هذا المشروع؟\')">حذف</a>'
            )
        rows += f"""
        <tr>
            <td>{p['id']}</td>
            <td>
                <a href="{'/project-daily?project_id=' + str(p['id']) + '&company=' + company if is_works_daily_scope_user else '/project/' + str(p['id']) + '?company=' + company}">
                    {p['name']}
                </a>
            </td>
            <td>{p['client']}</td>
            <td>{p['status']}</td>
            <td>{"-" if is_read_only_works_partner or is_works_expenses_scope_user or is_works_daily_scope_user else f'''<a href="/edit-project/{p['id']}?company={company}" class="action-btn">تعديل</a><a href="/delete-project/{p['id']}?company={company}" class="action-btn" onclick="return confirm('هل تريد حذف هذا المشروع؟')">حذف</a>'''}</td>
        </tr>
        """

    new_project_button_html = ""
    if not (is_read_only_works_partner or is_works_expenses_scope_user or is_works_daily_scope_user):
        new_project_button_html = f"""
<a href="/new-project?company={company}" class="company-card {company}">
<h2>مشروع جديد</h2>
</a>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>المشاريع</h1>

{"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

{new_project_button_html}

<br><br>

<table border="1" style="background:white;margin:auto;width:85%;">
<tr>
<th>رقم</th>
<th>اسم المشروع</th>
<th>العميل</th>
<th>الحالة</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='5'>لا توجد مشاريع</td></tr>"}

</table>

<br>

<a href="/company/{company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.get("/new-project", response_class=HTMLResponse)
def new_project_form(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    if is_employee(access_result):
        works_cards = ""
        if "daily_log" in allowed_sections or "expenses" in allowed_sections:
            works_cards += f"""
<a href="/projects?company={company}" class="company-card {company}">
<h2>المشاريع</h2>
</a>
"""
        return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
<h1>{names.get(company, "")}</h1>
<p>{arabic.get(company, "")}</p>

<div class="companies">
{works_cards}
</div>

<br>
<a href="/" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>إضافة مشروع</h2>

    <form action="/save-project" method="post">
        <input type="hidden" name="company" value="{company}">

        اسم المشروع:
        <input type="text" name="name" required><br><br>

        العميل:
        <input type="text" name="client" required><br><br>

        تاريخ البداية:
        <input type="date" name="start_date"><br><br>

        تاريخ النهاية:
        <input type="date" name="end_date"><br><br>

        الحالة:
        <select name="status">
            <option value="جاري">جاري</option>
            <option value="متوقف">متوقف</option>
            <option value="منتهي">منتهي</option>
        </select>

        {"<br><br>نوع المشروع:<select name='project_type'><option value=''>اختر نوع المشروع</option><option value='فيلا'>فيلا</option><option value='شقة'>شقة</option><option value='ترميم'>ترميم</option><option value='مبنى تجاري'>مبنى تجاري</option></select><br><br>نوع العمل:<select name='work_type'><option value=''>اختر نوع العمل</option><option value='عظم'>عظم</option><option value='تشطيب'>تشطيب</option><option value='ترميم'>ترميم</option><option value='صيانة'>صيانة</option></select><br><br>مستوى التشطيب:<select name='finish_level'><option value=''>اختر مستوى التشطيب</option><option value='اقتصادي'>اقتصادي</option><option value='متوسط'>متوسط</option><option value='فاخر'>فاخر</option></select><br><br>المساحة (متر مربع):<input type='number' step='0.01' min='0' name='area'><br><br>قيمة العقد:<input type='number' step='0.01' min='0' name='contract_value'><br><br>" if company == 'works' else ""}

        <br><br>
        <button type="submit" class="glass-btn gold-text">حفظ</button>
    </form>

    <br>
    <a href="/projects?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.post("/save-project", response_class=HTMLResponse)
def save_project(
    request: Request,
    company: str = Form(...),
    name: str = Form(...),
    client: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    status: str = Form(...),
    project_type: str = Form(""),
    work_type: str = Form(""),
    finish_level: str = Form(""),
    area: str = Form(""),
    contract_value: str = Form("")
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    structured_project_type = project_type.strip() if company == "works" else ""
    structured_work_type = work_type.strip() if company == "works" else ""
    structured_finish_level = finish_level.strip() if company == "works" else ""
    structured_area = safe_float(area) if company == "works" and str(area).strip() else None
    structured_contract_value = safe_float(contract_value) if company == "works" and str(contract_value).strip() else None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO projects (
            company, name, client, start_date, end_date, status,
            project_type, work_type, finish_level, area, contract_value
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company,
            name,
            client,
            start_date,
            end_date,
            status,
            structured_project_type,
            structured_work_type,
            structured_finish_level,
            structured_area,
            structured_contract_value,
        )
    )
    project_id = cur.lastrowid
    if company == "works":
        contract_cur = conn.cursor()
        contract_cur.execute(
            """
            INSERT INTO contracts (company, quote_id, status, source_type, manual_project_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                company,
                None,
                status,
                CONTRACT_RECORD_SOURCE_MANUAL_PROJECT,
                project_id,
            )
        )
        conn.execute(
            "UPDATE projects SET contract_id = ? WHERE id = ? AND company = ?",
            (contract_cur.lastrowid, project_id, company),
        )
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/projects?company={company}", status_code=303)


def render_project_structured_fields(company: str, project: sqlite3.Row | None = None) -> str:
    if company != "works":
        return ""

    project_type_value = (project["project_type"] or "") if project else ""
    work_type_value = (project["work_type"] or "") if project else ""
    finish_level_value = (project["finish_level"] or "") if project else ""
    area_value = project["area"] if project and project["area"] not in (None, "") else ""
    contract_value_value = project["contract_value"] if project and "contract_value" in project.keys() and project["contract_value"] not in (None, "") else ""

    def build_options(options: list[str], selected_value: str, placeholder: str) -> str:
        html = f"<option value=''>{placeholder}</option>"
        for option in options:
            selected = "selected" if option == selected_value else ""
            html += f"<option value='{option}' {selected}>{option}</option>"
        return html

    return f"""
        <br><br>
        نوع المشروع:
        <select name="project_type">
            {build_options(["فيلا", "شقة", "ترميم", "مبنى تجاري"], project_type_value, "اختر نوع المشروع")}
        </select>

        <br><br>
        نوع العمل:
        <select name="work_type">
            {build_options(["عظم", "تشطيب", "ترميم", "صيانة"], work_type_value, "اختر نوع العمل")}
        </select>

        <br><br>
        مستوى التشطيب:
        <select name="finish_level">
            {build_options(["اقتصادي", "متوسط", "فاخر"], finish_level_value, "اختر مستوى التشطيب")}
        </select>

        <br><br>
        المساحة (متر مربع):
        <input type="number" step="0.01" min="0" name="area" value="{area_value}">

        <br><br>
        قيمة العقد:
        <input type="number" step="0.01" min="0" name="contract_value" value="{contract_value_value}">
    """


@app.get("/edit-project/{project_id}", response_class=HTMLResponse)
def edit_project_form(request: Request, project_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND company = ?",
        (project_id, company)
    ).fetchone()
    conn.close()

    if not project:
        return "<h2>المشروع غير موجود</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>تعديل المشروع</h2>

    <form action="/update-project" method="post">
        <input type="hidden" name="project_id" value="{project_id}">
        <input type="hidden" name="company" value="{company}">

        اسم المشروع:
        <input type="text" name="name" value="{project['name']}" required><br><br>

        العميل:
        <input type="text" name="client" value="{project['client']}" required><br><br>

        تاريخ البداية:
        <input type="date" name="start_date" value="{project['start_date'] or ''}"><br><br>

        تاريخ النهاية:
        <input type="date" name="end_date" value="{project['end_date'] or ''}"><br><br>

        الحالة:
        <select name="status">
            <option value="جاري" {"selected" if project['status'] == "جاري" else ""}>جاري</option>
            <option value="متوقف" {"selected" if project['status'] == "متوقف" else ""}>متوقف</option>
            <option value="منتهي" {"selected" if project['status'] == "منتهي" else ""}>منتهي</option>
        </select>

        {render_project_structured_fields(company, project)}

        <br><br>
        <button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
    </form>

    <br>
    <a href="/projects?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.post("/update-project")
def update_project(
    request: Request,
    project_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    client: str = Form(...),
    start_date: str = Form(""),
    end_date: str = Form(""),
    status: str = Form(...),
    project_type: str = Form(""),
    work_type: str = Form(""),
    finish_level: str = Form(""),
    area: str = Form(""),
    contract_value: str = Form("")
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    structured_project_type = project_type.strip() if company == "works" else ""
    structured_work_type = work_type.strip() if company == "works" else ""
    structured_finish_level = finish_level.strip() if company == "works" else ""
    structured_area = safe_float(area) if company == "works" and str(area).strip() else None
    structured_contract_value = safe_float(contract_value) if company == "works" and str(contract_value).strip() else None
    conn = get_db()
    conn.execute(
        """
        UPDATE projects
        SET name = ?, client = ?, start_date = ?, end_date = ?, status = ?,
            project_type = ?, work_type = ?, finish_level = ?, area = ?, contract_value = ?
        WHERE id = ? AND company = ?
        """,
        (
            name,
            client,
            start_date,
            end_date,
            status,
            structured_project_type,
            structured_work_type,
            structured_finish_level,
            structured_area,
            structured_contract_value,
            project_id,
            company,
        )
    )
    if company == "works":
        conn.execute(
            """
            UPDATE contracts
            SET status = ?
            WHERE company = ? AND manual_project_id = ? AND COALESCE(NULLIF(TRIM(source_type), ''), ?) = ?
            """,
            (
                status,
                company,
                project_id,
                CONTRACT_RECORD_SOURCE_QUOTE,
                CONTRACT_RECORD_SOURCE_MANUAL_PROJECT,
            )
        )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/projects?company={company}", status_code=303)

@app.get("/delete-project/{project_id}")
def delete_project(request: Request, project_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url="/projects?company=works", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    cascade_delete_project_records(project_id, company)
    return RedirectResponse(url=f"/projects?company={company}", status_code=303)

@app.get("/quotes", response_class=HTMLResponse)
def quotes_page(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    conn = get_db()
    quotes = conn.execute(
        "SELECT id, client, status FROM quotes WHERE company = ?",
        (company,)
    ).fetchall()
    conn.close()

    rows = ""
    for q in quotes:
        manage_html = "-"
        if not is_read_only_works_partner:
            manage_html = (
                f'<a href="/edit-quote/{q["id"]}?company={company}" class="action-btn">تعديل</a>'
                f'<a href="/delete-quote/{q["id"]}?company={company}" class="action-btn" onclick="return confirm(\'هل تريد حذف عرض السعر؟\')">حذف</a>'
            )
        rows += f"""
        <tr>
            <td>{q['id']}</td>
            <td>{q['client']}</td>
            <td>{q['status']}</td>
            <td>
                <a href="/quote/{q['id']}?company={company}" target="_blank">فتح</a>
            </td>
            <td>{manage_html}</td>
        </tr>
        """

    create_card = ""
    if not is_read_only_works_partner:
        create_card = f"""
        <a href="/new-quote?company={company}" class="company-card {company}">
            <h2>&#10133; عرض سعر جديد</h2>
        </a>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
        <h1>عروض الأسعار</h1>

        {"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}
        {create_card}

        <br><br>

        <table border="1" style="background:white;margin:auto;width:85%;">
            <tr>
                <th>رقم</th>
                <th>العميل</th>
                <th>الحالة</th>
                <th>فتح</th>
                <th>إدارة</th>
            </tr>
            {rows if rows else "<tr><td colspan='5'>لا توجد عروض أسعار</td></tr>"}
        </table>

        <br>
        <a href="/company/{company}" class="glass-btn back-btn">⬅ رجوع</a>
    </div>
    """


@app.get("/new-quote", response_class=HTMLResponse)
def new_quote_form(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>إنشاء عرض سعر</h2>

    <form action="/save-quote" method="post">
        <input type="hidden" name="company" value="{company}">

        اسم العميل:
        <input type="text" name="client" required><br><br>

        رقم الهوية:
        <input type="text" name="client_id"><br><br>

        عنوان العميل:
        <input type="text" name="client_address"><br><br>

        موقع المشروع:
        <input type="text" name="project_location"><br><br>

        مدة التنفيذ (مثال: 90 يوم):
        <input type="text" name="duration"><br><br>

        <button type="submit" class="glass-btn gold-text">إنشاء العرض</button>
    </form>

    <br>
    <a href="/quotes?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-quote", response_class=HTMLResponse)
def save_quote(
    request: Request,
    company: str = Form(...),
    client: str = Form(...),
    client_id: str = Form(""),
    client_address: str = Form(""),
    project_location: str = Form(""),
    duration: str = Form("")
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO quotes 
        (company, client, status, client_id, client_address, project_location, duration)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        company,
        client,
        "جديد",
        client_id,
        client_address,
        project_location,
        duration
    ))

    quote_id = cur.lastrowid
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/quote/{quote_id}?company={company}", status_code=303)


@app.get("/edit-quote/{quote_id}", response_class=HTMLResponse)
def edit_quote_form(request: Request, quote_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ? AND company = ?",
        (quote_id, company)
    ).fetchone()
    conn.close()

    if not quote:
        return "<h2>عرض السعر غير موجود</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>تعديل عرض السعر</h2>

    <form action="/update-quote" method="post">
        <input type="hidden" name="quote_id" value="{quote_id}">
        <input type="hidden" name="company" value="{company}">

        اسم العميل:
        <input type="text" name="client" value="{quote['client']}" required><br><br>

        رقم الهوية:
        <input type="text" name="client_id" value="{quote['client_id'] or ''}"><br><br>

        عنوان العميل:
        <input type="text" name="client_address" value="{quote['client_address'] or ''}"><br><br>

        موقع المشروع:
        <input type="text" name="project_location" value="{quote['project_location'] or ''}"><br><br>

        مدة التنفيذ:
        <input type="text" name="duration" value="{quote['duration'] or ''}"><br><br>

        الحالة:
        <input type="text" name="status" value="{quote['status'] or ''}"><br><br>

        <button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
    </form>

    <br>
    <a href="/quotes?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.post("/update-quote")
def update_quote(
    request: Request,
    quote_id: int = Form(...),
    company: str = Form(...),
    client: str = Form(...),
    client_id: str = Form(""),
    client_address: str = Form(""),
    project_location: str = Form(""),
    duration: str = Form(""),
    status: str = Form("")
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "UPDATE quotes SET client = ?, client_id = ?, client_address = ?, project_location = ?, duration = ?, status = ? WHERE id = ? AND company = ?",
        (client, client_id, client_address, project_location, duration, status, quote_id, company)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/quotes?company={company}", status_code=303)

@app.get("/delete-quote/{quote_id}")
def delete_quote(request: Request, quote_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    contracts = conn.execute("SELECT id FROM contracts WHERE quote_id = ?", (quote_id,)).fetchall()
    for contract in contracts:
        conn.execute("DELETE FROM projects WHERE contract_id = ?", (contract['id'],))
    conn.execute("DELETE FROM contracts WHERE quote_id = ?", (quote_id,))
    conn.execute("DELETE FROM quote_items WHERE quote_id = ?", (quote_id,))
    conn.execute("DELETE FROM quote_payments WHERE quote_id = ?", (quote_id,))
    conn.execute("DELETE FROM quotes WHERE id = ? AND company = ?", (quote_id, company))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/quotes?company={company}", status_code=303)

@app.get("/quote/{quote_id}", response_class=HTMLResponse)
def quote_detail(request: Request, quote_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)
    from datetime import date
    conn = get_db()

    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ?",
        (quote_id,)
    ).fetchone()

    items = conn.execute(
        "SELECT description, qty, unit_price FROM quote_items WHERE quote_id = ?",
        (quote_id,)
    ).fetchall()

    payments = conn.execute(
        "SELECT * FROM quote_payments WHERE quote_id = ?",
        (quote_id,)
    ).fetchall()

    conn.close()

    rows = ""
    total = 0
    for i in items:
        line = i["qty"] * i["unit_price"]
        total += line
        rows += f"""
        <tr>
            <td>{i['description']}</td>
            <td>{i['qty']}</td>
            <td>{i['unit_price']}</td>
            <td>{line}</td>
        </tr>
        """

    payment_rows = ""
    for p in payments:
        amount = (p["percentage"] / 100) * total
        payment_rows += f"""
        <tr>
            <td>{p['title']}</td>
            <td>{p['percentage']} %</td>
            <td>{round(amount,2)} ريال</td>
        </tr>
        """

    today = date.today().strftime("%Y-%m-%d")

    company_names = {
        "works": "Urban Rise Works",
        "realestate": "Urban Rise",
        "logistics": "Urban Rise Logistics"
    }
    company_display = company_names.get(company, "Urban Rise")

    intro_text = """
    السلام عليكم ورحمة الله وبركاته،<br><br>
    نفيدكم بتقديم عرض سعر حسب التفاصيل التالية، ونأمل أن ينال العرض رضاكم.
    """

    footer_text = "يتم تنفيذ الأعمال وفق أعلى معايير الجودة، مع ضمان حسن التنفيذ بإذن الله."

    quote_item_form_html = ""
    if not is_read_only_works_partner:
        quote_item_form_html = f"""
<form action="/add-item/{quote_id}?company={company}" method="post">
الوصف:
<textarea name="description" rows="6" style="width:100%; resize: vertical;" required></textarea>

الكمية:
<input type="number" step="0.01" name="qty" required>

سعر الوحدة:
<input type="number" step="0.01" name="unit_price" required>

<button type="submit" class="glass-btn gold-text">&#10133; إضافة بند</button>
</form>
"""

    quote_payment_form_html = ""
    if not is_read_only_works_partner:
        quote_payment_form_html = f"""
<form action="/add-payment/{quote_id}?company={company}" method="post">

اسم الدفعة:
<input type="text" name="title" required>

النسبة:
<input type="number" step="0.01" name="percentage" required>

<button type="submit" class="glass-btn gold-text">إضافة دفعة</button>

</form>
"""

    convert_to_contract_html = ""
    if not is_read_only_works_partner:
        convert_to_contract_html = f'<a href="/convert-to-contract/{quote_id}?company={company}" class="action-btn">تحويل لعقد</a>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
{HOME_BUTTON}
<style>
.table td {{
    vertical-align: top;
    white-space: normal;
    line-height: 1.8;
}}
</style>

<div style="background:white;padding:60px 40px;line-height:2;text-align:right;position:relative">

<!-- علامة مائية -->
<div style="
position:absolute;
top:50%;
left:50%;
transform:translate(-50%,-50%);
opacity:0.06;
font-size:80px;
font-weight:bold;
text-align:center;
pointer-events:none;
">
URBAN RISE<br>WORKS
</div>

<!-- الهيدر -->
<div style="display:flex;justify-content:space-between;align-items:flex-start">

<!-- اسم الشركة -->
<div>
<strong style="font-size:22px">{company_display}</strong>
</div>

<!-- معلومات الشركة -->
<div style="text-align:left;font-size:14px;line-height:1.8">

📞 0566005668 <br>
✉️ kalamrah505@gmail.com <br>
📍 الرياض - حي النرجس - طريق أبو بكر الصديق

</div>

</div>

<div style="text-align:left">
التاريخ: {today}
</div>


<h2 style="text-align:center">عرض سعر</h2>

{f"<div style='display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin:18px 0;'><a href='/quote-pdf/{quote_id}?company={company}' class='glass-btn expense-primary-btn'>تحميل عرض السعر PDF</a></div>" if normalize_access_value(company) == "works" else ""}

<hr>

<p>{intro_text}</p>

{"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

{quote_item_form_html}

<br><br>

<table class="table" border="1" style="width:100%;text-align:center">

<tr>
<th>الوصف</th>
<th>الكمية</th>
<th>سعر الوحدة</th>
<th>الإجمالي</th>
</tr>

{rows if rows else "<tr><td colspan='4'>لا توجد بنود</td></tr>"}

<tr>
<td colspan="3"><strong>الإجمالي</strong></td>
<td><strong>{total} ريال</strong></td>
</tr>

</table>

<br><br>

<h3>الدفعات</h3>

{quote_payment_form_html}

<br>

<table class="table" border="1" style="width:100%;text-align:center">

<tr>
<th>الدفعة</th>
<th>النسبة</th>
<th>المبلغ</th>
</tr>

{payment_rows if payment_rows else "<tr><td colspan='3'>لا توجد دفعات</td></tr>"}

</table>

<br>

<p>{footer_text}</p>


<br>

    {convert_to_contract_html}

</div>
"""

@app.get("/quote-pdf/{quote_id}")
@app.get("/quote-report/{quote_id}")
def quote_report(request: Request, quote_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if normalize_access_value(company) != "works":
        return HTMLResponse("<h2>هذه الميزة متاحة لشركة المقاولات فقط</h2>", status_code=403)

    conn = get_db()
    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ? AND company = ?",
        (quote_id, company)
    ).fetchone()
    if not quote:
        conn.close()
        return HTMLResponse("<h2>عرض السعر غير موجود</h2>", status_code=404)
    items = conn.execute(
        "SELECT description, qty, unit_price FROM quote_items WHERE quote_id = ?",
        (quote_id,)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM quote_payments WHERE quote_id = ?",
        (quote_id,)
    ).fetchall()
    conn.close()

    file_path, file_name = build_quote_report_pdf(quote, items, payments, company=company)
    return FileResponse(path=file_path, filename=file_name, media_type="application/pdf")

@app.post("/add-item/{quote_id}", response_class=HTMLResponse)
def add_item(
    request: Request,
    quote_id: int,
    company: str = "",
    description: str = Form(...),
    qty: float = Form(...),
    unit_price: float = Form(...)
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "INSERT INTO quote_items (quote_id, description, qty, unit_price) VALUES (?, ?, ?, ?)",
        (quote_id, description, qty, unit_price)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/quote/{quote_id}?company={company}", status_code=303)

@app.post("/add-payment/{quote_id}")
def add_payment(
    request: Request,
    quote_id: int,
    company: str = "",
    title: str = Form(...),
    percentage: float = Form(...)
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "INSERT INTO quote_payments (quote_id, title, percentage) VALUES (?, ?, ?)",
        (quote_id, title, percentage)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/quote/{quote_id}?company={company}",
        status_code=303
    )

# ======================
# تحويل عرض إلى عقد
# ======================
@app.get("/convert-to-contract/{quote_id}")
def convert_to_contract(request: Request, quote_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    try:
        quote = conn.execute(
            "SELECT * FROM quotes WHERE id = ? AND company = ?",
            (quote_id, company)
        ).fetchone()
        if not quote:
            return HTMLResponse(
                "<div class='inventory-note' style='margin:20px 0;'>عرض السعر غير موجود أو لا يتبع هذه الشركة.</div>",
                status_code=404,
            )

        existing_contract = conn.execute(
            "SELECT id FROM contracts WHERE quote_id = ? AND company = ?",
            (quote_id, company)
        ).fetchone()

        contract_id = existing_contract["id"] if existing_contract else None
        if not contract_id:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO contracts (company, quote_id, status, source_type, manual_project_id) VALUES (?, ?, ?, ?, ?)",
                (company, quote_id, "ساري", CONTRACT_RECORD_SOURCE_QUOTE, None)
            )
            contract_id = cur.lastrowid

        existing_project = conn.execute(
            "SELECT id FROM projects WHERE contract_id = ? AND company = ?",
            (contract_id, company)
        ).fetchone()
        if existing_project:
            project_id = existing_project["id"]
        else:
            quote_keys = set(quote.keys())
            project_name = (quote["project_location"] or "").strip() or f"مشروع عرض {quote_id}"
            project_client = (quote["client"] or "").strip() or "غير محدد"
            start_date = ""
            end_date = ""
            status = "جاري"
            conn.execute(
                """
                INSERT INTO projects (
                    company, name, client, start_date, end_date, status, contract_id,
                    project_type, work_type, finish_level, area
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company,
                    project_name,
                    project_client,
                    start_date,
                    end_date,
                    status,
                    contract_id,
                    (quote["project_type"] or "").strip() if "project_type" in quote_keys and quote["project_type"] else "",
                    (quote["work_type"] or "").strip() if "work_type" in quote_keys and quote["work_type"] else "",
                    (quote["finish_level"] or "").strip() if "finish_level" in quote_keys and quote["finish_level"] else "",
                    safe_float(quote["area"]) if "area" in quote_keys and str(quote["area"]).strip() else None,
                )
            )
            project_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "UPDATE quotes SET status = ? WHERE id = ? AND company = ?",
            ("تم التحويل لعقد", quote_id, company)
        )
        conn.commit()
        return RedirectResponse(
            url=f"/project/{project_id}?company={company}",
            status_code=303
        )
    except Exception as exc:
        conn.rollback()
        logger.exception("Failed to convert quote %s to contract/project for company %s", quote_id, company, exc_info=exc)
        return HTMLResponse(
            "<div class='inventory-note' style='margin:20px 0;'>تعذر تحويل عرض السعر إلى عقد ومشروع. يرجى التحقق من البيانات ثم المحاولة مرة أخرى.</div>",
            status_code=500,
        )
    finally:
        conn.close()
# ======================
# العقود
# ======================

@app.post("/upload-contract-attachment")
def upload_contract_attachment(
    request: Request,
    source_type: str = Form(...),
    contract_id: int = Form(...),
    company: str = Form(""),
    property_id: int = Form(0),
    project_id: int = Form(0),
    attachment_file: UploadFile = File(...),
):
    context, access_result = ensure_contract_attachment_access(
        request,
        source_type=source_type,
        contract_id=contract_id,
        require_admin=True,
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    back_url = get_contract_attachment_back_url(
        source_type,
        company=context["company"] or company,
        property_id=context["property_id"] or property_id,
        project_id=context["project_id"] or project_id,
    )

    original_name = os.path.basename(attachment_file.filename or "").strip()
    if not original_name:
        return RedirectResponse(
            url=build_redirect_url(back_url, error="يرجى اختيار ملف مرفق"),
            status_code=303,
        )

    _, extension = os.path.splitext(original_name)
    unique_name = f"{uuid.uuid4().hex}{extension}"
    os.makedirs(CONTRACT_ATTACHMENT_UPLOAD_DIR, exist_ok=True)
    saved_path = os.path.join(CONTRACT_ATTACHMENT_UPLOAD_DIR, unique_name)

    try:
        with open(saved_path, "wb") as output_file:
            shutil.copyfileobj(attachment_file.file, output_file)
    finally:
        attachment_file.file.close()

    conn = get_db()
    conn.execute(
        """
        INSERT INTO contract_attachments (contract_id, source_type, file_path, file_name, uploaded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            contract_id,
            source_type,
            saved_path,
            original_name,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ),
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=build_redirect_url(back_url, message="تم رفع المرفق بنجاح"),
        status_code=303,
    )


@app.get("/contract-attachments/{attachment_id}")
def download_contract_attachment(request: Request, attachment_id: int):
    conn = get_db()
    attachment = conn.execute(
        "SELECT * FROM contract_attachments WHERE id = ?",
        (attachment_id,),
    ).fetchone()
    conn.close()

    if not attachment:
        return HTMLResponse("<h2>المرفق غير موجود</h2>", status_code=404)

    _, access_result = ensure_contract_attachment_access(
        request,
        source_type=attachment["source_type"],
        contract_id=attachment["contract_id"],
        require_admin=False,
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    file_path = attachment["file_path"] or ""
    if not file_path or not os.path.exists(file_path):
        return HTMLResponse("<h2>الملف غير موجود</h2>", status_code=404)

    return FileResponse(
        path=file_path,
        filename=attachment["file_name"] or os.path.basename(file_path),
    )

@app.get("/contracts", response_class=HTMLResponse)
def contracts_page(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)
    is_admin_user = is_admin(access_result)

    conn = get_db()
    data = conn.execute("""
        SELECT
            contracts.id,
            contracts.status,
            contracts.quote_id,
            contracts.source_type,
            contracts.manual_project_id,
            quotes.client AS quote_client,
            projects.name AS project_name,
            projects.client AS project_client
        FROM contracts
        LEFT JOIN quotes ON contracts.quote_id = quotes.id
        LEFT JOIN projects ON projects.id = contracts.manual_project_id
        WHERE contracts.company = ?
        ORDER BY contracts.id DESC
    """, (company,)).fetchall()
    attachments_map = get_contract_attachment_rows(
        conn,
        CONTRACT_ATTACHMENT_SOURCE_WORKS,
        [row["id"] for row in data],
    )
    conn.close()

    rows = ""
    for c in data:
        contract_source = get_contract_record_source(c)
        is_manual_project_contract = contract_source == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT
        manage_html = "-"
        if not is_read_only_works_partner and not is_manual_project_contract:
            manage_html = (
                f'<a href="/edit-contract/{c["id"]}?company={company}" class="action-btn">تعديل</a>'
                f'<a href="/delete-company-contract/{c["id"]}?company={company}" class="action-btn delete-btn" onclick="return confirm(\'هل تريد حذف هذا العقد؟\')">حذف</a>'
            )
        attachments_html = render_contract_attachments_html(
            attachments_map.get(c["id"], []),
            CONTRACT_ATTACHMENT_SOURCE_WORKS,
            c["id"],
            is_admin_user=is_admin_user,
            company=company,
        )
        contract_label = c["project_name"] if is_manual_project_contract else c["id"]
        if contract_label in (None, ""):
            contract_label = f"عقد رقم {c['id']}"
        client_label = c["project_client"] if is_manual_project_contract else c["quote_client"]
        if client_label in (None, ""):
            client_label = "-"
        contract_entry_html = (
            f'<span>{escape(str(contract_label))}</span>'
            if is_manual_project_contract
            else f'<a href="/contract/{c["id"]}?company={company}">{c["id"]}</a>'
        )
        rows += f"""
        <tr>
            <td>
                {contract_entry_html}
            </td>
            <td>{escape(str(client_label))}</td>
            <td>{c['status']}</td>
            <td>{attachments_html}</td>
            <td>{manage_html}</td>
        </tr>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>العقود</h1>

    {"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

    <table border="1" style="background:white;margin:auto;width:70%;">
        <tr>
            <th>رقم العقد</th>
            <th>العميل</th>
            <th>الحالة</th>
            <th>المرفقات</th>
            <th>الإدارة</th>
        </tr>
        {rows if rows else "<tr><td colspan='5'>لا توجد عقود</td></tr>"}
    </table>

    <br>
    <a href="/company/{company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.get("/contract/{contract_id}", response_class=HTMLResponse)
def contract_detail(request: Request, contract_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)
    is_admin_user = is_admin(access_result)
    conn = get_db()


    from datetime import date
    today = date.today().strftime("%Y-%m-%d")

    # نجيب العقد
    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company)
    ).fetchone()

    if not contract:
        conn.close()
        return "<h2>العقد غير موجود</h2>"

    if get_contract_record_source(contract) == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT:
        conn.close()
        return HTMLResponse(
            f"""
            <meta charset="UTF-8">
            <link rel="stylesheet" href="/static/style.css">
            <body class="system-dark">
            {HOME_BUTTON}
            <div class="dashboard">
                <div class="inventory-note" style="margin:20px 0;">
                    هذا الإدخال تم إنشاؤه من صفحة المشاريع اليدوية، ولا يملك صفحة عقد مستقلة.
                </div>
                <a href="/contracts?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
            </div>
            """,
            status_code=400,
        )

    # نجيب عرض السعر المرتبط
    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ?",
        (contract["quote_id"],)
    ).fetchone()

    # البنود
    items = conn.execute(
        "SELECT * FROM quote_items WHERE quote_id = ?",
        (quote["id"],)
    ).fetchall()

    # الدفعات
    payments = conn.execute(
        "SELECT * FROM quote_payments WHERE quote_id = ?",
        (quote["id"],)
    ).fetchall()
    attachment_rows = get_contract_attachment_rows(
        conn,
        CONTRACT_ATTACHMENT_SOURCE_WORKS,
        [contract_id],
    ).get(contract_id, [])

    conn.close()

    # حساب الإجمالي
    total = 0
    items_rows = ""
    for i in items:
        line = i["qty"] * i["unit_price"]
        total += line
        items_rows += f"""
        <tr>
            <td>{i['description']}</td>
            <td>{i['qty']}</td>
            <td>{i['unit_price']}</td>
            <td>{line}</td>
        </tr>
        """

    # حساب الدفعات
    payments_rows = ""
    for p in payments:
        amount = (p["percentage"] / 100) * total
        payments_rows += f"""
        <tr>
            <td>{p['title']}</td>
            <td>{p['percentage']}%</td>
            <td>{round(amount,2)} ريال</td>
        </tr>
        """

    attachments_html = render_contract_attachments_html(
        attachment_rows,
        CONTRACT_ATTACHMENT_SOURCE_WORKS,
        contract_id,
        is_admin_user=is_admin_user,
        company=company,
    )

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="contract-clean">
{HOME_BUTTON}

<div class="contract-page">


<h2 style="text-align:center">عقد مقاولات</h2>
{"<div class='inventory-note' style='margin:20px 0;text-align:center;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else f'''<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:20px 0;"><a href="/edit-contract/{contract_id}?company={company}" class="action-btn">تعديل</a><a href="/delete-company-contract/{contract_id}?company={company}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا العقد؟')">حذف</a></div>'''}
{f"<div style='display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin:12px 0 22px;'><a href='/contract-pdf/{contract_id}?company={company}' class='glass-btn expense-primary-btn'>تحميل العقد PDF</a></div>" if normalize_access_value(company) == "works" else ""}

<div class="inventory-panel inventory-table-panel" style="margin:20px 0;">
<h3>المرفقات</h3>
{attachments_html}
</div>

<p style="text-align:left">
رقم العقد: {contract_id}<br>
التاريخ: {today}
</p>

<hr>

<p>
تم الاتفاق في مدينة الرياض بين كل من:
</p>

<p>
<strong>رقم العقد:</strong> {contract_id}
</p>

<p>
<strong>الطرف الأول:</strong><br>
شركة أوربان رايز ووركس<br>
سجل تجاري رقم 1009136954<br>
ويمثلها المهندس/ فهد بن عبدالله آل عمره بصفته المالك<br>
ويشار إليها فيما بعد بـ "الطرف الأول" أو "المقاول".
</p>

<p>
<strong>الطرف الثاني:</strong><br>
{quote['client']}<br>
رقم الهوية: {quote['client_id'] or "................"}<br>
العنوان: {quote['client_address'] or "................"}<br>
ويشار إليه فيما بعد بـ "الطرف الثاني" أو "العميل".
</p>

<hr>

<h3>أولاً: التمهيد</h3>
<p>
حيث أن الطرف الثاني يرغب في تنفيذ أعمال مقاولات عامة في المشروع الكائن في:
{quote['project_location'] or "................"}،
وحيث أن الطرف الأول يملك الخبرة والكفاءة والإمكانات الفنية اللازمة لتنفيذ هذه الأعمال،
فقد تم الاتفاق بين الطرفين على ما يلي ويعد هذا التمهيد جزءاً لا يتجزأ من هذا العقد.
</p>

<h3>ثانياً: نطاق العمل</h3>
<p>
تنفيذ جميع الأعمال المتفق عليها حسب عرض السعر المعتمد من الطرفين.
</p>

<h3>التعديلات</h3>
<p>
أي أعمال إضافية خارج نطاق العقد يتم احتسابها بعرض سعر مستقل،
ولا يتم تنفيذها إلا بعد موافقة الطرفين.
</p>

<h3>ثالثاً: مدة التنفيذ</h3>
<p>
مدة التنفيذ: {quote['duration'] or "................"}  
وتبدأ من تاريخ استلام الموقع.
</p>

<h3>رابعاً: قيمة العقد</h3>

<table border="1" style="width:100%;text-align:center">
<tr>
<th>الوصف</th>
<th>الكمية</th>
<th>سعر الوحدة</th>
<th>الإجمالي</th>
</tr>
{items_rows}
<tr>
<td colspan="3"><strong>الإجمالي</strong></td>
<td><strong>{total} ريال سعودي</strong></td>
</tr>
</table>

<br>

<h3>آلية الدفعات</h3>

<table border="1" style="width:100%;text-align:center">
<tr>
<th>الدفعة</th>
<th>النسبة</th>
<th>المبلغ</th>
</tr>
{payments_rows}
</table>

<br>

<h3>خامساً: الأحكام العامة</h3>
<p>
يخضع هذا العقد لأنظمة المملكة العربية السعودية،
وأي نزاع يتم حله ودياً وإن تعذر يحال إلى المحكمة التجارية بالرياض.
</p>

<hr>

<p>
حرر هذا العقد من نسختين لكل طرف نسخة للعمل بموجبها.
</p>

<br><br>

<div style="display:flex;justify-content:space-between">
<div>
الطرف الأول<br><br>
التوقيع: ____________
</div>

<div>
الطرف الثاني<br><br>
التوقيع: ____________
</div>
</div>

<!-- الفوتر -->
<hr style="margin-top:50px">

<div style="text-align:center;font-size:14px;line-height:2">
📞 0566005668<br>
✉️ kalamrah505@gmail.com<br>
📍 الرياض - حي النرجس - طريق أبو بكر الصديق
</div>

<br><br>
<a href="/contracts?company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.get("/contract-pdf/{contract_id}")
@app.get("/contract-report/{contract_id}")
def contract_report(request: Request, contract_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if normalize_access_value(company) != "works":
        return HTMLResponse("<h2>هذه الميزة متاحة لشركة المقاولات فقط</h2>", status_code=403)

    conn = get_db()
    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company)
    ).fetchone()
    if not contract:
        conn.close()
        return HTMLResponse("<h2>العقد غير موجود</h2>", status_code=404)
    if get_contract_record_source(contract) == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT:
        conn.close()
        return HTMLResponse("<h2>لا يوجد تقرير عقد مستقل لهذا الإدخال</h2>", status_code=400)

    quote = conn.execute(
        "SELECT * FROM quotes WHERE id = ?",
        (contract["quote_id"],)
    ).fetchone()
    if not quote:
        conn.close()
        return HTMLResponse("<h2>عرض السعر المرتبط غير موجود</h2>", status_code=404)
    project = conn.execute(
        "SELECT * FROM projects WHERE contract_id = ? AND company = ? LIMIT 1",
        (contract_id, company)
    ).fetchone()
    items = conn.execute(
        "SELECT description, qty, unit_price FROM quote_items WHERE quote_id = ?",
        (quote["id"],)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM quote_payments WHERE quote_id = ?",
        (quote["id"],)
    ).fetchall()
    conn.close()

    file_path, file_name = build_contract_report_pdf(contract, quote, items, payments, company=company, project=project)
    return FileResponse(path=file_path, filename=file_name, media_type="application/pdf")



# ======================
# الموظفين (بدون تغيير)
# ======================

@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    conn = get_db()
    employees = conn.execute(
        "SELECT * FROM employees WHERE company = ?",
        (company,)
    ).fetchall()
    conn.close()

    rows = ""
    for e in employees:
        manage_html = "-"
        if not is_read_only_works_partner:
            manage_html = (
                f'<a href="/edit-employee/{e["id"]}?company={company}" class="action-btn">تعديل</a>'
                f'<a href="/delete-employee/{e["id"]}?company={company}" class="action-btn" onclick="return confirm(\'هل تريد حذف هذا الموظف؟\')">حذف</a>'
            )
        rows += f"""
<tr>
    <td>{e['id']}</td>
    <td>{e['name']}</td>
    <td>{e['role']}</td>
    <td>{manage_html}</td>
</tr>
"""

    create_card = ""
    if not is_read_only_works_partner:
        create_card = f"""
    <a href="/new-employee?company={company}" class="company-card {company}">
        <h2>&#10133; إضافة موظف</h2>
    </a>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<div class="dashboard">
{HOME_BUTTON}
    <h1>الموظفين</h1>

    {"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}
    {create_card}

    <br><br>

    <table border="1" style="background:white;margin:auto;width:80%;">
        <tr>
            <th>رقم</th>
            <th>الاسم</th>
            <th>الوظيفة</th>
            <th>إدارة</th>
        </tr>
        {rows if rows else "<tr><td colspan='4'>لا توجد موظفين</td></tr>"}
    </table>

    <br>
    <a href="/company/{company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.get("/new-employee", response_class=HTMLResponse)
def new_employee_form(request: Request, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<div class="dashboard">
{HOME_BUTTON}
    <h2>إضافة موظف</h2>

    <form action="/save-employee" method="post">
        <input type="hidden" name="company" value="{company}">
        الاسم: <input type="text" name="name"><br><br>
        الوظيفة: <input type="text" name="role"><br><br>
        <button type="submit" class="glass-btn gold-text">حفظ</button>
    </form>

    <br>
    <a href="/employees?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.post("/save-employee", response_class=HTMLResponse)
def save_employee(
    request: Request,
    company: str = Form(...),
    name: str = Form(...),
    role: str = Form(...)
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "INSERT INTO employees (name, role, company) VALUES (?, ?, ?)",
        (name, role, company)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/employees?company={company}", status_code=303)

@app.get("/edit-employee/{employee_id}", response_class=HTMLResponse)
def edit_employee_form(request: Request, employee_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    employee = conn.execute(
        "SELECT * FROM employees WHERE id = ? AND company = ?",
        (employee_id, company)
    ).fetchone()
    conn.close()

    if not employee:
        return "<h2>الموظف غير موجود</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<div class="dashboard">
{HOME_BUTTON}
    <h2>تعديل موظف</h2>

    <form action="/update-employee" method="post">
        <input type="hidden" name="employee_id" value="{employee_id}">
        <input type="hidden" name="company" value="{company}">
        الاسم: <input type="text" name="name" value="{employee['name']}" required><br><br>
        الوظيفة: <input type="text" name="role" value="{employee['role']}" required><br><br>
        <button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
    </form>

    <br>
    <a href="/employees?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.post("/update-employee")
def update_employee(
    request: Request,
    employee_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    role: str = Form(...)
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "UPDATE employees SET name = ?, role = ? WHERE id = ? AND company = ?",
        (name, role, employee_id, company)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/employees?company={company}", status_code=303)

@app.get("/delete-employee/{employee_id}")
def delete_employee(request: Request, employee_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute("DELETE FROM employees WHERE id = ? AND company = ?", (employee_id, company))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/employees?company={company}", status_code=303)

@app.get("/analyze-project/{project_id}", response_class=HTMLResponse)
def analyze_project_route(request: Request, project_id: int, company: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_any_section_access(request, company, {"daily_log", "expenses"})
    else:
        access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return deny_works_expenses_summary_access()
    if is_employee(access_result) and normalize_access_value(company) == "works":
        allowed_sections = get_employee_allowed_sections(access_result["id"], company)
        if allowed_sections in ({"daily_log"}, {"expenses"}):
            return access_denied_response(
                "ليس لديك صلاحية الوصول إلى تحليل المشروع",
                back_url=f"/projects?company={company}",
            )

    conn = get_db()
    try:
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND company = ?",
            (project_id, company)
        ).fetchone()
        if not project:
            return HTMLResponse(
                "<div class='inventory-note' style='margin:20px 0;'>المشروع غير موجود أو لا يمكن تحليله.</div>",
                status_code=404,
            )

        snapshot = build_project_financial_snapshot(conn, project)
        similar_projects = find_similar_project_snapshots(conn, snapshot)
        average_profit = (
            sum(item["profit"] for item in similar_projects) / len(similar_projects)
            if similar_projects else 0.0
        )
        average_profit_percentage = (
            sum(item["profit_percentage"] for item in similar_projects) / len(similar_projects)
            if similar_projects else 0.0
        )
        average_price_per_m2 = (
            sum(item["price_per_m2"] for item in similar_projects if item["price_per_m2"] > 0)
            / len([item for item in similar_projects if item["price_per_m2"] > 0])
            if any(item["price_per_m2"] > 0 for item in similar_projects) else 0.0
        )
        average_cost_per_m2 = (
            sum(item["cost_per_m2"] for item in similar_projects if item["cost_per_m2"] > 0)
            / len([item for item in similar_projects if item["cost_per_m2"] > 0])
            if any(item["cost_per_m2"] > 0 for item in similar_projects) else 0.0
        )
        average_profit_per_m2 = (
            sum(item["profit_per_m2"] for item in similar_projects)
            / len(similar_projects)
            if similar_projects else 0.0
        )
        summary = {
            "average_profit": average_profit,
            "average_profit_percentage": average_profit_percentage,
            "average_price_per_m2": average_price_per_m2,
            "average_cost_per_m2": average_cost_per_m2,
            "average_profit_per_m2": average_profit_per_m2,
            "best_project": max(similar_projects, key=lambda item: (item["profit_percentage"], item["profit"]), default=None),
            "worst_project": min(similar_projects, key=lambda item: (item["profit_percentage"], item["profit"]), default=None),
        }
        analysis_text = generate_project_analysis_text(snapshot, similar_projects, summary)
        return render_project_analysis_block(snapshot, similar_projects, summary, analysis_text)
    finally:
        conn.close()


@app.get("/analyze-project-items/{project_id}", response_class=HTMLResponse)
def analyze_project_items_route(request: Request, project_id: int, company: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_any_section_access(request, company, {"expenses"})
    else:
        access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if normalize_access_value(company) != "works":
        return access_denied_response(
            "تحليل البنود متاح لمشاريع المقاولات فقط",
            back_url=f"/project/{project_id}?company={company}",
        )
    if is_works_expenses_suppliers_employee(access_result, company):
        return deny_works_expenses_summary_access()
    if is_employee(access_result):
        allowed_sections = get_employee_allowed_sections(access_result["id"], company)
        if "expenses" not in allowed_sections:
            return access_denied_response(
                "ليس لديك صلاحية الوصول إلى تحليل البنود",
                back_url=f"/project/{project_id}?company={company}",
            )

    conn = get_db()
    try:
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND company = ?",
            (project_id, company)
        ).fetchone()
        if not project:
            return HTMLResponse(
                "<div class='inventory-note' style='margin:20px 0;'>المشروع غير موجود أو لا يمكن تحليل بنوده.</div>",
                status_code=404,
            )

        expenses = conn.execute(
            "SELECT title, amount, date FROM project_expenses WHERE project_id = ? ORDER BY date DESC, id DESC",
            (project_id,)
        ).fetchall()
        analysis = build_project_expense_item_analysis(expenses)
        return render_project_expense_item_analysis_block(project, analysis)
    finally:
        conn.close()


@app.get("/project/{project_id}", response_class=HTMLResponse)
def project_dashboard(request: Request, project_id: int, company: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_employee(user):
        access_result = ensure_employee_any_section_access(request, company, {"daily_log", "expenses"})
    else:
        access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_employee(access_result) and normalize_access_value(company) == "works":
        allowed_sections = get_employee_allowed_sections(access_result["id"], company)
        if allowed_sections == {"daily_log"}:
            return RedirectResponse(
                url=f"/project-daily?project_id={project_id}&company={company}",
                status_code=303,
            )
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    conn = get_db()
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        return "<h2>المشروع غير موجود</h2>"

    if is_works_expenses_suppliers_employee(access_result, company):
        conn.close()
        return f'''
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>{project['name']}</h1>

<div class="companies">

<a href="/project-expenses?project_id={project_id}&company={company}" class="company-card {company}">
<h2>المصروفات</h2>
</a>

<a href="/project-suppliers?project_id={project_id}&company={company}" class="company-card {company}">
<h2>الموردين</h2>
</a>

</div>

<br>

<a href="/projects?company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
'''

    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ?",
        (project["contract_id"],)
    ).fetchone()

    quote = None
    items = []
    if contract:
        quote = conn.execute(
            "SELECT * FROM quotes WHERE id = ?",
            (contract["quote_id"],)
        ).fetchone()
        if quote:
            items = conn.execute(
                "SELECT * FROM quote_items WHERE quote_id = ?",
                (quote["id"],)
            ).fetchall()

    expenses = conn.execute(
        "SELECT * FROM project_expenses WHERE project_id = ?",
        (project_id,)
    ).fetchall()
    conn.close()

    contract_total = sum(safe_float(i["qty"]) * safe_float(i["unit_price"]) for i in items)
    if contract_total <= 0 and "contract_value" in project.keys():
        contract_total = safe_float(project["contract_value"])
    expenses_total = sum(safe_float(e["amount"]) for e in expenses)
    profit = contract_total - expenses_total
    works_structured_details = ""
    if normalize_access_value(company) == "works":
        project_type_label = project["project_type"] or "غير محدد"
        work_type_label = project["work_type"] or "غير محدد"
        finish_level_label = project["finish_level"] or "غير محدد"
        area_label = f"{format_currency(project['area'])} متر مربع" if project["area"] not in (None, "") else "غير محدد"
        works_structured_details = f"""
<div class="inventory-note" style="margin:18px 0;text-align:right;">
<strong>نوع المشروع:</strong> {project_type_label}<br>
<strong>نوع العمل:</strong> {work_type_label}<br>
<strong>مستوى التشطيب:</strong> {finish_level_label}<br>
<strong>المساحة:</strong> {area_label}
</div>
"""

    action_bar = (
        "<div class='inventory-note' style='margin:18px 0;'>صلاحية شريك المقاولات للعرض فقط.</div>"
        if is_read_only_works_partner
        else (
            f'''<div style="display:flex;gap:10px;flex-wrap:wrap;margin:18px 0;">
<a href="/edit-project/{project_id}?company={company}" class="action-btn">تعديل المشروع</a>
<a href="/delete-project/{project_id}?company={company}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا المشروع وجميع بياناته المرتبطة؟')">حذف المشروع</a>
<button type="button" id="analyze-project-btn" class="glass-btn gold-text">تحليل المشروع بالذكاء</button>
{f'<a href="/analyze-project-items/{project_id}?company={company}" class="glass-btn gold-text">تحليل البنود</a>' if normalize_access_value(company) == "works" else ''}
</div>'''
        )
    )
    inventory_withdraw_html = ""
    if normalize_access_value(company) != "works":
        inventory_withdraw_html = f'<a href="/inventory?project_id={project_id}" class="company-card {company}"><h2>سحب من المستودع</h2></a>'

    return f'''
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>{project['name']}</h1>

<p>العميل: {project['client']}</p>
<p>الحالة: {project['status']}</p>
{works_structured_details}
{action_bar}

<br>

<h3>قيمة العقد</h3>
<p>{format_currency(contract_total)} ريال</p>

<h3>إجمالي المصروفات</h3>
<p>{format_currency(expenses_total)} ريال</p>

<h3>الربح الحالي</h3>
<p>{format_currency(profit)} ريال</p>

<div id="project-analysis-result"></div>

<br><br>

<div class="companies">

<a href="/project-expenses?project_id={project_id}&company={company}" class="company-card {company}">
<h2>المصروفات</h2>
</a>

<a href="/project-suppliers?project_id={project_id}&company={company}" class="company-card {company}">
<h2>الموردين</h2>
</a>

<a href="/project-equipment?project_id={project_id}&company={company}" class="company-card {company}">
<h2>المعدات</h2>
</a>

<a href="/project-daily?project_id={project_id}&company={company}" class="company-card {company}">
<h2>السجل اليومي</h2>
</a>

{inventory_withdraw_html}

</div>

<br>

<a href="/projects?company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
<script>
(function () {{
    const button = document.getElementById("analyze-project-btn");
    const container = document.getElementById("project-analysis-result");
    if (!button || !container) {{
        return;
    }}

    let loaded = false;
    button.addEventListener("click", async function () {{
        if (button.disabled) {{
            return;
        }}

        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = "جاري تحليل المشروع...";
        container.innerHTML = '<div class="inventory-note" style="margin:20px 0;">جاري تجهيز التحليل الذكي للمشروع...</div>';

        try {{
            const response = await fetch("/analyze-project/{project_id}?company={company}", {{
                credentials: "same-origin"
            }});
            const html = await response.text();
            container.innerHTML = html;
            loaded = true;
            button.textContent = "إعادة تحليل المشروع";
        }} catch (error) {{
            container.innerHTML = '<div class="inventory-note" style="margin:20px 0;border-color:rgba(127,29,29,0.45);color:#fecaca;">تعذر تحميل تحليل المشروع حاليًا.</div>';
            button.textContent = originalText;
        }} finally {{
            button.disabled = false;
            if (!loaded) {{
                button.textContent = originalText;
            }}
        }}
    }});
}})();
</script>
'''

@app.get("/project-expenses", response_class=HTMLResponse)
def project_expenses(request: Request, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    back_url = f"/project/{project_id}?company={company}"
    back_label = "⬅ رجوع للمشروع"
    if is_employee(access_result) and normalize_access_value(company) == "works":
        allowed_sections = get_employee_allowed_sections(access_result["id"], company)
        if allowed_sections == {"expenses"}:
            back_url = f"/projects?company={company}"
            back_label = "⬅ رجوع للمشاريع"

    conn = get_db()
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()
    suppliers = conn.execute(
        "SELECT * FROM project_suppliers WHERE project_id = ? ORDER BY name COLLATE NOCASE ASC, id DESC",
        (project_id,)
    ).fetchall()
    items = []
    if project and project["contract_id"]:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE id = ?",
            (project["contract_id"],)
        ).fetchone()
        if contract and contract["quote_id"]:
            items = conn.execute(
                "SELECT qty, unit_price FROM quote_items WHERE quote_id = ?",
                (contract["quote_id"],)
            ).fetchall()

    expenses = conn.execute(
        "SELECT * FROM project_expenses WHERE project_id = ? ORDER BY date DESC, id DESC",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""
    total = 0.0
    contract_total = sum(safe_float(item["qty"]) * safe_float(item["unit_price"]) for item in items)
    if contract_total <= 0 and project and "contract_value" in project.keys():
        contract_total = safe_float(project["contract_value"])
    remaining = contract_total - sum(safe_float(e["amount"]) for e in expenses)
    spend_ratio = (sum(safe_float(e["amount"]) for e in expenses) / contract_total * 100) if contract_total > 0 else 0.0
    feedback_html = render_page_feedback(
        request.query_params.get("message", ""),
        request.query_params.get("error", ""),
    )
    category_options = build_project_expense_category_options()
    category_selector = build_project_expense_category_selector()
    payment_method_options = "".join(
        f'<option value="{escape(item)}">{escape(item)}</option>'
        for item in PROJECT_EXPENSE_PAYMENT_METHODS
    )
    payment_status_options = "".join(
        f'<option value="{escape(item)}">{escape(item)}</option>'
        for item in PROJECT_EXPENSE_PAYMENT_STATUSES
    )
    supplier_datalist = build_project_supplier_datalist(suppliers)
    supplier_hint = (
        "<div style='color:rgba(255,255,255,0.78);font-size:13px;'>يمكن اختيار اسم مورد موجود أو كتابة اسم جديد.</div>"
        if supplier_datalist else
        "<div style='color:rgba(255,255,255,0.78);font-size:13px;'>لا يوجد موردون مسجلون بعد، يمكنك كتابة الاسم مباشرة.</div>"
    )
    project_name = escape(project["name"] or f"مشروع رقم {project_id}") if project else f"مشروع رقم {project_id}"
    summary_remaining_class = "finance-card-positive" if remaining >= 0 else "finance-card-negative"
    show_actions = not is_read_only_works_partner

    for e in expenses:
        amount = safe_float(e["amount"])
        total += amount
        attachment_html = (
            f'<a href="{escape(e["attachment_path"])}" target="_blank" class="expense-table-btn expense-table-btn-attachment">عرض المرفق</a>'
            if ("attachment_path" in e.keys() and e["attachment_path"]) else
            "-"
        )
        actions_html = "-"
        if show_actions:
            actions_html = (
                f'''<div class="expense-table-actions"><a href="/edit-project-expense/{e['id']}?project_id={project_id}&company={company}" class="expense-table-btn">تعديل</a>'''
                f'''<a href="/delete-project-expense/{e['id']}?project_id={project_id}&company={company}" class="expense-table-btn expense-table-btn-danger" onclick="return confirm('هل تريد حذف هذا المصروف؟')">حذف</a></div>'''
            )
        rows += f"""
        <tr class="expense-table-row">
            <td class="expense-cell-date">{escape(e['date'] or '-')}</td>
            <td class="expense-cell-title">
                <strong>{escape(e['title'] or '-')}</strong>
                {f"<br><span class='expense-cell-meta'>مرجع: {escape(e['invoice_reference'])}</span>" if ('invoice_reference' in e.keys() and e['invoice_reference']) else ""}
                {f"<br><span class='expense-cell-meta'>{escape(e['notes'])}</span>" if ('notes' in e.keys() and e['notes']) else ""}
            </td>
            <td>{build_project_expense_category_badge(e)}</td>
            <td>{escape((e['vendor'] or '-')) if 'vendor' in e.keys() else '-'}</td>
            <td>{escape((e['payment_method'] or '-')) if 'payment_method' in e.keys() else '-'}</td>
            <td>{build_project_expense_payment_status_badge(e['payment_status'] if 'payment_status' in e.keys() else '')}</td>
            <td class="expense-cell-amount">{format_currency(amount)} ريال</td>
            <td>{attachment_html}</td>
            <td>{actions_html}</td>
        </tr>
        """

    project_expense_form_html = ""
    if not is_read_only_works_partner:
        project_expense_form_html = f"""
    <div class="inventory-panel inventory-table-panel expense-form-panel">
        <div class="expense-panel-head">
            <div>
                <h3>إضافة مصروف</h3>
                <p>تنسيق ذكي وسريع يساعد المحاسب على إدخال بيانات أوضح وبأقل كتابة ممكنة.</p>
            </div>
        </div>
        <form action="/save-expense" method="post" enctype="multipart/form-data" id="project-expense-form" class="expense-form">
            <input type="hidden" name="project_id" value="{project_id}">
            <input type="hidden" name="company" value="{company}">

            <div class="expense-form-grid">
                <div class="expense-form-wide expense-category-field">
                    <label>تصنيف المصروف</label>
                    {category_selector}
                    <select name="category" id="expense-category" class="expense-category-native-select" required>
                        {category_options}
                    </select>
                </div>
                <div id="other-type-wrapper" class="expense-other-type-card" hidden>
                    <label>نوع الأخرى</label>
                    <input type="text" name="other_type" id="expense-other-type" placeholder="اكتب نوع المصروف">
                </div>
                <div>
                    <label>بيان/اسم المصروف</label>
                    <input type="text" name="title" id="expense-title" list="expense-name-suggestions" placeholder="مثال: شراء اسمنت" required>
                    <datalist id="expense-name-suggestions"></datalist>
                </div>
                <div>
                    <label>المبلغ</label>
                    <input type="number" step="0.01" min="0" name="amount" required>
                </div>
                <div>
                    <label>التاريخ</label>
                    <input type="date" name="expense_date" value="{date.today().isoformat()}" required>
                </div>
                <div>
                    <label>المورد</label>
                    <input type="text" name="vendor" list="project-suppliers-list" placeholder="اسم المورد">
                    <datalist id="project-suppliers-list">{supplier_datalist}</datalist>
                    {supplier_hint}
                </div>
                <div>
                    <label>طريقة الدفع</label>
                    <select name="payment_method">
                        <option value="">اختر طريقة الدفع</option>
                        {payment_method_options}
                    </select>
                </div>
                <div>
                    <label>حالة السداد</label>
                    <select name="payment_status">
                        <option value="">اختر حالة السداد</option>
                        {payment_status_options}
                    </select>
                </div>
                <div>
                    <label>رقم الفاتورة / المرجع</label>
                    <input type="text" name="invoice_reference" placeholder="مثال: INV-1042">
                </div>
                <div>
                    <label>مرفق</label>
                    <input type="file" name="attachment" accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx">
                </div>
                <div class="expense-form-wide">
                    <label>اقتراحات سريعة لاسم المصروف</label>
                    <div id="expense-suggestion-groups" class="expense-suggestion-shell">
                        <div class="expense-suggestion-empty">اختر التصنيف لتظهر أمثلة سريعة تساعد على سرعة الإدخال.</div>
                        {build_project_expense_name_suggestion_buttons()}
                    </div>
                </div>
                <div class="expense-form-wide">
                    <label>ملاحظات</label>
                    <textarea name="notes" rows="3" placeholder="أي تفاصيل إضافية للمحاسب أو للإرجاع لاحقًا"></textarea>
                </div>
            </div>

            <div class="expense-form-actions">
                <button type="submit" class="glass-btn gold-text expense-primary-btn">حفظ المصروف</button>
            </div>
        </form>
    </div>
    """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard expense-page">
    <div class="finance-page-header">
        <div>
            <h1>مصروفات المشروع</h1>
            <p>{project_name}</p>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
            <a href="/project-expenses-report?project_id={project_id}&company={company}" class="glass-btn expense-primary-btn">تحميل تقرير PDF</a>
            <a href="{back_url}" class="glass-btn back-btn expense-page-back-btn">{back_label}</a>
        </div>
    </div>

    {feedback_html}

    {"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

    <div class="finance-summary-grid expense-summary-grid">
        <div class="finance-card expense-summary-card expense-summary-card-contract">
            <span>قيمة العقد</span>
            <strong>{format_currency(contract_total)} ريال</strong>
        </div>
        <div class="finance-card finance-card-expense expense-summary-card expense-summary-card-expense">
            <span>إجمالي المصروفات</span>
            <strong>{format_currency(total)} ريال</strong>
        </div>
        <div class="finance-card {summary_remaining_class} expense-summary-card expense-summary-card-remaining">
            <span>المتبقي</span>
            <strong>{format_currency(remaining)} ريال</strong>
        </div>
        <div class="finance-card expense-summary-card expense-summary-card-ratio">
            <span>نسبة الصرف</span>
            <strong>{spend_ratio:.1f}%</strong>
        </div>
    </div>

    {project_expense_form_html}

    <div class="inventory-table-panel expense-table-panel">
        <div class="inventory-table-head expense-table-head">
            <h3 style="margin:0;color:#ffffff;">سجل المصروفات</h3>
            <div class="expense-table-count">عدد السجلات: {len(expenses)}</div>
        </div>

        <div class="expense-table-scroll">
            <table class="finance-table expense-table">
                <tr>
                    <th>التاريخ</th>
                    <th>البيان</th>
                    <th>التصنيف</th>
                    <th>المورد</th>
                    <th>طريقة الدفع</th>
                    <th>حالة السداد</th>
                    <th>المبلغ</th>
                    <th>المرفق</th>
                    <th>الإدارة</th>
                </tr>

                {rows if rows else "<tr><td colspan='9'>لا توجد مصروفات مسجلة حتى الآن</td></tr>"}

                <tr class="expense-table-total-row">
                    <td colspan="6"><strong>الإجمالي</strong></td>
                    <td><strong>{format_currency(total)} ريال</strong></td>
                    <td colspan="2"></td>
                </tr>
            </table>
        </div>
    </div>
</div>
<script>
(function () {{
    const categoryField = document.getElementById("expense-category");
    const categorySelector = document.getElementById("expense-category-selector");
    const titleField = document.getElementById("expense-title");
    const datalist = document.getElementById("expense-name-suggestions");
    const groupsContainer = document.getElementById("expense-suggestion-groups");
    const emptyState = groupsContainer ? groupsContainer.querySelector(".expense-suggestion-empty") : null;
    const otherTypeWrapper = document.getElementById("other-type-wrapper");
    const otherTypeField = document.getElementById("expense-other-type");
    const suggestionMap = {json.dumps(PROJECT_EXPENSE_NAME_SUGGESTIONS, ensure_ascii=False)};

    function updateOtherType() {{
        const isOther = categoryField && categoryField.value === "أخرى";
        if (!otherTypeWrapper || !otherTypeField) {{
            return;
        }}
        otherTypeWrapper.hidden = !isOther;
        otherTypeWrapper.classList.toggle("is-visible", !!isOther);
        otherTypeField.required = !!isOther;
        if (!isOther) {{
            otherTypeField.value = "";
        }}
    }}

    function updateCategoryChips() {{
        if (!categorySelector || !categoryField) {{
            return;
        }}
        categorySelector.querySelectorAll(".expense-category-chip").forEach((chip) => {{
            const isSelected = chip.dataset.categoryValue === categoryField.value;
            chip.classList.toggle("is-selected", isSelected);
            chip.setAttribute("aria-pressed", isSelected ? "true" : "false");
        }});
    }}

    function updateSuggestions() {{
        if (!categoryField || !datalist || !groupsContainer) {{
            return;
        }}
        const category = categoryField.value || "";
        const suggestions = suggestionMap[category] || [];
        datalist.innerHTML = suggestions.map((item) => '<option value="' + item.replace(/"/g, '&quot;') + '"></option>').join("");

        groupsContainer.querySelectorAll(".expense-suggestion-group").forEach((group) => {{
            group.hidden = group.dataset.category !== category;
        }});

        if (emptyState) {{
            emptyState.style.display = suggestions.length ? "none" : "block";
        }}
    }}

    if (groupsContainer) {{
        groupsContainer.addEventListener("click", function (event) {{
            const button = event.target.closest(".expense-suggestion-chip");
            if (!button || !titleField) {{
                return;
            }}
            titleField.value = button.dataset.suggestion || "";
            titleField.focus();
        }});
    }}

    if (categorySelector && categoryField) {{
        categorySelector.addEventListener("click", function (event) {{
            const chip = event.target.closest(".expense-category-chip");
            if (!chip) {{
                return;
            }}
            categoryField.value = chip.dataset.categoryValue || "";
            updateCategoryChips();
            updateOtherType();
            updateSuggestions();
        }});
    }}

    if (categoryField) {{
        categoryField.addEventListener("change", function () {{
            updateCategoryChips();
            updateOtherType();
            updateSuggestions();
        }});
        updateCategoryChips();
        updateOtherType();
        updateSuggestions();
    }}
}})();
</script>
"""

@app.post("/save-expense")
def save_expense(
    request: Request,
    project_id: int = Form(...),
    company: str = Form(...),
    title: str = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
    other_type: str = Form(""),
    expense_date: str = Form(""),
    vendor: str = Form(""),
    payment_method: str = Form(""),
    payment_status: str = Form(""),
    invoice_reference: str = Form(""),
    notes: str = Form(""),
    attachment: UploadFile = File(None),
):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    if category not in PROJECT_EXPENSE_CATEGORIES:
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="يرجى اختيار تصنيف صحيح للمصروف",
            ),
            status_code=303
        )
    cleaned_other_type = other_type.strip()
    cleaned_title = title.strip()
    if not cleaned_title:
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="بيان/اسم المصروف مطلوب",
            ),
            status_code=303
        )
    if payment_method.strip() and payment_method.strip() not in PROJECT_EXPENSE_PAYMENT_METHODS:
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="طريقة الدفع المحددة غير صحيحة",
            ),
            status_code=303
        )
    if payment_status.strip() and payment_status.strip() not in PROJECT_EXPENSE_PAYMENT_STATUSES:
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="حالة السداد المحددة غير صحيحة",
            ),
            status_code=303
        )
    if category == "أخرى" and not cleaned_other_type:
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="حقل نوع الأخرى مطلوب عند اختيار التصنيف أخرى",
            ),
            status_code=303
        )
    conn = get_db()
    attachment_path = save_project_expense_attachment(attachment)

    conn.execute(
        """
        INSERT INTO project_expenses (
            project_id, title, amount, date, category, other_type, vendor,
            payment_method, payment_status, invoice_reference, attachment_path, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            cleaned_title,
            amount,
            expense_date or date.today().isoformat(),
            category,
            cleaned_other_type if category == "أخرى" else "",
            vendor.strip(),
            payment_method.strip(),
            payment_status.strip(),
            invoice_reference.strip(),
            attachment_path,
            notes.strip(),
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=build_redirect_url(
            f"/project-expenses?project_id={project_id}&company={company}",
            message="تم حفظ المصروف بنجاح",
        ),
        status_code=303
    )


@app.get("/project-expenses-report")
def project_expenses_report(request: Request, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()
    if not project:
        conn.close()
        return HTMLResponse("<h2>المشروع غير موجود</h2>", status_code=404)

    items = []
    if project["contract_id"]:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE id = ?",
            (project["contract_id"],)
        ).fetchone()
        if contract and contract["quote_id"]:
            items = conn.execute(
                "SELECT qty, unit_price FROM quote_items WHERE quote_id = ?",
                (contract["quote_id"],)
            ).fetchall()

    expenses = conn.execute(
        "SELECT * FROM project_expenses WHERE project_id = ? ORDER BY date DESC, id DESC",
        (project_id,)
    ).fetchall()
    conn.close()

    contract_total = sum(safe_float(item["qty"]) * safe_float(item["unit_price"]) for item in items)
    if contract_total <= 0 and "contract_value" in project.keys():
        contract_total = safe_float(project["contract_value"])

    file_path, file_name = build_project_expenses_report_pdf(project, expenses, contract_total, company=company)
    return FileResponse(
        path=file_path,
        filename=file_name,
        media_type="application/pdf",
    )


@app.get("/edit-project-expense/{expense_id}", response_class=HTMLResponse)
def edit_project_expense(request: Request, expense_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    expense = conn.execute(
        "SELECT * FROM project_expenses WHERE id = ? AND project_id = ?",
        (expense_id, project_id)
    ).fetchone()
    suppliers = conn.execute(
        "SELECT * FROM project_suppliers WHERE project_id = ? ORDER BY name COLLATE NOCASE ASC, id DESC",
        (project_id,)
    ).fetchall()
    conn.close()

    if not expense:
        return "<h2>المصروف غير موجود</h2>"

    feedback_html = render_page_feedback(
        request.query_params.get("message", ""),
        request.query_params.get("error", ""),
    )
    supplier_datalist = build_project_supplier_datalist(suppliers)
    category_options = build_project_expense_category_options(expense["category"] if "category" in expense.keys() else "")
    category_selector = build_project_expense_category_selector(expense["category"] if "category" in expense.keys() else "")
    payment_method_options = "".join(
        f'<option value="{escape(item)}" {"selected" if (expense["payment_method"] or "") == item else ""}>{escape(item)}</option>'
        for item in PROJECT_EXPENSE_PAYMENT_METHODS
    )
    payment_status_options = "".join(
        f'<option value="{escape(item)}" {"selected" if (expense["payment_status"] or "") == item else ""}>{escape(item)}</option>'
        for item in PROJECT_EXPENSE_PAYMENT_STATUSES
    )
    current_attachment_html = (
        f'<a href="{escape(expense["attachment_path"] or "")}" target="_blank" class="expense-table-btn expense-table-btn-attachment">عرض المرفق الحالي</a>'
        if ("attachment_path" in expense.keys() and expense["attachment_path"]) else
        "<span class='expense-inline-hint'>لا يوجد مرفق حالي</span>"
    )

    return f"""
  <meta charset="UTF-8">
  <link rel="stylesheet" href="/static/style.css">
  <body class="system-dark">
{HOME_BUTTON}
<div class="dashboard expense-page expense-edit-page">

<div class="finance-page-header">
<div>
<h1>تعديل المصروف</h1>
<p>تحديث نفس سجل المصروف مع الحفاظ على الهيكل المحاسبي المنظم.</p>
</div>
<a href="/project-expenses?project_id={project_id}&company={company}" class="glass-btn back-btn expense-page-back-btn">⬅ رجوع</a>
</div>
{feedback_html}

<div class="inventory-panel inventory-table-panel expense-form-panel">
<form action="/update-project-expense/{expense_id}" method="post" enctype="multipart/form-data" class="expense-form">
<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

<div class="expense-form-grid">
<div class="expense-form-wide expense-category-field">
<label>تصنيف المصروف</label>
{category_selector}
<select name="category" id="expense-category" class="expense-category-native-select" required>
{category_options}
</select>
</div>

<div id="other-type-wrapper" class="expense-other-type-card {'is-visible' if (expense['category'] or '') == 'أخرى' else ''}" {"hidden" if (expense["category"] or "") != "أخرى" else ""}>
<label>نوع الأخرى</label>
<input type="text" name="other_type" id="expense-other-type" value="{escape(expense['other_type'] or '')}" {"required" if (expense['category'] or '') == 'أخرى' else ""}>
</div>

<div>
<label>بيان/اسم المصروف</label>
<input type="text" name="title" id="expense-title" list="expense-name-suggestions" value="{escape(expense['title'] or '')}" required>
<datalist id="expense-name-suggestions"></datalist>
</div>

<div>
<label>المبلغ</label>
<input type="number" step="0.01" min="0" name="amount" value="{expense['amount'] or 0}" required>
</div>

<div>
<label>التاريخ</label>
<input type="date" name="expense_date" value="{escape(expense['date'] or date.today().isoformat())}" required>
</div>

<div>
<label>المورد</label>
<input type="text" name="vendor" list="project-suppliers-list" value="{escape(expense['vendor'] or '')}">
<datalist id="project-suppliers-list">{supplier_datalist}</datalist>
</div>

<div>
<label>طريقة الدفع</label>
<select name="payment_method">
<option value="">اختر طريقة الدفع</option>
{payment_method_options}
</select>
</div>

<div>
<label>حالة السداد</label>
<select name="payment_status">
<option value="">اختر حالة السداد</option>
{payment_status_options}
</select>
</div>

<div>
<label>رقم الفاتورة / المرجع</label>
<input type="text" name="invoice_reference" value="{escape(expense['invoice_reference'] or '')}">
</div>

<div>
<label>مرفق جديد</label>
<input type="file" name="attachment" accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx">
{current_attachment_html}
</div>

<div class="expense-form-wide">
<label>اقتراحات سريعة لاسم المصروف</label>
<div id="expense-suggestion-groups" class="expense-suggestion-shell">
<div class="expense-suggestion-empty">اختر التصنيف لتظهر الاقتراحات السريعة.</div>
{build_project_expense_name_suggestion_buttons()}
</div>
</div>

<div class="expense-form-wide">
<label>ملاحظات</label>
<textarea name="notes" rows="3">{escape(expense['notes'] or '')}</textarea>
</div>
</div>

<div class="expense-form-actions">
<button type="submit" class="glass-btn gold-text expense-primary-btn">حفظ التعديل</button>
</div>
</form>
</div>

</div>
<script>
(function () {{
    const categoryField = document.getElementById("expense-category");
    const categorySelector = document.getElementById("expense-category-selector");
    const titleField = document.getElementById("expense-title");
    const datalist = document.getElementById("expense-name-suggestions");
    const groupsContainer = document.getElementById("expense-suggestion-groups");
    const emptyState = groupsContainer ? groupsContainer.querySelector(".expense-suggestion-empty") : null;
    const otherTypeWrapper = document.getElementById("other-type-wrapper");
    const otherTypeField = document.getElementById("expense-other-type");
    const suggestionMap = {json.dumps(PROJECT_EXPENSE_NAME_SUGGESTIONS, ensure_ascii=False)};

    function updateOtherType() {{
        const isOther = categoryField && categoryField.value === "أخرى";
        if (!otherTypeWrapper || !otherTypeField) {{
            return;
        }}
        otherTypeWrapper.hidden = !isOther;
        otherTypeWrapper.classList.toggle("is-visible", !!isOther);
        otherTypeField.required = !!isOther;
        if (!isOther) {{
            otherTypeField.value = "";
        }}
    }}

    function updateCategoryChips() {{
        if (!categorySelector || !categoryField) {{
            return;
        }}
        categorySelector.querySelectorAll(".expense-category-chip").forEach((chip) => {{
            const isSelected = chip.dataset.categoryValue === categoryField.value;
            chip.classList.toggle("is-selected", isSelected);
            chip.setAttribute("aria-pressed", isSelected ? "true" : "false");
        }});
    }}

    function updateSuggestions() {{
        if (!categoryField || !datalist || !groupsContainer) {{
            return;
        }}
        const category = categoryField.value || "";
        const suggestions = suggestionMap[category] || [];
        datalist.innerHTML = suggestions.map((item) => '<option value="' + item.replace(/"/g, '&quot;') + '"></option>').join("");

        groupsContainer.querySelectorAll(".expense-suggestion-group").forEach((group) => {{
            group.hidden = group.dataset.category !== category;
        }});

        if (emptyState) {{
            emptyState.style.display = suggestions.length ? "none" : "block";
        }}
    }}

    if (groupsContainer) {{
        groupsContainer.addEventListener("click", function (event) {{
            const button = event.target.closest(".expense-suggestion-chip");
            if (!button || !titleField) {{
                return;
            }}
            titleField.value = button.dataset.suggestion || "";
            titleField.focus();
        }});
    }}

    if (categorySelector && categoryField) {{
        categorySelector.addEventListener("click", function (event) {{
            const chip = event.target.closest(".expense-category-chip");
            if (!chip) {{
                return;
            }}
            categoryField.value = chip.dataset.categoryValue || "";
            updateCategoryChips();
            updateOtherType();
            updateSuggestions();
        }});
    }}

    if (categoryField) {{
        categoryField.addEventListener("change", function () {{
            updateCategoryChips();
            updateOtherType();
            updateSuggestions();
        }});
        updateCategoryChips();
        updateOtherType();
        updateSuggestions();
    }}
}})();
</script>
"""


@app.post("/update-project-expense/{expense_id}")
def update_project_expense(
    request: Request,
    expense_id: int,
    project_id: int = Form(...),
    company: str = Form(...),
    title: str = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
    other_type: str = Form(""),
    expense_date: str = Form(""),
    vendor: str = Form(""),
    payment_method: str = Form(""),
    payment_status: str = Form(""),
    invoice_reference: str = Form(""),
    notes: str = Form(""),
    attachment: UploadFile = File(None),
):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    if category not in PROJECT_EXPENSE_CATEGORIES:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/project-expenses?project_id={project_id}&company={company}",
                error="يرجى اختيار تصنيف صحيح للمصروف",
            ),
            status_code=303
        )
    cleaned_other_type = other_type.strip()
    cleaned_title = title.strip()
    if not cleaned_title:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/edit-project-expense/{expense_id}?project_id={project_id}&company={company}",
                error="بيان/اسم المصروف مطلوب",
            ),
            status_code=303
        )
    if payment_method.strip() and payment_method.strip() not in PROJECT_EXPENSE_PAYMENT_METHODS:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/edit-project-expense/{expense_id}?project_id={project_id}&company={company}",
                error="طريقة الدفع المحددة غير صحيحة",
            ),
            status_code=303
        )
    if payment_status.strip() and payment_status.strip() not in PROJECT_EXPENSE_PAYMENT_STATUSES:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/edit-project-expense/{expense_id}?project_id={project_id}&company={company}",
                error="حالة السداد المحددة غير صحيحة",
            ),
            status_code=303
        )
    if category == "أخرى" and not cleaned_other_type:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/edit-project-expense/{expense_id}?project_id={project_id}&company={company}",
                error="حقل نوع الأخرى مطلوب عند اختيار التصنيف أخرى",
            ),
            status_code=303
        )
    current_record = conn.execute(
        "SELECT attachment_path FROM project_expenses WHERE id = ? AND project_id = ?",
        (expense_id, project_id)
    ).fetchone()
    attachment_path = current_record["attachment_path"] if current_record and "attachment_path" in current_record.keys() else ""
    new_attachment_path = save_project_expense_attachment(attachment)
    if new_attachment_path:
        delete_project_daily_attachment_file(attachment_path or "")
        attachment_path = new_attachment_path
    conn.execute(
        """
        UPDATE project_expenses
        SET title = ?, amount = ?, date = ?, category = ?, other_type = ?, vendor = ?,
            payment_method = ?, payment_status = ?, invoice_reference = ?, attachment_path = ?, notes = ?
        WHERE id = ? AND project_id = ?
        """,
        (
            cleaned_title,
            amount,
            expense_date or date.today().isoformat(),
            category,
            cleaned_other_type if category == "أخرى" else "",
            vendor.strip(),
            payment_method.strip(),
            payment_status.strip(),
            invoice_reference.strip(),
            attachment_path,
            notes.strip(),
            expense_id,
            project_id,
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(
            f"/project-expenses?project_id={project_id}&company={company}",
            message="تم تحديث المصروف بنجاح",
        ),
        status_code=303
    )


@app.get("/delete-project-expense/{expense_id}")
def delete_project_expense(request: Request, expense_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    expense = conn.execute(
        "SELECT attachment_path FROM project_expenses WHERE id = ? AND project_id = ?",
        (expense_id, project_id)
    ).fetchone()
    conn.execute(
        "DELETE FROM project_expenses WHERE id = ? AND project_id = ?",
        (expense_id, project_id)
    )
    conn.commit()
    conn.close()
    if expense and "attachment_path" in expense.keys():
        delete_project_daily_attachment_file(expense["attachment_path"] or "")
    return RedirectResponse(
        url=build_redirect_url(
            f"/project-expenses?project_id={project_id}&company={company}",
            message="تم حذف المصروف بنجاح",
        ),
        status_code=303
    )

@app.get("/project-daily", response_class=HTMLResponse)
def project_daily(request: Request, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "daily_log")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    back_url = f"/project/{project_id}?company={company}"
    back_label = "⬅ رجوع للمشروع"
    if is_employee(access_result) and normalize_access_value(company) == "works":
        allowed_sections = get_employee_allowed_sections(access_result["id"], company)
        if allowed_sections == {"daily_log"}:
            back_url = f"/projects?company={company}"
            back_label = "⬅ رجوع للمشاريع"

    conn = get_db()

    reports = conn.execute(
        "SELECT * FROM project_daily WHERE project_id = ? ORDER BY id DESC",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for r in reports:
        attachment_path = r["attachment_path"] or ""
        attachment_html = "لا يوجد"
        if attachment_path:
            lower_path = attachment_path.lower()
            if lower_path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                attachment_html = (
                    f'<a href="{attachment_path}" target="_blank">عرض المرفق</a><br>'
                    f'<img src="{attachment_path}" alt="المرفق" style="max-width:140px;margin-top:8px;border-radius:10px;">'
                )
            else:
                attachment_html = f'<a href="{attachment_path}" target="_blank">تحميل الملف</a>'
        rows += f"""
        <tr>
            <td>{r['date']}</td>
            <td>{r['workers']}</td>
            <td>{r['report']}</td>
            <td>{attachment_html}</td>
            <td>{"-" if is_read_only_works_partner else f'''<a href="/edit-project-daily/{r['id']}?project_id={project_id}&company={company}" class="action-btn">تعديل</a><a href="/delete-project-daily/{r['id']}?project_id={project_id}&company={company}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا السجل اليومي؟')">حذف</a>'''}</td>
        </tr>
        """

    daily_form_html = ""
    if not is_read_only_works_partner:
        daily_form_html = f"""
<form action="/save-daily" method="post" enctype="multipart/form-data">

<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

عدد العمال:
<input type="number" name="workers" required>

<br><br>

تقرير العمل اليومي:
<br>
<textarea name="report" rows="5" style="width:400px"></textarea>

<br><br>

المرفق:
<input type="file" name="attachment">

<br><br>

<button type="submit" class="glass-btn gold-text">حفظ التقرير</button>

</form>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>السجل اليومي</h1>

{"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

{daily_form_html}

<br><br>

<table border="1" style="background:white;margin:auto;width:80%">

<tr>
<th>التاريخ</th>
<th>العمال</th>
<th>التقرير</th>
<th>المرفق</th>
<th>الإدارة</th>
</tr>

{rows if rows else "<tr><td colspan='5'>لا يوجد تقارير</td></tr>"}

</table>

<br>

<a href="{back_url}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-daily")
def save_daily(
    request: Request,
    project_id: int = Form(...),
    company: str = Form(...),
    workers: int = Form(...),
    report: str = Form(...),
    attachment: UploadFile = File(None)
):
    access_result = ensure_employee_section_access(request, company, "daily_log")
    if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard

    conn = get_db()
    attachment_path = save_project_daily_attachment(attachment)

    conn.execute(
        "INSERT INTO project_daily (project_id, report, workers, date, attachment_path) VALUES (?, ?, ?, DATE('now'), ?)",
        (project_id, report, workers, attachment_path)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/project-daily?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/edit-project-daily/{daily_id}", response_class=HTMLResponse)
def edit_project_daily(request: Request, daily_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "daily_log")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    report = conn.execute(
        "SELECT * FROM project_daily WHERE id = ? AND project_id = ?",
        (daily_id, project_id)
    ).fetchone()
    conn.close()

    if not report:
        return "<h2>السجل اليومي غير موجود</h2>"

    current_attachment_html = "لا يوجد"
    if report["attachment_path"]:
        current_attachment_html = f'<a href="{report["attachment_path"]}" target="_blank">عرض المرفق الحالي</a>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>تعديل السجل اليومي</h1>

<form action="/update-project-daily/{daily_id}" method="post" enctype="multipart/form-data">
<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

<label>عدد العمال</label>
<input type="number" name="workers" value="{report['workers']}" required>

<label>التقرير</label>
<textarea name="report" rows="5" style="width:400px">{report['report']}</textarea>

<label>المرفق الحالي</label>
<div class="inventory-note">{current_attachment_html}</div>

<label>استبدال المرفق</label>
<input type="file" name="attachment">

<br><br>
<button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
</form>

<br>
<a href="/project-daily?project_id={project_id}&company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.post("/update-project-daily/{daily_id}")
def update_project_daily(
    request: Request,
    daily_id: int,
    project_id: int = Form(...),
    company: str = Form(...),
    workers: int = Form(...),
    report: str = Form(...),
    attachment: UploadFile = File(None),
):
    access_result = ensure_employee_section_access(request, company, "daily_log")
    if isinstance(access_result, RedirectResponse) or isinstance(access_result, HTMLResponse):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    current_record = conn.execute(
        "SELECT * FROM project_daily WHERE id = ? AND project_id = ?",
        (daily_id, project_id)
    ).fetchone()
    if not current_record:
        conn.close()
        return RedirectResponse(
            url=f"/project-daily?project_id={project_id}&company={company}",
            status_code=303
        )

    attachment_path = current_record["attachment_path"] or ""
    new_attachment_path = save_project_daily_attachment(attachment)
    if new_attachment_path:
        delete_project_daily_attachment_file(attachment_path)
        attachment_path = new_attachment_path

    conn.execute(
        "UPDATE project_daily SET workers = ?, report = ?, attachment_path = ? WHERE id = ? AND project_id = ?",
        (workers, report, attachment_path, daily_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-daily?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/delete-project-daily/{daily_id}")
def delete_project_daily(request: Request, daily_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "daily_log")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    record = conn.execute(
        "SELECT attachment_path FROM project_daily WHERE id = ? AND project_id = ?",
        (daily_id, project_id)
    ).fetchone()
    if record:
        delete_project_daily_attachment_file(record["attachment_path"] or "")
    conn.execute(
        "DELETE FROM project_daily WHERE id = ? AND project_id = ?",
        (daily_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-daily?project_id={project_id}&company={company}",
        status_code=303
    )

@app.get("/project-equipment", response_class=HTMLResponse)
def project_equipment(request: Request, project_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    daily_scope_redirect = ensure_works_daily_log_scope(access_result, project_id, company)
    if not isinstance(daily_scope_redirect, sqlite3.Row):
        return daily_scope_redirect
    scope_redirect = ensure_works_expenses_suppliers_scope(access_result, project_id, company)
    if not isinstance(scope_redirect, sqlite3.Row):
        return scope_redirect
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM project_equipment WHERE project_id = ?",
        (project_id,)
    ).fetchall()
    conn.close()

    rows = ""
    for e in equipment:
        manage_html = "-"
        if not is_read_only_works_partner:
            manage_html = (
                f'<a href="/edit-project-equipment/{e["id"]}?project_id={project_id}&company={company}" class="action-btn">تعديل</a>'
                f'<a href="/delete-project-equipment/{e["id"]}?project_id={project_id}&company={company}" class="action-btn delete-btn" onclick="return confirm(\'هل تريد حذف هذه المعدة؟\')">حذف</a>'
            )
        rows += f"""
        <tr>
            <td>{e['name']}</td>
            <td>{e['qty']}</td>
            <td>{e['status']}</td>
            <td>{e['date']}</td>
            <td>{manage_html}</td>
        </tr>
        """

    create_form = ""
    if not is_read_only_works_partner:
        create_form = f"""
<form action="/save-equipment" method="post">

<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

اسم المعدة:
<input type="text" name="name" required>

<br><br>

الكمية:
<input type="number" name="qty" required>

<br><br>

الحالة:
<select name="status">
<option value="تعمل">تعمل</option>
<option value="متوقفة">متوقفة</option>
<option value="صيانة">صيانة</option>
</select>

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة المعدة</button>

</form>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>معدات المشروع</h1>

{"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

{create_form}

<br><br>

<table border="1" style="background:white;margin:auto;width:70%">

<tr>
<th>المعدة</th>
<th>الكمية</th>
<th>الحالة</th>
<th>التاريخ</th>
<th>الإدارة</th>
</tr>

{rows if rows else "<tr><td colspan='5'>لا توجد معدات</td></tr>"}

</table>

<br>

<a href="/project/{project_id}?company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-equipment")
def save_equipment(
    request: Request,
    project_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    qty: int = Form(...),
    status: str = Form(...)
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url=f"/project-expenses?project_id={project_id}&company={company}", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard

    conn = get_db()

    conn.execute(
        "INSERT INTO project_equipment (project_id, name, qty, status, date) VALUES (?, ?, ?, ?, DATE('now'))",
        (project_id, name, qty, status)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/project-equipment?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/edit-project-equipment/{equipment_id}", response_class=HTMLResponse)
def edit_project_equipment(request: Request, equipment_id: int, project_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url=f"/project-expenses?project_id={project_id}&company={company}", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM project_equipment WHERE id = ? AND project_id = ?",
        (equipment_id, project_id)
    ).fetchone()
    conn.close()

    if not equipment:
        return "<h2>المعدة غير موجودة</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>تعديل المعدة</h1>

<form action="/update-project-equipment/{equipment_id}" method="post">
<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

<label>اسم المعدة</label>
<input type="text" name="name" value="{equipment['name']}" required>

<label>الكمية</label>
<input type="number" name="qty" value="{equipment['qty']}" required>

<label>الحالة</label>
<input type="text" name="status" value="{equipment['status']}" required>

<br><br>
<button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
</form>

<br>
<a href="/project-equipment?project_id={project_id}&company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.post("/update-project-equipment/{equipment_id}")
def update_project_equipment(
    request: Request,
    equipment_id: int,
    project_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    qty: int = Form(...),
    status: str = Form(...),
):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url=f"/project-expenses?project_id={project_id}&company={company}", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "UPDATE project_equipment SET name = ?, qty = ?, status = ? WHERE id = ? AND project_id = ?",
        (name, qty, status, equipment_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-equipment?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/delete-project-equipment/{equipment_id}")
def delete_project_equipment(request: Request, equipment_id: int, project_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    if is_works_expenses_suppliers_employee(access_result, company):
        return RedirectResponse(url=f"/project-expenses?project_id={project_id}&company={company}", status_code=303)
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "DELETE FROM project_equipment WHERE id = ? AND project_id = ?",
        (equipment_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-equipment?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/project-suppliers", response_class=HTMLResponse)
def project_suppliers(request: Request, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_read_only_works_partner = is_works_partner_user(access_result, company)

    conn = get_db()

    suppliers = conn.execute(
        "SELECT * FROM project_suppliers WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for s in suppliers:
        rows += f"""
        <tr>
            <td>{s['name']}</td>
            <td>{s['material']}</td>
            <td>{s['phone']}</td>
            <td>{s['date']}</td>
            <td>{"-" if is_read_only_works_partner else f'''<a href="/edit-project-supplier/{s['id']}?project_id={project_id}&company={company}" class="action-btn">تعديل</a><a href="/delete-project-supplier/{s['id']}?project_id={project_id}&company={company}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا المورد؟')">حذف</a>'''}</td>
        </tr>
        """

    supplier_form_html = ""
    if not is_read_only_works_partner:
        supplier_form_html = f"""
<form action="/save-supplier" method="post">

<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

اسم المورد:
<input type="text" name="name" required>

<br><br>

المادة:
<input type="text" name="material">

<br><br>

رقم الجوال:
<input type="text" name="phone">

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة المورد</button>

</form>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>موردين المشروع</h1>

{"<div class='inventory-note' style='margin-bottom:16px;'>صلاحية شريك المقاولات للعرض فقط.</div>" if is_read_only_works_partner else ""}

{supplier_form_html}

<br><br>

<table border="1" style="background:white;margin:auto;width:70%">

<tr>
<th>المورد</th>
<th>المادة</th>
<th>الجوال</th>
<th>التاريخ</th>
<th>الإدارة</th>
</tr>

{rows if rows else "<tr><td colspan='5'>لا يوجد موردين</td></tr>"}

</table>

<br>

<a href="/project/{project_id}?company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-supplier")
def save_supplier(
    request: Request,
    project_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    material: str = Form(""),
    phone: str = Form("")
):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard

    conn = get_db()

    conn.execute(
        "INSERT INTO project_suppliers (project_id, name, material, phone, date) VALUES (?, ?, ?, ?, DATE('now'))",
        (project_id, name, material, phone)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/project-suppliers?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/edit-project-supplier/{supplier_id}", response_class=HTMLResponse)
def edit_project_supplier(request: Request, supplier_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    supplier = conn.execute(
        "SELECT * FROM project_suppliers WHERE id = ? AND project_id = ?",
        (supplier_id, project_id)
    ).fetchone()
    conn.close()

    if not supplier:
        return "<h2>المورد غير موجود</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>تعديل المورد</h1>

<form action="/update-project-supplier/{supplier_id}" method="post">
<input type="hidden" name="project_id" value="{project_id}">
<input type="hidden" name="company" value="{company}">

<label>اسم المورد</label>
<input type="text" name="name" value="{supplier['name']}" required>

<label>المادة</label>
<input type="text" name="material" value="{supplier['material'] or ''}">

<label>الجوال</label>
<input type="text" name="phone" value="{supplier['phone'] or ''}">

<br><br>
<button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
</form>

<br>
<a href="/project-suppliers?project_id={project_id}&company={company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.post("/update-project-supplier/{supplier_id}")
def update_project_supplier(
    request: Request,
    supplier_id: int,
    project_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    material: str = Form(""),
    phone: str = Form(""),
):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "UPDATE project_suppliers SET name = ?, material = ?, phone = ? WHERE id = ? AND project_id = ?",
        (name, material, phone, supplier_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-suppliers?project_id={project_id}&company={company}",
        status_code=303
    )


@app.get("/delete-project-supplier/{supplier_id}")
def delete_project_supplier(request: Request, supplier_id: int, project_id: int, company: str = ""):
    access_result = ensure_employee_section_access(request, company, "expenses")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    conn.execute(
        "DELETE FROM project_suppliers WHERE id = ? AND project_id = ?",
        (supplier_id, project_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=f"/project-suppliers?project_id={project_id}&company={company}",
        status_code=303
    )

@app.get("/realestate-development", response_class=HTMLResponse)
def realestate_development():
    conn = get_db()

    # Get all development projects
    projects = conn.execute("SELECT * FROM development_projects").fetchall()

    conn.close()

    # Build projects list
    projects_html = ""
    if projects:
        for project in projects:
            projects_html += f"""
            <a href="/development-project/{project['id']}" class="company-card realestate" style="text-decoration:none;color:inherit;">
                <h3>{project['name']}</h3>
                <p>📍 {project['location'] or 'غير محدد'}</p>
                <p>📋 {project['status'] or 'غير محدد'}</p>
            </a>
            """
    else:
        projects_html = '<p style="text-align:center;color:#666;margin:60px 0;grid-column:1/-1;">لا توجد مشاريع حالياً</p>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>التطوير العقاري</h1>
<p>Urban Rise - إدارة مشاريع التطوير العقاري</p>

<a href="/new-development-project" class="company-card realestate" style="display:inline-block;margin:20px 0 30px 0;padding:15px;text-align:center;text-decoration:none;color:inherit;">
    <h3>&#10133; مشروع جديد</h3>
</a>

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin:20px 0;">
    {projects_html}
</div>

<div style="margin-top:30px;">
    <a href="/company/realestate" style="color:#007bff;text-decoration:none;" class="glass-btn back-btn">⬅ رجوع</a>
</div>

</div>
"""

@app.get("/new-development-project", response_class=HTMLResponse)
def new_development_project():
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>مشروع تطوير عقاري جديد</h1>

<form action="/save-development-project" method="post" style="background:white;padding:20px;border-radius:8px;max-width:500px;">

<label>اسم المشروع:</label>
<input type="text" name="name" required style="width:100%;padding:8px;margin:10px 0;"><br>

<label>الموقع:</label>
<input type="text" name="location" style="width:100%;padding:8px;margin:10px 0;"><br>

<label>عدد الوحدات الكلي:</label>
<input type="number" name="total_units" required style="width:100%;padding:8px;margin:10px 0;"><br>

<label>حالة المشروع:</label>
<select name="status" style="width:100%;padding:8px;margin:10px 0;">
<option value="قيد الإنشاء">قيد الإنشاء</option>
<option value="جاهز للبيع">جاهز للبيع</option>
<option value="مكتمل">مكتمل</option>
</select><br>

<button type="submit" class="glass-btn gold-text">حفظ المشروع</button>
</form>

<br>
<a href="/realestate-development" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-development-project")
def save_development_project(
    name: str = Form(...),
    location: str = Form(None),
    total_units: int = Form(...),
    status: str = Form(None)
):
    conn = get_db()
    conn.execute(
        "INSERT INTO development_projects (name, location, total_units, status) VALUES (?, ?, ?, ?)",
        (name, location, total_units, status)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/realestate-development", status_code=303)

@app.get("/development-project/{project_id}", response_class=HTMLResponse)
def development_project_detail(project_id: int):
    conn = get_db()

    project = conn.execute("SELECT * FROM development_projects WHERE id = ?", (project_id,)).fetchone()

    if not project:
        conn.close()
        return "<h2>المشروع غير موجود</h2>"

    units = conn.execute("SELECT * FROM development_units WHERE project_id = ?", (project_id,)).fetchall()
    sales = conn.execute("SELECT * FROM development_sales WHERE project_id = ?", (project_id,)).fetchall()

    conn.close()

    total_units = project["total_units"] or len(units)
    if len(units) > total_units:
        total_units = len(units)

    sold_unit_ids = {sale["unit_id"] for sale in sales if sale["unit_id"] is not None}
    sold_units = len(sold_unit_ids)
    available_units = max(total_units - sold_units, 0)
    sale_percentage = round((sold_units / total_units) * 100) if total_units else 0

    available_units_for_sale = [unit for unit in units if unit["id"] not in sold_unit_ids]

    units_html = ""
    if units:
        for unit in units:
            is_sold = unit["id"] in sold_unit_ids
            status_badge = "مباعة" if is_sold else (unit["status"] or "متاحة")
            status_color = "#16a34a" if is_sold else "#0a2e4d"
            price_label = f"{int(unit['price']):,} ريال" if unit["price"] else "غير محدد"
            units_html += f"""
            <div class="development-unit-card">
                <div class="development-unit-top">
                    <div>
                        <h3>{unit['name']}</h3>
                        <p>{unit['type'] or 'وحدة عقارية'}</p>
                    </div>
                    <span class="development-badge" style="background:{status_color};">{status_badge}</span>
                </div>
                <div class="development-unit-meta">
                    <span>السعر</span>
                    <strong>{price_label}</strong>
                </div>
            </div>
            """
    else:
        units_html = """
        <div class="development-empty-state">
            <h3>لا توجد وحدات مضافة حتى الآن</h3>
            <p>ابدأ بإضافة أول وحدة داخل هذا المشروع ليظهر المخزون وحالة البيع بشكل أوضح.</p>
        </div>
        """

    sale_form_html = ""
    if available_units_for_sale:
        options_html = ""
        for unit in available_units_for_sale:
            unit_price = f" - {int(unit['price']):,} ريال" if unit["price"] else ""
            options_html += f'<option value="{unit["id"]}">{unit["name"]} ({unit["type"] or "وحدة"}){unit_price}</option>'

        sale_form_html = f"""
        <form action="/save-development-sale" method="post" class="development-sale-form">
            <input type="hidden" name="project_id" value="{project_id}">
            <div class="development-form-grid">
                <div>
                    <label for="unit_id">اختر الوحدة</label>
                    <select id="unit_id" name="unit_id" required>
                        {options_html}
                    </select>
                </div>
                <div>
                    <label for="price">سعر البيع</label>
                    <input id="price" type="number" step="0.01" name="price" placeholder="مثال: 850000" required>
                </div>
            </div>
            <button type="submit" class="development-primary-button glass-btn gold-text">تأكيد تسجيل البيع</button>
        </form>
        """
    else:
        sale_form_html = """
        <div class="development-empty-note">
            جميع الوحدات الحالية مسجلة كمباعة، أو لا توجد وحدات متاحة للبيع الآن.
        </div>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark" dir="rtl">
<style>
    .development-page {{
        max-width: 1180px;
        margin: 0 auto;
        padding: 30px 0 60px;
        text-align: right;
    }}

    .development-back-link {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: #dbeafe;
        text-decoration: none;
        font-size: 16px;
        font-weight: 700;
        margin-bottom: 24px;
    }}

    .development-hero {{
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 30px;
        padding: 28px;
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.28);
        backdrop-filter: blur(18px);
        margin-bottom: 24px;
    }}

    .development-hero-header {{
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 16px;
        flex-wrap: wrap;
    }}

    .development-eyebrow {{
        color: rgba(255, 255, 255, 0.72);
        font-size: 14px;
        margin: 0 0 10px 0;
    }}

    .development-hero h1 {{
        margin: 0;
        font-size: 38px;
        color: #ffffff;
    }}

    .development-location {{
        margin: 10px 0 0 0;
        color: rgba(255, 255, 255, 0.92);
        font-size: 18px;
    }}

    .development-actions {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
    }}

    .development-primary-button,
    .development-secondary-button {{
        min-height: 48px;
    }}

    .development-actions .glass-btn,
    .development-sale-form .glass-btn {{
        box-shadow: 0 12px 24px rgba(15, 23, 42, 0.18);
    }}

    .development-stats-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 18px;
        margin-bottom: 24px;
    }}

    .development-stat-card {{
        background: linear-gradient(180deg, rgba(255, 251, 245, 0.76), rgba(236, 224, 206, 0.68));
        border: 1px solid rgba(180, 150, 100, 0.15);
        border-radius: 24px;
        padding: 24px 20px;
        text-align: right;
        box-shadow: 0 14px 30px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.42);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        transition: transform 0.36s cubic-bezier(.22,1,.36,1), box-shadow 0.36s cubic-bezier(.22,1,.36,1), border-color 0.36s cubic-bezier(.22,1,.36,1), background 0.36s cubic-bezier(.22,1,.36,1);
        isolation: isolate;
    }}

    .development-stat-label {{
        margin: 0 0 10px 0;
        color: rgba(255, 255, 255, 0.72);
        font-size: 14px;
    }}

    .development-stat-value {{
        margin: 0;
        color: #ffffff;
        font-size: 34px;
        line-height: 1;
    }}

    .development-section-card {{
        background: linear-gradient(180deg, rgba(255, 251, 245, 0.76), rgba(236, 224, 206, 0.68));
        border: 1px solid rgba(180, 150, 100, 0.15);
        border-radius: 28px;
        padding: 28px;
        box-shadow: 0 14px 30px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.42);
        margin-bottom: 24px;
        overflow: hidden;
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        transition: transform 0.36s cubic-bezier(.22,1,.36,1), box-shadow 0.36s cubic-bezier(.22,1,.36,1), border-color 0.36s cubic-bezier(.22,1,.36,1), background 0.36s cubic-bezier(.22,1,.36,1);
        isolation: isolate;
    }}

    .development-section-head {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 20px;
    }}

    .development-section-head h2 {{
        margin: 0;
        color: #ffffff;
        font-size: 24px;
    }}

    .development-section-head p {{
        margin: 6px 0 0 0;
        color: rgba(255, 255, 255, 0.72);
        font-size: 15px;
    }}

    .development-sale-form {{
        margin: 0;
    }}

    .development-form-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
        margin-bottom: 18px;
    }}

    .development-sale-form label {{
        display: block;
        margin-bottom: 8px;
        color: #ffffff;
        font-weight: 700;
        font-size: 14px;
    }}

    .development-sale-form input,
    .development-sale-form select {{
        width: 100%;
        height: 48px;
        border-radius: 14px;
        border: 1px solid rgba(255, 255, 255, 0.18);
        background: rgba(255, 255, 255, 0.96);
        color: #0f172a;
        padding: 0 14px;
        box-sizing: border-box;
        font-size: 15px;
    }}

    .development-units-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 16px;
    }}

    .development-unit-card {{
        background: linear-gradient(180deg, rgba(255, 251, 245, 0.76), rgba(236, 224, 206, 0.68));
        border: 1px solid rgba(180, 150, 100, 0.15);
        border-radius: 22px;
        padding: 20px;
        box-shadow: 0 14px 30px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.42);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        transition: transform 0.36s cubic-bezier(.22,1,.36,1), box-shadow 0.36s cubic-bezier(.22,1,.36,1), border-color 0.36s cubic-bezier(.22,1,.36,1), background 0.36s cubic-bezier(.22,1,.36,1);
        isolation: isolate;
    }}
    .development-stat-card:hover,
    .development-section-card:hover,
    .development-unit-card:hover {{
        transform: translateY(-10px) scale(1.02);
        box-shadow: 0 28px 52px rgba(0,0,0,0.14), 0 0 0 1px rgba(214, 195, 163, 0.34), inset 0 1px 0 rgba(255,255,255,0.56);
        border-color: rgba(184, 155, 109, 0.34);
        background: linear-gradient(180deg, rgba(255, 252, 247, 0.82), rgba(238, 226, 208, 0.74));
        z-index: 2;
    }}

    .development-unit-top {{
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 18px;
    }}

    .development-unit-top h3 {{
        margin: 0 0 6px 0;
        color: #ffffff;
        font-size: 20px;
    }}

    .development-unit-top p {{
        margin: 0;
        color: rgba(255, 255, 255, 0.72);
        font-size: 14px;
    }}

    .development-badge {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 12px;
        border-radius: 999px;
        color: #ffffff;
        font-size: 13px;
        font-weight: 700;
        white-space: nowrap;
    }}

    .development-unit-meta {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        color: rgba(255, 255, 255, 0.78);
        font-size: 14px;
    }}

    .development-unit-meta strong {{
        color: #ffffff;
        font-size: 15px;
    }}

    .development-empty-state,
    .development-empty-note {{
        padding: 22px;
        border-radius: 20px;
        background: rgba(255, 255, 255, 0.05);
        border: 1px dashed rgba(255, 255, 255, 0.18);
        color: rgba(255, 255, 255, 0.86);
    }}

    .development-empty-state h3 {{
        margin: 0 0 10px 0;
        color: #ffffff;
    }}

    .development-empty-state p,
    .development-empty-note {{
        margin: 0;
        line-height: 1.8;
    }}

    @media (max-width: 700px) {{
        .development-page {{
            padding-top: 12px;
        }}

        .development-hero,
        .development-section-card {{
            padding: 22px;
        }}

        .development-hero h1 {{
            font-size: 30px;
        }}

        .development-stat-value {{
            font-size: 28px;
        }}

        .development-actions {{
            width: 100%;
        }}

        .development-primary-button,
        .development-secondary-button {{
            width: 100%;
        }}
    }}
</style>
{HOME_BUTTON}
<div class="dashboard">
    <div class="development-page">
        <a href="/realestate-development" class="development-back-link glass-btn back-btn">⬅ رجوع للمشاريع</a>

        <section class="development-hero">
            <div class="development-hero-header">
                <div>
                    <p class="development-eyebrow">تفاصيل مشروع التطوير العقاري</p>
                    <h1>{project['name']}</h1>
                    <p class="development-location">الموقع: {project['location'] or 'غير محدد'}</p>
                </div>
                <div class="development-actions">
                    <a href="/new-development-unit?project_id={project_id}" class="development-primary-button glass-btn">إضافة وحدة</a>
                    <a href="#register-sale" class="development-secondary-button glass-btn">تسجيل بيع</a>
                </div>
            </div>
        </section>

        <section class="development-stats-grid">
            <article class="development-stat-card">
                <p class="development-stat-label">عدد الوحدات</p>
                <h3 class="development-stat-value">{total_units}</h3>
            </article>
            <article class="development-stat-card">
                <p class="development-stat-label">المباعة</p>
                <h3 class="development-stat-value">{sold_units}</h3>
            </article>
            <article class="development-stat-card">
                <p class="development-stat-label">المتبقية</p>
                <h3 class="development-stat-value">{available_units}</h3>
            </article>
            <article class="development-stat-card">
                <p class="development-stat-label">نسبة البيع %</p>
                <h3 class="development-stat-value">{sale_percentage}%</h3>
            </article>
        </section>

        <section class="development-section-card" id="register-sale">
            <div class="development-section-head">
                <div>
                    <h2>تسجيل بيع جديد</h2>
                    <p>اختر وحدة متاحة وسجل سعر البيع مباشرة من داخل الصفحة.</p>
                </div>
            </div>
            {sale_form_html}
        </section>

        <section class="development-section-card">
            <div class="development-section-head">
                <div>
                    <h2>الوحدات</h2>
                    <p>عرض سريع لحالة الوحدات داخل المشروع مع السعر والحالة الحالية.</p>
                </div>
            </div>
            <div class="development-units-grid">
                {units_html}
            </div>
        </section>
    </div>
</div>
"""

@app.get("/new-development-unit", response_class=HTMLResponse)
def new_development_unit(project_id: int):
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>وحدة جديدة</h1>

<form action="/save-development-unit" method="post" style="background:white;padding:20px;border-radius:8px;max-width:500px;">
<input type="hidden" name="project_id" value="{project_id}">

<label>اسم الوحدة:</label>
<input type="text" name="name" required style="width:100%;padding:8px;margin:10px 0;"><br>

<label>النوع:</label>
<select name="type" required style="width:100%;padding:8px;margin:10px 0;">
<option value="شقة">شقة</option>
<option value="مكتب">مكتب</option>
<option value="محل">محل</option>
<option value="أرض">أرض</option>
</select><br>

<label>السعر (ريال):</label>
<input type="number" step="0.01" name="price" style="width:100%;padding:8px;margin:10px 0;"><br>

<button type="submit" class="glass-btn gold-text">حفظ الوحدة</button>
</form>

<br>
<a href="/development-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-development-unit")
def save_development_unit(
    project_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    price: float = Form(None)
):
    conn = get_db()
    conn.execute(
        "INSERT INTO development_units (project_id, name, type, price, status) VALUES (?, ?, ?, ?, ?)",
        (project_id, name, type, price, "متاح")
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.get("/edit-development-unit/{unit_id}", response_class=HTMLResponse)
def edit_development_unit(unit_id: int, project_id: int):
    conn = get_db()
    unit = conn.execute("SELECT * FROM development_units WHERE id = ?", (unit_id,)).fetchone()
    conn.close()

    if not unit:
        return "<h2>الوحدة غير موجودة</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>تعديل الوحدة</h1>

<form action="/update-development-unit" method="post" style="background:white;padding:20px;border-radius:8px;max-width:500px;">
<input type="hidden" name="unit_id" value="{unit_id}">
<input type="hidden" name="project_id" value="{project_id}">

<label>اسم الوحدة:</label>
<input type="text" name="name" value="{unit['name']}" required style="width:100%;padding:8px;margin:10px 0;"><br>

<label>النوع:</label>
<select name="type" required style="width:100%;padding:8px;margin:10px 0;">
<option value="شقة" {'selected' if unit['type'] == 'شقة' else ''}>شقة</option>
<option value="مكتب" {'selected' if unit['type'] == 'مكتب' else ''}>مكتب</option>
<option value="محل" {'selected' if unit['type'] == 'محل' else ''}>محل</option>
<option value="أرض" {'selected' if unit['type'] == 'أرض' else ''}>أرض</option>
</select><br>

<label>السعر (ريال):</label>
<input type="number" step="0.01" name="price" value="{unit['price'] or ''}" required style="width:100%;padding:8px;margin:10px 0;"><br>

<button type="submit" class="glass-btn gold-text">تحديث الوحدة</button>
</form>

<br>
<a href="/development-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/update-development-unit")
def update_development_unit(
    unit_id: int = Form(...),
    project_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    price: float = Form(...)
):
    conn = get_db()
    conn.execute(
        "UPDATE development_units SET name = ?, type = ?, price = ? WHERE id = ?",
        (name, type, price, unit_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.get("/delete-development-unit/{unit_id}")
def delete_development_unit(unit_id: int, project_id: int):
    conn = get_db()
    conn.execute("DELETE FROM development_units WHERE id = ?", (unit_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.post("/save-development-sale")
def save_development_sale(
    project_id: int = Form(...),
    unit_id: int = Form(...),
    price: float = Form(...)
):
    conn = get_db()
    conn.execute(
        "INSERT INTO development_sales (project_id, unit_id, price, date) VALUES (?, ?, ?, ?)",
        (project_id, unit_id, price, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.get("/delete-development-sale/{sale_id}")
def delete_development_sale(sale_id: int, project_id: int):
    conn = get_db()
    conn.execute("DELETE FROM development_sales WHERE id = ?", (sale_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.post("/save-development-expense")
def save_development_expense(
    project_id: int = Form(...),
    title: str = Form(...),
    amount: float = Form(...)
):
    conn = get_db()
    conn.execute(
        "INSERT INTO development_expenses (project_id, title, amount, date) VALUES (?, ?, ?, ?)",
        (project_id, title, amount, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.get("/delete-development-expense/{expense_id}")
def delete_development_expense(expense_id: int, project_id: int):
    conn = get_db()
    conn.execute("DELETE FROM development_expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/development-project/{project_id}", status_code=303)

@app.get("/property-management", response_class=HTMLResponse)
def property_management_home(request: Request, message: str = "", error: str = ""):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    realestate_access = ensure_realestate_property_management_access(request)
    if not isinstance(realestate_access, sqlite3.Row):
        return realestate_access
    conn = get_db()
    properties = conn.execute(
        """
        SELECT
            property_properties.*,
            property_supervisors.supervisor_name,
            property_supervisors.phone AS supervisor_phone
        FROM property_properties
        LEFT JOIN property_supervisors
            ON property_supervisors.id = (
                SELECT id
                FROM property_supervisors
                WHERE property_supervisors.property_id = property_properties.id
                ORDER BY id DESC
                LIMIT 1
            )
        ORDER BY property_properties.id DESC
        """
    ).fetchall()
    conn.close()

    accessible_property_ids = set(get_accessible_property_ids(user["id"]))
    if not is_admin(user) and not (is_employee(user) or is_partner(user)):
        properties = [prop for prop in properties if prop["id"] in accessible_property_ids]

    property_items = [
        {
            "id": prop["id"],
            "name": prop["name"],
            "location": prop["location"] or "بدون موقع",
            "property_type": prop["property_type"] or "ملكية عامة",
            "supervisor": prop["supervisor_name"] or "غير محدد",
        }
        for prop in properties
    ]

    return templates.TemplateResponse(
        request,
        "property_management.html",
        {
            "request": request,
            "home_button": HOME_BUTTON,
            "properties": property_items,
            "message": message,
            "error": error,
            "is_owner_read_only": realestate_owner_read_only(realestate_access),
            "is_property_accounts_employee": is_realestate_property_accounts_employee(realestate_access),
        },
    )


@app.get("/edit-contract/{contract_id}", response_class=HTMLResponse)
def edit_contract_form(request: Request, contract_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company)
    ).fetchone()
    conn.close()

    if not contract:
        return "<h2>العقد غير موجود</h2>"

    if get_contract_record_source(contract) == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT:
        return HTMLResponse(
            f"""
            <meta charset="UTF-8">
            <link rel="stylesheet" href="/static/style.css">
            <body class="system-dark">
            {HOME_BUTTON}
            <div class="dashboard">
                <div class="inventory-note" style="margin:20px 0;">
                    هذا الإدخال اليدوي يتم تعديله من صفحة المشروع وليس من صفحة العقود.
                </div>
                <a href="/projects?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
            </div>
            """,
            status_code=400,
        )

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>تعديل العقد</h2>

    <form action="/update-contract/{contract_id}" method="post">
        <input type="hidden" name="company" value="{company}">

        <label>رقم عرض السعر</label>
        <input type="text" value="{contract['quote_id']}" readonly>

        <label>الحالة</label>
        <input type="text" name="status" value="{contract['status'] or ''}" required>

        <br><br>
        <button type="submit" class="glass-btn gold-text">حفظ التعديل</button>
    </form>

    <br>
    <a href="/contract/{contract_id}?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-contract/{contract_id}")
def update_contract(request: Request, contract_id: int, company: str = Form(...), status: str = Form(...)):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company),
    ).fetchone()
    if not contract:
        conn.close()
        return "<h2>العقد غير موجود</h2>"
    if get_contract_record_source(contract) == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT:
        conn.close()
        return RedirectResponse(url=f"/projects?company={company}", status_code=303)
    conn.execute(
        "UPDATE contracts SET status = ? WHERE id = ? AND company = ?",
        (status, contract_id, company)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/contract/{contract_id}?company={company}", status_code=303)


@app.get("/delete-company-contract/{contract_id}")
def delete_company_contract(request: Request, contract_id: int, company: str = ""):
    access_result = ensure_company_access(request, company)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    partner_guard = ensure_not_works_partner_write(access_result, company)
    if not isinstance(partner_guard, sqlite3.Row):
        return partner_guard
    conn = get_db()
    contract = conn.execute(
        "SELECT * FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company),
    ).fetchone()
    if not contract:
        conn.close()
        return "<h2>العقد غير موجود</h2>"
    if get_contract_record_source(contract) == CONTRACT_RECORD_SOURCE_MANUAL_PROJECT:
        conn.close()
        return RedirectResponse(url=f"/projects?company={company}", status_code=303)
    conn.execute(
        "UPDATE projects SET contract_id = NULL WHERE contract_id = ? AND company = ?",
        (contract_id, company)
    )
    conn.execute(
        "DELETE FROM contracts WHERE id = ? AND company = ?",
        (contract_id, company)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/contracts?company={company}", status_code=303)


@app.get("/property-management/new", response_class=HTMLResponse)
def property_management_new(request: Request):
    role_check = require_role(request, {"admin", "employee", "partner"})
    if not isinstance(role_check, sqlite3.Row):
        return role_check
    access_result = ensure_realestate_write_access(request, back_url="/property-management")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    form_html = render_property_form(
        action_url="/save-property",
        submit_label="حفظ الملك",
    )
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>إضافة ملك جديد</h1>
<p>أدخل بيانات الملك أو الموقع الجديد وسيتم فتح لوحة مستقلة له مباشرة بعد الحفظ</p>

{form_html}

<a href="/property-management" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.get("/property-properties", response_class=HTMLResponse)
def property_properties_page():
    return RedirectResponse(url="/property-management", status_code=303)


def build_redirect_url(base_url: str, message: str = "", error: str = "") -> str:
    query_parts = []
    if message:
        query_parts.append(f"message={quote(str(message))}")
    if error:
        query_parts.append(f"error={quote(str(error))}")
    if not query_parts:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{'&'.join(query_parts)}"


def render_page_feedback(message: str = "", error: str = "") -> str:
    feedback_parts = []
    if message:
        feedback_parts.append(f'<div class="inventory-note">{escape(str(message))}</div>')
    if error:
        feedback_parts.append(
            f'<div class="inventory-note" style="border-color:rgba(127,29,29,0.45);color:#fecaca;">{escape(str(error))}</div>'
        )
    return "".join(feedback_parts)


def parse_safe_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw_value = str(value).strip()
    if not raw_value:
        return None
    normalized = raw_value.replace("T", " ")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:len(fmt)], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def safe_amount(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_median(values) -> float:
    cleaned_values = sorted(
        safe_amount(value)
        for value in (values or [])
    )
    if not cleaned_values:
        return 0.0

    mid = len(cleaned_values) // 2
    if len(cleaned_values) % 2:
        return cleaned_values[mid]
    return (cleaned_values[mid - 1] + cleaned_values[mid]) / 2


PAYMENT_FREQUENCY_CONFIG = {
    "yearly": {
        "months_step": 12,
        "installments_per_year": 1,
        "label": "سنوي",
    },
    "semi-annual": {
        "months_step": 6,
        "installments_per_year": 2,
        "label": "نصف سنوي",
    },
    "quarterly": {
        "months_step": 3,
        "installments_per_year": 4,
        "label": "ربع سنوي",
    },
    "monthly": {
        "months_step": 1,
        "installments_per_year": 12,
        "label": "شهري",
    },
}

PROPERTY_TYPE_OPTIONS = ["سكني", "تجاري", "مكتبي", "فندقي"]


def round_money(amount) -> float:
    return float(Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_property_type(value: str = "") -> str:
    clean_value = (value or "").strip()
    return clean_value


def add_months_to_date(base_date: date, months_to_add: int) -> date:
    total_month = (base_date.month - 1) + int(months_to_add or 0)
    year = base_date.year + (total_month // 12)
    month = (total_month % 12) + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(base_date.day, last_day))


def build_contract_installment_rows(
    contract_id: int,
    annual_rent,
    contract_duration_years,
    payment_frequency: str,
    contract_start_date: str = "",
):
    frequency_key = (payment_frequency or "").strip().lower()
    config = PAYMENT_FREQUENCY_CONFIG.get(frequency_key)
    if not config:
        return []

    try:
        duration_years = int(float(contract_duration_years or 0))
    except (TypeError, ValueError):
        duration_years = 0

    annual_amount = round_money(annual_rent)
    if contract_id <= 0 or annual_amount <= 0 or duration_years <= 0:
        return []

    start_date_value = parse_safe_date(contract_start_date) or date.today()
    total_months = duration_years * 12
    months_step = config["months_step"]
    if total_months <= 0 or months_step <= 0:
        return []

    total_installments = total_months // months_step
    if total_installments <= 0:
        return []

    installment_amount = round_money(annual_amount / config["installments_per_year"])
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for index in range(total_installments):
        due_date = add_months_to_date(start_date_value, index * months_step).isoformat()
        rows.append(
            (
                contract_id,
                installment_amount,
                due_date,
                "unpaid",
                created_at,
            )
        )
    return rows


def get_payment_frequency_from_installments(installments) -> str:
    rows = list(installments or [])
    if len(rows) < 2:
        return "yearly"

    first_due = parse_safe_date(rows[0]["due_date"])
    second_due = parse_safe_date(rows[1]["due_date"])
    if not first_due or not second_due:
        return "yearly"

    months_diff = (second_due.year - first_due.year) * 12 + (second_due.month - first_due.month)
    if months_diff <= 1:
        return "monthly"
    if months_diff <= 3:
        return "quarterly"
    if months_diff <= 6:
        return "semi-annual"
    return "yearly"


def derive_contract_form_values(conn, contract_id: int) -> dict[str, object]:
    installments = conn.execute(
        """
        SELECT *
        FROM contract_installments
        WHERE contract_id = ?
        ORDER BY due_date ASC, id ASC
        """,
        (contract_id,),
    ).fetchall()
    if not installments:
        return {
            "annual_rent": 0.0,
            "contract_duration_years": 1,
            "payment_frequency": "yearly",
        }

    frequency = get_payment_frequency_from_installments(installments)
    config = PAYMENT_FREQUENCY_CONFIG.get(frequency, PAYMENT_FREQUENCY_CONFIG["yearly"])
    annual_rent = round_money(safe_amount(installments[0]["amount"]) * config["installments_per_year"])
    duration_years = max(1, int(len(installments) / config["installments_per_year"]))
    return {
        "annual_rent": annual_rent,
        "contract_duration_years": duration_years,
        "payment_frequency": frequency,
    }


def calculate_contract_rent_value(annual_rent, contract_duration_years, payment_frequency: str) -> float:
    frequency_key = (payment_frequency or "").strip().lower()
    config = PAYMENT_FREQUENCY_CONFIG.get(frequency_key)
    annual_value = round_money(annual_rent)
    if not config or annual_value <= 0:
        return annual_value
    try:
        duration_years = max(1, int(float(contract_duration_years or 1)))
    except (TypeError, ValueError):
        duration_years = 1
    _ = duration_years
    return annual_value


def compute_contract_status(end_date_value: str = "", fallback_status: str = "") -> str:
    end_date_obj = parse_safe_date(end_date_value)
    if not end_date_obj:
        return fallback_status or "ساري"

    today = date.today()
    remaining_days = (end_date_obj - today).days
    if remaining_days < 0:
        return "منتهي"
    if remaining_days <= 30:
        return "قريب ينتهي"
    return "ساري"


def resolve_contract_tenant_id(
    conn,
    *,
    property_id: int,
    unit_id: int,
    tenant_id_value: str = "",
    new_tenant_name: str = "",
    new_tenant_phone: str = "",
    new_tenant_id_number: str = "",
    new_tenant_type: str = "",
) -> int:
    tenant_choice = (tenant_id_value or "").strip()
    if tenant_choice and tenant_choice != "__new__":
        try:
            selected_tenant_id = int(tenant_choice)
        except (TypeError, ValueError):
            return 0
        tenant_row = conn.execute(
            "SELECT id FROM property_tenants WHERE id = ? AND property_id = ?",
            (selected_tenant_id, property_id),
        ).fetchone()
        return tenant_row["id"] if tenant_row else 0

    tenant_name = (new_tenant_name or "").strip()
    if not tenant_name:
        return 0

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO property_tenants (property_id, unit_id, name, phone, id_number, tenant_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            unit_id if unit_id else None,
            tenant_name,
            (new_tenant_phone or "").strip(),
            (new_tenant_id_number or "").strip(),
            (new_tenant_type or "").strip(),
        ),
    )
    return cursor.lastrowid


def get_property_contract_installment_rows(conn, property_id: int):
    return conn.execute(
        """
        SELECT
            property_rent_contracts.id AS contract_id,
            property_rent_contracts.property_id,
            property_rent_contracts.unit_id,
            property_rent_contracts.tenant_id,
            property_rent_contracts.rent AS contract_rent,
            property_rent_contracts.start_date,
            property_rent_contracts.end_date,
            property_rent_contracts.status AS contract_status,
            property_units.name AS unit_name,
            property_tenants.name AS tenant_name,
            contract_installments.id AS installment_id,
            contract_installments.amount AS installment_amount,
            contract_installments.due_date,
            contract_installments.status AS installment_status,
            contract_installments.created_at,
            contract_installments.paid_at
        FROM property_rent_contracts
        LEFT JOIN property_units ON property_units.id = property_rent_contracts.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = property_rent_contracts.tenant_id
        LEFT JOIN contract_installments ON contract_installments.contract_id = property_rent_contracts.id
        WHERE property_rent_contracts.property_id = ?
        ORDER BY property_rent_contracts.id DESC, contract_installments.due_date ASC, contract_installments.id ASC
        """,
        (property_id,),
    ).fetchall()


def refresh_unit_status_from_contracts(conn, unit_id: int):
    if not unit_id:
        return

    contracts = conn.execute(
        """
        SELECT start_date, end_date, status, rent
        FROM property_rent_contracts
        WHERE unit_id = ?
        ORDER BY id DESC
        """,
        (unit_id,),
    ).fetchall()
    effective_status = "شاغرة"
    effective_rent = 0
    for contract in contracts:
        contract_status = compute_contract_status(contract["end_date"], contract["status"] or "")
        if contract_status in {"ساري", "قريب ينتهي"}:
            effective_status = "مؤجرة"
            effective_rent = safe_amount(contract["rent"])
            break

    conn.execute(
        "UPDATE property_units SET status = ?, rent = ? WHERE id = ?",
        (effective_status, effective_rent, unit_id),
    )


def sync_property_contracts_and_units(conn, property_id: int = 0):
    params = []
    where_clause = ""
    if property_id:
        where_clause = "WHERE property_id = ?"
        params.append(property_id)

    contracts = conn.execute(
        f"""
        SELECT unit_id, end_date, status
        FROM property_rent_contracts
        {where_clause}
        ORDER BY id DESC
        """,
        params,
    ).fetchall()

    affected_units = set()
    for contract in contracts:
        if contract["unit_id"]:
            affected_units.add(contract["unit_id"])

    if property_id:
        scoped_units = conn.execute(
            "SELECT id FROM property_units WHERE property_id = ?",
            (property_id,),
        ).fetchall()
        affected_units.update(row["id"] for row in scoped_units)

    for unit_id in affected_units:
        refresh_unit_status_from_contracts(conn, unit_id)


def property_expense_category_labels() -> dict[str, str]:
    return {
        "salary": "رواتب",
        "electricity": "كهرباء",
        "water": "مياه",
        "cleaning": "نظافة",
        "security": "أمن",
        "event_preparation": "تجهيز فعاليات",
        "marketing": "تسويق",
        "government_fees": "رسوم حكومية",
        "furniture": "أثاث وتجهيز",
        "hospitality": "ضيافة",
        "emergency": "طوارئ",
        "other": "أخرى",
        "maintenance": "صيانة",
    }


def render_property_form(
    action_url: str,
    submit_label: str,
    property_name: str = "",
    location: str = "",
    property_type: str = "",
    supervisor_name: str = "",
    supervisor_phone: str = "",
    notes: str = "",
) -> str:
    property_type_options = '<option value="">اختر نوع العقار</option>'
    if property_type and property_type not in PROPERTY_TYPE_OPTIONS:
        property_type_options += f'<option value="{escape(property_type)}" selected>{escape(property_type)}</option>'
    for option in PROPERTY_TYPE_OPTIONS:
        selected = "selected" if property_type == option else ""
        property_type_options += f'<option value="{option}" {selected}>{option}</option>'
    return f"""
<div class="inventory-panel inventory-table-panel">
    <form action="{escape(action_url)}" method="post">
        <label>اسم الملك</label>
        <input type="text" name="property_name" value="{escape(property_name)}" required>

        <label>الموقع</label>
        <input type="text" name="location" value="{escape(location)}">

        <label>نوع الملك</label>
        <select name="property_type" required>{property_type_options}</select>

        <label>اسم المشرف</label>
        <input type="text" name="supervisor_name" value="{escape(supervisor_name)}">

        <label>هاتف المشرف</label>
        <input type="text" name="supervisor_phone" value="{escape(supervisor_phone)}">

        <label>ملاحظات</label>
        <textarea name="notes" rows="4">{escape(notes)}</textarea>

        <button type="submit" class="glass-btn gold-text">{escape(submit_label)}</button>
    </form>
</div>
"""


def cascade_delete_property_records(property_id: int) -> bool:
    conn = get_db()
    prop = conn.execute("SELECT id FROM property_properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        conn.close()
        return False

    contract_ids = conn.execute(
        "SELECT id FROM property_rent_contracts WHERE property_id = ?",
        (property_id,),
    ).fetchall()
    contract_id_values = [row["id"] for row in contract_ids]

    tenant_ids = conn.execute(
        "SELECT id FROM property_tenants WHERE property_id = ?",
        (property_id,),
    ).fetchall()
    tenant_id_values = [row["id"] for row in tenant_ids]
    if tenant_id_values:
        placeholders = ", ".join("?" for _ in tenant_id_values)
        conn.execute(
            f"DELETE FROM user_tenant_access WHERE tenant_id IN ({placeholders})",
            tenant_id_values,
        )

    if contract_id_values:
        placeholders = ", ".join("?" for _ in contract_id_values)
        conn.execute(
            f"DELETE FROM contract_installments WHERE contract_id IN ({placeholders})",
            contract_id_values,
        )

    conn.execute("DELETE FROM user_property_access WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_expenses WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM maintenance_requests WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_rent_contracts WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_tenants WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_units WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_supervisors WHERE property_id = ?", (property_id,))
    conn.execute("DELETE FROM property_properties WHERE id = ?", (property_id,))
    conn.commit()
    conn.close()
    return True


@app.post("/save-property")
def save_property(
    request: Request,
    property_name: str = Form(...),
    location: str = Form(""),
    property_type: str = Form(""),
    supervisor_name: str = Form(""),
    supervisor_phone: str = Form(""),
    notes: str = Form(""),
):
    role_check = require_role(request, {"admin", "employee", "partner"})
    if not isinstance(role_check, sqlite3.Row):
        return role_check
    access_result = ensure_realestate_write_access(request, back_url="/property-management")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    cur = conn.cursor()
    property_type = normalize_property_type(property_type)
    cur.execute(
        """
        INSERT INTO property_properties (name, location, property_type, status, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (property_name, location, property_type, "نشط", notes)
    )
    property_id = cur.lastrowid

    if supervisor_name.strip() or supervisor_phone.strip() or notes.strip():
        conn.execute(
            """
            INSERT INTO property_supervisors (property_id, supervisor_name, phone, notes)
            VALUES (?, ?, ?, ?)
            """,
            (property_id, supervisor_name.strip(), supervisor_phone.strip(), notes.strip())
        )

    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/property-management/{property_id}", status_code=303)


@app.get("/edit-property/{property_id}", response_class=HTMLResponse)
def edit_property(request: Request, property_id: int):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-management/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()
    supervisor = conn.execute(
        """
        SELECT * FROM property_supervisors
        WHERE property_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (property_id,)
    ).fetchone()
    conn.close()

    if not prop:
        return RedirectResponse(
            url=build_redirect_url("/property-management", error="الملك غير موجود"),
            status_code=303
        )

    form_html = render_property_form(
        action_url=f"/update-property/{property_id}",
        submit_label="تحديث الملك",
        property_name=prop["name"] or "",
        location=prop["location"] or "",
        property_type=prop["property_type"] or "",
        supervisor_name=supervisor["supervisor_name"] if supervisor else "",
        supervisor_phone=supervisor["phone"] if supervisor else "",
        notes=prop["notes"] or "",
    )

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>تعديل الملك</h1>
<p>يمكنك تحديث بيانات الملك مع الإبقاء على نفس أسلوب النموذج الحالي.</p>

{form_html}

<a href="/property-management" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.post("/update-property/{property_id}")
def update_property(
    request: Request,
    property_id: int,
    property_name: str = Form(...),
    location: str = Form(""),
    property_type: str = Form(""),
    supervisor_name: str = Form(""),
    supervisor_phone: str = Form(""),
    notes: str = Form(""),
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-management/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url("/property-management", error="الملك غير موجود"),
            status_code=303
        )

    conn.execute(
        """
        UPDATE property_properties
        SET name = ?, location = ?, property_type = ?, notes = ?
        WHERE id = ?
        """,
        (property_name, location, normalize_property_type(property_type), notes, property_id)
    )

    latest_supervisor = conn.execute(
        """
        SELECT * FROM property_supervisors
        WHERE property_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (property_id,)
    ).fetchone()

    if supervisor_name.strip() or supervisor_phone.strip():
        if latest_supervisor:
            conn.execute(
                """
                UPDATE property_supervisors
                SET supervisor_name = ?, phone = ?, notes = ?
                WHERE id = ?
                """,
                (supervisor_name.strip(), supervisor_phone.strip(), notes.strip(), latest_supervisor["id"])
            )
        else:
            conn.execute(
                """
                INSERT INTO property_supervisors (property_id, supervisor_name, phone, notes)
                VALUES (?, ?, ?, ?)
                """,
                (property_id, supervisor_name.strip(), supervisor_phone.strip(), notes.strip())
            )
    elif latest_supervisor:
        conn.execute(
            """
            UPDATE property_supervisors
            SET supervisor_name = '', phone = '', notes = ?
            WHERE id = ?
            """,
            (notes.strip(), latest_supervisor["id"])
        )

    conn.commit()
    conn.close()
    return RedirectResponse(url="/property-management", status_code=303)


@app.get("/delete-property/{property_id}", response_class=HTMLResponse)
def delete_property_confirm(request: Request, property_id: int):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-management/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()
    conn.close()

    if not prop:
        return RedirectResponse(
            url=build_redirect_url("/property-management", error="الملك غير موجود"),
            status_code=303
        )

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>حذف الملك</h1>
<p>هل أنت متأكد من حذف الملك: <strong>{prop['name']}</strong>؟</p>

<div class="inventory-panel inventory-table-panel">
    <div class="inventory-note">هل أنت متأكد من حذف الملك؟ سيتم حذف جميع البيانات المرتبطة به نهائيًا.</div>
    <form action="/delete-property/{property_id}" method="post" style="display:flex;gap:10px;flex-wrap:wrap;">
        <button type="submit" class="glass-btn delete-btn">تأكيد الحذف</button>
        <a href="/property-management" class="glass-btn back-btn">⬅ رجوع</a>
    </form>
</div>

</div>
"""


@app.post("/delete-property/{property_id}")
def delete_property(request: Request, property_id: int):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-management/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    deleted = cascade_delete_property_records(property_id)
    if not deleted:
        return RedirectResponse(
            url=build_redirect_url("/property-management", error="الملك غير موجود"),
            status_code=303
        )
    return RedirectResponse(
        url=build_redirect_url("/property-management", message="تم حذف الملك بنجاح"),
        status_code=303
    )


def build_realestate_owner_property_dashboard(
    property_id: int,
    prop,
    units,
    contracts,
    maintenance_items,
    installment_rows,
    manual_expenses,
    attachments_map,
) -> str:
    today = date.today()
    total_units = len(units)
    installment_status_labels = {
        "unpaid": "غير مدفوعة",
        "paid": "مدفوعة",
        "late": "متأخرة",
    }

    expected_revenue = 0.0
    collected_revenue = 0.0
    contract_ids_with_installments = set()
    expiring_contracts = []
    due_soon_installments = []
    late_installments = []
    occupied_unit_ids = set()
    active_contracts_count = 0

    for row in installment_rows:
        contract_ids_with_installments.add(row["contract_id"])
        if not row["installment_id"]:
            continue
        amount = safe_amount(row["installment_amount"])
        expected_revenue += amount
        status_key = (row["installment_status"] or "").strip().lower()
        if status_key == "paid":
            collected_revenue += amount
        due_date_value = parse_safe_date(row["due_date"])
        remaining_days = (due_date_value - today).days if due_date_value else None
        item = {
            "unit_name": row["unit_name"] or "-",
            "tenant_name": row["tenant_name"] or "-",
            "amount": amount,
            "due_date": row["due_date"] or "-",
            "remaining_days": remaining_days,
            "status": installment_status_labels.get(status_key, row["installment_status"] or "-"),
        }
        if due_date_value and remaining_days is not None and 0 <= remaining_days <= 14 and status_key != "paid":
            due_soon_installments.append(item)
        if due_date_value and remaining_days is not None and remaining_days < 0 and status_key != "paid":
            late_installments.append(item)

    for contract in contracts:
        contract_status = compute_contract_status(contract["end_date"], contract["status"] or "-")
        if contract_status in {"ساري", "قريب ينتهي"}:
            active_contracts_count += 1
            if contract["unit_id"]:
                occupied_unit_ids.add(contract["unit_id"])
        if contract["id"] not in contract_ids_with_installments:
            expected_revenue += safe_amount(contract["rent"])
        end_date_value = parse_safe_date(contract["end_date"])
        if end_date_value:
            remaining_days = (end_date_value - today).days
            if 0 <= remaining_days <= 60:
                expiring_contracts.append({
                    "unit_name": contract["unit_name"] or "-",
                    "tenant_name": contract["tenant_name"] or "-",
                    "end_date": contract["end_date"] or "-",
                    "remaining_days": remaining_days,
                    "status": contract_status,
                })

    maintenance_total = 0.0
    open_important_maintenance = []
    for item in maintenance_items:
        amount = safe_amount(item["actual_cost"]) if safe_amount(item["actual_cost"]) > 0 else safe_amount(item["estimated_cost"])
        maintenance_total += amount
        item_keys = item.keys()
        status_value = (item["status"] if "status" in item_keys else "") or ""
        status_key = status_value.strip().lower()
        priority_value = (item["priority"] if "priority" in item_keys else "") or ""
        priority_key = priority_value.strip().lower()
        is_open = status_key not in {"completed", "مكتمل", "closed", "منتهي"}
        is_important = priority_key in {"high", "urgent", "critical", "عالية", "عالي", "حرجة"}
        if is_open and is_important:
            open_important_maintenance.append({
                "title": item["maintenance_type"] if "maintenance_type" in item_keys and item["maintenance_type"] else (item["title"] if "title" in item_keys and item["title"] else "-"),
                "unit_name": item["unit_name"] if "unit_name" in item_keys and item["unit_name"] else "-",
                "status": status_value or "-",
                "priority": priority_value or "-",
                "updated_at": (item["updated_at"] if "updated_at" in item_keys and item["updated_at"] else "") or (item["created_at"] if "created_at" in item_keys and item["created_at"] else "-"),
            })

    operational_total = sum(safe_amount(item["amount"]) for item in manual_expenses)
    total_expenses = maintenance_total + operational_total
    outstanding_revenue = max(expected_revenue - collected_revenue, 0.0)
    net_after_expenses = collected_revenue - total_expenses
    vacant_units_count = max(total_units - len(occupied_unit_ids), 0)
    occupancy_rate = (len(occupied_unit_ids) / total_units * 100) if total_units else 0.0

    summary_cards = f"""
    <section class="owner-kpi-section owner-reveal-group">
        <div class="owner-kpi-grid container-cards">
            <article class="owner-kpi-card owner-kpi-card-soft owner-reveal card" style="--owner-delay: 0.02s;">
                <span>عدد الوحدات</span>
                <strong data-count="{total_units}">{total_units}</strong>
            </article>
            <article class="owner-kpi-card owner-kpi-card-soft owner-reveal card" style="--owner-delay: 0.08s;">
                <span>نسبة الإشغال</span>
                <strong data-count="{occupancy_rate:.1f}" data-suffix="%">{occupancy_rate:,.1f}%</strong>
            </article>
            <article class="owner-kpi-card owner-kpi-card-gold owner-reveal card" style="--owner-delay: 0.14s;">
                <span>الإيراد المتوقع</span>
                <strong data-count="{expected_revenue:.2f}" data-suffix=" ريال">{expected_revenue:,.0f} ريال</strong>
            </article>
            <article class="owner-kpi-card owner-kpi-card-emerald owner-reveal card" style="--owner-delay: 0.20s;">
                <span>الإيراد المحصل</span>
                <strong data-count="{collected_revenue:.2f}" data-suffix=" ريال">{collected_revenue:,.0f} ريال</strong>
            </article>
        </div>
        <div class="row-bottom">
            <article class="owner-kpi-card owner-kpi-card-ruby owner-reveal card" style="--owner-delay: 0.26s;">
                <span>إجمالي المصروفات</span>
                <strong data-count="{total_expenses:.2f}" data-suffix=" ريال">{total_expenses:,.0f} ريال</strong>
            </article>
            <article class="owner-kpi-card {'owner-kpi-card-emerald' if net_after_expenses >= 0 else 'owner-kpi-card-ruby'} owner-reveal card" style="--owner-delay: 0.32s;">
                <span>الصافي بعد المصروفات</span>
                <strong data-count="{net_after_expenses:.2f}" data-suffix=" ريال">{net_after_expenses:,.0f} ريال</strong>
            </article>
            <article class="owner-kpi-card owner-kpi-card-soft owner-reveal card" style="--owner-delay: 0.38s;">
                <span>العقود النشطة</span>
                <strong data-count="{active_contracts_count}">{active_contracts_count}</strong>
            </article>
        </div>
    </section>
    """

    expiring_contracts_rows = "".join(
        f"""
        <tr>
            <td>{item['unit_name']}</td>
            <td>{item['tenant_name']}</td>
            <td>{item['end_date']}</td>
            <td>{item['remaining_days']} يوم</td>
            <td>{item['status']}</td>
        </tr>
        """
        for item in expiring_contracts
    )
    due_installment_rows = "".join(
        f"""
        <tr>
            <td>{item['unit_name']}</td>
            <td>{item['tenant_name']}</td>
            <td>{item['amount']:,.0f} ريال</td>
            <td>{item['due_date']}</td>
            <td>{item['remaining_days']} يوم</td>
            <td>{item['status']}</td>
        </tr>
        """
        for item in due_soon_installments
    )
    late_installment_rows = "".join(
        f"""
        <tr>
            <td>{item['unit_name']}</td>
            <td>{item['tenant_name']}</td>
            <td>{item['amount']:,.0f} ريال</td>
            <td>{item['due_date']}</td>
            <td>{abs(item['remaining_days'])} يوم</td>
            <td>{item['status']}</td>
        </tr>
        """
        for item in late_installments
    )
    maintenance_rows = "".join(
        f"""
        <tr>
            <td>{item['title']}</td>
            <td>{item['unit_name']}</td>
            <td>{item['status']}</td>
            <td>{item['priority']}</td>
            <td>{item['updated_at']}</td>
        </tr>
        """
        for item in open_important_maintenance
    )
    vacant_units_rows = "".join(
        f"""
        <tr>
            <td>{unit['name'] or '-'}</td>
            <td>{unit['type'] or '-'}</td>
            <td>{safe_amount(unit['rent']):,.0f} ريال</td>
            <td>{unit['status'] or 'شاغرة'}</td>
        </tr>
        """
        for unit in units
        if unit["id"] not in occupied_unit_ids or (unit["status"] or "").strip() == "شاغرة"
    )

    documents_rows = ""
    for contract in contracts:
        for attachment in attachments_map.get(contract["id"], []):
            file_name = escape(attachment["file_name"] or "ملف مرفق")
            documents_rows += f"""
            <tr>
                <td>{contract['unit_name'] or '-'}</td>
                <td>{contract['tenant_name'] or '-'}</td>
                <td>{file_name}</td>
                <td><a href="/contract-attachments/{attachment['id']}" class="action-btn">عرض / تنزيل</a></td>
            </tr>
            """

    expense_distribution_labels = []
    expense_distribution_values = []
    if maintenance_total > 0:
        expense_distribution_labels.append("الصيانة")
        expense_distribution_values.append(round(maintenance_total, 2))
    if operational_total > 0:
        expense_distribution_labels.append("التشغيل")
        expense_distribution_values.append(round(operational_total, 2))

    revenue_chart_config = json.dumps({
        "labels": ["الإيراد المتوقع", "الإيراد المحصل", "المتبقي للتحصيل"],
        "values": [
            round(expected_revenue, 2),
            round(collected_revenue, 2),
            round(outstanding_revenue, 2),
        ],
    }, ensure_ascii=False)
    occupancy_chart_config = json.dumps({
        "labels": ["الوحدات المؤجرة", "الوحدات الشاغرة"],
        "values": [len(occupied_unit_ids), vacant_units_count],
    }, ensure_ascii=False)
    expense_chart_config = json.dumps({
        "labels": expense_distribution_labels,
        "values": expense_distribution_values,
    }, ensure_ascii=False)

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
.owner-page {{ }}
.owner-dashboard {{ position: relative; padding: 30px 0 56px; }}
.owner-dashboard::before {{ content: ""; position: absolute; inset: 0; pointer-events: none; background: radial-gradient(circle at 12% 0%, rgba(245, 235, 218, 0.26), transparent 28%), radial-gradient(circle at 88% 0%, rgba(228, 214, 194, 0.22), transparent 24%), linear-gradient(180deg, rgba(70, 55, 38, 0.10), rgba(70, 55, 38, 0)); }}
.owner-shell {{ position: relative; display: grid; gap: 24px; }}
.owner-hero {{ position: relative; overflow: hidden; border-radius: 30px; padding: 32px; border: 1px solid rgba(240, 225, 204, 0.30); background: linear-gradient(135deg, rgba(255,255,255,0.18), rgba(187, 169, 145, 0.18)), radial-gradient(circle at top left, rgba(248, 239, 225, 0.24), transparent 36%); box-shadow: 0 18px 42px rgba(181, 153, 120, 0.12), 0 34px 70px rgba(138, 112, 83, 0.10); }}
.owner-hero::after {{ content: ""; position: absolute; left: -40px; bottom: -90px; width: 260px; height: 260px; border-radius: 999px; background: radial-gradient(circle, rgba(255, 248, 239, 0.28), transparent 68%); }}
.owner-hero-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 24px; }}
.owner-hero-copy h1 {{ margin: 0 0 10px; font-size: clamp(2.1rem, 4vw, 3.2rem); line-height: 1.02; letter-spacing: -0.04em; }}
.owner-hero-copy p {{ margin: 0; color: rgba(102, 84, 65, 0.88); }}
.owner-page .owner-dashboard h1 {{ color: #5f4b32 !important; }}
.owner-page .owner-dashboard h2,
.owner-page .owner-dashboard h3,
.owner-page .owner-dashboard h4,
.owner-page .owner-dashboard th,
.owner-page .owner-dashboard .owner-section-pill,
.owner-page .owner-dashboard .owner-badge,
.owner-page .owner-dashboard .owner-kpi-card span,
.owner-page .owner-dashboard .finance-chart-card h3,
.owner-page .owner-dashboard .inventory-panel h3 {{ color: #7a6447 !important; }}
.owner-page .owner-dashboard p,
.owner-page .owner-dashboard small,
.owner-page .owner-dashboard .finance-chart-note,
.owner-page .owner-dashboard .section-subtitle,
.owner-page .owner-dashboard .glass-note,
.owner-page .owner-dashboard .inventory-note,
.owner-page .owner-dashboard .owner-section-header p,
.owner-page .owner-dashboard .owner-hero-copy p,
.owner-page .owner-dashboard .empty-state {{ color: #8f7a5c !important; }}
.owner-page .owner-dashboard td,
.owner-page .owner-dashboard span,
.owner-page .owner-dashboard label,
.owner-page .owner-dashboard li,
.owner-page .owner-dashboard .owner-status-badge,
.owner-page .owner-dashboard .owner-table td,
.owner-page .owner-dashboard .owner-table-wrap *,
.owner-page .owner-dashboard .owner-badge strong,
.owner-page .owner-dashboard .inventory-note strong {{ color: #6b573d; }}
.owner-hero-badges {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.owner-badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 10px 16px; border-radius: 999px; border: 1px solid rgba(235, 220, 198, 0.34); background: linear-gradient(180deg, rgba(255,255,255,0.22), rgba(216, 198, 173, 0.16)); color: #5e4b39; box-shadow: inset 0 1px 0 rgba(255,255,255,0.46), 0 10px 24px rgba(180, 151, 116, 0.10); }}
.owner-badge strong {{ color: #7b644d; }}
.owner-kpi-section {{ margin-top: 30px; display: grid; gap: 20px; }}
.owner-kpi-grid, .container-cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 20px; }}
.row-bottom {{ display: flex; justify-content: center; gap: 20px; flex-wrap: wrap; }}
.row-bottom .owner-kpi-card, .row-bottom .card {{ width: 250px; max-width: 100%; }}
.owner-kpi-card, .card {{ position: relative; overflow: hidden; min-height: 120px; padding: 25px; border-radius: 20px; border: 1px solid rgba(180, 150, 100, 0.15); background: rgba(245, 240, 230, 0.68); box-shadow: 0 10px 25px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.4); display: flex; flex-direction: column; justify-content: space-between; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }}
.owner-kpi-card:hover, .card:hover {{ transform: translateY(-10px) scale(1.02); box-shadow: 0 28px 52px rgba(0,0,0,0.14), 0 0 0 1px rgba(214, 195, 163, 0.34), inset 0 1px 0 rgba(255,255,255,0.56); border-color: rgba(184, 155, 109, 0.34); background: rgba(248, 243, 234, 0.82); z-index: 2; }}
.owner-kpi-card::after {{ content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(to right, #d6c3a3, #b89b6d); border-radius: 20px 20px 0 0; opacity: 0.6; pointer-events: none; }}
.owner-kpi-card::before {{ content: ""; position: absolute; inset: 0 auto auto 0; width: 100%; height: 4px; opacity: 0.85; box-shadow: 0 0 18px rgba(231, 214, 188, 0.24); }}
.owner-kpi-card span {{ position: relative; z-index: 1; color: #8c7a5a; font-size: 0.94rem; }}
.owner-kpi-card strong {{ position: relative; z-index: 1; color: #4e3f2a; text-shadow: 0 1px 12px rgba(255, 255, 255, 0.22); font-size: clamp(1.55rem, 2.7vw, 2.3rem); line-height: 1.06; letter-spacing: -0.04em; }}
.owner-kpi-card-soft::before {{ background: linear-gradient(90deg, #eadcca, #c8b091); }}
.owner-kpi-card-soft {{ background: rgba(245, 240, 230, 0.68); }}
.owner-kpi-card-gold::before {{ background: linear-gradient(90deg, #e7d7bf, #c7a982); }}
.owner-kpi-card-gold {{ background: rgba(245, 240, 230, 0.68); }}
.owner-kpi-card-emerald::before {{ background: linear-gradient(90deg, #ddd7c2, #b4ac8f); }}
.owner-kpi-card-emerald {{ background: rgba(245, 240, 230, 0.68); }}
.owner-kpi-card-ruby::before {{ background: linear-gradient(90deg, #e5d1bf, #c6a38b); }}
.owner-kpi-card-ruby {{ background: rgba(245, 240, 230, 0.68); }}
.system-topbar {{ z-index: 24; }}
.owner-section {{ border-radius: 28px; padding: 24px; border: 1px solid rgba(236, 220, 201, 0.30); background: linear-gradient(180deg, rgba(255,255,255,0.74), rgba(234, 223, 206, 0.66)); box-shadow: 0 18px 44px rgba(176, 146, 112, 0.10), 0 1px 0 rgba(255,255,255,0.72) inset; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }}
.owner-section:hover {{ transform: translateY(-10px) scale(1.02); box-shadow: 0 28px 52px rgba(0,0,0,0.14), 0 0 0 1px rgba(214, 195, 163, 0.30), 0 1px 0 rgba(255,255,255,0.72) inset; border-color: rgba(184, 155, 109, 0.32); transition: transform 0.36s cubic-bezier(.22,1,.36,1), box-shadow 0.36s cubic-bezier(.22,1,.36,1), border-color 0.36s cubic-bezier(.22,1,.36,1); z-index: 2; }}
.owner-section-header {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 18px; }}
.owner-section-header h3 {{ margin: 0; font-size: 1.28rem; color: #7a6447; }}
.owner-section-header p {{ margin: 6px 0 0; color: #8f7a5c; }}
.owner-section-pill {{ padding: 9px 14px; border-radius: 999px; background: rgba(255,255,255,0.16); border: 1px solid rgba(233, 216, 196, 0.26); color: #7a6447; font-weight: 700; white-space: nowrap; }}
.owner-chart-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }}
.owner-chart-card {{ min-height: 360px; border: 1px solid rgba(236, 220, 199, 0.28) !important; background: linear-gradient(180deg, rgba(255,255,255,0.76), rgba(236, 223, 205, 0.68)) !important; box-shadow: 0 18px 40px rgba(176, 146, 112, 0.10), 0 1px 0 rgba(255,255,255,0.68) inset !important; }}
.owner-chart-card:hover {{ transform: translateY(-10px) scale(1.02); box-shadow: 0 28px 52px rgba(0,0,0,0.14), 0 0 0 1px rgba(214, 195, 163, 0.34), 0 1px 0 rgba(255,255,255,0.68) inset !important; border-color: rgba(184, 155, 109, 0.34) !important; z-index: 2; }}
.owner-table-wrap {{ overflow-x: auto; border-radius: 22px; border: 1px solid rgba(236, 220, 201, 0.28); background: linear-gradient(180deg, rgba(255,255,255,0.78), rgba(238, 227, 210, 0.72)); box-shadow: 0 16px 34px rgba(176, 146, 112, 0.10), 0 1px 0 rgba(255,255,255,0.68) inset; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }}
.owner-table {{ width: 100%; border-collapse: collapse; }}
.owner-table th {{ text-align: right; padding: 14px 16px; background: linear-gradient(180deg, rgba(255, 251, 246, 0.82), rgba(241, 232, 217, 0.64)); color: #7a6447; border-bottom: 1px solid rgba(230, 212, 191, 0.24); box-shadow: 0 -1px 0 rgba(255,255,255,0.38) inset; }}
.owner-table td {{ padding: 14px 16px; color: #6b573d; border-bottom: 1px solid rgba(228, 212, 191, 0.12); background: rgba(252, 248, 242, 0.76); }}
.owner-table tr:nth-child(even) td {{ background: rgba(245, 238, 228, 0.66); }}
.owner-table tr:hover td {{ background: rgba(239, 229, 214, 0.82); }}
.owner-status-badge {{ display: inline-flex; align-items: center; padding: 6px 12px; border-radius: 999px; border: 1px solid rgba(230, 213, 191, 0.22); background: rgba(255,255,255,0.72); font-size: 0.88rem; }}
.owner-status-badge-warning {{ color: #6b573d; }}
.owner-status-badge-danger {{ color: #6b573d; }}
.owner-status-badge-info {{ color: #6b573d; }}
.owner-dashboard .finance-chart-note {{ color: #8f7a5c; }}
.owner-dashboard .inventory-note {{ color: #8f7a5c; border-color: rgba(231, 214, 191, 0.28); background: linear-gradient(180deg, rgba(255,255,255,0.74), rgba(239, 229, 214, 0.68)); box-shadow: 0 10px 24px rgba(175, 147, 116, 0.08); }}
.owner-dashboard .action-btn, .owner-dashboard .glass-btn {{ background: linear-gradient(180deg, rgba(255,255,255,0.22), rgba(231, 216, 196, 0.20)) !important; color: #5b4838 !important; border: 1px solid rgba(232, 214, 191, 0.28) !important; box-shadow: 0 10px 20px rgba(178, 149, 116, 0.08), inset 0 1px 0 rgba(255,255,255,0.62); }}
.owner-dashboard .action-btn:hover, .owner-dashboard .glass-btn:hover {{ background: linear-gradient(180deg, rgba(255,255,255,0.30), rgba(236, 222, 202, 0.24)) !important; color: #4c3b2d !important; }}
.owner-reveal {{ opacity: 1; transform: translateY(0); animation: premium-fade-up 0.75s cubic-bezier(.22,1,.36,1) both; animation-delay: var(--owner-delay, 0s); will-change: opacity, transform; }}
.owner-reveal.is-visible {{ opacity: 1; transform: translateY(0); }}
@media (max-width: 960px) {{ .owner-kpi-grid, .container-cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
@media (max-width: 720px) {{ .owner-hero {{ padding: 24px; }} .owner-hero-top, .owner-section-header {{ flex-direction: column; align-items: flex-start; }} .owner-kpi-grid, .container-cards {{ grid-template-columns: 1fr; }} .row-bottom .owner-kpi-card, .row-bottom .card {{ width: 100%; }} }}
</style>
<body class="system-dark owner-page">
{HOME_BUTTON}
<div class="dashboard owner-dashboard">
    <div class="owner-shell">
        <section class="owner-hero owner-reveal">
            <div class="owner-hero-top">
                <div class="owner-hero-copy">
                    <h1>{prop['name']}</h1>
                    <p>{prop['location'] or 'بدون موقع محدد'} | النوع: {prop['property_type'] or '-'} | الحالة: {prop['status'] or '-'}</p>
                </div>
                <a href="/property-management" class="glass-btn back-btn">⬅ رجوع</a>
            </div>
            <div class="owner-hero-badges">
                <div class="owner-badge">المتبقي للتحصيل <strong>{outstanding_revenue:,.0f} ريال</strong></div>
                <div class="owner-badge">عقود تنتهي قريبًا <strong>{len(expiring_contracts)}</strong></div>
                <div class="owner-badge">دفعات متأخرة <strong>{len(late_installments)}</strong></div>
                <div class="owner-badge">الصيانة المفتوحة المهمة <strong>{len(open_important_maintenance)}</strong></div>
            </div>
        </section>

        {summary_cards}

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>المؤشرات البصرية</h3>
                    <p>لوحة مالك أنيقة تركز على التحصيل والإشغال والمصروفات بشكل واضح ومريح.</p>
                </div>
                <div class="owner-section-pill">عرض استثماري</div>
            </div>
            <div class="owner-chart-grid">
                <div class="inventory-panel finance-chart-card owner-chart-card owner-reveal" style="--owner-delay: 0.04s;">
            <h3>ملخص الإيرادات والتحصيل</h3>
            <p class="finance-chart-note">مقارنة مباشرة بين المتوقع والمحصل والمتبقي للتحصيل.</p>
            <div class="finance-line-stage">
                <canvas id="ownerRevenueOverviewChart"></canvas>
            </div>
        </div>

        <div class="inventory-panel finance-chart-card owner-chart-card owner-reveal" style="--owner-delay: 0.10s;">
            <h3>حالة الإشغال</h3>
            <p class="finance-chart-note">توزيع الوحدات بين المؤجر والشاغر بشكل بصري واضح.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="ownerOccupancyChart"></canvas>
                </div>
            </div>
        </div>

        <div class="inventory-panel finance-chart-card owner-chart-card owner-reveal" style="--owner-delay: 0.16s;">
            <h3>توزيع المصروفات</h3>
            <p class="finance-chart-note">عرض مبسط لتوزيع الصيانة مقابل التشغيل.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="ownerExpenseDistributionChart"></canvas>
                </div>
            </div>
            {'<div class="inventory-note">لا توجد مصروفات مسجلة بعد</div>' if not expense_distribution_values else ''}
        </div>
    </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>العقود التي تنتهي قريبًا</h3>
                    <p>العقود التي تستحق متابعة مبكرة للحفاظ على استمرارية الإشغال.</p>
                </div>
                <div class="owner-section-pill">{len(expiring_contracts)} عقد</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>تاريخ النهاية</th>
                    <th>الأيام المتبقية</th>
                    <th>الحالة</th>
                </tr>
                {expiring_contracts_rows if expiring_contracts_rows else "<tr><td colspan='5'>لا توجد عقود تنتهي قريبًا</td></tr>"}
                </table>
            </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>الدفعات المستحقة قريبًا</h3>
                    <p>دفعات التحصيل القادمة خلال الفترة القريبة، مع تركيز على الوضوح والجاهزية.</p>
                </div>
                <div class="owner-section-pill">{len(due_soon_installments)} دفعة</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>المبلغ</th>
                    <th>تاريخ الاستحقاق</th>
                    <th>الأيام المتبقية</th>
                    <th>الحالة</th>
                </tr>
                {due_installment_rows if due_installment_rows else "<tr><td colspan='6'>لا توجد دفعات مستحقة قريبًا</td></tr>"}
                </table>
            </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>الدفعات المتأخرة</h3>
                    <p>حالات التحصيل المتأخرة التي قد تتطلب متابعة مباشرة أو مراجعة سريعة.</p>
                </div>
                <div class="owner-section-pill">{len(late_installments)} حالة</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>المبلغ</th>
                    <th>تاريخ الاستحقاق</th>
                    <th>أيام التأخير</th>
                    <th>الحالة</th>
                </tr>
                {late_installment_rows if late_installment_rows else "<tr><td colspan='6'>لا توجد دفعات متأخرة</td></tr>"}
                </table>
            </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>الوحدات الشاغرة</h3>
                    <p>الوحدات غير المؤجرة حاليًا وفقًا لحالة الإشغال الحالية في العقار.</p>
                </div>
                <div class="owner-section-pill">{vacant_units_count} وحدة</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                    <tr>
                        <th>الوحدة</th>
                        <th>النوع</th>
                        <th>الإيجار</th>
                        <th>الحالة</th>
                    </tr>
                    {vacant_units_rows if vacant_units_rows else "<tr><td colspan='4'>لا توجد وحدات شاغرة</td></tr>"}
                </table>
            </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>المستندات والمرفقات</h3>
                    <p>الوصول السريع إلى مستندات العقود والمرفقات المتاحة حاليًا للعرض والتنزيل.</p>
                </div>
                <div class="owner-section-pill">مرفقات العقود</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>اسم الملف</th>
                    <th>الإجراء</th>
                </tr>
                {documents_rows if documents_rows else "<tr><td colspan='4'>لا توجد مستندات مرفوعة بعد</td></tr>"}
                </table>
            </div>
        </section>

        <section class="owner-section owner-reveal">
            <div class="owner-section-header">
                <div>
                    <h3>الصيانة المفتوحة المهمة</h3>
                    <p>الطلبات ذات الأولوية الأعلى فقط، مع عرض بسيط وواضح دون تفاصيل تشغيلية زائدة.</p>
                </div>
                <div class="owner-section-pill">{len(open_important_maintenance)} طلب</div>
            </div>
            <div class="owner-table-wrap">
                <table class="owner-table">
                <tr>
                    <th>النوع</th>
                    <th>الوحدة</th>
                    <th>الحالة</th>
                    <th>الأولوية</th>
                    <th>آخر تحديث</th>
                </tr>
                {maintenance_rows if maintenance_rows else "<tr><td colspan='5'>لا توجد طلبات صيانة مهمة مفتوحة</td></tr>"}
                </table>
            </div>
        </section>

        <a href="/property-management" class="glass-btn back-btn owner-reveal">⬅ رجوع</a>
    </div>
</div>
<script>
const ownerRevenueOverviewConfig = {revenue_chart_config};
const ownerOccupancyConfig = {occupancy_chart_config};
const ownerExpenseDistributionConfig = {expense_chart_config};

function ownerFormatNumber(value, suffix = '') {{
    const numericValue = Number(value || 0);
    const hasDecimal = Math.abs(numericValue % 1) > 0.001;
    const formatted = numericValue.toLocaleString('en-US', {{
        minimumFractionDigits: hasDecimal ? 1 : 0,
        maximumFractionDigits: hasDecimal ? 1 : 0
    }});
    return `${{formatted}}${{suffix}}`;
}}

function ownerBuildGradient(ctx, area, startColor, endColor) {{
    const gradient = ctx.createLinearGradient(0, area.bottom, 0, area.top);
    gradient.addColorStop(0, endColor);
    gradient.addColorStop(1, startColor);
    return gradient;
}}

const ownerPremiumShadowPlugin = {{
    id: 'ownerPremiumShadowPlugin',
    beforeDatasetDraw(chart, args) {{
        const ctx = chart.ctx;
        ctx.save();
        ctx.shadowColor = 'rgba(173, 143, 108, 0.20)';
        ctx.shadowBlur = 18;
        ctx.shadowOffsetY = 10;
    }},
    afterDatasetDraw(chart, args) {{
        chart.ctx.restore();
    }}
}};

function ownerCreateRevenueChart(canvasId, config) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;
    new Chart(canvas, {{
        type: 'bar',
        data: {{
            labels: config.labels,
            datasets: [{{
                data: config.values,
                borderRadius: 16,
                borderSkipped: false,
                backgroundColor(context) {{
                    const area = context.chart.chartArea;
                    const palettes = [
                        ['rgba(243, 232, 216, 0.96)', 'rgba(196, 171, 139, 0.96)'],
                        ['rgba(233, 225, 209, 0.96)', 'rgba(168, 153, 126, 0.96)'],
                        ['rgba(242, 228, 220, 0.96)', 'rgba(181, 152, 130, 0.96)']
                    ];
                    const palette = palettes[context.dataIndex % palettes.length];
                    if (!area) return palette[0];
                    return ownerBuildGradient(context.chart.ctx, area, palette[0], palette[1]);
                }},
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{
                    rtl: true,
                    callbacks: {{
                        label(context) {{
                            return `${{Number(context.raw).toLocaleString('en-US')}} ريال`;
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    ticks: {{ color: '#6b5a49' }},
                    grid: {{ display: false }}
                }},
                y: {{
                    ticks: {{ color: '#6b5a49' }},
                    grid: {{ color: 'rgba(186, 163, 133, 0.14)' }}
                }}
            }}
        }},
        plugins: [ownerPremiumShadowPlugin]
    }});
}}

function ownerCreateDoughnutChart(canvasId, config) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;
    const palettes = [
        ['rgba(243, 232, 216, 0.96)', 'rgba(196, 171, 139, 0.96)'],
        ['rgba(233, 225, 209, 0.96)', 'rgba(168, 153, 126, 0.96)'],
        ['rgba(242, 228, 220, 0.96)', 'rgba(181, 152, 130, 0.96)'],
        ['rgba(234, 221, 204, 0.96)', 'rgba(159, 134, 111, 0.96)']
    ];
    new Chart(canvas, {{
        type: 'doughnut',
        data: {{
            labels: config.labels,
            datasets: [{{
                data: config.values,
                hoverOffset: 12,
                borderWidth: 2,
                borderColor: 'rgba(255,255,255,0.16)',
                backgroundColor(context) {{
                    const area = context.chart.chartArea;
                    const palette = palettes[context.dataIndex % palettes.length];
                    if (!area) return palette[0];
                    return ownerBuildGradient(context.chart.ctx, area, palette[0], palette[1]);
                }}
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            cutout: '56%',
            plugins: {{
                legend: {{
                    position: 'bottom',
                    rtl: true,
                    labels: {{
                        color: '#6b5a49',
                        usePointStyle: true,
                        padding: 18
                    }}
                }},
                tooltip: {{
                    rtl: true,
                    callbacks: {{
                        label(context) {{
                            return `${{context.label}}: ${{Number(context.raw).toLocaleString('en-US')}}`;
                        }}
                    }}
                }}
            }}
        }},
        plugins: [ownerPremiumShadowPlugin]
    }});
}}

function ownerAnimateCounters() {{
    const counters = document.querySelectorAll('[data-count]');
    const observer = new IntersectionObserver((entries) => {{
        entries.forEach((entry) => {{
            if (!entry.isIntersecting || entry.target.dataset.animated === 'true') return;
            entry.target.dataset.animated = 'true';
            const target = Number(entry.target.dataset.count || 0);
            const suffix = entry.target.dataset.suffix || '';
            const duration = 1400;
            const start = performance.now();
            const tick = (now) => {{
                const progress = Math.min((now - start) / duration, 1);
                const eased = 1 - Math.pow(1 - progress, 3);
                const value = target * eased;
                entry.target.textContent = ownerFormatNumber(value, suffix);
                if (progress < 1) {{
                    requestAnimationFrame(tick);
                }} else {{
                    entry.target.textContent = ownerFormatNumber(target, suffix);
                }}
            }};
            requestAnimationFrame(tick);
            observer.unobserve(entry.target);
        }});
    }}, {{ threshold: 0.45 }});
    counters.forEach((counter) => observer.observe(counter));
}}

function ownerInitReveal() {{
    const elements = document.querySelectorAll('.owner-reveal');
    const observer = new IntersectionObserver((entries) => {{
        entries.forEach((entry) => {{
            if (entry.isIntersecting) {{
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target);
            }}
        }});
    }}, {{
        threshold: 0.16,
        rootMargin: '0px 0px -40px 0px'
    }});
    elements.forEach((element) => observer.observe(element));
}}

function ownerEnhanceTableStatuses() {{
    document.querySelectorAll('.owner-table td:last-child').forEach((cell) => {{
        const value = (cell.textContent || '').trim();
        if (!value || cell.querySelector('a')) return;
        let badgeClass = 'owner-status-badge-info';
        if (value.includes('متأخر') || value.includes('منتهي')) {{
            badgeClass = 'owner-status-badge-danger';
        }} else if (value.includes('قريب') || value.includes('عالية') || value.includes('حرجة')) {{
            badgeClass = 'owner-status-badge-warning';
        }}
        cell.innerHTML = `<span class="owner-status-badge ${{badgeClass}}">${{value}}</span>`;
    }});
}}

ownerCreateRevenueChart('ownerRevenueOverviewChart', ownerRevenueOverviewConfig);
ownerCreateDoughnutChart('ownerOccupancyChart', ownerOccupancyConfig);
ownerCreateDoughnutChart('ownerExpenseDistributionChart', ownerExpenseDistributionConfig);
ownerEnhanceTableStatuses();
ownerInitReveal();
ownerAnimateCounters();
</script>
"""


@app.get("/property-management/{property_id}", response_class=HTMLResponse)
def property_management_dashboard(request: Request, property_id: int):
    access_result = ensure_realestate_property_management_access(request, property_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    sync_property_contracts_and_units(conn, property_id)
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()

    if not prop:
        conn.close()
        return "<h2>العقار غير موجود</h2>"

    units = conn.execute("SELECT * FROM property_units WHERE property_id = ? ORDER BY id DESC", (property_id,)).fetchall()
    supervisors = conn.execute(
        "SELECT * FROM property_supervisors WHERE property_id = ? ORDER BY id DESC",
        (property_id,)
    ).fetchall()
    maintenance = conn.execute(
        """
        SELECT maintenance_requests.*,
               property_units.name AS unit_name,
               property_tenants.name AS tenant_name
        FROM maintenance_requests
        LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
        WHERE maintenance_requests.property_id = ?
        ORDER BY maintenance_requests.id DESC
        """,
        (property_id,)
    ).fetchall()
    contracts = conn.execute(
        """
        SELECT property_rent_contracts.*,
               property_units.name AS unit_name,
               property_tenants.name AS tenant_name
        FROM property_rent_contracts
        LEFT JOIN property_units ON property_units.id = property_rent_contracts.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = property_rent_contracts.tenant_id
        WHERE property_rent_contracts.property_id = ?
        ORDER BY property_rent_contracts.id DESC
        """,
        (property_id,)
    ).fetchall()
    installment_rows = get_property_contract_installment_rows(conn, property_id)
    manual_expenses = conn.execute(
        """
        SELECT property_expenses.*, property_units.name AS unit_name
        FROM property_expenses
        LEFT JOIN property_units ON property_units.id = property_expenses.unit_id
        WHERE property_expenses.property_id = ?
        ORDER BY property_expenses.id DESC
        """,
        (property_id,)
    ).fetchall()
    attachments_map = get_contract_attachment_rows(
        conn,
        CONTRACT_ATTACHMENT_SOURCE_PROPERTY,
        [row["id"] for row in contracts],
    )
    tenants = conn.execute(
        "SELECT * FROM property_tenants WHERE property_id = ? ORDER BY id DESC",
        (property_id,)
    ).fetchall()
    conn.close()

    is_owner_view_only = realestate_owner_read_only(access_result)
    if is_owner_view_only:
        return build_realestate_owner_property_dashboard(
            property_id,
            prop,
            units,
            contracts,
            maintenance,
            installment_rows,
            manual_expenses,
            attachments_map,
        )
    can_view_tenants = not is_owner_view_only
    can_view_maintenance = not is_owner_view_only and not is_realestate_property_accounts_employee(access_result)
    can_view_supervisors = not is_owner_view_only and not is_employee(access_result)

    latest_supervisor = supervisors[0] if supervisors else None
    expected_revenue = 0.0
    collected_revenue = 0.0
    total_expenses = sum(((item["actual_cost"] or item["estimated_cost"]) or 0) for item in maintenance)
    today = date.today()
    expiring_contracts = []
    due_soon_installments = []
    contract_ids_with_installments = set()
    contract_installment_counts = {}

    for row in installment_rows:
        contract_ids_with_installments.add(row["contract_id"])
        if row["installment_id"]:
            contract_installment_counts[row["contract_id"]] = contract_installment_counts.get(row["contract_id"], 0) + 1
            amount = safe_amount(row["installment_amount"])
            expected_revenue += amount
            status_key = (row["installment_status"] or "").strip().lower()
            if status_key == "paid":
                collected_revenue += amount
            due_date_value = parse_safe_date(row["due_date"])
            remaining_days = (due_date_value - today).days if due_date_value else None
            if due_date_value and remaining_days is not None and 0 < remaining_days <= 14 and status_key != "paid":
                due_soon_installments.append({
                    "unit_name": row["unit_name"] or "-",
                    "tenant_name": row["tenant_name"] or "-",
                    "amount": amount,
                    "due_date": row["due_date"] or "-",
                    "remaining_days": remaining_days,
                    "status": row["installment_status"] or "-",
                })

    for contract in contracts:
        if contract["id"] not in contract_ids_with_installments:
            expected_revenue += safe_amount(contract["rent"])
        end_date_value = parse_safe_date(contract["end_date"])
        if not end_date_value:
            continue
        remaining_days = (end_date_value - today).days
        if 0 <= remaining_days <= 60:
            expiring_contracts.append({
                "unit_name": contract["unit_name"] or "-",
                "tenant_name": contract["tenant_name"] or "-",
                "end_date": contract["end_date"] or "-",
                "remaining_days": remaining_days,
                "status": compute_contract_status(contract["end_date"], contract["status"] or "-"),
            })

    supervisor_cards = ""
    for sup in supervisors:
        supervisor_cards += f"""
        <div class="inventory-note">
            <strong>{sup['supervisor_name']}</strong><br>
            الهاتف: {sup['phone'] or '-'}<br>
            الملاحظات: {sup['notes'] or '-'}
        </div>
        """

    maintenance_rows = ""
    for item in maintenance:
        item_keys = item.keys()
        maintenance_title = item["maintenance_type"] if "maintenance_type" in item_keys and item["maintenance_type"] else (
            item["title"] if "title" in item_keys and item["title"] else "-"
        )
        unit_name = item["unit_name"] if "unit_name" in item_keys and item["unit_name"] else "-"
        tenant_name = item["tenant_name"] if "tenant_name" in item_keys and item["tenant_name"] else "-"
        assigned_to = item["assigned_to"] if "assigned_to" in item_keys and item["assigned_to"] else "-"
        request_status = item["status"] if "status" in item_keys and item["status"] else "-"
        priority = item["priority"] if "priority" in item_keys and item["priority"] else "-"
        estimated_cost = item["estimated_cost"] if "estimated_cost" in item_keys and item["estimated_cost"] else "-"
        actual_cost = item["actual_cost"] if "actual_cost" in item_keys and item["actual_cost"] else "-"
        created_at = item["created_at"] if "created_at" in item_keys and item["created_at"] else "-"
        final_report = item["final_report"] if "final_report" in item_keys and item["final_report"] else "-"
        maintenance_rows += f"""
        <tr>
            <td>{maintenance_title}</td>
            <td>{unit_name}</td>
            <td>{tenant_name}</td>
            <td>{assigned_to}</td>
            <td>{request_status}</td>
            <td>{priority}</td>
            <td>{estimated_cost}</td>
            <td>{actual_cost}</td>
            <td>{final_report}</td>
            <td>{created_at}</td>
        </tr>
        """

    expiring_contracts_rows = ""
    for contract in expiring_contracts:
        expiring_contracts_rows += f"""
        <tr>
            <td>{contract['unit_name']}</td>
            <td>{contract['tenant_name']}</td>
            <td>{contract['end_date']}</td>
            <td>{contract['remaining_days']} يوم</td>
        </tr>
        """

    due_installment_rows = ""
    for installment in due_soon_installments:
        due_installment_rows += f"""
        <tr>
            <td>{installment['unit_name']}</td>
            <td>{installment['tenant_name']}</td>
            <td>{installment['amount']:,.0f} ريال</td>
            <td>{installment['due_date']}</td>
            <td>{installment['remaining_days']} يوم</td>
            <td>{installment['status']}</td>
        </tr>
        """

    contracts_warning_badge = ""
    if expiring_contracts:
        contracts_warning_badge = f'<div class="company-card-warning">تنبيه: {len(expiring_contracts)} قرب الانتهاء</div>'
    installments_warning_badge = ""
    if due_soon_installments:
        installments_warning_badge = f'<div class="company-card-warning">دفعات قريبة: {len(due_soon_installments)}</div>'

    cards_html = f"""
        <a href="/property-units?property_id={property_id}" class="company-card realestate"><h2>الوحدات ({len(units)})</h2></a>
        <a href="/property-rental-contracts?property_id={property_id}" class="company-card realestate"><h2>العقود ({len(contracts)})</h2>{contracts_warning_badge}{installments_warning_badge}</a>
        <a href="/property-revenue/{property_id}" class="company-card realestate"><h2>الإيرادات المتوقعة<br>{int(expected_revenue):,} ريال</h2></a>
        <a href="/property-expenses/{property_id}" class="company-card realestate"><h2>المصروفات<br>{int(total_expenses):,} ريال</h2></a>
    """
    if can_view_maintenance:
        cards_html += f'<a href="/maintenance-management?property_id={property_id}" class="company-card realestate"><h2>الصيانة ({len(maintenance)})</h2></a>'
    if can_view_supervisors:
        cards_html += f'<a href="/property-supervisors?property_id={property_id}" class="company-card realestate"><h2>بيانات المشرف<br>{latest_supervisor["supervisor_name"] if latest_supervisor else "غير محدد"}</h2></a>'

    supervisor_section = ""
    if can_view_supervisors:
        supervisor_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>المشرفون المسؤولون</h3>
        {supervisor_cards if supervisor_cards else '<div class="inventory-note">لا يوجد مشرفون معينون لهذا العقار</div>'}
    </div>
        """

    contract_alerts_section = ""
    if expiring_contracts:
        contracts_label = "عقد" if len(expiring_contracts) == 1 else "عقود"
        contract_alerts_section = f"""
    <div class="inventory-warning-panel inventory-table-panel">
        <div class="inventory-warning-head">
            <div>
                <div class="inventory-warning-badge">العقود التي تنتهي قريبًا</div>
                <h3>يوجد {len(expiring_contracts)} {contracts_label} تنتهي خلال 60 يوم</h3>
                <p>مراجعة هذه العقود مبكرًا تساعد على تقليل الشواغر والانقطاع التشغيلي.</p>
            </div>
        </div>
        <div class="inventory-warning-table-wrap">
            <table class="inventory-warning-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>تاريخ النهاية</th>
                    <th>الأيام المتبقية</th>
                    <th>الحالة</th>
                </tr>
                {expiring_contracts_rows}
            </table>
        </div>
    </div>
        """

    installments_alert_section = ""
    if due_soon_installments:
        installments_label = "دفعة" if len(due_soon_installments) == 1 else "دفعات"
        installments_alert_section = f"""
    <div class="inventory-warning-panel inventory-table-panel finance-warning-soft">
        <div class="inventory-warning-head">
            <div>
                <div class="inventory-warning-badge">الدفعات المستحقة قريبًا</div>
                <h3>يوجد {len(due_soon_installments)} {installments_label} تستحق خلال 14 يوم</h3>
                <p>تم عرض الدفعات غير المدفوعة فقط ضمن نافذة الاستحقاق القريبة.</p>
            </div>
        </div>
        <div class="inventory-warning-table-wrap">
            <table class="inventory-warning-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>مبلغ الدفعة</th>
                    <th>تاريخ الاستحقاق</th>
                    <th>الأيام المتبقية</th>
                    <th>الحالة</th>
                </tr>
                {due_installment_rows}
            </table>
        </div>
    </div>
        """

    maintenance_section = ""
    if can_view_maintenance:
        maintenance_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>طلبات الصيانة</h3>
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>النوع</th>
                <th>الوحدة</th>
                <th>المستأجر</th>
                <th>المسند إليه</th>
                <th>الحالة</th>
                <th>الأولوية</th>
                <th>التقديري</th>
                <th>الفعلي</th>
                <th>التقرير النهائي</th>
                <th>التاريخ</th>
            </tr>
            {maintenance_rows if maintenance_rows else "<tr><td colspan='10'>لا توجد طلبات صيانة</td></tr>"}
        </table>
    </div>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>{prop['name']}</h1>
    <p>{prop['location'] or 'بدون موقع محدد'} | النوع: {prop['property_type'] or '-'} | الحالة: {prop['status'] or '-'}</p>

    <div class="companies">
        {cards_html}
    </div>

    {supervisor_section}
    {contract_alerts_section}
    {installments_alert_section}
    {maintenance_section}

    <a href="/property-management" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""
@app.get("/property-revenue/{property_id}", response_class=HTMLResponse)
def property_revenue_dashboard(request: Request, property_id: int):
    access_result = ensure_realestate_property_management_access(request, property_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    owner_guard = ensure_realestate_owner_dashboard_only(access_result, property_id)
    if not isinstance(owner_guard, sqlite3.Row):
        return owner_guard
    conn = get_db()
    sync_property_contracts_and_units(conn, property_id)
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()

    if not prop:
        conn.close()
        return "<h2>الملك غير موجود</h2>"

    units = conn.execute(
        "SELECT * FROM property_units WHERE property_id = ? ORDER BY id DESC",
        (property_id,)
    ).fetchall()
    contracts = conn.execute(
        """
        SELECT property_rent_contracts.*,
               property_units.name AS unit_name,
               property_tenants.name AS tenant_name
        FROM property_rent_contracts
        LEFT JOIN property_units ON property_units.id = property_rent_contracts.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = property_rent_contracts.tenant_id
        WHERE property_rent_contracts.property_id = ?
        ORDER BY property_rent_contracts.id DESC
        """,
        (property_id,)
    ).fetchall()
    installment_rows = get_property_contract_installment_rows(conn, property_id)
    maintenance_items = conn.execute(
        """
        SELECT maintenance_requests.*,
               property_units.name AS unit_name,
               property_tenants.name AS tenant_name
        FROM maintenance_requests
        LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
        WHERE maintenance_requests.property_id = ?
        ORDER BY maintenance_requests.id DESC
        """,
        (property_id,)
    ).fetchall()
    manual_expenses = conn.execute(
        """
        SELECT property_expenses.*
        FROM property_expenses
        WHERE property_expenses.property_id = ?
        ORDER BY property_expenses.id DESC
        """,
        (property_id,)
    ).fetchall()
    conn.close()

    today = date.today()
    expected_revenue = 0.0
    collected_revenue = 0.0
    total_expenses = 0.0
    outstanding_revenue = 0.0
    net_collected_revenue = 0.0
    revenue_chart_items = []
    expense_totals_by_unit = {}
    occupied_unit_ids = set()
    installment_status_labels = {
        "unpaid": "غير مدفوعة",
        "paid": "مدفوعة",
        "late": "متأخرة",
    }
    contract_ids_with_installments = set()
    contract_installment_number = {}

    for contract in contracts:
        if contract["unit_id"]:
            occupied_unit_ids.add(contract["unit_id"])

    revenue_rows = ""
    for row in installment_rows:
        contract_ids_with_installments.add(row["contract_id"])
        if row["installment_id"]:
            contract_installment_number[row["contract_id"]] = contract_installment_number.get(row["contract_id"], 0) + 1
            installment_number = contract_installment_number[row["contract_id"]]
            amount = safe_amount(row["installment_amount"])
            expected_revenue += amount
            status_key = (row["installment_status"] or "").strip().lower()
            if status_key == "paid":
                collected_revenue += amount
            revenue_rows += f"""
        <tr>
            <td>{row['unit_name'] or '-'}</td>
            <td>{row['tenant_name'] or '-'}</td>
            <td>{installment_number}</td>
            <td>{amount:,.0f} ريال</td>
            <td>{row['due_date'] or '-'}</td>
            <td>{installment_status_labels.get(status_key, row['installment_status'] or '-')}</td>
            <td>{row['paid_at'] or '-'}</td>
        </tr>
            """
            chart_label = f"{row['unit_name'] or 'وحدة غير محددة'} / {row['tenant_name'] or 'مستأجر غير محدد'}"
            existing_item = next((item for item in revenue_chart_items if item["label"] == chart_label), None)
            if existing_item:
                existing_item["amount"] += amount
            else:
                revenue_chart_items.append({"label": chart_label, "amount": amount})

    for contract in contracts:
        if contract["id"] not in contract_ids_with_installments:
            amount = safe_amount(contract["rent"])
            expected_revenue += amount
            revenue_rows += f"""
        <tr>
            <td>{contract['unit_name'] or '-'}</td>
            <td>{contract['tenant_name'] or '-'}</td>
            <td>-</td>
            <td>{amount:,.0f} ريال</td>
            <td>{contract['end_date'] or contract['start_date'] or '-'}</td>
            <td>غير مجدولة</td>
            <td>-</td>
        </tr>
            """
            revenue_chart_items.append({
                "label": f"{contract['unit_name'] or 'وحدة غير محددة'} / {contract['tenant_name'] or 'مستأجر غير محدد'}",
                "amount": amount,
            })

    for item in maintenance_items:
        expense_amount = safe_amount(item["actual_cost"]) if safe_amount(item["actual_cost"]) > 0 else safe_amount(item["estimated_cost"])
        total_expenses += expense_amount
        unit_label = item["unit_name"] or "بدون وحدة محددة"
        expense_totals_by_unit[unit_label] = expense_totals_by_unit.get(unit_label, 0.0) + expense_amount

    for item in manual_expenses:
        total_expenses += safe_amount(item["amount"])

    outstanding_revenue = expected_revenue - collected_revenue
    net_collected_revenue = collected_revenue - total_expenses
    vacant_units_count = sum(1 for unit in units if unit["id"] not in occupied_unit_ids or (unit["status"] or "").strip() == "شاغرة")

    expense_chart_items = [
        {"label": label, "amount": amount}
        for label, amount in sorted(expense_totals_by_unit.items(), key=lambda item: item[1], reverse=True)
    ]

    comparison_max = max(expected_revenue, collected_revenue, total_expenses, 1)

    comparison_chart_html = f"""
    <div class="finance-bar-item">
        <div class="finance-bar-meta">
            <strong>الإيرادات</strong>
            <span>{expected_revenue:,.0f} ريال</span>
        </div>
        <div class="finance-bar-track">
            <div class="finance-bar-fill finance-bar-fill-green" style="width: {max(8, int((expected_revenue / comparison_max) * 100))}%;"></div>
        </div>
    </div>
    <div class="finance-bar-item">
        <div class="finance-bar-meta">
            <strong>المحصل</strong>
            <span>{collected_revenue:,.0f} ريال</span>
        </div>
        <div class="finance-bar-track">
            <div class="finance-bar-fill finance-bar-fill-green" style="width: {max(8, int((collected_revenue / comparison_max) * 100))}%;"></div>
        </div>
    </div>
    <div class="finance-bar-item">
        <div class="finance-bar-meta">
            <strong>المصروفات</strong>
            <span>{total_expenses:,.0f} ريال</span>
        </div>
        <div class="finance-bar-track">
            <div class="finance-bar-fill finance-bar-fill-red" style="width: {max(8, int((total_expenses / comparison_max) * 100))}%;"></div>
        </div>
    </div>
    """

    expense_rows = ""
    for item in maintenance_items:
        maintenance_type = item["maintenance_type"] or item["title"] or "-"
        estimated_cost = safe_amount(item["estimated_cost"])
        actual_cost = safe_amount(item["actual_cost"])
        expense_rows += f"""
        <tr>
            <td>{maintenance_type}</td>
            <td>{item['unit_name'] or '-'}</td>
            <td>{estimated_cost:,.0f} ريال</td>
            <td>{actual_cost:,.0f} ريال</td>
            <td>{item['status'] or '-'}</td>
            <td>{item['created_at'] or '-'}</td>
        </tr>
        """

    warning_blocks = ""
    if expected_revenue > 0 and total_expenses >= expected_revenue * 0.6:
        warning_blocks += f"""
        <div class="inventory-warning-panel inventory-table-panel finance-warning-soft">
            <div class="inventory-warning-head">
                <div>
                    <div class="inventory-warning-badge">تنبيه المصروفات</div>
                    <h3>المصروفات مرتفعة مقارنة بالإيراد المتوقع</h3>
                    <p>بلغت المصروفات {total_expenses:,.0f} ريال مقابل إيراد متوقع {expected_revenue:,.0f} ريال.</p>
                </div>
            </div>
        </div>
        """

    net_card_class = "finance-card-positive" if net_collected_revenue >= 0 else "finance-card-negative"
    revenue_pie_items = [item for item in revenue_chart_items if item["amount"] > 0]
    expense_pie_items = [item for item in expense_chart_items if item["amount"] > 0]

    revenue_chart_config = json.dumps({
        "labels": [item["label"] for item in revenue_pie_items],
        "values": [round(item["amount"], 2) for item in revenue_pie_items],
    }, ensure_ascii=False)
    expense_chart_config = json.dumps({
        "labels": [item["label"] for item in expense_pie_items],
        "values": [round(item["amount"], 2) for item in expense_pie_items],
    }, ensure_ascii=False)

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <div class="finance-page-header">
        <div>
            <h1>لوحة الإيرادات</h1>
            <p>{prop['name']} | {prop['location'] or 'بدون موقع محدد'} | النوع: {prop['property_type'] or '-'}</p>
        </div>
        <a href="/property-management/{property_id}" class="glass-btn back-btn">⬅ رجوع</a>
    </div>

    {warning_blocks}

    <div class="finance-summary-grid">
        <div class="finance-card">
            <span>الإيراد المتوقع</span>
            <strong>{expected_revenue:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-positive">
            <span>الإيراد المحصل</span>
            <strong>{collected_revenue:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>الإيراد غير المحصل / المتبقي</span>
            <strong>{outstanding_revenue:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-expense">
            <span>إجمالي المصروفات</span>
            <strong>{total_expenses:,.0f} ريال</strong>
        </div>
        <div class="finance-card {net_card_class}">
            <span>صافي المحصل بعد المصروفات</span>
            <strong>{net_collected_revenue:,.0f} ريال</strong>
        </div>
    </div>

    <div class="finance-summary-grid finance-summary-grid-secondary">
        <div class="finance-card finance-card-secondary">
            <span>عدد العقود</span>
            <strong>{len(contracts)}</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>الوحدات الشاغرة</span>
            <strong>{vacant_units_count}</strong>
        </div>
    </div>

    <div class="finance-chart-grid">
        <div class="inventory-panel finance-chart-card">
            <h3>الإيرادات حسب العقود</h3>
            <p class="finance-chart-note">مخطط Doughnut بأسلوب ثلاثي الأبعاد بصريًا يوضح مساهمة كل عقد في إجمالي الإيراد.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="revenueContributionChart"></canvas>
                </div>
            </div>
            {'<div class="inventory-note">لا توجد عقود إيراد لعرضها</div>' if not revenue_pie_items else ''}
        </div>

        <div class="inventory-panel finance-chart-card">
            <h3>مصروفات الصيانة حسب الوحدة</h3>
            <p class="finance-chart-note">تم تجميع التكاليف على مستوى الوحدة مع تأثير عمق وإضاءة لعرض توزيع المصروفات بوضوح.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="maintenanceDistributionChart"></canvas>
                </div>
            </div>
            {'<div class="inventory-note">لا توجد مصروفات صيانة لعرضها</div>' if not expense_pie_items else ''}
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel finance-chart-card">
        <h3>مقارنة الإيرادات والمصروفات</h3>
        <p class="finance-chart-note">ملخص بصري سريع لحجم الإيراد مقابل المصروف.</p>
        <div class="finance-bar-chart">
            {comparison_chart_html}
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>تفاصيل الإيرادات</h3>
        <div class="inventory-warning-table-wrap">
            <table class="finance-table">
                <tr>
                    <th>الوحدة</th>
                    <th>المستأجر</th>
                    <th>رقم الدفعة</th>
                    <th>مبلغ الدفعة</th>
                    <th>تاريخ الاستحقاق</th>
                    <th>حالة الدفعة</th>
                    <th>تاريخ التحصيل</th>
                </tr>
                {revenue_rows if revenue_rows else "<tr><td colspan='7'>لا توجد بيانات إيرادات</td></tr>"}
            </table>
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>تفاصيل المصروفات</h3>
        <div class="inventory-warning-table-wrap">
            <table class="finance-table">
                <tr>
                    <th>نوع الصيانة</th>
                    <th>الوحدة</th>
                    <th>التكلفة التقديرية</th>
                    <th>التكلفة الفعلية</th>
                    <th>الحالة</th>
                    <th>التاريخ</th>
                </tr>
                {expense_rows if expense_rows else "<tr><td colspan='6'>لا توجد مصروفات صيانة</td></tr>"}
            </table>
        </div>
    </div>

    <a href="/property-management/{property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
<script>
const revenueChartConfig = {revenue_chart_config};
const maintenanceChartConfig = {expense_chart_config};

const premiumPalettes = [
    ["#f6d365", "#d4a24c"],
    ["#c8b6a6", "#8f7158"],
    ["#90caf9", "#356e9a"],
    ["#f7c59f", "#c77746"],
    ["#b8c0ff", "#5d69b3"],
    ["#c3aed6", "#7a4b9c"],
    ["#8fd3c7", "#2d7f73"],
    ["#f4e4ba", "#a27c3f"]
];

function hexToRgb(hex) {{
    const normalized = hex.replace('#', '');
    const value = normalized.length === 3
        ? normalized.split('').map((char) => char + char).join('')
        : normalized;
    const intValue = parseInt(value, 16);
    return {{
        r: (intValue >> 16) & 255,
        g: (intValue >> 8) & 255,
        b: intValue & 255
    }};
}}

function rgba(hex, alpha) {{
    const rgb = hexToRgb(hex);
    return `rgba(${{rgb.r}}, ${{rgb.g}}, ${{rgb.b}}, ${{alpha}})`;
}}

function buildGradient(ctx, area, startColor, endColor) {{
    const gradient = ctx.createLinearGradient(0, area.top, 0, area.bottom);
    gradient.addColorStop(0, rgba(startColor, 0.98));
    gradient.addColorStop(0.35, rgba(startColor, 0.92));
    gradient.addColorStop(1, rgba(endColor, 0.96));
    return gradient;
}}

const pie3DPlugin = {{
    id: 'pie3DPlugin',
    beforeDatasetDraw(chart, args, pluginOptions) {{
        const meta = chart.getDatasetMeta(args.index);
        const ctx = chart.ctx;
        ctx.save();
        meta.data.forEach((arc, index) => {{
            const x = arc.x;
            const y = arc.y + (pluginOptions.depth || 14);
            const startAngle = arc.startAngle;
            const endAngle = arc.endAngle;
            const outerRadius = arc.outerRadius;
            const innerRadius = arc.innerRadius;
            const palette = (pluginOptions.palette && pluginOptions.palette[index]) || ['#999999', '#666666'];
            ctx.beginPath();
            ctx.arc(x, y, outerRadius, startAngle, endAngle);
            ctx.arc(x, y, innerRadius, endAngle, startAngle, true);
            ctx.closePath();
            const shadowGradient = ctx.createLinearGradient(0, y - outerRadius, 0, y + outerRadius);
            shadowGradient.addColorStop(0, rgba(palette[1], 0.9));
            shadowGradient.addColorStop(1, rgba('#2b1d13', 0.72));
            ctx.fillStyle = shadowGradient;
            ctx.shadowColor = 'rgba(15, 23, 42, 0.28)';
            ctx.shadowBlur = 18;
            ctx.shadowOffsetY = 10;
            ctx.fill();
        }});
        ctx.restore();
    }},
    afterDatasetDraw(chart, args) {{
        const meta = chart.getDatasetMeta(args.index);
        const ctx = chart.ctx;
        ctx.save();
        meta.data.forEach((arc) => {{
            const x = arc.x;
            const y = arc.y;
            const glare = ctx.createRadialGradient(x - 30, y - 35, 5, x, y, arc.outerRadius);
            glare.addColorStop(0, 'rgba(255,255,255,0.34)');
            glare.addColorStop(0.42, 'rgba(255,255,255,0.09)');
            glare.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.beginPath();
            ctx.arc(x, y, arc.outerRadius - 1, arc.startAngle, arc.endAngle);
            ctx.arc(x, y, arc.innerRadius + 1, arc.endAngle, arc.startAngle, true);
            ctx.closePath();
            ctx.fillStyle = glare;
            ctx.fill();
        }});
        ctx.restore();
    }}
}};

function createPremium3DPieChart(canvasId, config, labelPrefix) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;

    const palette = config.values.map((_, index) => premiumPalettes[index % premiumPalettes.length]);

    new Chart(canvas, {{
        type: 'doughnut',
        data: {{
            labels: config.labels,
            datasets: [{{
                data: config.values,
                backgroundColor(context) {{
                    const chart = context.chart;
                    const area = chart.chartArea;
                    const colors = palette[context.dataIndex % palette.length];
                    if (!area) return colors[0];
                    return buildGradient(chart.ctx, area, colors[0], colors[1]);
                }},
                borderColor: palette.map((colors) => rgba(colors[1], 0.95)),
                borderWidth: 2,
                hoverBorderWidth: 3,
                hoverOffset: 16,
                spacing: 3
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            cutout: '54%',
            layout: {{
                padding: {{ top: 8, bottom: 26, left: 10, right: 10 }}
            }},
            plugins: {{
                legend: {{
                    position: 'bottom',
                    rtl: true,
                    labels: {{
                        color: '#f8fafc',
                        usePointStyle: true,
                        boxWidth: 12,
                        padding: 18,
                        font: {{
                            family: 'Tahoma, Arial, sans-serif',
                            size: 12
                        }}
                    }}
                }},
                tooltip: {{
                    rtl: true,
                    backgroundColor: 'rgba(15, 23, 42, 0.92)',
                    titleColor: '#ffffff',
                    bodyColor: '#f8fafc',
                    borderColor: 'rgba(255,255,255,0.18)',
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {{
                        label(context) {{
                            const dataset = context.dataset.data;
                            const total = dataset.reduce((sum, value) => sum + value, 0);
                            const current = context.raw || 0;
                            const percent = total ? ((current / total) * 100).toFixed(1) : '0.0';
                            return `${{labelPrefix}}: ${{context.label}} | ${{Number(current).toLocaleString('en-US')}} ريال | ${{percent}}%`;
                        }}
                    }}
                }},
                pie3DPlugin: {{
                    depth: 16,
                    palette
                }}
            }}
        }},
        plugins: [pie3DPlugin]
    }});
}}

createPremium3DPieChart('revenueContributionChart', revenueChartConfig, 'الإيراد');
createPremium3DPieChart('maintenanceDistributionChart', maintenanceChartConfig, 'المصروف');
</script>
"""


@app.get("/property-expenses/{property_id}", response_class=HTMLResponse)
def property_expenses_dashboard(request: Request, property_id: int, message: str = "", error: str = ""):
    access_result = ensure_realestate_property_management_access(request, property_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    owner_guard = ensure_realestate_owner_dashboard_only(access_result, property_id)
    if not isinstance(owner_guard, sqlite3.Row):
        return owner_guard
    is_owner_view_only = realestate_owner_read_only(access_result)
    owner_property_ids = get_owner_accessible_property_set(access_result)
    conn = get_db()
    sync_property_contracts_and_units(conn, property_id)
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()

    if not prop:
        conn.close()
        return "<h2>الملك غير موجود</h2>"

    units = conn.execute(
        "SELECT * FROM property_units WHERE property_id = ? ORDER BY name",
        (property_id,)
    ).fetchall()
    contracts = conn.execute(
        "SELECT * FROM property_rent_contracts WHERE property_id = ? ORDER BY id DESC",
        (property_id,)
    ).fetchall()
    maintenance_items = conn.execute(
        """
        SELECT maintenance_requests.*, property_units.name AS unit_name
        FROM maintenance_requests
        LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
        WHERE maintenance_requests.property_id = ?
        ORDER BY maintenance_requests.id DESC
        """,
        (property_id,)
    ).fetchall()
    manual_expenses = conn.execute(
        """
        SELECT property_expenses.*, property_units.name AS unit_name
        FROM property_expenses
        LEFT JOIN property_units ON property_units.id = property_expenses.unit_id
        WHERE property_expenses.property_id = ?
        ORDER BY property_expenses.id DESC
        """,
        (property_id,)
    ).fetchall()
    conn.close()

    category_labels = property_expense_category_labels()
    feedback_html = render_page_feedback(message, error)
    empty_colspan = "9" if not is_owner_view_only else "8"
    installment_status_labels = {
        "unpaid": "غير مدفوع",
        "paid": "مدفوع",
        "late": "متأخر",
    }
    total_revenue = sum(safe_amount(contract["rent"]) for contract in contracts)

    maintenance_total = 0.0
    operational_total = 0.0
    category_totals = {}
    unit_totals = {}
    unit_breakdown = {}
    monthly_totals = {}

    for unit in units:
        unit_breakdown[unit["id"]] = {
            "unit_id": unit["id"],
            "unit_name": unit["name"],
            "maintenance_total": 0.0,
            "operational_total": 0.0,
            "overall_total": 0.0,
            "exceeded_maintenance": False,
            "exceeded_operational": False,
        }

    maintenance_rows = ""
    for item in maintenance_items:
        amount = safe_amount(item["actual_cost"]) if safe_amount(item["actual_cost"]) > 0 else safe_amount(item["estimated_cost"])
        maintenance_total += amount
        category_totals["maintenance"] = category_totals.get("maintenance", 0.0) + amount
        month_key = (parse_safe_date(item["created_at"]) or parse_safe_date(item["completed_date"]) or parse_safe_date(item["updated_at"]))
        if month_key:
            label = month_key.strftime("%Y-%m")
            monthly_totals[label] = monthly_totals.get(label, 0.0) + amount
        if item["unit_id"]:
            unit_totals[item["unit_id"]] = unit_totals.get(item["unit_id"], 0.0) + amount
            if item["unit_id"] in unit_breakdown:
                unit_breakdown[item["unit_id"]]["maintenance_total"] += amount
                unit_breakdown[item["unit_id"]]["overall_total"] += amount

        maintenance_rows += f"""
        <tr>
            <td>{item['maintenance_type'] or item['title'] or '-'}</td>
            <td>{item['unit_name'] or '-'}</td>
            <td>{safe_amount(item['estimated_cost']):,.0f} ريال</td>
            <td>{safe_amount(item['actual_cost']):,.0f} ريال</td>
            <td>{item['status'] or '-'}</td>
            <td>{item['created_at'] or '-'}</td>
        </tr>
        """

    manual_rows = ""
    for item in manual_expenses:
        amount = safe_amount(item["amount"])
        operational_total += amount
        category_key = item["category"] or "other"
        category_totals[category_key] = category_totals.get(category_key, 0.0) + amount
        month_key = parse_safe_date(item["expense_date"]) or parse_safe_date(item["created_at"])
        if month_key:
            label = month_key.strftime("%Y-%m")
            monthly_totals[label] = monthly_totals.get(label, 0.0) + amount
        if item["unit_id"]:
            unit_totals[item["unit_id"]] = unit_totals.get(item["unit_id"], 0.0) + amount
            if item["unit_id"] in unit_breakdown:
                unit_breakdown[item["unit_id"]]["operational_total"] += amount
                unit_breakdown[item["unit_id"]]["overall_total"] += amount

        actions_cell = ""
        if not is_owner_view_only:
            actions_cell = f"""
            <td>
                <a href="/edit-property-expense/{item['id']}?property_id={property_id}" class="action-btn">تعديل</a>
                <a href="/delete-property-expense/{item['id']}?property_id={property_id}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا المصروف؟')">حذف</a>
            </td>
            """
        manual_rows += f"""
        <tr>
            <td>{category_labels.get(category_key, category_key or '-')}</td>
            <td>{item['unit_name'] or '-'}</td>
            <td>{amount:,.0f} ريال</td>
            <td>{item['expense_date'] or '-'}</td>
            <td>{item['vendor_or_payee'] or '-'}</td>
            <td>{item['notes'] or '-'}</td>
            {actions_cell}
        </tr>
        """

    total_expenses = maintenance_total + operational_total
    expense_ratio = (total_expenses / total_revenue * 100) if total_revenue > 0 else 0.0
    average_monthly_expense = (total_expenses / len(monthly_totals)) if monthly_totals else total_expenses

    highest_unit = max(unit_breakdown.values(), key=lambda item: item["overall_total"], default={"unit_name": "لا يوجد", "overall_total": 0.0})
    highest_category_key = max(category_totals, key=category_totals.get, default="other")
    highest_category_amount = category_totals.get(highest_category_key, 0.0)

    units_with_maintenance = [
        item for item in unit_breakdown.values()
        if item["maintenance_total"] > 0
    ]
    units_with_operational = [
        item for item in unit_breakdown.values()
        if item["operational_total"] > 0
    ]

    if units_with_maintenance:
        average_maintenance_expense = sum(item["maintenance_total"] for item in units_with_maintenance) / len(units_with_maintenance)
    else:
        average_maintenance_expense = 0.0

    if units_with_operational:
        average_operational_expense = sum(item["operational_total"] for item in units_with_operational) / len(units_with_operational)
    else:
        average_operational_expense = 0.0

    median_maintenance_expense = safe_median([item["maintenance_total"] for item in units_with_maintenance])
    median_operational_expense = safe_median([item["operational_total"] for item in units_with_operational])

    maintenance_absolute_limit = 250.0
    maintenance_relative_limit = average_maintenance_expense * 1.5 if average_maintenance_expense > 0 else maintenance_absolute_limit

    operational_absolute_limit = 1000.0
    operational_relative_limit = average_operational_expense * 1.5 if average_operational_expense > 0 else operational_absolute_limit

    flagged_maintenance_units = [
        item for item in unit_breakdown.values()
        if item["maintenance_total"] > maintenance_absolute_limit
        or item["maintenance_total"] > maintenance_relative_limit
    ]
    flagged_operational_units = [
        item for item in unit_breakdown.values()
        if item["operational_total"] > operational_absolute_limit
        or item["operational_total"] > operational_relative_limit
    ]

    for item in flagged_maintenance_units:
        item["exceeded_maintenance"] = True
    for item in flagged_operational_units:
        item["exceeded_operational"] = True

    warning_blocks = ""
    if flagged_maintenance_units:
        warning_rows = ""
        for unit in flagged_maintenance_units:
            maintenance_triggers = []
            if unit["maintenance_total"] > maintenance_absolute_limit:
                maintenance_triggers.append("تجاوز الحد الإداري الثابت")
            if unit["maintenance_total"] > maintenance_relative_limit:
                maintenance_triggers.append("تجاوز الحد النسبي حسب متوسط الوحدات النشطة")
            warning_rows += f"""
            <tr>
                <td>{unit['unit_name']}</td>
                <td>{unit['maintenance_total']:,.0f} ريال</td>
                <td>{unit['operational_total']:,.0f} ريال</td>
                <td>{maintenance_absolute_limit:,.0f} ريال</td>
                <td>{maintenance_relative_limit:,.0f} ريال</td>
                <td>{' + '.join(maintenance_triggers)}</td>
            </tr>
            """
        warning_blocks += f"""
        <div class="inventory-warning-panel inventory-table-panel finance-warning-soft">
            <div class="inventory-warning-head">
                <div>
                    <div class="inventory-warning-badge">تنبيه الصيانة للوحدات</div>
                    <h3>عدد الوحدات التي تجاوزت المعدل الطبيعي للصيانة: {len(flagged_maintenance_units)}</h3>
                    <p>تم تقييم كل وحدة بشكل مستقل مقابل حد إداري ثابت وحد نسبي مبني على متوسط الصيانة للوحدات النشطة.</p>
                </div>
            </div>
            <div class="inventory-warning-table-wrap">
                <table class="inventory-warning-table">
                    <tr>
                        <th>الوحدة</th>
                        <th>مصروفات الصيانة</th>
                        <th>مصروفات التشغيل</th>
                        <th>الحد الإداري الثابت</th>
                        <th>الحد النسبي</th>
                        <th>سبب التحذير</th>
                    </tr>
                    {warning_rows}
                </table>
            </div>
        </div>
        """

    if flagged_operational_units:
        warning_rows = ""
        for unit in flagged_operational_units:
            operational_triggers = []
            if unit["operational_total"] > operational_absolute_limit:
                operational_triggers.append("تجاوز الحد الإداري الثابت")
            if unit["operational_total"] > operational_relative_limit:
                operational_triggers.append("تجاوز الحد النسبي حسب متوسط الوحدات النشطة")
            warning_rows += f"""
            <tr>
                <td>{unit['unit_name']}</td>
                <td>{unit['maintenance_total']:,.0f} ريال</td>
                <td>{unit['operational_total']:,.0f} ريال</td>
                <td>{operational_absolute_limit:,.0f} ريال</td>
                <td>{operational_relative_limit:,.0f} ريال</td>
                <td>{' + '.join(operational_triggers)}</td>
            </tr>
            """
        warning_blocks += f"""
        <div class="inventory-warning-panel inventory-table-panel">
            <div class="inventory-warning-head">
                <div>
                    <div class="inventory-warning-badge">تنبيه التشغيل للوحدات</div>
                    <h3>عدد الوحدات التي تجاوزت المعدل الطبيعي للتشغيل: {len(flagged_operational_units)}</h3>
                    <p>تم تقييم كل وحدة بشكل مستقل مقابل حد إداري ثابت وحد نسبي مبني على متوسط التشغيل للوحدات النشطة.</p>
                </div>
            </div>
            <div class="inventory-warning-table-wrap">
                <table class="inventory-warning-table">
                    <tr>
                        <th>الوحدة</th>
                        <th>مصروفات الصيانة</th>
                        <th>مصروفات التشغيل</th>
                        <th>الحد الإداري الثابت</th>
                        <th>الحد النسبي</th>
                        <th>سبب التحذير</th>
                    </tr>
                    {warning_rows}
                </table>
            </div>
        </div>
        """

    if maintenance_total > operational_total and maintenance_total > 0:
        warning_blocks += f"""
        <div class="inventory-warning-panel inventory-table-panel">
            <div class="inventory-warning-head">
                <div>
                    <div class="inventory-warning-badge">تنبيه الصيانة</div>
                    <h3>مصروفات الصيانة تهيمن على هيكل الإنفاق</h3>
                    <p>بلغت مصروفات الصيانة {maintenance_total:,.0f} ريال مقابل مصروفات تشغيلية {operational_total:,.0f} ريال.</p>
                </div>
            </div>
        </div>
        """

    category_chart_items = [
        {"label": category_labels.get(key, key), "amount": round(value, 2)}
        for key, value in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    ]
    unit_chart_items = [
        {"label": item["unit_name"], "amount": round(item["overall_total"], 2)}
        for item in sorted(unit_breakdown.values(), key=lambda unit: unit["overall_total"], reverse=True)
        if item["overall_total"] > 0
    ]
    monthly_chart_items = [
        {"label": label, "amount": round(amount, 2)}
        for label, amount in sorted(monthly_totals.items())
    ]

    category_chart_config = json.dumps({
        "labels": [item["label"] for item in category_chart_items],
        "values": [item["amount"] for item in category_chart_items],
    }, ensure_ascii=False)
    unit_chart_config = json.dumps({
        "labels": [item["label"] for item in unit_chart_items],
        "values": [item["amount"] for item in unit_chart_items],
    }, ensure_ascii=False)
    monthly_chart_config = json.dumps({
        "labels": [item["label"] for item in monthly_chart_items],
        "values": [item["amount"] for item in monthly_chart_items],
    }, ensure_ascii=False)
    comparison_chart_config = json.dumps({
        "labels": ["الصيانة", "التشغيل"],
        "values": [round(maintenance_total, 2), round(operational_total, 2)],
    }, ensure_ascii=False)

    unit_options = '<option value="">بدون وحدة محددة</option>'
    for unit in units:
        unit_options += f'<option value="{unit["id"]}">{unit["name"]}</option>'

    category_options = ""
    for key in ["salary", "electricity", "water", "cleaning", "security", "event_preparation", "marketing", "government_fees", "furniture", "hospitality", "emergency", "other"]:
        category_options += f'<option value="{key}">{category_labels[key]}</option>'

    add_expense_section = ""
    if not is_owner_view_only:
        add_expense_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>إضافة مصروف تشغيلي</h3>
        <form action="/save-property-expense" method="post">
            <input type="hidden" name="property_id" value="{property_id}">

            <label>نوع المصروف</label>
            <select name="expense_type">
                <option value="operational">تشغيلي</option>
            </select>

            <label>التصنيف</label>
            <select name="category" required>
                {category_options}
            </select>

            <label>الوحدة</label>
            <select name="unit_id">{unit_options}</select>

            <label>المبلغ</label>
            <input type="number" step="0.01" name="amount" required>

            <label>تاريخ المصروف</label>
            <input type="date" name="expense_date" value="{date.today().isoformat()}">

            <label>المستفيد / المورد</label>
            <input type="text" name="vendor_or_payee">

            <label>ملاحظات</label>
            <textarea name="notes" rows="3"></textarea>

            <button type="submit" class="glass-btn gold-text">حفظ المصروف</button>
        </form>
    </div>
        """

    manual_actions_header = "<th>الإدارة</th>" if not is_owner_view_only else ""
    manual_empty_colspan = "7" if not is_owner_view_only else "6"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <div class="finance-page-header">
        <div>
            <h1>لوحة المصروفات</h1>
            <p>{prop['name']} | {prop['location'] or 'بدون موقع محدد'} | النوع: {prop['property_type'] or '-'}</p>
        </div>
        <a href="/property-management/{property_id}" class="glass-btn back-btn">⬅ رجوع</a>
    </div>

    {feedback_html}
    {warning_blocks}

    <div class="finance-summary-grid">
        <div class="finance-card finance-card-expense">
            <span>إجمالي مصروفات الصيانة</span>
            <strong>{maintenance_total:,.0f} ريال</strong>
        </div>
        <div class="finance-card">
            <span>إجمالي المصروفات التشغيلية</span>
            <strong>{operational_total:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-negative">
            <span>إجمالي المصروفات الكلي</span>
            <strong>{total_expenses:,.0f} ريال</strong>
        </div>
        <div class="finance-card">
            <span>نسبة المصروف إلى الإيراد</span>
            <strong>{expense_ratio:,.1f}%</strong>
        </div>
    </div>

    <div class="finance-summary-grid finance-summary-grid-secondary">
        <div class="finance-card finance-card-secondary">
            <span>أعلى وحدة صرفًا</span>
            <strong>{highest_unit['unit_name']}</strong>
            <span>{highest_unit['overall_total']:,.0f} ريال</span>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>أعلى بند صرف</span>
            <strong>{category_labels.get(highest_category_key, highest_category_key)}</strong>
            <span>{highest_category_amount:,.0f} ريال</span>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>متوسط الصيانة للوحدة النشطة</span>
            <strong>{average_maintenance_expense:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>متوسط التشغيل للوحدة النشطة</span>
            <strong>{average_operational_expense:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>وسيط الصيانة للوحدة النشطة</span>
            <strong>{median_maintenance_expense:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>وسيط التشغيل للوحدة النشطة</span>
            <strong>{median_operational_expense:,.0f} ريال</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>الوحدات المتجاوزة لحد الصيانة</span>
            <strong>{len(flagged_maintenance_units)}</strong>
        </div>
        <div class="finance-card finance-card-secondary">
            <span>الوحدات المتجاوزة لحد التشغيل</span>
            <strong>{len(flagged_operational_units)}</strong>
        </div>
    </div>

    {add_expense_section}

    <div class="finance-chart-grid">
        <div class="inventory-panel finance-chart-card">
            <h3>توزيع المصروفات حسب النوع</h3>
            <p class="finance-chart-note">توزيع فئات الصيانة والتشغيل داخل العقار الحالي.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="expenseCategoryChart"></canvas>
                </div>
            </div>
            {'<div class="inventory-note">لا توجد بيانات كافية للرسم</div>' if not category_chart_items else ''}
        </div>

        <div class="inventory-panel finance-chart-card">
            <h3>المصاريف حسب الوحدة</h3>
            <p class="finance-chart-note">كل عمود يمثل إجمالي ما صُرف على الوحدة من صيانة وتشغيل.</p>
            <div class="finance-line-stage">
                <canvas id="unitExpenseChart"></canvas>
            </div>
            {'<div class="inventory-note">لا توجد وحدات مرتبطة بمصروفات بعد</div>' if not unit_chart_items else ''}
        </div>
    </div>

    <div class="finance-chart-grid">
        <div class="inventory-panel finance-chart-card">
            <h3>مقارنة المصروفات الشهرية</h3>
            <p class="finance-chart-note">اتجاه الإنفاق عبر الأشهر حسب تاريخ التسجيل.</p>
            <div class="finance-line-stage">
                <canvas id="monthlyExpenseChart"></canvas>
            </div>
            {'<div class="inventory-note">لا توجد بيانات شهرية كافية</div>' if not monthly_chart_items else ''}
        </div>

        <div class="inventory-panel finance-chart-card">
            <h3>الصيانة مقابل التشغيل</h3>
            <p class="finance-chart-note">مقارنة مباشرة بين الإنفاق الفني والتشغيلي.</p>
            <div class="finance-pie-shell">
                <div class="finance-pie-stage">
                    <canvas id="expenseComparisonChart"></canvas>
                </div>
            </div>
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>ذكاء المصروفات لكل وحدة</h3>
        <div class="inventory-warning-table-wrap">
            <table class="finance-table">
                <tr>
                    <th>الوحدة</th>
                    <th>مصروفات الصيانة</th>
                    <th>المصروفات التشغيلية</th>
                    <th>الإجمالي</th>
                    <th>الحالة</th>
                </tr>
                {''.join(
                    f"<tr><td>{item['unit_name']}</td><td>{item['maintenance_total']:,.0f} ريال</td><td>{item['operational_total']:,.0f} ريال</td><td>{item['overall_total']:,.0f} ريال</td><td>{'تجاوز الصيانة والتشغيل' if item['exceeded_maintenance'] and item['exceeded_operational'] else 'تجاوز الصيانة' if item['exceeded_maintenance'] else 'تجاوز التشغيل' if item['exceeded_operational'] else 'ضمن الطبيعي'}</td></tr>"
                    for item in unit_breakdown.values()
                ) if unit_breakdown else "<tr><td colspan='5'>لا توجد وحدات</td></tr>"}
            </table>
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>تفاصيل مصروفات الصيانة</h3>
        <div class="inventory-warning-table-wrap">
            <table class="finance-table">
                <tr>
                    <th>نوع الصيانة</th>
                    <th>الوحدة</th>
                    <th>التكلفة التقديرية</th>
                    <th>التكلفة الفعلية</th>
                    <th>الحالة</th>
                    <th>التاريخ</th>
                </tr>
                {maintenance_rows if maintenance_rows else "<tr><td colspan='6'>لا توجد مصروفات صيانة</td></tr>"}
            </table>
        </div>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>تفاصيل المصروفات التشغيلية</h3>
        <div class="inventory-warning-table-wrap">
            <table class="finance-table">
                <tr>
                    <th>التصنيف</th>
                    <th>الوحدة</th>
                    <th>المبلغ</th>
                    <th>التاريخ</th>
                    <th>المستفيد/المورد</th>
                    <th>الملاحظات</th>
                    {manual_actions_header}
                </tr>
                {manual_rows if manual_rows else f"<tr><td colspan='{manual_empty_colspan}'>لا توجد مصروفات تشغيلية مسجلة</td></tr>"}
            </table>
        </div>
    </div>

    <a href="/property-management/{property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
<script>
const expenseCategoryChartConfig = {category_chart_config};
const unitExpenseChartConfig = {unit_chart_config};
const monthlyExpenseChartConfig = {monthly_chart_config};
const expenseComparisonChartConfig = {comparison_chart_config};

function buildExpenseGradient(ctx, chartArea, startColor, endColor) {{
    const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
    gradient.addColorStop(0, endColor);
    gradient.addColorStop(1, startColor);
    return gradient;
}}

function createExpenseDoughnutChart(canvasId, config) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;

    const baseColors = [
        ['rgba(245, 208, 122, 0.96)', 'rgba(180, 118, 38, 0.96)'],
        ['rgba(248, 113, 113, 0.94)', 'rgba(153, 27, 27, 0.94)'],
        ['rgba(125, 211, 252, 0.94)', 'rgba(8, 89, 144, 0.94)'],
        ['rgba(134, 239, 172, 0.94)', 'rgba(21, 128, 61, 0.94)'],
        ['rgba(196, 181, 253, 0.94)', 'rgba(109, 40, 217, 0.94)'],
        ['rgba(251, 191, 36, 0.94)', 'rgba(161, 98, 7, 0.94)']
    ];

    new Chart(canvas, {{
        type: 'doughnut',
        data: {{
            labels: config.labels,
            datasets: [{{
                data: config.values,
                backgroundColor(context) {{
                    const area = context.chart.chartArea;
                    const palette = baseColors[context.dataIndex % baseColors.length];
                    if (!area) return palette[0];
                    return buildExpenseGradient(context.chart.ctx, area, palette[0], palette[1]);
                }},
                borderColor: 'rgba(255,255,255,0.18)',
                borderWidth: 2,
                hoverOffset: 12
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            cutout: '56%',
            plugins: {{
                legend: {{
                    position: 'bottom',
                    rtl: true,
                    labels: {{
                        color: '#f8fafc',
                        usePointStyle: true,
                        padding: 18
                    }}
                }},
                tooltip: {{
                    rtl: true,
                    callbacks: {{
                        label(context) {{
                            const total = context.dataset.data.reduce((sum, value) => sum + value, 0);
                            const current = context.raw || 0;
                            const percent = total ? ((current / total) * 100).toFixed(1) : '0.0';
                            return `${{context.label}}: ${{Number(current).toLocaleString('en-US')}} ريال | ${{percent}}%`;
                        }}
                    }}
                }}
            }}
        }}
    }});
}}

function createExpenseBarChart(canvasId, config) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;

    new Chart(canvas, {{
        type: 'bar',
        data: {{
            labels: config.labels,
            datasets: [{{
                label: 'المصاريف',
                data: config.values,
                borderRadius: 12,
                backgroundColor(context) {{
                    const area = context.chart.chartArea;
                    if (!area) return 'rgba(245, 208, 122, 0.92)';
                    return buildExpenseGradient(context.chart.ctx, area, 'rgba(245, 208, 122, 0.96)', 'rgba(146, 92, 22, 0.96)');
                }},
                borderColor: 'rgba(255,255,255,0.12)',
                borderWidth: 1
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
                x: {{
                    ticks: {{ color: '#f8fafc' }},
                    grid: {{ display: false }}
                }},
                y: {{
                    ticks: {{ color: '#f8fafc' }},
                    grid: {{ color: 'rgba(255,255,255,0.12)' }}
                }}
            }},
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{
                    rtl: true,
                    callbacks: {{
                        label(context) {{
                            return `${{Number(context.raw).toLocaleString('en-US')}} ريال`;
                        }}
                    }}
                }}
            }}
        }}
    }});
}}

function createExpenseLineChart(canvasId, config) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || !config.values || !config.values.length) return;

    new Chart(canvas, {{
        type: 'line',
        data: {{
            labels: config.labels,
            datasets: [{{
                label: 'المصروف الشهري',
                data: config.values,
                fill: true,
                tension: 0.35,
                pointRadius: 4,
                pointHoverRadius: 6,
                borderColor: 'rgba(125, 211, 252, 0.95)',
                pointBackgroundColor: 'rgba(245, 208, 122, 0.98)',
                backgroundColor(context) {{
                    const area = context.chart.chartArea;
                    if (!area) return 'rgba(125, 211, 252, 0.2)';
                    return buildExpenseGradient(context.chart.ctx, area, 'rgba(125, 211, 252, 0.34)', 'rgba(15, 23, 42, 0.04)');
                }}
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
                x: {{
                    ticks: {{ color: '#f8fafc' }},
                    grid: {{ display: false }}
                }},
                y: {{
                    ticks: {{ color: '#f8fafc' }},
                    grid: {{ color: 'rgba(255,255,255,0.12)' }}
                }}
            }},
            plugins: {{
                legend: {{
                    labels: {{ color: '#f8fafc' }}
                }},
                tooltip: {{
                    rtl: true
                }}
            }}
        }}
    }});
}}

createExpenseDoughnutChart('expenseCategoryChart', expenseCategoryChartConfig);
createExpenseBarChart('unitExpenseChart', unitExpenseChartConfig);
createExpenseLineChart('monthlyExpenseChart', monthlyExpenseChartConfig);
createExpenseDoughnutChart('expenseComparisonChart', expenseComparisonChartConfig);
</script>
"""


@app.post("/save-property-expense")
def save_property_expense(
    request: Request,
    property_id: int = Form(...),
    expense_type: str = Form("operational"),
    category: str = Form("other"),
    unit_id: int = Form(0),
    amount: float = Form(...),
    expense_date: str = Form(""),
    vendor_or_payee: str = Form(""),
    notes: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-expenses/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        """
        INSERT INTO property_expenses (
            property_id, unit_id, maintenance_request_id, expense_type, category, amount,
            expense_date, vendor_or_payee, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            unit_id if unit_id else None,
            None,
            expense_type,
            category,
            amount,
            expense_date or date.today().isoformat(),
            vendor_or_payee,
            notes,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-expenses/{property_id}", message="تم حفظ المصروف التشغيلي بنجاح"),
        status_code=303
    )


@app.get("/edit-property-expense/{expense_id}", response_class=HTMLResponse)
def edit_property_expense_form(request: Request, expense_id: int, property_id: int = 0):
    conn = get_db()
    expense = conn.execute("SELECT * FROM property_expenses WHERE id = ?", (expense_id,)).fetchone()
    if not expense:
        conn.close()
        return "<h2>المصروف غير موجود</h2>"

    current_property_id = property_id or expense["property_id"] or 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-expenses/{current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    prop = conn.execute("SELECT * FROM property_properties WHERE id = ?", (current_property_id,)).fetchone()
    units = conn.execute("SELECT * FROM property_units WHERE property_id = ? ORDER BY name", (current_property_id,)).fetchall()
    conn.close()

    category_labels = property_expense_category_labels()
    unit_options = '<option value="">بدون وحدة محددة</option>'
    for unit in units:
        selected = "selected" if expense["unit_id"] == unit["id"] else ""
        unit_options += f'<option value="{unit["id"]}" {selected}>{unit["name"]}</option>'

    category_options = ""
    for key in ["salary", "electricity", "water", "cleaning", "security", "event_preparation", "marketing", "government_fees", "furniture", "hospitality", "emergency", "other"]:
        selected = "selected" if expense["category"] == key else ""
        category_options += f'<option value="{key}" {selected}>{category_labels[key]}</option>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تعديل مصروف تشغيلي</h1>
    <p>{prop['name']} | {prop['location'] or 'بدون موقع محدد'} | النوع: {prop['property_type'] or '-'}</p>

    <div class="inventory-panel inventory-table-panel">
        <form action="/update-property-expense" method="post">
            <input type="hidden" name="expense_id" value="{expense_id}">
            <input type="hidden" name="property_id" value="{current_property_id}">

            <label>نوع المصروف</label>
            <select name="expense_type">
                <option value="operational" {"selected" if expense['expense_type'] == "operational" else ""}>تشغيلي</option>
            </select>

            <label>التصنيف</label>
            <select name="category" required>{category_options}</select>

            <label>الوحدة</label>
            <select name="unit_id">{unit_options}</select>

            <label>المبلغ</label>
            <input type="number" step="0.01" name="amount" value="{expense['amount'] or 0}" required>

            <label>تاريخ المصروف</label>
            <input type="date" name="expense_date" value="{expense['expense_date'] or ''}">

            <label>المستفيد / المورد</label>
            <input type="text" name="vendor_or_payee" value="{expense['vendor_or_payee'] or ''}">

            <label>ملاحظات</label>
            <textarea name="notes" rows="3">{expense['notes'] or ''}</textarea>

            <button type="submit" class="glass-btn gold-text">حفظ التعديلات</button>
        </form>
    </div>

    <a href="/property-expenses/{current_property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-property-expense")
def update_property_expense(
    request: Request,
    expense_id: int = Form(...),
    property_id: int = Form(...),
    expense_type: str = Form("operational"),
    category: str = Form("other"),
    unit_id: int = Form(0),
    amount: float = Form(...),
    expense_date: str = Form(""),
    vendor_or_payee: str = Form(""),
    notes: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-expenses/{property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        """
        UPDATE property_expenses
        SET expense_type = ?, category = ?, unit_id = ?, amount = ?, expense_date = ?, vendor_or_payee = ?, notes = ?
        WHERE id = ?
        """,
        (
            expense_type,
            category,
            unit_id if unit_id else None,
            amount,
            expense_date or date.today().isoformat(),
            vendor_or_payee,
            notes,
            expense_id
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-expenses/{property_id}", message="تم تحديث المصروف بنجاح"),
        status_code=303
    )


@app.get("/delete-property-expense/{expense_id}")
def delete_property_expense(request: Request, expense_id: int, property_id: int = 0):
    conn = get_db()
    expense = conn.execute("SELECT * FROM property_expenses WHERE id = ?", (expense_id,)).fetchone()
    current_property_id = property_id or (expense["property_id"] if expense else 0)

    if not expense:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-expenses/{current_property_id}", error="المصروف غير موجود"),
            status_code=303
        )

    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-expenses/{current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result

    conn.execute("DELETE FROM property_expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-expenses/{current_property_id}", message="تم حذف المصروف بنجاح"),
        status_code=303
    )


@app.get("/property-details/{property_id}", response_class=HTMLResponse)
def property_details(request: Request, property_id: int):
    access_result = ensure_realestate_property_management_access(request, property_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    return RedirectResponse(url=f"/property-management/{property_id}", status_code=303)


@app.get("/property-units", response_class=HTMLResponse)
def property_units_page(request: Request, property_id: int = 0, message: str = "", error: str = ""):
    if property_id:
        access_result = ensure_realestate_property_management_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_property_management_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
        access_result = company_result
    owner_guard = ensure_realestate_owner_dashboard_only(access_result, property_id)
    if not isinstance(owner_guard, sqlite3.Row):
        return owner_guard
    conn = get_db()
    sync_property_contracts_and_units(conn, property_id)
    current_property = None
    properties = conn.execute("SELECT * FROM property_properties ORDER BY name").fetchall()
    is_owner_view_only = realestate_owner_read_only(access_result)
    owner_property_ids = get_owner_accessible_property_set(access_result)
    if is_owner_view_only:
        properties = filter_rows_by_property_scope(properties, owner_property_ids, "id")
    if property_id:
        current_property = conn.execute(
            "SELECT * FROM property_properties WHERE id = ?",
            (property_id,)
        ).fetchone()
    if property_id:
        units = conn.execute(
            """
            SELECT property_units.*, property_properties.name AS property_name
            FROM property_units
            LEFT JOIN property_properties ON property_properties.id = property_units.property_id
            WHERE property_units.property_id = ?
            ORDER BY property_units.id DESC
            """,
            (property_id,)
        ).fetchall()
    else:
        units = conn.execute(
            """
            SELECT property_units.*, property_properties.name AS property_name
            FROM property_units
            LEFT JOIN property_properties ON property_properties.id = property_units.property_id
            ORDER BY property_units.id DESC
            """
        ).fetchall()
    if is_owner_view_only:
        units = filter_rows_by_property_scope(units, owner_property_ids)
    unit_contracts = conn.execute(
        """
        SELECT unit_id, end_date, status
        FROM property_rent_contracts
        WHERE unit_id IS NOT NULL
        ORDER BY id DESC
        """
    ).fetchall()
    unit_contract_status_map: dict[int, str] = {}
    for contract in unit_contracts:
        if not contract["unit_id"] or contract["unit_id"] in unit_contract_status_map:
            continue
        contract_status = compute_contract_status(contract["end_date"], contract["status"] or "")
        if contract_status in {"ساري", "قريب ينتهي"}:
            unit_contract_status_map[contract["unit_id"]] = "مؤجرة"
        else:
            unit_contract_status_map[contract["unit_id"]] = "شاغرة"
    conn.close()

    property_selector = ""
    if property_id and current_property:
        property_selector = f"""
        <div class="inventory-note">الملك الحالي: <strong>{current_property['name']}</strong></div>
        <input type="hidden" name="property_id" value="{property_id}">
        """
    else:
        property_options = '<option value="">اختر الملك</option>'
        for prop in properties:
            selected = "selected" if property_id and prop["id"] == property_id else ""
            property_options += f'<option value="{prop["id"]}" {selected}>{prop["name"]}</option>'
        property_selector = f"""
        <label>الملك</label>
        <select name="property_id" required>{property_options}</select>
        """

    feedback_html = render_page_feedback(message, error)

    rows = ""
    for unit in units:
        display_status = unit_contract_status_map.get(unit["id"], unit["status"] or "-")
        actions_cell = ""
        if not is_owner_view_only:
            actions_cell = f"""
            <td>
                <a href="/edit-property-unit/{unit['id']}?property_id={unit['property_id'] or property_id}" class="action-btn">تعديل</a>
                <a href="/delete-property-unit/{unit['id']}?property_id={unit['property_id'] or property_id}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذه الوحدة؟')">حذف</a>
            </td>
            """
        rows += f"""
        <tr>
            <td>{unit['property_name'] or '-'}</td>
            <td>{unit['name']}</td>
            <td>{unit['type'] or '-'}</td>
            <td>{unit['rent'] or 0}</td>
            <td>{display_status}</td>
            {actions_cell}
        </tr>
        """

    add_unit_section = ""
    if not is_owner_view_only:
        add_unit_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>إضافة وحدة</h3>
        <form action="/save-property-unit" method="post">
            {property_selector}

            <label>اسم الوحدة</label>
            <input type="text" name="name" required>

            <label>النوع</label>
            <select name="type">
                <option value="">اختر نوع الوحدة</option>
                <option value="سكني">سكني</option>
                <option value="تجاري">تجاري</option>
                <option value="مكتبي">مكتبي</option>
                <option value="فندقي">فندقي</option>
            </select>

            <button type="submit" class="glass-btn gold-text">حفظ الوحدة</button>
        </form>
    </div>
        """

    actions_header = "<th>الإدارة</th>" if not is_owner_view_only else ""
    empty_colspan = "6" if not is_owner_view_only else "5"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>الوحدات</h1>
    <p>{f"إدارة وحدات الملك: {current_property['name']}" if current_property else "إدارة جميع وحدات الأملاك"}</p>
    {feedback_html}

    {add_unit_section}

    <div class="inventory-panel inventory-table-panel">
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الملك</th>
                <th>الوحدة</th>
                <th>النوع</th>
                <th>الإيجار</th>
                <th>الحالة</th>
                {actions_header}
            </tr>
            {rows if rows else f"<tr><td colspan='{empty_colspan}'>لا توجد وحدات</td></tr>"}
        </table>
    </div>

    <a href="{f'/property-management/{property_id}' if current_property else '/property-management'}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-property-unit")
def save_property_unit(
    request: Request,
    property_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(""),
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-units?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        "INSERT INTO property_units (property_id, name, type, rent, status) VALUES (?, ?, ?, ?, ?)",
        (property_id, name, type, 0, "شاغرة")
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-units?property_id={property_id}", message="تم حفظ الوحدة بنجاح"),
        status_code=303
    )


@app.get("/edit-property-unit/{unit_id}", response_class=HTMLResponse)
def edit_property_unit_form(request: Request, unit_id: int, property_id: int = 0):
    conn = get_db()
    unit = conn.execute("SELECT * FROM property_units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return "<h2>الوحدة غير موجودة</h2>"

    current_property_id = property_id or unit["property_id"] or 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-units?property_id={current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    current_property = conn.execute(
        "SELECT * FROM property_properties WHERE id = ?",
        (current_property_id,)
    ).fetchone() if current_property_id else None
    conn.close()
    unit_type_options = ""
    current_unit_type = unit["type"] or ""
    if current_unit_type and current_unit_type not in {"سكني", "تجاري", "مكتبي", "فندقي"}:
        unit_type_options += f'<option value="{current_unit_type}" selected>{current_unit_type}</option>'
    unit_type_options += f'''
                <option value="">اختر نوع الوحدة</option>
                <option value="سكني" {"selected" if current_unit_type == "سكني" else ""}>سكني</option>
                <option value="تجاري" {"selected" if current_unit_type == "تجاري" else ""}>تجاري</option>
                <option value="مكتبي" {"selected" if current_unit_type == "مكتبي" else ""}>مكتبي</option>
                <option value="فندقي" {"selected" if current_unit_type == "فندقي" else ""}>فندقي</option>
    '''

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script>
function toggleNewTenantFields(mode) {{
    const select = document.getElementById(`contract-tenant-select-${{mode}}`);
    const fields = document.getElementById(`contract-new-tenant-fields-${{mode}}`);
    if (!select || !fields) return;
    const isNewTenant = select.value === "__new__";
    fields.style.display = isNewTenant ? "block" : "none";
    fields.querySelectorAll("input, select").forEach((field) => {{
        if (field.name === "new_tenant_name") {{
            field.required = isNewTenant;
        }}
    }});
}}
</script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تعديل الوحدة</h1>
    <p>{f"تحديث بيانات وحدة في ملك: {current_property['name']}" if current_property else "تحديث بيانات الوحدة"}</p>

    <div class="inventory-panel inventory-table-panel">
        <form action="/update-property-unit" method="post">
            <input type="hidden" name="unit_id" value="{unit_id}">
            <input type="hidden" name="property_id" value="{current_property_id}">

            <label>اسم الوحدة</label>
            <input type="text" name="name" value="{unit['name'] or ''}" required>

            <label>النوع</label>
            <select name="type">
                {unit_type_options}
            </select>

            <button type="submit" class="glass-btn gold-text">حفظ التعديلات</button>
        </form>
    </div>

    <a href="/property-units?property_id={current_property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-property-unit")
def update_property_unit(
    request: Request,
    unit_id: int = Form(...),
    property_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(""),
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-units?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        "UPDATE property_units SET name = ?, type = ? WHERE id = ?",
        (name, type, unit_id)
    )
    refresh_unit_status_from_contracts(conn, unit_id)
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-units?property_id={property_id}", message="تم تحديث الوحدة بنجاح"),
        status_code=303
    )


@app.get("/delete-property-unit/{unit_id}")
def delete_property_unit(request: Request, unit_id: int, property_id: int = 0):
    conn = get_db()
    unit = conn.execute("SELECT * FROM property_units WHERE id = ?", (unit_id,)).fetchone()
    current_property_id = property_id or (unit["property_id"] if unit else 0)

    if not unit:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-units?property_id={current_property_id}", error="الوحدة غير موجودة"),
            status_code=303
        )

    linked_tenants = conn.execute(
        "SELECT COUNT(*) AS total FROM property_tenants WHERE unit_id = ?",
        (unit_id,)
    ).fetchone()["total"]
    linked_contracts = conn.execute(
        "SELECT COUNT(*) AS total FROM property_rent_contracts WHERE unit_id = ?",
        (unit_id,)
    ).fetchone()["total"]
    linked_maintenance = conn.execute(
        "SELECT COUNT(*) AS total FROM maintenance_requests WHERE unit_id = ?",
        (unit_id,)
    ).fetchone()["total"]

    if linked_tenants or linked_contracts or linked_maintenance:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/property-units?property_id={current_property_id}",
                error="لا يمكن حذف الوحدة لوجود مستأجرين أو عقود أو سجلات صيانة مرتبطة بها"
            ),
            status_code=303
        )

    conn.execute("DELETE FROM property_units WHERE id = ?", (unit_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-units?property_id={current_property_id}", message="تم حذف الوحدة بنجاح"),
        status_code=303
    )


@app.get("/property-tenants", response_class=HTMLResponse)
def property_tenants_page(request: Request, property_id: int = 0, message: str = "", error: str = ""):
    if property_id:
        access_result = ensure_realestate_property_management_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_property_management_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
        access_result = company_result
    owner_guard = ensure_realestate_owner_dashboard_only(access_result, property_id)
    if not isinstance(owner_guard, sqlite3.Row):
        return owner_guard
    return RedirectResponse(
        url=build_redirect_url(
            f"/property-rental-contracts?property_id={property_id}" if property_id else "/property-rental-contracts",
            message="تم نقل إدارة المستأجرين إلى صفحة العقود"
        ),
        status_code=303,
    )


@app.post("/save-property-tenant")
def save_property_tenant(
    request: Request,
    property_id: int = Form(...),
    unit_id: int = Form(0),
    name: str = Form(...),
    phone: str = Form(""),
    id_number: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-rental-contracts?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        "INSERT INTO property_tenants (property_id, unit_id, name, phone, id_number) VALUES (?, ?, ?, ?, ?)",
        (property_id, unit_id if unit_id else None, name, phone, id_number)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={property_id}", message="تم حفظ المستأجر بنجاح"),
        status_code=303
    )


@app.get("/edit-property-tenant/{tenant_id}", response_class=HTMLResponse)
def edit_property_tenant_form(request: Request, tenant_id: int, property_id: int = 0):
    conn = get_db()
    tenant = conn.execute("SELECT * FROM property_tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not tenant:
        conn.close()
        return "<h2>المستأجر غير موجود</h2>"

    current_property_id = property_id or tenant["property_id"] or 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-rental-contracts?property_id={current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(
            f"/property-rental-contracts?property_id={current_property_id}",
            message="تم نقل تعديل بيانات المستأجر إلى صفحة العقود"
        ),
        status_code=303,
    )


@app.post("/update-property-tenant")
def update_property_tenant(
    request: Request,
    tenant_id: int = Form(...),
    property_id: int = Form(...),
    unit_id: int = Form(0),
    name: str = Form(...),
    phone: str = Form(""),
    id_number: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-rental-contracts?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        "UPDATE property_tenants SET unit_id = ?, name = ?, phone = ?, id_number = ? WHERE id = ?",
        (unit_id if unit_id else None, name, phone, id_number, tenant_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={property_id}", message="تم تحديث المستأجر بنجاح"),
        status_code=303
    )


@app.get("/delete-property-tenant/{tenant_id}")
def delete_property_tenant(request: Request, tenant_id: int, property_id: int = 0):
    conn = get_db()
    tenant = conn.execute("SELECT * FROM property_tenants WHERE id = ?", (tenant_id,)).fetchone()
    current_property_id = property_id or (tenant["property_id"] if tenant else 0)

    if not tenant:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", error="المستأجر غير موجود"),
            status_code=303
        )

    linked_contracts = conn.execute(
        "SELECT COUNT(*) AS total FROM property_rent_contracts WHERE tenant_id = ?",
        (tenant_id,)
    ).fetchone()["total"]
    linked_maintenance = conn.execute(
        "SELECT COUNT(*) AS total FROM maintenance_requests WHERE tenant_id = ?",
        (tenant_id,)
    ).fetchone()["total"]

    if linked_contracts or linked_maintenance:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/property-rental-contracts?property_id={current_property_id}",
                error="لا يمكن حذف المستأجر لوجود عقود أو طلبات صيانة مرتبطة به"
            ),
            status_code=303
        )

    conn.execute("DELETE FROM property_tenants WHERE id = ?", (tenant_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", message="تم حذف المستأجر بنجاح"),
        status_code=303
    )


@app.get("/property-rental-contracts", response_class=HTMLResponse)
def property_rental_contracts_page(request: Request, property_id: int = 0, message: str = "", error: str = ""):
    installment_status_labels = {
        "unpaid": "غير مدفوعة",
        "paid": "مدفوعة",
        "late": "متأخرة",
    }
    if property_id:
        access_result = ensure_realestate_property_management_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_property_management_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
        access_result = company_result
    owner_guard = ensure_realestate_owner_dashboard_only(access_result, property_id)
    if not isinstance(owner_guard, sqlite3.Row):
        return owner_guard
    conn = get_db()
    sync_property_contracts_and_units(conn, property_id)
    current_property = None
    properties = conn.execute("SELECT * FROM property_properties ORDER BY name").fetchall()
    is_owner_view_only = realestate_owner_read_only(access_result)
    owner_property_ids = get_owner_accessible_property_set(access_result)
    is_admin_user = is_admin(access_result)
    if is_owner_view_only:
        properties = filter_rows_by_property_scope(properties, owner_property_ids, "id")
    if property_id:
        current_property = conn.execute(
            "SELECT * FROM property_properties WHERE id = ?",
            (property_id,)
        ).fetchone()
        units = conn.execute(
            "SELECT * FROM property_units WHERE property_id = ? ORDER BY name",
            (property_id,)
        ).fetchall()
        tenants = conn.execute(
            "SELECT * FROM property_tenants WHERE property_id = ? ORDER BY name",
            (property_id,)
        ).fetchall()
    else:
        units = conn.execute("SELECT * FROM property_units ORDER BY name").fetchall()
        tenants = conn.execute("SELECT * FROM property_tenants ORDER BY name").fetchall()
    if is_owner_view_only:
        units = filter_rows_by_property_scope(units, owner_property_ids)
        tenants = filter_rows_by_property_scope(tenants, owner_property_ids)
    if property_id:
        contracts = conn.execute(
            """
            SELECT property_rent_contracts.*, property_properties.name AS property_name,
                   property_units.name AS unit_name, property_tenants.name AS tenant_name
            FROM property_rent_contracts
            LEFT JOIN property_properties ON property_properties.id = property_rent_contracts.property_id
            LEFT JOIN property_units ON property_units.id = property_rent_contracts.unit_id
            LEFT JOIN property_tenants ON property_tenants.id = property_rent_contracts.tenant_id
            WHERE property_rent_contracts.property_id = ?
            ORDER BY property_rent_contracts.id DESC
            """,
            (property_id,)
        ).fetchall()
    else:
        contracts = conn.execute(
            """
            SELECT property_rent_contracts.*, property_properties.name AS property_name,
                   property_units.name AS unit_name, property_tenants.name AS tenant_name
            FROM property_rent_contracts
            LEFT JOIN property_properties ON property_properties.id = property_rent_contracts.property_id
            LEFT JOIN property_units ON property_units.id = property_rent_contracts.unit_id
            LEFT JOIN property_tenants ON property_tenants.id = property_rent_contracts.tenant_id
            ORDER BY property_rent_contracts.id DESC
            """
        ).fetchall()
    if is_owner_view_only:
        contracts = filter_rows_by_property_scope(contracts, owner_property_ids)
    attachments_map = get_contract_attachment_rows(
        conn,
        CONTRACT_ATTACHMENT_SOURCE_PROPERTY,
        [row["id"] for row in contracts],
    )
    contract_installments_map: dict[int, list[sqlite3.Row]] = {}
    contract_ids = [row["id"] for row in contracts]
    if contract_ids:
        placeholders = ", ".join("?" for _ in contract_ids)
        installment_rows = conn.execute(
            f"""
            SELECT *
            FROM contract_installments
            WHERE contract_id IN ({placeholders})
            ORDER BY due_date ASC, id ASC
            """,
            contract_ids,
        ).fetchall()
        for installment in installment_rows:
            contract_installments_map.setdefault(installment["contract_id"], []).append(installment)
    conn.close()

    property_selector = ""
    if property_id and current_property:
        property_selector = f"""
        <div class="inventory-note">الملك الحالي: <strong>{current_property['name']}</strong></div>
        <input type="hidden" name="property_id" value="{property_id}">
        """
    else:
        property_options = '<option value="">اختر الملك</option>'
        for prop in properties:
            selected = "selected" if property_id and prop["id"] == property_id else ""
            property_options += f'<option value="{prop["id"]}" {selected}>{prop["name"]}</option>'
        property_selector = f"""
        <label>الملك</label>
        <select name="property_id" required>{property_options}</select>
        """

    unit_options = '<option value="">اختر الوحدة</option>'
    for unit in units:
        unit_options += f'<option value="{unit["id"]}">{unit["name"]}</option>'

    tenant_options = '<option value="">اختر المستأجر</option><option value="__new__">إضافة مستأجر جديد</option>'
    for tenant in tenants:
        tenant_options += f'<option value="{tenant["id"]}">{tenant["name"]}</option>'

    feedback_html = render_page_feedback(message, error)

    rows = ""
    for contract in contracts:
        attachments_html = render_contract_attachments_html(
            attachments_map.get(contract["id"], []),
            CONTRACT_ATTACHMENT_SOURCE_PROPERTY,
            contract["id"],
            is_admin_user=is_admin_user,
            property_id=contract["property_id"] or property_id,
        )
        installments = contract_installments_map.get(contract["id"], [])
        installments_rows = ""
        for installment_index, installment in enumerate(installments, start=1):
            installment_status_key = (installment["status"] or "").strip().lower()
            installment_action_html = "-"
            if not is_owner_view_only and installment_status_key != "paid":
                installment_action_html = f"""
                <form action="/mark-contract-installment-paid/{installment['id']}" method="post" style="display:inline;">
                    <input type="hidden" name="property_id" value="{contract['property_id'] or property_id}">
                    <button type="submit" class="action-btn" onclick="return confirm('تأكيد تحصيل هذه الدفعة؟')">تم الدفع</button>
                </form>
                """
            installments_rows += f"""
            <tr>
                <td>{installment_index}</td>
                <td>{safe_amount(installment['amount']):,.2f}</td>
                <td>{installment['due_date'] or '-'}</td>
                <td>{installment_status_labels.get(installment_status_key, installment['status'] or '-')}</td>
                <td>{installment['paid_at'] or '-'}</td>
                <td>{installment_action_html}</td>
            </tr>
            """
        installments_table_html = f"""
        <div style="margin-top:10px;">
            <table border="1" style="background:white;margin:auto;width:100%;">
                <tr>
                    <th>رقم الدفعة</th>
                    <th>المبلغ</th>
                    <th>تاريخ الاستحقاق</th>
                    <th>الحالة</th>
                    <th>تاريخ التحصيل</th>
                    <th>الإجراء</th>
                </tr>
                {installments_rows if installments_rows else "<tr><td colspan='6'>لا توجد دفعات مجدولة لهذا العقد</td></tr>"}
            </table>
        </div>
        """
        installments_button_html = f'<button type="button" class="action-btn" onclick="toggleContractInstallments(\'contract-installments-{contract["id"]}\')">عرض الدفعات</button>'
        contract_status_display = compute_contract_status(contract["end_date"], contract["status"] or "-")
        actions_cell = ""
        if not is_owner_view_only:
            actions_cell = f"""
            <td>
                {installments_button_html}
                <a href="/edit-property-rental-contract/{contract['id']}?property_id={contract['property_id'] or property_id}" class="action-btn">تعديل</a>
                <a href="/delete-property-rental-contract/{contract['id']}?property_id={contract['property_id'] or property_id}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف هذا العقد؟')">حذف</a>
            </td>
            """
        attachments_cell_html = f"""
        <td>
            <div style="display:flex;flex-direction:column;gap:8px;">
                {installments_button_html if is_owner_view_only else ""}
                {attachments_html}
            </div>
        </td>
        """
        rows += f"""
        <tr>
            <td>{contract['property_name'] or '-'}</td>
            <td>{contract['unit_name'] or '-'}</td>
            <td>{contract['tenant_name'] or '-'}</td>
            <td>{contract['rent'] or 0}</td>
            <td>{contract['start_date'] or '-'}</td>
            <td>{contract['end_date'] or '-'}</td>
            <td>{contract_status_display}</td>
            {attachments_cell_html}
            {actions_cell}
        </tr>
        <tr id="contract-installments-{contract['id']}" style="display:none;">
            <td colspan="8" style="padding:14px;background:rgba(255,255,255,0.04);">
                {installments_table_html}
            </td>
        </tr>
        """

    add_contract_section = ""
    if not is_owner_view_only:
        add_contract_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>إضافة عقد إيجار</h3>
        <form action="/save-property-rental-contract" method="post">
            {property_selector}

            <label>الوحدة</label>
            <select name="unit_id" required>{unit_options}</select>

            <label>المستأجر</label>
            <select name="tenant_id" id="contract-tenant-select-new" required onchange="toggleNewTenantFields('new')">{tenant_options}</select>

            <div id="contract-new-tenant-fields-new" style="display:none;">
                <label>اسم المستأجر</label>
                <input type="text" name="new_tenant_name">

                <label>رقم الجوال</label>
                <input type="text" name="new_tenant_phone">

                <label>رقم الهوية</label>
                <input type="text" name="new_tenant_id_number">

                <label>نوع المستأجر</label>
                <select name="new_tenant_type">
                    <option value="">اختر نوع المستأجر</option>
                    <option value="فرد">فرد</option>
                    <option value="شركة">شركة</option>
                </select>
            </div>

            <input type="hidden" name="rent" value="0">

            <label>الإيجار السنوي</label>
            <input type="number" step="0.01" name="annual_rent" required>

            <label>مدة العقد (سنوات)</label>
            <input type="number" name="contract_duration_years" min="1" step="1" required>

            <label>طريقة الدفع</label>
            <select name="payment_frequency" required>
                <option value="yearly">سنوي</option>
                <option value="semi-annual">نصف سنوي</option>
                <option value="quarterly">ربع سنوي</option>
                <option value="monthly">شهري</option>
            </select>

            <label>بداية العقد</label>
            <input type="date" name="start_date">

            <label>نهاية العقد</label>
            <input type="date" name="end_date">

            <button type="submit" class="glass-btn gold-text">حفظ العقد</button>
        </form>
    </div>
        """

    actions_header = "<th>الإدارة</th>" if not is_owner_view_only else ""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script>
function toggleContractInstallments(rowId) {{
    const row = document.getElementById(rowId);
    if (!row) return;
    row.style.display = row.style.display === "none" ? "table-row" : "none";
}}
function toggleNewTenantFields(mode) {{
    const select = document.getElementById(`contract-tenant-select-${{mode}}`);
    const fields = document.getElementById(`contract-new-tenant-fields-${{mode}}`);
    if (!select || !fields) return;
    const isNewTenant = select.value === "__new__";
    fields.style.display = isNewTenant ? "block" : "none";
    fields.querySelectorAll("input, select").forEach((field) => {{
        if (field.name === "new_tenant_name") {{
            field.required = isNewTenant;
        }}
    }});
}}
</script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>عقود الإيجار</h1>
    <p>{f"إدارة عقود الملك: {current_property['name']}" if current_property else "إدارة جميع العقود الإيجارية"}</p>
    {feedback_html}

    {add_contract_section}

    <div class="inventory-panel inventory-table-panel">
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الملك</th>
                <th>الوحدة</th>
                <th>المستأجر</th>
                <th>الإيجار</th>
                <th>البداية</th>
                <th>النهاية</th>
                <th>الحالة</th>
                <th>المرفقات</th>
                {actions_header}
            </tr>
            {rows if rows else "<tr><td colspan='8'>لا توجد عقود إيجار</td></tr>"}
        </table>
    </div>

    <a href="{f'/property-management/{property_id}' if current_property else '/property-management'}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-property-rental-contract")
def save_property_rental_contract(
    request: Request,
    property_id: int = Form(...),
    unit_id: int = Form(...),
    tenant_id: str = Form(""),
    rent: float = Form(0),
    annual_rent: float = Form(0),
    contract_duration_years: int = Form(0),
    payment_frequency: str = Form("yearly"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    status: str = Form("ساري"),
    new_tenant_name: str = Form(""),
    new_tenant_phone: str = Form(""),
    new_tenant_id_number: str = Form(""),
    new_tenant_type: str = Form(""),
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-rental-contracts?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    resolved_tenant_id = resolve_contract_tenant_id(
        conn,
        property_id=property_id,
        unit_id=unit_id,
        tenant_id_value=tenant_id,
        new_tenant_name=new_tenant_name,
        new_tenant_phone=new_tenant_phone,
        new_tenant_id_number=new_tenant_id_number,
        new_tenant_type=new_tenant_type,
    )
    if not resolved_tenant_id:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/property-rental-contracts?property_id={property_id}",
                error="يرجى اختيار مستأجر موجود أو إدخال بيانات مستأجر جديد"
            ),
            status_code=303
        )
    rent = calculate_contract_rent_value(annual_rent, contract_duration_years, payment_frequency)
    status = compute_contract_status(end_date, status)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO property_rent_contracts (property_id, unit_id, tenant_id, rent, start_date, end_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (property_id, unit_id, resolved_tenant_id, rent, start_date, end_date, status)
    )
    contract_id = cursor.lastrowid
    installment_rows = build_contract_installment_rows(
        contract_id=contract_id,
        annual_rent=annual_rent,
        contract_duration_years=contract_duration_years,
        payment_frequency=payment_frequency,
        contract_start_date=start_date,
    )
    if installment_rows:
        conn.executemany(
            """
            INSERT INTO contract_installments (contract_id, amount, due_date, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            installment_rows,
        )
    refresh_unit_status_from_contracts(conn, unit_id)
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={property_id}", message="تم حفظ العقد بنجاح"),
        status_code=303
    )


@app.get("/edit-property-rental-contract/{contract_id}", response_class=HTMLResponse)
def edit_property_rental_contract_form(request: Request, contract_id: int, property_id: int = 0):
    conn = get_db()
    contract = conn.execute("SELECT * FROM property_rent_contracts WHERE id = ?", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        return "<h2>العقد غير موجود</h2>"

    current_property_id = property_id or contract["property_id"] or 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-rental-contracts?property_id={current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    current_property = conn.execute(
        "SELECT * FROM property_properties WHERE id = ?",
        (current_property_id,)
    ).fetchone() if current_property_id else None
    units = conn.execute(
        "SELECT * FROM property_units WHERE property_id = ? ORDER BY name",
        (current_property_id,)
    ).fetchall() if current_property_id else []
    tenants = conn.execute(
        "SELECT * FROM property_tenants WHERE property_id = ? ORDER BY name",
        (current_property_id,)
    ).fetchall() if current_property_id else []
    contract_form_values = derive_contract_form_values(conn, contract_id)
    conn.close()

    unit_options = '<option value="">اختر الوحدة</option>'
    for unit in units:
        selected = "selected" if contract["unit_id"] == unit["id"] else ""
        unit_options += f'<option value="{unit["id"]}" {selected}>{unit["name"]}</option>'

    tenant_options = '<option value="">اختر المستأجر</option><option value="__new__">إضافة مستأجر جديد</option>'
    for tenant in tenants:
        selected = "selected" if contract["tenant_id"] == tenant["id"] else ""
        tenant_options += f'<option value="{tenant["id"]}" {selected}>{tenant["name"]}</option>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<script>
function toggleNewTenantFields(mode) {{
    const select = document.getElementById(`contract-tenant-select-${{mode}}`);
    const fields = document.getElementById(`contract-new-tenant-fields-${{mode}}`);
    if (!select || !fields) return;
    const isNewTenant = select.value === "__new__";
    fields.style.display = isNewTenant ? "block" : "none";
    fields.querySelectorAll("input, select").forEach((field) => {{
        if (field.name === "new_tenant_name") {{
            field.required = isNewTenant;
        }}
    }});
}}
</script>
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تعديل عقد الإيجار</h1>
    <p>{f"تحديث عقد في ملك: {current_property['name']}" if current_property else "تحديث عقد الإيجار"}</p>

    <div class="inventory-panel inventory-table-panel">
        <form action="/update-property-rental-contract" method="post">
            <input type="hidden" name="contract_id" value="{contract_id}">
            <input type="hidden" name="property_id" value="{current_property_id}">

            <label>الوحدة</label>
            <select name="unit_id" required>{unit_options}</select>

            <label>المستأجر</label>
            <select name="tenant_id" id="contract-tenant-select-edit" required onchange="toggleNewTenantFields('edit')">{tenant_options}</select>

            <div id="contract-new-tenant-fields-edit" style="display:none;">
                <label>اسم المستأجر</label>
                <input type="text" name="new_tenant_name">

                <label>رقم الجوال</label>
                <input type="text" name="new_tenant_phone">

                <label>رقم الهوية</label>
                <input type="text" name="new_tenant_id_number">

                <label>نوع المستأجر</label>
                <select name="new_tenant_type">
                    <option value="">اختر نوع المستأجر</option>
                    <option value="فرد">فرد</option>
                    <option value="شركة">شركة</option>
                </select>
            </div>

            <input type="hidden" name="rent" value="{contract['rent'] or 0}">

            <label>الإيجار السنوي</label>
            <input type="number" step="0.01" name="annual_rent" value="{contract_form_values['annual_rent']}" required>

            <label>مدة العقد (سنوات)</label>
            <input type="number" name="contract_duration_years" min="1" step="1" value="{contract_form_values['contract_duration_years']}" required>

            <label>طريقة الدفع</label>
            <select name="payment_frequency" required>
                <option value="yearly" {"selected" if contract_form_values['payment_frequency'] == "yearly" else ""}>سنوي</option>
                <option value="semi-annual" {"selected" if contract_form_values['payment_frequency'] == "semi-annual" else ""}>نصف سنوي</option>
                <option value="quarterly" {"selected" if contract_form_values['payment_frequency'] == "quarterly" else ""}>ربع سنوي</option>
                <option value="monthly" {"selected" if contract_form_values['payment_frequency'] == "monthly" else ""}>شهري</option>
            </select>

            <label>بداية العقد</label>
            <input type="date" name="start_date" value="{contract['start_date'] or ''}">

            <label>نهاية العقد</label>
            <input type="date" name="end_date" value="{contract['end_date'] or ''}">

            <button type="submit" class="glass-btn gold-text">حفظ التعديلات</button>
        </form>
    </div>

    <a href="/property-rental-contracts?property_id={current_property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-property-rental-contract")
def update_property_rental_contract(
    request: Request,
    contract_id: int = Form(...),
    property_id: int = Form(...),
    unit_id: int = Form(...),
    tenant_id: str = Form(""),
    rent: float = Form(0),
    annual_rent: float = Form(0),
    contract_duration_years: int = Form(0),
    payment_frequency: str = Form("yearly"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    status: str = Form("ساري"),
    new_tenant_name: str = Form(""),
    new_tenant_phone: str = Form(""),
    new_tenant_id_number: str = Form(""),
    new_tenant_type: str = Form(""),
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-rental-contracts?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    current_contract = conn.execute(
        "SELECT unit_id FROM property_rent_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    old_unit_id = current_contract["unit_id"] if current_contract else 0
    resolved_tenant_id = resolve_contract_tenant_id(
        conn,
        property_id=property_id,
        unit_id=unit_id,
        tenant_id_value=tenant_id,
        new_tenant_name=new_tenant_name,
        new_tenant_phone=new_tenant_phone,
        new_tenant_id_number=new_tenant_id_number,
        new_tenant_type=new_tenant_type,
    )
    if not resolved_tenant_id:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(
                f"/property-rental-contracts?property_id={property_id}",
                error="يرجى اختيار مستأجر موجود أو إدخال بيانات مستأجر جديد"
            ),
            status_code=303
        )
    rent = calculate_contract_rent_value(annual_rent, contract_duration_years, payment_frequency)
    status = compute_contract_status(end_date, status)
    conn.execute(
        """
        UPDATE property_rent_contracts
        SET unit_id = ?, tenant_id = ?, rent = ?, start_date = ?, end_date = ?, status = ?
        WHERE id = ?
        """,
        (unit_id, resolved_tenant_id, rent, start_date, end_date, status, contract_id)
    )
    conn.execute("DELETE FROM contract_installments WHERE contract_id = ?", (contract_id,))
    installment_rows = build_contract_installment_rows(
        contract_id=contract_id,
        annual_rent=annual_rent,
        contract_duration_years=contract_duration_years,
        payment_frequency=payment_frequency,
        contract_start_date=start_date,
    )
    if installment_rows:
        conn.executemany(
            """
            INSERT INTO contract_installments (contract_id, amount, due_date, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            installment_rows,
        )
    if old_unit_id and old_unit_id != unit_id:
        refresh_unit_status_from_contracts(conn, old_unit_id)
    refresh_unit_status_from_contracts(conn, unit_id)
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={property_id}", message="تم تحديث العقد بنجاح"),
        status_code=303
    )


@app.post("/mark-contract-installment-paid/{installment_id}")
def mark_contract_installment_paid(request: Request, installment_id: int, property_id: int = Form(0)):
    conn = get_db()
    installment = conn.execute(
        """
        SELECT
            contract_installments.*,
            property_rent_contracts.property_id
        FROM contract_installments
        LEFT JOIN property_rent_contracts ON property_rent_contracts.id = contract_installments.contract_id
        WHERE contract_installments.id = ?
        """,
        (installment_id,),
    ).fetchone()

    current_property_id = property_id or (installment["property_id"] if installment else 0)
    if not installment:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", error="الدفعة غير موجودة"),
            status_code=303
        )

    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        back_url=f"/property-rental-contracts?property_id={current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result

    conn.execute(
        """
        UPDATE contract_installments
        SET status = ?, paid_at = ?
        WHERE id = ?
        """,
        ("paid", datetime.now().strftime("%Y-%m-%d %H:%M"), installment_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", message="تم تسجيل تحصيل الدفعة بنجاح"),
        status_code=303
    )


@app.get("/delete-property-rental-contract/{contract_id}")
def delete_property_rental_contract(request: Request, contract_id: int, property_id: int = 0):
    conn = get_db()
    contract = conn.execute("SELECT * FROM property_rent_contracts WHERE id = ?", (contract_id,)).fetchone()
    current_property_id = property_id or (contract["property_id"] if contract else 0)

    if not contract:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", error="العقد غير موجود"),
            status_code=303
        )

    unit_id = contract["unit_id"] or 0
    conn.execute("DELETE FROM property_rent_contracts WHERE id = ?", (contract_id,))
    conn.execute("DELETE FROM contract_installments WHERE contract_id = ?", (contract_id,))
    if unit_id:
        refresh_unit_status_from_contracts(conn, unit_id)
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-rental-contracts?property_id={current_property_id}", message="تم حذف العقد بنجاح"),
        status_code=303
    )


@app.get("/property-maintenance", response_class=HTMLResponse)
def property_maintenance_page(request: Request, property_id: int = 0, message: str = "", error: str = ""):
    if property_id:
        access_result = ensure_realestate_maintenance_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_maintenance_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
        access_result = company_result
    conn = get_db()
    current_property = None
    properties = conn.execute("SELECT * FROM property_properties ORDER BY name").fetchall()
    is_owner_view_only = realestate_owner_read_only(access_result)
    owner_property_ids = get_owner_accessible_property_set(access_result)
    if is_owner_view_only:
        properties = filter_rows_by_property_scope(properties, owner_property_ids, "id")
    if property_id:
        current_property = conn.execute(
            "SELECT * FROM property_properties WHERE id = ?",
            (property_id,)
        ).fetchone()
        units = conn.execute(
            "SELECT * FROM property_units WHERE property_id = ? ORDER BY name",
            (property_id,)
        ).fetchall()
        supervisors = conn.execute(
            "SELECT * FROM property_supervisors WHERE property_id = ? ORDER BY id DESC",
            (property_id,)
        ).fetchall()
    else:
        units = conn.execute("SELECT * FROM property_units ORDER BY name").fetchall()
        supervisors = conn.execute("SELECT * FROM property_supervisors ORDER BY supervisor_name").fetchall()
    if is_owner_view_only:
        units = filter_rows_by_property_scope(units, owner_property_ids)
        supervisors = filter_rows_by_property_scope(supervisors, owner_property_ids)
    if property_id:
        maintenance_items = conn.execute(
            """
            SELECT maintenance_requests.*, property_properties.name AS property_name,
                   property_units.name AS unit_name, property_tenants.name AS tenant_name
            FROM maintenance_requests
            LEFT JOIN property_properties ON property_properties.id = maintenance_requests.property_id
            LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
            LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
            WHERE maintenance_requests.property_id = ?
            ORDER BY maintenance_requests.id DESC
            """,
            (property_id,)
        ).fetchall()
    else:
        maintenance_items = conn.execute(
            """
            SELECT maintenance_requests.*, property_properties.name AS property_name,
                   property_units.name AS unit_name, property_tenants.name AS tenant_name
            FROM maintenance_requests
            LEFT JOIN property_properties ON property_properties.id = maintenance_requests.property_id
            LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
            LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
            ORDER BY maintenance_requests.id DESC
            """
        ).fetchall()
    if is_owner_view_only:
        maintenance_items = filter_rows_by_property_scope(maintenance_items, owner_property_ids)
    conn.close()

    property_selector = ""
    if property_id and current_property:
        property_selector = f"""
        <div class="inventory-note">الملك الحالي: <strong>{current_property['name']}</strong></div>
        <input type="hidden" name="property_id" value="{property_id}">
        """
    else:
        property_options = '<option value="">اختر الملك</option>'
        for prop in properties:
            selected = "selected" if property_id and prop["id"] == property_id else ""
            property_options += f'<option value="{prop["id"]}" {selected}>{prop["name"]}</option>'
        property_selector = f"""
        <label>الملك</label>
        <select name="property_id" required>{property_options}</select>
        """

    unit_options = '<option value="">بدون وحدة محددة</option>'
    for unit in units:
        unit_options += f'<option value="{unit["id"]}">{unit["name"]}</option>'

    default_supervisor = supervisors[0]["supervisor_name"] if supervisors else ""
    supervisor_options = '<option value="">اختر المشرف</option>'
    for supervisor in supervisors:
        selected = "selected" if default_supervisor and supervisor["supervisor_name"] == default_supervisor else ""
        supervisor_options += f'<option value="{supervisor["supervisor_name"]}" {selected}>{supervisor["supervisor_name"]}</option>'

    status_labels = {
        "new": "جديد",
        "reviewing": "قيد المراجعة",
        "scheduled": "مجدول",
        "in_progress": "جاري التنفيذ",
        "completed": "مكتمل",
        "cancelled": "ملغي",
    }

    feedback_html = render_page_feedback(message, error)

    rows = ""
    for item in maintenance_items:
        actions_cell = ""
        if not is_owner_view_only:
            actions_cell = f"""
            <td>
                <a href="/edit-property-maintenance/{item['id']}?property_id={item['property_id'] or property_id}" class="action-btn">تعديل</a>
                <a href="/update-property-maintenance-status/{item['id']}?status=reviewing&property_id={property_id}" class="action-btn">مراجعة</a>
                <a href="/update-property-maintenance-status/{item['id']}?status=in_progress&property_id={property_id}" class="action-btn">جاري</a>
                <a href="/update-property-maintenance-status/{item['id']}?status=completed&property_id={property_id}" class="action-btn">مكتمل</a>
                <a href="/delete-property-maintenance/{item['id']}?property_id={item['property_id'] or property_id}" class="action-btn delete-btn" onclick="return confirm('هل تريد حذف سجل الصيانة هذا؟')">حذف</a>
            </td>
            """
        rows += f"""
        <tr>
            <td>{item['property_name'] or '-'}</td>
            <td>{item['unit_name'] or '-'}</td>
            <td>{item['tenant_name'] or '-'}</td>
            <td>{item['maintenance_type'] or item['title'] or '-'}</td>
            <td>{item['assigned_to'] or '-'}</td>
            <td>{item['estimated_cost'] or 0}</td>
            <td>{status_labels.get(item['status'], item['status'] or '-')}</td>
            <td>{item['created_at'] or '-'}</td>
            {actions_cell}
        </tr>
        """

    add_maintenance_section = ""
    if not is_owner_view_only:
        add_maintenance_section = f"""
    <div class="inventory-panel inventory-table-panel">
        <h3>إضافة طلب صيانة</h3>
        <form action="/save-property-maintenance" method="post">
            {property_selector}

            <label>الوحدة</label>
            <select name="unit_id">{unit_options}</select>

            <label>نوع الصيانة</label>
            <input type="text" name="maintenance_type" placeholder="كهرباء / سباكة / تكييف / تشطيب" required>

            <label>العنوان</label>
            <input type="text" name="title" required>

            <label>الوصف</label>
            <textarea name="description" rows="4"></textarea>

            <label>الأولوية</label>
            <select name="priority">
                <option value="منخفضة">منخفضة</option>
                <option value="متوسطة">متوسطة</option>
                <option value="عالية">عالية</option>
                <option value="طارئة">طارئة</option>
            </select>

            <label>التكلفة التقديرية</label>
            <input type="number" step="0.01" name="estimated_cost">

            <label>المشرف المسؤول</label>
            <select name="assigned_to">{supervisor_options}</select>

            <label>الحالة</label>
            <select name="status">
                <option value="new">جديد</option>
                <option value="reviewing">قيد المراجعة</option>
                <option value="scheduled">مجدول</option>
                <option value="in_progress">جاري التنفيذ</option>
                <option value="completed">مكتمل</option>
                <option value="cancelled">ملغي</option>
            </select>

            <button type="submit" class="glass-btn gold-text">حفظ طلب الصيانة</button>
        </form>
    </div>
        """

    actions_header = "<th>الإدارة</th>" if not is_owner_view_only else ""
    empty_colspan = "9" if not is_owner_view_only else "8"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>الصيانة</h1>
    <p>{f"إدارة صيانة الملك: {current_property['name']}" if current_property else "إدارة جميع طلبات الصيانة"}</p>
    {feedback_html}

    {add_maintenance_section}

    <div class="inventory-panel inventory-table-panel">
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الملك</th>
                <th>الوحدة</th>
                <th>المستأجر</th>
                <th>النوع</th>
                <th>المسند إليه</th>
                <th>التكلفة التقديرية</th>
                <th>الحالة</th>
                <th>التاريخ</th>
                {actions_header}
            </tr>
            {rows if rows else f"<tr><td colspan='{empty_colspan}'>لا توجد طلبات صيانة</td></tr>"}
        </table>
    </div>

    <a href="{f'/property-management/{property_id}' if current_property else '/property-management'}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-property-maintenance")
def save_property_maintenance(
    request: Request,
    property_id: int = Form(...),
    unit_id: int = Form(0),
    maintenance_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("متوسطة"),
    estimated_cost: float = Form(0),
    status: str = Form("new"),
    assigned_to: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        area="maintenance",
        back_url=f"/property-maintenance?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    tenant = None
    if unit_id:
        tenant = conn.execute(
            "SELECT * FROM property_tenants WHERE unit_id = ? AND property_id = ? ORDER BY id DESC LIMIT 1",
            (unit_id, property_id)
        ).fetchone()
    conn.execute(
        """
        INSERT INTO maintenance_requests (
            property_id, unit_id, tenant_id, request_source, maintenance_type, title, description,
            priority, status, estimated_cost, assigned_to, created_at, updated_at, admin_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            unit_id if unit_id else None,
            tenant["id"] if tenant else None,
            "admin",
            maintenance_type,
            title,
            description,
            priority,
            status,
            estimated_cost,
            assigned_to,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            ""
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-maintenance?property_id={property_id}", message="تم حفظ طلب الصيانة بنجاح"),
        status_code=303
    )


@app.get("/update-property-maintenance-status/{maintenance_id}")
def update_property_maintenance_status(request: Request, maintenance_id: int, status: str, property_id: int = 0):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        area="maintenance",
        back_url=f"/property-maintenance?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        """
        UPDATE maintenance_requests
        SET status = ?, updated_at = ?, completed_date = CASE WHEN ? = 'completed' THEN ? ELSE completed_date END
        WHERE id = ?
        """,
        (
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            status,
            datetime.now().strftime("%Y-%m-%d"),
            maintenance_id
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/property-maintenance?property_id={property_id}", status_code=303)


@app.get("/edit-property-maintenance/{maintenance_id}", response_class=HTMLResponse)
def edit_property_maintenance_form(request: Request, maintenance_id: int, property_id: int = 0):
    conn = get_db()
    item = conn.execute("SELECT * FROM maintenance_requests WHERE id = ?", (maintenance_id,)).fetchone()
    if not item:
        conn.close()
        return "<h2>طلب الصيانة غير موجود</h2>"

    current_property_id = property_id or item["property_id"] or 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=current_property_id,
        area="maintenance",
        back_url=f"/property-maintenance?property_id={current_property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    current_property = conn.execute(
        "SELECT * FROM property_properties WHERE id = ?",
        (current_property_id,)
    ).fetchone() if current_property_id else None
    units = conn.execute(
        "SELECT * FROM property_units WHERE property_id = ? ORDER BY name",
        (current_property_id,)
    ).fetchall() if current_property_id else []
    supervisors = conn.execute(
        "SELECT * FROM property_supervisors WHERE property_id = ? ORDER BY id DESC",
        (current_property_id,)
    ).fetchall() if current_property_id else []
    conn.close()

    unit_options = '<option value="">بدون وحدة محددة</option>'
    for unit in units:
        selected = "selected" if item["unit_id"] == unit["id"] else ""
        unit_options += f'<option value="{unit["id"]}" {selected}>{unit["name"]}</option>'

    supervisor_options = '<option value="">اختر المشرف</option>'
    for supervisor in supervisors:
        selected = "selected" if item["assigned_to"] == supervisor["supervisor_name"] else ""
        supervisor_options += f'<option value="{supervisor["supervisor_name"]}" {selected}>{supervisor["supervisor_name"]}</option>'

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تعديل طلب الصيانة</h1>
    <p>{f"تحديث سجل صيانة في ملك: {current_property['name']}" if current_property else "تحديث سجل الصيانة"}</p>

    <div class="inventory-panel inventory-table-panel">
        <form action="/update-property-maintenance" method="post">
            <input type="hidden" name="maintenance_id" value="{maintenance_id}">
            <input type="hidden" name="property_id" value="{current_property_id}">

            <label>الوحدة</label>
            <select name="unit_id">{unit_options}</select>

            <label>نوع الصيانة</label>
            <input type="text" name="maintenance_type" value="{item['maintenance_type'] or ''}" required>

            <label>العنوان</label>
            <input type="text" name="title" value="{item['title'] or ''}" required>

            <label>الوصف</label>
            <textarea name="description" rows="4">{item['description'] or ''}</textarea>

            <label>الأولوية</label>
            <select name="priority">
                <option value="منخفضة" {"selected" if item['priority'] == "منخفضة" else ""}>منخفضة</option>
                <option value="متوسطة" {"selected" if item['priority'] == "متوسطة" else ""}>متوسطة</option>
                <option value="عالية" {"selected" if item['priority'] == "عالية" else ""}>عالية</option>
                <option value="طارئة" {"selected" if item['priority'] == "طارئة" else ""}>طارئة</option>
            </select>

            <label>التكلفة التقديرية</label>
            <input type="number" step="0.01" name="estimated_cost" value="{item['estimated_cost'] or 0}">

            <label>التكلفة الفعلية</label>
            <input type="number" step="0.01" name="actual_cost" value="{item['actual_cost'] or 0}">

            <label>المشرف المسؤول</label>
            <select name="assigned_to">{supervisor_options}</select>

            <label>الحالة</label>
            <select name="status">
                <option value="new" {"selected" if item['status'] == "new" else ""}>جديد</option>
                <option value="reviewing" {"selected" if item['status'] == "reviewing" else ""}>قيد المراجعة</option>
                <option value="scheduled" {"selected" if item['status'] == "scheduled" else ""}>مجدول</option>
                <option value="in_progress" {"selected" if item['status'] == "in_progress" else ""}>جاري التنفيذ</option>
                <option value="completed" {"selected" if item['status'] == "completed" else ""}>مكتمل</option>
                <option value="cancelled" {"selected" if item['status'] == "cancelled" else ""}>ملغي</option>
            </select>

            <button type="submit" class="glass-btn gold-text">حفظ التعديلات</button>
        </form>
    </div>

    <a href="/property-maintenance?property_id={current_property_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-property-maintenance")
def update_property_maintenance(
    request: Request,
    maintenance_id: int = Form(...),
    property_id: int = Form(...),
    unit_id: int = Form(0),
    maintenance_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("متوسطة"),
    estimated_cost: float = Form(0),
    actual_cost: float = Form(0),
    status: str = Form("new"),
    assigned_to: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        area="maintenance",
        back_url=f"/property-maintenance?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    tenant = None
    if unit_id:
        tenant = conn.execute(
            "SELECT * FROM property_tenants WHERE unit_id = ? AND property_id = ? ORDER BY id DESC LIMIT 1",
            (unit_id, property_id)
        ).fetchone()

    conn.execute(
        """
        UPDATE maintenance_requests
        SET unit_id = ?, tenant_id = ?, maintenance_type = ?, title = ?, description = ?,
            priority = ?, estimated_cost = ?, actual_cost = ?, status = ?, assigned_to = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            unit_id if unit_id else None,
            tenant["id"] if tenant else None,
            maintenance_type,
            title,
            description,
            priority,
            estimated_cost,
            actual_cost,
            status,
            assigned_to,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            maintenance_id
        )
    )
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-maintenance?property_id={property_id}", message="تم تحديث طلب الصيانة بنجاح"),
        status_code=303
    )


@app.get("/delete-property-maintenance/{maintenance_id}")
def delete_property_maintenance(request: Request, maintenance_id: int, property_id: int = 0):
    conn = get_db()
    item = conn.execute("SELECT * FROM maintenance_requests WHERE id = ?", (maintenance_id,)).fetchone()
    current_property_id = property_id or (item["property_id"] if item else 0)

    if not item:
        conn.close()
        return RedirectResponse(
            url=build_redirect_url(f"/property-maintenance?property_id={current_property_id}", error="سجل الصيانة غير موجود"),
            status_code=303
        )

    conn.execute("DELETE FROM maintenance_requests WHERE id = ?", (maintenance_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(
        url=build_redirect_url(f"/property-maintenance?property_id={current_property_id}", message="تم حذف سجل الصيانة بنجاح"),
        status_code=303
    )


@app.get("/property-supervisors", response_class=HTMLResponse)
def property_supervisors_page(request: Request, property_id: int = 0):
    if property_id:
        access_result = ensure_realestate_supervisors_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_supervisors_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
    conn = get_db()
    current_property = None
    properties = conn.execute("SELECT * FROM property_properties ORDER BY name").fetchall()
    if property_id:
        current_property = conn.execute(
            "SELECT * FROM property_properties WHERE id = ?",
            (property_id,)
        ).fetchone()
    if property_id:
        supervisors = conn.execute(
            """
            SELECT property_supervisors.*, property_properties.name AS property_name
            FROM property_supervisors
            LEFT JOIN property_properties ON property_properties.id = property_supervisors.property_id
            WHERE property_supervisors.property_id = ?
            ORDER BY property_supervisors.id DESC
            """,
            (property_id,)
        ).fetchall()
    else:
        supervisors = conn.execute(
            """
            SELECT property_supervisors.*, property_properties.name AS property_name
            FROM property_supervisors
            LEFT JOIN property_properties ON property_properties.id = property_supervisors.property_id
            ORDER BY property_supervisors.id DESC
            """
        ).fetchall()
    conn.close()

    property_selector = ""
    if property_id and current_property:
        property_selector = f"""
        <div class="inventory-note">الملك الحالي: <strong>{current_property['name']}</strong></div>
        <input type="hidden" name="property_id" value="{property_id}">
        """
    else:
        property_options = '<option value="">اختر الملك</option>'
        for prop in properties:
            selected = "selected" if property_id and prop["id"] == property_id else ""
            property_options += f'<option value="{prop["id"]}" {selected}>{prop["name"]}</option>'
        property_selector = f"""
        <label>الملك</label>
        <select name="property_id" required>{property_options}</select>
        """

    rows = ""
    for supervisor in supervisors:
        rows += f"""
        <tr>
            <td>{supervisor['property_name'] or '-'}</td>
            <td>{supervisor['supervisor_name']}</td>
            <td>{supervisor['phone'] or '-'}</td>
            <td>{supervisor['notes'] or '-'}</td>
        </tr>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>المشرفين</h1>
    <p>{f"المشرفون المرتبطون بالملك: {current_property['name']}" if current_property else "إدارة مشرفي الأملاك"}</p>

    <div class="inventory-panel inventory-table-panel">
        <h3>إضافة مشرف</h3>
        <form action="/save-property-supervisor" method="post">
            {property_selector}

            <label>اسم المشرف</label>
            <input type="text" name="supervisor_name" required>

            <label>الهاتف</label>
            <input type="text" name="phone">

            <label>ملاحظات</label>
            <textarea name="notes" rows="3"></textarea>

            <button type="submit" class="glass-btn gold-text">حفظ المشرف</button>
        </form>
    </div>

    <div class="inventory-panel inventory-table-panel">
        <table border="1" style="background:white;margin:auto;width:100%;">
            <tr>
                <th>الملك</th>
                <th>اسم المشرف</th>
                <th>الهاتف</th>
                <th>الملاحظات</th>
            </tr>
            {rows if rows else "<tr><td colspan='4'>لا يوجد مشرفون</td></tr>"}
        </table>
    </div>

    <a href="{f'/property-management/{property_id}' if current_property else '/property-management'}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-property-supervisor")
def save_property_supervisor(
    request: Request,
    property_id: int = Form(...),
    supervisor_name: str = Form(...),
    phone: str = Form(""),
    notes: str = Form("")
):
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        back_url=f"/property-supervisors?property_id={property_id}",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    conn.execute(
        """
        INSERT INTO property_supervisors (property_id, supervisor_name, phone, notes)
        VALUES (?, ?, ?, ?)
        """,
        (property_id, supervisor_name, phone, notes)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/property-supervisors?property_id={property_id}", status_code=303)


@app.get("/maintenance-management", response_class=HTMLResponse)
def maintenance_management(request: Request, property_id: int = 0, status: str = "", maintenance_type: str = ""):
    if property_id:
        access_result = ensure_realestate_maintenance_access(request, property_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    else:
        company_result = ensure_realestate_maintenance_access(request)
        if not isinstance(company_result, sqlite3.Row):
            return company_result
    current_user = getattr(request.state, "current_user", None) or get_current_user(request)
    conn = get_db()
    properties = conn.execute("SELECT * FROM property_properties ORDER BY name").fetchall()
    filters = []
    params = []

    query = """
    SELECT maintenance_requests.*, property_properties.name AS property_name,
           property_units.name AS unit_name, property_tenants.name AS tenant_name
    FROM maintenance_requests
    LEFT JOIN property_properties ON property_properties.id = maintenance_requests.property_id
    LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
    LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
    """

    if property_id:
        filters.append("maintenance_requests.property_id = ?")
        params.append(property_id)
    if status:
        filters.append("maintenance_requests.status = ?")
        params.append(status)
    if maintenance_type:
        filters.append("maintenance_requests.maintenance_type = ?")
        params.append(maintenance_type)

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += " ORDER BY maintenance_requests.id DESC"
    requests = conn.execute(query, params).fetchall()
    types = conn.execute(
        "SELECT DISTINCT maintenance_type FROM maintenance_requests WHERE maintenance_type IS NOT NULL AND maintenance_type != '' ORDER BY maintenance_type"
    ).fetchall()
    current_property = None
    if property_id:
        current_property = conn.execute("SELECT * FROM property_properties WHERE id = ?", (property_id,)).fetchone()
    conn.close()

    property_options = '<option value="">كل الأملاك</option>'
    for prop in properties:
        selected = "selected" if property_id and prop["id"] == property_id else ""
        property_options += f'<option value="{prop["id"]}" {selected}>{prop["name"]}</option>'

    status_options = '<option value="">كل الحالات</option>'
    for value, label in [
        ("new", "جديد"),
        ("reviewing", "قيد المراجعة"),
        ("scheduled", "مجدول"),
        ("in_progress", "جاري التنفيذ"),
        ("completed", "مكتمل"),
        ("cancelled", "ملغي"),
    ]:
        selected = "selected" if status == value else ""
        status_options += f'<option value="{value}" {selected}>{label}</option>'

    type_options = '<option value="">كل الأنواع</option>'
    for item in types:
        value = item["maintenance_type"]
        selected = "selected" if maintenance_type == value else ""
        type_options += f'<option value="{value}" {selected}>{value}</option>'

    is_admin_user = bool(current_user and is_admin(current_user))
    request_cards = ""
    for request in requests:
        delete_button_html = ""
        if is_admin_user:
            delete_button_html = f"""
            <form action="/delete-maintenance/{request['id']}" method="post" style="margin-top:10px;" onsubmit="return confirm('هل أنت متأكد من حذف طلب الصيانة؟');">
                <button type="submit" class="action-btn delete-btn">حذف</button>
            </form>
            """
        request_cards += f"""
        <div class="company-card realestate property-site-card">
            <a href="/maintenance-management/{request['id']}" style="text-decoration:none;color:inherit;display:block;">
                <h3>{request['maintenance_type'] or request['title'] or 'طلب صيانة'}</h3>
                <p>🏢 {request['property_name'] or '-'}</p>
                <p>🚪 {request['unit_name'] or '-'}</p>
                <p>👤 {request['tenant_name'] or '-'}</p>
                <p>📌 {request['status'] or '-'}</p>
                <p>🚨 {request['priority'] or '-'}</p>
            </a>
            {delete_button_html}
        </div>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>إدارة طلبات الصيانة</h1>
    <p>{f"لوحة الصيانة الداخلية للملك: {current_property['name']}" if current_property else "لوحة الإدارة الداخلية لجميع طلبات الصيانة"}</p>

    <div class="inventory-panel inventory-table-panel">
        <form method="get" action="/maintenance-management">
            <label>الملك</label>
            <select name="property_id">{property_options}</select>

            <label>الحالة</label>
            <select name="status">{status_options}</select>

            <label>النوع</label>
            <select name="maintenance_type">{type_options}</select>

            <button type="submit" class="glass-btn gold-text">تطبيق الفلاتر</button>
        </form>
    </div>

    <div class="companies">
        {request_cards if request_cards else '<div class="inventory-note">لا توجد طلبات صيانة مطابقة</div>'}
    </div>

    <br>
    <a href="{f'/property-management/{property_id}' if property_id else '/company/realestate'}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/delete-maintenance/{request_id}")
def delete_maintenance_request(request: Request, request_id: int):
    current_user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)
    if not is_admin(current_user):
        return access_denied_response("هذه العملية متاحة للمدير فقط", back_url="/maintenance-management")

    conn = get_db()
    request_row = conn.execute(
        "SELECT id FROM maintenance_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if request_row:
        conn.execute("DELETE FROM maintenance_requests WHERE id = ?", (request_id,))
        conn.commit()
    conn.close()
    return RedirectResponse(url="/maintenance-management", status_code=303)


@app.get("/maintenance-management/{request_id}", response_class=HTMLResponse)
def maintenance_management_detail(web_request: Request, request_id: int):
    conn = get_db()
    request = conn.execute(
        """
        SELECT maintenance_requests.*, property_properties.name AS property_name,
               property_units.name AS unit_name, property_tenants.name AS tenant_name
        FROM maintenance_requests
        LEFT JOIN property_properties ON property_properties.id = maintenance_requests.property_id
        LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
        LEFT JOIN property_tenants ON property_tenants.id = maintenance_requests.tenant_id
        WHERE maintenance_requests.id = ?
        """,
        (request_id,)
    ).fetchone()
    if not request:
        conn.close()
        return "<h2>طلب الصيانة غير موجود</h2>"

    access_result = ensure_realestate_maintenance_access(web_request, request["property_id"] or 0)
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result

    supervisors = conn.execute(
        "SELECT * FROM property_supervisors WHERE property_id = ? ORDER BY id DESC",
        (request["property_id"],)
    ).fetchall()
    conn.close()
    scheduled_input_value = format_scheduled_datetime_for_input(request["scheduled_date"] or "")

    supervisor_options = '<option value="">اختر الفني / المسؤول</option>'
    for supervisor in supervisors:
        selected = "selected" if request["assigned_to"] == supervisor["supervisor_name"] else ""
        supervisor_options += f'<option value="{supervisor["supervisor_name"]}" {selected}>{supervisor["supervisor_name"]}</option>'

    image_html = f'<img src="{request["image_path"]}" class="maintenance-preview">' if request["image_path"] else ""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تفاصيل طلب الصيانة</h1>
    <p>{request['maintenance_type'] or request['title'] or 'طلب صيانة'} - {request['property_name'] or '-'}</p>

    <div class="inventory-panel inventory-table-panel">
        <div class="maintenance-meta">
            <div class="inventory-note">الملك: <strong>{request['property_name'] or '-'}</strong></div>
            <div class="inventory-note">الوحدة: <strong>{request['unit_name'] or '-'}</strong></div>
            <div class="inventory-note">المستأجر: <strong>{request['tenant_name'] or '-'}</strong></div>
            <div class="inventory-note">المصدر: <strong>{request['request_source'] or '-'}</strong></div>
        </div>
        <div class="inventory-note">الوصف: {request['description'] or '-'}</div>
        {image_html}
    </div>

    <div class="inventory-panel inventory-table-panel">
        <h3>تحديث الطلب</h3>
        <form action="/ai/auto-process-maintenance/{request_id}" method="post" style="margin-bottom:15px;">
            <button type="submit" class="glass-btn gold-text">تشغيل المساعد وحفظ التحديثات</button>
        </form>
        <form action="/maintenance-management/{request_id}/update" method="post">
            <label>الفني / المسؤول</label>
            <select name="assigned_to">{supervisor_options}</select>

            <label>الأولوية</label>
            <select name="priority">
                <option value="منخفضة" {"selected" if request['priority'] == 'منخفضة' else ""}>منخفضة</option>
                <option value="متوسطة" {"selected" if request['priority'] == 'متوسطة' else ""}>متوسطة</option>
                <option value="عالية" {"selected" if request['priority'] == 'عالية' else ""}>عالية</option>
                <option value="طارئة" {"selected" if request['priority'] == 'طارئة' else ""}>طارئة</option>
            </select>

            <label>الحالة</label>
            <select name="status">
                <option value="new" {"selected" if request['status'] == 'new' else ""}>جديد</option>
                <option value="reviewing" {"selected" if request['status'] == 'reviewing' else ""}>قيد المراجعة</option>
                <option value="scheduled" {"selected" if request['status'] == 'scheduled' else ""}>مجدول</option>
                <option value="in_progress" {"selected" if request['status'] == 'in_progress' else ""}>جاري التنفيذ</option>
                <option value="completed" {"selected" if request['status'] == 'completed' else ""}>مكتمل</option>
                <option value="cancelled" {"selected" if request['status'] == 'cancelled' else ""}>ملغي</option>
            </select>

            <label>التكلفة التقديرية</label>
            <input type="number" step="0.01" name="estimated_cost" value="{request['estimated_cost'] or ''}">

            <label>التكلفة الفعلية</label>
            <input type="number" step="0.01" name="actual_cost" value="{request['actual_cost'] or ''}">

            <label>التاريخ المجدول</label>
            <input type="datetime-local" name="scheduled_date" value="{scheduled_input_value}">

            <label>ملاحظات الإدارة</label>
            <textarea name="admin_notes" rows="4">{request['admin_notes'] or ''}</textarea>

            <label>التقرير النهائي</label>
            <textarea name="final_report" rows="4">{request['final_report'] or ''}</textarea>

            <button type="submit" class="glass-btn gold-text">حفظ التحديثات</button>
        </form>
    </div>

    <a href="/maintenance-management?property_id={request['property_id'] or 0}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/maintenance-management/{request_id}/update")
def maintenance_management_update(
    request: Request,
    request_id: int,
    assigned_to: str = Form(""),
    priority: str = Form("متوسطة"),
    status: str = Form("reviewing"),
    estimated_cost: float = Form(0),
    actual_cost: float = Form(0),
    scheduled_date: str = Form(""),
    admin_notes: str = Form(""),
    final_report: str = Form(""),
):
    conn = get_db()
    request_row = conn.execute("SELECT property_id FROM maintenance_requests WHERE id = ?", (request_id,)).fetchone()
    property_id = request_row["property_id"] if request_row else 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        area="maintenance",
        back_url=f"/maintenance-management?property_id={property_id}" if property_id else "/maintenance-management",
    )
    if not isinstance(access_result, sqlite3.Row):
        conn.close()
        return access_result
    updated = update_maintenance_request_record(
        conn=conn,
        request_id=request_id,
        assigned_to=assigned_to,
        priority=priority,
        status=status,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        scheduled_date=scheduled_date,
        admin_notes=admin_notes,
        final_report=final_report,
    )
    if not updated:
        conn.close()
        return RedirectResponse(url="/maintenance-management", status_code=303)
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/maintenance-management/{request_id}", status_code=303)


def auto_process_maintenance_request(request_id: int) -> bool:
    conn = get_db()
    request = conn.execute("SELECT * FROM maintenance_requests WHERE id = ?", (request_id,)).fetchone()
    if not request:
        conn.close()
        return False

    if request["status"] == "completed":
        conn.close()
        return False

    issue_category = detect_maintenance_issue_category(
        request["maintenance_type"] or "",
        request["description"] or "",
    )

    property_responsible = get_property_responsible_person(conn, request["property_id"])
    assigned_to = property_responsible or (request["assigned_to"] or "")

    if issue_category == "electricity":
        priority = "عاجلة"
        estimated_cost = 500
    elif issue_category == "water":
        priority = "عاجلة"
        estimated_cost = 1000
    else:
        priority = "متوسطة"
        estimated_cost = 1000

    existing_schedule = request["scheduled_date"] or ""
    if existing_schedule and scheduled_date_has_explicit_time(existing_schedule):
        slot_start = parse_scheduled_datetime(existing_schedule)
        slot_end = slot_start + timedelta(hours=3) if slot_start else None
        scheduled_value = normalize_scheduled_date_value(existing_schedule)
    else:
        scheduling_start = parse_scheduled_datetime(existing_schedule) if existing_schedule else datetime.now()
        slot_start, slot_end = find_next_available_maintenance_slot(
            conn,
            current_request_id=request_id,
            start_from=scheduling_start or datetime.now()
        )
        scheduled_value = slot_start.strftime("%Y-%m-%d %H:%M")

    final_report = request["final_report"] or ""
    if slot_start and slot_end:
        final_report = build_visit_timing_message(slot_start, slot_end)

    maintenance_label = request["maintenance_type"] or "صيانة عامة"
    assigned_label = assigned_to or "لم يتم العثور على مسؤول مرتبط بالملك"
    admin_notes = (
        f"تم تصنيف الطلب تلقائيًا كـ {maintenance_label}، "
        f"وتحديد الأولوية {priority}، وإسناده إلى {assigned_label}، "
        f"وجدولة الزيارة تلقائيًا."
    )

    update_maintenance_request_record(
        conn=conn,
        request_id=request_id,
        assigned_to=assigned_to,
        priority=priority,
        status="scheduled",
        estimated_cost=estimated_cost,
        actual_cost=request["actual_cost"],
        scheduled_date=scheduled_value,
        admin_notes=admin_notes,
        final_report=final_report,
    )
    conn.commit()
    conn.close()
    return True


@app.post("/ai/auto-process-maintenance/{request_id}")
def auto_process_maintenance_request_route(request: Request, request_id: int):
    conn = get_db()
    request_row = conn.execute("SELECT property_id FROM maintenance_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()
    property_id = request_row["property_id"] if request_row else 0
    access_result = ensure_realestate_write_access(
        request,
        property_id=property_id,
        area="maintenance",
        back_url=f"/maintenance-management?property_id={property_id}" if property_id else "/maintenance-management",
    )
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    auto_process_maintenance_request(request_id)
    return RedirectResponse(url=f"/maintenance-management/{request_id}", status_code=303)


@app.get("/client-maintenance", response_class=HTMLResponse)
def client_maintenance(request: Request, tenant_id: int = 0):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_tenant(user):
        tenant_id = get_primary_tenant_id(user["id"]) or 0
        if not tenant_id:
            return access_denied_response("لا يوجد مستأجر مرتبط بحسابك", "/")
    elif tenant_id:
        access_result = ensure_tenant_access(request, tenant_id, "ليس لديك صلاحية الوصول")
        if not isinstance(access_result, sqlite3.Row):
            return access_result
    conn = get_db()
    tenants = conn.execute(
        """
        SELECT property_tenants.*, property_properties.name AS property_name, property_units.name AS unit_name
        FROM property_tenants
        LEFT JOIN property_properties ON property_properties.id = property_tenants.property_id
        LEFT JOIN property_units ON property_units.id = property_tenants.unit_id
        ORDER BY property_tenants.name
        """
    ).fetchall()

    current_tenant = None
    requests = []
    if tenant_id:
        current_tenant = conn.execute(
            """
            SELECT property_tenants.*, property_properties.name AS property_name, property_units.name AS unit_name
            FROM property_tenants
            LEFT JOIN property_properties ON property_properties.id = property_tenants.property_id
            LEFT JOIN property_units ON property_units.id = property_tenants.unit_id
            WHERE property_tenants.id = ?
            """,
            (tenant_id,)
        ).fetchone()
        requests = conn.execute(
            """
            SELECT * FROM maintenance_requests
            WHERE tenant_id = ?
            ORDER BY id DESC
            """,
            (tenant_id,)
        ).fetchall()
    conn.close()

    tenant_options = '<option value="">اختر مستأجرًا للتجربة</option>'
    for tenant in tenants:
        selected = "selected" if tenant_id and tenant["id"] == tenant_id else ""
        tenant_options += f'<option value="{tenant["id"]}" {selected}>{tenant["name"]} - {tenant["property_name"] or "-"}</option>'

    request_cards = ""
    for request in requests:
        request_cards += f"""
        <a href="/client-maintenance/{request['id']}?tenant_id={tenant_id}" class="company-card realestate property-site-card">
            <h3>{request['maintenance_type'] or 'طلب صيانة'}</h3>
            <p>📌 {request['status'] or '-'}</p>
            <p>🗓️ {request['created_at'] or '-'}</p>
            <p>{(request['description'] or '-')[:70]}</p>
        </a>
        """

    new_button = f'<a href="/client-maintenance/new?tenant_id={tenant_id}" class="glass-btn gold-text">طلب صيانة جديد</a>' if tenant_id else ''

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>واجهة العميل للصيانة</h1>
    <p>محاكاة تجربة المستأجر داخليًا بدون تسجيل دخول في وضع العرض التجريبي</p>

    <div class="inventory-panel inventory-table-panel">
        <form method="get" action="/client-maintenance">
            <label>اختر المستأجر</label>
            <select name="tenant_id" required>{tenant_options}</select>
            <button type="submit" class="glass-btn gold-text">دخول كمستأجر</button>
        </form>
    </div>

    {f'<div class="inventory-note">المستأجر الحالي: <strong>{current_tenant["name"]}</strong> | الوحدة: {current_tenant["unit_name"] or "-"} | الملك: {current_tenant["property_name"] or "-"}</div>' if current_tenant else ''}

    {new_button}

    <br><br>
    <div class="companies">
        {request_cards if tenant_id else '<div class="inventory-note">اختر مستأجرًا أولًا لعرض طلباته</div>'}
    </div>

    <br>
    <a href="/company/realestate" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.get("/client-maintenance/new", response_class=HTMLResponse)
def client_maintenance_new(request: Request, tenant_id: int):
    access_result = ensure_tenant_access(request, tenant_id, "ليس لديك صلاحية الوصول")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    tenant = conn.execute(
        """
        SELECT property_tenants.*, property_properties.name AS property_name, property_units.name AS unit_name
        FROM property_tenants
        LEFT JOIN property_properties ON property_properties.id = property_tenants.property_id
        LEFT JOIN property_units ON property_units.id = property_tenants.unit_id
        WHERE property_tenants.id = ?
        """,
        (tenant_id,)
    ).fetchone()
    conn.close()

    if not tenant:
        return RedirectResponse(url="/client-maintenance", status_code=303)

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>طلب صيانة جديد</h1>
    <p>سيتم ربط الطلب تلقائيًا بالمستأجر ووحدته وملكه بدون أي اختيار إضافي</p>

    <div class="inventory-panel inventory-table-panel">
        <div class="inventory-note">المستأجر: <strong>{tenant['name']}</strong></div>
        <div class="inventory-note">الملك: <strong>{tenant['property_name'] or '-'}</strong> | الوحدة: <strong>{tenant['unit_name'] or '-'}</strong></div>

        <form action="/client-maintenance/new" method="post" enctype="multipart/form-data">
            <input type="hidden" name="tenant_id" value="{tenant_id}">

            <div class="glass-card" style="margin-bottom:20px;">
                <h3>🤖 مساعد الصيانة الذكي</h3>
                <p>اكتب مشكلتك بشكل طبيعي وسيتم تعبئة الطلب تلقائياً</p>

                <textarea id="ai-input" class="glass-input" placeholder="مثال: المكيف ما يبرد وفي صوت مزعج"></textarea>

                <button type="button" onclick="analyzeRequest()" class="glass-btn" style="margin-top:10px;">
                    تحليل الطلب
                </button>
            </div>

            <label>نوع الصيانة</label>
            <input type="text" name="maintenance_type" placeholder="كهرباء / سباكة / تكييف / تشطيب" required>

            <label>الوصف</label>
            <textarea name="description" rows="5" required></textarea>

            <label>ملاحظة للعميل</label>
            <textarea name="client_notes" rows="3"></textarea>

            <label>صورة مرفقة (اختياري)</label>
            <input type="file" name="image">

            <button type="submit" class="glass-btn gold-text">إرسال الطلب</button>
        </form>
    </div>

    <a href="/client-maintenance?tenant_id={tenant_id}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
<script>
async function analyzeRequest() {{
    const text = document.getElementById("ai-input").value;

    if (!text) {{
        alert("اكتب المشكلة أولاً");
        return;
    }}

    const res = await fetch("/ai/analyze-maintenance", {{
        method: "POST",
        headers: {{
            "Content-Type": "application/json"
        }},
        body: JSON.stringify({{ text: text }})
    }});

    const data = await res.json();

    if (data.type) {{
        document.querySelector("input[name='maintenance_type']").value = data.type;
    }}

    if (data.description) {{
        document.querySelector("textarea[name='description']").value = data.description;
    }}

    if (data.priority) {{
        console.log("priority:", data.priority);
    }}
}}
</script>
"""


@app.post("/ai/analyze-maintenance")
async def analyze_maintenance(request: Request):
    data = await request.json()
    text = data.get("text", "")

    text_lower = text.lower()

    if "مكيف" in text or "ac" in text_lower:
        maintenance_type = "تكييف"
        priority = "متوسط"
    elif "كهرباء" in text or "ماس" in text:
        maintenance_type = "كهرباء"
        priority = "طارئ"
    elif "ماء" in text or "تسريب" in text:
        maintenance_type = "سباكة"
        priority = "عالي"
    else:
        maintenance_type = "صيانة عامة"
        priority = "عادي"

    return {
        "type": maintenance_type,
        "priority": priority,
        "description": text
    }


@app.post("/client-maintenance/new")
def client_maintenance_create(
    request: Request,
    tenant_id: int = Form(...),
    maintenance_type: str = Form(...),
    description: str = Form(...),
    client_notes: str = Form(""),
    image: UploadFile = File(None),
):
    access_result = ensure_tenant_access(request, tenant_id, "ليس لديك صلاحية الوصول")
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    conn = get_db()
    tenant = conn.execute("SELECT * FROM property_tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not tenant:
        conn.close()
        return RedirectResponse(url="/client-maintenance", status_code=303)

    image_path = save_maintenance_image(image)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor = conn.execute(
        """
        INSERT INTO maintenance_requests (
            property_id, unit_id, tenant_id, request_source, maintenance_type, title, description,
            priority, status, client_notes, created_at, updated_at, image_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant["property_id"],
            tenant["unit_id"],
            tenant_id,
            "tenant",
            maintenance_type,
            maintenance_type,
            description,
            "متوسطة",
            "new",
            client_notes,
            now,
            now,
            image_path
        )
    )
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()
    auto_process_maintenance_request(request_id)
    return RedirectResponse(url=f"/client-maintenance?tenant_id={tenant_id}", status_code=303)


@app.get("/client-maintenance/{request_id}", response_class=HTMLResponse)
def client_maintenance_detail(web_request: Request, request_id: int, tenant_id: int = 0):
    conn = get_db()
    request = conn.execute(
        """
        SELECT maintenance_requests.*, property_properties.name AS property_name, property_units.name AS unit_name
        FROM maintenance_requests
        LEFT JOIN property_properties ON property_properties.id = maintenance_requests.property_id
        LEFT JOIN property_units ON property_units.id = maintenance_requests.unit_id
        WHERE maintenance_requests.id = ?
        """,
        (request_id,)
    ).fetchone()
    conn.close()

    if not request:
        return "<h2>طلب الصيانة غير موجود</h2>"

    access_result = ensure_request_belongs_to_tenant(web_request, request_id, "ليس لديك صلاحية الوصول")
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    if tenant_id and request["tenant_id"] != tenant_id:
        return RedirectResponse(url=f"/client-maintenance?tenant_id={tenant_id}", status_code=303)

    image_html = f'<img src="{request["image_path"]}" class="maintenance-preview">' if request["image_path"] else ""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h1>تفاصيل طلب الصيانة</h1>
    <p>{request['maintenance_type'] or '-'} | الحالة: {request['status'] or '-'}</p>

    <div class="inventory-panel inventory-table-panel">
        <div class="inventory-note">الملك: <strong>{request['property_name'] or '-'}</strong></div>
        <div class="inventory-note">الوحدة: <strong>{request['unit_name'] or '-'}</strong></div>
        <div class="inventory-note">تاريخ الإنشاء: <strong>{request['created_at'] or '-'}</strong></div>
        <div class="inventory-note">الوصف: {request['description'] or '-'}</div>
        <div class="inventory-note">التاريخ المجدول: {request['scheduled_date'] or '-'}</div>
        <div class="inventory-note">التقرير النهائي: {request['final_report'] or 'لم يصدر بعد'}</div>
        {image_html}
    </div>

    <a href="/client-maintenance?tenant_id={tenant_id or request['tenant_id'] or 0}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""

@app.get("/realestate-investment", response_class=HTMLResponse)
def realestate_investment(request: Request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if is_active_projects_project_manager(user):
        return RedirectResponse(url="/investment-projects", status_code=303)

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>الاستثمار العقاري</h1>

<div class="companies">

<a href="/investment-projects" class="company-card realestate">
<h2>المشاريع القائمة</h2>
</a>

<a href="/investment-under-construction" class="company-card realestate">
<h2>مشاريع تحت التنفيذ</h2>
</a>

</div>

<br>

<a href="/company/realestate" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.get("/investment-projects", response_class=HTMLResponse)
def investment_projects(request: Request):
    try:
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        projects = get_investment_projects_for_user(user)
        if is_active_projects_project_manager(user) and len(projects) == 1:
            return RedirectResponse(url=f"/investment-project/{projects[0]['id']}", status_code=303)

        buttons = ""
        for p in projects:
            buttons += f"""
<a href="/investment-project/{p['id']}" class="company-card realestate">
<h2>{p['name']}</h2>
<p>{escape(p['location'] or 'بدون موقع')}</p>
</a>
"""

        create_button = ""
        back_url = "/realestate-investment"
        if not is_active_projects_project_manager(user):
            create_button = """
<a href="/new-investment-project" class="company-card realestate">
<h2>&#10133; إضافة مشروع</h2>
</a>
"""
        else:
            back_url = "/"

        return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>المشاريع القائمة</h1>

{create_button}

<br><br>

<div class="companies">

{buttons if buttons else "<p>لا توجد مشاريع مخصصة لك حالياً</p>"}

</div>

<br>

<a href="{back_url}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)

@app.get("/new-investment-project", response_class=HTMLResponse)
def new_investment_project(request: Request):
    try:
        access_result = ensure_investment_project_management_access(request)
        if not isinstance(access_result, sqlite3.Row):
            return access_result

        return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>إضافة مشروع استثماري</h1>

<form action="/save-investment-project" method="post">

اسم المشروع:
<br>
<input type="text" name="name" required>

<br><br>

الموقع:
<br>
<input type="text" name="location">

<br><br>

عدد الوحدات:
<br>
<input type="number" name="units">

<br><br>

<button type="submit" class="glass-btn gold-text">حفظ المشروع</button>

</form>

<br>

<a href="/investment-projects" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)

@app.post("/save-investment-project")
def save_investment_project(
    request: Request,
    name: str = Form(...),
    location: str = Form(""),
    units: int = Form(0),
):
    try:
        access_result = ensure_investment_project_management_access(request)
        if not isinstance(access_result, sqlite3.Row):
            return access_result

        conn = get_db()
        conn.execute(
            "INSERT INTO investment_projects (name, location, units, status) VALUES (?, ?, ?, ?)",
            (name, location, units, "قائم")
        )
        conn.commit()
        conn.close()

        return RedirectResponse(
            url="/investment-projects",
            status_code=303
        )
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)


@app.get("/edit-investment-project/{project_id}", response_class=HTMLResponse)
def edit_investment_project(request: Request, project_id: int):
    try:
        access_result = ensure_investment_project_management_access(request)
        if not isinstance(access_result, sqlite3.Row):
            return access_result

        conn = get_db()
        project = conn.execute("SELECT * FROM investment_projects WHERE id = ?", (project_id,)).fetchone()
        conn.close()
        if not project:
            return RedirectResponse(url="/investment-projects", status_code=303)

        return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>تعديل المشروع الاستثماري</h1>

<form action="/update-investment-project/{project_id}" method="post">

اسم المشروع:
<br>
<input type="text" name="name" value="{escape(project['name'] or '')}" required>

<br><br>

الموقع:
<br>
<input type="text" name="location" value="{escape(project['location'] or '')}">

<br><br>

عدد الوحدات:
<br>
<input type="number" name="units" value="{project['units'] or 0}">

<br><br>

الحالة:
<br>
<input type="text" name="status" value="{escape(project['status'] or 'قائم')}">

<br><br>

<button type="submit" class="glass-btn gold-text">حفظ التعديل</button>

</form>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)


@app.post("/update-investment-project/{project_id}")
def update_investment_project(
    request: Request,
    project_id: int,
    name: str = Form(...),
    location: str = Form(""),
    units: int = Form(0),
    status: str = Form("قائم"),
):
    try:
        access_result = ensure_investment_project_management_access(request)
        if not isinstance(access_result, sqlite3.Row):
            return access_result

        conn = get_db()
        conn.execute(
            "UPDATE investment_projects SET name = ?, location = ?, units = ?, status = ? WHERE id = ?",
            (name, location, units, status, project_id),
        )
        conn.commit()
        conn.close()
        return RedirectResponse(url=f"/investment-project/{project_id}", status_code=303)
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)

@app.get("/investment-project/{project_id}", response_class=HTMLResponse)
def investment_project_dashboard(request: Request, project_id: int):
    try:
        access_result = ensure_investment_project_access(request, project_id)
        if not isinstance(access_result, sqlite3.Row):
            return access_result
        user = access_result

        conn = get_db()
        project = conn.execute(
            "SELECT * FROM investment_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not project:
            conn.close()
            return HTMLResponse("<h2>المشروع غير موجود</h2>", status_code=404)

        units = conn.execute(
            "SELECT * FROM investment_units WHERE project_id = ?",
            (project_id,)
        ).fetchall()
        contracts = conn.execute(
            """
            SELECT investment_contracts.*, investment_units.name as unit_name
            FROM investment_contracts
            JOIN investment_units ON investment_contracts.unit_id = investment_units.id
            WHERE investment_units.project_id = ?
            """,
            (project_id,),
        ).fetchall()
        expenses = conn.execute(
            "SELECT * FROM investment_expenses WHERE project_id = ?",
            (project_id,)
        ).fetchall()
        conn.close()

        total_units = len(units)
        rented_units = len(contracts)
        empty_units = total_units - rented_units
        occupancy_rate = round((rented_units / total_units) * 100) if total_units > 0 else 0

        yearly_income = 0
        for c in contracts:
            rent = c["rent"] or 0
            payment = c["payment_type"]
            if payment == "شهري":
                yearly_income += rent * 12
            elif payment == "ربع سنوي":
                yearly_income += rent * 4
            elif payment == "نصف سنوي":
                yearly_income += rent * 2
            elif payment == "سنوي":
                yearly_income += rent

        total_expenses = sum((e["amount"] or 0) for e in expenses)
        profit = int(yearly_income - total_expenses)
        yearly_income = int(yearly_income)
        total_expenses = int(total_expenses)

        today = datetime.today()
        ending_contracts = ""
        for c in contracts:
            if c["end_date"]:
                end = datetime.strptime(c["end_date"], "%Y-%m-%d")
                days_left = (end - today).days
                if 0 <= days_left <= 30:
                    ending_contracts += f"""
<tr>
<td>{c['unit_name']}</td>
<td>{days_left} يوم</td>
<td>{c['end_date']}</td>
</tr>
"""

        edit_button = ""
        if not is_active_projects_project_manager(user):
            edit_button = f'<a href="/edit-investment-project/{project_id}" class="glass-btn gold-text">تعديل المشروع</a>'

        return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>{project['name']}</h1>
<p>الموقع: {project['location'] or '-'}</p>

<br><br>

<h2>ملخص المشروع</h2>
<table border="1" style="background:white;margin:auto;width:60%;text-align:center">
<tr>
<th>الإيرادات السنوية</th>
<th>المصروفات</th>
<th>صافي الربح</th>
</tr>
<tr>
<td>{yearly_income} ريال</td>
<td>{total_expenses} ريال</td>
<td>{profit} ريال</td>
</tr>
</table>

<br><br>

<h2>حالة الوحدات</h2>
<table border="1" style="background:white;margin:auto;width:60%;text-align:center">
<tr>
<th>عدد الوحدات</th>
<th>الوحدات المؤجرة</th>
<th>الوحدات الفارغة</th>
<th>نسبة الإشغال</th>
</tr>
<tr>
<td>{total_units}</td>
<td>{rented_units}</td>
<td>{empty_units}</td>
<td>{occupancy_rate}%</td>
</tr>
</table>

<br><br>

<h2>العقود التي ستنتهي قريباً</h2>
<table border="1" style="background:white;margin:auto;width:60%;text-align:center">
<tr>
<th>الوحدة</th>
<th>الأيام المتبقية</th>
<th>تاريخ النهاية</th>
</tr>
{ending_contracts if ending_contracts else "<tr><td colspan='3'>لا يوجد عقود قريبة الانتهاء</td></tr>"}
</table>

<br><br>

<div class="companies">
<a href="/investment-units?project_id={project_id}" class="company-card realestate"><h2>الوحدات</h2></a>
<a href="/investment-tenants?project_id={project_id}" class="company-card realestate"><h2>المستأجرين</h2></a>
<a href="/investment-contracts?project_id={project_id}" class="company-card realestate"><h2>العقود</h2></a>
<a href="/investment-income?project_id={project_id}" class="company-card realestate"><h2>الإيرادات</h2></a>
<a href="/investment-expenses?project_id={project_id}" class="company-card realestate"><h2>المصروفات</h2></a>
<a href="/investment-employees?project_id={project_id}" class="company-card realestate"><h2>الموظفين</h2></a>
</div>

<br>
{edit_button}
<br><br>
<a href="/investment-projects" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""
    except Exception as exc:
        return safe_error_response(request, exc, status_code=500)

@app.get("/investment-units", response_class=HTMLResponse)
def investment_units(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    units = conn.execute(
        "SELECT * FROM investment_units WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for u in units:
        rows += f"""
<tr>
<td>{u['name']}</td>
<td>{u['type']}</td>
<td>{u['rent']} ريال</td>
<td>{u['status']}</td>

<td>

<a href="/edit-unit/{u['id']}?project_id={project_id}" class="action-btn">
تعديل
</a>

|

<a href="/delete-unit/{u['id']}?project_id={project_id}"
onclick="return confirm('هل تريد حذف الوحدة؟')" class="action-btn">
حذف
</a>

</td>

</tr>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>الوحدات</h1>

<form action="/save-unit" method="post">

<input type="hidden" name="project_id" value="{project_id}">

اسم الوحدة:
<input type="text" name="name" required>

<br><br>

النوع:
<select name="type">
<option value="شقة">شقة</option>
<option value="محل">محل</option>
<option value="مكتب">مكتب</option>
</select>

<br><br>

الإيجار:
<input type="number" name="rent">

<br><br>

الحالة:
<select name="status">
<option value="متاح">متاح</option>
<option value="مؤجر">مؤجر</option>
<option value="صيانة">صيانة</option>
</select>

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة وحدة</button>

</form>

<br><br>

<table border="1" style="background:white;margin:auto;width:70%">

<tr>
<th>الوحدة</th>
<th>النوع</th>
<th>الإيجار</th>
<th>الحالة</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='4'>لا توجد وحدات</td></tr>"}

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-unit")
def save_unit(
    request: Request,
    project_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    rent: float = Form(0),
    status: str = Form(...)
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "INSERT INTO investment_units (project_id, name, type, rent, status) VALUES (?, ?, ?, ?, ?)",
        (project_id, name, type, rent, status)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-units?project_id={project_id}",
        status_code=303
    )

@app.get("/investment-tenants", response_class=HTMLResponse)
def investment_tenants(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    tenants = conn.execute("""
        SELECT investment_tenants.*, investment_units.name as unit_name
        FROM investment_tenants
        JOIN investment_units
        ON investment_tenants.unit_id = investment_units.id
        WHERE investment_units.project_id = ?
    """, (project_id,)).fetchall()

    units = conn.execute(
        "SELECT * FROM investment_units WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for t in tenants:
        rows += f"""
<tr>

<td>{t['name']}</td>
<td>{t['unit_name']}</td>
<td>{t['phone']}</td>
<td>{t['id_number']}</td>

<td>

<a href="/edit-tenant/{t['id']}?project_id={project_id}" class="action-btn">
تعديل
</a>

|

<a href="/delete-tenant/{t['id']}?project_id={project_id}"
onclick="return confirm('حذف المستأجر؟')" class="action-btn">
حذف
</a>

</td>

</tr>
"""

    unit_options = ""
    for u in units:
        unit_options += f"<option value='{u['id']}'>{u['name']}</option>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>المستأجرين</h1>

<form action="/save-tenant" method="post">

<input type="hidden" name="project_id" value="{project_id}">

اسم المستأجر:
<input type="text" name="name" required>

<br><br>

الوحدة:
<select name="unit_id">
{unit_options}
</select>

<br><br>

رقم الجوال:
<input type="text" name="phone">

<br><br>

رقم الهوية:
<input type="text" name="id_number">

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة مستأجر</button>

</form>

<br><br>

<table border="1" style="background:white;margin:auto;width:80%">

<tr>
<th>المستأجر</th>
<th>الوحدة</th>
<th>الجوال</th>
<th>الهوية</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='4'>لا يوجد مستأجرين</td></tr>"}

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-tenant")
def save_tenant(
    request: Request,
    project_id: int = Form(...),
    unit_id: int = Form(...),
    name: str = Form(...),
    phone: str = Form(""),
    id_number: str = Form("")
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    unit = conn.execute("SELECT id FROM investment_units WHERE id = ? AND project_id = ?", (unit_id, project_id)).fetchone()
    if not unit:
        conn.close()
        return access_denied_response("الوحدة المحددة لا تتبع هذا المشروع", back_url=f"/investment-tenants?project_id={project_id}")

    conn.execute(
        "INSERT INTO investment_tenants (unit_id, name, phone, id_number) VALUES (?, ?, ?, ?)",
        (unit_id, name, phone, id_number)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-tenants?project_id={project_id}",
        status_code=303
    )

@app.get("/investment-contracts", response_class=HTMLResponse)
def investment_contracts(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result
    is_admin_user = is_admin(access_result)

    conn = get_db()

    contracts = conn.execute("""
        SELECT investment_contracts.*, 
        investment_tenants.name as tenant_name,
        investment_units.name as unit_name
        FROM investment_contracts
        JOIN investment_tenants
        ON investment_contracts.tenant_id = investment_tenants.id
        JOIN investment_units
        ON investment_contracts.unit_id = investment_units.id
        WHERE investment_units.project_id = ?
    """, (project_id,)).fetchall()
    attachments_map = get_contract_attachment_rows(
        conn,
        CONTRACT_ATTACHMENT_SOURCE_INVESTMENT,
        [row["id"] for row in contracts],
    )

    tenants = conn.execute(
        """
        SELECT investment_tenants.*
        FROM investment_tenants
        JOIN investment_units ON investment_units.id = investment_tenants.unit_id
        WHERE investment_units.project_id = ?
        ORDER BY investment_tenants.id DESC
        """,
        (project_id,)
    ).fetchall()

    units = conn.execute(
        "SELECT * FROM investment_units WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for c in contracts:
        attachments_html = render_contract_attachments_html(
            attachments_map.get(c["id"], []),
            CONTRACT_ATTACHMENT_SOURCE_INVESTMENT,
            c["id"],
            is_admin_user=is_admin_user,
            project_id=project_id,
        )
        rows += f"""
<tr>

<td>{c['tenant_name']}</td>
<td>{c['unit_name']}</td>
<td>{c['rent']} ريال</td>
<td>{c['payment_type']}</td>
<td>{c['start_date']}</td>
<td>{c['end_date']}</td>
<td>{attachments_html}</td>

<td>

<a href="/delete-contract/{c['id']}?project_id={project_id}"
onclick="return confirm('حذف العقد؟')" class="action-btn">
حذف
</a>

</td>

</tr>
"""

    tenant_options = ""
    for t in tenants:
        tenant_options += f"<option value='{t['id']}'>{t['name']}</option>"

    unit_options = ""
    for u in units:
        unit_options += f"<option value='{u['id']}'>{u['name']}</option>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>العقود</h1>

<form action="/save-investment-contract" method="post">

<input type="hidden" name="project_id" value="{project_id}">

المستأجر:
<select name="tenant_id">
{tenant_options}
</select>

<br><br>

الوحدة:
<select name="unit_id">
{unit_options}
</select>

<br><br>

الإيجار:
<input type="number" name="rent">

<br><br>

طريقة الدفع:
<select name="payment_type">
<option value="شهري">شهري</option>
<option value="ربع سنوي">ربع سنوي</option>
<option value="نصف سنوي">نصف سنوي</option>
<option value="سنوي">سنوي</option>
</select>

<br><br>

بداية العقد:
<input type="date" name="start_date">

<br><br>

نهاية العقد:
<input type="date" name="end_date">

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة عقد</button>

</form>

<br><br>

<table border="1" style="background:white;margin:auto;width:90%">

<tr>
<th>المستأجر</th>
<th>الوحدة</th>
<th>الإيجار</th>
<th>طريقة الدفع</th>
<th>بداية العقد</th>
<th>نهاية العقد</th>
<th>المرفقات</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='8'>لا توجد عقود</td></tr>"}

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-investment-contract")
def save_investment_contract(
    request: Request,
    project_id: int = Form(...),
    tenant_id: int = Form(...),
    unit_id: int = Form(...),
    rent: float = Form(...),
    payment_type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...)
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    unit = conn.execute("SELECT id FROM investment_units WHERE id = ? AND project_id = ?", (unit_id, project_id)).fetchone()
    tenant = conn.execute(
        """
        SELECT investment_tenants.id
        FROM investment_tenants
        JOIN investment_units ON investment_units.id = investment_tenants.unit_id
        WHERE investment_tenants.id = ? AND investment_units.project_id = ?
        """,
        (tenant_id, project_id),
    ).fetchone()
    if not unit or not tenant:
        conn.close()
        return access_denied_response("بيانات العقد لا تتبع هذا المشروع", back_url=f"/investment-contracts?project_id={project_id}")

    conn.execute(
        """INSERT INTO investment_contracts 
        (tenant_id, unit_id, rent, payment_type, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (tenant_id, unit_id, rent, payment_type, start_date, end_date)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-contracts?project_id={project_id}",
        status_code=303
    )

@app.get("/investment-income", response_class=HTMLResponse)
def investment_income(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    contracts = conn.execute("""
        SELECT investment_contracts.*, investment_units.name as unit_name
        FROM investment_contracts
        JOIN investment_units
        ON investment_contracts.unit_id = investment_units.id
        WHERE investment_units.project_id = ?
""", (project_id,)).fetchall()

    conn.close()

    monthly_total = 0
    yearly_total = 0

    rows = ""

    for c in contracts:

        rent = c["rent"]
        payment = c["payment_type"]

        monthly = 0

        if payment == "شهري":
            monthly = rent

        elif payment == "ربع سنوي":
            monthly = rent / 3

        elif payment == "نصف سنوي":
            monthly = rent / 6

        elif payment == "سنوي":
            monthly = rent / 12

        monthly_total += monthly
        yearly_total += monthly * 12

        rows += f"""
        <tr>
            <td>{c['unit_name']}</td>
            <td>{rent} ريال</td>
            <td>{payment}</td>
            <td>{round(monthly,2)} ريال</td>
        </tr>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>إيرادات المشروع</h1>

<h3>الإيراد الشهري</h3>
<p>{round(monthly_total,2)} ريال</p>

<h3>الإيراد السنوي</h3>
<p>{round(yearly_total,2)} ريال</p>

<br><br>

<table border="1" style="background:white;margin:auto;width:80%">

<tr>
<th>الوحدة</th>
<th>قيمة الإيجار</th>
<th>طريقة الدفع</th>
<th>الإيراد الشهري</th>
</tr>

{rows if rows else "<tr><td colspan='4'>لا توجد عقود</td></tr>"}

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.get("/investment-expenses", response_class=HTMLResponse)
def investment_expenses(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    expenses = conn.execute(
        "SELECT * FROM investment_expenses WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""
    total = 0

    for e in expenses:
        total += e["amount"]
        rows += f"""
<tr>

<td>{e['title']}</td>
<td>{e['amount']} ريال</td>
<td>{e['date']}</td>

<td>

<a href="/delete-expense/{e['id']}?project_id={project_id}"
onclick="return confirm('حذف المصروف؟')" class="action-btn">
حذف
</a>

</td>

</tr>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}

<div class="dashboard">

<h1>مصروفات المشروع</h1>

<form action="/save-investment-expense" method="post">

<input type="hidden" name="project_id" value="{project_id}">

اسم المصروف:
<input type="text" name="title" required>

<br><br>

المبلغ:
<input type="number" step="0.01" name="amount" required>

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة المصروف</button>

</form>

<br><br>

<table border="1" style="background:white;margin:auto;width:70%">

<tr>
<th>المصروف</th>
<th>المبلغ</th>
<th>التاريخ</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='3'>لا توجد مصروفات</td></tr>"}

<tr>
<td><strong>الإجمالي</strong></td>
<td colspan="2"><strong>{total} ريال</strong></td>
</tr>

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-investment-expense")
def save_investment_expense(
    request: Request,
    project_id: int = Form(...),
    title: str = Form(...),
    amount: float = Form(...)
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "INSERT INTO investment_expenses (project_id, title, amount, date) VALUES (?, ?, ?, DATE('now'))",
        (project_id, title, amount)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-expenses?project_id={project_id}",
        status_code=303
    )

@app.get("/investment-employees", response_class=HTMLResponse)
def investment_employees(request: Request, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    employees = conn.execute(
        "SELECT * FROM investment_employees WHERE project_id = ?",
        (project_id,)
    ).fetchall()

    conn.close()

    rows = ""

    for e in employees:
        rows += f"""
<tr>

<td>{e['name']}</td>
<td>{e['role']}</td>
<td>{e['phone']}</td>

<td>

<a href="/delete-investment-employee/{e['id']}?project_id={project_id}"
onclick="return confirm('حذف الموظف؟')" class="action-btn">
حذف
</a>

</td>

</tr>
"""

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">

<div class="dashboard">

<h1>موظفين المشروع</h1>

<form action="/save-investment-employee" method="post">

<input type="hidden" name="project_id" value="{project_id}">

الاسم:
<input type="text" name="name" required>

<br><br>

الوظيفة:
<input type="text" name="role">

<br><br>

الجوال:
<input type="text" name="phone">

<br><br>

<button type="submit" class="glass-btn gold-text">إضافة موظف</button>

</form>

<br><br>

<table border="1" style="background:white;margin:auto;width:70%">

<tr>
<th>الاسم</th>
<th>الوظيفة</th>
<th>الجوال</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='3'>لا يوجد موظفين</td></tr>"}

</table>

<br>

<a href="/investment-project/{project_id}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""

@app.post("/save-investment-employee")
def save_investment_employee(
    request: Request,
    project_id: int = Form(...),
    name: str = Form(...),
    role: str = Form(""),
    phone: str = Form("")
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "INSERT INTO investment_employees (project_id, name, role, phone) VALUES (?, ?, ?, ?)",
        (project_id, name, role, phone)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-employees?project_id={project_id}",
        status_code=303
    )

@app.get("/delete-unit/{unit_id}")
def delete_unit(request: Request, unit_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "DELETE FROM investment_units WHERE id = ? AND project_id = ?",
        (unit_id, project_id)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-units?project_id={project_id}",
        status_code=303
    )


@app.get("/edit-unit/{unit_id}", response_class=HTMLResponse)
def edit_unit(request: Request, unit_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    unit = conn.execute(
        "SELECT * FROM investment_units WHERE id = ? AND project_id = ?",
        (unit_id, project_id)
    ).fetchone()

    conn.close()

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">

<div class="dashboard">

<h1>تعديل الوحدة</h1>

<form action="/update-unit" method="post">

<input type="hidden" name="unit_id" value="{unit_id}">
<input type="hidden" name="project_id" value="{project_id}">

اسم الوحدة:
<input type="text" name="name" value="{unit['name']}">

<br><br>

النوع:
<input type="text" name="type" value="{unit['type']}">

<br><br>

الإيجار:
<input type="number" name="rent" value="{unit['rent']}">

<br><br>

الحالة:
<input type="text" name="status" value="{unit['status']}">

<br><br>

<button type="submit" class="glass-btn gold-text">حفظ التعديل</button>

</form>

</div>
"""


@app.post("/update-unit")
def update_unit(
    request: Request,
    unit_id: int = Form(...),
    project_id: int = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    rent: float = Form(...),
    status: str = Form(...)
):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute("""
    UPDATE investment_units
    SET name=?, type=?, rent=?, status=?
    WHERE id=? AND project_id=?
    """, (name, type, rent, status, unit_id, project_id))

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-units?project_id={project_id}",
        status_code=303
    )

@app.get("/delete-tenant/{tenant_id}")
def delete_tenant(request: Request, tenant_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        """
        DELETE FROM investment_tenants
        WHERE id = ? AND unit_id IN (SELECT id FROM investment_units WHERE project_id = ?)
        """,
        (tenant_id, project_id)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-tenants?project_id={project_id}",
        status_code=303
    )


@app.get("/delete-contract/{contract_id}")
def delete_contract(request: Request, contract_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        """
        DELETE FROM investment_contracts
        WHERE id = ? AND unit_id IN (SELECT id FROM investment_units WHERE project_id = ?)
        """,
        (contract_id, project_id)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-contracts?project_id={project_id}",
        status_code=303
    )


@app.get("/delete-expense/{expense_id}")
def delete_expense(request: Request, expense_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "DELETE FROM investment_expenses WHERE id = ? AND project_id = ?",
        (expense_id, project_id)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-expenses?project_id={project_id}",
        status_code=303
    )

@app.get("/delete-investment-employee/{employee_id}")
def delete_investment_employee(request: Request, employee_id: int, project_id: int):
    access_result = ensure_investment_project_access(request, project_id)
    if not isinstance(access_result, sqlite3.Row):
        return access_result

    conn = get_db()

    conn.execute(
        "DELETE FROM investment_employees WHERE id = ? AND project_id = ?",
        (employee_id, project_id)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/investment-employees?project_id={project_id}",
        status_code=303
    )


# ======================
# معدات اللوجستيات (CRUD كامل)
# ======================

@app.get("/equipment", response_class=HTMLResponse)
def equipment_list(company: str = ""):
    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM logistics_equipment WHERE company = ? ORDER BY id DESC",
        (company,)
    ).fetchall()
    conn.close()

    rows = ""
    for e in equipment:
        rows += f"""
        <tr>
            <td>{e['id']}</td>
            <td>{e['name']}</td>
            <td>{e['type']}</td>
            <td>{e['quantity']}</td>
            <td>{e['status']}</td>
            <td>{e['location']}</td>
            <td>{e['cost']} ريال</td>
            <td>{e['date_added']}</td>
            <td>
                <a href="/equipment/{e['id']}?company={company}" class="action-btn">عرض</a>
                <a href="/edit-logistics-equipment/{e['id']}?company={company}" class="action-btn">تعديل</a>
                <a href="/delete-logistics-equipment/{e['id']}?company={company}" onclick="return confirm('هل تريد حذف هذه المعدة؟')" class="action-btn">حذف</a>
            </td>
        </tr>
        """

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">

<h1>معدات اللوجستيات</h1>

<a href="/new-equipment?company={company}" class="company-card {company}">
<h2>&#10133; معدة جديدة</h2>
</a>

<br><br>

<table border="1" style="background:white;margin:auto;width:95%;">
<tr>
<th>رقم</th>
<th>الاسم</th>
<th>النوع</th>
<th>الكمية</th>
<th>الحالة</th>
<th>الموقع</th>
<th>التكلفة</th>
<th>تاريخ الإضافة</th>
<th>إدارة</th>
</tr>

{rows if rows else "<tr><td colspan='9'>لا توجد معدات</td></tr>"}

</table>

<br>

<a href="/company/{company}" class="glass-btn back-btn">⬅ رجوع</a>

</div>
"""


@app.get("/new-equipment", response_class=HTMLResponse)
def new_equipment_form(company: str = ""):
    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>إضافة معدة جديدة</h2>

    <form action="/save-logistics-equipment" method="post" style="max-width:500px;margin:auto;">
        <input type="hidden" name="company" value="{company}">

        <label>اسم المعدة:</label>
        <input type="text" name="name" required style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>نوع المعدة:</label>
        <select name="type" required style="width:100%;padding:8px;margin-bottom:15px;">
            <option value="">اختر النوع</option>
            <option value="سيارة">سيارة</option>
            <option value="دراجة نارية">دراجة نارية</option>
            <option value="شاحنة">شاحنة</option>
            <option value="رافعة">رافعة</option>
            <option value="جرار">جرار</option>
            <option value="معدات يدوية">معدات يدوية</option>
            <option value="أخرى">أخرى</option>
        </select><br>

        <label>الكمية:</label>
        <input type="number" name="quantity" value="1" required style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>الحالة:</label>
        <select name="status" required style="width:100%;padding:8px;margin-bottom:15px;">
            <option value="">اختر الحالة</option>
            <option value="متاحة">متاحة</option>
            <option value="قيد الاستخدام">قيد الاستخدام</option>
            <option value="صيانة">صيانة</option>
            <option value="معطلة">معطلة</option>
            <option value="متقاعدة">متقاعدة</option>
        </select><br>

        <label>الموقع:</label>
        <input type="text" name="location" placeholder="مركز اللوجستيات، المستودع، إلخ" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>تاريخ الشراء:</label>
        <input type="date" name="purchase_date" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>التكلفة (ريال):</label>
        <input type="number" step="0.01" name="cost" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <button type="submit" class="glass-btn gold-text">حفظ</button>
    </form>

    <br>
    <a href="/equipment?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/save-logistics-equipment", response_class=HTMLResponse)
def save_logistics_equipment(
    company: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    quantity: int = Form(...),
    status: str = Form(...),
    location: str = Form(""),
    purchase_date: str = Form(""),
    cost: float = Form(0)
):
    conn = get_db()
    conn.execute(
        """INSERT INTO logistics_equipment 
        (company, name, type, quantity, status, location, purchase_date, cost, date_added)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, DATE('now'))""",
        (company, name, type, quantity, status, location, purchase_date, cost)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/equipment?company={company}",
        status_code=303
    )


@app.get("/equipment/{equipment_id}", response_class=HTMLResponse)
def equipment_detail(equipment_id: int, company: str = ""):
    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM logistics_equipment WHERE id = ? AND company = ?",
        (equipment_id, company)
    ).fetchone()
    conn.close()

    if not equipment:
        return "<h2>المعدة غير موجودة</h2>"

    status_color = {
        "متاحة": "#28a745",
        "قيد الاستخدام": "#ffc107",
        "صيانة": "#fd7e14",
        "معطلة": "#dc3545",
        "متقاعدة": "#6c757d"
    }
    color = status_color.get(equipment['status'], "#000")

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard" style="background:white;padding:40px;border-radius:8px;">

<h1>{equipment['name']}</h1>

<div style="background:white;padding:20px;border:1px solid #ddd;border-radius:4px;margin-bottom:20px;">

<table style="width:100%;text-align:right;">
<tr>
<td><strong>رقم المعدة:</strong></td>
<td>{equipment['id']}</td>
</tr>
<tr>
<td><strong>الاسم:</strong></td>
<td>{equipment['name']}</td>
</tr>
<tr>
<td><strong>النوع:</strong></td>
<td>{equipment['type']}</td>
</tr>
<tr>
<td><strong>الكمية:</strong></td>
<td>{equipment['quantity']}</td>
</tr>
<tr>
<td><strong>الحالة:</strong></td>
<td style="color:white;background-color:{color};padding:5px;border-radius:3px;display:inline-block;">{equipment['status']}</td>
</tr>
<tr>
<td><strong>الموقع:</strong></td>
<td>{equipment['location']}</td>
</tr>
<tr>
<td><strong>تاريخ الشراء:</strong></td>
<td>{equipment['purchase_date']}</td>
</tr>
<tr>
<td><strong>التكلفة:</strong></td>
<td>{equipment['cost']} ريال</td>
</tr>
<tr>
<td><strong>تاريخ الإضافة:</strong></td>
<td>{equipment['date_added']}</td>
</tr>
</table>

</div>

<div>
<a href="/edit-logistics-equipment/{equipment_id}?company={company}" class="action-btn">تعديل</a>
<a href="/delete-logistics-equipment/{equipment_id}?company={company}" onclick="return confirm('هل تريد حذف هذه المعدة؟')" class="action-btn">حذف</a>
<a href="/equipment?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>

</div>
"""


@app.get("/edit-logistics-equipment/{equipment_id}", response_class=HTMLResponse)
def edit_logistics_equipment_form(equipment_id: int, company: str = ""):
    conn = get_db()
    equipment = conn.execute(
        "SELECT * FROM logistics_equipment WHERE id = ? AND company = ?",
        (equipment_id, company)
    ).fetchone()
    conn.close()

    if not equipment:
        return "<h2>المعدة غير موجودة</h2>"

    return f"""
<meta charset="UTF-8">
<link rel="stylesheet" href="/static/style.css">
<body class="system-dark">
{HOME_BUTTON}
<div class="dashboard">
    <h2>تعديل معدة</h2>

    <form action="/update-logistics-equipment" method="post" style="max-width:500px;margin:auto;">
        <input type="hidden" name="equipment_id" value="{equipment_id}">
        <input type="hidden" name="company" value="{company}">

        <label>اسم المعدة:</label>
        <input type="text" name="name" value="{equipment['name']}" required style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>نوع المعدة:</label>
        <select name="type" required style="width:100%;padding:8px;margin-bottom:15px;">
            <option value="سيارة" {'selected' if equipment['type'] == 'سيارة' else ''}>سيارة</option>
            <option value="دراجة نارية" {'selected' if equipment['type'] == 'دراجة نارية' else ''}>دراجة نارية</option>
            <option value="شاحنة" {'selected' if equipment['type'] == 'شاحنة' else ''}>شاحنة</option>
            <option value="رافعة" {'selected' if equipment['type'] == 'رافعة' else ''}>رافعة</option>
            <option value="جرار" {'selected' if equipment['type'] == 'جرار' else ''}>جرار</option>
            <option value="معدات يدوية" {'selected' if equipment['type'] == 'معدات يدوية' else ''}>معدات يدوية</option>
            <option value="أخرى" {'selected' if equipment['type'] == 'أخرى' else ''}>أخرى</option>
        </select><br>

        <label>الكمية:</label>
        <input type="number" name="quantity" value="{equipment['quantity']}" required style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>الحالة:</label>
        <select name="status" required style="width:100%;padding:8px;margin-bottom:15px;">
            <option value="متاحة" {'selected' if equipment['status'] == 'متاحة' else ''}>متاحة</option>
            <option value="قيد الاستخدام" {'selected' if equipment['status'] == 'قيد الاستخدام' else ''}>قيد الاستخدام</option>
            <option value="صيانة" {'selected' if equipment['status'] == 'صيانة' else ''}>صيانة</option>
            <option value="معطلة" {'selected' if equipment['status'] == 'معطلة' else ''}>معطلة</option>
            <option value="متقاعدة" {'selected' if equipment['status'] == 'متقاعدة' else ''}>متقاعدة</option>
        </select><br>

        <label>الموقع:</label>
        <input type="text" name="location" value="{equipment['location'] or ''}" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>تاريخ الشراء:</label>
        <input type="date" name="purchase_date" value="{equipment['purchase_date'] or ''}" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <label>التكلفة (ريال):</label>
        <input type="number" step="0.01" name="cost" value="{equipment['cost']}" style="width:100%;padding:8px;margin-bottom:15px;"><br>

        <button type="submit" class="glass-btn gold-text">حفظ التعديلات</button>
    </form>

    <br>
    <a href="/equipment?company={company}" class="glass-btn back-btn">⬅ رجوع</a>
</div>
"""


@app.post("/update-logistics-equipment")
def update_logistics_equipment(
    equipment_id: int = Form(...),
    company: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    quantity: int = Form(...),
    status: str = Form(...),
    location: str = Form(""),
    purchase_date: str = Form(""),
    cost: float = Form(0)
):
    conn = get_db()
    conn.execute(
        """UPDATE logistics_equipment 
        SET name=?, type=?, quantity=?, status=?, location=?, purchase_date=?, cost=?
        WHERE id=? AND company=?""",
        (name, type, quantity, status, location, purchase_date, cost, equipment_id, company)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/equipment/{equipment_id}?company={company}",
        status_code=303
    )


@app.get("/delete-logistics-equipment/{equipment_id}")
def delete_logistics_equipment(equipment_id: int, company: str = ""):
    conn = get_db()
    conn.execute(
        "DELETE FROM logistics_equipment WHERE id = ? AND company = ?",
        (equipment_id, company)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(
        url=f"/equipment?company={company}",
        status_code=303
    )

