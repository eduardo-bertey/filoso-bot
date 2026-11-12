"""Estado persistente del bot: sesiones de juego y contadores.

Se guarda en `data/state.json` con el `JsonStore`.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import GameSession
from .storage import JsonStore


class StateStore:
    """Sesiones de juego por chat (y por tema) + contadores globales."""

    def __init__(self, path: Path):
        self._store = JsonStore(path, default={"sessions": {}, "counters": {}})

    # ---------------- juegos ----------------
    @staticmethod
    def key_of(chat_id: int, thread_id: Optional[int] = None) -> str:
        """Clave de sesión: por chat, y por tema si es supergrupo con temas."""
        return f"{chat_id}:{thread_id}" if thread_id is not None else str(chat_id)

    def get_session(self, chat_id: int, thread_id: Optional[int] = None) -> GameSession:
        key = self.key_of(chat_id, thread_id)
        raw = self._store.get("sessions", {}).get(key)
        if not raw:
            return GameSession(chat_id=chat_id)
        return GameSession(
            chat_id=chat_id,
            game=raw.get("game", ""),
            active=bool(raw.get("active", False)),
            data=raw.get("data", {}) or {},
            score=int(raw.get("score", 0)),
            created_at=float(raw.get("created_at", time.time())),
            updated_at=float(raw.get("updated_at", time.time())),
        )

    def save_session(self, session: GameSession, thread_id: Optional[int] = None) -> None:
        session.updated_at = time.time()
        key = self.key_of(session.chat_id, thread_id)
        self._store.update(
            "sessions",
            {
                key: {
                    "game": session.game,
                    "active": session.active,
                    "data": session.data,
                    "score": session.score,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                }
            },
        )

    def end_session(self, chat_id: int, thread_id: Optional[int] = None) -> None:
        sessions: Dict[str, Any] = self._store.get("sessions", {})
        sessions.pop(self.key_of(chat_id, thread_id), None)
        self._store.set("sessions", sessions)

    def active_session(self, chat_id: int, thread_id: Optional[int] = None) -> Optional[GameSession]:
        session = self.get_session(chat_id, thread_id)
        return session if session.active else None

    # ---------------- contadores ----------------
    def bump(self, name: str, delta: int = 1) -> int:
        counters = self._store.get("counters", {})
        value = int(counters.get(name, 0)) + delta
        counters[name] = value
        self._store.set("counters", counters)
        return value

    def counter(self, name: str) -> int:
        return int(self._store.get("counters", {}).get(name, 0))

    def reset_counter(self, name: str) -> None:
        counters = self._store.get("counters", {})
        counters[name] = 0
        self._store.set("counters", counters)

    # ---------------- historial ----------------
    def push_history(
        self,
        chat_id: int,
        role: str,
        text: str,
        limit: int = 20,
        thread_id: Optional[int] = None,
    ) -> None:
        history = self._store.get("history", {})
        key = self.key_of(chat_id, thread_id)
        chat = history.setdefault(key, [])
        chat.append({"role": role, "text": text, "ts": time.time()})
        if len(chat) > limit:
            del chat[: len(chat) - limit]
        history[key] = chat
        self._store.set("history", history)

    def history(
        self, chat_id: int, limit: int = 6, thread_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        key = self.key_of(chat_id, thread_id)
        chat = self._store.get("history", {}).get(key, [])
        return chat[-limit:]

    def clear_history(self, chat_id: int, thread_id: Optional[int] = None) -> None:
        history = self._store.get("history", {})
        history.pop(self.key_of(chat_id, thread_id), None)
        self._store.set("history", history)
