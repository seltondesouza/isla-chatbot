"""CSV imports for the school office (Excel 'CSV UTF-8' with ; or , works).

Each import is an upsert: running the same file twice does not duplicate rows.
"""

import csv
import io
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, CalendarEvent, Charge, Course, Enrollment, TimetableSlot, User
from .security import generate_password, hash_password
from .textutil import parse_date, parse_money_cents, parse_time, parse_weekday

TEMPLATES: dict[str, list[str]] = {
    "students": ["student_number", "name", "email", "program", "year", "class_group"],
    "courses": ["code", "name", "program", "year", "semester"],
    "enrollments": ["student_number", "course_code"],
    "timetable": ["course_code", "class_group", "weekday", "start", "end", "room", "teacher", "type", "valid_from", "valid_to"],
    "calendar": ["title", "start_date", "end_date", "kind", "course_code", "notes"],
    "charges": ["student_number", "period", "description", "amount", "due_date", "status", "reason", "reference"],
    "activities": ["title", "summary", "starts_on", "ends_on", "apply_deadline", "how_to_apply", "url", "requires_application", "capacity"],
}

EXAMPLES: dict[str, list[list[str]]] = {
    "students": [["20250001", "Ana Silva", "ana.silva@alunos.islasantarem.pt", "CTeSP Inteligência Artificial", "1", "IA1"]],
    "courses": [["IIA", "Introdução à Inteligência Artificial", "CTeSP Inteligência Artificial", "1", "1"]],
    "enrollments": [["20250001", "IIA"]],
    "timetable": [["IIA", "IA1", "segunda", "18:30", "20:30", "2.04", "Prof. Exemplo", "T", "2025-09-15", "2026-01-31"]],
    "calendar": [["Feriado — Imaculada Conceição", "2025-12-08", "2025-12-08", "holiday", "", ""]],
    "charges": [["20250001", "2025-12", "Propina — prestação 4/10", "120,00", "2025-12-10", "pending", "Propina anual dividida em 10 prestações (despacho de propinas)", "MB 12345 123 456 789"]],
    "activities": [["Workshop de Python", "Introdução prática", "2026-01-20", "2026-01-20", "2026-01-15", "Inscrição pela app", "", "sim", "30"]],
}

_TRUE = {"1", "true", "sim", "s", "yes", "y", "x"}


def template_csv(kind: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(TEMPLATES[kind])
    w.writerows(EXAMPLES.get(kind, []))
    return buf.getvalue()


def read_rows(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if any(clean.values()):
            rows.append(clean)
    return rows


def _student(db: Session, number: str) -> User:
    u = db.scalar(select(User).where(User.student_number == number))
    if not u:
        u = db.scalar(select(User).where(User.email == number.lower()))
    if not u:
        raise ValueError(f"estudante {number!r} não existe (importa primeiro os estudantes)")
    return u


def _course(db: Session, code: str) -> Course:
    c = db.scalar(select(Course).where(Course.code == code.upper()))
    if not c:
        raise ValueError(f"UC {code!r} não existe (importa primeiro as UCs)")
    return c


def _int(v: str) -> int | None:
    return int(v) if v.strip() else None


def _students(db: Session, r: dict) -> dict | None:
    email = r["email"].lower()
    if "@" not in email:
        raise ValueError("email inválido")
    u = db.scalar(select(User).where(User.student_number == r["student_number"])) or \
        db.scalar(select(User).where(User.email == email))
    if not u:
        password = generate_password()
        u = User(email=email, password_hash=hash_password(password), role="student", must_change_password=True)
        db.add(u)
    u.email, u.name, u.student_number = email, r["name"], r["student_number"]
    u.program, u.year, u.class_group = r.get("program") or None, _int(r.get("year", "")), r.get("class_group") or None
    if u.id is None:
        return {"student_number": r["student_number"], "name": r["name"], "email": email, "temporary_password": password}
    return None


def _courses(db: Session, r: dict) -> dict | None:
    code = r["code"].upper()
    c = db.scalar(select(Course).where(Course.code == code)) or Course(code=code)
    c.name, c.program = r["name"], r.get("program") or None
    c.year, c.semester = _int(r.get("year", "")), _int(r.get("semester", ""))
    db.add(c)


def _enrollments(db: Session, r: dict) -> dict | None:
    u, c = _student(db, r["student_number"]), _course(db, r["course_code"])
    if not db.scalar(select(Enrollment).where(Enrollment.user_id == u.id, Enrollment.course_id == c.id)):
        db.add(Enrollment(user_id=u.id, course_id=c.id))


def _timetable(db: Session, r: dict) -> dict | None:
    c = _course(db, r["course_code"])
    key = dict(course_id=c.id, class_group=r.get("class_group") or None, weekday=parse_weekday(r["weekday"]),
               start_time=parse_time(r["start"]))
    slot = db.scalar(select(TimetableSlot).filter_by(**key)) or TimetableSlot(**key)
    slot.end_time = parse_time(r["end"])
    slot.room, slot.teacher, slot.kind = r.get("room") or None, r.get("teacher") or None, r.get("type") or None
    slot.valid_from, slot.valid_to = parse_date(r.get("valid_from")), parse_date(r.get("valid_to"))
    db.add(slot)


def _calendar(db: Session, r: dict) -> dict | None:
    start = parse_date(r["start_date"])
    end = parse_date(r.get("end_date")) or start
    course_id = _course(db, r["course_code"]).id if r.get("course_code") else None
    kind = (r.get("kind") or "other").lower()
    if kind not in ("holiday", "break", "exam", "deadline", "other"):
        raise ValueError("kind deve ser holiday, break, exam, deadline ou other")
    ev = db.scalar(select(CalendarEvent).where(CalendarEvent.title == r["title"], CalendarEvent.start_date == start)) \
        or CalendarEvent(title=r["title"], start_date=start)
    ev.end_date, ev.kind, ev.course_id, ev.notes = end, kind, course_id, r.get("notes") or None
    db.add(ev)


def _charges(db: Session, r: dict) -> dict | None:
    u = _student(db, r["student_number"])
    period = r["period"]
    if len(period) != 7 or period[4] != "-":
        raise ValueError("period deve ser AAAA-MM")
    ch = db.scalar(select(Charge).where(Charge.user_id == u.id, Charge.period == period,
                                        Charge.description == r["description"])) \
        or Charge(user_id=u.id, period=period, description=r["description"])
    ch.amount_cents = parse_money_cents(r["amount"])
    ch.due_date = parse_date(r.get("due_date"))
    status = (r.get("status") or "pending").lower()
    if status not in ("pending", "paid", "cancelled"):
        raise ValueError("status deve ser pending, paid ou cancelled")
    ch.status, ch.reason, ch.reference = status, r.get("reason") or None, r.get("reference") or None
    db.add(ch)


def _activities(db: Session, r: dict) -> dict | None:
    a = db.scalar(select(Activity).where(Activity.title == r["title"], Activity.source == "manual")) \
        or Activity(title=r["title"], source="manual")
    a.summary, a.how_to_apply, a.url = r.get("summary") or None, r.get("how_to_apply") or None, r.get("url") or None
    a.starts_on, a.ends_on = parse_date(r.get("starts_on")), parse_date(r.get("ends_on"))
    a.apply_deadline = parse_date(r.get("apply_deadline"))
    a.requires_application = r.get("requires_application", "").lower() in _TRUE
    a.capacity = _int(r.get("capacity", ""))
    db.add(a)


HANDLERS: dict[str, Callable[[Session, dict], dict | None]] = {
    "students": _students, "courses": _courses, "enrollments": _enrollments, "timetable": _timetable,
    "calendar": _calendar, "charges": _charges, "activities": _activities,
}


def import_csv(db: Session, kind: str, data: bytes) -> dict:
    if kind not in HANDLERS:
        raise ValueError(f"tipo desconhecido: {kind}")
    rows = read_rows(data)
    required = [c for c in TEMPLATES[kind] if c in {
        "student_number", "name", "email", "code", "course_code", "weekday", "start", "end",
        "title", "start_date", "period", "description", "amount"}]
    if rows:
        missing = [c for c in required if c not in rows[0]]
        if missing:
            raise ValueError(f"faltam colunas: {', '.join(missing)}")
    out: dict = {"ok": 0, "errors": [], "new_credentials": []}
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            with db.begin_nested():
                credential = HANDLERS[kind](db, row)
                db.flush()
            out["ok"] += 1
            if credential:
                out["new_credentials"].append(credential)
        except (ValueError, KeyError) as exc:
            out["errors"].append(f"linha {i}: {exc}")
        except Exception as exc:  # integrity errors etc.
            out["errors"].append(f"linha {i}: {type(exc).__name__}: {exc}"[:300])
    db.commit()
    return out
