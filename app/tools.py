"""Tools the assistant can call.

Every tool receives a ToolContext bound to the logged-in user. Tools never take a
user id as an argument, so the model cannot ask for another student's data.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import search as search_mod
from .models import Activity, Application, CalendarEvent, Charge, Course, Enrollment, TimetableSlot, User
from .textutil import WEEKDAYS_PT, euros, format_date_pt, parse_date


@dataclass
class ToolContext:
    db: Session
    user: User
    now: datetime  # local school time
    sources: list[dict] = field(default_factory=list)

    @property
    def today(self) -> date:
        return self.now.date()

    def course_ids(self) -> list[int]:
        return list(self.db.scalars(select(Enrollment.course_id).where(Enrollment.user_id == self.user.id)))


class ToolError(Exception):
    """Raised for bad arguments; the message is shown to the model."""


# ── Implementations ──────────────────────────────────────────────────────────

def get_my_profile(ctx: ToolContext) -> dict:
    u = ctx.user
    courses = ctx.db.scalars(
        select(Course).join(Enrollment, Enrollment.course_id == Course.id).where(Enrollment.user_id == u.id)
        .order_by(Course.code)
    ).all()
    return {
        "name": u.name, "email": u.email, "role": u.role, "student_number": u.student_number,
        "program": u.program, "year": u.year, "class_group": u.class_group,
        "courses": [{"code": c.code, "name": c.name, "semester": c.semester} for c in courses],
    }


def _events_on(ctx: ToolContext, day: date, course_ids: list[int]) -> list[CalendarEvent]:
    return list(ctx.db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.start_date <= day, CalendarEvent.end_date >= day,
            or_(CalendarEvent.course_id.is_(None), CalendarEvent.course_id.in_(course_ids)),
        ).order_by(CalendarEvent.start_date)
    ))


def get_my_schedule(ctx: ToolContext, date: str | None = None, days: int = 1) -> dict:
    start = parse_date(date) if date else ctx.today
    days = max(1, min(int(days or 1), 14))
    course_ids = ctx.course_ids()
    if not course_ids:
        return {"note": "Não tens unidades curriculares associadas. Fala com a secretaria.", "days": []}

    slots = ctx.db.scalars(
        select(TimetableSlot).where(
            TimetableSlot.course_id.in_(course_ids),
            or_(TimetableSlot.class_group.is_(None), TimetableSlot.class_group == "",
                TimetableSlot.class_group == (ctx.user.class_group or "")),
        ).order_by(TimetableSlot.weekday, TimetableSlot.start_time)
    ).all()

    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        events = _events_on(ctx, day, course_ids)
        no_classes = [e for e in events if e.cancels_classes]
        classes = []
        if not no_classes:
            for s in slots:
                if s.weekday != day.weekday():
                    continue
                if (s.valid_from and day < s.valid_from) or (s.valid_to and day > s.valid_to):
                    continue
                classes.append({
                    "start": s.start_time, "end": s.end_time, "course_code": s.course.code,
                    "course": s.course.name, "room": s.room, "teacher": s.teacher, "type": s.kind,
                })
        out.append({
            "date": day.isoformat(), "weekday": WEEKDAYS_PT[day.weekday()],
            "label": format_date_pt(day),
            "no_classes_because": [e.title for e in no_classes],
            "calendar_events": [{"title": e.title, "kind": e.kind} for e in events],
            "classes": classes,
        })
    return {"today": ctx.today.isoformat(), "days": out}


def _charge_dict(c: Charge, today: date) -> dict:
    overdue = c.status == "pending" and c.due_date is not None and c.due_date < today
    return {
        "id": c.id, "period": c.period, "description": c.description, "amount": euros(c.amount_cents),
        "amount_cents": c.amount_cents, "due_date": c.due_date.isoformat() if c.due_date else None,
        "status": "overdue" if overdue else c.status, "reason": c.reason, "payment_reference": c.reference,
    }


def get_my_charges(ctx: ToolContext, period: str | None = None, status: str | None = None) -> dict:
    """period: 'YYYY-MM'. Without filters: what is overdue or due this month, plus the next payment."""
    q = select(Charge).where(Charge.user_id == ctx.user.id, Charge.status != "cancelled")
    this_month = ctx.today.strftime("%Y-%m")
    if period:
        if len(period) != 7 or period[4] != "-":
            raise ToolError("period deve ter o formato AAAA-MM")
        q = q.where(Charge.period == period)
    elif not status:
        q = q.where(or_(Charge.period == this_month, (Charge.status == "pending") & (Charge.period < this_month)))
    if status in ("pending", "paid"):
        q = q.where(Charge.status == status)
    charges = [_charge_dict(c, ctx.today) for c in ctx.db.scalars(q.order_by(Charge.period, Charge.id))]
    unpaid = [c for c in charges if c["status"] in ("pending", "overdue")]
    # Always report overdue payments, whatever the filter, so a narrow question
    # ("did I pay October?") can never be answered with "nothing is overdue" by mistake.
    overdue_all = [_charge_dict(c, ctx.today) for c in ctx.db.scalars(select(Charge).where(
        Charge.user_id == ctx.user.id, Charge.status == "pending", Charge.due_date < ctx.today,
    ).order_by(Charge.period))]
    upcoming = ctx.db.scalar(select(Charge).where(
        Charge.user_id == ctx.user.id, Charge.status == "pending", Charge.period > this_month,
    ).order_by(Charge.period, Charge.id).limit(1))
    future_total = ctx.db.scalar(select(func.coalesce(func.sum(Charge.amount_cents), 0)).where(
        Charge.user_id == ctx.user.id, Charge.status == "pending", Charge.period > this_month))
    return {
        "today": ctx.today.isoformat(),
        "charges": charges,
        "total_unpaid": euros(sum(c["amount_cents"] for c in unpaid)),
        "filter": {"period": period, "status": status} if (period or status) else "default: overdue + this month",
        "overdue_all_periods": overdue_all,
        "overdue_total_all_periods": euros(sum(c["amount_cents"] for c in overdue_all)),
        "next_payment": _charge_dict(upcoming, ctx.today) if upcoming else None,
        "remaining_after_this_month": euros(future_total or 0),
    }


def get_academic_calendar(ctx: ToolContext, from_date: str | None = None, to_date: str | None = None) -> dict:
    start = parse_date(from_date) or ctx.today
    end = parse_date(to_date) or start + timedelta(days=60)
    course_ids = ctx.course_ids()
    events = ctx.db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.end_date >= start, CalendarEvent.start_date <= end,
            or_(CalendarEvent.course_id.is_(None), CalendarEvent.course_id.in_(course_ids)),
        ).order_by(CalendarEvent.start_date)
    ).all()
    return {"from": start.isoformat(), "to": end.isoformat(), "events": [
        {"title": e.title, "kind": e.kind, "start": e.start_date.isoformat(), "end": e.end_date.isoformat(),
         "course": e.course.code if e.course else None, "notes": e.notes}
        for e in events
    ]}


def _activity_dict(a: Activity, applied: Application | None, today: date) -> dict:
    deadline_passed = a.apply_deadline is not None and a.apply_deadline < today
    return {
        "id": a.id, "title": a.title, "summary": (a.summary or "")[:600],
        "starts_on": a.starts_on.isoformat() if a.starts_on else None,
        "ends_on": a.ends_on.isoformat() if a.ends_on else None,
        "apply_deadline": a.apply_deadline.isoformat() if a.apply_deadline else None,
        "deadline_passed": deadline_passed,
        "can_apply_in_app": a.requires_application,
        "how_to_apply": a.how_to_apply or (f"Ver detalhes em {a.url}" if a.url else None),
        "url": a.url, "my_application_status": applied.status if applied else None,
    }


def list_activities(ctx: ToolContext, include_past: bool = False, query: str | None = None) -> dict:
    q = select(Activity).where(Activity.is_published.is_(True))
    if not include_past:
        cutoff = ctx.today - timedelta(days=1)
        q = q.where(or_(Activity.ends_on >= cutoff, Activity.starts_on >= cutoff,
                        Activity.apply_deadline >= ctx.today,
                        (Activity.starts_on.is_(None)) & (Activity.created_at >= ctx.now - timedelta(days=60))))
    if query:
        like = f"%{query}%"
        q = q.where(or_(Activity.title.ilike(like), Activity.summary.ilike(like)))
    acts = ctx.db.scalars(q.order_by(Activity.starts_on.is_(None), Activity.starts_on).limit(25)).all()
    mine = {a.activity_id: a for a in ctx.db.scalars(select(Application).where(Application.user_id == ctx.user.id))}
    for a in acts:
        if a.url:
            ctx.sources.append({"title": a.title, "url": a.url})
    return {"today": ctx.today.isoformat(), "activities": [_activity_dict(a, mine.get(a.id), ctx.today) for a in acts]}


def apply_to_activity(ctx: ToolContext, activity_id: int, confirmed: bool = False, note: str | None = None) -> dict:
    a = ctx.db.get(Activity, int(activity_id))
    if not a or not a.is_published:
        raise ToolError("Atividade não encontrada.")
    if ctx.user.role != "student":
        return {"status": "not_allowed", "message": "Só estudantes se podem candidatar."}
    if not a.requires_application:
        return {"status": "external", "message": "Esta atividade não tem inscrição pela app.",
                "how_to_apply": a.how_to_apply, "url": a.url}
    if a.apply_deadline and a.apply_deadline < ctx.today:
        return {"status": "closed", "message": f"O prazo terminou a {a.apply_deadline.isoformat()}."}
    existing = ctx.db.scalar(select(Application).where(
        Application.user_id == ctx.user.id, Application.activity_id == a.id))
    if existing and existing.status != "cancelled":
        return {"status": "already_applied", "application_status": existing.status}
    if a.capacity:
        taken = ctx.db.query(Application).filter(
            Application.activity_id == a.id, Application.status.in_(("submitted", "accepted"))).count()
        if taken >= a.capacity:
            return {"status": "full", "message": "Já não há vagas."}
    if not confirmed:
        return {"status": "needs_confirmation",
                "message": "Mostra os detalhes ao estudante e pergunta se confirma a candidatura.",
                "activity": _activity_dict(a, None, ctx.today)}
    if existing:  # re-apply after cancelling
        existing.status, existing.note = "submitted", note
    else:
        ctx.db.add(Application(user_id=ctx.user.id, activity_id=a.id, note=note))
    ctx.db.commit()
    return {"status": "submitted", "activity": a.title}


def list_my_applications(ctx: ToolContext) -> dict:
    apps = ctx.db.scalars(select(Application).where(Application.user_id == ctx.user.id)
                          .order_by(Application.created_at.desc())).all()
    return {"applications": [
        {"id": p.id, "activity_id": p.activity_id, "activity": p.activity.title, "status": p.status,
         "submitted_at": p.created_at.isoformat() if p.created_at else None}
        for p in apps
    ]}


def cancel_application(ctx: ToolContext, application_id: int, confirmed: bool = False) -> dict:
    p = ctx.db.get(Application, int(application_id))
    if not p or p.user_id != ctx.user.id:
        raise ToolError("Candidatura não encontrada.")
    if p.status == "cancelled":
        return {"status": "already_cancelled"}
    if not confirmed:
        return {"status": "needs_confirmation", "activity": p.activity.title}
    p.status = "cancelled"
    ctx.db.commit()
    return {"status": "cancelled", "activity": p.activity.title}


def search_school_info(ctx: ToolContext, query: str) -> dict:
    course_ids = None if ctx.user.role in ("staff", "admin") else ctx.course_ids()
    hits = search_mod.search(ctx.db, query, course_ids=course_ids, limit=6)
    for h in hits:
        ctx.sources.append({"title": h["title"], "url": h["url"]})
    if not hits:
        return {"results": [], "note": "Nada encontrado. Tenta outras palavras ou indica o contacto da secretaria."}
    return {"results": [{"title": h["title"], "url": h["url"], "text": h["text"]} for h in hits]}


# ── Registry (OpenAI / OpenRouter tool format) ───────────────────────────────

@dataclass
class Tool:
    fn: Callable[..., dict]
    description: str
    parameters: dict


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


TOOLS: dict[str, Tool] = {
    "get_my_profile": Tool(get_my_profile,
        "Dados do estudante autenticado: nome, número, curso, ano, turma e unidades curriculares inscritas.", _obj()),
    "get_my_schedule": Tool(get_my_schedule,
        "Horário de aulas do estudante para um dia ou vários dias seguidos, já com feriados e interrupções letivas.",
        _obj({"date": {"type": "string", "description": "Primeiro dia, AAAA-MM-DD. Omite para hoje."},
              "days": {"type": "integer", "minimum": 1, "maximum": 14, "description": "Quantos dias (1 = só esse dia, 7 = semana)."}})),
    "get_my_charges": Tool(get_my_charges,
        "Pagamentos do estudante (propinas, emolumentos, outros): valor, prazo, estado e porquê. "
        "Sem filtros devolve tudo o que está por pagar e o mês atual.",
        _obj({"period": {"type": "string", "description": "Mês AAAA-MM."},
              "status": {"type": "string", "enum": ["pending", "paid"]}})),
    "get_academic_calendar": Tool(get_academic_calendar,
        "Calendário académico: feriados, interrupções, épocas de exames e prazos, num intervalo de datas.",
        _obj({"from_date": {"type": "string", "description": "AAAA-MM-DD, por omissão hoje."},
              "to_date": {"type": "string", "description": "AAAA-MM-DD, por omissão +60 dias."}})),
    "list_activities": Tool(list_activities,
        "Atividades e eventos da escola (inclui os publicados no site), com datas, prazos e como se inscrever.",
        _obj({"include_past": {"type": "boolean"},
              "query": {"type": "string", "description": "Filtro opcional por palavra."}})),
    "apply_to_activity": Tool(apply_to_activity,
        "Candidata o estudante a uma atividade. Chama primeiro com confirmed=false; só usa confirmed=true "
        "depois de o estudante confirmar explicitamente nesta conversa.",
        _obj({"activity_id": {"type": "integer"}, "confirmed": {"type": "boolean"},
              "note": {"type": "string", "description": "Mensagem opcional do estudante."}}, ["activity_id"])),
    "list_my_applications": Tool(list_my_applications, "Candidaturas do estudante a atividades e o seu estado.", _obj()),
    "cancel_application": Tool(cancel_application,
        "Cancela uma candidatura do estudante. confirmed=true só depois de confirmação explícita.",
        _obj({"application_id": {"type": "integer"}, "confirmed": {"type": "boolean"}}, ["application_id"])),
    "search_school_info": Tool(search_school_info,
        "Pesquisa no site da escola, regulamentos, despachos de propinas/emolumentos, notícias e materiais das UCs. "
        "Usa para qualquer pergunta sobre regras, prazos, procedimentos, bolsas, candidaturas, contactos, cursos.",
        _obj({"query": {"type": "string", "description": "Palavras-chave em português."}}, ["query"])),
}


def openai_tool_specs() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": t.description, "parameters": t.parameters}}
            for n, t in TOOLS.items()]


def run_tool(ctx: ToolContext, name: str, arguments: str | dict | None) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"Ferramenta desconhecida: {name}"}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        if not isinstance(args, dict):
            raise ToolError("argumentos inválidos")
        allowed = set(tool.parameters["properties"])
        args = {k: v for k, v in args.items() if k in allowed}
        return tool.fn(ctx, **args)
    except (ToolError, ValueError, TypeError) as exc:
        ctx.db.rollback()
        return {"error": str(exc)}
