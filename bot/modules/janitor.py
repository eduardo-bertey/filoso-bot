"""Janitor con IA: cada X mensajes, la IA decide cuáles borrar.

En vez de borrar por antigüedad (la limpieza clásica), este módulo
junta un lote de mensajes (con sus ids) y le pide a la IA que
identifique los que deberían borrarse (spam, off-topic, insultos,
repetidos, links no deseados).

Reglas de conducta (`rules.toml` → `cleanup`):

    [cleanup]
    ai_enabled = false              # activar el janitor con IA
    ai_batch_size = 30              # mensajes por lote
    ai_every_n_messages = 25        # disparar cada N mensajes recibidos
    ai_every_seconds = 600          # o cada N segundos
    ai_delete_only_bot = true       # sólo borrar mensajes propios del bot

Si la IA no está disponible, cae a la limpieza clásica por antigüedad.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import Message, OutgoingMessage

log = logging.getLogger("filoso.janitor")

MIN_AGE_SECONDS = 48 * 3600


@dataclass
class TrackedMessage:
    chat_id: int
    message_id: int
    created: float
    is_bot: bool = True
    text: str = ""
    user_id: int = 0


@dataclass
class AIJanitor:
    """Limpieza de mensajes asistida por IA."""

    enabled: bool = False
    batch_size: int = 30
    every_n_messages: int = 25
    every_seconds: int = 600
    delete_only_bot: bool = True
    delete_older_than: int = 0   # 0 = sin límite de edad
    max_tracked_per_chat: int = 200

    _tracked: Dict[int, List[TrackedMessage]] = field(default_factory=dict)
    _counter: int = 0
    _last_run: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    def track(self, chat_id: int, message_id: int, is_bot: bool = True,
              text: str = "", user_id: int = 0) -> None:
        lista = self._tracked.setdefault(chat_id, [])
        lista.append(TrackedMessage(
            chat_id=chat_id, message_id=message_id, created=time.time(),
            is_bot=is_bot, text=(text or "")[:300], user_id=user_id,
        ))
        if len(lista) > self.max_tracked_per_chat:
            del lista[: len(lista) - self.max_tracked_per_chat]

    def register_incoming(self, msg: Message) -> None:
        self._counter += 1
        self.track(msg.chat_id, msg.message_id, is_bot=False,
                   text=msg.text, user_id=msg.user_id)

    def due_by_messages(self) -> bool:
        if not self.enabled or self.every_n_messages <= 0:
            return False
        return self._counter % self.every_n_messages == 0

    def due_by_time(self) -> bool:
        if not self.enabled or self.every_seconds <= 0:
            return False
        return (time.time() - self._last_run) >= self.every_seconds

    def maybe_run(self) -> bool:
        if not self.enabled:
            return False
        if self.due_by_messages() or self.due_by_time():
            self._last_run = time.time()
            return True
        return False

    # ------------------------------------------------------------------
    def build_prompt(self, chat_id: int) -> Tuple[str, List[int]]:
        """Arma el prompt para la IA con los mensajes del chat. Devuelve
        (prompt, lista de ids enviados)."""
        items = self._tracked.get(chat_id, [])
        lote = items[-self.batch_size:]
        ids = [m.message_id for m in lote if m.message_id]
        lineas = ["Eres un moderador de chat. Recibís una lista de mensajes con sus IDs.",
                  "Identificá los que deberían borrarse (spam, off-topic, insultos, repetidos, links no deseados).",
                  'Respondé SÓLO con un JSON: {"delete": [id1, id2, ...]}',
                  'Si ninguno debe borrarse: {"delete": []}.',
                  "No expliques. No agregues texto. Solo el JSON.",
                  "",
                  "Mensajes:"]
        for m in lote:
            prefijo = "[bot]" if m.is_bot else f"[user {m.user_id}]"
            lineas.append(f"[{m.message_id}] {prefijo} {m.text}")
        return "\n".join(lineas), ids

    def _parse_ids(self, text: str, permitidos: List[int]) -> List[int]:
        """Extrae la lista de ids a borrar de la respuesta de la IA."""
        permitidos_set = set(permitidos)
        try:
            # intentar JSON directo
            data = json.loads(text.strip())
            ids = data.get("delete", []) if isinstance(data, dict) else []
            return [int(i) for i in ids if int(i) in permitidos_set]
        except Exception:  # noqa: BLE001
            pass
        # fallback: regex de numeros
        try:
            match = re.search(r"\[([^\]]*)\]", text)
            if match:
                ids = [int(x.strip()) for x in match.group(1).split(",") if x.strip().isdigit()]
                return [i for i in ids if i in permitidos_set]
        except Exception:  # noqa: BLE001
            pass
        return []

    def run(self, chat_id: int, ai_service: Any, delete_fn: Callable[[int, int], bool]) -> int:
        """Corre el janitor con IA sobre un chat. Devuelve cuántos borró."""
        if not self.enabled:
            return 0
        prompt, permitidos = self.build_prompt(chat_id)
        if not permitidos:
            return 0
        comp = ai_service.ask(prompt, chat_id=chat_id, history=[])
        if not comp.ok or not comp.text.strip():
            log.warning("janitor: IA no respondió (%s), se saltea", comp.error)
            return 0

        a_borrar = self._parse_ids(comp.text, permitidos)
        if not a_borrar:
            log.info("janitor: nada para borrar en chat %s", chat_id)
            return 0

        borrados = 0
        for mid in a_borrar:
            # no borrar mensajes de otros usuarios si no es admin
            item = next((t for t in self._tracked.get(chat_id, []) if t.message_id == mid), None)
            if item and not item.is_bot and not self._can_delete_others():
                log.info("janitor: omitiendo mensaje de otro usuario %s", mid)
                continue
            if delete_fn(chat_id, mid):
                borrados += 1
        # sacar de la lista los borrados
        self._tracked[chat_id] = [
            t for t in self._tracked.get(chat_id, []) if t.message_id not in a_borrar
        ]
        log.info("janitor: %s mensaje(s) borrados por IA en chat %s", borrados, chat_id)
        return borrados

    def _can_delete_others(self) -> bool:
        return False

    def tracked_count(self, chat_id: int) -> int:
        return len(self._tracked.get(chat_id, []))

    def stats(self, chat_id: int) -> Dict[str, Any]:
        return {
            "tracked": len(self._tracked.get(chat_id, [])),
            "counter": self._counter,
            "every_n_messages": self.every_n_messages,
            "every_seconds": self.every_seconds,
            "batch_size": self.batch_size,
            "enabled": self.enabled,
            "last_run": self._last_run,
        }
