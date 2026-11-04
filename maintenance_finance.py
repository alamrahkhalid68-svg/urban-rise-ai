"""Financial ledger for one-off maintenance work orders. All new money is halalas."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
import sqlite3

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()
CATEGORIES = ("العمال", "مواد التنظيف", "مواد الصيانة وقطع الغيار", "النقل والوقود", "المعدات أو الاستئجار", "العمالة الخارجية", "مصروفات أخرى")
METHODS = ("نقدي", "تحويل", "شبكة", "أخرى")
ESTIMATE_FIELDS = ("expected_worker_hours_minutes", "expected_labor_halalas", "expected_materials_halalas", "expected_transport_halalas", "expected_equipment_halalas", "expected_other_halalas")


def money(value):
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise ValueError
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("أدخل مبلغًا صحيحًا غير سالب بحد أقصى منزلتين عشريتين")


def riyals(halalas):
    return f"{Decimal(halalas or 0) / Decimal(100):,.2f}"


def _columns(conn, table):
    return {r['name'] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    # The first batch creates work orders; keep this migration additive and idempotent.
    from maintenance_work_orders import migrate as migrate_work_orders
    migrate_work_orders(conn)
    import main
    main.ensure_maintenance_cleaning_project_tables(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_work_order_finance (
        work_order_id INTEGER PRIMARY KEY, base_halalas INTEGER, vat_halalas INTEGER,
        admin_share_halalas INTEGER, expected_worker_hours_minutes INTEGER,
        expected_labor_halalas INTEGER, expected_materials_halalas INTEGER,
        expected_transport_halalas INTEGER, expected_equipment_halalas INTEGER,
        expected_other_halalas INTEGER, financial_status TEXT NOT NULL DEFAULT 'مفتوح',
        debt_reason TEXT, financially_closed_at TEXT, financially_closed_by INTEGER,
        updated_at TEXT, updated_by_user_id INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_worker_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL,
        expense_id INTEGER UNIQUE, worker_name TEXT NOT NULL, worker_count INTEGER NOT NULL,
        hours_minutes INTEGER NOT NULL, hourly_rate_halalas INTEGER NOT NULL,
        team_cost_halalas INTEGER, pricing_mode TEXT NOT NULL DEFAULT 'hourly',
        total_halalas INTEGER NOT NULL, recorded_by_user_id INTEGER NOT NULL,
        edited_by_user_id INTEGER, edited_at TEXT, created_at TEXT NOT NULL)""")
    labor_cols=_columns(conn,'maintenance_cleaning_worker_costs')
    if 'team_cost_halalas' not in labor_cols:conn.execute('ALTER TABLE maintenance_cleaning_worker_costs ADD COLUMN team_cost_halalas INTEGER')
    if 'pricing_mode' not in labor_cols:conn.execute("ALTER TABLE maintenance_cleaning_worker_costs ADD COLUMN pricing_mode TEXT NOT NULL DEFAULT 'hourly'")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL,
        amount_halalas INTEGER NOT NULL, collected_on TEXT NOT NULL,
        method TEXT NOT NULL, reference TEXT, dedupe_key TEXT NOT NULL UNIQUE,
        recorded_by_user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        updated_by_user_id INTEGER, updated_at TEXT, is_void INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_cleaning_extra_work (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id INTEGER NOT NULL,
        description TEXT NOT NULL, added_financially INTEGER NOT NULL DEFAULT 0,
        added_amount_halalas INTEGER, added_vat_halalas INTEGER,
        created_at TEXT NOT NULL, recorded_by_user_id INTEGER NOT NULL)""")
    if 'added_amount_halalas' not in _columns(conn,'maintenance_cleaning_extra_work'):
        conn.execute('ALTER TABLE maintenance_cleaning_extra_work ADD COLUMN added_amount_halalas INTEGER')
    if 'added_vat_halalas' not in _columns(conn,'maintenance_cleaning_extra_work'):
        conn.execute('ALTER TABLE maintenance_cleaning_extra_work ADD COLUMN added_vat_halalas INTEGER')
    expense_cols = _columns(conn, 'maintenance_cleaning_project_expenses')
    for name, kind in (("work_order_id","INTEGER"),("amount_halalas","INTEGER"),("invoice_ref","TEXT"),("invoice_path","TEXT"),("recorded_by_user_id","INTEGER"),("updated_by_user_id","INTEGER"),("updated_at","TEXT")):
        if name not in expense_cols:
            conn.execute(f"ALTER TABLE maintenance_cleaning_project_expenses ADD COLUMN {name} {kind}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_expense_order ON maintenance_cleaning_project_expenses(work_order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_collection_order ON maintenance_cleaning_collections(work_order_id,is_void)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_worker_order ON maintenance_cleaning_worker_costs(work_order_id)")
    conn.commit()


def _order(conn, order_id):
    row = conn.execute("""SELECT w.*,p.client_name FROM maintenance_cleaning_work_orders w
        JOIN maintenance_cleaning_projects p ON p.id=w.project_id WHERE w.id=?""", (order_id,)).fetchone()
    if not row: raise ValueError("أمر العمل غير موجود")
    return row


def _legacy_halalas(value):
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def snapshot(conn, order_id):
    order = _order(conn, order_id)
    f = conn.execute("SELECT * FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?",(order_id,)).fetchone()
    expense_rows = conn.execute("SELECT * FROM maintenance_cleaning_project_expenses WHERE project_id=?",(order['project_id'],)).fetchall()
    cost = sum((r['amount_halalas'] if r['amount_halalas'] is not None else _legacy_halalas(r['amount'])) for r in expense_rows)
    collected = conn.execute("SELECT COALESCE(SUM(amount_halalas),0) FROM maintenance_cleaning_collections WHERE work_order_id=? AND is_void=0",(order_id,)).fetchone()[0]
    base = f['base_halalas'] if f and f['base_halalas'] is not None else None
    vat = f['vat_halalas'] if f and f['vat_halalas'] is not None else None
    admin = f['admin_share_halalas'] if f and f['admin_share_halalas'] is not None else None
    total = base + vat if base is not None and vat is not None else None
    remaining = total - collected if total is not None else None
    gross = base - cost if base is not None else None
    net = gross - admin if gross is not None and admin is not None else None
    margin = (Decimal(net) / Decimal(base) * 100).quantize(Decimal('0.01')) if net is not None and base else None
    labor = sum(r['amount_halalas'] if r['amount_halalas'] is not None else _legacy_halalas(r['amount']) for r in expense_rows if r['category']=='العمال')
    materials = sum(r['amount_halalas'] if r['amount_halalas'] is not None else _legacy_halalas(r['amount']) for r in expense_rows if r['category'] in ('مواد التنظيف','مواد الصيانة وقطع الغيار'))
    transport = sum(r['amount_halalas'] if r['amount_halalas'] is not None else _legacy_halalas(r['amount']) for r in expense_rows if r['category']=='النقل والوقود')
    equipment = sum(r['amount_halalas'] if r['amount_halalas'] is not None else _legacy_halalas(r['amount']) for r in expense_rows if r['category']=='المعدات أو الاستئجار')
    hours = conn.execute("SELECT COALESCE(SUM(hours_minutes * worker_count),0) FROM maintenance_cleaning_worker_costs WHERE work_order_id=?",(order_id,)).fetchone()[0]
    expected = None if not f or any(f[k] is None for k in ESTIMATE_FIELDS[1:]) else sum(f[k] for k in ESTIMATE_FIELDS[1:])
    expected_profit = base - expected - admin if base is not None and expected is not None and admin is not None else None
    return dict(order=order, finance=f, expenses=expense_rows, base=base, vat=vat, total=total,
                collected=collected, remaining=remaining, cost=cost, gross=gross, admin=admin,
                net=net, margin=margin, labor=labor, materials=materials, transport=transport,
                equipment=equipment, hours_minutes=hours, expected=expected, expected_profit=expected_profit)


def save_pricing(conn, order_id, base, vat, admin_share, estimates, user_id):
    _order(conn, order_id)
    current=conn.execute('SELECT financial_status FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?',(order_id,)).fetchone()
    if current and current['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):raise ValueError('أمر العمل مغلق ماليًا')
    values = [money(base),money(vat),money(admin_share)]
    expected=[]
    for idx, raw in enumerate(estimates):
        if raw is None or str(raw).strip()=='': expected.append(None)
        elif idx==0:
            hours=Decimal(str(raw))
            if not hours.is_finite() or hours<0: raise ValueError('ساعات العمال المتوقعة غير صحيحة')
            expected.append(int((hours*60).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))
        else: expected.append(money(raw))
    old=conn.execute("SELECT 1 FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?",(order_id,)).fetchone()
    if old:
        conn.execute("""UPDATE maintenance_cleaning_work_order_finance SET base_halalas=?,vat_halalas=?,admin_share_halalas=?,
          expected_worker_hours_minutes=?,expected_labor_halalas=?,expected_materials_halalas=?,expected_transport_halalas=?,
          expected_equipment_halalas=?,expected_other_halalas=?,updated_at=?,updated_by_user_id=? WHERE work_order_id=?""",
          (*values,*expected,datetime.now().isoformat(timespec='seconds'),user_id,order_id))
    else:
        conn.execute("""INSERT INTO maintenance_cleaning_work_order_finance
          (work_order_id,base_halalas,vat_halalas,admin_share_halalas,expected_worker_hours_minutes,expected_labor_halalas,
          expected_materials_halalas,expected_transport_halalas,expected_equipment_halalas,expected_other_halalas,updated_at,updated_by_user_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(order_id,*values,*expected,datetime.now().isoformat(timespec='seconds'),user_id))
    if snapshot(conn,order_id)['remaining'] < 0: raise ValueError('الإجمالي الجديد أقل من التحصيل المسجل')


def add_cost(conn, order_id, category, description, amount, day, invoice_ref, invoice_path, user_id):
    order=_order(conn,order_id)
    f=conn.execute('SELECT financial_status FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?',(order_id,)).fetchone()
    if f and f['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):raise ValueError('أمر العمل مغلق ماليًا')
    if category not in CATEGORIES: raise ValueError('تصنيف التكلفة غير صحيح')
    halalas=money(amount)
    if halalas<=0: raise ValueError('مبلغ التكلفة يجب أن يكون أكبر من صفر')
    date.fromisoformat(day)
    cur=conn.execute("""INSERT INTO maintenance_cleaning_project_expenses
       (project_id,work_order_id,title,category,amount_halalas,date,invoice_ref,invoice_path,recorded_by_user_id,created_at)
       VALUES(?,?,?,?,?,?,?,?,?,?)""",(order['project_id'],order_id,description.strip(),category,halalas,day,invoice_ref.strip(),invoice_path,user_id,datetime.now().isoformat(timespec='seconds')))
    return cur.lastrowid


def add_labor(conn, order_id, worker_name, worker_count, hours, rate, day, user_id, pricing_mode='hourly', team_cost=''):
    if not worker_name.strip() or worker_count<=0: raise ValueError('اسم الفريق وعدد العمال مطلوبان')
    duration=Decimal(str(hours))
    if not duration.is_finite() or duration<=0: raise ValueError('ساعات العمل غير صحيحة')
    minutes=int((duration*60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if pricing_mode not in ('hourly','team'):raise ValueError('طريقة حساب العمال غير صحيحة')
    rate_h=money(rate) if pricing_mode=='hourly' else 0
    team_h=money(team_cost) if pricing_mode=='team' else None
    total=int((Decimal(worker_count)*Decimal(minutes)*Decimal(rate_h)/Decimal(60)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if pricing_mode=='hourly' else team_h
    expense_id=add_cost(conn,order_id,'العمال',f'{worker_name.strip()} — {worker_count} عامل × {hours} ساعة',riyals(total),day,'','',user_id)
    conn.execute("""INSERT INTO maintenance_cleaning_worker_costs(work_order_id,expense_id,worker_name,worker_count,hours_minutes,hourly_rate_halalas,team_cost_halalas,pricing_mode,total_halalas,recorded_by_user_id,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(order_id,expense_id,worker_name.strip(),worker_count,minutes,rate_h,team_h,pricing_mode,total,user_id,datetime.now().isoformat(timespec='seconds')))
    return total


def edit_labor(conn, labor_id, worker_name, worker_count, hours, rate, user_id, pricing_mode='hourly', team_cost=''):
    row=conn.execute("SELECT * FROM maintenance_cleaning_worker_costs WHERE id=?",(labor_id,)).fetchone()
    if not row: raise ValueError('تكلفة العمال غير موجودة')
    f=conn.execute('SELECT financial_status FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?',(row['work_order_id'],)).fetchone()
    if f and f['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):raise ValueError('أمر العمل مغلق ماليًا')
    duration=Decimal(str(hours))
    if pricing_mode not in ('hourly','team'):raise ValueError('طريقة حساب العمال غير صحيحة')
    rate_h=money(rate) if pricing_mode=='hourly' else 0
    team_h=money(team_cost) if pricing_mode=='team' else None
    if not worker_name.strip() or worker_count<=0 or not duration.is_finite() or duration<=0: raise ValueError('بيانات تكلفة العمال غير صحيحة')
    minutes=int((duration*60).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    total=int((Decimal(worker_count)*Decimal(minutes)*Decimal(rate_h)/Decimal(60)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if pricing_mode=='hourly' else team_h
    now=datetime.now().isoformat(timespec='seconds')
    conn.execute("""UPDATE maintenance_cleaning_worker_costs SET worker_name=?,worker_count=?,hours_minutes=?,hourly_rate_halalas=?,team_cost_halalas=?,pricing_mode=?,total_halalas=?,edited_by_user_id=?,edited_at=? WHERE id=?""",(worker_name.strip(),worker_count,minutes,rate_h,team_h,pricing_mode,total,user_id,now,labor_id))
    conn.execute("UPDATE maintenance_cleaning_project_expenses SET title=?,amount_halalas=?,updated_by_user_id=?,updated_at=? WHERE id=?",(f'{worker_name.strip()} — {worker_count} عامل × {hours} ساعة',total,user_id,now,row['expense_id']))
    return total


def collect(conn, order_id, amount, day, method, reference, user_id):
    if method not in METHODS: raise ValueError('طريقة الدفع غير صحيحة')
    date.fromisoformat(day)
    halalas=money(amount)
    if halalas<=0: raise ValueError('مبلغ التحصيل يجب أن يكون أكبر من صفر')
    s=snapshot(conn,order_id)
    if s['remaining'] is None: raise ValueError('أدخل قيمة العمل والضريبة قبل التحصيل')
    if halalas>s['remaining']: raise ValueError('مبلغ التحصيل أكبر من المتبقي')
    ref=reference.strip()
    key=f'{order_id}|{day}|{method}|{halalas}|{ref.casefold()}'
    if conn.execute('SELECT 1 FROM maintenance_cleaning_collections WHERE dedupe_key=?',(key,)).fetchone(): raise ValueError('عملية التحصيل مسجلة مسبقًا')
    conn.execute("""INSERT INTO maintenance_cleaning_collections(work_order_id,amount_halalas,collected_on,method,reference,dedupe_key,recorded_by_user_id,created_at)
      VALUES(?,?,?,?,?,?,?,?)""",(order_id,halalas,day,method,ref,key,user_id,datetime.now().isoformat(timespec='seconds')))
    refresh_status(conn,order_id)


def refresh_status(conn, order_id):
    s=snapshot(conn,order_id)
    f=s['finance']
    if f and f['financial_status']=='مغلق ماليًا' and s['remaining'] is not None and s['remaining']>0:
        status='محصل جزئيًا' if s['collected'] else 'مفتوح'
        conn.execute('UPDATE maintenance_cleaning_work_order_finance SET financial_status=? WHERE work_order_id=?',(status,order_id))
    elif f and f['financial_status']=='مغلق مع مديونية' and s['remaining']==0:
        conn.execute("UPDATE maintenance_cleaning_work_order_finance SET financial_status='مغلق ماليًا',debt_reason='' WHERE work_order_id=?",(order_id,))
    elif f and f['financial_status'] not in ('مغلق ماليًا','مغلق مع مديونية'):
        status='محصل بالكامل' if s['remaining']==0 else ('محصل جزئيًا' if s['collected'] else 'مفتوح')
        conn.execute('UPDATE maintenance_cleaning_work_order_finance SET financial_status=? WHERE work_order_id=?',(status,order_id))


def void_collection(conn, collection_id, user_id):
    row=conn.execute('SELECT * FROM maintenance_cleaning_collections WHERE id=?',(collection_id,)).fetchone()
    if not row or row['is_void']: raise ValueError('التحصيل غير موجود أو ملغى')
    conn.execute("UPDATE maintenance_cleaning_collections SET is_void=1,updated_by_user_id=?,updated_at=? WHERE id=?",(user_id,datetime.now().isoformat(timespec='seconds'),collection_id))
    refresh_status(conn,row['work_order_id'])


def close_financial(conn, order_id, user_id, with_debt=False, reason=''):
    s=snapshot(conn,order_id)
    if s['finance'] and s['finance']['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):
        raise ValueError('أمر العمل مغلق ماليًا بالفعل')
    if s['order']['status']!='مغلق': raise ValueError('يجب إغلاق التنفيذ أولاً')
    if s['remaining'] is None: raise ValueError('أدخل قيمة العمل والضريبة أولاً')
    if s['remaining'] and (not with_debt or not reason.strip()): raise ValueError('يوجد مبلغ متبقٍ؛ يلزم إغلاق مع مديونية وكتابة السبب')
    conn.execute("""UPDATE maintenance_cleaning_work_order_finance SET financial_status=?,debt_reason=?,financially_closed_at=?,financially_closed_by=? WHERE work_order_id=?""",
      ('مغلق مع مديونية' if s['remaining'] else 'مغلق ماليًا',reason.strip() if s['remaining'] else '',datetime.now().isoformat(timespec='seconds'),user_id,order_id))


def comparison(s):
    f=s['finance']
    expected_hours=f['expected_worker_hours_minutes'] if f else None
    pairs=[('ساعات العمال',expected_hours,s['hours_minutes'],'hours'),('تكلفة العمال',f['expected_labor_halalas'] if f else None,s['labor'],'money'),('المواد',f['expected_materials_halalas'] if f else None,s['materials'],'money'),('النقل',f['expected_transport_halalas'] if f else None,s['transport'],'money'),('المعدات',f['expected_equipment_halalas'] if f else None,s['equipment'],'money'),('إجمالي التكلفة',s['expected'],s['cost'],'money'),('الربح',s['expected_profit'],s['net'],'profit')]
    result=[]
    for name,planned,actual,kind in pairs:
        if planned is None: color='neutral'
        elif kind=='profit': color='green' if actual is not None and actual>=planned else ('red' if actual is not None and actual<0 else 'yellow')
        else: color='green' if actual<=planned else ('yellow' if planned>0 and actual<=planned*Decimal('1.10') else 'red')
        result.append((name,planned,actual,kind,color))
    return result


def alerts(conn,s,low_margin=Decimal('10')):
    order=s['order']; out=[]
    completed=order['status'] in ('مكتمل بانتظار المراجعة','مغلق')
    if completed and not s['expenses']: out.append('اكتمل العمل دون تسجيل تكاليف')
    if completed and not s['collected']: out.append('اكتمل العمل دون تحصيل')
    if s['expected'] is not None and s['cost']>s['expected']: out.append('التكلفة الفعلية تجاوزت المتوقعة')
    if s['margin'] is not None and s['margin']<low_margin: out.append('هامش الربح منخفض')
    if s['net'] is not None and s['net']<0: out.append('أمر العمل خاسر')
    if any(not (r['title'] or '').strip() for r in s['expenses']): out.append('يوجد مصروف بلا وصف')
    if order['status']=='مغلق' and s['remaining'] is not None and s['remaining']>0: out.append('يوجد مبلغ متبقٍ بعد إغلاق العمل')
    if conn.execute('SELECT 1 FROM maintenance_cleaning_extra_work WHERE work_order_id=? AND added_financially=0 LIMIT 1',(order['id'],)).fetchone(): out.append('يوجد عمل إضافي لم يضف ماليًا')
    return out


def dashboard(conn,from_day='',to_day='',service_type='',status='',team=''):
    sql="""SELECT id FROM maintenance_cleaning_work_orders WHERE 1=1"""; params=[]
    for clause,value in (("date(COALESCE(finished_at,scheduled_at,created_at))>=?",from_day),("date(COALESCE(finished_at,scheduled_at,created_at))<=?",to_day),("service_type=?",service_type),("status=?",status),("team=?",team)):
        if value: sql+=' AND '+clause;params.append(value)
    rows=[snapshot(conn,r['id']) for r in conn.execute(sql,params)]
    for item in rows:item['alerts']=alerts(conn,item)
    completed=[s for s in rows if s['order']['status'] in ('مكتمل بانتظار المراجعة','مغلق')]
    by_service={}
    for s in rows:
        label=s['order']['service_type'] or 'غير محدد'
        group=by_service.setdefault(label,{'net':0,'labor_materials':0,'priced_count':0})
        if s['net'] is not None:
            group['net']+=s['net'];group['priced_count']+=1
        group['labor_materials']+=s['labor']+s['materials']
    nets=[s['net'] for s in rows if s['net'] is not None]
    return dict(rows=rows,completed=len(completed),base=sum(s['base'] or 0 for s in rows),vat=sum(s['vat'] or 0 for s in rows),
        collected=sum(s['collected'] for s in rows),remaining=sum(s['remaining'] or 0 for s in rows),cost=sum(s['cost'] for s in rows),
        net=sum(nets),average_net=int(Decimal(sum(nets))/len(nets)) if nets else None,
        losing=[s for s in rows if s['net'] is not None and s['net']<0],
        completed_unpaid=[s for s in completed if not s['collected']],
        missing_costs=[s for s in completed if not s['expenses']],
        profitable=sorted(((k,v) for k,v in by_service.items() if v['priced_count']),key=lambda x:x[1]['net'],reverse=True),
        costly=sorted(by_service.items(),key=lambda x:x[1]['labor_materials'],reverse=True))


def _main():
    import main
    return main


def _auth(request, admin=False):
    m=_main(); user=m.get_company_page_access(request,'works')
    if not isinstance(user,sqlite3.Row): return user
    if not (m.is_admin(user) or (not admin and m.is_maintenance_cleaning_accountant(user))):
        return HTMLResponse('<h2>لا تملك صلاحية حسابات الصيانة والنظافة</h2>',status_code=403)
    return user


def _error(exc,code=400):
    return HTMLResponse(f'<h2>{escape(str(exc))}</h2>',status_code=code)


def _redirect(order_id):
    return RedirectResponse(f'/maintenance-cleaning/work-order/{order_id}/finance',status_code=303)


@router.get('/maintenance-cleaning/work-order/{order_id}/finance',response_class=HTMLResponse)
def finance_page(request:Request,order_id:int):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn); s=snapshot(conn,order_id); comparisons=comparison(s); warnings=alerts(conn,s)
        workers=conn.execute('SELECT * FROM maintenance_cleaning_worker_costs WHERE work_order_id=? ORDER BY id DESC',(order_id,)).fetchall()
        collections=conn.execute('SELECT * FROM maintenance_cleaning_collections WHERE work_order_id=? ORDER BY id DESC',(order_id,)).fetchall()
        extras=conn.execute('SELECT * FROM maintenance_cleaning_extra_work WHERE work_order_id=? ORDER BY id DESC',(order_id,)).fetchall()
    except ValueError as exc: return _error(exc,404)
    finally: conn.close()
    h=lambda x: escape(str(x if x is not None else ''))
    money_display=lambda n: 'غير مدخل' if n is None else riyals(n)+' ريال'
    fields=[('قيمة العمل قبل الضريبة',s['base']),('ضريبة القيمة المضافة',s['vat']),('الإجمالي على العميل',s['total']),('المحصل',s['collected']),('المتبقي',s['remaining']),('التكلفة الفعلية',s['cost']),('الربح قبل المصاريف الإدارية',s['gross']),('حصة المصاريف الإدارية',s['admin']),('صافي الربح',s['net'])]
    cards=''.join(f'<article><b>{h(label)}</b><strong>{money_display(value)}</strong></article>' for label,value in fields)
    cards+=f'<article><b>هامش الربح</b><strong>{h(s["margin"])}% </strong></article>' if s['margin'] is not None else '<article><b>هامش الربح</b><strong>غير مدخل</strong></article>'
    priority_fields=(('المحصل',s['collected'],bool(collections)),('المتبقي',s['remaining'],s['total'] is not None),('إجمالي التكلفة',s['cost'],bool(s['expenses']) or bool(workers)),('صافي الربح',s['net'],s['net'] is not None))
    priority_cards=''.join(f'<article><b>{h(label)}</b><strong>{money_display(value) if recorded else "لم يُسجّل"}</strong></article>' for label,value,recorded in priority_fields)
    secondary_fields=[(label,value) for label,value in fields if label not in ('المحصل','المتبقي','التكلفة الفعلية','صافي الربح')]
    cards=''.join(f'<article><b>{h(label)}</b><strong>{money_display(value)}</strong></article>' for label,value in secondary_fields)
    cards+=f'<article><b>هامش الربح</b><strong>{h(s["margin"])}% </strong></article>' if s['margin'] is not None else '<article><b>هامش الربح</b><strong>غير مدخل</strong></article>'
    compare_rows=''.join(f'<tr class="{color}"><td data-label="البند">{h(label)}</td><td data-label="المتوقع">{("غير مدخل" if planned is None else (format(Decimal(planned)/60,".2f")+" ساعة" if kind=="hours" else money_display(planned)))}</td><td data-label="الفعلي">{(format(Decimal(actual)/60,".2f")+" ساعة" if kind=="hours" else money_display(actual))}</td></tr>' for label,planned,actual,kind,color in comparisons)
    def invoice_link(expense):
        return f'<a href="{h(expense["invoice_path"])}">فاتورة</a>' if expense['invoice_path'] else ''
    def void_form(collection):
        return f'<form method=post action=/maintenance-cleaning/collection/{collection["id"]}/void><button>إلغاء التحصيل</button></form>' if m.is_admin(user) and not collection['is_void'] else ''
    cost_rows=''.join(f'<tr><td data-label="التصنيف">{h(r["category"] or "غير مصنف")}</td><td data-label="الوصف">{h(r["title"])}</td><td data-label="المبلغ">{money_display(r["amount_halalas"] if r["amount_halalas"] is not None else _legacy_halalas(r["amount"]))}</td><td data-label="التاريخ">{h(r["date"])}</td><td data-label="المرجع">{h(r["invoice_ref"])}</td><td data-label="الفاتورة">{invoice_link(r)}</td><td data-label="مسجل بواسطة">{h(r["recorded_by_user_id"])}</td></tr>' for r in s['expenses'])
    collection_rows=''.join(f'<tr><td data-label="المبلغ">{money_display(r["amount_halalas"])}</td><td data-label="التاريخ">{h(r["collected_on"])}</td><td data-label="الطريقة">{h(r["method"])}</td><td data-label="المرجع">{h(r["reference"])}</td><td data-label="الحالة">{"ملغى" if r["is_void"] else "نشط"}</td><td data-label="المستخدم">{h(r["recorded_by_user_id"])}</td><td data-label="إجراء">{void_form(r)}</td></tr>' for r in collections)
    worker_rows=''.join(f'<form method="post" action="/maintenance-cleaning/labor/{r["id"]}/edit"><input name="worker_name" value="{h(r["worker_name"])}"><input type="number" min="1" name="worker_count" value="{r["worker_count"]}"><input type="number" step="0.01" name="hours" value="{format(Decimal(r["hours_minutes"])/60,".2f")}"><input type="number" step="0.01" name="rate" value="{riyals(r["hourly_rate_halalas"])}"><span>{money_display(r["total_halalas"])}</span>{"<button>تعديل تكلفة العمال</button>" if m.is_admin(user) else ""}</form>' for r in workers)
    if m.is_admin(user):
        worker_rows=''.join(f'<form method="post" action="/maintenance-cleaning/labor/{r["id"]}/edit"><input name="worker_name" value="{h(r["worker_name"])}" required><input type="number" min="1" name="worker_count" value="{r["worker_count"]}" required><input type="number" min="0.01" step="0.01" name="hours" value="{format(Decimal(r["hours_minutes"])/60,".2f")}" required><select name="pricing_mode"><option value="hourly" {"selected" if r["pricing_mode"]=="hourly" else ""}>بالساعة</option><option value="team" {"selected" if r["pricing_mode"]=="team" else ""}>تكلفة الفريق</option></select><input type="number" min="0" step="0.01" name="rate" value="{riyals(r["hourly_rate_halalas"])}" placeholder="تكلفة الساعة"><input type="number" min="0" step="0.01" name="team_cost" value="{riyals(r["team_cost_halalas"])}" placeholder="تكلفة الفريق"><span>{money_display(r["total_halalas"])}</span><button>تعديل تكلفة العمال</button></form>' for r in workers)
    if not m.is_admin(user):
        worker_rows=''.join(f'<p>{h(r["worker_name"])} — {r["worker_count"]} عامل — {format(Decimal(r["hours_minutes"])/60,".2f")} ساعة — {money_display(r["total_halalas"])}</p>' for r in workers)
    def price_form(extra):
        return f' <form method=post action=/maintenance-cleaning/extra/{extra["id"]}/priced><input name=amount type=number min=0.01 step=0.01 placeholder=القيمة_قبل_الضريبة required><input name=vat type=number min=0 step=0.01 placeholder=الضريبة required><button>إضافة قيمة العمل</button></form>' if not extra['added_financially'] else ''
    extra_rows=''.join(f'<li>{h(r["description"])} — {"أضيف ماليًا" if r["added_financially"] else "لم يضف ماليًا"}{price_form(r)}</li>' for r in extras)
    f=s['finance']; estimate_inputs=''.join(f'<label>{h(label)}<input type="number" step="0.01" name="{field}" value="{h(Decimal(f[field])/60 if field=="expected_worker_hours_minutes" and f and f[field] is not None else (riyals(f[field]) if f and f[field] is not None else ""))}"></label>' for field,label in zip(ESTIMATE_FIELDS,('ساعات العمال المتوقعة','تكلفة العمال المتوقعة','المواد المتوقعة','النقل المتوقع','المعدات المتوقعة','تكاليف أخرى متوقعة')))
    cat_options=''.join(f'<option>{h(c)}</option>' for c in CATEGORIES if c!='العمال')
    method_options=''.join(f'<option>{h(v)}</option>' for v in METHODS)
    close_form=f'''<form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/close"><label><input type="checkbox" name="with_debt" value="1">إغلاق مع مديونية</label><label>سبب المديونية<input name="reason" placeholder="سبب المديونية"></label><button>إغلاق مالي</button></form>''' if m.is_admin(user) else ''
    team_form=f'''<form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/labor"><input type="hidden" name="pricing_mode" value="team"><label>اسم الفريق<input name="worker_name" required></label><label>عدد العمال<input type="number" name="worker_count" min="1" required></label><label>الساعات الفعلية<input type="number" name="hours" min="0.01" step="0.01" required></label><label>تكلفة الفريق كاملة<input type="number" name="team_cost" min="0.01" step="0.01" required></label><label>التاريخ<input type="date" name="day" required></label><button>حفظ تكلفة الفريق</button></form>'''
    return HTMLResponse(f'''<html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/maintenance-cleaning.css"><link rel="stylesheet" href="/static/maintenance-finance.css"><body class="system-dark mc-finance"><main class="dashboard"><nav class="mc-top-nav" aria-label="تنقل الصيانة والنظافة"><a href="/company/works" aria-label="رجوع إلى أقسام شركة المقاولات"><span aria-hidden="true">←</span> رجوع</a><a href="/dashboard" aria-label="العودة إلى الرئيسية"><span aria-hidden="true">🏠</span> الرئيسية</a></nav><h1>حسابات أمر العمل #{order_id}</h1><p>حالة التنفيذ: {h(s['order']['status'])} | الحالة المالية: {h(f['financial_status'] if f else 'غير معدة')}</p><div class="fin-priority">{priority_cards}</div><div class="fin-cards">{cards}</div><p>الربحية محسوبة قبل الضريبة، والتحصيل منفصل عنها.</p><h2>تنبيهات</h2><ul>{''.join(f'<li>{h(w)}</li>' for w in warnings) or '<li>لا توجد تنبيهات</li>'}</ul><h2>التسعير والتوقعات</h2><form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/pricing"><label>قيمة العمل قبل الضريبة<input type="number" step="0.01" name="base" value="{riyals(s['base']) if s['base'] is not None else ''}" required></label><label>الضريبة<input type="number" step="0.01" name="vat" value="{riyals(s['vat']) if s['vat'] is not None else ''}" required></label><label>حصة المصاريف الإدارية<input type="number" step="0.01" name="admin_share" value="{riyals(s['admin']) if s['admin'] is not None else ''}" required></label>{estimate_inputs}<button>حفظ التسعير والتوقعات</button></form><h2>المتوقع مقابل الفعلي</h2><table><tr><th>البند</th><th>المتوقع</th><th>الفعلي</th></tr>{compare_rows}</table><h2>التكاليف</h2><table><tr><th>التصنيف</th><th>الوصف</th><th>المبلغ</th><th>التاريخ</th><th>المرجع</th><th>الفاتورة</th><th>مسجل بواسطة</th></tr>{cost_rows}</table><form method="post" enctype="multipart/form-data" action="/maintenance-cleaning/work-order/{order_id}/finance/cost"><label>التصنيف<select name="category">{cat_options}</select></label><label>الوصف<input name="description"></label><label>المبلغ<input name="amount" type="number" min="0.01" step="0.01" required></label><label>التاريخ<input type="date" name="day" required></label><label>المرجع<input name="invoice_ref"></label><label>الفاتورة<input type="file" name="invoice" accept="image/*,.pdf"></label><button>إضافة تكلفة</button></form><h2>العمال</h2>{worker_rows}<form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/labor"><label>اسم العامل أو الفريق<input name="worker_name" required></label><label>عدد العمال<input type="number" name="worker_count" min="1" required></label><label>الساعات الفعلية<input type="number" name="hours" min="0.01" step="0.01" required></label><label>تكلفة الساعة<input type="number" name="rate" min="0" step="0.01" required></label><label>التاريخ<input type="date" name="day" required></label><button>حفظ تكلفة العمال</button></form>{team_form}<h2>التحصيل</h2><table><tr><th>المبلغ</th><th>التاريخ</th><th>الطريقة</th><th>المرجع</th><th>الحالة</th><th>المستخدم</th><th></th></tr>{collection_rows}</table><form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/collection"><label>المبلغ<input name="amount" type="number" min="0.01" step="0.01" required></label><label>التاريخ<input type="date" name="day" required></label><label>طريقة الدفع<select name="method">{method_options}</select></label><label>المرجع<input name="reference"></label><button>تسجيل تحصيل</button></form><h2>أعمال إضافية</h2><ul>{extra_rows}</ul><form method="post" action="/maintenance-cleaning/work-order/{order_id}/finance/extra"><label>وصف العمل الإضافي<input name="description" required></label><button>تسجيل عمل إضافي</button></form>{close_form}<a href="/maintenance-cleaning/work-order/{order_id}">العودة لأمر العمل</a></main></body></html>''')


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/pricing')
def pricing_route(request:Request,order_id:int,base:str=Form(...),vat:str=Form(...),admin_share:str=Form(...),expected_worker_hours_minutes:str=Form(''),expected_labor_halalas:str=Form(''),expected_materials_halalas:str=Form(''),expected_transport_halalas:str=Form(''),expected_equipment_halalas:str=Form(''),expected_other_halalas:str=Form('')):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn);save_pricing(conn,order_id,base,vat,admin_share,(expected_worker_hours_minutes,expected_labor_halalas,expected_materials_halalas,expected_transport_halalas,expected_equipment_halalas,expected_other_halalas),user['id']);conn.commit()
    except (ValueError,InvalidOperation) as exc: conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/cost')
def cost_route(request:Request,order_id:int,category:str=Form(...),description:str=Form(''),amount:str=Form(...),day:str=Form(...),invoice_ref:str=Form(''),invoice:UploadFile=File(None)):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row): return user
    m=_main(); conn=m.get_db()
    try:
        migrate(conn)
        if invoice and invoice.filename and not ((invoice.content_type or '').startswith('image/') or invoice.content_type=='application/pdf'): return _error('الفاتورة يجب أن تكون صورة أو PDF')
        path=m.save_upload_file(invoice,'expenses') if invoice and invoice.filename else ''
        add_cost(conn,order_id,category,description,amount,day,invoice_ref,path,user['id']);conn.commit()
    except ValueError as exc:conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/labor')
def labor_route(request:Request,order_id:int,worker_name:str=Form(...),worker_count:int=Form(...),hours:str=Form(...),rate:str=Form(''),day:str=Form(...),pricing_mode:str=Form('hourly'),team_cost:str=Form('')):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row): return user
    m=_main();conn=m.get_db()
    try:migrate(conn);add_labor(conn,order_id,worker_name,worker_count,hours,rate,day,user['id'],pricing_mode,team_cost);conn.commit()
    except (ValueError,InvalidOperation) as exc:conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/labor/{labor_id}/edit')
def labor_edit_route(request:Request,labor_id:int,worker_name:str=Form(...),worker_count:int=Form(...),hours:str=Form(...),rate:str=Form(''),pricing_mode:str=Form('hourly'),team_cost:str=Form('')):
    user=_auth(request,admin=True)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:
        migrate(conn);row=conn.execute('SELECT work_order_id FROM maintenance_cleaning_worker_costs WHERE id=?',(labor_id,)).fetchone()
        if not row:return _error('تكلفة العمال غير موجودة',404)
        edit_labor(conn,labor_id,worker_name,worker_count,hours,rate,user['id'],pricing_mode,team_cost);conn.commit();return _redirect(row['work_order_id'])
    except (ValueError,InvalidOperation) as exc:conn.rollback();return _error(exc)
    finally:conn.close()


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/collection')
def collection_route(request:Request,order_id:int,amount:str=Form(...),day:str=Form(...),method:str=Form(...),reference:str=Form('')):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:migrate(conn);collect(conn,order_id,amount,day,method,reference,user['id']);conn.commit()
    except (ValueError,sqlite3.IntegrityError) as exc:conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/collection/{collection_id}/void')
def collection_void_route(request:Request,collection_id:int):
    user=_auth(request,admin=True)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:
        migrate(conn);row=conn.execute('SELECT work_order_id FROM maintenance_cleaning_collections WHERE id=?',(collection_id,)).fetchone()
        if not row:return _error('التحصيل غير موجود',404)
        void_collection(conn,collection_id,user['id']);conn.commit();return _redirect(row['work_order_id'])
    except ValueError as exc:conn.rollback();return _error(exc)
    finally:conn.close()


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/close')
def close_route(request:Request,order_id:int,with_debt:str=Form(''),reason:str=Form('')):
    user=_auth(request,admin=True)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:migrate(conn);close_financial(conn,order_id,user['id'],with_debt=='1',reason);conn.commit()
    except ValueError as exc:conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/work-order/{order_id}/finance/extra')
def extra_route(request:Request,order_id:int,description:str=Form(...)):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:
        migrate(conn);_order(conn,order_id)
        f=conn.execute('SELECT financial_status FROM maintenance_cleaning_work_order_finance WHERE work_order_id=?',(order_id,)).fetchone()
        if f and f['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):return _error('أمر العمل مغلق ماليًا')
        if not description.strip():return _error('اكتب وصف العمل الإضافي')
        conn.execute('INSERT INTO maintenance_cleaning_extra_work(work_order_id,description,created_at,recorded_by_user_id) VALUES(?,?,?,?)',(order_id,description.strip(),datetime.now().isoformat(timespec='seconds'),user['id']));conn.commit()
    except ValueError as exc:conn.rollback();return _error(exc)
    finally:conn.close()
    return _redirect(order_id)


@router.post('/maintenance-cleaning/extra/{extra_id}/priced')
def extra_priced_route(request:Request,extra_id:int,amount:str=Form(...),vat:str=Form(...)):
    user=_auth(request)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:
        migrate(conn);row=conn.execute('SELECT work_order_id,added_financially FROM maintenance_cleaning_extra_work WHERE id=?',(extra_id,)).fetchone()
        if not row:return _error('العمل الإضافي غير موجود',404)
        if row['added_financially']:return _error('العمل الإضافي مضاف ماليًا بالفعل')
        halalas=money(amount)
        vat_halalas=money(vat)
        if halalas<=0:return _error('أدخل قيمة أكبر من صفر')
        s=snapshot(conn,row['work_order_id'])
        if s['base'] is None:return _error('أدخل قيمة العمل الأساسية أولاً')
        if s['finance']['financial_status'] in ('مغلق ماليًا','مغلق مع مديونية'):return _error('أمر العمل مغلق ماليًا')
        conn.execute('UPDATE maintenance_cleaning_work_order_finance SET base_halalas=base_halalas+?,vat_halalas=vat_halalas+? WHERE work_order_id=?',(halalas,vat_halalas,row['work_order_id']))
        conn.execute('UPDATE maintenance_cleaning_extra_work SET added_financially=1,added_amount_halalas=?,added_vat_halalas=? WHERE id=?',(halalas,vat_halalas,extra_id));conn.commit();return _redirect(row['work_order_id'])
    except ValueError as exc:conn.rollback();return _error(exc)
    finally:conn.close()


@router.get('/maintenance-cleaning/finance',response_class=HTMLResponse)
def dashboard_route(request:Request,from_date:str='',to_date:str='',service_type:str='',status:str='',team:str=''):
    user=_auth(request,admin=True)
    if not isinstance(user,sqlite3.Row):return user
    m=_main();conn=m.get_db()
    try:migrate(conn);data=dashboard(conn,from_date,to_date,service_type,status,team)
    finally:conn.close()
    h=lambda x:escape(str(x or ''))
    metrics=(('الأعمال المكتملة',data['completed'],False),('قيمة الأعمال قبل الضريبة',data['base'],True),('الضريبة',data['vat'],True),('المحصل',data['collected'],True),('المتبقي',data['remaining'],True),('إجمالي التكاليف',data['cost'],True),('صافي الربح',data['net'],True),('متوسط ربح أمر العمل',data['average_net'],True),('الأعمال الخاسرة',len(data['losing']),False),('المكتملة غير المحصلة',len(data['completed_unpaid']),False),('المكتملة دون تكاليف',len(data['missing_costs']),False))
    cards=''.join(f'<article><b>{name}</b><strong>{(riyals(value)+" ريال") if cash and value is not None else (value if value is not None else "غير مدخل")}</strong></article>' for name,value,cash in metrics)
    work_rows=''.join(f'<tr><td data-label="الأمر"><a href="/maintenance-cleaning/work-order/{s["order"]["id"]}/finance">#{s["order"]["id"]}</a></td><td data-label="الخدمة">{h(s["order"]["service_type"])}</td><td data-label="الفريق">{h(s["order"]["team"])}</td><td data-label="الحالة">{h(s["order"]["status"])}</td><td data-label="صافي الربح">{riyals(s["net"]) if s["net"] is not None else "غير مدخل"}</td><td data-label="تنبيهات">{h("، ".join(s["alerts"]))}</td></tr>' for s in data['rows'])
    top_profit=''.join(f'<li>{h(k)}: {riyals(v["net"])} ريال</li>' for k,v in data['profitable'][:5])
    top_cost=''.join(f'<li>{h(k)}: {riyals(v["labor_materials"])} ريال</li>' for k,v in data['costly'][:5])
    return HTMLResponse(f'''<html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/maintenance-cleaning.css"><link rel="stylesheet" href="/static/maintenance-finance.css"><body class="system-dark mc-finance"><main class="dashboard"><nav class="mc-top-nav" aria-label="تنقل الصيانة والنظافة"><a href="/company/works" aria-label="رجوع إلى أقسام شركة المقاولات"><span aria-hidden="true">←</span> رجوع</a><a href="/dashboard" aria-label="العودة إلى الرئيسية"><span aria-hidden="true">🏠</span> الرئيسية</a></nav><h1>لوحة حسابات الصيانة والنظافة</h1><form method="get"><label>من<input type="date" name="from_date" value="{h(from_date)}"></label><label>إلى<input type="date" name="to_date" value="{h(to_date)}"></label><label>نوع الخدمة<input name="service_type" placeholder="نوع الخدمة" value="{h(service_type)}"></label><label>الحالة<input name="status" placeholder="الحالة" value="{h(status)}"></label><label>الفريق<input name="team" placeholder="الفريق" value="{h(team)}"></label><button>تصفية</button></form><div class="fin-cards">{cards}</div><h2>أكثر الخدمات ربحًا</h2><ol>{top_profit}</ol><h2>أكثر الخدمات استهلاكًا للعمال والمواد</h2><ol>{top_cost}</ol><h2>أوامر العمل</h2><table><tr><th>الأمر</th><th>الخدمة</th><th>الفريق</th><th>الحالة</th><th>صافي الربح</th><th>تنبيهات</th></tr>{work_rows}</table><a href="/company/works/maintenance-cleaning">العودة للقسم</a></main></body></html>''')


