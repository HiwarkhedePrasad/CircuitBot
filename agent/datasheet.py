"""Progressive datasheet text fetcher.

Fetches datasheet content from KiCad symbol URLs in small chunks.
The LLM sees the first 500 chars and can request the next 500
(501-1000) if it needs more context to validate a component.

Uses a simple in-memory cache to avoid re-downloading when
the LLM requests the second chunk.
"""

import io
import re
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_TOTAL = 1000
_cached_texts: dict[str, str] = {}


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
    ]
    for pat, repl in patterns:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            try:
                resp = requests.get(repl(m), timeout=8, headers={
                    "User-Agent": "CircuitBot/1.0",
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
    return ""


def _download_text(url: str) -> str:
    """Download and extract clean text from a URL (HTML or PDF)."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "CircuitBot/1.0",
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
        return _try_html_fallback(url)[:MAX_TOTAL]
    except Exception as e:
        print(f"[datasheet] parse failed for {url[:60]}...: {e}")
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
