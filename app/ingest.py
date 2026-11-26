"""Turn files into searchable documents."""

import io
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Document
from .search import content_hash, index_document
from .textutil import clean_whitespace

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 200


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # broken page; keep the rest
            log.debug("pdf page failed: %s", exc)
    return clean_whitespace("\n\n".join(pages))


def file_to_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return pdf_to_text(data)
    if name.endswith((".txt", ".md", ".csv")):
        return clean_whitespace(data.decode("utf-8", errors="replace"))
    raise ValueError("Formato não suportado (usa PDF, TXT ou MD).")


def upsert_document(
    db: Session, *, title: str, text: str, url: str | None = None, kind: str = "page",
    course_id: int | None = None, published_on: date | None = None,
) -> tuple[Document, bool]:
    """Create or update a document by URL and reindex if the text changed.
    Returns (document, changed). Commits."""
    text = clean_whitespace(text)
    h = content_hash(f"{title}\n{text}")
    doc = db.scalar(select(Document).where(Document.url == url)) if url else None
    if doc and doc.content_hash == h:
        if kind == "pdf":  # remember we checked it, so the crawler can skip it for a week
            doc.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        return doc, False
    if not doc:
        doc = Document(url=url)
        db.add(doc)
    doc.title, doc.text, doc.kind, doc.course_id, doc.content_hash = title[:300], text, kind, course_id, h
    if published_on:
        doc.published_on = published_on
    db.flush()
    index_document(db, doc)
    db.commit()
    return doc, True
