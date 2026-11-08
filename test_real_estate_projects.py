"""Test selected real-estate projects directly against a disposable SQLite database."""
import asyncio
import io
import re
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing

from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from PIL import Image
from starlette.datastructures import FormData, Headers, UploadFile

import featured_projects as works
import real_estate_projects as estate


def request(app, path, method="GET", fields=None, files=None, role=None, session=None):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": method, "path": path, "headers": [],
             "query_string": b"", "session": session if session is not None else {}, "app": app,
             "server": ("test", 80), "scheme": "http", "root_path": ""}
    req = Request(scope, receive)
    items = [(key, item) for key, value in (fields or {}).items()
             for item in (value if isinstance(value, list) else [value])]
    for key, (filename, content, mime) in files or []:
        items.append((key, UploadFile(file=io.BytesIO(content), filename=filename,
                                      headers=Headers({"content-type": mime}))))
    async def form():
        return FormData(items)
    req.form = form
    if role:
        req.state.current_user = {"role": role}
    return req


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if route.path == path)


def run():
    tmp_root = Path("tmp").resolve()
    assert tmp_root.is_relative_to(Path.cwd().resolve())
    tmp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="urbanrise-estate-test-", dir=tmp_root) as temporary:
        root = Path(temporary)
        db_path = root / "test.sqlite"

        def get_temp_db():
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            return connection

        estate.get_db = works.get_db = get_temp_db
        estate.UPLOAD_DIR = root / "estate-images"
        works.UPLOAD_DIR = root / "works-images"
        app = FastAPI()
        templates = Jinja2Templates(directory="templates")
        works.register_featured_projects(app, templates)
        estate.register_real_estate_projects(app, templates)
        index = endpoint(app, "/admin/real-estate-projects")
        save_route = endpoint(app, "/admin/real-estate-projects/save")
        delete_route = endpoint(app, "/admin/real-estate-projects/{project_id}/delete")
        detail = endpoint(app, "/real-estate/projects/{slug}")
        session = {}

        for role in (None, "employee"):
            try:
                index(request(app, "/admin/real-estate-projects", role=role, session=session))
                raise AssertionError("Non-admin reached project administration")
            except HTTPException as error:
                assert error.status_code == 403

        page = index(request(app, "/admin/real-estate-projects", role="admin", session=session))
        html = page.body.decode("utf-8")
        assert "مشاريع مختارة للتطوير العقاري" in html
        token = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        fields = {"csrf": token, "name": "مشروع عقاري مؤقت", "stage": estate.STAGES[1],
                  "location": "الرياض", "summary": "وصف مشروع مؤقت", "description": "تفاصيل مؤقتة",
                  "sort_order": "2", "status": "draft", "featured": "on"}
        try:
            asyncio.run(save_route(request(app, "/admin/real-estate-projects/save", "POST",
                                           {**fields, "csrf": "invalid"}, role="admin", session=session)))
            raise AssertionError("Invalid CSRF token was accepted")
        except HTTPException as error:
            assert error.status_code == 403

        def save(changes=None, files=None):
            req = request(app, "/admin/real-estate-projects/save", "POST", {**fields, **(changes or {})},
                          files, "admin", session)
            return asyncio.run(save_route(req))

        assert save().status_code == 303
        with closing(get_temp_db()) as db:
            project = db.execute("SELECT * FROM real_estate_projects").fetchone()
            project_id, slug = project["id"], project["slug"]
            assert project["location"] == "الرياض" and project["featured"] == 1
            assert db.execute("SELECT COUNT(*) FROM featured_projects").fetchone()[0] == 0
        assert "حذف المشروع وصوره نهائيًا" in index(request(app, "/admin/real-estate-projects", role="admin", session=session)).body.decode("utf-8")
        assert estate.published_projects() == []
        for operation in (lambda: detail(request(app, f"/real-estate/projects/{slug}"), slug),
                          lambda: save({"project_id": str(project_id), "status": "published"})):
            try:
                operation()
                raise AssertionError("Draft project became public without a cover")
            except HTTPException as error:
                assert error.status_code in (400, 404)

        image = io.BytesIO()
        Image.new("RGB", (640, 420), "#c5ab7c").save(image, "PNG")
        picture = image.getvalue()
        result = save({"project_id": str(project_id), "status": "published", "new_alt_0": "غلاف العقار",
                       "new_alt_1": "صورة إضافية"},
                      [("cover_image", ("cover.png", picture, "image/png")),
                       ("gallery_images", ("gallery.png", picture, "image/png"))])
        assert result.status_code == 303
        published = estate.published_projects()
        assert len(published) == 1 and published[0]["slug"] == slug and published[0]["featured"] == 1
        assert works.published_projects() == []
        detail_html = detail(request(app, f"/real-estate/projects/{slug}"), slug).body.decode("utf-8")
        assert f"https://urbanrise.sa/real-estate/projects/{slug}" in detail_html
        assert "صور المشروع" in detail_html and "الرياض" in detail_html
        with closing(get_temp_db()) as db:
            images = db.execute("SELECT * FROM real_estate_project_images WHERE project_id=?", (project_id,)).fetchall()
            assert len(images) == 2 and sum(row["is_cover"] for row in images) == 1
            old_cover = next(row for row in images if row["is_cover"])
            new_cover = next(row for row in images if not row["is_cover"])
            db.execute("INSERT INTO featured_projects(name,slug,service,summary,status) VALUES(?,?,?,?,?)",
                       ("مشروع مقاولات", "works-only", works.SERVICES[0], "وصف المقاولات", "published"))
            works_id = db.execute("SELECT id FROM featured_projects WHERE slug='works-only'").fetchone()[0]
            db.execute("INSERT INTO featured_project_images(project_id,image_url,thumbnail_url,alt,is_cover) VALUES(?,?,?,?,1)",
                       (works_id, "/works/media/featured-projects/" + "a"*32 + ".webp",
                        "/works/media/featured-projects/" + "a"*32 + "_thumb.webp", "غلاف"))
            db.commit()
        assert len(works.published_projects()) == len(estate.published_projects()) == 1
        try:
            detail(request(app, "/real-estate/projects/works-only"), "works-only")
            raise AssertionError("Works project appeared in real estate")
        except HTTPException as error:
            assert error.status_code == 404

        assert save({"project_id": str(project_id), "status": "published",
                     "remove_image": str(old_cover["id"]),
                     "cover_choice": f'existing-{new_cover["id"]}',
                     "existing_image": str(new_cover["id"]),
                     f'existing_alt_{new_cover["id"]}': "غلاف جديد",
                     f'existing_order_{new_cover["id"]}': "1"}).status_code == 303
        with closing(get_temp_db()) as db:
            remaining = db.execute("SELECT id,is_cover,alt FROM real_estate_project_images WHERE project_id=?", (project_id,)).fetchall()
            assert len(remaining) == 1 and tuple(remaining[0]) == (new_cover["id"], 1, "غلاف جديد")

        assert save({"project_id": str(project_id), "name": "مشروع عقاري معدل",
                     "location": "الرياض - النرجس", "stage": estate.STAGES[0], "status": "published"}).status_code == 303
        with closing(get_temp_db()) as db:
            edited = db.execute("SELECT name,location,stage,slug FROM real_estate_projects WHERE id=?", (project_id,)).fetchone()
            assert tuple(edited) == ("مشروع عقاري معدل", "الرياض - النرجس", estate.STAGES[0], slug)
        assert save({"project_id": str(project_id), "status": "draft"}).status_code == 303
        assert estate.published_projects() == [] and len(works.published_projects()) == 1
        response = asyncio.run(delete_route(request(app, f"/admin/real-estate-projects/{project_id}/delete",
                                                    "POST", {"csrf": token}, role="admin", session=session), project_id))
        assert response.status_code == 303
        with closing(get_temp_db()) as db:
            assert db.execute("SELECT COUNT(*) FROM real_estate_projects").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM featured_projects").fetchone()[0] == 1
        migration_path = root / "migration.sqlite"
        with closing(sqlite3.connect(migration_path)) as db:
            db.execute("CREATE TABLE real_estate_projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, stage TEXT NOT NULL, summary TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', featured INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.commit()
        def get_migration_db():
            connection = sqlite3.connect(migration_path)
            connection.row_factory = sqlite3.Row
            return connection
        estate.get_db = get_migration_db
        estate.init_schema()
        with closing(get_migration_db()) as db:
            assert "location" in {row[1] for row in db.execute("PRAGMA table_info(real_estate_projects)")}
        print("Real-estate CRUD, publishing, images, permissions, and Works separation passed")


if __name__ == "__main__":
    run()
