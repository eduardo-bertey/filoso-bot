"""Gestor de API keys — sobre el Persist de FilosoIA.

Toda la verdad está en `bot/persist.py` (clon de `persist.rs`): un solo
archivo cifrado con las claves, el estado de salud, los modelos bloqueados
y el tracking de llamadas/tokens.

Este módulo es la vista "amigable" del gestor: entradas con metadata,
rotación, bloqueo, cooldown, rate limit por hora y probe HTTP.

Filosofía (idéntica a FilosoIA):

  * 401/403 -> esa key no sirve: se marca y se prueba la SIGUIENTE,
  * 429     -> no se quema la key,
  * 5xx     -> el proveedor entra en cooldown (300 s),
  * una llamada por hora se cuentan en una ventana de 1 h,
  * los tokens se acumulan por proveedor.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ... import crypto
from ...persist import COOLDOWN_SECS as COOLDOWN_SECONDS
from ...persist import Persist
from .providers import PROVIDER_SPECS, ProviderConfig, ProviderSpec

log = logging.getLogger("filoso.keys")

AUTH_ERROR_MARKERS = ("401", "403", "unauthorized", "forbidden", "permission", "invalid api key")
RATE_MARKERS = ("429", "too many requests", "rate limit")
SERVER_MARKERS = ("500", "502", "503", "504", "internal server", "bad gateway", "service unavailable")


@dataclass
class KeyEntry:
    """Una API key con su estado (la metadata vive en el Persist)."""

    key: str
    is_default: bool = False
    label: str = ""
    added_at: float = field(default_factory=time.time)
    uses: int = 0
    failures: int = 0
    last_error: str = ""
    cooldown_until: float = 0.0
    blocked: bool = False

    @classmethod
    def from_raw(cls, raw: Any, index: int = 0) -> "KeyEntry":
        if isinstance(raw, str):
            return cls(key=raw, is_default=(index == 0))
        if isinstance(raw, dict):
            known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
            data = {k: v for k, v in raw.items() if k in known}
            data.setdefault("key", "")
            return cls(**data)
        return cls(key="", is_default=False)

    def masked(self, keep: int = 4) -> str:
        return crypto.mask_secret(self.key, keep)

    def available(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return bool(self.key) and not self.blocked and self.cooldown_until <= now

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "is_default": self.is_default,
            "label": self.label,
            "added_at": self.added_at,
            "uses": self.uses,
            "failures": self.failures,
            "last_error": self.last_error,
            "cooldown_until": self.cooldown_until,
            "blocked": self.blocked,
        }


class KeyManager:
    """CRUD + salud + rotación de las API keys (delego en el Persist)."""

    def __init__(self, persist: Persist):
        self.persist = persist

    # ------------------------------------------------------------------
    # persistencia
    # ------------------------------------------------------------------
    def save(self) -> None:
        self.persist.save()

    # ------------------------------------------------------------------
    # lectura
    # ------------------------------------------------------------------
    def providers(self) -> List[str]:
        return self.persist.provider_ids()

    def entries(self, provider: str) -> List[KeyEntry]:
        """Entradas con su metadata (usos, fallos, bloqueo)."""
        out: List[KeyEntry] = []
        for _, raw in self.persist.get_keys(provider):
            entry = KeyEntry.from_raw(raw)
            meta = self._meta(provider, entry.key)
            entry.uses = int(meta.get("uses", 0))
            entry.failures = int(meta.get("failures", 0))
            entry.last_error = str(meta.get("last_error", ""))
            entry.blocked = bool(meta.get("blocked", False))
            entry.cooldown_until = float(meta.get("cooldown_until", 0.0))
            entry.label = str(meta.get("label", "")) or f"key #{entry.key[:0]}"
            out.append(entry)
        return out

    def _meta(self, provider: str, key: str) -> Dict[str, Any]:
        raw = self.persist.get_metadata(provider, key)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _set_meta(self, provider: str, key: str, meta: Dict[str, Any]) -> None:
        self.persist.set_metadata(provider, key, json.dumps(meta, ensure_ascii=False))

    def keys(self, provider: str) -> List[str]:
        return [k.get("key", "") for _, k in self.persist.get_keys(provider) if k.get("key")]

    def count(self, provider: str) -> int:
        return len(self.persist.get_keys(provider))

    def default_entry(self, provider: str) -> Optional[KeyEntry]:
        entries = self.entries(provider)
        if not entries:
            return None
        for entry in entries:
            if entry.is_default:
                return entry
        return entries[0]

    def default_key(self, provider: str) -> Optional[str]:
        return self.persist.get_key(provider)

    # ------------------------------------------------------------------
    # escritura
    # ------------------------------------------------------------------
    def add(self, provider: str, key: str, label: str = "") -> KeyEntry:
        key = (key or "").strip()
        if not key:
            raise ValueError("la key está vacía")
        if key in self.keys(provider):
            raise ValueError("esa key ya está cargada en ese proveedor")
        self.persist.add_key(provider, key)
        entry = KeyEntry(key=key, is_default=self.count(provider) == 1, label=label)
        meta = entry.to_dict()
        meta.pop("key", None)
        self._set_meta(provider, key, meta)
        self.persist.save()
        return entry

    def remove(self, provider: str, index: int) -> bool:
        ok = self.persist.remove_key(provider, index)
        if ok:
            self.persist.save()
        return ok

    def set_default(self, provider: str, index: int) -> bool:
        ok = self.persist.set_default_key(provider, index)
        if ok:
            self.persist.save()
        return ok

    def rotate(self, provider: str) -> Optional[str]:
        return self.persist.rotate_key(provider)

    def block(self, provider: str, index: int) -> bool:
        entries = self.entries(provider)
        if index < 0 or index >= len(entries):
            return False
        meta = self._meta(provider, entries[index].key)
        meta["blocked"] = True
        self._set_meta(provider, entries[index].key, meta)
        self.persist.save()
        return True

    def unblock(self, provider: str, index: int) -> bool:
        entries = self.entries(provider)
        if index < 0 or index >= len(entries):
            return False
        meta = self._meta(provider, entries[index].key)
        meta["blocked"] = False
        meta["cooldown_until"] = 0.0
        self._set_meta(provider, entries[index].key, meta)
        self.persist.save()
        return True

    # ------------------------------------------------------------------
    # salud (filosofía FilosoIA)
    # ------------------------------------------------------------------
    @staticmethod
    def classify_error(error: str) -> str:
        low = (error or "").lower()
        if any(m in low for m in AUTH_ERROR_MARKERS):
            return "auth"
        if any(m in low for m in RATE_MARKERS):
            return "rate"
        if any(m in low for m in SERVER_MARKERS):
            return "server"
        return "other"

    def mark_failure(self, provider: str, error: str, key: str = "") -> str:
        """Aplica la regla de FilosoIA y devuelve el tipo de error."""
        kind = self.classify_error(error)
        entry = self._find(provider, key)
        if entry and kind != "rate":
            meta = self._meta(provider, entry.key)
            meta["failures"] = int(meta.get("failures", 0)) + 1
            meta["last_error"] = (error or "")[:200]
            self._set_meta(provider, entry.key, meta)
        if kind == "auth":
            self.persist.rotate_key(provider)
        elif kind == "server":
            self.persist.mark_error(provider, error)
        self.persist.save()
        return kind

    def mark_success(self, provider: str, key: str = "") -> None:
        entry = self._find(provider, key)
        if entry:
            meta = self._meta(provider, entry.key)
            meta["uses"] = int(meta.get("uses", 0)) + 1
            meta["last_error"] = ""
            self._set_meta(provider, entry.key, meta)
        self.persist.mark_healthy(provider)
        self.persist.save()

    def _find(self, provider: str, key: str) -> Optional[KeyEntry]:
        for entry in self.entries(provider):
            if key and entry.key == key:
                return entry
        return self.default_entry(provider)

    # ------------------------------------------------------------------
    # cooldown del proveedor (del Persist)
    # ------------------------------------------------------------------
    def set_provider_cooldown(self, provider: str, seconds: float) -> None:
        cfg = self.persist._cfg(provider)  # noqa: SLF001 - mismo modulo de dominio
        cfg["healthy"] = False
        cfg["cooldown_until"] = time.time() + seconds
        self.persist.save()

    def clear_provider_cooldown(self, provider: str) -> None:
        self.persist.mark_healthy(provider)

    def provider_cooldown(self, provider: str) -> float:
        until = self.persist._cfg(provider).get("cooldown_until")  # noqa: SLF001
        return float(until) if until else 0.0

    def is_provider_cooled(self, provider: str) -> bool:
        return self.persist.is_on_cooldown(provider)

    # ------------------------------------------------------------------
    # rate limit por hora y tokens (del Persist)
    # ------------------------------------------------------------------
    def track_call(self, provider: str) -> int:
        return self.persist.track_call(provider)

    def calls_in_window(self, provider: str) -> int:
        return self.persist.calls_in_window(provider)

    def track_tokens(self, provider: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.persist.track_tokens(provider, prompt_tokens, completion_tokens)

    def token_usage(self, provider: str) -> Tuple[int, int]:
        return self.persist.get_token_usage(provider)

    # ------------------------------------------------------------------
    # selección de clave
    # ------------------------------------------------------------------
    def next_usable_key(self, provider: str) -> Optional[str]:
        now = time.time()
        entries = self.entries(provider)
        if not entries:
            return None
        default = next((e for e in entries if e.is_default), None)
        if default and default.available(now):
            return default.key
        for entry in entries:
            if entry.available(now):
                return entry.key
        return None

    def usable_keys(self, provider: str) -> List[str]:
        now = time.time()
        return [e.key for e in self.entries(provider) if e.available(now)]

    # ------------------------------------------------------------------
    # modelos bloqueados / personalizados (del Persist)
    # ------------------------------------------------------------------
    def block_model(self, provider: str, model: str) -> None:
        self.persist.block_model(provider, model)

    def unblock_model(self, provider: str, model: str) -> None:
        self.persist.unblock_model(provider, model)

    def blocked_models(self, provider: str) -> List[str]:
        return self.persist.get_blocked_models(provider)

    def add_custom_model(self, provider: str, model: str) -> None:
        self.persist.add_custom_model(provider, model)

    def custom_models(self, provider: str) -> List[str]:
        return self.persist.get_custom_models(provider)

    # ------------------------------------------------------------------
    # estado legible
    # ------------------------------------------------------------------
    def status(self, provider: str) -> List[Dict[str, Any]]:
        now = time.time()
        out = []
        for i, entry in enumerate(self.entries(provider)):
            out.append(
                {
                    "index": i,
                    "masked": entry.masked(),
                    "default": entry.is_default,
                    "label": entry.label,
                    "uses": entry.uses,
                    "failures": entry.failures,
                    "blocked": entry.blocked,
                    "cooldown": max(0, int(entry.cooldown_until - now)),
                    "last_error": entry.last_error,
                    "added_at": entry.added_at,
                }
            )
        return out

    def summary(self) -> Dict[str, Any]:
        return self.persist.summary()


def probe_key(cfg: ProviderConfig, key: str, timeout: float = 20.0) -> Tuple[bool, str]:
    """Prueba una key contra el endpoint del proveedor.

    Devuelve (ok, detalle). No manda prompts: sólo pide la lista de modelos
    (o `tags` en Ollama), que es barata y no consume tokens.
    """
    import urllib.error
    import urllib.request

    spec: Optional[ProviderSpec] = PROVIDER_SPECS.get(cfg.id)
    style = cfg.style or (spec.style if spec else "openai")
    base = (cfg.base_url or "").rstrip("/")

    if style == "ollama":
        url = f"{base}/api/tags"
        headers: Dict[str, str] = {}
    elif style == "anthropic":
        url = f"{base}/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif style == "gemini":
        url = f"{base}/models?key={key}"
        headers = {}
    else:
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {key}"}
        if cfg.id == "openrouter":
            headers.update(cfg.extra_headers or {})

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return True, f"HTTP {resp.status} ({len(body)} bytes)"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return False, f"HTTP {exc.code}: {raw[:120]}"
    except urllib.error.URLError as exc:
        return False, f"red: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
