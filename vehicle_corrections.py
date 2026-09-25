"""Vehicle corrections: explicit ownership, transactional audit, additive migrations."""
import json
import re
import sqlite3
from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import get_current_user, is_admin
from db import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")
RECORD_LABELS = {"odometer": "قراءة العداد", "oil": "تغيير الزيت", "incident": "الحادث / الواقعة", "maintenance": "الصيانة", "vehicle": "بيانات السيارة", "assignment": "التسليم والاسترجاع", "employee_login": "ربط حساب الموظف"}


def migrate(conn):
    additions = {
        "vehicles": {"oil_interval": "INTEGER NOT NULL DEFAULT 5000"},
        "employees": {"user_id": "INTEGER REFERENCES users(id)"},
        "vehicle_odometer_logs": {"notes": "TEXT", "source_kind": "TEXT", "source_id": "INTEGER"},
        "vehicle_oil_changes": {"notes": "TEXT", "next_change_odometer": "INTEGER", "next_change_date": "TEXT"},
    }
    new_vehicle_interval = "oil_interval" not in {r["name"] for r in conn.execute("PRAGMA table_info(vehicles)")}
    new_source_links = "source_kind" not in {r["name"] for r in conn.execute("PRAGMA table_info(vehicle_odometer_logs)")}
    for table, columns in additions.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_employee_login ON employees(user_id) WHERE user_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS vehicle_maintenance (
            id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
            service_date TEXT NOT NULL, odometer INTEGER NOT NULL CHECK(odometer>=0),
            service_type TEXT NOT NULL, notes TEXT, recorded_at TEXT NOT NULL,
            recorded_by INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS vehicle_edit_audit (
            id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL, record_type TEXT NOT NULL,
            record_id INTEGER NOT NULL, edited_by INTEGER NOT NULL, edited_at TEXT NOT NULL,
            old_values TEXT NOT NULL, new_values TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_vehicle_edit_audit ON vehicle_edit_audit(vehicle_id,id);
    """)
    if new_vehicle_interval:
        conn.execute("""UPDATE vehicles SET oil_interval=COALESCE(
            (SELECT oil_interval FROM vehicle_oil_changes WHERE vehicle_id=vehicles.id ORDER BY change_date DESC,id DESC LIMIT 1),5000)""")
    if not new_source_links:
        return
    # Old oil updates created a reading at the exact same timestamp. Link only
    # unambiguous matches; never infer record ownership from names or readings.
    conn.execute("""UPDATE vehicle_odometer_logs AS l SET source_kind='oil',
        source_id=(SELECT o.id FROM vehicle_oil_changes o WHERE o.vehicle_id=l.vehicle_id
          AND o.recorded_at=l.recorded_at AND o.odometer=l.reading AND o.recorded_by=l.recorded_by)
        WHERE source_kind IS NULL AND
        (SELECT COUNT(*) FROM vehicle_oil_changes o WHERE o.vehicle_id=l.vehicle_id
          AND o.recorded_at=l.recorded_at AND o.odometer=l.reading AND o.recorded_by=l.recorded_by)=1
        AND (SELECT COUNT(*) FROM vehicle_odometer_logs x WHERE x.vehicle_id=l.vehicle_id
          AND x.recorded_at=l.recorded_at AND x.reading=l.reading AND x.recorded_by=l.recorded_by)=1""")
    for source, reading, timestamp in (("delivery", "delivery_odometer", "delivered_at"), ("return", "return_odometer", "returned_at")):
        # Legacy handover events have no creation timestamp. Only an exclusive
        # reading/date match on both sides can be safely associated.
        conn.execute(f"""UPDATE vehicle_odometer_logs AS l SET source_kind=?,
            source_id=(SELECT a.id FROM vehicle_assignments a WHERE a.vehicle_id=l.vehicle_id
              AND a.{reading}=l.reading AND substr(a.{timestamp},1,10)=substr(l.recorded_at,1,10))
            WHERE l.source_kind IS NULL AND
              (SELECT COUNT(*) FROM vehicle_assignments a WHERE a.vehicle_id=l.vehicle_id
                AND a.{reading}=l.reading AND substr(a.{timestamp},1,10)=substr(l.recorded_at,1,10))=1
              AND (SELECT COUNT(*) FROM vehicle_odometer_logs x WHERE x.vehicle_id=l.vehicle_id
                AND x.reading=l.reading AND substr(x.recorded_at,1,10)=substr(l.recorded_at,1,10))=1""", (source,))


def owns_vehicle(conn, user, vehicle_id):
    return bool(user and user["role"] == "employee" and conn.execute("""
        SELECT 1 FROM vehicle_assignments a JOIN employees e ON e.id=a.employee_id
        WHERE a.vehicle_id=? AND a.status='assigned' AND e.user_id=?
    """, (vehicle_id, user["id"])).fetchone())


def has_vehicle_custody(user):
    if not user or user["role"] != "employee":
        return False
    conn = get_db()
    try:
        return bool(conn.execute("SELECT 1 FROM employees e JOIN vehicle_assignments a ON a.employee_id=e.id WHERE e.user_id=? AND a.status='assigned'", (user["id"],)).fetchone())
    finally:
        conn.close()


def vehicle_guard(request, vehicle_id, *, view=False):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=303)
    if is_admin(user):
        return user, None
    with get_db() as conn:
        allowed = owns_vehicle(conn, user, vehicle_id)
        if view:
            allowed = allowed or bool(conn.execute("SELECT 1 FROM user_company_access WHERE user_id=? AND section='asset_custody_manager' AND company IN ('works','all')", (user["id"],)).fetchone())
    conn.close()
    if allowed:
        return user, None
    return None, HTMLResponse("<h2 dir='rtl'>لا يمكنك تعديل أو استخدام سيارة غير مسلّمة لك</h2>", status_code=403)


# Explicit field allowlists: submitted IDs, authors, assignments and roles are never writable.
FIELDS = {
    "odometer": ("vehicle_odometer_logs", [("reading", "قراءة العداد", "number"), ("recorded_at", "تاريخ ووقت القراءة", "datetime-local"), ("notes", "ملاحظات التحديث", "textarea")]),
    "oil": ("vehicle_oil_changes", [("change_date", "تاريخ تغيير الزيت", "date"), ("odometer", "كيلومترات تغيير الزيت", "number"), ("oil_interval", "فترة تغيير الزيت (5000 أو 10000)", "number"), ("next_change_odometer", "قراءة تغيير الزيت القادم (فارغ للحساب التلقائي)", "number"), ("next_change_date", "تاريخ تغيير الزيت القادم", "date"), ("notes", "ملاحظات الزيت", "textarea")]),
    "incident": ("vehicle_incidents", [("incident_date", "تاريخ الواقعة", "date"), ("odometer", "قراءة العداد", "number"), ("notes", "وصف الواقعة والملاحظات", "textarea")]),
    "maintenance": ("vehicle_maintenance", [("service_date", "تاريخ الصيانة", "date"), ("odometer", "قراءة العداد", "number"), ("service_type", "نوع الصيانة", "text"), ("notes", "ملاحظات الصيانة", "textarea")]),
    "vehicle": ("vehicles", [("vehicle_type", "نوع السيارة", "text"), ("make", "الماركة", "text"), ("model", "الموديل", "text"), ("manufacture_year", "سنة الصنع", "number"), ("color", "اللون", "text"), ("plate_number", "رقم اللوحة", "text"), ("vin", "رقم الهيكل", "text"), ("oil_interval", "فترة الزيت الافتراضية للدورات الجديدة (5000 أو 10000)", "number"), ("notes", "ملاحظات السيارة", "textarea")]),
    "assignment": ("vehicle_assignments", [("delivered_at", "تاريخ التسليم", "date"), ("delivery_odometer", "عداد التسليم", "number"), ("delivery_notes", "ملاحظات التسليم", "textarea"), ("returned_at", "تاريخ الاسترجاع", "date"), ("return_odometer", "عداد الاسترجاع", "number"), ("return_notes", "ملاحظات الاسترجاع", "textarea"), ("accessories", "الملحقات", "text"), ("notes", "ملاحظات العهدة", "textarea")]),
}


def editable_fields(kind, user):
    fields = FIELDS[kind][1]
    if kind == "oil" and not is_admin(user):
        fields = [f for f in fields if f[0] != "oil_interval"]
    return fields


def can_edit(conn, user, vehicle_id, kind, row):
    if is_admin(user):
        return True
    if kind == "odometer" and row["source_kind"] in ("delivery", "return"):
        return False
    return (kind not in ("vehicle", "assignment") and owns_vehicle(conn, user, vehicle_id)
            and row["recorded_by"] == user["id"])


def audit_update(conn, vehicle_id, kind, table, old, changes, user):
    changed = {k: v for k, v in changes.items() if old[k] != v}
    if not changed:
        return
    conn.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in changed) + " WHERE id=?", (*changed.values(), old["id"]))
    conn.execute("INSERT INTO vehicle_edit_audit(vehicle_id,record_type,record_id,edited_by,edited_at,old_values,new_values) VALUES(?,?,?,?,?,?,?)",
                 (vehicle_id, kind, old["id"], user["id"], datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                  json.dumps({k: old[k] for k in changed}, ensure_ascii=False), json.dumps(changed, ensure_ascii=False)))


def validate(kind, values, fields, original):
    result = {}
    optional = {"manufacture_year", "next_change_odometer", "next_change_date", "returned_at", "return_odometer"}
    for name, label, field_type in fields:
        value = values.get(name, "").strip()
        if field_type in ("number", "date", "datetime-local"):
            if not value and name in optional:
                result[name] = None
                continue
            if field_type == "number":
                if not re.fullmatch(r"[0-9]{1,9}", value):
                    raise ValueError(f"{label}: أدخل عددًا صحيحًا غير سالب لا يزيد عن 9 أرقام")
                result[name] = int(value)
            else:
                try:
                    parsed = datetime.fromisoformat(value) if field_type == "datetime-local" else date.fromisoformat(value)
                    if field_type == "datetime-local" and parsed.tzinfo is not None:
                        raise ValueError()
                    if name != "next_change_date" and (parsed.date() if isinstance(parsed, datetime) else parsed) > date.today():
                        raise ValueError()
                    result[name] = parsed.isoformat()
                except ValueError:
                    raise ValueError(f"{label}: أدخل تاريخًا صحيحًا، ولا تستخدم تاريخًا مستقبليًا للسجلات")
        else:
            if len(value) > 5000:
                raise ValueError(f"{label}: النص طويل جدًا")
            result[name] = value or (None if name == "vin" else "")
    full = dict(original) | result
    for name in {"vehicle": ["vehicle_type", "make", "model", "plate_number"], "maintenance": ["service_type"], "incident": ["notes"]}.get(kind, []):
        if not full.get(name):
            raise ValueError("يرجى تعبئة الحقول الأساسية")
    if kind == "vehicle" and full.get("manufacture_year") is not None and not 1900 <= full["manufacture_year"] <= date.today().year + 1:
        raise ValueError("سنة الصنع غير صحيحة")
    if kind == "vehicle" and full["oil_interval"] not in (5000, 10000):
        raise ValueError("فترة الزيت يجب أن تكون 5000 أو 10000 كم")
    if kind == "oil":
        if full["oil_interval"] not in (5000, 10000):
            raise ValueError("فترة الزيت يجب أن تكون 5000 أو 10000 كم")
        if full.get("next_change_odometer") is not None and full["next_change_odometer"] <= full["odometer"]:
            raise ValueError("قراءة الزيت القادمة يجب أن تكون أكبر من قراءة التغيير")
        if full.get("next_change_date") and full["next_change_date"] < full["change_date"]:
            raise ValueError("موعد الزيت القادم لا يمكن أن يسبق التغيير")
    if kind == "assignment":
        if original["status"] == "returned" and (not full["returned_at"] or full["return_odometer"] is None):
            raise ValueError("بيانات الاسترجاع مطلوبة للعهدة المسترجعة")
        if original["status"] == "assigned" and (full["returned_at"] or full["return_odometer"] is not None):
            raise ValueError("استخدم إجراء استرجاع السيارة لإنهاء العهدة")
        if full["returned_at"] and full["returned_at"] < full["delivered_at"]:
            raise ValueError("تاريخ الاسترجاع لا يمكن أن يسبق التسليم")
        if full["return_odometer"] is not None and full["return_odometer"] < full["delivery_odometer"]:
            raise ValueError("عداد الاسترجاع لا يمكن أن يقل عن عداد التسليم")
    return result


def reconcile_readings(conn, vehicle_id, kind, old, changes, user):
    if kind == "oil":
        for log in conn.execute("SELECT * FROM vehicle_odometer_logs WHERE vehicle_id=? AND source_kind='oil' AND source_id=?", (vehicle_id, old["id"])).fetchall():
            audit_update(conn, vehicle_id, "odometer", "vehicle_odometer_logs", log, {"reading": changes["odometer"]}, user)
    elif kind == "assignment":
        for source, field in (("delivery", "delivery_odometer"), ("return", "return_odometer")):
            for log in conn.execute("SELECT * FROM vehicle_odometer_logs WHERE vehicle_id=? AND source_kind=? AND source_id=?", (vehicle_id, source, old["id"])).fetchall():
                audit_update(conn, vehicle_id, "odometer", "vehicle_odometer_logs", log, {"reading": changes[field]}, user)
    elif kind == "odometer" and old["source_kind"] in ("delivery", "return"):
        assignment = conn.execute("SELECT * FROM vehicle_assignments WHERE id=? AND vehicle_id=?", (old["source_id"], vehicle_id)).fetchone()
        if assignment:
            field = "delivery_odometer" if old["source_kind"] == "delivery" else "return_odometer"
            updated = dict(assignment) | {field: changes["reading"]}
            if updated["return_odometer"] is not None and updated["return_odometer"] < updated["delivery_odometer"]:
                raise ValueError("قراءة الاسترجاع لا يمكن أن تقل عن قراءة التسليم؛ صحّح العهدة أولًا")
            audit_update(conn, vehicle_id, "assignment", "vehicle_assignments", assignment, {field: changes["reading"]}, user)
    elif kind == "odometer" and old["source_kind"] == "oil":
        oil = conn.execute("SELECT * FROM vehicle_oil_changes WHERE id=? AND vehicle_id=?", (old["source_id"], vehicle_id)).fetchone()
        if oil:
            if not can_edit(conn, user, vehicle_id, "oil", oil):
                raise ValueError("القراءة مرتبطة بسجل زيت لم تدخله بنفسك")
            if oil["next_change_odometer"] is not None and oil["next_change_odometer"] <= changes["reading"]:
                raise ValueError("صحّح موعد الزيت القادم من سجل الزيت أولًا")
            audit_update(conn, vehicle_id, "oil", "vehicle_oil_changes", oil, {"odometer": changes["reading"]}, user)
    if kind in ("odometer", "oil", "assignment"):
        latest = conn.execute("SELECT reading FROM vehicle_odometer_logs WHERE vehicle_id=? ORDER BY recorded_at DESC,id DESC LIMIT 1", (vehicle_id,)).fetchone()
        if latest:
            vehicle = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
            audit_update(conn, vehicle_id, "vehicle", "vehicles", vehicle, {"current_odometer": latest["reading"]}, user)


def edit_page(request, vehicle_id, kind, record_id, values, fields, error="", status=200):
    return templates.TemplateResponse(request=request, name="vehicle_record_edit.html", context={
        "vehicle_id": vehicle_id, "kind": kind, "record_id": record_id, "values": values,
        "fields": fields, "error": error, "record_label": RECORD_LABELS[kind]}, status_code=status)


@router.api_route("/vehicle/{vehicle_id}/records/{kind}/{record_id}/edit", methods=["GET", "POST"], response_class=HTMLResponse)
async def edit_record(request: Request, vehicle_id: int, kind: str, record_id: int):
    user, denied = vehicle_guard(request, vehicle_id)
    if denied:
        return denied
    if kind not in FIELDS:
        return HTMLResponse("السجل غير موجود", status_code=404)
    table = FIELDS[kind][0]
    conn = get_db()
    try:
        # Read authorization and original values within the same write transaction.
        if request.method == "POST":
            values = dict(await request.form())
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?" + (" AND vehicle_id=?" if kind != "vehicle" else " AND id=?"), (record_id, vehicle_id)).fetchone()
        if not row:
            return HTMLResponse("السجل غير موجود", status_code=404)
        if not can_edit(conn, user, vehicle_id, kind, row):
            return HTMLResponse("لا تملك صلاحية تعديل هذا السجل", status_code=403)
        fields = editable_fields(kind, user)
        if request.method == "GET":
            return edit_page(request, vehicle_id, kind, record_id, dict(row), fields)
        if kind == "oil" and not is_admin(user) and "oil_interval" in values and values["oil_interval"] != str(row["oil_interval"]):
            return edit_page(request, vehicle_id, kind, record_id, values, fields, "تعديل فترة الزيت متاح للأدمن فقط", 403)
        try:
            changes = validate(kind, values, fields, row)
            audit_update(conn, vehicle_id, kind, table, row, changes, user)
            reconcile_readings(conn, vehicle_id, kind, row, changes, user)
            conn.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            error = str(exc) if isinstance(exc, ValueError) else "رقم اللوحة أو الهيكل مستخدم مسبقًا؛ يرجى تصحيحه"
            return edit_page(request, vehicle_id, kind, record_id, values, fields, error, 400)
        return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+حفظ+التعديل+في+السجل+نفسه+بنجاح", status_code=303)
    finally:
        conn.close()


@router.api_route("/vehicle/{vehicle_id}/maintenance/new", methods=["GET", "POST"])
async def new_maintenance(request: Request, vehicle_id: int):
    user, denied = vehicle_guard(request, vehicle_id)
    if denied:
        return denied
    fields = FIELDS["maintenance"][1]
    values = dict(await request.form()) if request.method == "POST" else {"service_date": date.today().isoformat()}
    if request.method == "POST":
        try:
            changes = validate("maintenance", values, fields, {})
        except ValueError as exc:
            return edit_page(request, vehicle_id, "maintenance", 0, values, fields, str(exc), 400)
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not is_admin(user) and not owns_vehicle(conn, user, vehicle_id):
                return HTMLResponse("السيارة غير مسلّمة لك", status_code=403)
            if not conn.execute("SELECT 1 FROM vehicles WHERE id=?", (vehicle_id,)).fetchone():
                return HTMLResponse("السيارة غير موجودة", status_code=404)
            conn.execute("INSERT INTO vehicle_maintenance(vehicle_id,service_date,odometer,service_type,notes,recorded_at,recorded_by) VALUES(?,?,?,?,?,?,?)", (vehicle_id, changes["service_date"], changes["odometer"], changes["service_type"], changes["notes"], datetime.now().isoformat(timespec="seconds"), user["id"]))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+حفظ+سجل+الصيانة+بنجاح", status_code=303)
    return edit_page(request, vehicle_id, "maintenance", 0, values, fields)


@router.api_route("/vehicle/{vehicle_id}/employee-login", methods=["GET", "POST"])
async def employee_login(request: Request, vehicle_id: int):
    user, denied = vehicle_guard(request, vehicle_id)
    if denied:
        return denied
    if not is_admin(user):
        return HTMLResponse("ربط حساب الموظف متاح للأدمن فقط", status_code=403)
    conn = get_db()
    try:
        values = dict(await request.form()) if request.method == "POST" else {}
        if request.method == "POST":
            conn.execute("BEGIN IMMEDIATE")
        employee = conn.execute("SELECT e.* FROM employees e JOIN vehicle_assignments a ON a.employee_id=e.id WHERE a.vehicle_id=? AND a.status='assigned'", (vehicle_id,)).fetchone()
        if not employee:
            return HTMLResponse("سلّم السيارة لموظف أولًا عبر نظام العهد الحالي", status_code=400)
        users = conn.execute("SELECT id,full_name,username FROM users WHERE role='employee' AND is_active=1 ORDER BY full_name,id").fetchall()
        error = ""
        if request.method == "POST":
            try:
                uid = int(values.get("user_id", "0")) or None
                if uid and uid not in {u["id"] for u in users}:
                    raise ValueError()
                audit_update(conn, vehicle_id, "employee_login", "employees", employee, {"user_id": uid}, user)
                conn.commit()
                return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+حفظ+ربط+حساب+الموظف", status_code=303)
            except (ValueError, sqlite3.IntegrityError):
                conn.rollback()
                error = "اختر حساب موظف نشط غير مرتبط بموظف آخر"
        return templates.TemplateResponse(request=request, name="vehicle_employee_login.html", context={"employee": employee, "users": users, "selected": values.get("user_id", str(employee["user_id"] or "")), "vehicle_id": vehicle_id, "error": error}, status_code=400 if error else 200)
    finally:
        conn.close()


def detail_context(conn, user, vehicle_id):
    admin = is_admin(user)
    own = owns_vehicle(conn, user, vehicle_id)
    editable = {}
    for kind, (table, _) in FIELDS.items():
        rows = conn.execute(f"SELECT * FROM {table} WHERE " + ("id=?" if kind == "vehicle" else "vehicle_id=?"), (vehicle_id,)).fetchall()
        editable[kind] = {r["id"] for r in rows if can_edit(conn, user, vehicle_id, kind, r)}
    audits = []
    if admin:
        for row in conn.execute("SELECT a.*,u.full_name,u.username FROM vehicle_edit_audit a LEFT JOIN users u ON u.id=a.edited_by WHERE vehicle_id=? ORDER BY a.id DESC", (vehicle_id,)):
            item = dict(row)
            item["old_values"] = json.loads(item["old_values"])
            item["new_values"] = json.loads(item["new_values"])
            audits.append(item)
    return {"editable": editable, "is_vehicle_admin": admin, "can_operate": admin or own, "can_manage_custody": admin,
            "audits": audits, "record_labels": RECORD_LABELS,
            "field_labels": {kind: {f[0]: f[1] for f in fields} for kind, (_, fields) in FIELDS.items()} | {"employee_login": {"user_id": "حساب الموظف"}},
            "maintenance": conn.execute("SELECT * FROM vehicle_maintenance WHERE vehicle_id=? ORDER BY service_date DESC,id DESC", (vehicle_id,)).fetchall()}
