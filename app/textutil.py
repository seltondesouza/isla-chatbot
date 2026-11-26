"""Small helpers for Portuguese dates, money and text."""

import re
import unicodedata
from datetime import date, datetime

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]

_WEEKDAY_ALIASES = {
    "1": 0, "seg": 0, "segunda": 0, "mon": 0, "monday": 0,
    "2": 1, "ter": 1, "terca": 1, "tue": 1, "tuesday": 1,
    "3": 2, "qua": 2, "quarta": 2, "wed": 2, "wednesday": 2,
    "4": 3, "qui": 3, "quinta": 3, "thu": 3, "thursday": 3,
    "5": 4, "sex": 4, "sexta": 4, "fri": 4, "friday": 4,
    "6": 5, "sab": 5, "sabado": 5, "sat": 5, "saturday": 5,
    "7": 6, "dom": 6, "domingo": 6, "sun": 6, "sunday": 6,
}

_MONTH_INDEX = {name: i + 1 for i, name in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
     "agosto", "setembro", "outubro", "novembro", "dezembro"])}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def parse_weekday(value: str) -> int:
    key = strip_accents(value.strip().lower()).replace("-feira", "")
    if key in _WEEKDAY_ALIASES:
        return _WEEKDAY_ALIASES[key]
    raise ValueError(f"dia da semana inválido: {value!r}")


def parse_date(value: str | None) -> date | None:
    """Accepts 2026-11-26, 26/11/2026 and 26-11-2026."""
    if value is None or not str(value).strip():
        return None
    v = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"data inválida: {value!r} (usa AAAA-MM-DD ou DD/MM/AAAA)")


def parse_time(value: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})[:hH](\d{2})?\s*", value or "")
    if not m:
        raise ValueError(f"hora inválida: {value!r} (usa HH:MM)")
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mnt > 59:
        raise ValueError(f"hora inválida: {value!r}")
    return f"{h:02d}:{mnt:02d}"


def parse_money_cents(value: str) -> int:
    """'325,50' / '325.50' / '1.234,56' / '€ 90' → cents."""
    v = re.sub(r"[^\d,.\-]", "", str(value))
    if not v:
        raise ValueError(f"valor inválido: {value!r}")
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    else:
        v = v.replace(",", ".")
    return round(float(v) * 100)


def euros(cents: int) -> str:
    s = f"{cents / 100:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def format_date_pt(d: date) -> str:
    return f"{WEEKDAYS_PT[d.weekday()]}, {d.day} de {MONTHS_PT[d.month - 1]} de {d.year}"


_PT_RANGE = re.compile(
    r"\b(\d{1,2})(?:\s*(?:a|e|-|–)\s*(\d{1,2}))?\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?",
    re.IGNORECASE,
)


def find_pt_date_range(text: str, today: date) -> tuple[date, date] | None:
    """Find the first date like '2 a 5 de dezembro' or '12 de março de 2027' in text.

    When the year is missing, picks the year that puts the date closest to today.
    """
    for m in _PT_RANGE.finditer(text[:2000]):
        month = _MONTH_INDEX.get(strip_accents(m.group(3).lower()))
        if not month:
            continue
        d1, d2 = int(m.group(1)), int(m.group(2) or m.group(1))
        years = [int(m.group(4))] if m.group(4) else [today.year - 1, today.year, today.year + 1]
        best = None
        for y in years:
            try:
                start, end = date(y, month, d1), date(y, month, d2)
            except ValueError:
                continue
            if end < start:
                continue
            dist = abs((start - today).days)
            if best is None or dist < best[0]:
                best = (dist, start, end)
        if best:
            return best[1], best[2]
    return None


def clean_whitespace(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
