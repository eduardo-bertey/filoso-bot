"""Clasificación de mensajes: determina el TIPO de cada mensaje.

Este módulo es el que "recupera los mensajes y determina el tipo":
toma el texto crudo y devuelve un `Intent` + el comando parseado.

Reglas (100% sin IA, determinísticas):

  /xxx                -> COMMAND      (comando explícito)
  "pregunta" / "info"  -> INFO         (estado/config del bot)
  "jugar/trivia/..."   -> GAME
  "preguntame/quiz"   -> QA
  empieza con /request, o texto largo con pregunta -> REQUEST (a la IA)
  termina en "?"      -> QUESTION
  saludo corto        -> SMALLTALK
  resto               -> UNKNOWN
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..models import Intent, Message

# prefijos que fuerzan el pedido a la IA (sin la barra: se comparan con el
# nombre del comando ya parseado)
AI_PREFIXES = (
    "request",
    "ask",
    "ia",
    "ai",
    "preguntar",
    "prompt",
)

INFO_WORDS = (
    "info",
    "informacion",
    "información",
    "estado",
    "status",
    "config",
    "ayuda",
    "help",
    "comandos",
    "proveedores",
    "providers",
    "modelos",
    "quien eres",
    "quién eres",
    "que sos",
    "qué sos",
)

GAME_WORDS = (
    "jugar",
    "juego",
    "game",
    "trivia",
    "quiz",
    "preguntas",
    "piedra papel tijera",
    "piedra, papel y tijera",
    "tijeras",
    "dado",
    "dados",
    "numero",
    "número",
)

QA_WORDS = (
    "preguntame",
    "preguntáme",
    "preguntar",
    "trivia",
    "quiz",
    "sabias",
    "sabías",
    "dato curioso",
    "curiosidad",
)

SMALLTALK = (
    "hola",
    "buenas",
    "hey",
    "holis",
    "buen dia",
    "buen día",
    "buenas tardes",
    "buenas noches",
    "gracias",
    "chau",
    "adios",
    "adiós",
    "nos vemos",
)

QUESTION_WORDS = (
    "que",
    "qué",
    "como",
    "cómo",
    "cuando",
    "cuándo",
    "donde",
    "dónde",
    "quien",
    "quién",
    "cual",
    "cuál",
    "cuanto",
    "cuánto",
    "por que",
    "por qué",
    "para que",
    "para qué",
    "cuantos",
    "cuántos",
    "cuales",
    "cuáles",
    "significa",
    "definicion",
    "definición",
)

KNOWN_COMMANDS = (
    "start",
    "help",
    "info",
    "status",
    "ask",
    "request",
    "ia",
    "ai",
    "game",
    "jugar",
    "trivia",
    "quiz",
    "rps",
    "piedrapapeltijera",
    "dado",
    "adivina",
    "preguntas",
    "modelos",
    "proveedores",
    "provider",
    "usar",
    "limpiar",
    "clear",
    "filtros",
    "stats",
    "on",
    "off",
    "stop",
)


@dataclass
class Classification:
    """Resultado del clasificador."""

    intent: Intent = Intent.UNKNOWN
    command: str = ""
    args: List[str] = None  # type: ignore[assignment]
    text: str = ""
    confidence: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.args is None:
            self.args = []

    @property
    def is_command(self) -> bool:
        return self.intent == Intent.COMMAND

    @property
    def wants_ai(self) -> bool:
        return self.intent in (Intent.REQUEST, Intent.QUESTION)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def classify(msg: Message) -> Classification:
    """Devuelve la clasificación de un mensaje entrante."""
    raw = (msg.text or "").strip()
    low = normalize(raw)
    if not low:
        return Classification(intent=Intent.UNKNOWN, text=raw, reason="vacío")

    # 1) comando explícito
    if raw.startswith("/"):
        parts = raw[1:].split()
        name = parts[0].split("@", 1)[0].lower() if parts else ""
        args = parts[1:]
        if name in AI_PREFIXES:
            return Classification(
                intent=Intent.REQUEST,
                command=name,
                args=args,
                text=" ".join(args),
                confidence=1.0,
                reason="prefijo de IA",
            )
        if name in ("game", "jugar"):
            return Classification(
                intent=Intent.GAME, command=name, args=args, text=" ".join(args),
                confidence=1.0, reason="comando de juego",
            )
        if name in ("trivia", "quiz", "preguntas"):
            return Classification(
                intent=Intent.QA, command=name, args=args, text=" ".join(args),
                confidence=1.0, reason="comando de preguntas",
            )
        if name in ("info", "status", "modelos", "proveedores", "provider", "filtros", "stats"):
            return Classification(
                intent=Intent.INFO, command=name, args=args, text=" ".join(args),
                confidence=1.0, reason="comando de info",
            )
        return Classification(
            intent=Intent.COMMAND, command=name, args=args, text=" ".join(args),
            confidence=1.0, reason="comando",
        )

    # 2) prefijos de IA escritos sin barra ("request: ...")
    for prefix in ("request:", "request ", "preguntale a la ia:", "ia:"):
        if low.startswith(prefix):
            return Classification(
                intent=Intent.REQUEST,
                text=raw[len(prefix):].strip(),
                confidence=0.95,
                reason="prefijo de IA",
            )

    # 3) palabras clave de info
    for word in INFO_WORDS:
        if low == word or low.startswith(word + " ") or low.startswith(word + ":"):
            return Classification(
                intent=Intent.INFO, text=raw, confidence=0.9, reason=f"palabra info: {word}"
            )

    # 4) juegos
    for word in GAME_WORDS:
        if word in low:
            return Classification(
                intent=Intent.GAME, text=raw, confidence=0.85, reason=f"juego: {word}"
            )

    # 5) preguntas y respuestas / trivia
    for word in QA_WORDS:
        if word in low:
            return Classification(
                intent=Intent.QA, text=raw, confidence=0.8, reason=f"qa: {word}"
            )

    # 6) pregunta general -> IA
    if raw.endswith("?") or "?" in raw:
        return Classification(
            intent=Intent.QUESTION, text=raw, confidence=0.8, reason="contiene ?"
        )
    for word in QUESTION_WORDS:
        if low.startswith(word + " ") or low.startswith(word + "?"):
            return Classification(
                intent=Intent.QUESTION, text=raw, confidence=0.7, reason=f"pregunta: {word}"
            )

    # 7) smalltalk
    for word in SMALLTALK:
        if low == word or low.startswith(word + " ") or low.endswith(" " + word):
            return Classification(
                intent=Intent.SMALLTALK, text=raw, confidence=0.6, reason=f"saludo: {word}"
            )

    # 8) texto largo -> lo tratamos como pedido a la IA
    if len(raw) >= 25:
        return Classification(
            intent=Intent.REQUEST, text=raw, confidence=0.4, reason="texto largo"
        )

    return Classification(intent=Intent.UNKNOWN, text=raw, reason="sin clasificar")
