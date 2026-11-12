"""Reglas de comportamiento del bot (`config/rules.toml`).

Acá vive lo que el bot **hace**, no cómo está configurado el transporte.
Diferencia con `config/config.toml`:

  * `config.toml`  -> credenciales de conexión, endpoints, filtros, juegos.
  * `rules.toml`   -> reglas de conducta, editables desde el CLI.

Ejemplos de reglas:

    [general]
    allow_offtopic = true      # permitir mensajes de otros temas
    allow_unknown = true       # responder aunque no se entienda

    [games]
    interactive = true         # dejar el juego interactivo (seguir respondiendo
                               # mientras hay partida activa)
    allow_commands_during_game = true
    timeout_seconds = 600

Cada regla tiene una descripción para que `python -m bot rules` muestre
qué hace y qué valor tiene.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .toml_io import load_toml, save_toml

DEFAULT_RULES: Dict[str, Any] = {
    "general": {
        # permitir mensajes de otros temas (no clasificados)
        "allow_offtopic": True,
        # responder aunque el mensaje no se entienda (va al fallback/IA)
        "allow_unknown": True,
        # ignorar mensajes de otros bots
        "ignore_bots": True,
        # responder siempre, o sólo cuando mencionan al bot
        "always_reply": False,
        "require_mention": False,
        "log_level": "INFO",
    },
    "reply": {
        # largo máximo de la respuesta
        "max_length": 900,
        # mostrar el motivo tecnico de un error (solo a los admins)
        "show_error_details": False,
        # citar el mensaje original al responder
        "quote": True,
        # parse mode: Markdown | HTML | ""
        "parse_mode": "Markdown",
    },
    "games": {
        # dejar el juego interactivo: mientras hay partida, los mensajes
        # del chat se interpretan como respuestas del juego
        "interactive": True,
        # permitir comandos (/trivia, /help...) durante una partida
        "allow_commands_during_game": True,
        # segundos de inactividad antes de dar la partida por terminada
        "timeout_seconds": 600,
        # permitir abandonar la partida a mitad (/salir)
        "allow_skip": True,
        # rondas por partida de trivia
        "rounds": 5,
    },
    "qa": {
        "enabled": True,
        # si no esta en el banco local, derivar a la IA
        "fallback_to_ai": True,
        # confianza minima para dar por buena una busqueda local
        "min_confidence": 0.34,
        # responder preguntas libres sin comando (/preguntame)
        "allow_questions": True,
    },
    "ai": {
        "enabled": False,
        # responder con IA aunque el mensaje no sea pregunta
        "allow_without_command": True,
        # mandar historial del chat como contexto
        "send_history": True,
        #_segundos_ que puede esperar el bot antes de avisar que no hay IA
        "warn_if_disabled": True,
        # segundos entre chequeos del vigilante de claves
        "scheduler_interval": 300,
        # el vigilante prueba las claves por HTTP (no consume tokens)
        "scheduler_probe": True,
    },
    "topics": {
        # trabajar con supergrupos que tienen temas (foros)
        "enabled": True,
        # responder siempre en el mismo tema del mensaje
        "reply_in_thread": True,
        # cada tema tiene su propia partida de juego e historial
        "per_topic_session": True,
        # en grupos con temas, exigir que lo mencionen
        "require_mention": False,
    },
    "cleanup": {
        # borrar mensajes viejos automaticamente
        "enabled": True,
        # cada N mensajes recibidos se evalua
        "every_n_messages": 25,
        # cada N segundos se evalua tambien
        "every_seconds": 300,
        # edad a partir de la cual se borra
        "delete_older_than_seconds": 3600,
        # no borrar mensajes que sean comandos
        "protect_commands": True,
        # --- limpieza con IA: la IA decide que borrar ---
        "ai_enabled": False,
        "ai_batch_size": 30,
        "ai_every_n_messages": 25,
        "ai_every_seconds": 600,
        "ai_delete_only_bot": True,
    },
    "filters": {
        "enabled": True,
        # borrar el mensaje (true) o solo avisar (false)
        "delete_on_block": True,
        # silenciar al usuario que mando el mensaje bloqueado
        "mute_on_block": False,
        # bloquear cualquier link
        "block_links": False,
        # maximo de caracteres permitido (0 = sin limite)
        "max_chars": 2000,
        "spam": {
            "enabled": True,
            "max_repeats": 6,
            "duplicate_window": 20,
            "max_duplicates": 3,
        },
    },
}

DESCRIPTIONS: Dict[str, str] = {
    "general.allow_offtopic": "Permitir mensajes de otros temas (los no clasificados)",
    "general.allow_unknown": "Responder aunque no se entienda el mensaje",
    "general.ignore_bots": "Ignorar mensajes enviados por otros bots",
    "general.always_reply": "Responder siempre, sin importar el tema",
    "general.require_mention": "Exigir que mencionen al bot para responder",
    "general.log_level": "Nivel de log (DEBUG, INFO, WARNING, ERROR)",
    "reply.max_length": "Largo máximo de la respuesta",
    "reply.show_error_details": "Mostrar el motivo técnico de un error (sólo admins)",
    "reply.quote": "Citar el mensaje original al responder",
    "reply.parse_mode": "Formato: Markdown, HTML o vacío",
    "games.interactive": "Dejar el juego interactivo (seguir respondiendo en la partida)",
    "games.allow_commands_during_game": "Permitir comandos durante una partida",
    "games.timeout_seconds": "Segundos de inactividad antes de terminar la partida",
    "games.allow_skip": "Permitir abandonar la partida a mitad (/salir)",
    "games.rounds": "Rondas por partida de trivia",
    "qa.enabled": "Activar el módulo de preguntas y respuestas",
    "qa.fallback_to_ai": "Si no está en el banco local, derivar a la IA",
    "qa.min_confidence": "Confianza mínima para aceptar una búsqueda local",
    "qa.allow_questions": "Aceptar preguntas libres (no sólo /preguntas)",
    "ai.enabled": "Activar el uso de IA",
    "ai.allow_without_command": "Usar IA aunque el mensaje no sea pregunta",
    "ai.send_history": "Enviar el historial del chat como contexto",
    "ai.warn_if_disabled": "Avisar cuando no hay IA configurada",
    "ai.scheduler_interval": "Segundos entre chequeos del vigilante de claves",
    "ai.scheduler_probe": "El vigilante prueba las claves por HTTP (no consume tokens)",
    "topics.enabled": "Trabajar con supergrupos que tienen temas (foros)",
    "topics.reply_in_thread": "Responder en el mismo tema del mensaje",
    "topics.per_topic_session": "Cada tema tiene su propia partida e historial",
    "topics.require_mention": "En grupos con temas, exigir que lo mencionen",
    "cleanup.enabled": "Activar la limpieza automática de mensajes",
    "cleanup.every_n_messages": "Evaluar limpieza cada N mensajes",
    "cleanup.every_seconds": "Evaluar limpieza cada N segundos",
    "cleanup.delete_older_than_seconds": "Antigüedad para borrar un mensaje",
    "cleanup.protect_commands": "No borrar mensajes que sean comandos",
    "cleanup.ai_enabled": "Limpieza con IA: la IA decide que borrar (cada N mensajes)",
    "cleanup.ai_batch_size": "Mensajes por lote que se pasan a la IA",
    "cleanup.ai_every_n_messages": "Disparar el janitor cada N mensajes",
    "cleanup.ai_every_seconds": "Disparar el janitor cada N segundos",
    "cleanup.ai_delete_only_bot": "Solo borrar mensajes propios del bot (mas seguro)",
    "filters.enabled": "Activar los filtros automáticos",
    "filters.delete_on_block": "Borrar el mensaje bloqueado (si no, sólo avisar)",
    "filters.mute_on_block": "Silenciar al usuario que mando el mensaje bloqueado",
    "filters.block_links": "Bloquear cualquier link",
    "filters.max_chars": "Máximo de caracteres (0 = sin límite)",
    "filters.spam.enabled": "Detectar spam (repeticiones y duplicados)",
    "filters.spam.max_repeats": "Máximo de caracteres repetidos seguidos",
    "filters.spam.duplicate_window": "Ventana en segundos para detectar duplicados",
    "filters.spam.max_duplicates": "Repetidos tolerados en la ventana",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class RuleSet:
    """Conjunto de reglas con acceso por ruta (`games.interactive`)."""

    def __init__(self, data: Dict[str, Any], path: Path | None = None):
        self.data = data
        self.path = path

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "RuleSet":
        raw = load_toml(path)
        return cls(_deep_merge(DEFAULT_RULES, raw), path)

    def save(self) -> None:
        if self.path:
            save_toml(self.path, self.data)

    # ------------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def reset(self) -> None:
        self.data = _deep_merge(DEFAULT_RULES, {})

    # ------------------------------------------------------------------
    def flat(self) -> List[Tuple[str, Any, str]]:
        """(ruta, valor, descripción) de todas las reglas."""
        out: List[Tuple[str, Any, str]] = []

        def walk(node: Dict[str, Any], path: List[str]) -> None:
            for key, value in node.items():
                if isinstance(value, dict):
                    walk(value, path + [key])
                else:
                    ruta = ".".join(path + [key])
                    out.append((ruta, value, DESCRIPTIONS.get(ruta, "")))

        walk(self.data, [])
        return out

    def sections(self) -> List[str]:
        return [k for k, v in self.data.items() if isinstance(v, dict)]

    def known_paths(self) -> List[str]:
        return [r for r, _, _ in self.flat()]
