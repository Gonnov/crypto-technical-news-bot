from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import settings
from .summarizer import DigestBundle


def _root() -> Path:
    p = Path(settings.data_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today() -> str:
    return date.today().isoformat()


def save_payload(payload: dict[str, Any], day: str | None = None) -> Path:
    day = day or _today()
    path = _root() / f"{day}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load_payload(day: str | None = None) -> dict[str, Any] | None:
    day = day or _today()
    path = _root() / f"{day}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_digest(bundle: DigestBundle, markdown: str, day: str | None = None) -> None:
    day = day or _today()
    (_root() / f"{day}.digest.json").write_text(bundle.model_dump_json(indent=2))
    (_root() / f"{day}.digest.md").write_text(markdown)


def load_digest_bundle(day: str | None = None) -> DigestBundle | None:
    day = day or _today()
    path = _root() / f"{day}.digest.json"
    if not path.exists():
        return None
    return DigestBundle.model_validate_json(path.read_text())


def load_digest_markdown(day: str | None = None) -> str | None:
    day = day or _today()
    path = _root() / f"{day}.digest.md"
    return path.read_text() if path.exists() else None


SPOTLIGHT_HISTORY_FILE = "spotlight_history.json"

_SUFFIX_RE = re.compile(
    r"\b(v\d+(\.\d+)?|blue|endgame|dao|finance|protocol|money|network|labs?|foundation)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Canonical form for dedup: lowercase, strip common suffixes/versions."""
    s = (name or "").lower().strip()
    s = _SUFFIX_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_history() -> dict[str, Any]:
    path = _root() / SPOTLIGHT_HISTORY_FILE
    if not path.exists():
        return {"names": [], "normalized": []}
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return {"names": [], "normalized": []}
    names = raw.get("names", [])
    normalized = raw.get("normalized") or [_normalize(n) for n in names]
    return {"names": names, "normalized": normalized}


def load_recent_spotlights() -> list[str]:
    """Return all previously covered spotlight names (display form)."""
    return _load_history()["names"]


def spotlight_already_covered(name: str) -> bool:
    norm = _normalize(name)
    if not norm:
        return False
    return norm in set(_load_history()["normalized"])


def record_spotlight(name: str) -> None:
    data = _load_history()
    data["names"].append(name)
    data["normalized"].append(_normalize(name))
    (_root() / SPOTLIGHT_HISTORY_FILE).write_text(json.dumps(data, indent=2))


def last_updated(day: str | None = None) -> datetime | None:
    day = day or _today()
    path = _root() / f"{day}.json"
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


# ---------- Article map: short_id -> {source, title, url, image_url?} ----------

ARTICLES_FILE = "articles.json"


def _articles_path() -> Path:
    return _root() / ARTICLES_FILE


def save_articles(mapping: dict[str, dict[str, Any]]) -> None:
    """Persist the article lookup table used for Telegram button callbacks.

    Telegram restricts callback_data to 64 bytes, so we reference articles via
    short IDs and resolve them here when a button is pressed.
    """
    _articles_path().write_text(json.dumps(mapping, indent=2, default=str))


def load_articles() -> dict[str, dict[str, Any]]:
    path = _articles_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


def get_article(article_id: str) -> dict[str, Any] | None:
    return load_articles().get(article_id)


def upsert_articles(mapping: dict[str, dict[str, Any]]) -> None:
    """Merge new articles into the existing map (does not evict)."""
    existing = load_articles()
    existing.update(mapping)
    save_articles(existing)
