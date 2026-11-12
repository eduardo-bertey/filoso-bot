"""Persistencia JSON simple (sin dependencias).

Sirve para: sesiones de juego, contadores de mensajes, histórico de
respuestas, lista de mensajes a limpiar.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict


class JsonStore:
    """Archivo JSON con escritura atómica y lock de hilo."""

    def __init__(self, path: Path, default: Dict[str, Any] | None = None):
        self.path = Path(path)
        self.default = default or {}
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if self.path.exists():
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    backup = self.path.with_suffix(".corrupt")
                    try:
                        self.path.rename(backup)
                    except OSError:
                        pass
                    self._data = dict(self.default)
            else:
                self._data = dict(self.default)
            return self._data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".tmp-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # ---------------- acceso ----------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self.save()

    def update(self, key: str, values: Dict[str, Any]) -> None:
        with self._lock:
            current = self._data.setdefault(key, {})
            if not isinstance(current, dict):
                current = {}
                self._data[key] = current
            current.update(values)
            self.save()

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.pop(key, default)

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)
