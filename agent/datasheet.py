"""Datasheet text fetcher with targeted critical-section extraction and persistent caching.

Fetches datasheet content from KiCad symbol URLs. Two extraction paths:
  1. Legacy: fetch_datasheet_text(url, offset, length) — progressive chunked
     access (up to MAX_TOTAL chars), used by older callers.
  2. Targeted: extract_critical_specs(url) — downloads up to CRITICAL_MAX chars,
     regex-scans for engineering-critical sections (pinout, electrical
     characteristics, operating conditions), and returns the matching block
     (or first CRITICAL_FALLBACK chars as a fallback).

Content is cached persistently in ``datasheet_cache.sqlite`` so repeated
runs (or different pipeline instances) reuse previously downloaded text.
"""

import hashlib
import io
import logging
import os
import re
import sqlite3
import threading
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from pypdf import PdfReader

logger = logging.getLogger(__name__)

MAX_TOTAL = 500
_cached_texts: dict[str, str] = {}
_FETCH_TIMEOUT = 5
_DATASHEET_TIMEOUT = 15  # seconds max for a full fetch + parse + fallback
_BAD_DOMAINS = {"buydisplay.com"}
_KNOWN_FAIL: set[str] = set()

CRITICAL_MAX = 3000
CRITICAL_FALLBACK = 1000
_extended_cache: dict[str, str] = {}

# ── Persistent SQLite cache ─────────────────────────────────────────────

_CACHE_DIR = Path(__file__).resolve().parent / "data"
_CACHE_DB = _CACHE_DIR / "datasheet_cache.sqlite"


def _ensure_cache_db():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS datasheet_cache (
            url_hash TEXT PRIMARY KEY,
            url      TEXT NOT NULL,
            content  TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )"""
    )
    con.commit()
    con.close()


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _cache_lookup(url: str) -> str | None:
    try:
        _ensure_cache_db()
        con = sqlite3.connect(_CACHE_DB)
        row = con.execute(
            "SELECT content FROM datasheet_cache WHERE url_hash = ?",
            (_cache_key(url),),
        ).fetchone()
        con.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug("Datasheet cache lookup failed: %s", e)
        return None


def _cache_store(url: str, content: str):
    try:
        _ensure_cache_db()
        import time
        con = sqlite3.connect(_CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO datasheet_cache (url_hash, url, content, fetched_at) VALUES (?, ?, ?, ?)",
            (_cache_key(url), url, content, time.time()),
        )
        con.commit()
        con.close()
    except Exception as e:
        logger.debug("Datasheet cache write failed: %s", e)


def clear_persistent_cache():
    """Delete all cached datasheet content from the on-disk cache."""
    try:
        if _CACHE_DB.is_file():
            _CACHE_DB.unlink()
            logger.info("Datasheet persistent cache cleared")
    except Exception as e:
        logger.warning("Failed to clear datasheet cache: %s", e)

# Vendor-agnostic section headers that contain the engineering data the
# validate LLM actually needs to see (pinout, voltages, specs).
_SECTION_PATTERNS = re.compile(
    r'(pin\s+(?:configuration|description|functions?|assignments?|layout|connections?|diagram)'
    r'|terminal\s+functions?'
    r'|electrical\s+characteristics?'
    r'|recommended\s+operating\s+conditions?'
    r'|absolute\s+maximum\s+ratings?'
    r'|specifications?'
    r'|ordering\s+information'
    r'|supply\s+(?:voltage|current|requirements?)'
    r'|operating\s+voltage'
    r'|power\s+(?:supply|consumption|requirements?)'
    r'|voltage\s+range)',
    re.IGNORECASE
)


def _should_skip(url: str) -> bool:
    if not url:
        return True
    for d in _BAD_DOMAINS:
        if d in url:
            return True
    if url in _KNOWN_FAIL:
        return True
    return False


def _mark_failed(url: str):
    _KNOWN_FAIL.add(url)


def _pdf_to_text(content: bytes) -> str:
    """Extract text from raw PDF bytes using pypdf."""
    try:
        reader = PdfReader(io.BytesIO(content))
        text = []
        for page in reader.pages[:3]:
            t = page.extract_text()
            if t:
                text.append(t)
        return re.sub(r'\s+', ' ', " ".join(text)).strip()
    except Exception as e:
        print(f"[datasheet] PDF parse failed: {e}")
        return ""


def _try_html_fallback(url: str) -> str:
    """Try to construct an HTML product page from a known PDF URL pattern."""
    patterns = [
        # Maxim/ADI: https://www.analog.com/media/en/.../datasheet/DS18B20.pdf
        # → https://www.analog.com/en/products/ds18b20.html
        (r'analog\.com/media/en.*/datasheet/(.+)\.pdf',
         lambda m: f"https://www.analog.com/en/products/{m.group(1).lower()}.html"),
        # TI: https://www.ti.com/lit/ds/symlink/tmp117.pdf
        # → https://www.ti.com/product/TMP117
        (r'ti\.com/lit/ds/symlink/(.+)\.pdf',
         lambda m: f"https://www.ti.com/product/{m.group(1).upper()}"),
        # Espressif: https://www.espressif.com/.../esp32-c3_datasheet.pdf
        # → https://www.espressif.com/en/products/socs/esp32-c3
        (r'espressif\.com.*/esp32-c3',
         lambda m: "https://www.espressif.com/en/products/socs/esp32-c3"),
        # ST: https://www.st.com/resource/en/datasheet/stm32f411.pdf
        # → https://www.st.com/en/microcontrollers-microprocessors/stm32f411.html
        (r'st\.com/resource/en/datasheet/(.+)\.pdf',
         lambda m: f"https://www.st.com/en/microcontrollers-microprocessors/{m.group(1).lower()}.html"),
        # Microchip: https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328P_datasheet.pdf
        # → https://www.microchip.com/en/product/ATmega328P
        (r'ww1?\.microchip\.com/downloads/en/DeviceDoc/([A-Za-z0-9_\-]+?)(?:[_\.].*)?\.pdf',
         lambda m: f"https://www.microchip.com/en/product/{m.group(1).rstrip('_-')}"),
    ]
    for pat, repl in patterns:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            try:
                resp = requests.get(repl(m), timeout=_FETCH_TIMEOUT, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                })
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "").lower()
                if "pdf" not in ctype and "html" in ctype:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                        tag.decompose()
                    text = soup.get_text(separator=" ", strip=True)
                    text = re.sub(r'\s+', ' ', text)
                    return text[:MAX_TOTAL]
            except requests.RequestException:
                continue
    _mark_failed(url)
    return ""


def _download_text(url: str) -> str:
    """Download and extract clean text from a URL (HTML or PDF)."""
    if _should_skip(url):
        return ""
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").lower()
        if "pdf" in ctype:
            text = _pdf_to_text(resp.content)
            if text:
                return text[:MAX_TOTAL]
            return _try_html_fallback(url)[:MAX_TOTAL]
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text)
        return text[:MAX_TOTAL]
    except requests.RequestException as e:
        print(f"[datasheet] fetch failed for {url[:60]}...: {e}")
        _mark_failed(url)
        return _try_html_fallback(url)[:MAX_TOTAL]
    except Exception as e:
        print(f"[datasheet] parse failed for {url[:60]}...: {e}")
        _mark_failed(url)
        return ""


def _get_cached_or_fetch(url: str, extended: bool = False) -> str:
    """Check persistent cache → in-memory cache → download, caching at each level."""
    if not url:
        return ""
    # 1) in-memory cache
    cache = _extended_cache if extended else _cached_texts
    if url in cache:
        return cache[url]
    # 2) persistent cache
    persisted = _cache_lookup(url)
    if persisted is not None:
        cache[url] = persisted
        return persisted
    # 3) download
    text = _download_extended(url) if extended else _download_text(url)
    cache[url] = text
    if text:
        _cache_store(url, text)
    return text


def fetch_datasheet_text(url: str, offset: int = 0, length: int = 500) -> str:
    """Return a slice of cleaned text from a datasheet URL.

    The full text (up to MAX_TOTAL chars) is cached on first access
    so subsequent offset/length calls are instant.  Persisted across
    process restarts via ``datasheet_cache.sqlite``.
    """
    if not url:
        return ""
    text = _get_cached_or_fetch(url, extended=False)
    return text[offset:offset+length]


def clear_cache():
    _cached_texts.clear()
    _extended_cache.clear()


# ── Targeted critical-section extraction ─────────────────────────────────


def _download_extended(url: str) -> str:
    """Download up to CRITICAL_MAX chars, cached separately from the 500-char path."""
    if not url or _should_skip(url):
        return ""
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").lower()
        if "pdf" in ctype:
            text = _pdf_to_text(resp.content)
            if text:
                return text[:CRITICAL_MAX]
            text = _try_html_fallback(url)[:CRITICAL_MAX]
            return text
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text)
        return text[:CRITICAL_MAX]
    except requests.RequestException as e:
        print(f"[datasheet] extended fetch failed for {url[:60]}...: {e}")
        _mark_failed(url)
        text = _try_html_fallback(url)[:CRITICAL_MAX]
        return text
    except Exception as e:
        print(f"[datasheet] extended parse failed for {url[:60]}...: {e}")
        _mark_failed(url)
        return ""


def _extract_critical_block(text: str) -> str:
    """Find the first critical-section header and return the following ~500 chars."""
    match = _SECTION_PATTERNS.search(text)
    if match:
        start = match.start()
        block = text[start:start + 500]
        return block.strip()
    return ""


def extract_critical_specs(url: str) -> str:
    """Return targeted engineering text from a datasheet URL (max _DATASHEET_TIMEOUT s).

    Uses persistent cache so repeated calls are instant.
    """
    if not url:
        return ""
    # Check caches first (fast path, no thread needed)
    if url in _extended_cache:
        cached = _extended_cache[url]
        if cached:
            return _extract_critical_block(cached) or cached[:CRITICAL_FALLBACK].strip()
        return ""
    persisted = _cache_lookup(url)
    if persisted is not None:
        _extended_cache[url] = persisted
        return _extract_critical_block(persisted) or persisted[:CRITICAL_FALLBACK].strip()
    # Threaded fetch with timeout for first download
    result_holder = {}
    def target():
        try:
            result_holder["result"] = _extract_critical_specs_impl(url)
        except BaseException as e:
            result_holder["exception"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=_DATASHEET_TIMEOUT)
    if t.is_alive():
        print(f"[datasheet] extract_critical_specs timed out after {_DATASHEET_TIMEOUT}s for {url[:60]}")
        return ""
    if "exception" in result_holder:
        print(f"[datasheet] extract_critical_specs failed for {url[:60]}: {result_holder['exception']}")
        return ""
    result = result_holder.get("result") or ""
    if result:
        _cache_store(url, result)
    return result


def _extract_critical_specs_impl(url: str) -> str:
    """Return targeted engineering text from a datasheet URL.

    Strategy:
      1. Download up to 3000 chars of cleaned text.
      2. Regex-scan for critical section headers (pinout, electrical
         characteristics, operating conditions).
      3. Return the ~500-char block following the first matched header.
      4. Fallback: first 1000 chars of raw text.
    """
    text = _download_extended(url)
    if not text:
        return ""
    block = _extract_critical_block(text)
    if block:
        return block
    return text[:CRITICAL_FALLBACK].strip()
