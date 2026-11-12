"""Configuración: `config/config.toml` + `config/rules.toml` + credenciales.

Regla de oro del proyecto:
  * `config/config.toml`    -> ajustes NO secretos (endpoints, juegos, filtros).
  * `config/rules.toml`     -> reglas de conducta (temas permitidos, juego
                               interactivo, limpieza, IA). Editables por CLI.
  * `data/credentials.json` -> secretos (token de Telegram, API keys) CIFRADOS.

Nunca se escribe una API key en un .toml.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import crypto
from .config import Config
from .models import ProviderConfig
from .persist import Persist
from .rules import DEFAULT_RULES, RuleSet
from .toml_io import load_toml, save_toml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = CONFIG_DIR / "config.toml"
RULES_PATH = CONFIG_DIR / "rules.toml"
# ruta del persist: un solo archivo, igual que FilosoIA.
# Se puede apuntar al config.json de FilosoIA con la variable FILOSO_PERSIST.
# mismo nombre y formato que FilosoIA: config.json en la raiz del proyecto.
# Se puede apuntar al config.json de FilosoIA con la variable FILOSO_PERSIST.
CREDENTIALS_PATH = Path(os.environ.get("FILOSO_PERSIST") or (BASE_DIR / "config.json"))

DEFAULT_CONFIG = Config.as_dict()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Settings:
    """Configuración completa ya resuelta (secretos incluidos, en memoria)."""

    config: Dict[str, Any] = field(default_factory=dict)
    rules: RuleSet = field(default_factory=lambda: RuleSet(dict(DEFAULT_RULES)))
    persist: Optional[Persist] = None
    master_password: Optional[str] = None

    # ---------------- Telegram ----------------
    @property
    def telegram_token(self) -> str:
        return str(self.persist.telegram_token) if self.persist else ""

    @property
    def allowed_chats(self) -> List[int]:
        return _int_list(self.config.get("bot", {}).get("allowed_chats", []))

    @property
    def allow_all_chats(self) -> bool:
        return bool(self.config.get("bot", {}).get("allow_all_chats", False))

    @property
    def admin_ids(self) -> List[int]:
        return _int_list(self.config.get("bot", {}).get("admin_ids", []))

    # ---------------- IA ----------------
    @property
    def ai_enabled(self) -> bool:
        return bool(self.config.get("ai", {}).get("enabled", False)) and bool(
            self.rules.get("ai.enabled", True)
        )

    def provider_configs(self) -> List[ProviderConfig]:
        raw = self.config.get("providers", {}) or {}
        out: List[ProviderConfig] = []
        for pid, data in raw.items():
            cfg = ProviderConfig.from_dict({"id": pid, **(data or {})})
            out.append(cfg)
        out.sort(key=lambda c: (c.priority, c.id))
        return out

    def api_keys(self, provider_id: str) -> List[str]:
        if not self.persist:
            return []
        return self.persist.keys(provider_id)

    # ---------------- filtros ----------------
    @property
    def filters(self) -> Dict[str, Any]:
        """Filtros = sección del config.toml + reglas de `rules.toml`."""
        base = dict(self.config.get("filters", {}) or {})
        rules_filters = self.rules.get("filters", {}) or {}
        for key, value in rules_filters.items():
            if isinstance(value, dict):
                merged = dict(base.get(key) or {})
                merged.update(value)
                base[key] = merged
            else:
                base[key] = value
        return base

    @property
    def cleanup(self) -> Dict[str, Any]:
        """Limpieza: reglas mandan sobre la config."""
        cfg = dict(self.config.get("cleanup", {}) or {})
        rules = self.rules.get("cleanup", {}) or {}
        cfg.update(rules)
        return cfg

    @property
    def games(self) -> Dict[str, Any]:
        cfg = dict(self.config.get("games", {}) or {})
        rules = self.rules.get("games", {}) or {}
        cfg.update(rules)
        return cfg

    # ---------------- helpers ----------------
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def rule(self, path: str, default: Any = None) -> Any:
        return self.rules.get(path, default)


def _int_list(raw: Any) -> List[int]:
    out: List[int] = []
    for item in raw or []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def load_settings(password: Optional[str] = None) -> Settings:
    """Carga config.toml + rules.toml + el persist cifrado (tipo FilosoIA)."""
    config = _deep_merge(DEFAULT_CONFIG, load_toml(CONFIG_PATH))
    rules = RuleSet.load(RULES_PATH)
    # migracion desde el formato viejo (data/config.json) si hace falta
    if not CREDENTIALS_PATH.exists():
        viejo = DATA_DIR / "config.json"
        if viejo.exists():
            try:
                viejo.replace(CREDENTIALS_PATH)
            except OSError:
                pass
    persist = Persist(CREDENTIALS_PATH, password)
    return Settings(config=config, rules=rules, persist=persist, master_password=password)


def save_config(config: Dict[str, Any]) -> None:
    save_toml(CONFIG_PATH, config)


def save_rules(rules: RuleSet) -> None:
    rules.save()


def save_credentials(creds: Dict[str, Any], password: Optional[str]) -> None:
    """Guarda token + API keys en el persist cifrado (mismo archivo que FilosoIA)."""
    persist = Persist(CREDENTIALS_PATH, password)
    token = creds.get("telegram_token")
    if token:
        persist.telegram_token = str(token)
    for provider, keys in (creds.get("api_keys") or {}).items():
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            if key and key not in persist.keys(provider):
                persist.add_key(provider, key)
    persist.save()


def config_exists() -> bool:
    return CONFIG_PATH.exists()


def rules_exist() -> bool:
    return RULES_PATH.exists()


def credentials_exist() -> bool:
    return CREDENTIALS_PATH.exists()


def credentials_encrypted() -> bool:
    """True si el archivo de credenciales está cifrado (pide clave maestra)."""
    if not CREDENTIALS_PATH.exists():
        return False
    try:
        blob = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return True   # si no se puede leer, asumimos cifrado (mas seguro)
    if "encrypted" in blob:
        return bool(blob.get("encrypted"))
    return str(blob.get("cipher", "none")) != "none"


def env_master_password() -> Optional[str]:
    return os.environ.get("FILOSO_MASTER_PASSWORD") or None
