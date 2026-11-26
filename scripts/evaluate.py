"""Ask the assistant a fixed set of student questions and score the answers.

    python -m scripts.evaluate                 # all questions (uses the real model if a key is set)
    python -m scripts.evaluate --only site     # one category

Questions live in eval/questions.json. Each has checks:
  must:      list of groups; every group needs at least one of its words in the answer
  must_not:  words that must NOT appear
  tools:     tools that must have been called (LLM mode only)
  history:   earlier turns, for follow-up questions
  student:   student number to ask as (default 20260001, demo student Ana)
  db_check:  "no_application:<activity title prefix>" — side effects that must not happen
Scoring is done against a fixed date (2026-11-26 10:00) so personal answers are stable.
A report is written to eval/report.md.
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app import agent
from app.config import ROOT, get_settings
from app.db import db_session, init_db
from app.models import Activity, Application, User
from app.textutil import strip_accents

EVAL_DIR = ROOT / "eval"
FIXED_NOW = datetime(2026, 11, 26, 10, 0)


def _norm(s: str) -> str:
    return strip_accents(s.lower())


def check(case: dict, result: dict, db) -> list[str]:
    text = _norm(result["answer"])
    problems = []
    for group in case.get("must", []):
        options = group if isinstance(group, list) else [group]
        if not any(_norm(o) in text for o in options):
            problems.append(f"falta: {' | '.join(options)}")
    for word in case.get("must_not", []):
        if re.search(r"\b" + re.escape(_norm(word)) + r"\b", text):
            problems.append(f"não devia ter: {word}")
    if result["mode"] == "llm":
        for tool in case.get("tools", []):
            if tool not in result["tools"]:
                problems.append(f"não usou a ferramenta {tool}")
    dbc = case.get("db_check", "")
    if dbc.startswith("no_application:"):
        prefix = dbc.split(":", 1)[1]
        act = db.scalar(select(Activity).where(Activity.title.like(prefix + "%")))
        user = db.scalar(select(User).where(User.student_number == case.get("student", "20260001")))
        if act and db.scalar(select(Application).where(Application.activity_id == act.id,
                                                        Application.user_id == user.id,
                                                        Application.status != "cancelled")):
            problems.append("candidatou sem confirmação")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="categoria (personal, site, safety, general)")
    ap.add_argument("--id", help="uma pergunta pelo id")
    args = ap.parse_args()

    init_db()
    agent.school_now = lambda: FIXED_NOW  # stable "today"
    cases = json.loads((EVAL_DIR / "questions.json").read_text())
    if args.only:
        cases = [c for c in cases if c["category"] == args.only]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]

    rows, passed = [], 0
    started = time.monotonic()
    with db_session() as db:
        for case in cases:
            user = db.scalar(select(User).where(User.student_number == case.get("student", "20260001")))
            t0 = time.monotonic()
            result = agent.answer(db, user, case.get("history", []), case["question"])
            secs = time.monotonic() - t0
            problems = check(case, result, db)
            ok = not problems
            passed += ok
            print(f"{'✅' if ok else '❌'} {case['id']:<22} {secs:4.1f}s  {'; '.join(problems)}")
            rows.append((case, result, problems, secs))

    total = len(rows)
    s = get_settings()
    lines = [f"# Avaliação do assistente — {datetime.now():%Y-%m-%d %H:%M}",
             "", f"Modelo: `{s.llm_model if s.llm_enabled else 'modo básico (sem IA)'}` · "
             f"**{passed}/{total} certas** · {time.monotonic() - started:.0f}s no total", ""]
    for case, result, problems, secs in rows:
        lines += [f"## {'✅' if not problems else '❌'} {case['id']} ({case['category']})",
                  f"**Pergunta:** {case['question']}", "",
                  f"**Ferramentas:** {', '.join(result['tools']) or '—'} · {secs:.1f}s", ""]
        if problems:
            lines += [f"**Problemas:** {'; '.join(problems)}", ""]
        lines += ["**Resposta:**", "", "> " + result["answer"].replace("\n", "\n> "), ""]
        if result["sources"]:
            lines += ["Fontes: " + ", ".join(s.get("url") or s.get("title") for s in result["sources"]), ""]
    (EVAL_DIR / "report.md").write_text("\n".join(lines))
    print(f"\n{passed}/{total} certas. Relatório: {EVAL_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
