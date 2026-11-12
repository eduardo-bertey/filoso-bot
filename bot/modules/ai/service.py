"""Servicio de IA: elige proveedor por disponibilidad y rota claves.

Reglas (mismo criterio que FilosoIA):
  * se prueban los proveedores por prioridad (menor = primero),
  * si uno falla -> queda en cooldown y se pasa al siguiente,
  * dentro de un proveedor, si hay varias claves, se rotan,
  * 401/403 = error de auth -> se prueba la siguiente clave/provider,
  * nunca se filtra la API key a la respuesta del chat.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from ...models import Completion
from ..classifier import Classification
from .providers import (
    PROVIDER_SPECS,
    ProviderClient,
    ProviderConfig,
    ProviderRegistry,
    endpoint_for,
    spec_for,
)

log = logging.getLogger("filoso.ai.service")


class AIService:
    """Fachada del bot hacia los proveedores de IA."""

    def __init__(
        self,
        registry: ProviderRegistry,
        default_provider: str = "",
        default_model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 900,
        timeout: float = 60.0,
        system_prompt: str = "",
        history_size: int = 6,
    ):
        self.registry = registry
        self.client = ProviderClient(registry)
        self.default_provider = default_provider
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.system_prompt = system_prompt
        self.history_size = history_size
        self._forced: Dict[int, Tuple[str, str]] = {}   # chat_id -> (provider, model)

    # ------------------------------------------------------------------
    def set_default(self, provider: str = "", model: str = "") -> None:
        if provider:
            self.default_provider = provider
        if model:
            self.default_model = model

    def force_for_chat(self, chat_id: int, provider: str, model: str = "") -> None:
        self._forced[chat_id] = (provider, model)

    def clear_for_chat(self, chat_id: int) -> None:
        self._forced.pop(chat_id, None)

    def model_for(self, provider_id: str) -> str:
        cfg = self.registry.get(provider_id)
        if cfg and cfg.models:
            usable = [m for m in cfg.models if m not in cfg.blocked_models]
            if usable:
                return usable[0]
            if cfg.models:
                return cfg.models[0]
        spec = spec_for(provider_id)
        if spec and spec.default_models:
            return spec.default_models[0]
        return ""

    # ------------------------------------------------------------------
    def candidates(self, chat_id: int = 0) -> List[str]:
        """Lista de proveedores a probar, en orden."""
        order: List[str] = []

        forced = self._forced.get(chat_id)
        if forced and forced[0]:
            order.append(forced[0])

        if self.default_provider:
            order.append(self.default_provider)

        for cfg in self.registry.configs():
            if cfg.id not in order:
                order.append(cfg.id)

        return [p for p in order if self.registry.is_available(p)]

    def is_enabled(self) -> bool:
        return bool(self.candidates())

    # ------------------------------------------------------------------
    def ask(
        self,
        prompt: str,
        chat_id: int = 0,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Completion:
        """Pide una respuesta. Prueba proveedores en orden hasta que uno funcione."""
        prompt = (prompt or "").strip()
        if not prompt:
            return Completion(ok=False, error="pregunta vacía")

        candidates = self.candidates(chat_id)
        if not candidates:
            return Completion(
                ok=False,
                error=(
                    "No hay ningun proveedor de IA disponible. "
                    "Configuralo con `python -m bot setup --provider <id>` "
                    "o activá un proveedor sin clave (ollama)."
                ),
            )

        messages: List[Dict[str, str]] = list(history or [])[-self.history_size:]
        messages.append({"role": "user", "content": prompt})

        attempts: List[str] = []
        last_error = ""

        for provider_id in candidates:
            model = self._forced.get(chat_id, ("", ""))[1] if chat_id in self._forced else ""
            model = model or self.model_for(provider_id)
            if not model:
                attempts.append(f"{provider_id}: sin modelo configurado")
                continue
            attempts.append(f"{provider_id}/{model}")
            comp = self.client.complete(
                provider_id=provider_id,
                model=model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
                system=self.system_prompt,
            )
            if comp.ok and comp.text.strip():
                comp.attempts = attempts
                return comp
            last_error = comp.error or "respuesta vacía"
            log.warning("proveedor %s fallo: %s", provider_id, last_error)

        return Completion(ok=False, error=last_error or "todos los proveedores fallaron", attempts=attempts)

    # ------------------------------------------------------------------
    def status_lines(self) -> List[str]:
        """Texto para /info y /proveedores."""
        lines = []
        for st in self.registry.status():
            flags = []
            if st["enabled"]:
                flags.append("habilitado")
            if st["available"]:
                flags.append("disponible")
            if st["cooldown_until"] > time.time():
                flags.append(f"cooldown {int(st['cooldown_until'] - time.time())}s")
            if st["last_error"]:
                flags.append("ultimo error: " + st["last_error"][:60])
            lines.append(
                f"- {st['label']} ({st['id']}): {', '.join(flags) if flags else 'inactivo'} "
                f"| claves: {st['keys']} | modelos: {', '.join(st['models'][:4]) or '-'}"
            )
        return lines
