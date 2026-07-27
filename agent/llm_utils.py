import json
import os
import random
import time
import traceback
from typing import Any

from agent.tools import llm_call, execute_tool, TOOL_DESCRIPTIONS
from config import LLM_BASE_URL, ensure_proxy

try:
    import httpx
except ImportError:
    httpx = None
try:
    import openai
except ImportError:
    openai = None


MAX_LLM_RETRIES = 5
MAX_VALIDATION_RETRIES = 3
MAX_BATCH_PINS = 16

_LLM_CALL_HISTORY: list[float] = []
_LLM_CALL_HISTORY_MAX = 50
_LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "0.5"))
_LLM_WINDOW_SEC = 60.0
_LLM_MAX_PER_WINDOW = int(os.getenv("LLM_MAX_PER_MINUTE", "20"))
_LAST_429_TIME: float = 0.0


class AgentLLMError(Exception):
    """Raised when an LLM call fails after exhausting all retries."""


def _rate_limit() -> None:
    global _LLM_CALL_HISTORY, _LAST_429_TIME
    now = time.time()

    if _LAST_429_TIME > 0:
        since_429 = now - _LAST_429_TIME
        if since_429 < _LLM_WINDOW_SEC:
            wait = _LLM_WINDOW_SEC - since_429 + random.uniform(0, 2)
            print(f"  Rate limiter: 429 cooldown {wait:.1f}s (since_429={since_429:.0f}s)")
            time.sleep(wait)
            now = time.time()

    cutoff = now - _LLM_WINDOW_SEC
    _LLM_CALL_HISTORY = [t for t in _LLM_CALL_HISTORY if t > cutoff]

    if _LLM_CALL_HISTORY:
        elapsed = now - _LLM_CALL_HISTORY[-1]
        if elapsed < _LLM_MIN_INTERVAL:
            wait = _LLM_MIN_INTERVAL - elapsed + random.uniform(0, 1)
            print(f"  Rate limiter: waiting {wait:.1f}s (interval={_LLM_MIN_INTERVAL}s)")
            time.sleep(wait)

    if len(_LLM_CALL_HISTORY) >= _LLM_MAX_PER_WINDOW:
        oldest = _LLM_CALL_HISTORY[0]
        wait = oldest + _LLM_WINDOW_SEC - time.time() + random.uniform(0, 0.5)
        if wait > 0:
            print(f"  Rate limiter: waiting {wait:.1f}s (window limit={_LLM_MAX_PER_WINDOW}/{_LLM_WINDOW_SEC}s)")
            time.sleep(wait)


def _record_call() -> None:
    global _LLM_CALL_HISTORY
    cutoff = time.time() - _LLM_WINDOW_SEC
    _LLM_CALL_HISTORY = [t for t in _LLM_CALL_HISTORY if t > cutoff]
    _LLM_CALL_HISTORY.append(time.time())
    if len(_LLM_CALL_HISTORY) > _LLM_CALL_HISTORY_MAX:
        _LLM_CALL_HISTORY = _LLM_CALL_HISTORY[-_LLM_CALL_HISTORY_MAX:]


_LLM_TOTAL_TIMEOUT = 600.0  # 10 minutes for complex operations like netlist generation


def _is_connection_error(e: Exception) -> bool:
    msg = str(e).lower()
    if "connection error" in msg or "connection refused" in msg or "connection reset" in msg or "connect error" in msg:
        return True
    if httpx is not None and isinstance(e, httpx.ConnectError):
        return True
    if httpx is not None and isinstance(e, httpx.RemoteProtocolError):
        return True
    if openai is not None and isinstance(e, openai.APIConnectionError):
        return True
    return False


def _retry_llm_call(system: str, user: str, stage: str = "", tools: list[dict] | None = None) -> str:
    global _LAST_429_TIME

    # Inject skill content for this pipeline stage
    try:
        from agent.skill_loader import load_skill_for_stage
        skill_content = load_skill_for_stage(stage)
        if skill_content:
            system = skill_content + "\n" + system
    except Exception:
        pass  # skill loader failure should not break the pipeline

    t0 = time.time()
    for attempt in range(MAX_LLM_RETRIES):
        elapsed = time.time() - t0
        if elapsed > _LLM_TOTAL_TIMEOUT:
            raise AgentLLMError(
                f"LLM call timed out after {elapsed:.0f}s "
                f"({attempt} attempts){': ' + stage if stage else ''}"
            )
        try:
            _rate_limit()
            result = llm_call(system, user, tools=tools)
            _record_call()
            prefix = f" ({stage})" if stage else ""
            snippet = result[:300].replace('\n', ' ')
            print(f"[LLM{prefix}] {snippet}{'...' if len(result) > 300 else ''}")
            return result
        except Exception as e:
            prefix = f" ({stage})" if stage else ""
            print(f"LLM call failed{prefix} (attempt {attempt + 1}/{MAX_LLM_RETRIES}): {e}")
            is_429 = "429" in str(e) or "Too Many Requests" in str(e)
            is_conn_err = _is_connection_error(e)
            if is_429:
                _LAST_429_TIME = time.time()
            elif is_conn_err:
                print(f"  -> Connection error — re-checking proxy at {LLM_BASE_URL}...")
                ensure_proxy(timeout=10)
            elapsed = time.time() - t0
            if elapsed > _LLM_TOTAL_TIMEOUT:
                raise AgentLLMError(
                    f"LLM call timed out after {elapsed:.0f}s "
                    f"({attempt + 1} attempts){': ' + stage if stage else ''}"
                )
            if attempt < MAX_LLM_RETRIES - 1:
                delay = (60.0 if is_429 else 2 ** (attempt + 3)) + random.uniform(0, 4)
                print(f"  Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                traceback.print_exc()
    raise AgentLLMError(f"LLM call failed after {MAX_LLM_RETRIES} retries{': ' + stage if stage else ''}")


def _call_llm(system: str, user: str, stage: str = "", retries: int = MAX_LLM_RETRIES, tools: list[dict] | None = None) -> str:
    return _retry_llm_call(system, user, stage, tools=tools)


def _call_llm_with_tools(system: str, user: str, max_tool_rounds: int = 2) -> str:
    from agent.emit_utils import _clean_json

    conversation = user
    for _ in range(max_tool_rounds):
        response = _retry_llm_call(system, conversation, stage="netlist")
        if not response:
            return ""
        try:
            parsed = json.loads(_clean_json(response))
        except (json.JSONDecodeError, TypeError):
            return response
        if isinstance(parsed, dict) and "_tool" in parsed:
            tool_name = parsed["_tool"]
            tool_args = parsed.get("args", {})
            result = execute_tool(tool_name, **tool_args)
            conversation += f"\n\nI called tool '{tool_name}' and got:\n{json.dumps(result, indent=2)}\n\nContinue with the netlist JSON array."
            continue
        return response
    return response
