# -*- coding: utf-8 -*-
"""Isolated client-facing project portal for Urban Rise AI.

The module only reads legacy operational tables. Portal-specific state is kept in
new tables and additive, nullable/defaulted columns so existing workflows remain
unchanged.
"""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import os
import logging
import sqlite3
import re
import uuid

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import get_current_user, is_admin
from db import get_db

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    generate_password_hash = None


TEMPLATES = Jinja2Templates(directory="templates")
UPLOAD_DIR = Path("static/uploads/client_portal")
PHASES = [
    "التعاقد", "الأعمال التحضيرية", "الهدم أو الإزالة", "التأسيس",
    "الكهرباء", "السباكة", "اللياسة", "العزل", "الجبس", "الدهان",
    "الأرضيات", "التشطيبات النهائية", "التسليم",
]
PHASE_STATUSES = {"not_started", "in_progress", "completed", "awaiting_client", "paused"}
REQUEST_STATUSES = {"new", "reviewing", "assigned", "awaiting_client", "approved", "implemented", "rejected", "closed"}
REQUEST_TYPES = {"change", "inquiry", "note", "maintenance", "materials"}
logger = logging.getLogger("urbanrise.client_requests")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _column(conn, table, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    except sqlite3.OperationalError:
        pass


def init_client_portal_schema():
    conn = get_db()
    statements = ["""CREATE TABLE IF NOT EXISTS client_project_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        portal_enabled INTEGER NOT NULL DEFAULT 0,
        portal_published INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, project_id)
    )""", """CREATE TABLE IF NOT EXISTS client_portal_settings (
        project_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        published INTEGER NOT NULL DEFAULT 0,
        prepared INTEGER NOT NULL DEFAULT 0,
        page_title TEXT DEFAULT 'متابعة مشروعي',
        progress_override REAL,
        current_phase TEXT,
        client_note TEXT,
        announcement TEXT,
        project_manager_name TEXT,
        project_manager_whatsapp TEXT,
        expected_delivery TEXT,
        updated_at TEXT
    )""", """CREATE TABLE IF NOT EXISTS client_project_phases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'not_started',
        sort_order INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT,
        UNIQUE(project_id, name)
    )""", """CREATE TABLE IF NOT EXISTS client_payment_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        amount REAL NOT NULL DEFAULT 0,
        due_reason TEXT,
        phase_id INTEGER,
        status TEXT NOT NULL DEFAULT 'not_due',
        paid_at TEXT,
        show_to_client INTEGER NOT NULL DEFAULT 1,
        created_at TEXT
    )""", """CREATE TABLE IF NOT EXISTS client_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        document_type TEXT NOT NULL DEFAULT 'other',
        file_path TEXT NOT NULL,
        show_to_client INTEGER NOT NULL DEFAULT 0,
        created_at TEXT
    )""", """CREATE TABLE IF NOT EXISTS client_change_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        attachment_path TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        admin_note TEXT,
        created_at TEXT,
        updated_at TEXT
    )""", """CREATE TABLE IF NOT EXISTS client_change_request_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT,
        uploaded_at TEXT,
        uploaded_by INTEGER
    )""", """CREATE TABLE IF NOT EXISTS client_portal_item_controls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        item_type TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        visible_to_client INTEGER NOT NULL DEFAULT 1,
        client_summary TEXT,
        display_order INTEGER NOT NULL DEFAULT 0,
        admin_override INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT,
        UNIQUE(project_id,item_type,item_id)
    )"""]
    for statement in statements:
        conn.execute(statement)
    for definition in (
        "show_to_client INTEGER NOT NULL DEFAULT 0",
        "client_summary TEXT",
        "client_phase TEXT",
        "client_progress REAL",
        "internal_notes TEXT",
        "client_image_visible INTEGER NOT NULL DEFAULT 0",
    ):
        _column(conn, "project_daily", definition)
    _column(conn, "contracts", "show_to_client INTEGER NOT NULL DEFAULT 0")
    for definition in (
        "show_to_client INTEGER NOT NULL DEFAULT 0",
        "client_notes TEXT",
    ):
        _column(conn, "client_material_receipts", definition)
    _column(conn, "client_material_receipt_images", "show_to_client INTEGER NOT NULL DEFAULT 0")
    _column(conn, "client_project_access", "portal_enabled INTEGER NOT NULL DEFAULT 0")
    _column(conn, "client_project_access", "portal_published INTEGER NOT NULL DEFAULT 0")
    _column(conn, "client_portal_settings", "published INTEGER NOT NULL DEFAULT 0")
    _column(conn, "projects", "duration_days INTEGER")
    _column(conn, "contracts", "duration_days INTEGER")
    _column(conn, "contract_appendices", "appendix_extra_days INTEGER NOT NULL DEFAULT 0")
    for definition in (
        "client_user_id INTEGER", "request_type TEXT NOT NULL DEFAULT 'change'",
        "admin_reply TEXT", "internal_note TEXT", "assigned_to INTEGER", "replied_at TEXT",
        "hidden_from_today_after TEXT", "converted_daily_task_id INTEGER",
        "is_read INTEGER NOT NULL DEFAULT 0", "read_at TEXT",
    ):
        _column(conn, "client_change_requests", definition)
    conn.execute("UPDATE client_change_requests SET client_user_id=user_id WHERE client_user_id IS NULL")
    conn.execute("""INSERT INTO client_change_request_attachments(request_id,file_path,file_type,uploaded_at,uploaded_by)
                    SELECT request.id,request.attachment_path,'legacy',request.created_at,request.user_id
                    FROM client_change_requests request
                    WHERE COALESCE(request.attachment_path,'')<>'' AND NOT EXISTS
                    (SELECT 1 FROM client_change_request_attachments attachment
                     WHERE attachment.request_id=request.id AND attachment.file_path=request.attachment_path)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_requests_today ON client_change_requests(created_at,status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_request_attachments ON client_change_request_attachments(request_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_access_user ON client_project_access(user_id, project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_daily ON project_daily(project_id, show_to_client)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_portal_controls ON client_portal_item_controls(project_id,item_type,item_id)")
    conn.commit()
    conn.close()


def _save_upload(upload, folder="requests"):
    if not upload or not getattr(upload, "filename", ""):
        return ""
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".pdf"}:
        return ""
    target_dir = UPLOAD_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as output:
        while chunk := upload.file.read(1024 * 1024):
            output.write(chunk)
    return "/" + target.as_posix()


def _request_attachments(conn, request_ids):
    if not request_ids:
        return {}
    placeholders = ",".join("?" * len(request_ids))
    rows = conn.execute(
        f"SELECT * FROM client_change_request_attachments WHERE request_id IN ({placeholders}) ORDER BY id",
        list(request_ids),
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["request_id"], []).append(row)
    return result


def _request_integrity_before(conn, request_id, action):
    row = conn.execute("SELECT id,client_user_id,user_id,assigned_to,status FROM client_change_requests WHERE id=?",
                       (request_id,)).fetchone()
    if not row:
        return None
    client_user_id = row["client_user_id"] or row["user_id"]
    account = conn.execute("SELECT id,username,password,role,is_active FROM users WHERE id=?", (client_user_id,)).fetchone()
    snapshot = {"client_user_id": client_user_id, "account": tuple(account) if account else None}
    logger.info("client_request_before action=%s request_id=%s client_user_id=%s assigned_to=%s status=%s",
                action, request_id, client_user_id, row["assigned_to"], row["status"])
    return snapshot


def _request_integrity_after(conn, request_id, action, before):
    if not before:
        return
    row = conn.execute("SELECT client_user_id,user_id,assigned_to,status FROM client_change_requests WHERE id=?",
                       (request_id,)).fetchone()
    client_user_id = (row["client_user_id"] or row["user_id"]) if row else None
    account = conn.execute("SELECT id,username,password,role,is_active FROM users WHERE id=?", (client_user_id,)).fetchone() if client_user_id else None
    if client_user_id != before["client_user_id"] or (tuple(account) if account else None) != before["account"]:
        logger.error("client_request_integrity_violation action=%s request_id=%s before_client_user_id=%s after_client_user_id=%s",
                     action, request_id, before["client_user_id"], client_user_id)
        raise RuntimeError("Client account integrity changed during request update")
    logger.info("client_request_after action=%s request_id=%s client_user_id=%s assigned_to=%s status=%s",
                action, request_id, client_user_id, row["assigned_to"], row["status"])


def _admin(request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    return user if is_admin(user) else None


def _client_project_ids(conn, user_id):
    return [r["project_id"] for r in conn.execute(
        "SELECT project_id FROM client_project_access WHERE user_id=? ORDER BY project_id", (user_id,)
    ).fetchall()]


def _phase_progress(phases):
    if not phases:
        return None
    completed = sum(1 for phase in phases if phase["status"] == "completed")
    return round(completed * 100 / len(phases))


def _duration_days(value):
    """Convert legacy quote duration text (days/weeks/months) to calendar days."""
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if "أسبوعين" in text or "اسبوعين" in text:
        return 14
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return 0
    amount = float(match.group())
    if any(unit in text for unit in ("أسبوع", "اسبوع", "أسابيع", "اسابيع", "week")):
        amount *= 7
    elif any(unit in text for unit in ("شهر", "أشهر", "اشهر", "month")):
        amount *= 30
    return max(0, round(amount))


def _as_date(value):
    try:
        return datetime.fromisoformat(str(value).strip()[:10]).date()
    except (TypeError, ValueError):
        return None


def _progress_details(project, contract, quote, appendices, phases, today=None):
    original = 0
    source = ""
    if contract and "duration_days" in contract.keys():
        original = _duration_days(contract["duration_days"])
        source = "العقد" if original else ""
    if not original and quote:
        original = _duration_days(quote["duration"])
        source = "العقد" if contract else "عرض السعر"
    if not original and "duration_days" in project.keys():
        original = _duration_days(project["duration_days"])
        source = "المشروع" if original else ""
    start = _as_date(project["start_date"])
    if not original and start:
        end = _as_date(project["end_date"])
        if end and end >= start:
            original = (end - start).days
            source = "المشروع"
    approved = {"ساري", "معتمد", "مدفوع", "نشط", "active", "approved", "paid"}
    extra = sum(max(0, int(a["appendix_extra_days"] or 0)) for a in appendices
                if (a["status"] or "").strip().lower() in {s.lower() for s in approved})
    total = original + extra
    elapsed = max(0, ((today or date.today()) - start).days) if start else 0
    timeline = min(100, round(elapsed * 100 / total)) if start and total > 0 else None
    phase = _phase_progress(phases)
    candidates = [value for value in (timeline, phase) if value is not None]
    overall = min(candidates) if candidates else 0
    expected = start + timedelta(days=total) if start and total > 0 else None
    return dict(progress=overall, timeline_progress=timeline, phase_progress=phase,
                duration_days=total, original_duration_days=original, appendix_extra_days=extra,
                elapsed_days=min(elapsed, total) if total else elapsed,
                remaining_days=max(0, total - elapsed) if total else None,
                expected_delivery=expected.isoformat() if expected else "", duration_source=source,
                progress_label="التقدم العام" if phase is not None else "التقدم حسب الجدول الزمني")


def _ensure_settings(conn, project_id):
    conn.execute(
        "INSERT OR IGNORE INTO client_portal_settings(project_id, updated_at) VALUES (?, ?)",
        (project_id, _now()),
    )


def _controls(conn, project_id):
    rows = conn.execute("SELECT * FROM client_portal_item_controls WHERE project_id=?", (project_id,)).fetchall()
    return {(r["item_type"], int(r["item_id"])): r for r in rows}


def _is_visible(controls, item_type, item_id, default=True):
    row = controls.get((item_type, int(item_id)))
    return bool(row["visible_to_client"]) if row else default


def _payment_status(amount, paid_before, paid_total):
    if paid_total >= paid_before + amount - 0.01:
        return "paid"
    if paid_total > paid_before:
        return "due"
    return "not_due"


def _project_context(conn, project_id, client_user_id=None):
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        return None
    _ensure_settings(conn, project_id)
    settings = conn.execute("SELECT * FROM client_portal_settings WHERE project_id=?", (project_id,)).fetchone()
    controls = _controls(conn, project_id)
    phases = conn.execute("SELECT * FROM client_project_phases WHERE project_id=? ORDER BY sort_order,id", (project_id,)).fetchall()
    # A phase-linked installment becomes due as soon as that phase is completed.
    conn.execute("""UPDATE client_payment_schedule SET status='due'
                    WHERE project_id=? AND status='not_due' AND phase_id IN
                    (SELECT id FROM client_project_phases WHERE project_id=? AND status='completed')""", (project_id, project_id))
    conn.commit()
    daily_source = conn.execute("SELECT * FROM project_daily WHERE project_id=? ORDER BY date DESC,id DESC", (project_id,)).fetchall()
    daily = []
    for row in daily_source:
        if not _is_visible(controls, "daily", row["id"], True):
            continue
        item = dict(row)
        override = controls.get(("daily", int(row["id"])))
        item["client_summary"] = (override["client_summary"] if override and override["client_summary"] else item.get("client_summary")) or ""
        item["client_image_visible"] = bool(item.get("attachment_path") and _is_visible(controls, "daily_image", row["id"], True))
        daily.append(item)
        if len(daily) == 3:
            break
    payments = []
    documents = []
    if client_user_id:
        requests = conn.execute("""SELECT * FROM client_change_requests WHERE project_id=?
                                  AND COALESCE(client_user_id,user_id)=? ORDER BY id DESC""",
                                (project_id, client_user_id)).fetchall()
    else:
        requests = conn.execute("SELECT * FROM client_change_requests WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
    request_attachments = _request_attachments(conn, [row["id"] for row in requests])
    receipts = conn.execute("SELECT * FROM client_material_receipts WHERE project_id=? ORDER BY receipt_date DESC,id DESC", (project_id,)).fetchall()
    receipt_data = []
    for receipt in receipts:
        if not _is_visible(controls, "material_receipt", receipt["id"], True):
            continue
        items = conn.execute("SELECT * FROM client_material_receipt_items WHERE receipt_id=? ORDER BY id", (receipt["id"],)).fetchall()
        images = [image for image in conn.execute("SELECT * FROM client_material_receipt_images WHERE receipt_id=? ORDER BY id", (receipt["id"],)).fetchall() if _is_visible(controls, "material_image", image["id"], True)]
        receipt_data.append((receipt, items, images))
    collections = conn.execute("SELECT COALESCE(SUM(amount),0) total FROM project_collections WHERE project_id=? AND COALESCE(collection_status,'') NOT IN ('ملغي','ملغاة')", (project_id,)).fetchone()["total"] or 0
    contract_value = float(project["contract_value"] or 0)
    if not contract_value and project["contract_id"]:
        row = conn.execute("""SELECT COALESCE(SUM(qi.qty*qi.unit_price),0) total FROM contracts c JOIN quote_items qi ON qi.quote_id=c.quote_id WHERE c.id=?""", (project["contract_id"],)).fetchone()
        contract_value = float(row["total"] or 0)
    contract = conn.execute("SELECT * FROM contracts WHERE id=?", (project["contract_id"],)).fetchone() if project["contract_id"] else None
    quote = conn.execute("SELECT * FROM quotes WHERE id=?", (contract["quote_id"],)).fetchone() if contract and contract["quote_id"] else None
    approved_contract_statuses = {"ساري", "معتمد", "نشط", "active", "approved"}
    contract_visible = False
    if contract:
        contract_approved = (contract["status"] or "").strip().lower() in {s.lower() for s in approved_contract_statuses}
        contract_visible = _is_visible(controls, "contract", contract["id"], contract_approved)
    if contract and contract_visible:
        documents.append({"id": contract["id"], "title": f"العقد رقم {contract['id']}", "document_type": "contract", "file_path": f"/client-portal/contract/{contract['id']}", "created_at": contract["status"] or "معتمد"})
        for attachment in conn.execute("SELECT * FROM contract_attachments WHERE contract_id=? ORDER BY id DESC", (contract["id"],)).fetchall():
            if _is_visible(controls, "contract_attachment", attachment["id"], True):
                documents.append({"id": attachment["id"], "title": attachment["file_name"] or "مرفق العقد", "document_type": attachment["source_type"] or "contract", "file_path": f"/client-portal/attachment/{attachment['id']}", "created_at": attachment["uploaded_at"] or ""})
    appendices = conn.execute("SELECT * FROM contract_appendices WHERE project_id=? OR parent_contract_id=? ORDER BY id DESC", (project_id, project["contract_id"] or 0)).fetchall()
    approved_appendix_statuses = {"معتمد", "مدفوع", "approved", "paid"}
    for appendix in appendices:
        appendix_approved = (appendix["status"] or "").strip().lower() in {s.lower() for s in approved_appendix_statuses}
        if _is_visible(controls, "appendix", appendix["id"], appendix_approved):
            title = appendix["short_description"] or f"ملحق عقد رقم {appendix['id']}"
            documents.append({"id": appendix["id"], "title": title, "document_type": "appendix", "file_path": f"/client-portal/appendix/{appendix['id']}", "created_at": f"{appendix['status'] or ''} · {float(appendix['total'] or 0):,.0f} ر.س"})
    has_source_payments = bool(contract and contract["quote_id"])
    if has_source_payments:
        quote_payments = conn.execute("SELECT * FROM quote_payments WHERE quote_id=? ORDER BY id", (contract["quote_id"],)).fetchall()
        paid_cursor = 0.0
        for payment in quote_payments:
            amount = contract_value * float(payment["percentage"] or 0) / 100
            title = payment["title"] or "دفعة تعاقدية"
            if _is_visible(controls, "payment", payment["id"], True):
                payments.append({"id": payment["id"], "title": title, "amount": amount, "due_reason": title, "status": _payment_status(amount, paid_cursor, float(collections))})
            paid_cursor += amount
    if not has_source_payments:
        for manual in conn.execute("SELECT * FROM client_payment_schedule WHERE project_id=? AND show_to_client=1 ORDER BY id", (project_id,)).fetchall():
            if _is_visible(controls, "portal_payment", manual["id"], True):
                payments.append(dict(manual))
    for manual_doc in conn.execute("SELECT * FROM client_documents WHERE project_id=? AND show_to_client=1 ORDER BY id DESC", (project_id,)).fetchall():
        if _is_visible(controls, "portal_document", manual_doc["id"], True):
            documents.append(dict(manual_doc))
    next_payment = next((p for p in payments if p["status"] not in {"paid"}), None)
    progress_details = _progress_details(project, contract, quote, appendices, phases)
    return dict(project=project, settings=settings, phases=phases, daily=daily, payments=payments,
                documents=documents, requests=requests, request_attachments=request_attachments, receipts=receipt_data, **progress_details,
                contract_value=contract_value, paid=float(collections), remaining=max(0, contract_value-float(collections)),
                next_payment=next_payment)


def register_client_portal(app):
    init_client_portal_schema()

    @app.get("/client-portal", response_class=HTMLResponse)
    def client_portal_home(request: Request, project_id: int = 0):
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        is_admin_preview = bool(user and is_admin(user) and project_id)
        if not user or (user["role"] != "client" and not is_admin_preview):
            return RedirectResponse("/login", status_code=303)
        conn = get_db()
        ids = [project_id] if is_admin_preview else _client_project_ids(conn, user["id"])
        if not ids:
            conn.close()
            return TEMPLATES.TemplateResponse(request, "client_portal.html", {"request": request, "empty": True, "user": user})
        selected = project_id if project_id in ids else ids[0]
        context = _project_context(conn, selected, None if is_admin_preview else user["id"])
        available = conn.execute(f"SELECT id,name FROM projects WHERE id IN ({','.join('?'*len(ids))}) ORDER BY id", ids).fetchall()
        access = None if is_admin_preview else conn.execute("SELECT * FROM client_project_access WHERE user_id=? AND project_id=?", (user["id"], selected)).fetchone()
        if not context:
            conn.close()
            return TEMPLATES.TemplateResponse(request, "client_portal.html", {"request": request, "empty": True, "user": user})
        context.update(request=request, user=user, available_projects=available, empty=False,
                       portal_available=bool((is_admin_preview or (access and access["portal_enabled"] and access["portal_published"]))
                                             and context["settings"]["enabled"] and context["settings"]["published"]))
        conn.close()
        return TEMPLATES.TemplateResponse(request, "client_portal.html", context)

    @app.post("/client-portal/change-request")
    def create_change_request(request: Request, project_id: int = Form(...), title: str = Form(...),
                              description: str = Form(...), request_type: str = Form("change"),
                              attachments: list[UploadFile] = File([]), attachment: UploadFile = File(None)):
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        if not user or user["role"] != "client":
            return RedirectResponse("/login", status_code=303)
        conn = get_db()
        allowed = conn.execute("SELECT 1 FROM client_project_access WHERE user_id=? AND project_id=?", (user["id"], project_id)).fetchone()
        if allowed and title.strip() and description.strip():
            now_value = _now()
            uploads = list(attachments or [])
            if attachment and getattr(attachment, "filename", ""):
                uploads.append(attachment)
            saved = [_save_upload(item) for item in uploads]
            saved = [path for path in saved if path]
            request_id = conn.execute("""INSERT INTO client_change_requests
                (project_id,user_id,client_user_id,title,description,attachment_path,request_type,status,created_at,updated_at,hidden_from_today_after)
                VALUES(?,?,?,?,?,?,?,'new',?,?,datetime(?,'+7 days'))""",
                (project_id, user["id"], user["id"], title.strip()[:160], description.strip(),
                 saved[0] if saved else "", request_type if request_type in REQUEST_TYPES else "change",
                 now_value, now_value, now_value)).lastrowid
            for path in saved:
                conn.execute("""INSERT INTO client_change_request_attachments
                    (request_id,file_path,file_type,uploaded_at,uploaded_by) VALUES(?,?,?,?,?)""",
                    (request_id, path, Path(path).suffix.lower().lstrip("."), now_value, user["id"]))
            conn.commit()
        conn.close()
        return RedirectResponse(f"/client-portal?project_id={project_id}&request_sent=1#requests", status_code=303)

    @app.get("/client-portal/change-request")
    def change_request_page(request: Request, project_id: int = 0):
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        if not user or user["role"] != "client":
            return RedirectResponse("/login", 303)
        conn = get_db(); ids = _client_project_ids(conn, user["id"]); conn.close()
        if not ids:
            return RedirectResponse("/client-portal", 303)
        selected = project_id if project_id in ids else ids[0]
        return RedirectResponse(f"/client-portal?project_id={selected}#requests", 303)

    @app.get("/client-portal/request-attachment/{attachment_id}")
    def request_attachment(request: Request, attachment_id: int):
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        conn = get_db()
        row = conn.execute("""SELECT a.*,r.project_id,r.client_user_id,r.user_id FROM client_change_request_attachments a
                              JOIN client_change_requests r ON r.id=a.request_id WHERE a.id=?""", (attachment_id,)).fetchone()
        allowed = bool(row and user and (is_admin(user) or (user["role"] == "client" and
                       user["id"] in {row["client_user_id"], row["user_id"]})))
        conn.close()
        if not allowed: return HTMLResponse("غير مصرح", 403)
        path = (row["file_path"] or "").lstrip("/")
        return FileResponse(path) if Path(path).exists() else HTMLResponse("الملف غير موجود", 404)

    def can_view_project_document(request, conn, project_id):
        user = getattr(request.state, "current_user", None) or get_current_user(request)
        if not user: return False
        if is_admin(user): return True
        return bool(user["role"] == "client" and conn.execute("SELECT 1 FROM client_project_access WHERE user_id=? AND project_id=?", (user["id"], project_id)).fetchone())

    @app.get("/client-portal/attachment/{attachment_id}")
    def client_attachment(request: Request, attachment_id: int):
        conn=get_db(); row=conn.execute("""SELECT a.*,p.id project_id FROM contract_attachments a JOIN contracts c ON c.id=a.contract_id LEFT JOIN projects p ON p.contract_id=c.id OR p.id=c.manual_project_id WHERE a.id=?""",(attachment_id,)).fetchone()
        allowed=bool(row and row["project_id"] and can_view_project_document(request,conn,row["project_id"])); conn.close()
        if not allowed: return HTMLResponse("غير مصرح",403)
        path=(row["file_path"] or "").lstrip("/")
        return FileResponse(path,filename=row["file_name"] or Path(path).name) if Path(path).exists() else HTMLResponse("الملف غير موجود",404)

    @app.get("/client-portal/contract/{contract_id}")
    def client_contract_pdf(request: Request, contract_id: int):
        conn=get_db(); contract=conn.execute("SELECT * FROM contracts WHERE id=?",(contract_id,)).fetchone()
        project=conn.execute("SELECT * FROM projects WHERE contract_id=? OR id=? LIMIT 1",(contract_id,contract["manual_project_id"] if contract else 0)).fetchone() if contract else None
        if not contract or not project or not can_view_project_document(request,conn,project["id"]): conn.close(); return HTMLResponse("غير مصرح",403)
        quote=conn.execute("SELECT * FROM quotes WHERE id=?",(contract["quote_id"],)).fetchone(); items=conn.execute("SELECT * FROM quote_items WHERE quote_id=?",(contract["quote_id"],)).fetchall(); payments=conn.execute("SELECT * FROM quote_payments WHERE quote_id=?",(contract["quote_id"],)).fetchall(); conn.close()
        from main import build_contract_report_pdf
        path,name=build_contract_report_pdf(contract,quote,items,payments,contract["company"] or "",project)
        return FileResponse(path,filename=name,media_type="application/pdf")

    @app.get("/client-portal/appendix/{appendix_id}")
    def client_appendix_pdf(request: Request, appendix_id: int):
        conn=get_db(); appendix=conn.execute("SELECT * FROM contract_appendices WHERE id=?",(appendix_id,)).fetchone(); project=conn.execute("SELECT * FROM projects WHERE id=?",(appendix["project_id"],)).fetchone() if appendix else None; parent=conn.execute("SELECT * FROM contracts WHERE id=?",(appendix["parent_contract_id"],)).fetchone() if appendix else None; items=conn.execute("SELECT * FROM contract_appendix_items WHERE appendix_id=?",(appendix_id,)).fetchall()
        if not appendix or not project or not can_view_project_document(request,conn,project["id"]): conn.close(); return HTMLResponse("غير مصرح",403)
        conn.close(); from main import build_contract_appendix_pdf
        path,name=build_contract_appendix_pdf(appendix,parent,project,items)
        return FileResponse(path,filename=name,media_type="application/pdf")

    @app.get("/admin/project/{project_id}/client-portal", response_class=HTMLResponse)
    def admin_client_portal(request: Request, project_id: int):
        if not _admin(request):
            return HTMLResponse("غير مصرح", status_code=403)
        conn = get_db(); context = _project_context(conn, project_id)
        if not context:
            conn.close(); return HTMLResponse("المشروع غير موجود", status_code=404)
        all_daily = conn.execute("SELECT * FROM project_daily WHERE project_id=? ORDER BY date DESC,id DESC", (project_id,)).fetchall()
        all_docs = conn.execute("SELECT * FROM client_documents WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        source_contract = conn.execute("SELECT * FROM contracts WHERE id=?", (context["project"]["contract_id"],)).fetchone() if context["project"]["contract_id"] else None
        source_appendices = conn.execute("SELECT * FROM contract_appendices WHERE project_id=? OR parent_contract_id=? ORDER BY id DESC", (project_id, context["project"]["contract_id"] or 0)).fetchall()
        source_receipts = conn.execute("SELECT * FROM client_material_receipts WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        source_payments = conn.execute("SELECT * FROM quote_payments WHERE quote_id=? ORDER BY id", (source_contract["quote_id"],)).fetchall() if source_contract and source_contract["quote_id"] else []
        controls = _controls(conn, project_id)
        clients = conn.execute("""SELECT u.*,a.portal_enabled,a.portal_published FROM users u JOIN client_project_access a ON a.user_id=u.id WHERE a.project_id=? ORDER BY u.id""", (project_id,)).fetchall()
        created_credentials = getattr(request.state, "client_created_credentials", None)
        context.update(request=request, all_daily=all_daily, all_documents=all_docs, clients=clients,
                       login_url=str(request.base_url).rstrip('/') + '/login', source_contract=source_contract,
                       source_appendices=source_appendices, source_receipts=source_receipts,
                       source_payments=source_payments, controls=controls, created_credentials=created_credentials)
        conn.close()
        return TEMPLATES.TemplateResponse(request, "client_portal_admin.html", context)

    @app.post("/admin/project/{project_id}/client-portal/settings")
    def save_settings(request: Request, project_id: int, page_title: str = Form("متابعة مشروعي"), progress_override: str = Form(""), current_phase: str = Form(""), client_note: str = Form(""), announcement: str = Form(""), manager_name: str = Form(""), manager_whatsapp: str = Form(""), expected_delivery: str = Form(""), enabled: str = Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح", status_code=403)
        try: progress = max(0, min(100, float(progress_override))) if progress_override.strip() else None
        except ValueError: progress = None
        conn=get_db(); _ensure_settings(conn,project_id)
        conn.execute("""UPDATE client_portal_settings SET enabled=?,page_title=?,progress_override=?,current_phase=?,client_note=?,announcement=?,project_manager_name=?,project_manager_whatsapp=?,expected_delivery=?,updated_at=? WHERE project_id=?""", (1 if enabled else 0,page_title.strip() or "متابعة مشروعي",progress,current_phase.strip(),client_note.strip(),announcement.strip(),manager_name.strip(),manager_whatsapp.strip(),expected_delivery.strip(),_now(),project_id))
        conn.execute("UPDATE client_project_access SET portal_enabled=? WHERE project_id=?", (1 if enabled else 0, project_id))
        conn.commit(); conn.close(); return RedirectResponse(f"/admin/project/{project_id}/client-portal",303)

    @app.post("/admin/project/{project_id}/client-portal/prepare")
    def prepare_portal(request: Request, project_id: int):
        if not _admin(request): return HTMLResponse("غير مصرح", status_code=403)
        conn=get_db(); _ensure_settings(conn,project_id)
        for order,name in enumerate(PHASES):
            conn.execute("INSERT OR IGNORE INTO client_project_phases(project_id,name,status,sort_order,updated_at) VALUES(?,?,'not_started',?,?)",(project_id,name,order,_now()))
        conn.execute("UPDATE client_portal_settings SET prepared=1,updated_at=? WHERE project_id=?",(_now(),project_id)); conn.commit(); conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal",303)

    @app.post("/admin/project/{project_id}/client-portal/daily/{daily_id}")
    def update_daily_visibility(request: Request, project_id: int, daily_id: int, show: str=Form(""), image_show: str=Form(""), summary: str=Form(""), phase: str=Form(""), progress: str=Form(""), internal_notes: str=Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        try: value=float(progress) if progress.strip() else None
        except ValueError: value=None
        conn=get_db(); conn.execute("UPDATE project_daily SET show_to_client=?,client_image_visible=?,client_summary=?,client_phase=?,client_progress=?,internal_notes=? WHERE id=? AND project_id=?",(1 if show else 0,1 if image_show else 0,summary.strip(),phase.strip(),value,internal_notes.strip(),daily_id,project_id))
        for item_type, visible in (("daily", bool(show)), ("daily_image", bool(image_show))):
            conn.execute("""INSERT INTO client_portal_item_controls(project_id,item_type,item_id,visible_to_client,client_summary,admin_override,updated_at)
                            VALUES(?,?,?,?,?,1,?) ON CONFLICT(project_id,item_type,item_id) DO UPDATE SET
                            visible_to_client=excluded.visible_to_client,client_summary=excluded.client_summary,admin_override=1,updated_at=excluded.updated_at""",
                         (project_id,item_type,daily_id,1 if visible else 0,summary.strip() if item_type == "daily" else "",_now()))
        conn.commit();conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal#daily",303)

    @app.post("/admin/project/{project_id}/client-portal/item")
    def update_item_control(request: Request, project_id: int, item_type: str = Form(...), item_id: int = Form(...), visible: str = Form(""), client_summary: str = Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح", 403)
        allowed_types = {"daily", "daily_image", "contract", "contract_attachment", "appendix", "payment", "material_receipt", "material_image", "portal_payment", "portal_document"}
        if item_type not in allowed_types: return HTMLResponse("نوع غير صالح", 400)
        conn = get_db()
        conn.execute("""INSERT INTO client_portal_item_controls(project_id,item_type,item_id,visible_to_client,client_summary,admin_override,updated_at)
                        VALUES(?,?,?,?,?,1,?) ON CONFLICT(project_id,item_type,item_id) DO UPDATE SET
                        visible_to_client=excluded.visible_to_client,client_summary=excluded.client_summary,admin_override=1,updated_at=excluded.updated_at""",
                     (project_id,item_type,item_id,1 if visible else 0,client_summary.strip(),_now()))
        conn.commit(); conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal#review", 303)

    @app.post("/admin/project/{project_id}/client-portal/phase")
    def save_phase(request:Request,project_id:int,phase_id:int=Form(0),name:str=Form(...),status:str=Form("not_started"),sort_order:int=Form(0)):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        status=status if status in PHASE_STATUSES else "not_started"; conn=get_db()
        if phase_id: conn.execute("UPDATE client_project_phases SET name=?,status=?,sort_order=?,updated_at=? WHERE id=? AND project_id=?",(name.strip(),status,sort_order,_now(),phase_id,project_id))
        elif name.strip(): conn.execute("INSERT INTO client_project_phases(project_id,name,status,sort_order,updated_at) VALUES(?,?,?,?,?)",(project_id,name.strip(),status,sort_order,_now()))
        conn.commit();conn.close(); return RedirectResponse(f"/admin/project/{project_id}/client-portal#phases",303)

    @app.post("/admin/project/{project_id}/client-portal/payment")
    def save_payment(request:Request,project_id:int,title:str=Form(...),amount:float=Form(0),due_reason:str=Form(""),phase_id:int=Form(0),status:str=Form("not_due"),show:str=Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        conn=get_db();conn.execute("INSERT INTO client_payment_schedule(project_id,title,amount,due_reason,phase_id,status,show_to_client,created_at) VALUES(?,?,?,?,?,?,?,?)",(project_id,title.strip(),amount,due_reason.strip(),phase_id or None,status,1 if show else 0,_now()));conn.commit();conn.close();return RedirectResponse(f"/admin/project/{project_id}/client-portal#payments",303)

    @app.post("/admin/project/{project_id}/client-portal/document")
    def add_document(request:Request,project_id:int,title:str=Form(...),document_type:str=Form("other"),show:str=Form(""),document:UploadFile=File(...)):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        path=_save_upload(document,"documents")
        if path:
            conn=get_db();conn.execute("INSERT INTO client_documents(project_id,title,document_type,file_path,show_to_client,created_at) VALUES(?,?,?,?,?,?)",(project_id,title.strip(),document_type,path,1 if show else 0,_now()));conn.commit();conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal#documents",303)

    @app.post("/admin/project/{project_id}/client-portal/document/{document_id}")
    def toggle_document(request:Request,project_id:int,document_id:int,show:str=Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        conn=get_db();conn.execute("UPDATE client_documents SET show_to_client=? WHERE id=? AND project_id=?",(1 if show else 0,document_id,project_id));conn.commit();conn.close();return RedirectResponse(f"/admin/project/{project_id}/client-portal#documents",303)

    @app.post("/admin/project/{project_id}/client-portal/client")
    def add_client(request:Request,project_id:int,username:str=Form(...),password:str=Form(...),full_name:str=Form(""),enable_access:str=Form("")):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        username=username.strip(); password=(password or "").strip()
        if not username or not password: return HTMLResponse("اسم المستخدم وكلمة المرور مطلوبان",400)
        conn=get_db(); existing=conn.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()
        stored=generate_password_hash(password) if generate_password_hash else password
        if existing: user_id=existing["id"]; conn.execute("UPDATE users SET full_name=?,password=?,role='client',is_active=1 WHERE id=?",(full_name.strip(),stored,user_id))
        else: user_id=conn.execute("INSERT INTO users(username,password,full_name,role,is_active,created_at) VALUES(?,?,?,'client',1,?)",(username,stored,full_name.strip(),_now())).lastrowid
        active = 1 if enable_access else 0
        conn.execute("INSERT OR IGNORE INTO client_project_access(user_id,project_id,portal_enabled,portal_published) VALUES(?,?,?,?)",(user_id,project_id,active,active))
        conn.execute("UPDATE client_project_access SET portal_enabled=?,portal_published=? WHERE user_id=? AND project_id=?",(active,active,user_id,project_id))
        if active:
            _ensure_settings(conn,project_id)
            conn.execute("UPDATE client_portal_settings SET enabled=1,published=1,updated_at=? WHERE project_id=?",(_now(),project_id))
        conn.commit();conn.close()
        request.state.client_created_credentials={"username":username,"password":password,"login_url":"/login"}
        return admin_client_portal(request,project_id)

    @app.post("/admin/project/{project_id}/client-portal/status")
    def update_portal_status(request: Request, project_id: int, action: str = Form(...)):
        if not _admin(request): return HTMLResponse("غير مصرح", 403)
        conn = get_db(); _ensure_settings(conn, project_id)
        if action == "publish":
            conn.execute("UPDATE client_portal_settings SET enabled=1,published=1,updated_at=? WHERE project_id=?", (_now(), project_id))
            conn.execute("UPDATE client_project_access SET portal_enabled=1,portal_published=1 WHERE project_id=?", (project_id,))
        elif action == "enable":
            conn.execute("UPDATE client_portal_settings SET enabled=1,updated_at=? WHERE project_id=?", (_now(), project_id))
            conn.execute("UPDATE client_project_access SET portal_enabled=1 WHERE project_id=?", (project_id,))
        else:
            conn.execute("UPDATE client_portal_settings SET enabled=0,published=0,updated_at=? WHERE project_id=?", (_now(), project_id))
            conn.execute("UPDATE client_project_access SET portal_enabled=0,portal_published=0 WHERE project_id=?", (project_id,))
        conn.commit(); conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal", 303)

    @app.post("/admin/project/{project_id}/client-portal/request/{request_id}")
    def update_request(request:Request,project_id:int,request_id:int,status:str=Form("reviewing"),
                       admin_reply:str=Form(""),internal_note:str=Form(""),assigned_to:int=Form(0),
                       reply_attachment:UploadFile=File(None)):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        status=status if status in REQUEST_STATUSES else "reviewing"; now_value=_now(); conn=get_db()
        integrity=_request_integrity_before(conn,request_id,f"admin_update:{status}")
        conn.execute("""UPDATE client_change_requests SET status=?,admin_reply=?,admin_note=?,internal_note=?,assigned_to=?,
                        replied_at=CASE WHEN ?<>'' THEN ? ELSE replied_at END,updated_at=? WHERE id=? AND project_id=?""",
                     (status,admin_reply.strip(),internal_note.strip(),internal_note.strip(),assigned_to or None,
                      admin_reply.strip(),now_value,now_value,request_id,project_id))
        path=_save_upload(reply_attachment,"request_replies")
        if path:
            admin=_admin(request)
            conn.execute("INSERT INTO client_change_request_attachments(request_id,file_path,file_type,uploaded_at,uploaded_by) VALUES(?,?,?,?,?)",
                         (request_id,path,Path(path).suffix.lower().lstrip('.'),now_value,admin["id"]))
        _request_integrity_after(conn,request_id,f"admin_update:{status}",integrity)
        conn.commit();conn.close();return RedirectResponse(f"/admin/project/{project_id}/client-portal#requests",303)

    @app.post("/admin/client-request/{request_id}/convert-task")
    def convert_request_to_task(request: Request, request_id: int):
        admin = _admin(request)
        if not admin: return HTMLResponse("غير مصرح",403)
        conn=get_db(); row=conn.execute("SELECT * FROM client_change_requests WHERE id=?",(request_id,)).fetchone()
        if not row: conn.close(); return HTMLResponse("الطلب غير موجود",404)
        integrity=_request_integrity_before(conn,request_id,"convert_to_daily_task")
        task_text=f"طلب العميل: {row['title']} — {row['description'][:220]}"; task_key=f"client-request-{request_id}"
        now_value=_now(); today=date.today().isoformat()
        conn.execute("""INSERT INTO daily_report_tasks(project_id,task_date,task_text,task_key,is_active,created_by,created_at,updated_at)
                        VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(project_id,task_date,task_key)
                        DO UPDATE SET task_text=excluded.task_text,is_active=1,updated_at=excluded.updated_at""",
                     (row["project_id"],today,task_text,task_key,admin["id"],now_value,now_value))
        task=conn.execute("SELECT id FROM daily_report_tasks WHERE project_id=? AND task_date=? AND task_key=?",
                          (row["project_id"],today,task_key)).fetchone()
        conn.execute("UPDATE client_change_requests SET status='assigned',converted_daily_task_id=?,updated_at=? WHERE id=?",
                     (task["id"],now_value,request_id));_request_integrity_after(conn,request_id,"convert_to_daily_task",integrity);conn.commit();conn.close()
        return RedirectResponse("/daily-activity-report",303)

    @app.post("/admin/client-request/{request_id}/assign")
    def assign_client_request(request: Request, request_id: int):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        conn=get_db(); row=conn.execute("SELECT id FROM client_change_requests WHERE id=?",(request_id,)).fetchone()
        if not row: conn.close(); return HTMLResponse("الطلب غير موجود",404)
        integrity=_request_integrity_before(conn,request_id,"assign_to_supervisor")
        conn.execute("UPDATE client_change_requests SET status='assigned',updated_at=? WHERE id=?",(_now(),request_id))
        _request_integrity_after(conn,request_id,"assign_to_supervisor",integrity);conn.commit();conn.close();return RedirectResponse("/daily-activity-report",303)

    def mark_request_read(conn, request_id):
        integrity=_request_integrity_before(conn,request_id,"mark_read")
        conn.execute("""UPDATE client_change_requests SET is_read=1,read_at=?,
                        status=CASE WHEN status IN ('new','جديد') THEN 'reviewing' ELSE status END,
                        updated_at=? WHERE id=?""", (_now(), _now(), request_id))
        _request_integrity_after(conn,request_id,"mark_read",integrity)

    @app.get("/admin/client-request/{request_id}/view")
    def view_client_request(request: Request, request_id: int):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        conn=get_db(); row=conn.execute("SELECT project_id FROM client_change_requests WHERE id=?",(request_id,)).fetchone()
        if not row: conn.close(); return HTMLResponse("الطلب غير موجود",404)
        mark_request_read(conn,request_id);conn.commit();project_id=row["project_id"];conn.close()
        return RedirectResponse(f"/admin/project/{project_id}/client-portal#requests",303)

    @app.post("/admin/client-request/{request_id}/mark-read")
    def read_client_request(request: Request, request_id: int):
        if not _admin(request): return HTMLResponse("غير مصرح",403)
        conn=get_db(); mark_request_read(conn,request_id);conn.commit();conn.close()
        return RedirectResponse("/daily-activity-report",303)
