import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:4010/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "opencode/deepseek-v4-flash-free")


def get_llm_client(temperature=1.0, max_tokens=8192):
    return ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key="not-needed",
        temperature=temperature,
        max_tokens=max_tokens,
    )
