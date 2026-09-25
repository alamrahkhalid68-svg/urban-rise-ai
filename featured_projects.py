"""Independent, manually curated public Works projects."""
import hmac
import io
import re
import secrets
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile

from auth import get_current_user, is_admin
from db import DB_PATH, get_db

SERVICES = ("ترميم المنازل", "التشطيب والتعديلات الداخلية", "البناء والملاحق", "الصيانة والنظافة", "خدمات اتحاد الملاك")
UPLOAD_DIR = Path(DB_PATH).resolve().parent / "featured-projects"
PUBLIC_DIR = "/works/media/featured-projects/"
MAX_BYTES = 8 * 1024 * 1024
IMAGE_RE = re.compile(r"^/works/media/featured-projects/[0-9a-f]{32}(?:_thumb)?\.webp$")
FILE_RE = re.compile(r"^[0-9a-f]{32}(?:_thumb)?\.webp$")


@contextmanager
def database():
    conn = get_db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema():
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS featured_projects (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL, summary TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','published')),
            featured INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS featured_project_images (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES featured_projects(id) ON DELETE CASCADE,
            image_url TEXT NOT NULL, thumbnail_url TEXT NOT NULL, alt TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, is_cover INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_featured_projects_public ON featured_projects(status, sort_order, id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_featured_images_project ON featured_project_images(project_id, sort_order, id)")


def published_projects():
    with database() as db:
        return [dict(row) for row in db.execute("""SELECT p.*, i.image_url cover_url, i.thumbnail_url cover_thumb, i.alt cover_alt
            FROM featured_projects p JOIN featured_project_images i ON i.project_id=p.id AND i.is_cover=1
            WHERE p.status='published' ORDER BY p.featured DESC, p.sort_order, p.id""").fetchall()]


def admin(request):
    user = getattr(request.state, "current_user", None) or get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(403, "هذه الصفحة للأدمن فقط")
    return user


def csrf_token(request):
    return request.session.setdefault("featured_projects_csrf", secrets.token_urlsafe(32))


def check_csrf(request, form):
    if not hmac.compare_digest(str(form.get("csrf", "")), csrf_token(request)):
        raise HTTPException(403, "رمز الحماية غير صالح")


def validate(form):
    name = str(form.get("name", "")).strip()
    service = str(form.get("service", "")).strip()
    summary = str(form.get("summary", "")).strip()
    description = str(form.get("description", "")).strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(400, "أدخل اسم مشروع صالحًا")
    if service not in SERVICES or not (1 <= len(summary) <= 350) or len(description) > 5000:
        raise HTTPException(400, "نوع الخدمة أو الوصف غير صالح")
    try:
        order = int(form.get("sort_order", "0"))
    except ValueError:
        raise HTTPException(400, "الترتيب غير صالح")
    if not -100000 <= order <= 100000:
        raise HTTPException(400, "الترتيب خارج النطاق")
    status = str(form.get("status", "draft"))
    if status not in {"draft", "published"}:
        raise HTTPException(400, "حالة المشروع غير صالحة")
    return name, service, summary, description, order, int(form.get("featured") == "on"), status


def make_slug(db, name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:80].strip("-")
    if not base:
        base = f"project-{db.execute('SELECT COALESCE(MAX(id),0)+1 FROM featured_projects').fetchone()[0]}"
    slug = base
    suffix = 2
    while db.execute("SELECT 1 FROM featured_projects WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def safe_unlink(url):
    if IMAGE_RE.fullmatch(url or ""):
        path = UPLOAD_DIR / url.rsplit("/", 1)[-1]
        if path.resolve().is_relative_to(UPLOAD_DIR.resolve()):
            path.unlink(missing_ok=True)


async def save_image(upload: UploadFile):
    if not upload.filename or Path(upload.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "صيغة الصورة غير مسموحة")
    raw = await upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(400, "حجم الصورة يتجاوز 8 ميجابايت")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 24000000:
                raise ValueError()
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                background = Image.new("RGBA", image.size, "#f6f0e5")
                background.alpha_composite(image.convert("RGBA"))
                image = background.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            thumbnail = image.copy()
            thumbnail.thumbnail((560, 420), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, "محتوى الصورة غير صالح")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    paths = [UPLOAD_DIR / f"{stem}.webp", UPLOAD_DIR / f"{stem}_thumb.webp"]
    try:
        image.save(paths[0], "WEBP", quality=85, method=6)
        thumbnail.save(paths[1], "WEBP", quality=79, method=6)
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    return PUBLIC_DIR + paths[0].name, PUBLIC_DIR + paths[1].name


def register_featured_projects(app, templates):
    init_schema()

    @app.get("/works/media/featured-projects/{filename}")
    def media(filename: str):
        if not FILE_RE.fullmatch(filename):
            raise HTTPException(404)
        path = UPLOAD_DIR / filename
        if not path.is_file() or not path.resolve().is_relative_to(UPLOAD_DIR.resolve()):
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp")

    @app.get("/admin/featured-projects")
    def index(request: Request, edit: int = 0):
        admin(request)
        with database() as db:
            projects = [dict(row) for row in db.execute("SELECT * FROM featured_projects ORDER BY sort_order,id")]
            project_row = db.execute("SELECT * FROM featured_projects WHERE id=?", (edit,)).fetchone() if edit else None
            if edit and not project_row:
                raise HTTPException(404)
            images = [dict(row) for row in db.execute("SELECT * FROM featured_project_images WHERE project_id=? ORDER BY sort_order,id", (edit,))] if edit else []
        return templates.TemplateResponse(request, "featured_projects_admin.html", {"projects": projects, "project": dict(project_row) if project_row else None, "images": images, "services": SERVICES, "csrf": csrf_token(request)})

    @app.post("/admin/featured-projects/save")
    async def save(request: Request):
        admin(request)
        form = await request.form()
        check_csrf(request, form)
        name, service, summary, description, sort_order, featured, status = validate(form)
        try:
            project_id = int(form.get("project_id", "0"))
        except ValueError:
            raise HTTPException(400)
        if project_id < 0:
            raise HTTPException(400)
        cover_upload = form.get("cover_image")
        if not isinstance(cover_upload, UploadFile) or not cover_upload.filename:
            cover_upload = None
        gallery = [file for file in form.getlist("gallery_images") if isinstance(file, UploadFile) and file.filename]
        if len(gallery) > 10 or len(gallery) + int(bool(cover_upload)) > 10:
            raise HTTPException(400, "يمكن رفع عشر صور كحد أقصى في المرة")
        removed = set()
        for value in form.getlist("remove_image"):
            try:
                removed.add(int(value))
            except ValueError:
                raise HTTPException(400, "اختيار حذف الصورة غير صالح")
        existing_edits = {}
        for value in form.getlist("existing_image"):
            try:
                image_id = int(value)
                alt = str(form.get(f"existing_alt_{image_id}", "")).strip()
                order = int(form.get(f"existing_order_{image_id}", "0"))
            except ValueError:
                raise HTTPException(400, "ترتيب الصورة غير صالح")
            if not 1 <= len(alt) <= 180 or not -100000 <= order <= 100000:
                raise HTTPException(400, "نص الصورة أو ترتيبها غير صالح")
            existing_edits[image_id] = (alt, order)
        cover_choice = str(form.get("cover_choice", "")).strip()
        new_files = ([cover_upload] if cover_upload else []) + gallery
        new_alts = []
        for index, _ in enumerate(new_files):
            alt = str(form.get(f"new_alt_{index}", "")).strip() or name
            if len(alt) > 180:
                raise HTTPException(400, "النص البديل للصورة طويل جدًا")
            new_alts.append(alt)
        created = []
        old_files = []
        try:
            for file in new_files:
                created.append(await save_image(file))
            with database() as db:
                old_images = {row["id"]: row for row in db.execute(
                    "SELECT * FROM featured_project_images WHERE project_id=?", (project_id,))} if project_id else {}
                if project_id and not db.execute("SELECT 1 FROM featured_projects WHERE id=?", (project_id,)).fetchone():
                    raise HTTPException(404)
                if removed - old_images.keys() or existing_edits.keys() - old_images.keys():
                    raise HTTPException(400, "إحدى الصور لا تتبع هذا المشروع")
                if project_id:
                    db.execute("""UPDATE featured_projects SET name=?,service=?,summary=?,description=?,sort_order=?,featured=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                               (name, service, summary, description, sort_order, featured, status, project_id))
                else:
                    slug = make_slug(db, name)
                    project_id = db.execute("""INSERT INTO featured_projects(name,slug,service,summary,description,sort_order,featured,status)
                        VALUES(?,?,?,?,?,?,?,?)""", (name, slug, service, summary, description, sort_order, featured, status)).lastrowid
                for image_id, (alt, order) in existing_edits.items():
                    if image_id not in removed:
                        db.execute("UPDATE featured_project_images SET alt=?,sort_order=? WHERE id=?", (alt, order, image_id))
                for image_id in removed:
                    image = old_images[image_id]
                    db.execute("DELETE FROM featured_project_images WHERE id=?", (image_id,))
                    old_files.extend((image["image_url"], image["thumbnail_url"]))
                new_ids = []
                for index, ((url, thumb), alt) in enumerate(zip(created, new_alts)):
                    order_text = str(form.get(f"new_order_{index}", str(len(old_images) + index)))
                    try:
                        order = int(order_text)
                    except ValueError:
                        raise HTTPException(400, "ترتيب الصورة الجديدة غير صالح")
                    if not -100000 <= order <= 100000:
                        raise HTTPException(400, "ترتيب الصورة الجديدة خارج النطاق")
                    new_ids.append(db.execute("""INSERT INTO featured_project_images(project_id,image_url,thumbnail_url,alt,sort_order,is_cover)
                        VALUES(?,?,?,?,?,0)""", (project_id, url, thumb, alt, order)).lastrowid)
                available = set(old_images) - removed
                if cover_choice.startswith("existing-"):
                    try:
                        cover_id = int(cover_choice.removeprefix("existing-"))
                    except ValueError:
                        raise HTTPException(400, "اختيار الغلاف غير صالح")
                    if cover_id not in available:
                        raise HTTPException(400, "صورة الغلاف المختارة غير متاحة")
                elif cover_choice.startswith("new-"):
                    try:
                        cover_id = new_ids[int(cover_choice.removeprefix("new-"))]
                    except (ValueError, IndexError):
                        raise HTTPException(400, "اختيار الغلاف غير صالح")
                elif cover_choice:
                    raise HTTPException(400, "اختيار الغلاف غير صالح")
                else:
                    current = next((row["id"] for row in old_images.values() if row["is_cover"] and row["id"] in available), None)
                    cover_id = new_ids[0] if cover_upload else current or (new_ids[0] if new_ids else next(iter(available), None))
                if status == "published" and not cover_id:
                    raise HTTPException(400, "لا يمكن نشر المشروع دون صورة غلاف. أرفق صورة واخترها غلافًا.")
                db.execute("UPDATE featured_project_images SET is_cover=0 WHERE project_id=?", (project_id,))
                if cover_id:
                    db.execute("UPDATE featured_project_images SET is_cover=1 WHERE id=? AND project_id=?", (cover_id, project_id))
        except sqlite3.IntegrityError:
            for pair in created:
                for url in pair:
                    safe_unlink(url)
            raise HTTPException(400, "تعذر إنشاء رابط فريد للمشروع، حاول مرة أخرى")
        except Exception:
            for pair in created:
                for url in pair:
                    safe_unlink(url)
            raise
        for url in old_files:
            safe_unlink(url)
        return RedirectResponse(f"/admin/featured-projects?edit={project_id}", 303)

    @app.post("/admin/featured-projects/{project_id}/delete")
    async def delete(request: Request, project_id: int):
        admin(request)
        check_csrf(request, await request.form())
        with database() as db:
            if not db.execute("SELECT 1 FROM featured_projects WHERE id=?", (project_id,)).fetchone():
                raise HTTPException(404)
            images = db.execute("SELECT image_url,thumbnail_url FROM featured_project_images WHERE project_id=?", (project_id,)).fetchall()
            db.execute("DELETE FROM featured_project_images WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM featured_projects WHERE id=?", (project_id,))
        for image in images:
            safe_unlink(image["image_url"])
            safe_unlink(image["thumbnail_url"])
        return RedirectResponse("/admin/featured-projects", 303)

    @app.get("/works/projects/{slug}")
    def detail(request: Request, slug: str):
        with database() as db:
            row = db.execute("""SELECT p.*,i.image_url cover_url,i.alt cover_alt FROM featured_projects p
                JOIN featured_project_images i ON i.project_id=p.id AND i.is_cover=1
                WHERE p.slug=? AND p.status='published'""", (slug,)).fetchone()
            if not row:
                raise HTTPException(404)
            images = [dict(i) for i in db.execute("SELECT * FROM featured_project_images WHERE project_id=? ORDER BY sort_order,id", (row["id"],))]
        project = dict(row)
        message = quote(f"السلام عليكم، شاهدت مشروع {project['name']} في Urban Rise Works وأرغب في طلب معاينة وعرض سعر.")
        return templates.TemplateResponse(request, "works_project_detail.html", {"project": project, "images": images, "whatsapp": f"https://wa.me/966545687944?text={message}"})
