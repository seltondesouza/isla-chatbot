"""Index a folder of PDFs/TXT files.

Sub-folders named after a course code (e.g. data/courses/IIA/) are attached to that
course, so only students enrolled in it get them in search. Anything else is general.

    python -m scripts.import_folder path/to/courses
"""

import sys
from pathlib import Path

from sqlalchemy import select

from app.db import db_session, init_db
from app.ingest import file_to_text, upsert_document
from app.models import Course


def main(folder: Path) -> None:
    init_db()
    with db_session() as db:
        courses = {c.code: c.id for c in db.scalars(select(Course))}
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() not in (".pdf", ".txt", ".md"):
                continue
            code = path.parent.name.upper()
            course_id = courses.get(code)
            try:
                text = file_to_text(path.name, path.read_bytes())
            except Exception as exc:
                print(f"✗ {path}: {exc}")
                continue
            if len(text) < 50:
                print(f"✗ {path}: sem texto (PDF digitalizado?)")
                continue
            _, changed = upsert_document(db, title=path.stem, text=text, kind="upload", course_id=course_id,
                                         url=f"upload://{code if course_id else 'geral'}/{path.name}")
            print(f"{'✓' if changed else '='} {path.relative_to(folder)}  ({code if course_id else 'geral'})")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/courses"))
