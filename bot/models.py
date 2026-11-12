"""Modelos de dominio compartidos por todos los módulos.

Acá viven las estructuras que cruzan capas: un `Message` entra al bot,
sale un `OutgoingMessage`. Nada de esto depende de Telegram ni de ningún
proveedor de IA: son tipos planos (dataclasses) sin dependencias externas.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Intent(str, Enum):
    """Clasificación de un mensaje entrante (ver `modules.messages`)."""

    COMMAND = "command"      # /algo
    REQUEST = "request"      # pedido explícito a la IA
    QUESTION = "question"    # pregunta general ("¿qué es...?")
    INFO = "info"            # consulta de estado/config del bot
    GAME = "game"            # quiere jugar
    QA = "qa"                # trivia / preguntas y respuestas
    SMALLTALK = "smalltalk"  # saludo, charla corta
    UNKNOWN = "unknown"


@dataclass
class Message:
    """Mensaje entrante normalizado (independiente del transporte)."""

    message_id: int
    chat_id: int
    text: str
    user_id: int
    username: str = ""
    full_name: str = ""
    date: float = field(default_factory=time.time)
    reply_to_message_id: Optional[int] = None
    chat_type: str = "private"
    # id del tema (forum topic) del supergrupo; None en chats normales
    thread_id: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_command(self) -> bool:
        return self.text.startswith("/")

    @property
    def is_topic(self) -> bool:
        """True si el mensaje viene de un tema (topic) de un supergrupo."""
        return self.thread_id is not None

    @property
    def session_key(self) -> str:
        """Clave de sesión: por chat, y por tema si es supergrupo con temas."""
        if self.thread_id is not None:
            return f"{self.chat_id}:{self.thread_id}"
        return str(self.chat_id)

    @property
    def command(self) -> str:
        """Nombre del comando sin la barra y sin el @bot."""
        if not self.is_command:
            return ""
        head = self.text[1:].split()[0] if len(self.text) > 1 else ""
        return head.split("@", 1)[0].lower()

    @property
    def args(self) -> List[str]:
        if not self.is_command:
            return []
        return self.text[1:].split()[1:]


@dataclass
class OutgoingMessage:
    """Respuesta que el bot quiere enviar. La capa Telegram la traduce."""

    text: str
    reply_to: Optional[int] = None
    parse_mode: Optional[str] = None
    disable_web_page_preview: bool = True
    delete_after: Optional[int] = None  # segundos; lo usa el filtro de limpieza
    # tema (forum topic) donde tiene que ir la respuesta
    thread_id: Optional[int] = None


@dataclass
class ProviderConfig:
    """Configuración de un proveedor de IA (endpoint + modelos + estado)."""

    id: str
    style: str = "openai"                 # openai | anthropic | gemini | ollama
    base_url: str = ""
    chat_path: str = "/chat/completions"
    models: List[str] = field(default_factory=list)
    enabled: bool = False
    priority: int = 50                    # menor = se prueba antes
    timeout: float = 60.0
    extra_headers: Dict[str, str] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)
    healthy: bool = True
    cooldown_until: float = 0.0
    last_error: str = ""
    blocked_models: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "style": self.style,
            "base_url": self.base_url,
            "chat_path": self.chat_path,
            "models": list(self.models),
            "enabled": self.enabled,
            "priority": self.priority,
            "timeout": self.timeout,
            "extra_headers": dict(self.extra_headers),
            "extra_body": dict(self.extra_body),
            "healthy": self.healthy,
            "cooldown_until": self.cooldown_until,
            "last_error": self.last_error,
            "blocked_models": list(self.blocked_models),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class FilterVerdict:
    """Resultado de pasar un mensaje por el pipeline de filtros."""

    allowed: bool = True
    reason: str = ""
    action: str = "none"          # none | delete | mute | warn
    modified_text: Optional[str] = None


@dataclass
class Completion:
    """Respuesta normalizada de un proveedor de IA."""

    text: str = ""
    provider: str = ""
    model: str = ""
    ok: bool = True
    error: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    attempts: List[str] = field(default_factory=list)


@dataclass
class GameSession:
    """Estado de una partida por chat."""

    chat_id: int
    game: str = ""
    active: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    score: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
