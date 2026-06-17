import os
from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA

load_dotenv(override=True)


def get_llm_client(temperature=1.0, max_completion_tokens=8192):
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise ValueError("NVIDIA_API_KEY is not set in the environment.")
    return ChatNVIDIA(
        model="minimaxai/minimax-m3",
        api_key=api_key,
        temperature=temperature,
        top_p=0.95,
        max_tokens=max_completion_tokens,
    )
