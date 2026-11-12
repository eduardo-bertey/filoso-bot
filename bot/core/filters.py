"""Pipeline de filtros automáticos (sin IA).

Se aplica ANTES de cualquier módulo de negocio. Un mensaje puede:

  * pasar intacto,
  * ser bloqueado (palabra clave / mala palabra / dominio / link),
  * ser silenciado (usuario muteado),
  * ser recortado (excede el máximo de caracteres),
  * ser marcado como spam (repeticiones o duplicados).

Todo configurable desde `config.json` -> `filters`.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from ..models import FilterVerdict, Message

DEFAULT_BAD_WORDS = [
    "puta",
    "mierda",
    "hijo de puta",
    "cabron",
    "gilipollas",
    "subnormal",
    "idiota",
    "pendejo",
    "concha",
    "carajo",
    "malparido",
]


class FilterPipeline:
    """Encadena de filtros. `check()` devuelve un veredicto."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.blocked_words = [w.lower() for w in (self.config.get("blocked_words") or [])]
        self.delete_on_block = bool(self.config.get("delete_on_block", True))
        self.mute_on_block = bool(self.config.get("mute_on_block", False))
        self.muted_users = set(self.config.get("muted_users") or [])
        self.allowed_domains = [
            d.lower().strip() for d in (self.config.get("allowed_domains") or [])
        ]
        self.block_links = bool(self.config.get("block_links", False))
        self.max_chars = int(self.config.get("max_chars", 2000))

        spam = self.config.get("spam", {}) or {}
        self.spam_enabled = bool(spam.get("enabled", True))
        self.max_repeats = int(spam.get("max_repeats", 6))
        self.duplicate_window = int(spam.get("duplicate_window", 20))
        self.max_duplicates = int(spam.get("max_duplicates", 3))

        # historial en memoria para spam
        self._recent: Dict[int, Deque[Tuple[float, str]]] = defaultdict(deque)

    # ------------------------------------------------------------------
    def check(self, msg: Message) -> FilterVerdict:
        if not self.enabled:
            return FilterVerdict()

        text = msg.text or ""
        lower = text.lower()

        # 1) usuario silenciado
        if msg.user_id in self.muted_users:
            return FilterVerdict(
                allowed=False, reason="usuario silenciado", action="mute"
            )

        # 2) palabras bloqueadas (mala palabra / palabra clave)
        hit = self._match_blocked(lower)
        if hit:
            action = "delete" if self.delete_on_block else "warn"
            if self.mute_on_block:
                action = "mute"
            return FilterVerdict(
                allowed=False, reason=f"palabra bloqueada: {hit}", action=action
            )

        # 3) links
        if self.allowed_domains:
            domains = self._extract_domains(lower)
            if domains:
                fuera = [d for d in domains if not self._domain_allowed(d)]
                if fuera:
                    return FilterVerdict(
                        allowed=False,
                        reason=f"dominio no permitido: {', '.join(fuera)}",
                        action="delete" if self.delete_on_block else "warn",
                    )
        if self.block_links and re.search(r"https?://", lower):
            return FilterVerdict(
                allowed=False, reason="link bloqueado", action="warn"
            )

        # 4) largo (antes del spam: un mensaje enorme se recorta y se
        #    evalúa el spam sobre el texto ya recortado)
        texto = text
        if self.max_chars and len(texto) > self.max_chars:
            texto = texto[: self.max_chars] + "…"

        # 5) spam: caracteres repetidos
        if self.spam_enabled:
            m = re.search(r"(.)\1{" + str(self.max_repeats) + r",}", texto)
            if m:
                return FilterVerdict(
                    allowed=False, reason="caracteres repetidos", action="warn"
                )

            # 6) spam: mensajes duplicados en la ventana
            self._recent[msg.chat_id].append((time.time(), lower))
            window = self._recent[msg.chat_id]
            cutoff = time.time() - self.duplicate_window
            while window and window[0][0] < cutoff:
                window.popleft()
            if sum(1 for _, t in window if t == lower) > self.max_duplicates:
                return FilterVerdict(
                    allowed=False, reason="mensaje duplicado", action="warn"
                )

        if texto != text:
            return FilterVerdict(
                allowed=True,
                reason=f"mensaje recortado a {self.max_chars} caracteres",
                action="none",
                modified_text=texto,
            )

        return FilterVerdict()

    # ------------------------------------------------------------------
    def _match_blocked(self, lower_text: str) -> Optional[str]:
        for word in self.blocked_words:
            if not word:
                continue
            if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lower_text):
                return word
        return None

    def _extract_domains(self, lower_text: str) -> List[str]:
        found = re.findall(
            r"(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z]{2,})(?:/\S*)?", lower_text
        )
        return sorted(set(found))

    def _domain_allowed(self, domain: str) -> bool:
        for allowed in self.allowed_domains:
            if domain == allowed or domain.endswith("." + allowed):
                return True
        return False

    # ------------------------------------------------------------------
    def add_blocked_word(self, word: str) -> List[str]:
        word = word.strip().lower()
        if word and word not in self.blocked_words:
            self.blocked_words.append(word)
        return self.blocked_words

    def remove_blocked_word(self, word: str) -> List[str]:
        word = word.strip().lower()
        self.blocked_words = [w for w in self.blocked_words if w != word]
        return self.blocked_words

    def mute_user(self, user_id: int) -> None:
        self.muted_users.add(user_id)

    def unmute_user(self, user_id: int) -> None:
        self.muted_users.discard(user_id)
