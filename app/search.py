"""Full-text search over school documents (SQLite FTS5, BM25 ranking)."""

import hashlib
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import is_postgres
from .models import Document
from .textutil import clean_whitespace, strip_accents

CHUNK_CHARS = 1100
CHUNK_OVERLAP = 150

_STOPWORDS = set("""
a o as os um uma uns umas de do da dos das no na nos nas em ao aos à às por para pelo pela
com sem que qual quais quando quanto como onde porque se e ou mas é são ser foi sou tem ter
há eu tu ele ela nós vós eles elas me te lhe meu minha meus minhas teu tua isto isso aquilo
este esta esse essa sobre mais menos muito pouco já ainda também só não sim
the an of to in on for and or is are was what when where how which who why do does my me i
you your it this that with about can please
""".split())


def content_hash(text_: str) -> str:
    return hashlib.sha256(text_.encode("utf-8", "ignore")).hexdigest()


def chunk_text(text_: str) -> list[str]:
    """Split on paragraph boundaries into ~CHUNK_CHARS pieces with a small overlap."""
    text_ = clean_whitespace(text_)
    if not text_:
        return []
    paragraphs = [p.strip() for p in text_.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        while len(p) > CHUNK_CHARS:  # very long paragraph: hard split
            cut = p.rfind(" ", 0, CHUNK_CHARS) if " " in p[:CHUNK_CHARS] else CHUNK_CHARS
            head = p[:cut]
            restart = p.find(" ", max(0, cut - CHUNK_OVERLAP)) + 1 or cut
            p = p[restart:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(p) + 2 > CHUNK_CHARS and current:
            chunks.append(current)
            tail = current[-CHUNK_OVERLAP:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else ""  # start on a word boundary
            current = (tail + "\n\n" + p) if tail else p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def index_document(db: Session, doc: Document) -> int:
    """(Re)build the search chunks for one document. Caller commits."""
    db.execute(text("DELETE FROM chunks WHERE doc_id = :id"), {"id": doc.id})
    pieces = chunk_text(doc.text)
    if is_postgres(db):
        # Accents are stripped before indexing (and in queries) so "calendario" finds "calendário".
        sql = text("INSERT INTO chunks (content, title, doc_id, url, course_id, tsv) VALUES (:c, :t, :d, :u, :k,"
                   " setweight(to_tsvector('portuguese', :pt), 'A') || to_tsvector('portuguese', :pc))")
        for piece in pieces:
            db.execute(sql, {"c": piece, "t": doc.title, "d": doc.id, "u": doc.url or "", "k": doc.course_id,
                             "pt": strip_accents(doc.title or ""), "pc": strip_accents(piece)})
    else:
        sql = text("INSERT INTO chunks (content, title, doc_id, url, course_id) VALUES (:c, :t, :d, :u, :k)")
        for piece in pieces:
            db.execute(sql, {"c": piece, "t": doc.title, "d": doc.id, "u": doc.url or "", "k": doc.course_id})
    return len(pieces)


def delete_document(db: Session, doc: Document) -> None:
    db.execute(text("DELETE FROM chunks WHERE doc_id = :id"), {"id": doc.id})
    db.delete(doc)


def _terms(query: str) -> list[str]:
    words = re.findall(r"[\wÀ-ÿ]+", query.lower())
    out = []
    for w in words:
        w = re.sub(r"[^a-z0-9]", "", strip_accents(w))
        if len(w) >= 3 and w not in _STOPWORDS:
            out.append(w)
    return list(dict.fromkeys(out))


def _sqlite_query(terms: list[str]) -> str:
    # prefix match helps with Portuguese plurals/inflections (propina → propinas)
    return " OR ".join(f'"{w[:7]}"*' if len(w) > 5 else f'"{w}"' for w in terms)


def _pg_query(terms: list[str]) -> str:
    # The 'portuguese' config stems each term (propinas → propin) before the prefix match.
    return " | ".join(f"{w}:*" for w in terms)


def search(db: Session, query: str, course_ids: list[int] | None = None, limit: int = 6) -> list[dict]:
    """Return the best chunks. course_ids=None → search everything;
    otherwise general documents plus the given courses only."""
    terms = _terms(query)
    if not terms:
        return []
    allowed = ",".join(str(int(c)) for c in (course_ids or [])) or "-1"
    params: dict = {"lim": limit * 3}
    if is_postgres(db):
        params["q"] = _pg_query(terms)
        sql = ("SELECT content, title, doc_id, url, course_id, ts_rank_cd(tsv, q) AS score "
               "FROM chunks, to_tsquery('portuguese', :q) q WHERE tsv @@ q")
        if course_ids is not None:
            sql += f" AND (course_id IS NULL OR course_id IN ({allowed}))"
        sql += " ORDER BY score DESC LIMIT :lim"
    else:
        params["q"] = _sqlite_query(terms)
        sql = ("SELECT content, title, doc_id, url, course_id, bm25(chunks, 1.0, 2.0) AS score "
               "FROM chunks WHERE chunks MATCH :q")
        if course_ids is not None:
            sql += f" AND (course_id IS NULL OR course_id = '' OR course_id IN ({allowed}))"
        sql += " ORDER BY score LIMIT :lim"
    rows = db.execute(text(sql), params).mappings().all()

    results, per_doc = [], {}
    for r in rows:  # at most 2 chunks per document, for variety
        if per_doc.get(r["doc_id"], 0) >= 2:
            continue
        per_doc[r["doc_id"]] = per_doc.get(r["doc_id"], 0) + 1
        results.append({"title": r["title"], "url": r["url"] or None, "text": r["content"], "doc_id": r["doc_id"]})
        if len(results) >= limit:
            break
    return results
