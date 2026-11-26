"""Keeps the bot up to date with the school website.

Crawls the public /pt/ pages, the news and events listings and the PDFs they link
(regulations, tuition orders…). Unchanged pages are skipped by content hash.
Event pages also become Activities so students can ask "what's on?".
"""

import logging
import re
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from .config import get_settings
from .db import db_session, set_setting
from .ingest import pdf_to_text, upsert_document
from .models import Activity
from .textutil import clean_whitespace, find_pt_date_range

log = logging.getLogger(__name__)

USER_AGENT = "ISLA-Assistente-Bot/1.0 (school assistant; respects robots.txt)"  # ASCII only
SEED_PATHS = [
    "/pt/", "/pt/isla-media/noticias", "/pt/isla-media/eventos", "/pt/informacoes-academicas",
    "/pt/calendario-escolar-e-horarios", "/pt/emolumentos-e-propinas", "/pt/normas-e-regulamentos",
    "/pt/acao-social-bolsas-e-financiamentos", "/pt/candidaturas", "/pt/projetos", "/pt/contactos",
]
SKIP_PATTERNS = re.compile(r"/login|/administrator|/api/|/component/|\.(jpg|jpeg|png|gif|svg|zip|docx?|xlsx?|mp4)$", re.I)
MAX_LISTING_OFFSET = 24  # follow ?start=12, ?start=24 on listings, not the whole archive
MAX_PDF_BYTES = 15 * 1024 * 1024

_lock = threading.Lock()


def _normalize(url: str) -> str:
    p = urlparse(url)
    query = p.query if re.fullmatch(r"start=\d+", p.query or "") else ""
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", query, ""))


def _wanted(url: str, host: str) -> bool:
    p = urlparse(url)
    if p.netloc.lower() != host or p.scheme not in ("http", "https"):
        return False
    if SKIP_PATTERNS.search(p.path):
        return False
    if p.query:
        m = re.fullmatch(r"start=(\d+)", p.query)
        if not m or int(m.group(1)) > MAX_LISTING_OFFSET:
            return False
    return p.path.startswith("/pt/") or p.path == "/pt" or p.path.lower().endswith(".pdf")


def extract_page(html: str, base_url: str = "") -> tuple[str, str, list[str]]:
    """Return (title, main text, links) from a page.

    Links to documents (PDFs, forms) are written into the text as "name (link: URL)",
    so the assistant can hand students the direct link."""
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else None) or (soup.title.get_text() if soup.title else "")
    title = re.sub(r"\s+—\s+ISLA.*$", "", title).strip()
    links = [a["href"] for a in soup.find_all("a", href=True)]
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "iframe"]):
        tag.decompose()
    main = soup.select_one("main") or soup.select_one("article") or soup.body or soup
    for a in main.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(pdf|docx?|xlsx?)($|\?)", href, re.I) or href.startswith("mailto:"):
            label = a.get_text(" ", strip=True)
            target = href if href.startswith("mailto:") else urljoin(base_url, href)
            a.replace_with(f"{label} (link: {target})" if label else f"(link: {target})")
    lines = []
    for line in main.get_text("\n", strip=True).split("\n"):
        if line in ("Facebook", "LinkedIn", "WhatsApp", "X", "Partilhar", "Share"):
            continue
        lines.append(line)
    return title, clean_whitespace("\n".join(lines)), links


def _page_kind(path: str) -> str:
    if "/isla-media/eventos/" in path:
        return "event"
    if "/isla-media/noticias/" in path:
        return "news"
    return "page"


def _upsert_event_activity(db, url: str, title: str, text: str, today: date) -> None:
    body = text
    if body.startswith("Eventos"):
        body = body[len("Eventos"):].strip()
    if body.startswith(title):
        body = body[len(title):].strip()
    rng = find_pt_date_range(body, today)
    act = db.scalar(select(Activity).where(Activity.external_key == url))
    if not act:
        act = Activity(external_key=url, source="web", requires_application=False, is_published=True)
        db.add(act)
    act.title = title[:250]
    act.summary = body[:1500] or None
    act.url = url
    if rng:
        act.starts_on, act.ends_on = rng
    if not act.how_to_apply:
        act.how_to_apply = f"Informação e inscrições na página do evento: {url}"
    db.commit()


def crawl(max_pages: int | None = None, max_pdfs: int | None = None, time_budget: float | None = None) -> dict:
    """Run one crawl. Safe to call from a thread; concurrent calls are skipped.

    time_budget (seconds) is for serverless hosts: pages get ~60% of it, PDFs the rest.
    PDFs fetched in the last 7 days are skipped, so repeated short runs keep making progress."""
    if not _lock.acquire(blocking=False):
        return {"status": "already_running"}
    s = get_settings()
    max_pages = max_pages or s.crawl_max_pages
    max_pdfs = max_pdfs if max_pdfs is not None else s.crawl_max_pdfs
    base = s.site_base_url.rstrip("/")
    host = urlparse(base).netloc.lower()
    stats = {"pages": 0, "pdfs": 0, "changed": 0, "events": 0, "errors": 0}
    started = time.monotonic()
    try:
        robots = RobotFileParser()
        try:
            robots.parse(httpx.get(f"{base}/robots.txt", timeout=15, headers={"User-Agent": USER_AGENT}).text.splitlines())
        except httpx.HTTPError:
            robots = None

        queue = deque(_normalize(base + p) for p in SEED_PATHS)
        seen = set(queue)
        pdf_queue: list[tuple[str, str]] = []
        today = datetime.now(s.tz).date()

        with httpx.Client(timeout=30, follow_redirects=True, headers={
            "User-Agent": USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9"}) as client, db_session() as db:
            def left() -> float:
                return float("inf") if time_budget is None else time_budget - (time.monotonic() - started)

            while queue and stats["pages"] < max_pages:
                if time_budget is not None and left() < time_budget * 0.4:
                    stats["stopped_early"] = True
                    break
                url = queue.popleft()
                if robots and not robots.can_fetch(USER_AGENT, url):
                    continue
                if url.lower().endswith(".pdf"):
                    continue
                try:
                    r = client.get(url)
                    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                        continue
                    title, text, links = extract_page(r.text, url)
                except Exception as exc:
                    stats["errors"] += 1
                    log.warning("crawl %s failed: %s", url, exc)
                    continue
                stats["pages"] += 1
                path = urlparse(url).path
                kind = _page_kind(path)

                if len(text) > 80 and not urlparse(url).query:
                    rng = find_pt_date_range(text, today) if kind in ("news", "event") else None
                    _, changed = upsert_document(db, title=title or path, text=text, url=url, kind=kind,
                                                 published_on=rng[0] if rng else None)
                    stats["changed"] += int(changed)
                    if kind == "event":
                        _upsert_event_activity(db, url, title, text, today)
                        stats["events"] += 1

                for href in links:
                    full = _normalize(urljoin(url, href))
                    if full in seen or not _wanted(full, host):
                        continue
                    seen.add(full)
                    if full.lower().endswith(".pdf"):
                        pdf_queue.append((full, title))
                    else:
                        queue.append(full)
                time.sleep(s.crawl_delay_seconds)

            fresh = _recent_pdf_urls(db)
            todo = [(u, t) for u, t in pdf_queue if u not in fresh][:max_pdfs]
            for pdf_url, parent_title in todo:
                if left() < 20:
                    stats["stopped_early"] = True
                    break
                try:
                    _ingest_pdf(client, db, pdf_url, parent_title)
                    stats["pdfs"] += 1
                except Exception as exc:
                    stats["errors"] += 1
                    log.warning("pdf %s failed: %s", pdf_url, exc)
                time.sleep(s.crawl_delay_seconds)

            stats["seconds"] = round(time.monotonic() - started)
            set_setting(db, "crawl_last_run", datetime.now(s.tz).isoformat(timespec="seconds"))
            set_setting(db, "crawl_last_stats", str(stats))
        log.info("crawl finished: %s", stats)
        return {"status": "ok", **stats}
    finally:
        _lock.release()


def _recent_pdf_urls(db) -> set[str]:
    from .models import Document
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    return set(db.scalars(select(Document.url).where(Document.kind == "pdf", Document.updated_at >= cutoff)))


def _ingest_pdf(client: httpx.Client, db, url: str, parent_title: str) -> None:
    with client.stream("GET", url) as r:
        if r.status_code != 200:
            return
        if int(r.headers.get("content-length") or 0) > MAX_PDF_BYTES:
            return
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                return
    text = pdf_to_text(data)
    if len(text) < 50:
        return
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    title = re.sub(r"[_\-]+", " ", name).strip()
    upsert_document(db, title=f"{title} (PDF · {parent_title})", text=text, url=url, kind="pdf")


def is_running() -> bool:
    return _lock.locked()


def start_scheduler() -> None:
    """Background loop: crawl shortly after startup, then every N hours."""
    s = get_settings()
    if not s.crawl_enabled:
        return

    interval = max(1, s.crawl_interval_hours) * 3600

    def seconds_until_due() -> float:
        from .db import get_setting
        with db_session() as db:
            last = get_setting(db, "crawl_last_run")
        if not last:
            return 20
        elapsed = (datetime.now(s.tz) - datetime.fromisoformat(last)).total_seconds()
        return max(20, interval - elapsed)

    def loop():
        while True:
            time.sleep(seconds_until_due())
            try:
                crawl()
            except Exception:
                log.exception("scheduled crawl failed")
                time.sleep(600)

    threading.Thread(target=loop, daemon=True, name="crawler").start()
