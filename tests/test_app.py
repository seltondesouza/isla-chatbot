from datetime import date

from sqlalchemy import select

from app import db as dbmod
from app.importers import import_csv, read_rows
from app.ingest import upsert_document
from app.models import Activity, Charge, Course, User
from app.search import search
from app.textutil import find_pt_date_range, parse_money_cents

from .conftest import login


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_login_by_email_or_number_and_bad_password(app_client):
    assert login(app_client, "20260001")["name"] == "Ana Demo"
    r = app_client.post("/api/auth/login", json={"email": "20260001", "password": "wrong"})
    assert r.status_code == 401


def test_no_public_registration(app_client):
    assert app_client.post("/api/auth/register", json={}).status_code in (404, 405)


def test_student_cannot_use_admin_api(ana):
    assert ana.get("/api/admin/status").status_code == 403
    assert ana.post("/api/admin/import/charges", files={"file": ("c.csv", b"x")}).status_code == 403


def test_unauthenticated_chat_rejected(app_client):
    assert app_client.post("/api/chat", json={"message": "olá"}).status_code == 401


def test_change_password(ana):
    r = ana.post("/api/auth/change-password", json={"current_password": "student-pass-123", "new_password": "short"})
    assert r.status_code == 400
    r = ana.post("/api/auth/change-password",
                 json={"current_password": "student-pass-123", "new_password": "a-much-longer-one"})
    assert r.status_code == 200
    ana.post("/api/auth/logout")
    login(ana, "20260001", "a-much-longer-one")


# ── Personal data through the tools ───────────────────────────────────────────

def test_schedule_today_uses_class_group(ana):
    # 2026-11-26 is a Thursday: Ana (IA1) has PROG at 18:30; IA2's 20:30 slot must not appear.
    days = ana.get("/api/me/overview").json()["today"]["days"]
    assert days[0]["date"] == "2026-11-26"
    assert [c["start"] for c in days[0]["classes"]] == ["18:30"]


def test_schedule_respects_holidays(ana):
    data = ana.get("/api/me/schedule", params={"date": "2026-12-08", "days": 1}).json()
    day = data["days"][0]
    assert day["classes"] == [] and "Imaculada" in day["no_classes_because"][0]


def test_charges_show_overdue_and_only_own(ana):
    charges = ana.get("/api/me/overview").json()["charges"]
    nov = [c for c in charges["charges"] if c["period"] == "2026-11"]
    assert nov and nov[0]["status"] == "overdue" and nov[0]["reason"]
    with dbmod.db_session() as db:
        ana_id = db.scalar(select(User.id).where(User.student_number == "20260001"))
        own_ids = set(db.scalars(select(Charge.id).where(Charge.user_id == ana_id)))
    assert {c["id"] for c in charges["charges"]} <= own_ids


def test_offline_chat_answers_payment_question(ana):
    r = ana.post("/api/chat", json={"message": "O que tenho de pagar este mês?"}).json()
    assert r["mode"] == "offline"
    assert "120,00" in r["answer"] and "em atraso" in r["answer"]
    conv = ana.get(f"/api/conversations/{r['conversation_id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]


def test_offline_chat_schedule_tomorrow(ana):
    r = ana.post("/api/chat", json={"message": "que aulas tenho amanhã?"}).json()
    assert "Programação" in r["answer"]  # Friday PROG theory class for every group


def test_conversations_are_private(app_client):
    login(app_client, "20260001")
    conv_id = app_client.post("/api/chat", json={"message": "aulas hoje"}).json()["conversation_id"]
    app_client.post("/api/auth/logout")
    login(app_client, "20260002")
    assert app_client.get(f"/api/conversations/{conv_id}").status_code == 404
    assert app_client.delete(f"/api/conversations/{conv_id}").status_code == 404


def test_apply_to_activity_and_capacity(ana):
    with dbmod.db_session() as db:
        act = db.scalar(select(Activity).where(Activity.title.like("Workshop%")))
        act.capacity = 1
        db.commit()
        act_id = act.id
    assert ana.post(f"/api/activities/{act_id}/apply", json={}).json()["status"] == "submitted"
    assert ana.post(f"/api/activities/{act_id}/apply", json={}).json()["status"] == "already_applied"
    ana.post("/api/auth/logout")
    login(ana, "20260002")
    assert ana.post(f"/api/activities/{act_id}/apply", json={}).json()["status"] == "full"


def test_tool_requires_confirmation():
    # The model must call twice: first without confirmation.
    from app.agent import school_now
    from app.tools import ToolContext, run_tool
    with dbmod.db_session() as db:
        user = db.scalar(select(User).where(User.student_number == "20260002"))
        act = db.scalar(select(Activity).where(Activity.title.like("Mentoria%")))
        ctx = ToolContext(db=db, user=user, now=school_now())
        assert run_tool(ctx, "apply_to_activity", {"activity_id": act.id})["status"] == "needs_confirmation"
        assert run_tool(ctx, "apply_to_activity", {"activity_id": act.id, "confirmed": True})["status"] == "submitted"
        # Unknown arguments (e.g. someone else's user id) are dropped, not honoured.
        res = run_tool(ctx, "get_my_charges", '{"user_id": 1}')
        assert all(c["id"] for c in res["charges"])


# ── Search, import, parsing ───────────────────────────────────────────────────

def test_search_respects_course_scope(app_client):
    with dbmod.db_session() as db:
        upsert_document(db, title="Regulamento de propinas", text="As propinas pagam-se em dez prestações mensais.",
                        url="https://example.test/propinas")
        iia, fcsi = (db.scalar(select(Course.id).where(Course.code == c)) for c in ("IIA", "FCSI"))
        upsert_document(db, title="Material de outra UC", text="Prestações do curso de outra UC.",
                        url="upload://FCSI/doc", course_id=fcsi)
        hits = search(db, "quantas prestações de propinas", course_ids=[iia])
    assert [h["title"] for h in hits] == ["Regulamento de propinas"]


def test_reindex_skips_unchanged(app_client):
    with dbmod.db_session() as db:
        _, changed1 = upsert_document(db, title="A", text="texto igual", url="https://e.test/a")
        _, changed2 = upsert_document(db, title="A", text="texto igual", url="https://e.test/a")
    assert changed1 and not changed2


def test_import_reports_bad_rows_and_is_idempotent(app_client):
    csv = "student_number;period;description;amount\n20260001;2026-13x;X;10\n99999;2026-12;Y;10\n".encode()
    with dbmod.db_session() as db:
        res = import_csv(db, "charges", csv)
        assert res["ok"] == 0 and len(res["errors"]) == 2
        again = import_csv(db, "students", "student_number,name,email\n20260001,Ana Demo,ana.demo@alunos.exemplo.pt\n".encode())
        assert again["ok"] == 1 and again["new_credentials"] == []


def test_csv_delimiter_and_bom():
    rows = read_rows("﻿code,name\nIIA,Intro\n".encode())
    assert rows == [{"code": "IIA", "name": "Intro"}]


def test_money_and_dates():
    assert parse_money_cents("1.234,56 €") == 123456
    assert parse_money_cents("325.5") == 32550
    assert find_pt_date_range("Mercado de Natal\n2 a 5 de dezembro", date(2026, 11, 26)) == (date(2026, 12, 2), date(2026, 12, 5))
    assert find_pt_date_range("dia 10 de janeiro", date(2026, 11, 26)) == (date(2027, 1, 10), date(2027, 1, 10))


# ── Hosting (Vercel) helpers ──────────────────────────────────────────────────

def test_demo_data_is_admin_only_and_idempotent(app_client):
    login(app_client, "20260001")
    assert app_client.post("/api/admin/demo-data").status_code == 403
    app_client.post("/api/auth/logout")
    login(app_client, "admin@islasantarem.pt", "admin-test-password")
    r = app_client.post("/api/admin/demo-data").json()
    assert r["new_credentials"] == []  # demo students already exist in the test DB
    assert all(not v["errors"] for v in r["summary"].values())


def test_cron_crawl_rate_limited_without_secret(app_client, monkeypatch):
    from datetime import datetime
    from app.config import get_settings
    from app.db import set_setting
    with dbmod.db_session() as db:
        set_setting(db, "crawl_last_run", datetime.now(get_settings().tz).isoformat(timespec="seconds"))
    assert app_client.get("/api/cron/crawl").json()["status"] == "skipped"
    monkeypatch.setattr(get_settings(), "cron_secret", "s3cret-value")
    assert app_client.get("/api/cron/crawl").status_code == 401


def test_postgres_url_is_normalized(monkeypatch):
    from app.config import Settings, _database_url
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@host:6543/db?sslmode=require&supa=base-pooler.x")
    url = _database_url(Settings(_env_file=None))
    assert url == "postgresql+psycopg://u:p@host:6543/db?sslmode=require"
