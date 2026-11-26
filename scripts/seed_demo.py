"""Load the demo data in sample_data/ (and optionally the old project's knowledge files).

    python -m scripts.seed_demo                      # demo students, timetable, payments…
    python -m scripts.seed_demo --old-knowledge isla-chatbot/data/knowledge

Demo student passwords are written to data/demo_credentials.txt.
"""

import argparse
import re
from pathlib import Path

from sqlalchemy import select

from app.config import DATA_DIR, ROOT
from app.db import db_session, init_db
from app.importers import import_csv
from app.ingest import upsert_document
from app.models import Course

ORDER = ["students", "courses", "enrollments", "timetable", "calendar", "charges", "activities"]
_BLOCK = re.compile(r"\n={40,}\n# (.+?)\s{2,}\[[^\]]*\]\n={40,}\n", re.S)


def load_samples() -> None:
    creds = []
    with db_session() as db:
        for kind in ORDER:
            path = ROOT / "app" / "sample_data" / f"{kind}.csv"
            result = import_csv(db, kind, path.read_bytes())
            print(f"{kind:12} {result['ok']:3} ok  {len(result['errors'])} erros")
            for e in result["errors"]:
                print("   ", e)
            creds += result["new_credentials"]
    if creds:
        out = DATA_DIR / "demo_credentials.txt"
        out.write_text("".join(f"{c['email']}  (n.º {c['student_number']})  {c['temporary_password']}\n" for c in creds))
        out.chmod(0o600)
        print(f"\nPalavras-passe dos estudantes de demonstração: {out}")


def load_old_knowledge(folder: Path) -> None:
    """The old project stored extracted PDFs as big .txt files with '# Title [date]' blocks."""
    with db_session() as db:
        iia = db.scalar(select(Course).where(Course.code == "IIA"))
        for path in sorted(folder.glob("*.txt")):
            text = "\n" + path.read_text(encoding="utf-8", errors="replace")
            parts = _BLOCK.split(text)
            course_id = iia.id if path.name.startswith("course_") and iia else None
            n = 0
            for title, body in zip(parts[1::2], parts[2::2]):
                if len(body.strip()) < 100:
                    continue
                upsert_document(db, title=title.strip(), text=body, kind="upload", course_id=course_id,
                                url=f"upload://legacy/{path.stem}/{title.strip()}")
                n += 1
            print(f"{path.name}: {n} documentos")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-knowledge", type=Path, help="pasta data/knowledge do projeto antigo")
    args = ap.parse_args()
    init_db()
    load_samples()
    if args.old_knowledge:
        load_old_knowledge(args.old_knowledge)
