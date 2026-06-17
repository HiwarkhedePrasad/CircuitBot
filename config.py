import os
from dotenv import load_dotenv

load_dotenv(override=True)

def get_llm_client(temperature=1.0, max_completion_tokens=8192):
    nvidia_key = os.environ.get("NVIDIA_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")

    if nvidia_key:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        return ChatNVIDIA(
            model="minimaxai/minimax-m3",
            api_key=nvidia_key,
            temperature=temperature,
            top_p=0.95,
            max_tokens=max_completion_tokens,
        )
    elif groq_key:
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            raise RuntimeError("langchain-groq is required for Groq support. Please install it.")
        
        return ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=groq_key,
            temperature=temperature,
            max_tokens=max_completion_tokens,
        )
    else:
        raise ValueError("Neither NVIDIA_API_KEY nor GROQ_API_KEY is set in the environment.")
