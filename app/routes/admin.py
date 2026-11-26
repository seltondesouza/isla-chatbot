"""School office (staff) and admin endpoints."""

import asyncio
import csv
import io
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import crawler
from ..config import get_settings
from ..db import get_db, get_setting
from ..importers import TEMPLATES, import_csv, template_csv
from ..ingest import file_to_text, upsert_document
from ..models import (
    Activity, Application, CalendarEvent, Charge, Course, Document, Enrollment, TimetableSlot, User,
)
from ..search import delete_document
from ..security import generate_password, hash_password, require_admin, require_staff
from ..textutil import parse_date

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_staff)])

MAX_UPLOAD = 20 * 1024 * 1024
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


@router.get("/status")
def status(db: Session = Depends(get_db)):
    count = lambda model: db.scalar(select(func.count()).select_from(model))  # noqa: E731
    s = get_settings()
    return {
        "llm_enabled": s.llm_enabled, "llm_model": s.llm_model,
        "temporary_database": s.database_url.startswith("sqlite:////tmp"),
        "counts": {
            "students": db.scalar(select(func.count()).select_from(User).where(User.role == "student")),
            "courses": count(Course), "enrollments": count(Enrollment), "timetable_slots": count(TimetableSlot),
            "calendar_events": count(CalendarEvent), "charges": count(Charge), "activities": count(Activity),
            "applications": count(Application), "documents": count(Document),
        },
        "crawl": {"running": crawler.is_running(), "last_run": get_setting(db, "crawl_last_run") or None,
                  "last_stats": get_setting(db, "crawl_last_stats") or None,
                  "interval_hours": s.crawl_interval_hours, "enabled": s.crawl_enabled},
    }


@router.post("/crawl")
async def crawl_now():
    if crawler.is_running():
        return {"status": "already_running"}
    if get_settings().on_vercel:
        # Serverless: no background threads, so crawl within this request (time-limited).
        return await asyncio.to_thread(crawler.crawl, None, None, 240)
    threading.Thread(target=crawler.crawl, daemon=True, name="crawl-manual").start()
    return {"status": "started"}


@router.post("/demo-data", dependencies=[Depends(require_admin)])
def load_demo_data(db: Session = Depends(get_db)):
    """Import the demo students, courses, timetable, calendar, payments and activities (app/sample_data)."""
    summary, creds = {}, []
    for kind in ["students", "courses", "enrollments", "timetable", "calendar", "charges", "activities"]:
        res = import_csv(db, kind, (SAMPLE_DIR / f"{kind}.csv").read_bytes())
        summary[kind] = {"ok": res["ok"], "errors": res["errors"]}
        creds += res["new_credentials"]
    return {"summary": summary, "new_credentials": creds}


# ── CSV import ────────────────────────────────────────────────────────────────

@router.get("/templates/{kind}.csv", response_class=PlainTextResponse)
def template(kind: str):
    if kind not in TEMPLATES:
        raise HTTPException(404, "Modelo desconhecido.")
    return PlainTextResponse(template_csv(kind), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})


@router.post("/import/{kind}")
async def import_file(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if kind not in TEMPLATES:
        raise HTTPException(404, "Tipo de importação desconhecido.")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Ficheiro demasiado grande.")
    try:
        return import_csv(db, kind, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ── Activities and applications ───────────────────────────────────────────────

class ActivityIn(BaseModel):
    title: str = Field(min_length=2, max_length=250)
    summary: str | None = None
    starts_on: str | None = None
    ends_on: str | None = None
    apply_deadline: str | None = None
    how_to_apply: str | None = None
    url: str | None = None
    requires_application: bool = False
    capacity: int | None = None
    is_published: bool = True


def _activity_out(a: Activity, n_apps: int = 0) -> dict:
    return {"id": a.id, "title": a.title, "summary": a.summary, "starts_on": a.starts_on, "ends_on": a.ends_on,
            "apply_deadline": a.apply_deadline, "how_to_apply": a.how_to_apply, "url": a.url,
            "requires_application": a.requires_application, "capacity": a.capacity, "source": a.source,
            "is_published": a.is_published, "applications": n_apps}


def _apply_activity_fields(a: Activity, body: ActivityIn) -> None:
    try:
        a.starts_on, a.ends_on = parse_date(body.starts_on), parse_date(body.ends_on)
        a.apply_deadline = parse_date(body.apply_deadline)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    a.title, a.summary, a.how_to_apply, a.url = body.title, body.summary, body.how_to_apply, body.url
    a.requires_application, a.capacity, a.is_published = body.requires_application, body.capacity, body.is_published


@router.get("/activities")
def list_activities(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Application.activity_id, func.count())
                             .where(Application.status != "cancelled").group_by(Application.activity_id)).all())
    acts = db.scalars(select(Activity).order_by(Activity.starts_on.desc().nulls_last(), Activity.id.desc()))
    return [_activity_out(a, counts.get(a.id, 0)) for a in acts]


@router.post("/activities", status_code=201)
def create_activity(body: ActivityIn, db: Session = Depends(get_db)):
    a = Activity(source="manual")
    _apply_activity_fields(a, body)
    db.add(a)
    db.commit()
    return _activity_out(a)


@router.put("/activities/{activity_id}")
def update_activity(activity_id: int, body: ActivityIn, db: Session = Depends(get_db)):
    a = db.get(Activity, activity_id) or _404()
    _apply_activity_fields(a, body)
    db.commit()
    return _activity_out(a)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: int, db: Session = Depends(get_db)):
    db.delete(db.get(Activity, activity_id) or _404())
    db.commit()
    return {"ok": True}


@router.get("/activities/{activity_id}/applications")
def activity_applications(activity_id: int, db: Session = Depends(get_db)):
    apps = db.scalars(select(Application).where(Application.activity_id == activity_id)
                      .order_by(Application.created_at))
    return [{"id": p.id, "status": p.status, "note": p.note, "created_at": p.created_at,
             "student": {"name": p.user.name, "number": p.user.student_number, "email": p.user.email}}
            for p in apps]


class AppStatusIn(BaseModel):
    status: str = Field(pattern="^(submitted|accepted|rejected|cancelled)$")


@router.patch("/applications/{app_id}")
def set_application_status(app_id: int, body: AppStatusIn, db: Session = Depends(get_db)):
    p = db.get(Application, app_id) or _404()
    p.status = body.status
    db.commit()
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────

@router.get("/documents")
def list_documents(q: str = "", kind: str = "", db: Session = Depends(get_db)):
    query = select(Document).order_by(Document.updated_at.desc()).limit(300)
    if q:
        query = query.where(or_(Document.title.ilike(f"%{q}%"), Document.url.ilike(f"%{q}%")))
    if kind:
        query = query.where(Document.kind == kind)
    return [{"id": d.id, "title": d.title, "url": d.url, "kind": d.kind, "chars": len(d.text),
             "course": d.course.code if d.course else None, "updated_at": d.updated_at}
            for d in db.scalars(query)]


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...), title: str = Form(""), course_code: str = Form(""),
    db: Session = Depends(get_db),
):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Ficheiro demasiado grande (máx. 20 MB).")
    course_id = None
    if course_code.strip():
        course = db.scalar(select(Course).where(Course.code == course_code.strip().upper()))
        if not course:
            raise HTTPException(400, f"UC {course_code} não existe.")
        course_id = course.id
    filename = file.filename or "documento"
    try:
        text = file_to_text(filename, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if len(text) < 30:
        raise HTTPException(400, "Não foi possível extrair texto (PDF digitalizado como imagem?).")
    doc, _ = upsert_document(db, title=title.strip() or filename, text=text, kind="upload",
                             url=f"upload://{course_code.strip().upper() or 'geral'}/{filename}", course_id=course_id)
    return {"id": doc.id, "title": doc.title, "chars": len(text)}


@router.delete("/documents/{doc_id}")
def remove_document(doc_id: int, db: Session = Depends(get_db)):
    delete_document(db, db.get(Document, doc_id) or _404())
    db.commit()
    return {"ok": True}


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(q: str = "", db: Session = Depends(get_db)):
    query = select(User).order_by(User.role, User.name).limit(500)
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.name.ilike(like), User.email.ilike(like), User.student_number.ilike(like)))
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "student_number": u.student_number,
             "program": u.program, "year": u.year, "class_group": u.class_group, "is_active": u.is_active}
            for u in db.scalars(query)]


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), actor: User = Depends(require_staff)):
    u = db.get(User, user_id) or _404()
    if u.role != "student" and actor.role != "admin":
        raise HTTPException(403, "Só um administrador pode repor contas de funcionários.")
    password = generate_password()
    u.password_hash, u.must_change_password = hash_password(password), True
    db.commit()
    return {"email": u.email, "temporary_password": password}


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = Field(default=None, pattern="^(student|staff|admin)$")


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), actor: User = Depends(require_staff)):
    u = db.get(User, user_id) or _404()
    if (body.role is not None or u.role != "student") and actor.role != "admin":
        raise HTTPException(403, "Só um administrador pode alterar funcionários ou papéis.")
    if u.id == actor.id and (body.is_active is False or (body.role and body.role != actor.role)):
        raise HTTPException(400, "Não podes desativar nem despromover a tua própria conta.")
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.role is not None:
        u.role = body.role
    db.commit()
    return {"ok": True}


class StaffIn(BaseModel):
    name: str
    email: str
    role: str = Field(default="staff", pattern="^(staff|admin)$")


@router.post("/staff", status_code=201, dependencies=[Depends(require_admin)])
def create_staff(body: StaffIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Já existe uma conta com esse email.")
    password = generate_password()
    db.add(User(name=body.name, email=email, role=body.role, password_hash=hash_password(password),
                must_change_password=True))
    db.commit()
    return {"email": email, "temporary_password": password}


@router.post("/credentials.csv", response_class=PlainTextResponse)
def credentials_csv(rows: list[dict]):
    """Turns the new_credentials list from a student import into a CSV download."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["student_number", "name", "email", "temporary_password"])
    for r in rows:
        w.writerow([r.get("student_number", ""), r.get("name", ""), r.get("email", ""), r.get("temporary_password", "")])
    return PlainTextResponse("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="credenciais.csv"'})


def _404():
    raise HTTPException(404, "Não encontrado.")
