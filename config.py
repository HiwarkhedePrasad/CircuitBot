import os
import subprocess
import sys
import time
import urllib.request
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:4010/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "opencode/deepseek-v4-flash-free")
_PROXY_READY_CACHE: float = 0.0
_PROXY_CHECK_INTERVAL = 5.0
_LLM_BASE_HOST = LLM_BASE_URL.rstrip("/").rsplit("/v1", 1)[0] if "/v1" in LLM_BASE_URL else LLM_BASE_URL.rstrip("/")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_NODE_BIN = os.path.join(_PROJECT_ROOT, "node_modules", ".bin")


def _proxy_ready() -> bool:
    try:
        req = urllib.request.Request(f"{_LLM_BASE_HOST}/v1/models", method="GET")
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:
        return False


def _find_opencode() -> str:
    """Find the opencode binary — check node_modules/.bin first, then PATH."""
    # Check node_modules/.bin
    if sys.platform == "win32":
        candidate = os.path.join(_NODE_BIN, "opencode.cmd")
    else:
        candidate = os.path.join(_NODE_BIN, "opencode")
    if os.path.isfile(candidate):
        return candidate
    # Fallback to PATH
    return "opencode"


def _start_opencode_proxy() -> bool:
    try:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.Popen(
            [_find_opencode(), "serve", "--headless"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=_PROJECT_ROOT,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def ensure_proxy(timeout: int = 30) -> bool:
    global _PROXY_READY_CACHE

    now = time.time()
    if _PROXY_READY_CACHE and now - _PROXY_READY_CACHE < _PROXY_CHECK_INTERVAL:
        return True

    if _proxy_ready():
        _PROXY_READY_CACHE = time.time()
        return True

    print(f"[config] LLM proxy not reachable at {LLM_BASE_URL}")
    print("[config] Attempting to start opencode serve --headless...")
    _start_opencode_proxy()

    for i in range(timeout):
        if _proxy_ready():
            _PROXY_READY_CACHE = time.time()
            print(f"[config] LLM proxy is ready after ~{i + 1}s")
            return True
        time.sleep(1)

    print(
        f"[config] WARNING: LLM proxy did not become ready after {timeout}s.\n"
        f"  Run 'opencode serve' in the project directory, or set LLM_BASE_URL in .env\n"
        f"  to point to any OpenAI-compatible endpoint."
    )
    return False


def get_llm_client(temperature=1.0, max_tokens=8192, request_timeout=300):
    return ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key="not-needed",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=request_timeout,
    )
