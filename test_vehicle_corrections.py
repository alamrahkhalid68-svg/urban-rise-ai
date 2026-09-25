"""Run in its own process: never uses or changes the working database."""
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
import atexit
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / "tmp" / "vehicle-correction-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEST_ROOT)
TEST_DB = TEST_ROOT / ("run-" + uuid.uuid4().hex + ".db")
atexit.register(lambda: TEST_DB.unlink(missing_ok=True))
os.environ["URBANRISE_DB_PATH"] = str(TEST_DB)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import main
from fastapi.testclient import TestClient
from assets_custody import init_assets_custody_schema
from vehicle_corrections import FIELDS


class VehicleCorrectionsTest(unittest.TestCase):
    def setUp(self):
        conn = main.get_db()
        for table in ("vehicle_edit_audit", "vehicle_assignment_images", "vehicle_maintenance", "vehicle_incidents", "vehicle_odometer_logs", "vehicle_oil_changes", "vehicle_assignments", "vehicles"):
            conn.execute(f"DELETE FROM {table}")
        for uid, role in ((801, "admin"), (802, "employee"), (803, "employee")):
            conn.execute("INSERT OR REPLACE INTO users(id,username,password,full_name,role,is_active) VALUES(?,?,?,?,?,1)", (uid, f"correction_{uid}", "test-only-password", f"test {uid}", role))
        conn.execute("INSERT OR REPLACE INTO employees(id,name,role,company,user_id) VALUES(801,'test employee','driver','works',802)")
        conn.execute("INSERT OR REPLACE INTO employees(id,name,role,company,user_id) VALUES(802,'other employee','driver','works',803)")
        for vid, eid in ((801, 801), (802, 802)):
            conn.execute("INSERT INTO vehicles(id,vehicle_type,make,model,plate_number,current_odometer,created_at) VALUES(?,'car','Toyota','Hilux',?,1200,'2026-01-01')", (vid, str(vid)))
            conn.execute("INSERT INTO vehicle_assignments(id,vehicle_id,employee_id,delivered_at,delivery_odometer,created_by) VALUES(?,?,?,'2026-01-01',100,801)", (vid, vid, eid))
        conn.execute("INSERT INTO vehicle_odometer_logs(id,vehicle_id,reading,recorded_at,recorded_by,notes) VALUES(801,801,100,'2026-01-01T10:00:00',801,'initial'),(802,801,1200,'2026-01-02T10:00:00',802,'weekly'),(803,802,1200,'2026-01-02T10:00:00',802,'old assignment')")
        conn.execute("INSERT INTO vehicle_oil_changes(id,vehicle_id,change_date,odometer,oil_interval,odometer_image,recorded_at,recorded_by) VALUES(801,801,'2026-01-02',1200,5000,'/test.jpg','2026-01-02T10:00:00',802)")
        conn.execute("UPDATE vehicle_odometer_logs SET source_kind='oil',source_id=801 WHERE id=802")
        conn.execute("INSERT INTO vehicle_incidents(id,vehicle_id,assignment_id,incident_date,odometer,notes,recorded_at,recorded_by) VALUES(801,801,801,'2026-01-02',1200,'incident','2026-01-02',802)")
        conn.execute("INSERT INTO vehicle_maintenance(id,vehicle_id,service_date,odometer,service_type,notes,recorded_at,recorded_by) VALUES(801,801,'2026-01-02',1200,'brakes','original','2026-01-02',802)")
        conn.commit(); conn.close()
        self.admin = self.login(801)
        self.employee = self.login(802)
        self.other = self.login(803)
        self.addCleanup(self.admin.close)
        self.addCleanup(self.employee.close)
        self.addCleanup(self.other.close)

    def login(self, uid):
        client = TestClient(main.app, follow_redirects=False)
        response = client.post('/login', data={"username": f"correction_{uid}", "password": "test-only-password"})
        self.assertEqual(response.status_code, 303, response.text)
        return client

    def row(self, table, rid):
        conn = main.get_db()
        result = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone())
        conn.close()
        return result

    def count(self, table):
        conn = main.get_db(); n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]; conn.close(); return n

    def edit(self, client, kind, rid, changes, vid=801):
        original = self.row(FIELDS[kind][0], rid)
        values = {f[0]: original[f[0]] if original[f[0]] is not None else "" for f in FIELDS[kind][1]}
        if client is not self.admin and kind == 'oil':
            values.pop('oil_interval')
        values.update(changes)
        return client.post(f'/vehicle/{vid}/records/{kind}/{rid}/edit', data=values)

    def test_admin_edits_every_record_and_settings(self):
        for kind, rid, changes in (("odometer",802,{"reading":1100}), ("oil",801,{"oil_interval":10000,"next_change_odometer":11100,"next_change_date":"2026-12-01","notes":"corrected oil"}), ("incident",801,{"notes":"corrected incident"}), ("maintenance",801,{"service_date":"2026-01-03","odometer":1150,"notes":"corrected maintenance"}), ("vehicle",801,{"color":"blue","manufacture_year":2020}), ("assignment",801,{"delivery_notes":"corrected delivery"})):
            with self.subTest(kind=kind):
                response = self.edit(self.admin,kind,rid,changes)
                self.assertEqual(response.status_code,303,response.text)
                for key,value in changes.items(): self.assertEqual(self.row(FIELDS[kind][0],rid)[key],value)

    def test_employee_edits_own_records_in_place_and_current_reading(self):
        before = self.count('vehicle_odometer_logs')
        response = self.edit(self.employee,'odometer',802,{"reading":1050,"recorded_at":"2026-01-03T12:15:00","notes":"corrected weekly"})
        self.assertEqual(response.status_code,303,response.text)
        self.assertEqual(self.count('vehicle_odometer_logs'),before)
        self.assertEqual(self.row('vehicles',801)['current_odometer'],1050)
        self.assertEqual(self.row('vehicle_oil_changes',801)['odometer'],1050)
        for kind, changes in (("oil",{"odometer":1060,"next_change_odometer":6200,"notes":"oil note"}), ("maintenance",{"odometer":1055,"notes":"service note"}), ("incident",{"notes":"incident note"})):
            response=self.edit(self.employee,kind,801,changes)
            self.assertEqual(response.status_code,303,response.text)
        page=self.employee.get('/vehicle/801')
        self.assertEqual(page.status_code,200,page.text)
        for value in ('1,060','corrected weekly','service note','oil note','incident note','6,200'):
            self.assertIn(value,page.text)
        self.assertIn('1,060',self.employee.get('/assets-custody').text)

    def test_employee_other_vehicle_and_other_author_forbidden(self):
        for client, vid, rid in ((self.employee,802,803),(self.other,801,802),(self.employee,801,801)):
            for method in ('get','post'):
                response=getattr(client,method)(f'/vehicle/{vid}/records/odometer/{rid}/edit',**({'data':{'reading':900}} if method=='post' else {}))
                self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.row('vehicle_odometer_logs',801)['reading'],100)
        self.assertEqual(self.count('vehicle_edit_audit'),0)

    def test_employee_forbidden_settings_assignment_and_account_link(self):
        for url in ('/vehicle/801/records/vehicle/801/edit','/vehicle/801/records/assignment/801/edit','/vehicle/801/employee-login','/vehicle/801/assign','/vehicle/801/return'):
            response=self.employee.post(url,data={'employee_id':802,'delivered_at':'2026-01-01','delivery_odometer':1300,'returned_at':'2026-01-03','return_odometer':1300})
            self.assertEqual(response.status_code,403,response.text)
        response=self.edit(self.employee,'oil',801,{'oil_interval':10000})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.row('vehicle_oil_changes',801)['oil_interval'],5000)

    def test_invalid_values_preserved_and_no_changes_or_audit(self):
        for changes in ({'reading':'-1'},{'reading':'1.5'},{'reading':'9999999999999999999999'},{'recorded_at':'2026-02-30'},{'recorded_at':'2099-01-01'}):
            response=self.edit(self.employee,'odometer',802,changes|{'notes':'احتفظ بهذه الملاحظات'})
            self.assertEqual(response.status_code,400,response.text)
            self.assertIn('احتفظ بهذه الملاحظات',response.text)
            for value in changes.values(): self.assertIn(value,response.text)
        self.assertEqual(self.row('vehicle_odometer_logs',802)['reading'],1200)
        self.assertEqual(self.count('vehicle_edit_audit'),0)

    def test_audit_contains_actor_time_before_after_and_is_admin_only(self):
        self.edit(self.employee,'maintenance',801,{'notes':'تصحيح'})
        conn=main.get_db();audit=dict(conn.execute('SELECT * FROM vehicle_edit_audit').fetchone());conn.close()
        self.assertEqual(audit['edited_by'],802)
        self.assertTrue(audit['edited_at'].endswith('+00:00'))
        self.assertEqual(json.loads(audit['old_values']),{'notes':'original'})
        self.assertEqual(json.loads(audit['new_values']),{'notes':'تصحيح'})
        self.assertIn('سجل التعديلات — للأدمن فقط',self.admin.get('/vehicle/801').text)
        self.assertNotIn('سجل التعديلات — للأدمن فقط',self.employee.get('/vehicle/801').text)
        self.assertNotIn('original',self.employee.get('/vehicle/801').text)

    def test_new_maintenance_and_edit_not_duplicate(self):
        response=self.employee.post('/vehicle/801/maintenance/new',data={'service_date':'2026-01-04','odometer':1250,'service_type':'filters','notes':'new'})
        self.assertEqual(response.status_code,303,response.text)
        conn=main.get_db();rid=conn.execute('SELECT MAX(id) FROM vehicle_maintenance').fetchone()[0];conn.close()
        count=self.count('vehicle_maintenance')
        self.assertEqual(self.edit(self.employee,'maintenance',rid,{'notes':'fixed'}).status_code,303)
        self.assertEqual(self.count('vehicle_maintenance'),count)

    def test_migration_is_repeatable_preserves_data(self):
        before=self.row('vehicle_odometer_logs',802)
        init_assets_custody_schema();init_assets_custody_schema()
        self.assertEqual(self.row('vehicle_odometer_logs',802),before)
        self.assertEqual(self.count('vehicles'),2)

    def test_revoked_assignment_blocks_previous_author(self):
        conn=main.get_db();conn.execute("UPDATE vehicle_assignments SET status='returned' WHERE id=801");conn.commit();conn.close()
        self.assertEqual(self.edit(self.employee,'odometer',802,{'reading':1100}).status_code,403)

    def test_explicit_login_link_no_name_inference_and_unique(self):
        conn=main.get_db();conn.execute('UPDATE employees SET user_id=NULL WHERE id=801');conn.commit();conn.close()
        self.assertEqual(self.edit(self.employee,'odometer',802,{'reading':1100}).status_code,403)
        self.assertEqual(self.admin.post('/vehicle/801/employee-login',data={'user_id':803}).status_code,400)
        self.assertEqual(self.admin.post('/vehicle/801/employee-login',data={'user_id':802}).status_code,303)
        self.assertEqual(self.edit(self.employee,'odometer',802,{'reading':1100}).status_code,303)

    def test_date_change_recalculates_latest_reading(self):
        self.assertEqual(self.edit(self.employee,'odometer',802,{'recorded_at':'2025-12-31T12:00:00'}).status_code,303)
        self.assertEqual(self.row('vehicles',801)['current_odometer'],100)

    def test_duplicate_plate_retains_form(self):
        response=self.edit(self.admin,'vehicle',801,{'plate_number':'802','color':'فضي'})
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('فضي',response.text)
        self.assertEqual(self.row('vehicles',801)['plate_number'],'801')

    def test_existing_handover_return_reports_and_employee_assets(self):
        self.edit(self.employee,'maintenance',801,{'notes':'corrected report'})
        response=self.admin.post('/vehicle/801/return',data={'returned_at':'2026-01-05','return_odometer':1300,'return_notes':'good'})
        self.assertEqual(response.status_code,303,response.text)
        for path in ('/vehicle/801/custody.pdf?assignment_id=801','/vehicle/801/assignment/801/final-report.pdf','/employee/801'):
            response=self.admin.get(path)
            self.assertEqual(response.status_code,200,response.text[:200] if 'pdf' not in path else '')
            if '.pdf' in path: self.assertTrue(response.content.startswith(b'%PDF'))
        response=self.admin.post('/employee/801/assets',data={'asset_type':'phone','delivered_at':'2026-01-01','notes':'unchanged'})
        self.assertEqual(response.status_code,303,response.text)
        response=self.admin.post('/vehicle/801/assign',data={'employee_id':802,'delivered_at':'2026-01-06','delivery_odometer':1400})
        self.assertEqual(response.status_code,303,response.text)

    def test_cross_vehicle_record_id_and_unknown_record(self):
        self.assertEqual(self.edit(self.admin,'odometer',802,{'reading':1100},vid=802).status_code,404)
        self.assertEqual(self.admin.get('/vehicle/801/records/users/802/edit').status_code,404)

    def test_daily_log_employee_can_correct_only_assigned_vehicle(self):
        conn = main.get_db()
        conn.execute("INSERT INTO user_company_access(user_id,company,section) VALUES(802,'works','daily_log')")
        conn.commit(); conn.close()
        self.addCleanup(self.clear_daily_log_access)
        self.assertEqual(self.employee.get('/assets-custody').status_code, 200)
        self.assertEqual(self.edit(self.employee, 'odometer', 802, {'reading': 1100}).status_code, 303)
        self.assertEqual(self.employee.get('/vehicle/802').status_code, 403)
        self.assertEqual(self.employee.get('/vehicle/801/records/vehicle/801/edit').status_code, 403)

    def clear_daily_log_access(self):
        conn = main.get_db()
        conn.execute('DELETE FROM user_company_access WHERE user_id=802')
        conn.commit(); conn.close()

    def test_employee_vehicle_entry_and_arabic_audit_labels(self):
        self.assertEqual(self.employee.get('/').headers['location'], '/assets-custody')
        self.assertIn('سياراتي المسلّمة', self.employee.get('/portal').text)
        self.edit(self.employee, 'maintenance', 801, {'notes': 'تصحيح'})
        page = self.admin.get('/vehicle/801').text
        self.assertIn('ملاحظات الصيانة', page)
        self.assertNotIn('<td>notes</td>', page)
        self.assertIn('تعديل الصيانة', self.employee.get('/vehicle/801/records/maintenance/801/edit').text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
