"""Exercise Care project publishing against an isolated temporary database."""
import io
import re
import sqlite3
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from PIL import Image
from starlette.datastructures import FormData, Headers, UploadFile

import care_projects as care


def request(app, path, fields=None, files=None, session=None):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "POST" if fields else "GET", "path": path,
             "headers": [], "query_string": b"", "session": session if session is not None else {}, "app": app,
             "server": ("test", 80), "scheme": "http", "root_path": ""}
    req = Request(scope, receive)
    req.state.current_user = {"role": "admin"}
    items = [(key, item) for key, value in (fields or {}).items()
             for item in (value if isinstance(value, list) else [value])]
    for key, filename, content in files or []:
        upload = UploadFile(file=io.BytesIO(content), filename=filename,
                            headers=Headers({"content-type": "image/png"}))
        async def read(size=-1, file=upload.file):
            return file.read(size)
        upload.read = read
        items.append((key, upload))

    async def form():
        return FormData(items)

    req.form = form
    return req


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if route.path == path)


def run_async(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Unexpected asynchronous wait in test")


def run():
    Path("tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir="tmp") as root_name:
        root = Path(root_name)
        db_path = root / "test.sqlite"

        def get_temp_db():
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            return db

        care.get_db = get_temp_db
        care.UPLOAD_DIR = root / "care-images"
        app = FastAPI()
        care.register_care_projects(app, Jinja2Templates(directory="templates"))
        care.init_schema()
        save = endpoint(app, "/admin/care-projects/save")
        detail = endpoint(app, "/works/care/projects/{slug}")
        index = endpoint(app, "/works/care/projects")
        session = {}
        token = care.csrf_token(request(app, "/admin/care-projects", session=session))
        fields = {"csrf": token, "name": "مشروع اختبار", "project_type": care.TYPES[0],
                  "project_state": "قائم", "city": "الرياض", "location": "الملقا", "units": "12",
                  "summary": "وصف قصير", "description": "تفاصيل", "services": "نظافة وصيانة",
                  "sort_order": "5", "status": "draft", "featured": "on"}
        run_async(save(request(app, "/admin/care-projects/save", fields, session=session)))
        assert care.published_projects() == []
        with care.database() as db:
            row = db.execute("SELECT * FROM care_projects").fetchone()
            project_id, slug = row["id"], row["slug"]
            assert db.execute("SELECT COUNT(*) FROM care_project_images").fetchone()[0] == 0
        try:
            detail(request(app, "/works/care/projects/" + slug), slug)
            raise AssertionError("Draft appeared publicly")
        except HTTPException as exc:
            assert exc.status_code == 404
        image = Image.new("RGB", (40, 30), "#468780")
        stream = io.BytesIO()
        image.save(stream, "PNG")
        portrait = Image.new("RGB", (30, 60), "#244b46")
        portrait_stream = io.BytesIO()
        portrait.save(portrait_stream, "PNG")
        files = [("cover_image", "cover.png", stream.getvalue()),
                 ("gallery_images", "portrait.png", portrait_stream.getvalue()),
                 ("gallery_images", "two.png", stream.getvalue())]
        run_async(save(request(app, "/admin/care-projects/save",
                                 {**fields, "project_id": str(project_id), "status": "published",
                                  "sort_order": "2"}, files, session)))
        assert len(care.published_projects()) == 1
        assert len(care.published_projects(featured_only=True)) == 1
        detail_html = detail(request(app, "/works/care/projects/" + slug), slug).body.decode()
        assert "مشروع اختبار" in detail_html
        assert 'id="care-main-image"' in detail_html and detail_html.count('<button class="care-thumbnail') == 3
        assert 'id="care-image-lightbox"' in detail_html
        assert 'name="units"' not in detail_html
        with care.database() as db:
            db.execute("UPDATE care_projects SET units=0 WHERE id=?", (project_id,))
        assert "0 وحدة" not in detail(request(app, "/works/care/projects/" + slug), slug).body.decode()
        assert "مشروع اختبار" in index(request(app, "/works/care/projects")).body.decode()
        with care.database() as db:
            images = db.execute("SELECT * FROM care_project_images ORDER BY sort_order,id").fetchall()
            assert len(images) == 3
            with Image.open(care.UPLOAD_DIR / images[0]["image_url"].rsplit("/", 1)[-1]) as saved:
                assert saved.width > saved.height
            with Image.open(care.UPLOAD_DIR / images[1]["image_url"].rsplit("/", 1)[-1]) as saved:
                assert saved.height > saved.width
            assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('featured_projects','real_estate_projects')").fetchone()[0] == 0
        run_async(save(request(app, "/admin/care-projects/save",
                                 {**fields, "project_id": str(project_id), "status": "published",
                                  "name": "مشروع معدل", "sort_order": "1",
                                  "existing_image": [str(i["id"]) for i in images],
                                  **{f'existing_alt_{i["id"]}': "صورة" for i in images},
                                  **{f'existing_order_{i["id"]}': str(3 - n) for n, i in enumerate(images)}},
                                 session=session)))
        assert care.published_projects()[0]["name"] == "مشروع معدل"
        assert care.published_projects()[0]["sort_order"] == 1
        with care.database() as db:
            assert [i["sort_order"] for i in db.execute("SELECT * FROM care_project_images ORDER BY sort_order")] == [1, 2, 3]
        run_async(save(request(app, "/admin/care-projects/save",
                                 {**fields, "project_id": str(project_id), "status": "draft"}, session=session)))
        assert not care.published_projects()
        care.init_schema()


if __name__ == "__main__":
    run()
    print("Care project tests passed")
