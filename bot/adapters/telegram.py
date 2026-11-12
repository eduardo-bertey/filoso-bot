"""Adaptador de Telegram.

ÚNICO lugar del proyecto que habla con la API de Telegram.
No importa nada de IA, juegos ni filtros: sólo traduce
`Message` <-> JSON de Telegram y expone `send`/`delete`.

Usa `urllib` de la stdlib: cero dependencias externas.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..models import Message, OutgoingMessage

log = logging.getLogger("filoso.telegram")

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int = 0):
        super().__init__(f"{method}: {description} ({error_code})")
        self.method = method
        self.description = description
        self.error_code = error_code


class TelegramClient:
    """Cliente HTTP de la Bot API (long polling)."""

    def __init__(
        self,
        token: str,
        parse_mode: str = "Markdown",
        poll_timeout: int = 30,
        timeout: float = 60.0,
        base_url: str = API_BASE,
    ):
        if not token:
            raise ValueError("Falta el token del bot")
        self.token = token
        self.parse_mode = parse_mode
        self.poll_timeout = poll_timeout
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.offset: Optional[int] = None
        self._bot_username: Optional[str] = None

    # ------------------------------------------------------------------
    # bajo nivel
    # ------------------------------------------------------------------
    def _call(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                desc = parsed.get("description", raw)
                code = parsed.get("error_code", exc.code)
            except json.JSONDecodeError:
                desc, code = raw, exc.code
            raise TelegramError(method, desc, code) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(method, f"red: {exc.reason}", 0) from exc

        if not body.get("ok"):
            raise TelegramError(method, body.get("description", "?"), body.get("error_code", 0))
        return body.get("result")

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def get_me(self) -> Dict[str, Any]:
        info = self._call("getMe") or {}
        self._bot_username = info.get("username")
        return info

    @property
    def bot_username(self) -> Optional[str]:
        return self._bot_username

    def get_updates(self, limit: int = 50) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "timeout": self.poll_timeout,
            "limit": limit,
            "allowed_updates": json.dumps(
                ["message", "edited_message", "channel_post", "edited_channel_post"]
            ),
        }
        if self.offset is not None:
            params["offset"] = self.offset
        updates = self._call("getUpdates", params) or []
        if updates:
            last = updates[-1].get("update_id", 0)
            self.offset = last + 1
        return updates

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to: Optional[int] = None,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        thread_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        if thread_id is not None:
            # supergrupo con temas: la respuesta va al mismo topic
            payload["message_thread_id"] = thread_id
        mode = parse_mode or self.parse_mode
        if mode:
            payload["parse_mode"] = mode
        return self._call("sendMessage", payload) or {}

    def send(self, chat_id: int, out: OutgoingMessage) -> Optional[Dict[str, Any]]:
        """Envía un `OutgoingMessage` (el módulo de negocio nunca ve Telegram)."""
        try:
            return self.send_message(
                chat_id=chat_id,
                text=out.text,
                reply_to=out.reply_to,
                parse_mode=out.parse_mode,
                disable_web_page_preview=out.disable_web_page_preview,
                thread_id=out.thread_id,
            )
        except TelegramError as exc:
            log.warning("no se pudo enviar a %s: %s", chat_id, exc)
            return None

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        try:
            self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            return True
        except TelegramError as exc:
            if exc.error_code == 400 and "message to delete" in exc.description.lower():
                return False  # ya borrado / muy viejo
            log.debug("deleteMessage falló: %s", exc)
            return False

    def pin_message(self, chat_id: int, message_id: int) -> bool:
        try:
            self._call(
                "pinChatMessage",
                {"chat_id": chat_id, "message_id": message_id, "disable_notification": True},
            )
            return True
        except TelegramError:
            return False

    # ------------------------------------------------------------------
    # conversión
    # ------------------------------------------------------------------
    def parse_update(self, update: Dict[str, Any]) -> Optional[Message]:
        """Traduce un update de Telegram a `Message` de dominio."""
        data = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not data:
            return None
        chat = data.get("chat") or {}
        user = data.get("from") or {}
        text = data.get("text") or data.get("caption") or ""
        if not text:
            return None
        reply = data.get("reply_to_message") or {}
        # supergrupo con temas: message_thread_id identifica el topic
        thread_id = data.get("message_thread_id")
        try:
            thread_id = int(thread_id) if thread_id is not None else None
        except (TypeError, ValueError):
            thread_id = None
        return Message(
            message_id=int(data.get("message_id", 0)),
            chat_id=int(chat.get("id", 0)),
            text=str(text),
            user_id=int(user.get("id", 0)),
            username=str(user.get("username", "") or ""),
            full_name=str(user.get("first_name", "") or "")
            + (" " + str(user.get("last_name", "") or "") if user.get("last_name") else ""),
            date=float(data.get("date", time.time())),
            reply_to_message_id=reply.get("message_id"),
            chat_type=str(chat.get("type", "private")),
            thread_id=thread_id,
            raw=update,
        )
