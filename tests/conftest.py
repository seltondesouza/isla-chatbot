import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ["CRAWL_ENABLED"] = "false"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["ADMIN_PASSWORD"] = "admin-test-password"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import agent, db as dbmod  # noqa: E402
from app.config import ROOT, get_settings  # noqa: E402
from app.importers import import_csv  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

FIXED_NOW = datetime(2026, 11, 26, 10, 0)  # a Thursday
PASSWORD = "student-pass-123"


@pytest.fixture()
def app_client(tmp_path: Path, monkeypatch):
    get_settings.cache_clear()
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(agent, "school_now", lambda: FIXED_NOW)
    dbmod.init_db(url)
    with dbmod.db_session() as db:
        for kind in ["students", "courses", "enrollments", "timetable", "calendar", "charges", "activities"]:
            res = import_csv(db, kind, (ROOT / "app" / "sample_data" / f"{kind}.csv").read_bytes())
            assert not res["errors"], res["errors"]
        for u in db.scalars(select(User)):
            u.password_hash = hash_password(PASSWORD)
            u.must_change_password = False
        db.commit()

    from app.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def login(client, ident: str, password: str = PASSWORD):
    r = client.post("/api/auth/login", json={"email": ident, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def ana(app_client):
    login(app_client, "ana.demo@alunos.exemplo.pt")
    return app_client
