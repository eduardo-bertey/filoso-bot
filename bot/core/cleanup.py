"""Servicio de limpieza automática de mensajes.

Reglas configurables (`config.json` -> `filters.cleanup`):

  * `every_n_messages`: cada N mensajes recibidos se evalua si hay que
    borrar algo.
  * `every_seconds`: cada N segundos se evalua tambien (por si el chat
    esta quieto).
  * `delete_older_than_seconds`: edad a partir de la cual se borran los
    mensajes del bot (o todos, si `protect_commands` esta en false).

Telegram exige >= 48 horas para borrar mensajes de otros; los nuestros
se pueden borrar al instante. El servicio distingue los dos casos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import Message, OutgoingMessage

log = logging.getLogger("filoso.cleanup")

# Telegram no deja borrar mensajes ajenos antes de 48h
MIN_AGE_SECONDS = 48 * 3600


@dataclass
class TrackedMessage:
    chat_id: int
    message_id: int
    created: float
    is_bot: bool = True
    text: str = ""


@dataclass
class CleanupService:
    """Guarda los mensajes enviados y borra los viejos cuando toca."""

    enabled: bool = True
    every_n_messages: int = 25
    every_seconds: int = 300
    delete_older_than: int = 3600
    protect_commands: bool = True
    max_tracked_per_chat: int = 200

    _tracked: Dict[int, List[TrackedMessage]] = field(default_factory=dict)
    _counter: int = 0
    _last_run: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    def track(self, chat_id: int, message_id: int, is_bot: bool = True, text: str = "") -> None:
        lista = self._tracked.setdefault(chat_id, [])
        lista.append(
            TrackedMessage(chat_id=chat_id, message_id=message_id, created=time.time(),
                           is_bot=is_bot, text=(text or "")[:200])
        )
        if len(lista) > self.max_tracked_per_chat:
            del lista[: len(lista) - self.max_tracked_per_chat]

    def track_outgoing(self, chat_id: int, msg: Message | None, out: OutgoingMessage | None) -> None:
        """Registra un mensaje propio para poder borrarlo despues."""
        if out is None:
            return
        self.track(chat_id, 0, is_bot=True, text=out.text)

    # ------------------------------------------------------------------
    def register_incoming(self, msg: Message) -> None:
        self._counter += 1
        self.track(msg.chat_id, msg.message_id, is_bot=False, text=msg.text)

    def due_by_messages(self) -> bool:
        if not self.enabled or self.every_n_messages <= 0:
            return False
        return self._counter % self.every_n_messages == 0

    def due_by_time(self) -> bool:
        if not self.enabled or self.every_seconds <= 0:
            return False
        return (time.time() - self._last_run) >= self.every_seconds

    def maybe_run(self) -> bool:
        """Devuelve True si toca correr la limpieza ahora."""
        if not self.enabled:
            return False
        if self.due_by_messages() or self.due_by_time():
            self._last_run = time.time()
            return True
        return False

    # ------------------------------------------------------------------
    def expired(self, chat_id: int, now: Optional[float] = None) -> List[TrackedMessage]:
        """Mensajes que ya superstaron el limite de edad."""
        now = now or time.time()
        limite = now - self.delete_older_than
        out = []
        for item in self._tracked.get(chat_id, []):
            if item.created <= limite:
                out.append(item)
        return out

    def purge_expired(
        self,
        chat_id: int,
        delete_fn: Callable[[int, int], bool],
        now: Optional[float] = None,
    ) -> int:
        """Borra los mensajes vencidos. Devuelve cuantos borro."""
        if not self.enabled or self.delete_older_than <= 0:
            return 0
        now = now or time.time()
        limite = now - self.delete_older_than
        restantes: List[TrackedMessage] = []
        borrados = 0
        for item in self._tracked.get(chat_id, []):
            vencido = item.created <= limite
            if not vencido:
                restantes.append(item)
                continue
            # mensajes de otros: Telegram exige 48h
            if not item.is_bot and (now - item.created) < MIN_AGE_SECONDS:
                restantes.append(item)
                continue
            if item.message_id and delete_fn(chat_id, item.message_id):
                borrados += 1
            elif not item.message_id:
                borrados += 1
        self._tracked[chat_id] = restantes
        if borrados:
            log.info("limpieza: %s mensaje(s) borrados en chat %s", borrados, chat_id)
        return borrados

    def stats(self, chat_id: int) -> Dict[str, Any]:
        lista = self._tracked.get(chat_id, [])
        return {
            "tracked": len(lista),
            "counter": self._counter,
            "every_n_messages": self.every_n_messages,
            "every_seconds": self.every_seconds,
            "delete_older_than": self.delete_older_than,
            "last_run": self._last_run,
        }
