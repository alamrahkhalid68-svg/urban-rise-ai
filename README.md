# Urban Rise FastAPI App

FastAPI application with SQLite, Jinja templates, and static assets.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

4. Open:

```text
http://127.0.0.1:8000
```

## Deploy on Render

Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Notes:

- The FastAPI app entry is exposed as `app` in `main.py`.
- Static files are served from `static/`.
- Templates are loaded from `templates/`.
- SQLite database path is relative: `urbanrise.db`.
