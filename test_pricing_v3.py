"""Pricing v3 checks against an isolated SQLite database and temporary PDFs."""
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class PricingV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp_root = Path(__file__).resolve().parent / "tmp"
        temp_root.mkdir(exist_ok=True)
        cls.temp = SimpleNamespace(name=tempfile.mkdtemp(prefix="pricing_v3_", dir=temp_root))
        cls.previous_db = os.environ.get("URBANRISE_DB_PATH")
        os.environ["URBANRISE_DB_PATH"] = str(Path(cls.temp.name) / "test.sqlite")
        import main
        cls.main = main
        cls.previous_cwd = os.getcwd()
        os.chdir(cls.temp.name)
        conn = main.get_db()
        cls.user = conn.execute("SELECT 1 AS id, 'admin' AS role, 'works' AS company").fetchone()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.previous_cwd)
        if cls.previous_db is None:
            os.environ.pop("URBANRISE_DB_PATH", None)
        else:
            os.environ["URBANRISE_DB_PATH"] = cls.previous_db
        print(f"Pricing v3 test files retained: {cls.temp.name}")

    def setUp(self):
        for name in ("ensure_company_access", "ensure_not_works_partner_write"):
            patcher = patch.object(self.main, name, return_value=self.user)
            patcher.start()
            self.addCleanup(patcher.stop)

    def quote(self, vat=1, version=3):
        conn = self.main.get_db()
        try:
            cur = conn.execute("INSERT INTO quotes(company,client,pricing_version,vat_enabled,overhead_rate,profit_rate,vat_rate,issued_at) VALUES('works','عميل',?,?,19,15,15,'2026-11-02')", (version, vat))
            qid = cur.lastrowid
            conn.commit()
            return qid
        finally:
            conn.close()

    def test_customer_sale_allocation(self):
        sell = self.main.customer_sale_items
        one = sell([dict(qty=Decimal("1"), unit_price=Decimal("168067.22"), line_total=Decimal("168067.22"))], Decimal("200000"))
        self.assertEqual((one[0]["unit_price"], one[0]["line_total"]), (Decimal("200000.00"), Decimal("200000")))
        rows = sell([dict(qty=Decimal("1"), line_total=Decimal("1")), dict(qty=Decimal("1"), line_total=Decimal("2")), dict(qty=Decimal("1"), line_total=Decimal("0"))], Decimal("10"))
        self.assertEqual([r["line_total"] for r in rows], [Decimal("3.33"), Decimal("6.67"), Decimal("0")])
        fractional = sell([dict(qty=Decimal("1.5"), line_total=Decimal("10"))], Decimal("20"))[0]
        self.assertEqual(fractional["line_total"], Decimal("20"))
        self.assertEqual(fractional["unit_price_display"], "13.3333")
        with self.assertRaisesRegex(ValueError, "لا توجد بنود مسعّرة"):
            sell([dict(qty=Decimal("1"), line_total=Decimal("0"))], Decimal("1"))

    def test_works_contract_50_pdf_from_other_working_directory(self):
        qid = self.quote(version=1)
        conn = self.main.get_db()
        try:
            conn.execute("INSERT INTO quote_items(quote_id,description,qty,unit_price) VALUES(?,?,?,?)", (qid, "عمل اختبار", 1, 1000))
            conn.execute("INSERT INTO quote_payments(quote_id,title,percentage) VALUES(?,?,?)", (qid, "الدفعة الأولى", 100))
            conn.execute("INSERT INTO contracts(id,company,quote_id,contract_total,contract_subtotal_before_vat,contract_vat_amount,financial_snapshot_locked) VALUES(50,'works',?,1000,1000,0,1)", (qid,))
            conn.execute("INSERT INTO projects(company,name,contract_id,contract_value) VALUES('works','مشروع 50',50,1000)")
            conn.commit()
        finally:
            conn.close()
        response = self.main.contract_report(None, 50, "works")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertGreater(Path(response.path).stat().st_size, 1000)
        self.assertEqual(Path(response.path).parent, Path(self.main.__file__).resolve().parent / "pdfs")

    def test_sale_views_and_frozen_totals(self):
        qid = self.quote()
        self.main.add_item(None, qid, "works", "بند بيع", "1", "168067.22")
        financials = self.main.calculate_quote_financials(qid)
        conn = self.main.get_db()
        try:
            quote = conn.execute("SELECT * FROM quotes WHERE id=?", (qid,)).fetchone()
            raw, payments = self.main.quote_source_rows(conn, quote)
            expected = self.main.customer_sale_items(raw, financials["subtotal_before_vat"])[0]
        finally:
            conn.close()
        screen = self.main.quote_detail(None, qid, "works")
        self.assertIn(self.main.format_currency(expected["line_total"]), screen if isinstance(screen, str) else screen.body.decode())
        pdf = self.main.quote_report(None, qid, "works")
        self.assertGreater(Path(pdf.path).stat().st_size, 1000)
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 422)
        self.main.add_payment(None, qid, "works", "أولى", "33.33")
        self.main.add_payment(None, qid, "works", "أخيرة", "66.67")
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 303)
        conn = self.main.get_db()
        try:
            contract = conn.execute("SELECT * FROM contracts WHERE quote_id=?", (qid,)).fetchone()
            project = conn.execute("SELECT * FROM projects WHERE contract_id=?", (contract["id"],)).fetchone()
            before = (project["contract_value_halalas"], tuple(conn.execute("SELECT amount_halalas FROM contract_v3_payments WHERE contract_id=? ORDER BY id", (contract["id"],)).fetchall()))
            frozen, frozen_payments = self.main.contract_source_rows(conn, contract)
            contract_financials = self.main.get_contract_effective_financials(conn, contract)
            visible = self.main.customer_sale_items(frozen, contract_financials["subtotal_before_vat"])
            self.assertEqual(sum((r["line_total"] for r in visible), Decimal("0")), contract_financials["subtotal_before_vat"])
            self.assertEqual(sum((p["amount"] for p in frozen_payments), Decimal("0")), contract_financials["grand_total_with_vat"])
        finally:
            conn.close()
        contract_screen = self.main.contract_detail(None, contract["id"], "works")
        self.assertIn(self.main.format_currency(visible[0]["line_total"]), contract_screen if isinstance(contract_screen, str) else contract_screen.body.decode())
        contract_pdf = self.main.contract_report(None, contract["id"], "works")
        self.assertGreater(Path(contract_pdf.path).stat().st_size, 1000)
        conn = self.main.get_db()
        try:
            after = (conn.execute("SELECT contract_value_halalas FROM projects WHERE id=?", (project["id"],)).fetchone()[0], tuple(conn.execute("SELECT amount_halalas FROM contract_v3_payments WHERE contract_id=? ORDER BY id", (contract["id"],)).fetchall()))
            self.assertEqual(before, after)
        finally:
            conn.close()

    def test_vat_adjustment_and_no_vat(self):
        for vat in (1, 0):
            qid = self.quote(vat)
            self.assertEqual(self.main.add_item(None, qid, "works", "بند تجريبي", "1.5", "101.25").status_code, 303)
            f = self.main.calculate_quote_financials(qid)
            subtotal = Decimal(str(f["subtotal_before_vat"]))
            raw = sum(Decimal(str(f[key])) for key in ("items_total", "overhead_amount", "profit_amount"))
            self.assertEqual(raw + Decimal(str(f["pricing_adjustment"])), subtotal)
            self.assertEqual(subtotal % (20 if vat else 1), 0)
            self.assertEqual(Decimal(str(f["vat_amount"])), subtotal * (Decimal("0.15") if vat else 0))
            conn = self.main.get_db()
            try:
                self.assertEqual(conn.execute("SELECT line_total_halalas FROM quote_v3_items WHERE quote_item_id IN (SELECT id FROM quote_items WHERE quote_id=?)", (qid,)).fetchone()[0], 15188)
            finally:
                conn.close()

    def test_rate_change_stays_decimal(self):
        qid = self.quote()
        self.main.add_item(None, qid, "works", "تغيير نسبة", "1", "100.01")
        self.assertEqual(self.main.update_quote_profit_rate(None, qid, "works", "12.35").status_code, 303)
        f = self.main.calculate_quote_financials(qid)
        self.assertEqual(f["profit_rate"], Decimal("12.35"))
        conn = self.main.get_db()
        try:
            row = conn.execute("SELECT profit_rate_decimal,typeof(profit_halalas),typeof(total_halalas) FROM quote_v3_financials WHERE quote_id=?", (qid,)).fetchone()
            self.assertEqual(tuple(row), ("12.35", "integer", "integer"))
        finally:
            conn.close()

    def test_unit_price_precision_and_project_lock(self):
        qid = self.quote()
        self.assertEqual(self.main.add_item(None, qid, "works", "غير صالح", "1", "1.001").status_code, 422)
        self.main.add_item(None, qid, "works", "صالح", "1", "1.01")
        self.main.add_payment(None, qid, "works", "كاملة", "100")
        self.main.convert_to_contract(None, qid, "works")
        conn = self.main.get_db()
        try:
            project = conn.execute("SELECT * FROM projects WHERE contract_id=(SELECT id FROM contracts WHERE quote_id=?)", (qid,)).fetchone()
            self.assertIsNone(project["contract_value"])
            self.assertEqual(self.main.render_project_structured_fields("works", project).count("readonly"), 1)
        finally:
            conn.close()

    def test_legacy_rows_unchanged(self):
        qid = self.quote(version=2)
        conn = self.main.get_db()
        try:
            conn.execute("INSERT INTO quote_items(quote_id,description,qty,unit_price) VALUES(?,?,?,?)", (qid, "قديم", 1.25, 777.77))
            contract_id = conn.execute("INSERT INTO contracts(company,quote_id,contract_total,contract_subtotal_before_vat,contract_vat_amount,financial_snapshot_locked) VALUES('works',?,?,?, ?,1)", (qid, 1200.25, 1000.25, 200)).lastrowid
            project_id = conn.execute("INSERT INTO projects(company,contract_id,contract_value) VALUES('works',?,?)", (contract_id, 1200.25)).lastrowid
            conn.commit()
            before = tuple(conn.execute("SELECT contract_total,contract_subtotal_before_vat,contract_vat_amount FROM contracts WHERE id=?", (contract_id,)).fetchone())
            old_total = self.main.calculate_project_contract_total(conn, conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
        finally:
            conn.close()
        new_id = self.quote()
        self.main.add_item(None, new_id, "works", "جديد", "1", "15")
        conn = self.main.get_db()
        try:
            self.assertEqual(tuple(conn.execute("SELECT contract_total,contract_subtotal_before_vat,contract_vat_amount FROM contracts WHERE id=?", (contract_id,)).fetchone()), before)
            self.assertEqual(self.main.calculate_project_contract_total(conn, conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()), old_total)
        finally:
            conn.close()

    def test_payments_and_locked_contract(self):
        qid = self.quote()
        self.main.add_item(None, qid, "works", "أصل", "2", "123.45")
        self.main.add_payment(None, qid, "works", "أولى", "33.33")
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 422)
        conn = self.main.get_db()
        try:
            partial = self.main.quote_v3_payment_rows(conn, qid)
        finally:
            conn.close()
        self.assertEqual(self.main.quote_payment_amounts("100", partial), [Decimal("33.33")])
        self.main.add_payment(None, qid, "works", "أخيرة", "70")
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 422)
        conn = self.main.get_db()
        try:
            conn.execute("UPDATE quote_v3_payments SET percentage_decimal='66.67' WHERE quote_payment_id IN (SELECT id FROM quote_payments WHERE quote_id=? AND title='أخيرة')", (qid,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 303)
        conn = self.main.get_db()
        try:
            contract = conn.execute("SELECT * FROM contracts WHERE quote_id=?", (qid,)).fetchone()
            items, payments = self.main.contract_source_rows(conn, contract)
            self.assertEqual(items[0]["description"], "أصل")
            snapshot = conn.execute("SELECT * FROM contract_v3_financials WHERE contract_id=?", (contract["id"],)).fetchone()
            self.assertEqual(snapshot["overhead_rate_decimal"], "19.0")
            self.assertEqual(snapshot["profit_rate_decimal"], "15.0")
            self.assertEqual(sum((self.main.to_halalas(p["amount"]) for p in payments)), snapshot["total_halalas"])
            project = conn.execute("SELECT * FROM projects WHERE contract_id=?", (contract["id"],)).fetchone()
            self.assertEqual(project["contract_value_halalas"], snapshot["total_halalas"])
            self.assertIsNone(project["contract_value"])
            self.assertIsNone(contract["contract_total"])
            self.assertEqual(self.main.calculate_project_contract_total(conn, project), self.main.from_halalas(snapshot["total_halalas"]))
            groups = self.main.works_payment_groups(conn, project)
            self.assertEqual(groups[0]["total"], self.main.from_halalas(snapshot["total_halalas"]))
            self.assertEqual(sum((p["amount"] for p in groups[0]["payments"]), Decimal("0")), groups[0]["total"])
            from client_portal import _project_context
            portal = _project_context(conn, project["id"])
            self.assertEqual(portal["contract_value"], groups[0]["total"])
            self.assertEqual(sum((p["amount"] for p in portal["payments"]), Decimal("0")), groups[0]["total"])
            report_path, _ = self.main.build_project_expenses_report_pdf(project, [], portal["contract_value"], collections=[], company="works")
            self.assertGreater(Path(report_path).stat().st_size, 1000)
            types = conn.execute("SELECT typeof(unit_price_halalas),typeof(line_total_halalas) FROM quote_v3_items LIMIT 1").fetchone()
            self.assertEqual(tuple(types), ("integer", "integer"))
            self.assertEqual(tuple(conn.execute("SELECT qty,unit_price FROM quote_items WHERE quote_id=?", (qid,)).fetchone()), (None, None))
            self.assertIsNone(conn.execute("SELECT percentage FROM quote_payments WHERE quote_id=?", (qid,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT typeof(total_halalas) FROM contract_v3_financials WHERE contract_id=?", (contract["id"],)).fetchone()[0], "integer")
        finally:
            conn.close()
        self.assertEqual(self.main.add_item(None, qid, "works", "مرفوض", "1", "1").status_code, 409)
        self.assertEqual(self.main.update_quote_profit_rate(None, qid, "works", 20).status_code, 409)
        quote_pdf = self.main.quote_report(None, qid, "works")
        conn = self.main.get_db()
        try:
            conn.execute("UPDATE quote_items SET description='متغير' WHERE quote_id=?", (qid,))
            conn.execute("UPDATE quote_v3_items SET unit_price_halalas=1 WHERE quote_item_id IN (SELECT id FROM quote_items WHERE quote_id=?)", (qid,))
            conn.execute("UPDATE quote_v3_payments SET percentage_decimal='1' WHERE quote_payment_id IN (SELECT id FROM quote_payments WHERE quote_id=?)", (qid,))
            conn.commit()
            frozen_items, frozen_payments = self.main.contract_source_rows(conn, contract)
            self.assertEqual(frozen_items[0]["description"], "أصل")
            self.assertEqual(Decimal(frozen_payments[0]["percentage"]), Decimal("33.33"))
        finally:
            conn.close()
        contract_pdf = self.main.contract_report(None, contract["id"], "works")
        self.assertGreater(Path(quote_pdf.path).stat().st_size, 1000)
        self.assertGreater(Path(contract_pdf.path).stat().st_size, 1000)
        from pypdf import PdfReader
        expected_total = self.main.format_currency(self.main.from_halalas(snapshot["total_halalas"]))
        for path in (quote_pdf.path, contract_pdf.path):
            pages = PdfReader(path).pages
            self.assertTrue(pages)
            self.assertTrue(all((page.extract_text() or "").strip() for page in pages))
            self.assertIn(expected_total, " ".join(page.extract_text(extraction_mode="layout") or "" for page in pages))
            font_objects = [font.get_object() for page in pages for font in page["/Resources"]["/Font"].get_object().values()]
            embedded_names = {str(font.get("/BaseFont", "")) for font in font_objects
                              if font.get("/FontDescriptor") and font["/FontDescriptor"].get_object().get("/FontFile2")}
            self.assertTrue(any("IBMPlexSansArabic" in name and "Regular" in name for name in embedded_names))
            self.assertTrue(any("IBMPlexSansArabic" in name and "SemiBold" in name for name in embedded_names))
        qa_dir = os.environ.get("PRICING_QA_DIR")
        if qa_dir:
            Path(qa_dir).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(quote_pdf.path, Path(qa_dir) / "quote_v3.pdf")
            shutil.copyfile(contract_pdf.path, Path(qa_dir) / "contract_v3.pdf")

    def test_long_description_pdf(self):
        qid = self.quote()
        self.main.add_item(None, qid, "works", "وصف طويل " * 1000, "1", "100")
        self.main.add_payment(None, qid, "works", "نهائية", "100")
        path = self.main.quote_report(None, qid, "works").path
        from pypdf import PdfReader
        self.assertTrue(all((page.extract_text() or "").strip() for page in PdfReader(path).pages))
        self.assertEqual(self.main.convert_to_contract(None, qid, "works").status_code, 303)
        conn = self.main.get_db()
        try:
            contract_id = conn.execute("SELECT id FROM contracts WHERE quote_id=?", (qid,)).fetchone()[0]
        finally:
            conn.close()
        path = self.main.contract_report(None, contract_id, "works").path
        self.assertTrue(all((page.extract_text() or "").strip() for page in PdfReader(path).pages))


if __name__ == "__main__":
    unittest.main()
