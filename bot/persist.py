"""Persist — clon de `src/persist.rs` de FilosoIA.

FilosoIA guarda TODO en un solo archivo: `config.json`, con las claves
cifradas (AES-256-GCM + Argon2). Si no hay password, queda en texto plano.

Acá es igual:

    data/config.json   ->  {"encrypted": true, "data": "<base64>"}

El contenido descifrado:

    {
      "telegram_token": "123:ABC",
      "providers": {
        "groq": {
          "keys": [{"key": "gsk_...", "is_default": true}],
          "metadata": {"gsk_...": "{\\"uses\\": 3, ...}"},
          "healthy": true,
          "cooldown_until": null,
          "last_error": "",
          "blocked_models": [],
          "custom_models": []
        }
      },
      "calls": {"groq": {"calls": 12, "window_start": 1700000000.0}},
      "token_usage": {"groq": {"prompt": 1200, "completion": 300}}
    }

Misma API que el Rust:

    get_key / set_key / add_key / remove_key / set_default_key / rotate_key
    get_metadata / set_metadata
    mark_error / mark_healthy / is_on_cooldown
    track_call / calls_in_window
    track_tokens / get_token_usage
    block_model / unblock_model / is_model_blocked / get_blocked_models
    add_custom_model / remove_custom_model / get_custom_models
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import crypto

log = logging.getLogger("filoso.persist")

COOLDOWN_SECS = 300       # igual que FilosoIA
CALL_WINDOW_SECS = 3600   # ventana de 1 hora para el rate limit

EMPTY_PROVIDER: Dict[str, Any] = {
    "keys": [],
    "metadata": {},
    "healthy": True,
    "cooldown_until": None,
    "last_error": None,
    "blocked_models": [],
    "custom_models": [],
}


class Persist:
    """Estado persistente (cifrado) de tokens, claves y proveedores."""

    def __init__(self, path: Path, password: Optional[str] = None):
        self.path = Path(path)
        self.password = password
        self.providers: Dict[str, Dict[str, Any]] = {}
        self.telegram_token: str = ""
        self.calls: Dict[str, Dict[str, float]] = {}
        self.token_usage: Dict[str, Dict[str, int]] = {}
        self.load()

    def reset(self) -> None:
        """Estado vacio (se usa cuando el archivo no se puede leer)."""
        self.providers = {}
        self.telegram_token = ""
        self.calls = {}
        self.token_usage = {}

    # ------------------------------------------------------------------
    # carga / guardado
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self.providers = {}
            self.telegram_token = ""
            self.calls = {}
            self.token_usage = {}
            return
        raw = self.path.read_text(encoding="utf-8")
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("%s esta corrupto, se empieza de cero", self.path.name)
            self.providers = {}
            self.telegram_token = ""
            return

        data = None
        if isinstance(blob, dict) and "encrypted" in blob:
            if not blob.get("encrypted"):
                # plano: puede venir en base64 (nuestro) o como JSON directo
                raw_data = blob.get("data", {})
                if isinstance(raw_data, str):
                    try:
                        raw_data = json.loads(base64.b64decode(raw_data))
                    except Exception:  # noqa: BLE001
                        raw_data = json.loads(raw_data)
                data = raw_data
            else:
                if not self.password:
                    log.error("El archivo está cifrado y no se pasó password.")
                    self.reset()
                    return
                # 1) formato FilosoIA (Argon2 salt fijo + AES-GCM sin AAD)
                try:
                    data = crypto.decrypt_filosofia(blob["data"], self.password)
                except Exception:  # noqa: BLE001
                    # 2) nuestro formato (salt propio)
                    try:
                        data = crypto.decrypt_payload(blob, self.password)
                    except Exception as exc:  # noqa: BLE001
                        log.error("No se pudo descifrar: %s", exc)
                        self.reset()
                        return
        else:
            # formato plano de FilosoIA: {"providers": {...}}
            data = blob

        if not isinstance(data, dict):
            log.error("Formato de persist inesperado.")
            self.reset()
            return

        self.telegram_token = str(data.get("telegram_token", "") or "")
        self.providers = data.get("providers", {}) or {}
        self.calls = data.get("calls", {}) or {}
        self.token_usage = data.get("token_usage", {}) or {}

    def save(self) -> None:
        payload = {
            "telegram_token": self.telegram_token,
            "providers": self.providers,
            "calls": self.calls,
            "token_usage": self.token_usage,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.password:
            try:
                # formato FilosoIA: {encrypted, data} -> FilosoIA puede leerlo
                out = {
                    "encrypted": True,
                    "data": crypto.encrypt_filosofia(payload, self.password),
                }
            except Exception:  # noqa: BLE001 - sin cryptography: nuestro formato
                blob = crypto.encrypt_payload(payload, self.password)
                out = {
                    "encrypted": True,
                    "data": blob["data"],
                    "kdf": blob["kdf"],
                    "cipher": blob["cipher"],
                }
        else:
            # plano, igual que FilosoIA sin password
            out = payload
        self.path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # helpers de proveedor
    # ------------------------------------------------------------------
    def _cfg(self, provider: str) -> Dict[str, Any]:
        cfg = self.providers.get(provider)
        if cfg is None:
            cfg = json.loads(json.dumps(EMPTY_PROVIDER))
            self.providers[provider] = cfg
        for key, default in EMPTY_PROVIDER.items():
            cfg.setdefault(key, json.loads(json.dumps(default)))
        return cfg

    # ---------------- claves ----------------
    def get_keys(self, provider: str) -> List[Tuple[int, Dict[str, Any]]]:
        return list(enumerate(self._cfg(provider)["keys"]))

    def keys(self, provider: str) -> List[str]:
        """Las claves del proveedor (strings)."""
        return [k.get("key", "") for _, k in self.get_keys(provider) if k.get("key")]

    def get_key(self, provider: str) -> Optional[str]:
        keys = self._cfg(provider)["keys"]
        if not keys:
            return None
        for k in keys:
            if k.get("is_default"):
                return k.get("key")
        return keys[0].get("key")

    def set_key(self, provider: str, key: str) -> None:
        cfg = self._cfg(provider)
        found = False
        for entry in cfg["keys"]:
            if entry.get("is_default"):
                entry["key"] = key
                found = True
                break
        if not found and cfg["keys"]:
            cfg["keys"][0]["key"] = key
        elif not cfg["keys"]:
            cfg["keys"].append({"key": key, "is_default": True})
        self.save()

    def add_key(self, provider: str, key: str) -> None:
        cfg = self._cfg(provider)
        if any(k.get("key") == key for k in cfg["keys"]):
            return
        cfg["keys"].append({"key": key, "is_default": not cfg["keys"]})
        self.save()

    def remove_key(self, provider: str, index: int) -> bool:
        cfg = self._cfg(provider)
        keys = cfg["keys"]
        if index < 0 or index >= len(keys):
            return False
        removed = keys.pop(index)
        if removed.get("is_default") and keys:
            keys[0]["is_default"] = True
        self.save()
        return True

    def set_default_key(self, provider: str, index: int) -> bool:
        cfg = self._cfg(provider)
        keys = cfg["keys"]
        if index < 0 or index >= len(keys):
            return False
        for i, entry in enumerate(keys):
            entry["is_default"] = (i == index)
        self.save()
        return True

    def rotate_key(self, provider: str) -> Optional[str]:
        """FilosoIA: la siguiente clave pasa a ser la default."""
        cfg = self._cfg(provider)
        keys = cfg["keys"]
        if len(keys) < 2:
            self.save()
            return self.get_key(provider)
        idx = next((i for i, k in enumerate(keys) if k.get("is_default")), 0)
        keys[idx]["is_default"] = False
        nxt = (idx + 1) % len(keys)
        keys[nxt]["is_default"] = True
        self.save()
        return keys[nxt].get("key")

    # ---------------- metadata por clave ----------------
    def get_metadata(self, provider: str, key: str) -> Optional[str]:
        return self._cfg(provider)["metadata"].get(key)

    def set_metadata(self, provider: str, key: str, value: str) -> None:
        self._cfg(provider)["metadata"][key] = value
        self.save()

    # ---------------- salud / cooldown ----------------
    def mark_error(self, provider: str, error: str) -> None:
        cfg = self._cfg(provider)
        cfg["healthy"] = False
        cfg["cooldown_until"] = time.time() + COOLDOWN_SECS
        cfg["last_error"] = error[:300]
        self.save()

    def mark_healthy(self, provider: str) -> None:
        cfg = self._cfg(provider)
        cfg["healthy"] = True
        cfg["cooldown_until"] = None
        cfg["last_error"] = None
        self.save()

    def is_on_cooldown(self, provider: str) -> bool:
        until = self._cfg(provider).get("cooldown_until")
        if not until:
            return False
        return time.time() < float(until)

    def cooldown_remaining(self, provider: str) -> int:
        until = self._cfg(provider).get("cooldown_until")
        if not until:
            return 0
        return max(0, int(float(until) - time.time()))

    # ---------------- rate limit por hora ----------------
    def track_call(self, provider: str) -> int:
        now = time.time()
        state = self.calls.setdefault(provider, {"calls": 0, "window_start": now})
        if now - float(state.get("window_start", now)) > CALL_WINDOW_SECS:
            state["calls"] = 0
            state["window_start"] = now
        state["calls"] = int(state.get("calls", 0)) + 1
        self.save()
        return int(state["calls"])

    def calls_in_window(self, provider: str) -> int:
        now = time.time()
        state = self.calls.get(provider)
        if not state:
            return 0
        if now - float(state.get("window_start", now)) > CALL_WINDOW_SECS:
            return 0
        return int(state.get("calls", 0))

    # ---------------- tokens ----------------
    def track_tokens(self, provider: str, prompt_tokens: int, completion_tokens: int) -> None:
        entry = self.token_usage.setdefault(provider, {"prompt": 0, "completion": 0})
        entry["prompt"] = int(entry.get("prompt", 0)) + int(prompt_tokens or 0)
        entry["completion"] = int(entry.get("completion", 0)) + int(completion_tokens or 0)
        self.save()

    def get_token_usage(self, provider: str) -> Tuple[int, int]:
        entry = self.token_usage.get(provider, {})
        return int(entry.get("prompt", 0)), int(entry.get("completion", 0))

    # ---------------- modelos bloqueados / propios ----------------
    def block_model(self, provider: str, model: str) -> None:
        cfg = self._cfg(provider)
        if model not in cfg["blocked_models"]:
            cfg["blocked_models"].append(model)
        self.save()

    def unblock_model(self, provider: str, model: str) -> None:
        cfg = self._cfg(provider)
        cfg["blocked_models"] = [m for m in cfg["blocked_models"] if m != model]
        self.save()

    def get_blocked_models(self, provider: str) -> List[str]:
        return list(self._cfg(provider)["blocked_models"])

    def is_model_blocked(self, provider: str, model: str) -> bool:
        return model in self._cfg(provider)["blocked_models"]

    def add_custom_model(self, provider: str, model: str) -> None:
        cfg = self._cfg(provider)
        if model not in cfg["custom_models"]:
            cfg["custom_models"].append(model)
        self.save()

    def remove_custom_model(self, provider: str, index: int) -> bool:
        cfg = self._cfg(provider)
        models = cfg["custom_models"]
        if index < 0 or index >= len(models):
            return False
        models.pop(index)
        self.save()
        return True

    def get_custom_models(self, provider: str) -> List[str]:
        return list(self._cfg(provider)["custom_models"])

    # ---------------- token de telegram ----------------
    def set_telegram_token(self, token: str) -> None:
        self.telegram_token = token
        self.save()

    # ---------------- estado legible ----------------
    def provider_ids(self) -> List[str]:
        return sorted(self.providers.keys())

    def summary(self) -> Dict[str, Any]:
        return {
            pid: {
                "keys": len(self._cfg(pid)["keys"]),
                "healthy": self._cfg(pid)["healthy"],
                "cooldown": self.cooldown_remaining(pid),
                "blocked_models": self.get_blocked_models(pid),
                "calls_1h": self.calls_in_window(pid),
                "tokens": self.get_token_usage(pid),
            }
            for pid in self.provider_ids()
        }
