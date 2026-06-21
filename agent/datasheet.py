"""Datasheet text fetcher with targeted critical-section extraction.

Fetches datasheet content from KiCad symbol URLs. Two extraction paths:
  1. Legacy: fetch_datasheet_text(url, offset, length) — progressive chunked
     access (up to MAX_TOTAL chars), used by older callers.
  2. Targeted: extract_critical_specs(url) — downloads up to CRITICAL_MAX chars,
     regex-scans for engineering-critical sections (pinout, electrical
     characteristics, operating conditions), and returns the matching block
     (or first CRITICAL_FALLBACK chars as a fallback).

The targeted path is preferred for the validate LLM so it sees actual
engineering data (voltage ranges, pin functions) rather than marketing fluff.
"""

import io
import re
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_TOTAL = 500
_cached_texts: dict[str, str] = {}
_FETCH_TIMEOUT = 5
_BAD_DOMAINS = {"buydisplay.com"}
_KNOWN_FAIL: set[str] = set()

CRITICAL_MAX = 3000
CRITICAL_FALLBACK = 1000
_extended_cache: dict[str, str] = {}

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


def fetch_datasheet_text(url: str, offset: int = 0, length: int = 500) -> str:
    """Return a slice of cleaned text from a datasheet URL.

    The full text (up to MAX_TOTAL chars) is cached on first access
    so subsequent offset/length calls are instant.
    """
    if not url:
        return ""
    if url not in _cached_texts:
        _cached_texts[url] = _download_text(url)
    return _cached_texts[url][offset:offset+length]


def clear_cache():
    _cached_texts.clear()
    _extended_cache.clear()


# ── Targeted critical-section extraction ─────────────────────────────────


def _download_extended(url: str) -> str:
    """Download up to CRITICAL_MAX chars, cached separately from the 500-char path."""
    if not url or _should_skip(url):
        return ""
    if url in _extended_cache:
        return _extended_cache[url]
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
                _extended_cache[url] = text[:CRITICAL_MAX]
                return _extended_cache[url]
            text = _try_html_fallback(url)[:CRITICAL_MAX]
            _extended_cache[url] = text
            return text
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text)
        _extended_cache[url] = text[:CRITICAL_MAX]
        return _extended_cache[url]
    except requests.RequestException as e:
        print(f"[datasheet] extended fetch failed for {url[:60]}...: {e}")
        _mark_failed(url)
        text = _try_html_fallback(url)[:CRITICAL_MAX]
        _extended_cache[url] = text
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
