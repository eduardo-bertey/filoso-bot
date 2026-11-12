"""Router: recibe `Message`, clasifica y despacha al módulo correcto.

Es el único lugar que sabe qué módulo maneja qué intención.
Los módulos nunca se hablan entre sí: el router decide.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..models import Intent, Message, OutgoingMessage
from ..modules.classifier import Classification, classify

log = logging.getLogger("filoso.router")

Handler = Callable[[Message, Classification], Optional[OutgoingMessage]]


@dataclass
class ModuleResult:
    """Resultado de despachar un mensaje."""

    handled: bool = False
    output: Optional[OutgoingMessage] = None
    intent: Intent = Intent.UNKNOWN
    classification: Optional[Classification] = None
    module: str = ""
    error: str = ""

    @property
    def text(self) -> str:
        return self.output.text if self.output else ""


class Router:
    """Tabla de ruteo por intención + módulos."""

    def __init__(self) -> None:
        self.handlers: Dict[Intent, List[Handler]] = {}
        self.fallback: Optional[Handler] = None
        self.classifications: Dict[int, Classification] = {}

    # ------------------------------------------------------------------
    def register(self, intent: Intent, handler: Handler) -> None:
        self.handlers.setdefault(intent, []).append(handler)

    def set_fallback(self, handler: Handler) -> None:
        self.fallback = handler

    def intents(self) -> List[str]:
        return [i.value for i in self.handlers if self.handlers[i]]

    # ------------------------------------------------------------------
    def dispatch(self, msg: Message) -> ModuleResult:
        classification = classify(msg)
        self.classifications[msg.chat_id] = classification
        log.info(
            "chat=%s user=%s intent=%s cmd=%s",
            msg.chat_id, msg.user_id, classification.intent.value, classification.command,
        )

        for handler in self.handlers.get(classification.intent, []):
            try:
                out = handler(msg, classification)
            except Exception as exc:  # noqa: BLE001 - un modulo no debe tumbar el bot
                log.exception("modulo fallo: %s", exc)
                return ModuleResult(
                    handled=False,
                    intent=classification.intent,
                    classification=classification,
                    error=str(exc),
                )
            if out is not None:
                return ModuleResult(
                    handled=True,
                    output=out,
                    intent=classification.intent,
                    classification=classification,
                )

        if self.fallback:
            try:
                out = self.fallback(msg, classification)
                if out is not None:
                    return ModuleResult(
                        handled=True,
                        output=out,
                        intent=classification.intent,
                        classification=classification,
                        module="fallback",
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception("fallback fallo: %s", exc)
                return ModuleResult(
                    handled=False,
                    intent=classification.intent,
                    classification=classification,
                    error=str(exc),
                )

        return ModuleResult(
            handled=False, intent=classification.intent, classification=classification
        )
