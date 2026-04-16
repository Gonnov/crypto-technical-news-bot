"""Fetch article body text on demand — used when the user clicks 'More info'.

Two code paths:
  - Messari (JS-rendered) -> Playwright
  - Everything else       -> httpx + a simple main-content heuristic

The goal is *enough* clean text for Gemini to write a detailed summary — we do
not need a perfect Readability implementation.
"""
from __future__ import annotations

import html as _html
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", re.I | re.S)
_WHITESPACE_RE = re.compile(r"[ \t]+")

# Best-effort CSS selectors for article body in common CMSes (Wordpress, Ghost,
# Next.js blogs). First match wins.
_ARTICLE_BLOCK_PATTERNS = [
    re.compile(r'<article[^>]*>(.*?)</article>', re.I | re.S),
    re.compile(r'<main[^>]*>(.*?)</main>', re.I | re.S),
]

# Fallback: walk every <p> tag with substantial text content. Works on any CMS
# because articles are almost always paragraph-based.
_PARAGRAPH_RE = re.compile(r"<p[^>]*>([\s\S]*?)</p>", re.I)
_HEADING_RE = re.compile(r"<(h[1-4])[^>]*>([\s\S]*?)</\1>", re.I)


def _clean(text: str, max_chars: int = 12_000) -> str:
    """Strip tags + normalise whitespace; truncate to keep Gemini prompts tight."""
    if not text:
        return ""
    text = _SCRIPT_STYLE_RE.sub("", text)
    text = text.replace("</p>", "\n\n").replace("<br />", "\n").replace("<br>", "\n")
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    text = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _extract_article_body(html: str) -> str:
    """Prefer CMS containers; fall back to stitching substantial <p> + <h*> tags."""
    for pat in _ARTICLE_BLOCK_PATTERNS:
        m = pat.search(html)
        if m:
            candidate = _clean(m.group(1))
            if len(candidate) > 400:
                return candidate
    # Fallback: all <p>/<h*> with >=60 chars of real text
    parts: list[str] = []
    for m in _PARAGRAPH_RE.finditer(html):
        text = _clean(m.group(1), max_chars=2000)
        if len(text) >= 60:
            parts.append(text)
    for m in _HEADING_RE.finditer(html):
        text = _clean(m.group(2), max_chars=200)
        if 10 <= len(text) <= 200:
            parts.append(f"\n## {text}")
    return _clean("\n\n".join(parts))


def _extract_title(html: str) -> str:
    m = re.search(r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"]([^'\"]+)", html, re.I)
    if m:
        return _html.unescape(m.group(1).strip())
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if m:
        return _html.unescape(m.group(1).strip())
    return ""


async def _fetch_generic(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"user-agent": UA})
        r.raise_for_status()
        html = r.text
    return {
        "title": _extract_title(html),
        "text": _extract_article_body(html),
        "url": url,
    }


async def _fetch_with_playwright(url: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(user_agent=UA)
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Give the React app time to render the article body.
            await page.wait_for_timeout(4000)
            title = await page.title()
            body = await page.evaluate(
                """() => {
                    const pick = sel => document.querySelector(sel);
                    const el =
                        pick('article') ||
                        pick('main') ||
                        pick('[class*="report" i]') ||
                        pick('[class*="content" i]') ||
                        document.body;
                    return el ? el.innerText : '';
                }"""
            )
        finally:
            await browser.close()
    return {"title": title, "text": _clean(body), "url": url}


async def fetch_article(url: str, source: str | None = None) -> dict[str, Any]:
    """Fetch article body. Messari needs JS rendering; others use plain httpx."""
    if not url:
        return {"title": "", "text": "", "url": url}
    use_playwright = (source or "").lower() == "messari" or "messari.io" in url
    try:
        if use_playwright:
            return await _fetch_with_playwright(url)
        return await _fetch_generic(url)
    except Exception as e:  # noqa: BLE001
        log.warning("Article fetch failed (%s): %s", url, e)
        return {"title": "", "text": "", "url": url, "error": str(e)}
