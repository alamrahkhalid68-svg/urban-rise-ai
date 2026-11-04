"""One-off maintenance and cleaning work orders; additive SQLite schema."""
from datetime import datetime
from html import escape
import sqlite3

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

STATUSES = ("جديد", "يحتاج جدولة", "مجدول", "الفريق في الطريق", "جاري التنفيذ", "مكتمل بانتظار المراجعة", "مغلق", "ملغي")
NEXT = {
    "جديد": {"يحتاج جدولة", "مجدول", "ملغي"},
    "يحتاج جدولة": {"مجدول", "ملغي"},
    "مجدول": {"الفريق في الطريق", "ملغي"},
    "الفريق في الطريق": {"جاري التنفيذ", "ملغي"},
    "جاري التنفيذ": {"مكتمل بانتظار المراجعة", "ملغي"},
    "مكتمل بانتظار المراجعة": {"مغلق", "جاري التنفيذ"},
    "مغلق": set(), "ملغي": set(),
}
TEMPLATES = {
    "تنظيف فيلا أو شقة": [("تجهيز الموقع وحماية الأسطح", 1), ("تنظيف الغرف والمطابخ والحمامات", 1), ("فحص النتيجة مع العميل", 1)],
    "تنظيف بعد التشطيب": [("إزالة مخلفات التشطيب", 1), ("إزالة الغبار من الأسطح", 1), ("تنظيف الأرضيات والزجاج", 1)],
    "جلي وتلميع": [("فحص السطح قبل الجلي", 1), ("الجلي والتلميع", 1), ("تنظيف الموقع بعد العمل", 1)],
    "تنظيف أثاث وسجاد": [("فحص الأثاث والسجاد", 1), ("التنظيف والمعالجة", 1), ("التحقق من التجفيف والنتيجة", 1)],
    "تنظيف واجهات": [("تأمين منطقة العمل", 1), ("تنظيف الواجهات", 1), ("فحص الواجهة بعد التنظيف", 1)],
    "صيانة خفيفة": [("فحص العطل", 1), ("تنفيذ الإصلاح", 1), ("اختبار التشغيل", 1)],
}


def migrate(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL UNIQUE,
        service_type TEXT, location TEXT, contact_name TEXT, contact_phone TEXT,
        scheduled_at TEXT, expected_minutes INTEGER, team TEXT, expected_workers INTEGER,
        scope TEXT, internal_notes TEXT, status TEXT NOT NULL DEFAULT 'جديد',
        arrived_at TEXT, started_at TEXT, finished_at TEXT, pre_damage_notes TEXT,
        completion_notes TEXT, checklist_template TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_work_order_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL,
        title TEXT NOT NULL, required INTEGER NOT NULL DEFAULT 1,
        completed INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_work_order_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL,
        image_path TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('before','after')),
        created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_wo_schedule ON maintenance_cleaning_work_orders(scheduled_at,status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_wo_checks ON maintenance_cleaning_work_order_checks(work_order_id,position)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_wo_images ON maintenance_cleaning_work_order_images(work_order_id,phase)")
    conn.commit()


def template_for(service):
    value = (service or "").strip()
    for key, words in (("بعد التشطيب", "تنظيف بعد التشطيب"), ("جلي", "جلي وتلميع"), ("تلميع", "جلي وتلميع"), ("أثاث", "تنظيف أثاث وسجاد"), ("سجاد", "تنظيف أثاث وسجاد"), ("واجه", "تنظيف واجهات"), ("صيانة", "صيانة خفيفة")):
        if key in value:
            return words
    if any(word in value for word in ("فيلا", "شقة", "تنظيف", "نظافة")):
        return "تنظيف فيلا أو شقة"
    return ""


def create_from_quote(conn, project_id, quote, items):
    """Called in the same transaction as quote conversion; never backfills old projects."""
    if conn.execute("SELECT 1 FROM maintenance_cleaning_work_orders WHERE project_id=?", (project_id,)).fetchone():
        return
    service = quote["service_type"] or ""
    scope = "\n".join(str(item["description"] or "").strip() for item in items if item["description"])
    template = template_for(service + " " + scope)
    now = datetime.now().isoformat(timespec="minutes")
    cur = conn.execute("""INSERT INTO maintenance_cleaning_work_orders
        (project_id,service_type,location,contact_name,scope,status,checklist_template,created_at,updated_at)
        VALUES (?,?,?,?,?,'يحتاج جدولة',?,?,?)""",
        (project_id, service, quote["service_location"] or "", quote["client"] or "", scope, template, now, now))
    for pos, (title, required) in enumerate(TEMPLATES.get(template, [])):
        conn.execute("INSERT INTO maintenance_cleaning_work_order_checks(work_order_id,title,required,position) VALUES(?,?,?,?)", (cur.lastrowid,title,required,pos))


def transition(conn, order_id, target, *, supervisor=False):
    row = conn.execute("SELECT * FROM maintenance_cleaning_work_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise ValueError("أمر العمل غير موجود")
    current = row["status"]
    if target not in NEXT.get(current, set()):
        raise ValueError(f"لا يمكن نقل الحالة من «{current}» إلى «{target}»")
    if target == "مغلق":
        if not supervisor:
            raise PermissionError("اعتماد وإغلاق العمل للمشرف أو الأدمن فقط")
        missing = conn.execute("SELECT COUNT(*) FROM maintenance_cleaning_work_order_checks WHERE work_order_id=? AND required=1 AND completed=0", (order_id,)).fetchone()[0]
        total_checks = conn.execute("SELECT COUNT(*) FROM maintenance_cleaning_work_order_checks WHERE work_order_id=?", (order_id,)).fetchone()[0]
        photos = {r[0]:r[1] for r in conn.execute("SELECT phase,COUNT(*) FROM maintenance_cleaning_work_order_images WHERE work_order_id=? GROUP BY phase", (order_id,))}
        if not total_checks or missing or not photos.get("before") or not photos.get("after") or not (row["completion_notes"] or "").strip():
            raise ValueError("لا يمكن الإغلاق: أكمل عناصر الفحص الإلزامية وارفع صور قبل وبعد واكتب ملاحظات الإنجاز")
    if target in ("الفريق في الطريق", "جاري التنفيذ") and not row["scheduled_at"]:
        raise ValueError("حدد موعد العمل أولاً")
    if target == "مجدول" and (not row["scheduled_at"] or not (row["team"] or "").strip()):
        raise ValueError("حدد موعد العمل والفريق أولاً")
    if target == "جاري التنفيذ" and current == "الفريق في الطريق" and not row["arrived_at"]:
        raise ValueError("سجل الوصول إلى الموقع قبل بدء العمل")
    now = datetime.now().isoformat(timespec="seconds")
    field = {"جاري التنفيذ":"started_at", "مكتمل بانتظار المراجعة":"finished_at"}.get(target)
    if field and not row[field]:
        conn.execute(f"UPDATE maintenance_cleaning_work_orders SET {field}=? WHERE id=?", (now,order_id))
    conn.execute("UPDATE maintenance_cleaning_work_orders SET status=?,updated_at=? WHERE id=?", (target,now,order_id))


def _main():
    import main
    return main


def _access(request, write=False, supervisor=False):
    m = _main()
    user = m.get_company_page_access(request, "works")
    if not isinstance(user, sqlite3.Row):
        return user
    if write:
        guard = m.ensure_not_works_partner_write(user, "works")
        if not isinstance(guard, sqlite3.Row):
            return guard
    if supervisor and not (m.is_admin(user) or m.is_maintenance_cleaning_supervisor(user)):
        return HTMLResponse("<h2>اعتماد وإغلاق العمل للمشرف أو الأدمن فقط</h2>", status_code=403)
    return user


def _error(exc, code=400):
    return HTMLResponse(f"<h2>{escape(str(exc))}</h2>", status_code=code)


@router.get("/maintenance-cleaning/work-order/{order_id}", response_class=HTMLResponse)
def detail(request: Request, order_id: int):
    user = _access(request)
    if not isinstance(user, sqlite3.Row): return user
    m = _main(); conn = m.get_db()
    try:
        migrate(conn)
        row = conn.execute("""SELECT w.*,p.client_name FROM maintenance_cleaning_work_orders w
          JOIN maintenance_cleaning_projects p ON p.id=w.project_id WHERE w.id=?""", (order_id,)).fetchone()
        if not row: return _error("أمر العمل غير موجود",404)
        checks = conn.execute("SELECT * FROM maintenance_cleaning_work_order_checks WHERE work_order_id=? ORDER BY position,id",(order_id,)).fetchall()
        photos = conn.execute("SELECT * FROM maintenance_cleaning_work_order_images WHERE work_order_id=? ORDER BY id",(order_id,)).fetchall()
    finally: conn.close()
    h=lambda x: escape(str(x or ""))
    writable = not m.is_works_partner_user(user,"works")
    options="".join(f'<option value="{h(k)}" {"selected" if k==row["checklist_template"] else ""}>{h(k)}</option>' for k in TEMPLATES)
    check_html="".join(f'<form method="post" action="/maintenance-cleaning/work-order/{order_id}/check/{c["id"]}"><label class="mc-check"><input type="checkbox" name="completed" value="1" {"checked" if c["completed"] else ""}> مكتمل</label><label>عنصر الفحص<input name="title" value="{h(c["title"])}" required></label><label class="mc-check"><input type="checkbox" name="required" value="1" {"checked" if c["required"] else ""}> إلزامي</label><button>حفظ العنصر</button></form>' for c in checks)
    photo_html="".join(f'<figure class="mc-photo"><a href="{h(p["image_path"])}" target="_blank" rel="noopener"><img loading="lazy" src="{h(p["image_path"])}" alt="صورة {h("قبل" if p["phase"]=="before" else "بعد")} العمل"><figcaption>{h("قبل" if p["phase"]=="before" else "بعد")} #{p["id"]}</figcaption></a></figure>' for p in photos)
    actions="".join(f'<form method="post" action="/maintenance-cleaning/work-order/{order_id}/transition"><input type="hidden" name="target" value="{h(s)}"><button>{h({"الفريق في الطريق":"الفريق في الطريق","جاري التنفيذ":"بدء العمل","مكتمل بانتظار المراجعة":"إنهاء العمل","مغلق":"اعتماد وإغلاق"}.get(s,s))}</button></form>' for s in NEXT[row["status"]] if s not in ("مغلق",) or (m.is_admin(user) or m.is_maintenance_cleaning_supervisor(user))) if writable else ""
    form=f'''<form method="post" action="/maintenance-cleaning/work-order/{order_id}/schedule">
      <label>نوع الخدمة<input name="service_type" value="{h(row['service_type'])}"></label><label>الموقع<input name="location" value="{h(row['location'])}"></label>
      <label>مسؤول التواصل<input name="contact_name" value="{h(row['contact_name'])}"></label><label>رقم التواصل<input name="contact_phone" value="{h(row['contact_phone'])}"></label>
      <label>الموعد<input type="datetime-local" name="scheduled_at" value="{h(row['scheduled_at'])}"></label><label>المدة المتوقعة بالدقائق<input type="number" min="1" name="expected_minutes" value="{h(row['expected_minutes'])}"></label>
      <label>الفريق المسؤول<input name="team" value="{h(row['team'])}"></label><label>عدد العمال المتوقع<input type="number" min="0" name="expected_workers" value="{h(row['expected_workers'])}"></label>
      <label>نطاق العمل<textarea name="scope">{h(row['scope'])}</textarea></label><label>ملاحظات داخلية<textarea name="internal_notes">{h(row['internal_notes'])}</textarea></label><button>حفظ وجدولة</button></form>
      <form method="post" action="/maintenance-cleaning/work-order/{order_id}/template"><label>قالب الفحص<select name="template">{options}</select></label><button>نسخ قائمة الفحص</button></form>
      <form method="post" action="/maintenance-cleaning/work-order/{order_id}/check"><label>عنصر فحص جديد<input name="title" required></label><label class="mc-check"><input type="checkbox" name="required" value="1" checked>إلزامي</label><button>إضافة عنصر</button></form>
      <form method="post" action="/maintenance-cleaning/work-order/{order_id}/notes"><label>أضرار قبل التنفيذ<textarea name="pre_damage_notes">{h(row['pre_damage_notes'])}</textarea></label><label>ملاحظات الإنجاز<textarea name="completion_notes">{h(row['completion_notes'])}</textarea></label><button>حفظ الملاحظات</button></form>
      <form method="post" enctype="multipart/form-data" action="/maintenance-cleaning/work-order/{order_id}/images"><label>مرحلة الصور<select name="phase"><option value="before">صور قبل</option><option value="after">صور بعد</option></select></label><label>الصور<input type="file" name="images" accept="image/*" multiple required></label><button>رفع الصور</button></form>'''
    arrive=f'<form method="post" action="/maintenance-cleaning/work-order/{order_id}/arrive"><button>وصلنا للموقع</button></form>' if writable and row['status']=='الفريق في الطريق' and not row['arrived_at'] else ''
    finance_link=f'<a href="/maintenance-cleaning/work-order/{order_id}/finance">حسابات أمر العمل</a>' if m.is_admin(user) or m.is_maintenance_cleaning_accountant(user) else ''
    return HTMLResponse(f'''<html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/maintenance-cleaning.css"><body class="system-dark mc-page mc-order"><main class="dashboard"><nav class="mc-top-nav" aria-label="تنقل الصيانة والنظافة"><a href="/company/works" aria-label="رجوع إلى أقسام شركة المقاولات"><span aria-hidden="true">←</span> رجوع</a><a href="/dashboard" aria-label="العودة إلى الرئيسية"><span aria-hidden="true">🏠</span> الرئيسية</a></nav><h1>أمر عمل #{order_id}</h1><div class="mc-summary"><p>{h(row['client_name'])} · {h(row['service_type'])} · {h(row['location'])}</p><p>الحالة: <strong class="mc-status">{h(row['status'])}</strong></p><p>الوصول: {h(row['arrived_at'])} | البدء: {h(row['started_at'])} | الانتهاء: {h(row['finished_at'])}</p></div>{form if writable else ''}<h2>قائمة الفحص</h2><div class="mc-checks">{check_html if writable else ''.join(h(c['title']) for c in checks)}</div><h2>الصور</h2><div class="mc-photo-grid">{photo_html or '<p>لا توجد صور بعد</p>'}</div><div class="mc-actions">{arrive}{actions}{finance_link}<a href="/company/works/maintenance-cleaning">العودة للوحة</a></div></main></body></html>''')


@router.post("/maintenance-cleaning/work-order/{order_id}/schedule")
def schedule(request: Request, order_id: int, service_type: str=Form(""), location: str=Form(""), contact_name: str=Form(""), contact_phone: str=Form(""), scheduled_at: str=Form(""), expected_minutes: int=Form(0), team: str=Form(""), expected_workers: int=Form(0), scope: str=Form(""), internal_notes: str=Form("")):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    if not scheduled_at or not team.strip() or expected_minutes <= 0 or expected_workers < 0: return _error("حدد الموعد والفريق والمدة المتوقعة وعدد عمال صحيح")
    try: datetime.fromisoformat(scheduled_at)
    except ValueError: return _error("صيغة الموعد غير صحيحة")
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status,service_type FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row: return _error("أمر العمل غير موجود",404)
        if row['status'] not in ('جديد','يحتاج جدولة','مجدول'): return _error("لا يمكن إعادة جدولة العمل في حالته الحالية")
        conn.execute("""UPDATE maintenance_cleaning_work_orders SET service_type=?,location=?,contact_name=?,contact_phone=?,scheduled_at=?,expected_minutes=?,team=?,expected_workers=?,scope=?,internal_notes=?,status='مجدول',updated_at=? WHERE id=?""",
          (service_type.strip(),location.strip(),contact_name.strip(),contact_phone.strip(),scheduled_at,expected_minutes,team.strip(),expected_workers,scope.strip(),internal_notes.strip(),datetime.now().isoformat(timespec='minutes'),order_id))
        if service_type.strip() != (row['service_type'] or '').strip():
            template=template_for(service_type)
            conn.execute("DELETE FROM maintenance_cleaning_work_order_checks WHERE work_order_id=?",(order_id,))
            for pos,(title,required) in enumerate(TEMPLATES.get(template,[])):
                conn.execute("INSERT INTO maintenance_cleaning_work_order_checks(work_order_id,title,required,position) VALUES(?,?,?,?)",(order_id,title,required,pos))
            conn.execute("UPDATE maintenance_cleaning_work_orders SET checklist_template=? WHERE id=?",(template,order_id))
        conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/transition")
def change_status(request: Request, order_id: int, target: str=Form(...)):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn)
        transition(conn,order_id,target,supervisor=m.is_admin(user) or m.is_maintenance_cleaning_supervisor(user))
        conn.commit()
    except PermissionError as exc: return _error(exc,403)
    except ValueError as exc: return _error(exc)
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/arrive")
def arrive(request: Request, order_id: int):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row or row['status']!='الفريق في الطريق': return _error("يجب أن يكون الفريق في الطريق قبل تسجيل الوصول")
        conn.execute("UPDATE maintenance_cleaning_work_orders SET arrived_at=COALESCE(arrived_at,?),updated_at=? WHERE id=?",(datetime.now().isoformat(timespec='seconds'),datetime.now().isoformat(timespec='seconds'),order_id)); conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/notes")
def notes(request: Request,order_id:int,pre_damage_notes:str=Form(""),completion_notes:str=Form("")):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); conn.execute("UPDATE maintenance_cleaning_work_orders SET pre_damage_notes=?,completion_notes=? WHERE id=? AND status NOT IN ('مغلق','ملغي')",(pre_damage_notes.strip(),completion_notes.strip(),order_id)); conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/template")
def set_template(request:Request,order_id:int,template:str=Form(...)):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    if template not in TEMPLATES: return _error("قالب الفحص غير معروف")
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row or row['status'] not in ('جديد','يحتاج جدولة','مجدول'): return _error("لا يمكن تبديل القالب بعد بدء التنفيذ")
        conn.execute("DELETE FROM maintenance_cleaning_work_order_checks WHERE work_order_id=?",(order_id,))
        for pos,(title,required) in enumerate(TEMPLATES[template]): conn.execute("INSERT INTO maintenance_cleaning_work_order_checks(work_order_id,title,required,position) VALUES(?,?,?,?)",(order_id,title,required,pos))
        conn.execute("UPDATE maintenance_cleaning_work_orders SET checklist_template=? WHERE id=?",(template,order_id)); conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/check")
def add_check(request:Request,order_id:int,title:str=Form(...),required:str=Form("")):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row or row['status'] in ('مغلق','ملغي'): return _error("لا يمكن تعديل قائمة فحص عمل مغلق")
        pos=conn.execute("SELECT COUNT(*) FROM maintenance_cleaning_work_order_checks WHERE work_order_id=?",(order_id,)).fetchone()[0]
        conn.execute("INSERT INTO maintenance_cleaning_work_order_checks(work_order_id,title,required,position) VALUES(?,?,?,?)",(order_id,title.strip(),int(required=='1'),pos)); conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/check/{check_id}")
def mark_check(request:Request,order_id:int,check_id:int,completed:str=Form(""),title:str=Form(...),required:str=Form("")):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row or row['status'] in ('مغلق','ملغي'): return _error("لا يمكن تعديل قائمة فحص عمل مغلق")
        conn.execute("UPDATE maintenance_cleaning_work_order_checks SET completed=?,title=?,required=? WHERE id=? AND work_order_id=?",(int(completed=='1'),title.strip(),int(required=='1'),check_id,order_id)); conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)


@router.post("/maintenance-cleaning/work-order/{order_id}/images")
def upload_images(request:Request,order_id:int,phase:str=Form(...),images:list[UploadFile]=File(...)):
    user=_access(request,True)
    if not isinstance(user,sqlite3.Row): return user
    if phase not in ('before','after'): return _error("تصنيف الصورة غير صحيح")
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); row=conn.execute("SELECT status FROM maintenance_cleaning_work_orders WHERE id=?",(order_id,)).fetchone()
        if not row or row['status'] in ('مغلق','ملغي'): return _error("لا يمكن رفع صور لعمل مغلق")
        for image in images:
            if not image.filename or not (image.content_type or '').startswith('image/'): return _error("ارفع ملفات صور فقط")
            path=m.save_upload_file(image,'maintenance_visits')
            if path: conn.execute("INSERT INTO maintenance_cleaning_work_order_images(work_order_id,image_path,phase,created_at) VALUES(?,?,?,?)",(order_id,path,phase,datetime.now().isoformat(timespec='seconds')))
        conn.commit()
    finally: conn.close()
    return RedirectResponse(f"/maintenance-cleaning/work-order/{order_id}",303)
