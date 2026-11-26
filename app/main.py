"""FastAPI application: `uvicorn app.main:app`."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import crawler
from .config import DATA_DIR, get_settings
from .db import db_session, get_setting, init_db
from .models import User
from .routes import admin, auth, student
from .security import generate_password, hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"
CRAWL_BUDGET_SECONDS = 270  # Vercel function limit is 300 s (vercel.json)


def ensure_admin() -> None:
    """Create the first admin. If ADMIN_PASSWORD is empty, a random one is written to data/admin_password.txt."""
    s = get_settings()
    with db_session() as db:
        if db.scalar(select(User).where(User.role == "admin")):
            return
        if not s.admin_password and s.on_vercel:
            log.error("No admin account yet: set ADMIN_PASSWORD in the Vercel project settings and redeploy.")
            return
        password = s.admin_password or generate_password()
        db.add(User(name="Administrador", email=s.admin_email.lower(), role="admin",
                    password_hash=hash_password(password), must_change_password=not s.admin_password))
        try:
            db.commit()
        except IntegrityError:  # another instance created it at the same moment
            db.rollback()
            return
    if not s.admin_password:
        path = DATA_DIR / "admin_password.txt"
        path.write_text(f"{s.admin_email}\n{password}\n")
        path.chmod(0o600)
        log.warning("Admin account created. Temporary password saved in %s (change it after logging in).", path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_admin()
    if not get_settings().on_vercel:  # on Vercel the daily crawl runs as a Cron Job (vercel.json)
        crawler.start_scheduler()
    s = get_settings()
    log.info("LLM: %s", s.llm_model if s.llm_enabled else "OFF (no OPENROUTER_API_KEY) — offline answers")
    yield


app = FastAPI(title="ISLA Assistente", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/api/docs"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Erro interno. Tenta novamente."}, status_code=500)


app.include_router(auth.router)
app.include_router(student.router)
app.include_router(admin.router)


@app.get("/api/cron/crawl", include_in_schema=False)
async def cron_crawl(request: Request):
    """Daily crawl, called by Vercel Cron. With CRON_SECRET set, only Vercel (which sends it) may call it;
    without it, calls are limited to one run every 6 hours."""
    s = get_settings()
    if s.cron_secret:
        if request.headers.get("authorization") != f"Bearer {s.cron_secret}":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    else:
        with db_session() as db:
            last = get_setting(db, "crawl_last_run")
        if last and datetime.now(s.tz) - datetime.fromisoformat(last) < timedelta(hours=6):
            return {"status": "skipped", "last_run": last}
    return await asyncio.to_thread(crawler.crawl, None, None, CRAWL_BUDGET_SECONDS)


@app.get("/api/health")
def health():
    s = get_settings()
    db = "postgres" if s.database_url.startswith("postgresql") else (
        "temporary" if s.database_url.startswith("sqlite:////tmp") else "sqlite")
    return {"ok": True, "llm_enabled": s.llm_enabled, "database": db}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def page_login():
    return FileResponse(STATIC / "index.html")


@app.get("/chat", include_in_schema=False)
def page_chat():
    return FileResponse(STATIC / "chat.html")


@app.get("/admin", include_in_schema=False)
def page_admin():
    return FileResponse(STATIC / "admin.html")
