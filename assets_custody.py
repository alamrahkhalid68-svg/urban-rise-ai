# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image as ReportLabImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from auth import get_current_user, is_admin
from db import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def init_assets_custody_schema():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS vehicles (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_type TEXT NOT NULL, make TEXT NOT NULL, model TEXT NOT NULL, manufacture_year INTEGER, color TEXT, plate_number TEXT NOT NULL UNIQUE, vin TEXT UNIQUE, current_odometer INTEGER NOT NULL DEFAULT 0 CHECK(current_odometer>=0), custody_status TEXT NOT NULL DEFAULT 'unassigned', notes TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS vehicle_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, employee_id INTEGER NOT NULL, delivered_at TEXT NOT NULL, returned_at TEXT, status TEXT NOT NULL DEFAULT 'assigned', accessories TEXT, notes TEXT, created_by INTEGER, FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(employee_id) REFERENCES employees(id), FOREIGN KEY(created_by) REFERENCES users(id));
    CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_one_active_assignment ON vehicle_assignments(vehicle_id) WHERE status='assigned';
    CREATE TABLE IF NOT EXISTS vehicle_odometer_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, reading INTEGER NOT NULL CHECK(reading>=0), recorded_at TEXT NOT NULL, recorded_by INTEGER, FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(recorded_by) REFERENCES users(id));
    CREATE INDEX IF NOT EXISTS idx_vehicle_odometer_history ON vehicle_odometer_logs(vehicle_id,recorded_at DESC,id DESC);
    CREATE TABLE IF NOT EXISTS vehicle_oil_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, change_date TEXT NOT NULL, odometer INTEGER NOT NULL CHECK(odometer>=0), oil_interval INTEGER NOT NULL CHECK(oil_interval IN (5000,10000)), odometer_image TEXT NOT NULL, recorded_at TEXT NOT NULL, recorded_by INTEGER, FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(recorded_by) REFERENCES users(id));
    CREATE INDEX IF NOT EXISTS idx_vehicle_oil_history ON vehicle_oil_changes(vehicle_id,change_date DESC,id DESC);
    CREATE TABLE IF NOT EXISTS employee_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, asset_type TEXT NOT NULL, brand_model TEXT, serial_imei TEXT, accessories TEXT, condition TEXT, delivered_at TEXT NOT NULL, returned_at TEXT, status TEXT NOT NULL DEFAULT 'assigned', notes TEXT, created_by INTEGER, FOREIGN KEY(employee_id) REFERENCES employees(id), FOREIGN KEY(created_by) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS vehicle_incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, assignment_id INTEGER, incident_date TEXT NOT NULL, odometer INTEGER NOT NULL CHECK(odometer>=0), notes TEXT NOT NULL, recorded_at TEXT NOT NULL, recorded_by INTEGER, FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(assignment_id) REFERENCES vehicle_assignments(id), FOREIGN KEY(recorded_by) REFERENCES users(id));
    CREATE INDEX IF NOT EXISTS idx_vehicle_incidents_history ON vehicle_incidents(vehicle_id,incident_date DESC,id DESC);
    CREATE TABLE IF NOT EXISTS vehicle_assignment_images (id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER, incident_id INTEGER, image_type TEXT NOT NULL CHECK(image_type IN ('delivery','return','incident')), image_path TEXT NOT NULL, uploaded_at TEXT NOT NULL, uploaded_by INTEGER, FOREIGN KEY(assignment_id) REFERENCES vehicle_assignments(id), FOREIGN KEY(incident_id) REFERENCES vehicle_incidents(id), FOREIGN KEY(uploaded_by) REFERENCES users(id));
    CREATE INDEX IF NOT EXISTS idx_vehicle_assignment_images_assignment ON vehicle_assignment_images(assignment_id,image_type,id);
    CREATE INDEX IF NOT EXISTS idx_vehicle_assignment_images_incident ON vehicle_assignment_images(incident_id,id);
    """)
    assignment_columns={row["name"] for row in conn.execute("PRAGMA table_info(vehicle_assignments)").fetchall()}
    for column_name,column_definition in {"delivery_odometer":"INTEGER","delivery_notes":"TEXT","return_odometer":"INTEGER","return_notes":"TEXT"}.items():
        if column_name not in assignment_columns:
            conn.execute(f"ALTER TABLE vehicle_assignments ADD COLUMN {column_name} {column_definition}")
    conn.execute("UPDATE vehicle_assignments SET delivery_odometer=(SELECT reading FROM vehicle_odometer_logs WHERE vehicle_id=vehicle_assignments.vehicle_id AND recorded_at<=vehicle_assignments.delivered_at||'T23:59:59' ORDER BY recorded_at DESC,id DESC LIMIT 1) WHERE delivery_odometer IS NULL")
    conn.execute("UPDATE vehicle_assignments SET delivery_odometer=(SELECT current_odometer FROM vehicles WHERE id=vehicle_assignments.vehicle_id) WHERE delivery_odometer IS NULL")
    conn.commit(); conn.close()


def calculate_oil_status(current_odometer, oil_change):
    if not oil_change:
        return {"has_cycle": False, "used": 0, "remaining": None, "interval": None, "next_change": None, "progress": 0, "status": "not_set", "label": "سجّل أول تغيير زيت", "sort_rank": 4}
    start, interval = int(oil_change["odometer"]), int(oil_change["oil_interval"])
    used = max(int(current_odometer) - start, 0); remaining = interval - used
    threshold = interval * .10; progress = min(max(used / interval * 100, 0), 100)
    if remaining < 0: status, label, rank = "overdue", f"متأخر عن تغيير الزيت بـ {abs(remaining):,} كم", 0
    elif remaining == 0: status, label, rank = "due", "يجب تغيير الزيت الآن", 0
    elif remaining <= threshold: status, label, rank = "warning", f"باقي على تغيير الزيت {remaining:,} كم", 1
    else: status, label, rank = "normal", "حالة الزيت طبيعية", 3
    return {"has_cycle": True, "start": start, "used": used, "remaining": remaining, "interval": interval, "next_change": start+interval, "warning_at": start+int(interval*.9), "warning_threshold": int(threshold), "progress": round(progress,1), "status": status, "label": label, "sort_rank": rank}


def _guard(request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user: return None, RedirectResponse("/login", status_code=303)
    if is_admin(user): return user, None
    conn=get_db(); allowed=conn.execute("SELECT 1 FROM user_company_access WHERE user_id=? AND section='asset_custody_manager' AND company IN ('works','all') LIMIT 1",(user["id"],)).fetchone(); conn.close()
    if allowed: return user, None
    return None, HTMLResponse("<h2 dir='rtl'>ليس لديك صلاحية إدارة العهد</h2>",status_code=403)


def _admin_vehicle_guard(request, denied_message="إدارة السيارات متاحة لمدير النظام فقط"):
    user=getattr(request.state,"current_user",None) or get_current_user(request)
    if not user:return None,RedirectResponse("/login",status_code=303)
    if is_admin(user):return user,None
    return None,HTMLResponse(f"<h2 dir='rtl'>{denied_message}</h2>",status_code=403)


def _days_since(value):
    if not value: return None
    try: return max((datetime.now()-datetime.fromisoformat(value)).days,0)
    except ValueError: return None


def _bundle(conn, vehicle_id):
    vehicle=conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    if not vehicle:return None
    assignment=conn.execute("SELECT a.*,e.name employee_name,e.role employee_role,e.company employee_company FROM vehicle_assignments a JOIN employees e ON e.id=a.employee_id WHERE a.vehicle_id=? AND a.status='assigned' ORDER BY a.id DESC LIMIT 1",(vehicle_id,)).fetchone()
    oil=conn.execute("SELECT * FROM vehicle_oil_changes WHERE vehicle_id=? ORDER BY change_date DESC,id DESC LIMIT 1",(vehicle_id,)).fetchone()
    log=conn.execute("SELECT * FROM vehicle_odometer_logs WHERE vehicle_id=? ORDER BY recorded_at DESC,id DESC LIMIT 1",(vehicle_id,)).fetchone()
    return vehicle,assignment,oil,log


def _save_images(files, assignment_id, image_type, user_id, incident_id=None):
    from main import save_upload_file
    saved=[]
    for upload in files or []:
        if not upload or not upload.filename or not (upload.content_type or "").startswith("image/"):
            continue
        path=save_upload_file(upload,"vehicle_custody")
        if path:saved.append((assignment_id,incident_id,image_type,path,datetime.now().isoformat(timespec="seconds"),user_id))
    return saved


@router.get("/assets-custody",response_class=HTMLResponse)
def dashboard(request:Request):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db(); rows=conn.execute("""SELECT v.*,e.name employee_name,l.recorded_at last_odometer_update FROM vehicles v LEFT JOIN vehicle_assignments a ON a.vehicle_id=v.id AND a.status='assigned' LEFT JOIN employees e ON e.id=a.employee_id LEFT JOIN vehicle_odometer_logs l ON l.id=(SELECT id FROM vehicle_odometer_logs x WHERE x.vehicle_id=v.id ORDER BY x.recorded_at DESC,x.id DESC LIMIT 1)""").fetchall(); vehicles=[]
    for row in rows:
        oil=conn.execute("SELECT * FROM vehicle_oil_changes WHERE vehicle_id=? ORDER BY change_date DESC,id DESC LIMIT 1",(row["id"],)).fetchone(); item=dict(row); item["oil"]=calculate_oil_status(row["current_odometer"],oil); item["days"]=_days_since(row["last_odometer_update"]); item["stale"]=item["days"] is None or item["days"]>7; vehicles.append(item)
    conn.close(); vehicles.sort(key=lambda x:(x["oil"]["sort_rank"],0 if x["stale"] else 1,x["plate_number"]))
    return templates.TemplateResponse(request=request,name="assets_dashboard.html",context={"vehicles":vehicles,"can_create_vehicle":is_admin(user),"home_url":"/assets-custody" if not is_admin(user) else "/"})


@router.get("/vehicles/new",response_class=HTMLResponse)
def vehicle_form(request:Request):
    _,denied=_admin_vehicle_guard(request,"إضافة سيارة جديدة متاحة لمدير النظام فقط")
    if denied:return denied
    conn=get_db();employees=conn.execute("SELECT id,name,company FROM employees ORDER BY name").fetchall();conn.close()
    return templates.TemplateResponse(request=request,name="vehicle_form.html",context={"employees":employees,"today":date.today().isoformat()})


@router.post("/vehicles")
def create_vehicle(request:Request,vehicle_type:str=Form(...),make:str=Form(...),model:str=Form(...),manufacture_year:int|None=Form(None),color:str=Form(""),plate_number:str=Form(...),vin:str=Form(""),current_odometer:int=Form(0),employee_id:int|None=Form(None),delivered_at:str=Form(""),accessories:str=Form(""),notes:str=Form(""),delivery_notes:str=Form(""),delivery_images:list[UploadFile]=File([])):
    user,denied=_admin_vehicle_guard(request,"إضافة سيارة جديدة متاحة لمدير النظام فقط")
    if denied:return denied
    if current_odometer<0:return HTMLResponse("قراءة العداد غير صحيحة",status_code=400)
    conn=get_db()
    try:
        cur=conn.execute("INSERT INTO vehicles(vehicle_type,make,model,manufacture_year,color,plate_number,vin,current_odometer,custody_status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(vehicle_type.strip(),make.strip(),model.strip(),manufacture_year,color.strip(),plate_number.strip(),vin.strip() or None,current_odometer,"assigned" if employee_id else "unassigned",notes.strip(),datetime.now().isoformat(timespec="seconds")));vid=cur.lastrowid;now=datetime.now().isoformat(timespec="seconds")
        conn.execute("INSERT INTO vehicle_odometer_logs(vehicle_id,reading,recorded_at,recorded_by) VALUES(?,?,?,?)",(vid,current_odometer,now,user["id"]))
        if employee_id:
            assignment_id=conn.execute("INSERT INTO vehicle_assignments(vehicle_id,employee_id,delivered_at,status,accessories,created_by,delivery_odometer,delivery_notes) VALUES(?,?,?,'assigned',?,?,?,?)",(vid,employee_id,delivered_at or date.today().isoformat(),accessories,user["id"],current_odometer,delivery_notes)).lastrowid
            conn.executemany("INSERT INTO vehicle_assignment_images(assignment_id,incident_id,image_type,image_path,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?)",_save_images(delivery_images,assignment_id,"delivery",user["id"]))
        conn.commit()
    except sqlite3.IntegrityError:conn.rollback();conn.close();return HTMLResponse("رقم اللوحة أو الهيكل مستخدم مسبقًا",status_code=400)
    conn.close();return RedirectResponse(f"/vehicle/{vid}",status_code=303)


@router.get("/vehicle/{vehicle_id}",response_class=HTMLResponse)
def vehicle_detail(request:Request,vehicle_id:int,message:str="",error:str=""):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db(); bundle=_bundle(conn,vehicle_id)
    if not bundle:conn.close();return HTMLResponse("السيارة غير موجودة",status_code=404)
    vehicle,assignment,oil,log=bundle; oils=conn.execute("SELECT * FROM vehicle_oil_changes WHERE vehicle_id=? ORDER BY change_date DESC,id DESC",(vehicle_id,)).fetchall(); log_rows=conn.execute("SELECT l.*,u.full_name recorder_name FROM vehicle_odometer_logs l LEFT JOIN users u ON u.id=l.recorded_by WHERE l.vehicle_id=? ORDER BY l.recorded_at DESC,l.id DESC LIMIT 20",(vehicle_id,)).fetchall(); assignments=conn.execute("SELECT a.*,e.name employee_name FROM vehicle_assignments a JOIN employees e ON e.id=a.employee_id WHERE a.vehicle_id=? ORDER BY a.id DESC",(vehicle_id,)).fetchall();employees=conn.execute("SELECT id,name,company FROM employees ORDER BY name").fetchall();images=conn.execute("SELECT * FROM vehicle_assignment_images WHERE assignment_id IN (SELECT id FROM vehicle_assignments WHERE vehicle_id=?) OR incident_id IN (SELECT id FROM vehicle_incidents WHERE vehicle_id=?) ORDER BY id",(vehicle_id,vehicle_id)).fetchall();incidents=conn.execute("SELECT i.*,e.name employee_name,u.full_name recorder_name FROM vehicle_incidents i LEFT JOIN vehicle_assignments a ON a.id=i.assignment_id LEFT JOIN employees e ON e.id=a.employee_id LEFT JOIN users u ON u.id=i.recorded_by WHERE i.vehicle_id=? ORDER BY i.incident_date DESC,i.id DESC",(vehicle_id,)).fetchall();conn.close();days=_days_since(log["recorded_at"] if log else None)
    logs=[]
    for index,row in enumerate(log_rows):
        item=dict(row); item["distance_since_previous"] = item["reading"]-log_rows[index+1]["reading"] if index+1<len(log_rows) else None; logs.append(item)
    images_by_assignment={}; images_by_incident={}
    for image in images:
        if image["assignment_id"]:images_by_assignment.setdefault(image["assignment_id"],[]).append(image)
        if image["incident_id"]:images_by_incident.setdefault(image["incident_id"],[]).append(image)
    assignment_days=_days_since(assignment["delivered_at"]) if assignment else None
    assignment_mileage=vehicle["current_odometer"]-(assignment["delivery_odometer"] or vehicle["current_odometer"]) if assignment else 0
    return templates.TemplateResponse(request=request,name="vehicle_detail.html",context={"vehicle":vehicle,"assignment":assignment,"assignment_days":assignment_days,"assignment_mileage":assignment_mileage,"assignment_images":images_by_assignment,"incident_images":images_by_incident,"incidents":incidents,"active_oil_change":oil,"oil":calculate_oil_status(vehicle["current_odometer"],oil),"oil_history":oils,"logs":logs,"assignments":assignments,"employees":employees,"days":days,"stale":days is None or days>7,"today":date.today().isoformat(),"message":message,"error":error,"can_delete_vehicle":is_admin(user),"home_url":"/assets-custody" if not is_admin(user) else "/"})


@router.post("/vehicle/{vehicle_id}/delete")
def delete_vehicle(request:Request,vehicle_id:int):
    _,denied=_admin_vehicle_guard(request,"حذف السيارة متاح لمدير النظام فقط")
    if denied:return denied
    conn=get_db()
    vehicle=conn.execute("SELECT id FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    if not vehicle:conn.close();return HTMLResponse("السيارة غير موجودة",status_code=404)
    image_rows=conn.execute("""SELECT image_path FROM vehicle_assignment_images
        WHERE assignment_id IN (SELECT id FROM vehicle_assignments WHERE vehicle_id=?)
           OR incident_id IN (SELECT id FROM vehicle_incidents WHERE vehicle_id=?)
        UNION SELECT odometer_image FROM vehicle_oil_changes WHERE vehicle_id=?""",(vehicle_id,vehicle_id,vehicle_id)).fetchall()
    try:
        conn.execute("""DELETE FROM vehicle_assignment_images
            WHERE assignment_id IN (SELECT id FROM vehicle_assignments WHERE vehicle_id=?)
               OR incident_id IN (SELECT id FROM vehicle_incidents WHERE vehicle_id=?)""",(vehicle_id,vehicle_id))
        conn.execute("DELETE FROM vehicle_incidents WHERE vehicle_id=?",(vehicle_id,))
        conn.execute("DELETE FROM vehicle_odometer_logs WHERE vehicle_id=?",(vehicle_id,))
        conn.execute("DELETE FROM vehicle_oil_changes WHERE vehicle_id=?",(vehicle_id,))
        conn.execute("DELETE FROM vehicle_assignments WHERE vehicle_id=?",(vehicle_id,))
        conn.execute("DELETE FROM vehicles WHERE id=?",(vehicle_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    from main import delete_upload_file
    for row in image_rows:
        if row[0]:
            try:delete_upload_file(row[0])
            except OSError:pass
    return RedirectResponse("/assets-custody",status_code=303)


@router.post("/vehicle/{vehicle_id}/odometer")
def update_odometer(request:Request,vehicle_id:int,reading:int=Form(...)):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db();v=conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    if not v:conn.close();return HTMLResponse("السيارة غير موجودة",status_code=404)
    if reading<v["current_odometer"]:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=لا+يمكن+إدخال+قراءة+أقل+من+آخر+قراءة",status_code=303)
    conn.execute("UPDATE vehicles SET current_odometer=? WHERE id=?",(reading,vehicle_id));conn.execute("INSERT INTO vehicle_odometer_logs(vehicle_id,reading,recorded_at,recorded_by) VALUES(?,?,?,?)",(vehicle_id,reading,datetime.now().isoformat(timespec="seconds"),user["id"]));conn.commit();conn.close();msg="تم تسجيل القراءة (السيارة لم تتحرك)" if reading==v["current_odometer"] else "تم تحديث قراءة العداد";return RedirectResponse(f"/vehicle/{vehicle_id}?message={msg}",status_code=303)


@router.post("/vehicle/{vehicle_id}/oil-change")
def oil_change(request:Request,vehicle_id:int,odometer:int=Form(...),oil_interval:int=Form(...),change_date:str=Form(...),odometer_image:UploadFile=File(...)):
    user,denied=_guard(request)
    if denied:return denied
    if oil_interval not in (5000,10000):return HTMLResponse("دورة الزيت غير صحيحة",status_code=400)
    if not odometer_image.filename or not (odometer_image.content_type or "").startswith("image/"):return RedirectResponse(f"/vehicle/{vehicle_id}?error=صورة+العداد+مطلوبة",status_code=303)
    conn=get_db();v=conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    if not v:conn.close();return HTMLResponse("السيارة غير موجودة",status_code=404)
    if odometer<v["current_odometer"]:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=قراءة+تغيير+الزيت+أقل+من+العداد+الحالي",status_code=303)
    from main import save_upload_file
    path=save_upload_file(odometer_image,"vehicle_oil");now=datetime.now().isoformat(timespec="seconds")
    if not path:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=تعذر+حفظ+الصورة",status_code=303)
    conn.execute("INSERT INTO vehicle_oil_changes(vehicle_id,change_date,odometer,oil_interval,odometer_image,recorded_at,recorded_by) VALUES(?,?,?,?,?,?,?)",(vehicle_id,change_date,odometer,oil_interval,path,now,user["id"]))
    if odometer>v["current_odometer"]:conn.execute("UPDATE vehicles SET current_odometer=? WHERE id=?",(odometer,vehicle_id));conn.execute("INSERT INTO vehicle_odometer_logs(vehicle_id,reading,recorded_at,recorded_by) VALUES(?,?,?,?)",(vehicle_id,odometer,now,user["id"]))
    conn.commit();conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?message=بدأت+دورة+زيت+جديدة",status_code=303)


@router.post("/vehicle/{vehicle_id}/assign")
def assign(request:Request,vehicle_id:int,employee_id:int=Form(...),delivered_at:str=Form(...),delivery_odometer:int=Form(...),accessories:str=Form(""),delivery_notes:str=Form(""),delivery_images:list[UploadFile]=File([])):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db()
    if conn.execute("SELECT 1 FROM vehicle_assignments WHERE vehicle_id=? AND status='assigned'",(vehicle_id,)).fetchone():conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=يجب+استرجاع+العهدة+الحالية+أولاً",status_code=303)
    vehicle=conn.execute("SELECT current_odometer FROM vehicles WHERE id=?",(vehicle_id,)).fetchone()
    if not vehicle or delivery_odometer<vehicle["current_odometer"]:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=عداد+التسليم+أقل+من+العداد+الحالي",status_code=303)
    assignment_id=conn.execute("INSERT INTO vehicle_assignments(vehicle_id,employee_id,delivered_at,status,accessories,created_by,delivery_odometer,delivery_notes) VALUES(?,?,?,'assigned',?,?,?,?)",(vehicle_id,employee_id,delivered_at,accessories,user["id"],delivery_odometer,delivery_notes)).lastrowid
    if delivery_odometer>vehicle["current_odometer"]:conn.execute("UPDATE vehicles SET current_odometer=? WHERE id=?",(delivery_odometer,vehicle_id));conn.execute("INSERT INTO vehicle_odometer_logs(vehicle_id,reading,recorded_at,recorded_by) VALUES(?,?,?,?)",(vehicle_id,delivery_odometer,datetime.now().isoformat(timespec="seconds"),user["id"]))
    conn.executemany("INSERT INTO vehicle_assignment_images(assignment_id,incident_id,image_type,image_path,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?)",_save_images(delivery_images,assignment_id,"delivery",user["id"]));conn.execute("UPDATE vehicles SET custody_status='assigned' WHERE id=?",(vehicle_id,));conn.commit();conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+تسليم+السيارة",status_code=303)


@router.post("/vehicle/{vehicle_id}/return")
def return_vehicle(request:Request,vehicle_id:int,returned_at:str=Form(...),return_odometer:int=Form(...),return_notes:str=Form(""),return_images:list[UploadFile]=File([])):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db();vehicle=conn.execute("SELECT current_odometer FROM vehicles WHERE id=?",(vehicle_id,)).fetchone();assignment=conn.execute("SELECT * FROM vehicle_assignments WHERE vehicle_id=? AND status='assigned'",(vehicle_id,)).fetchone()
    if not vehicle or not assignment:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=لا+توجد+عهدة+نشطة",status_code=303)
    if return_odometer<vehicle["current_odometer"] or return_odometer<(assignment["delivery_odometer"] or 0):conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=عداد+الاسترجاع+أقل+من+آخر+قراءة",status_code=303)
    conn.execute("UPDATE vehicle_assignments SET status='returned',returned_at=?,return_odometer=?,return_notes=? WHERE id=?",(returned_at,return_odometer,return_notes,assignment["id"]));conn.executemany("INSERT INTO vehicle_assignment_images(assignment_id,incident_id,image_type,image_path,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?)",_save_images(return_images,assignment["id"],"return",user["id"]));
    if return_odometer>vehicle["current_odometer"]:conn.execute("UPDATE vehicles SET current_odometer=? WHERE id=?",(return_odometer,vehicle_id));conn.execute("INSERT INTO vehicle_odometer_logs(vehicle_id,reading,recorded_at,recorded_by) VALUES(?,?,?,?)",(vehicle_id,return_odometer,datetime.now().isoformat(timespec="seconds"),user["id"]))
    conn.execute("UPDATE vehicles SET custody_status='returned' WHERE id=?",(vehicle_id,));conn.commit();conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+استرجاع+السيارة+وحفظ+تقرير+الحالة",status_code=303)


@router.post("/vehicle/{vehicle_id}/incidents")
def add_incident(request:Request,vehicle_id:int,incident_date:str=Form(...),odometer:int=Form(...),notes:str=Form(...),incident_images:list[UploadFile]=File([])):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db();vehicle=conn.execute("SELECT current_odometer FROM vehicles WHERE id=?",(vehicle_id,)).fetchone();assignment=conn.execute("SELECT id FROM vehicle_assignments WHERE vehicle_id=? AND status='assigned' ORDER BY id DESC LIMIT 1",(vehicle_id,)).fetchone()
    if not vehicle:conn.close();return HTMLResponse("السيارة غير موجودة",status_code=404)
    if odometer<vehicle["current_odometer"]:conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?error=عداد+الواقعة+أقل+من+العداد+الحالي",status_code=303)
    incident_id=conn.execute("INSERT INTO vehicle_incidents(vehicle_id,assignment_id,incident_date,odometer,notes,recorded_at,recorded_by) VALUES(?,?,?,?,?,?,?)",(vehicle_id,assignment["id"] if assignment else None,incident_date,odometer,notes.strip(),datetime.now().isoformat(timespec="seconds"),user["id"])).lastrowid
    conn.executemany("INSERT INTO vehicle_assignment_images(assignment_id,incident_id,image_type,image_path,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?)",_save_images(incident_images,assignment["id"] if assignment else None,"incident",user["id"],incident_id));conn.commit();conn.close();return RedirectResponse(f"/vehicle/{vehicle_id}?message=تم+تسجيل+الحادث+أو+الواقعة",status_code=303)


def _p(text,style):
    from main import format_arabic_pdf_text
    return Paragraph(format_arabic_pdf_text(str(text or "-")),style)


def _make_pdf(title,employee,details,delivered_at,accessories=""):
    from main import get_pdf_report_font_name
    font=get_pdf_report_font_name();path=os.path.join(tempfile.gettempdir(),f"custody_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.pdf");style=ParagraphStyle("ar",fontName=font,fontSize=11,leading=18,alignment=2);heading=ParagraphStyle("arh",parent=style,fontSize=16,leading=24,textColor=colors.HexColor("#8a6a20"));doc=SimpleDocTemplate(path,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm);story=[_p("Urban Rise Works – أعمال أوربان رايز للمقاولات",heading),Spacer(1,6*mm),_p(title,heading),Spacer(1,4*mm)];rows=[("اسم الموظف",employee["name"]),("الوظيفة",employee["role"]),("الشركة",employee["company"]),("تاريخ التسليم",delivered_at),*details,("الملحقات",accessories or "لا يوجد")];table=Table([[_p(v,style),_p(k,style)] for k,v in rows],colWidths=[105*mm,45*mm]);table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.HexColor("#b89b55")),("BACKGROUND",(1,0),(1,-1),colors.HexColor("#f3ead4")),("VALIGN",(0,0),(-1,-1),"MIDDLE") ]));story.extend([table,Spacer(1,8*mm),_p("أقر باستلام العهدة الموضحة أعلاه والمحافظة عليها واستخدامها لأغراض العمل، وإعادتها عند الطلب أو انتهاء العلاقة الوظيفية بالحالة التي استلمتها بها مع مراعاة الاستهلاك الطبيعي.",style),Spacer(1,14*mm),_p("توقيع الموظف: ____________________      توقيع مسؤول التسليم: ____________________",style),Spacer(1,8*mm),_p("التاريخ: ____________________                 الختم: ____________________",style)]);doc.build(story);return path


@router.get("/vehicle/{vehicle_id}/custody.pdf")
def vehicle_pdf(request:Request,vehicle_id:int,assignment_id:int|None=None):
    _,denied=_guard(request)
    if denied:return denied
    conn=get_db();v=conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone();a=conn.execute("SELECT a.*,e.name,e.role,e.company FROM vehicle_assignments a JOIN employees e ON e.id=a.employee_id WHERE a.vehicle_id=? AND (? IS NULL OR a.id=?) ORDER BY a.id DESC LIMIT 1",(vehicle_id,assignment_id,assignment_id)).fetchone();conn.close()
    if not v or not a:return HTMLResponse("لا توجد عهدة سيارة لإصدارها",status_code=404)
    details=[("السيارة",f"{v['make']} {v['model']}"),("النوع",v["vehicle_type"]),("سنة الصنع",v["manufacture_year"]),("اللون",v["color"]),("رقم اللوحة",v["plate_number"]),("رقم الهيكل",v["vin"]),("قراءة العداد",f"{v['current_odometer']:,} كم")];path=_make_pdf("محضر عهدة سيارة",a,details,a["delivered_at"],a["accessories"]);return FileResponse(path,media_type="application/pdf",filename=f"vehicle-custody-{vehicle_id}.pdf")


def _pdf_images(story, title, images, style, heading):
    from main import resolve_upload_path
    valid=[]
    for image in images:
        resolved=resolve_upload_path(image["image_path"])
        if resolved:valid.append(resolved)
    if not valid:return
    story.extend([Spacer(1,5*mm),_p(title,heading),Spacer(1,3*mm)])
    for index,path in enumerate(valid):
        try:
            item=ReportLabImage(path);item._restrictSize(165*mm,105*mm);story.extend([item,Spacer(1,4*mm)])
            if index and index%2==1:story.append(PageBreak())
        except Exception:
            continue


@router.get("/vehicle/{vehicle_id}/assignment/{assignment_id}/final-report.pdf")
def assignment_final_report_pdf(request:Request,vehicle_id:int,assignment_id:int):
    _,denied=_guard(request)
    if denied:return denied
    conn=get_db();vehicle=conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone();assignment=conn.execute("SELECT a.*,e.name,e.role,e.company FROM vehicle_assignments a JOIN employees e ON e.id=a.employee_id WHERE a.id=? AND a.vehicle_id=?",(assignment_id,vehicle_id)).fetchone()
    if not vehicle or not assignment or assignment["status"]!="returned":conn.close();return HTMLResponse("تقرير نهاية العهدة متاح بعد الاسترجاع",status_code=400)
    images=conn.execute("SELECT * FROM vehicle_assignment_images WHERE assignment_id=? ORDER BY id",(assignment_id,)).fetchall();incidents=conn.execute("SELECT * FROM vehicle_incidents WHERE assignment_id=? ORDER BY incident_date,id",(assignment_id,)).fetchall();incident_images=conn.execute("SELECT ai.* FROM vehicle_assignment_images ai JOIN vehicle_incidents i ON i.id=ai.incident_id WHERE i.assignment_id=? ORDER BY ai.id",(assignment_id,)).fetchall();oils=conn.execute("SELECT change_date,odometer,oil_interval FROM vehicle_oil_changes WHERE vehicle_id=? AND change_date>=? AND change_date<=? ORDER BY change_date,id",(vehicle_id,assignment["delivered_at"][:10],assignment["returned_at"][:10])).fetchall();conn.close()
    from main import get_pdf_report_font_name
    font=get_pdf_report_font_name();style=ParagraphStyle("final_ar",fontName=font,fontSize=10.5,leading=17,alignment=2);heading=ParagraphStyle("final_h",parent=style,fontSize=15,leading=22,textColor=colors.HexColor("#8a6a20"));path=os.path.join(tempfile.gettempdir(),f"assignment_final_{assignment_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.pdf");doc=SimpleDocTemplate(path,pagesize=A4,rightMargin=16*mm,leftMargin=16*mm,topMargin=16*mm,bottomMargin=16*mm)
    mileage=(assignment["return_odometer"] or 0)-(assignment["delivery_odometer"] or 0);story=[_p("Urban Rise Works – أعمال أوربان رايز للمقاولات",heading),Spacer(1,4*mm),_p("تقرير نهاية عهدة سيارة",heading),Spacer(1,5*mm)]
    rows=[("الموظف",assignment["name"]),("الوظيفة",assignment["role"]),("الشركة/القسم",assignment["company"]),("السيارة",f"{vehicle['vehicle_type']} — {vehicle['make']} {vehicle['model']}"),("السنة / اللون",f"{vehicle['manufacture_year'] or '-'} / {vehicle['color'] or '-'}"),("اللوحة / VIN",f"{vehicle['plate_number']} / {vehicle['vin'] or '-'}"),("تاريخ التسليم",assignment["delivered_at"]),("عداد التسليم",f"{assignment['delivery_odometer'] or 0:,} كم"),("ملاحظات التسليم",assignment["delivery_notes"] or "لا يوجد"),("تاريخ الاسترجاع",assignment["returned_at"]),("عداد الاسترجاع",f"{assignment['return_odometer'] or 0:,} كم"),("إجمالي المسافة",f"{mileage:,} كم"),("ملاحظات الاسترجاع",assignment["return_notes"] or "لا يوجد")];table=Table([[_p(v,style),_p(k,style)] for k,v in rows],colWidths=[110*mm,50*mm]);table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.HexColor("#b89b55")),("BACKGROUND",(1,0),(1,-1),colors.HexColor("#f3ead4")),("VALIGN",(0,0),(-1,-1),"MIDDLE")]));story.append(table)
    _pdf_images(story,"صور السيارة عند التسليم",[x for x in images if x["image_type"]=="delivery"],style,heading);_pdf_images(story,"صور السيارة عند الاسترجاع",[x for x in images if x["image_type"]=="return"],style,heading)
    story.extend([PageBreak(),_p("الحوادث والوقائع خلال العهدة",heading),Spacer(1,3*mm)])
    if incidents:
        for incident in incidents:
            story.extend([_p(f"{incident['incident_date']} — {incident['odometer']:,} كم",style),_p(incident["notes"],style)]);_pdf_images(story,"صور الواقعة",[x for x in incident_images if x["incident_id"]==incident["id"]],style,heading)
    else:story.append(_p("لا توجد حوادث أو وقائع مسجلة خلال هذه العهدة.",style))
    story.extend([Spacer(1,5*mm),_p("تغييرات الزيت خلال العهدة",heading)])
    if oils:
        oil_table=Table([[_p(f"{x['oil_interval']:,} كم",style),_p(f"{x['odometer']:,} كم",style),_p(x["change_date"],style)] for x in oils],colWidths=[50*mm,50*mm,50*mm]);oil_table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.grey)]));story.append(oil_table)
    else:story.append(_p("لا توجد تغييرات زيت مسجلة خلال هذه العهدة.",style))
    story.extend([Spacer(1,12*mm),_p("توقيع الموظف: ____________________      توقيع مسؤول الاستلام: ____________________",style),Spacer(1,7*mm),_p("التاريخ: ____________________                 الختم: ____________________",style)]);doc.build(story);return FileResponse(path,media_type="application/pdf",filename=f"vehicle-assignment-final-{assignment_id}.pdf")


@router.get("/employee/{employee_id}",response_class=HTMLResponse)
def employee_profile(request:Request,employee_id:int,message:str=""):
    _,denied=_guard(request)
    if denied:return denied
    conn=get_db();e=conn.execute("SELECT * FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not e:conn.close();return HTMLResponse("الموظف غير موجود",status_code=404)
    vehicles=conn.execute("SELECT a.*,v.make,v.model,v.plate_number FROM vehicle_assignments a JOIN vehicles v ON v.id=a.vehicle_id WHERE a.employee_id=? ORDER BY a.id DESC",(employee_id,)).fetchall();assets=conn.execute("SELECT * FROM employee_assets WHERE employee_id=? ORDER BY id DESC",(employee_id,)).fetchall();conn.close();return templates.TemplateResponse(request=request,name="employee_assets.html",context={"employee":e,"vehicles":vehicles,"assets":assets,"today":date.today().isoformat(),"message":message})


@router.post("/employee/{employee_id}/assets")
def add_asset(request:Request,employee_id:int,asset_type:str=Form(...),brand_model:str=Form(""),serial_imei:str=Form(""),accessories:str=Form(""),condition:str=Form(""),delivered_at:str=Form(...),notes:str=Form("")):
    user,denied=_guard(request)
    if denied:return denied
    conn=get_db();conn.execute("INSERT INTO employee_assets(employee_id,asset_type,brand_model,serial_imei,accessories,condition,delivered_at,status,notes,created_by) VALUES(?,?,?,?,?,?,?,'assigned',?,?)",(employee_id,asset_type,brand_model,serial_imei,accessories,condition,delivered_at,notes,user["id"]));conn.commit();conn.close();return RedirectResponse(f"/employee/{employee_id}?message=تم+تسجيل+العهدة",status_code=303)


@router.post("/employee/{employee_id}/assets/{asset_id}/return")
def return_asset(request:Request,employee_id:int,asset_id:int,returned_at:str=Form(...)):
    _,denied=_guard(request)
    if denied:return denied
    conn=get_db();conn.execute("UPDATE employee_assets SET status='returned',returned_at=? WHERE id=? AND employee_id=?",(returned_at,asset_id,employee_id));conn.commit();conn.close();return RedirectResponse(f"/employee/{employee_id}?message=تم+استرجاع+العهدة",status_code=303)


@router.get("/employee/{employee_id}/assets/{asset_id}.pdf")
def asset_pdf(request:Request,employee_id:int,asset_id:int):
    _,denied=_guard(request)
    if denied:return denied
    conn=get_db();e=conn.execute("SELECT * FROM employees WHERE id=?",(employee_id,)).fetchone();a=conn.execute("SELECT * FROM employee_assets WHERE id=? AND employee_id=?",(asset_id,employee_id)).fetchone();conn.close()
    if not e or not a:return HTMLResponse("العهدة غير موجودة",status_code=404)
    details=[("نوع العهدة",a["asset_type"]),("الماركة / الموديل",a["brand_model"]),("الرقم التسلسلي / IMEI",a["serial_imei"]),("الحالة",a["condition"])];path=_make_pdf("محضر عهدة ممتلكات",e,details,a["delivered_at"],a["accessories"]);return FileResponse(path,media_type="application/pdf",filename=f"asset-custody-{asset_id}.pdf")


def register_assets_custody(app):
    init_assets_custody_schema();app.include_router(router)
