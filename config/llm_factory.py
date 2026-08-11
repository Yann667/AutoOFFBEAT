"""
llm_factory.py – Couche d'abstraction LLM pour AutoOFFBEAT.

Permet de basculer entre fournisseurs sans toucher au code des agents.
Configuration via .env :

    LLM_PROVIDER=ollama            # anthropic | gemini | ollama
    ANTHROPIC_MODEL=claude-sonnet-4-5
    GEMINI_MODEL=gemini-1.5-flash  # tier gratuit
    OLLAMA_MODEL=qwen2.5-coder:14b # 100% local
    OLLAMA_BASE_URL=http://localhost:11434

Les trois fournisseurs supportent le tool-calling natif de LangChain,
ce qui est indispensable pour l'architecture Superviseur + Tools.
"""

import os
from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

load_dotenv()


def get_llm(temperature: float = 0.0, provider: str | None = None) -> BaseChatModel:
    """Retourne une instance de ChatModel selon LLM_PROVIDER.

    `provider` peut être passé explicitement pour, par exemple, utiliser
    un petit modèle local pour le routage et Claude pour le debug lourd.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).lower()

    if provider == "anthropic":
        # Debug lourd / raisonnement complexe sur les logs de crash.
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            temperature=temperature,
            max_tokens=4096,
        )

    if provider == "gemini":
        # Tier gratuit Google AI Studio – bon compromis pour l'usage courant.
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            temperature=temperature,
        )

    if provider == "ollama":
        # 100% local, zéro coût, données confidentielles (cohérent avec
        # la philosophie "tout en local" d'AutoFLUKA).
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:14b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=temperature,
        )

    raise ValueError(
        f"LLM_PROVIDER inconnu : '{provider}'. "
        "Valeurs acceptées : anthropic | gemini | ollama"
    )


# Raccourcis sémantiques : on peut mixer les modèles par rôle.
def get_supervisor_llm() -> BaseChatModel:
    """Modèle du superviseur (routage + dialogue)."""
    return get_llm(temperature=0.0)


def get_debug_llm() -> BaseChatModel:
    """Modèle dédié à l'analyse des crashs (self-healing). On peut le
    forcer sur Anthropic via DEBUG_LLM_PROVIDER même si le reste tourne
    en local."""
    return get_llm(
        temperature=0.0,
        provider=os.getenv("DEBUG_LLM_PROVIDER", os.getenv("LLM_PROVIDER")),
    )
