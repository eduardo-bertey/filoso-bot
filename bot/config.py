"""Config — toda la configuración del bot en UN solo lugar.

Igual que la clase `Config` de `filosofia/model.py`: todos los parámetros
juntos, arriba, editables a mano.

Los `.toml` (config.toml / rules.toml) **pisan** estos valores si existen.
Para cambiar algo en vivo, usá los comandos de Telegram (`/addkey`, `/addtopic`,
`/usar`, ...), que escriben en esos archivos.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .rules import DEFAULT_RULES


# ----------------------------------------------------------------------
# Proveedores: id -> endpoint y modelos
# ----------------------------------------------------------------------
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "style": "openai",
        "base_url": "https://api.openai.com/v1",
        "chat_path": "/chat/completions",
        "models": ["gpt-4o-mini"],
        "enabled": False,
        "priority": 10,
        "timeout": 60,
    },
    "openrouter": {
        "style": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "chat_path": "/chat/completions",
        "models": ["meta-llama/llama-3.3-70b-instruct"],
        "enabled": False,
        "priority": 20,
        "timeout": 60,
        "extra_headers": {
            "HTTP-Referer": "https://github.com/emanuelbertey/FilosoIA",
            "X-Title": "FilosoBot",
        },
    },
    "groq": {
        "style": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "chat_path": "/chat/completions",
        "models": ["llama-3.3-70b-versatile"],
        "enabled": False,
        "priority": 20,
        "timeout": 45,
    },
    "mistral": {
        "style": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "chat_path": "/chat/completions",
        "models": ["mistral-small-latest"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "deepseek": {
        "style": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "chat_path": "/chat/completions",
        "models": ["deepseek-chat"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "nvidia": {
        "style": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "chat_path": "/chat/completions",
        "models": ["meta/llama-llama-3.3-70b-instruct"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "cerebras": {
        "style": "openai",
        "base_url": "https://api.cerebras.ai/v1",
        "chat_path": "/chat/completions",
        "models": ["llama-3.1-8b"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "github": {
        "style": "openai",
        "base_url": "https://models.github.ai/inference",
        "chat_path": "/chat/completions",
        "models": ["openai/gpt-4.1-mini"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "cloudflare": {
        "style": "openai",
        "base_url": "https://gateway.ai.cloudflare.com/v1",
        "chat_path": "/chat/completions",
        "models": [],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "anthropic": {
        "style": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "chat_path": "/messages",
        "models": ["claude-3-5-haiku-latest"],
        "enabled": False,
        "priority": 15,
        "timeout": 60,
    },
    "gemini": {
        "style": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "chat_path": "/models/{model}:generateContent",
        "models": ["gemini-2.0-flash"],
        "enabled": False,
        "priority": 15,
        "timeout": 60,
    },
    "llm7": {
        "style": "openai",
        "base_url": "https://api.llm7.io/v1",
        "chat_path": "/chat/completions",
        "models": ["gpt-4.1-mini"],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
    "kilo": {
        "style": "openai",
        "base_url": "https://api.kilo.ai/v1",
        "chat_path": "/chat/completions",
        "models": [],
        "enabled": False,
        "priority": 25,
        "timeout": 60,
    },
}


class Config:
    """Todos los parámetros del bot, juntos y arriba (como en filosofia).

    Editá lo que quieras acá y, si existen `config/config.toml` o
    `config/rules.toml`, esos pisan estos valores.
    """

    # ---------------- bot ----------------
    name: str = "FilosoBot"
    language: str = "es"
    admin_ids: List[int] = []          # ids de Telegram que pueden /addkey, /addtopic...
    allowed_chats: List[int] = []      # chats donde responde (vacio = ninguno)
    allowed_threads: List[int] = []     # topics permitidos (vacio = todos)
    allow_all_chats: bool = False       # True = responde en cualquier chat

    # ---------------- telegram ----------------
    poll_timeout: int = 30              # segundos de long polling
    parse_mode: str = "Markdown"
    max_message_length: int = 4000

    # ---------------- IA ----------------
    ai_enabled: bool = False
    default_provider: str = ""
    default_model: str = ""
    temperature: float = 0.7
    max_tokens: int = 900
    system_prompt: str = (
        "Sos un asistente en Telegram. Respondé en español, breve y claro. "
        "Usá Markdown simple. Si no sabés algo, decilo."
    )
    timeout: float = 60
    cooldown_seconds: int = 300         # cuánto queda fuera de juego un proveedor caído
    context_messages: int = 6           # mensajes de historial que se mandan como contexto

    # ---------------- preguntas y respuestas ----------------
    qa_enabled: bool = True
    qa_fallback_to_ai: bool = True
    qa_data_file: str = "data/qa.json"

    # ---------------- filtros (las listas van en config.toml) ----------------
    blocked_words: List[str] = []
    muted_users: List[int] = []
    allowed_domains: List[str] = []

    # ---------------- reglas de conducta ----------------
    rules: Dict[str, Any] = DEFAULT_RULES

    # ---------------- proveedores ----------------
    providers: Dict[str, Dict[str, Any]] = PROVIDERS

    # ------------------------------------------------------------------
    @classmethod
    def as_dict(cls) -> Dict[str, Any]:
        """El dict anidado que usa el resto del bot."""
        return {
            "bot": {
                "name": cls.name,
                "language": cls.language,
                "admin_ids": list(cls.admin_ids),
                "allowed_chats": list(cls.allowed_chats),
                "allowed_threads": list(cls.allowed_threads),
                "allow_all_chats": cls.allow_all_chats,
            },
            "telegram": {
                "poll_timeout": cls.poll_timeout,
                "parse_mode": cls.parse_mode,
                "max_message_length": cls.max_message_length,
            },
            "ai": {
                "enabled": cls.ai_enabled,
                "default_provider": cls.default_provider,
                "default_model": cls.default_model,
                "temperature": cls.temperature,
                "max_tokens": cls.max_tokens,
                "system_prompt": cls.system_prompt,
                "timeout": cls.timeout,
                "cooldown_seconds": cls.cooldown_seconds,
                "context_messages": cls.context_messages,
            },
            "qa": {
                "enabled": cls.qa_enabled,
                "fallback_to_ai": cls.qa_fallback_to_ai,
                "data_file": cls.qa_data_file,
            },
            "filters": {
                "blocked_words": list(cls.blocked_words),
                "muted_users": list(cls.muted_users),
                "allowed_domains": list(cls.allowed_domains),
            },
            "providers": {
                pid: dict(data) for pid, data in cls.providers.items()
            },
        }

    # ------------------------------------------------------------------
    @classmethod
    def resumen(cls) -> str:
        """Resumen para mostrar al arrancar."""
        return (
            f"bot={cls.name} | chats={len(cls.allowed_chats) or 'todos'} "
            f"| topics={len(cls.allowed_threads) or 'todos'} "
            f"| admins={len(cls.admin_ids)} | ia={'sí' if cls.ai_enabled else 'no'} "
            f"| proveedores={len(cls.providers)}"
        )
