"""Lectura y escritura de TOML.

- Lectura: `tomllib` (stdlib, Python 3.11+).
- Escritura: serializer propio (la stdlib no escribe TOML).

Los archivos que maneja el bot usan un subconjunto sencillo de TOML:
tablas anidadas `[a.b]`, escalares y listas de escalares.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


class TomlError(RuntimeError):
    pass


def load_toml(path: Path) -> Dict[str, Any]:
    """Lee un .toml. Devuelve {} si no existe."""
    path = Path(path)
    if not path.exists():
        return {}
    if tomllib is None:
        raise TomlError(
            "Este Python no trae tomllib (3.11+). Instalá Python 3.11 o superior, "
            "o editá el archivo a mano."
        )
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise TomlError(f"{path.name} esta corrupto: {exc}") from exc


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        # basic string de TOML: escapado JSON es compatible
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    raise TomlError(f"tipo no soportado en TOML: {type(value).__name__}")


def dump_toml(data: Dict[str, Any]) -> str:
    """Serializa un dict a TOML (tablas anidadas + escalares + listas)."""
    lines: List[str] = []

    def walk(node: Dict[str, Any], path: List[str]) -> None:
        scalars = {k: v for k, v in node.items() if not isinstance(v, dict)}
        tables = {k: v for k, v in node.items() if isinstance(v, dict)}
        if path:
            lines.append(f"[{'.'.join(path)}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_fmt(value)}")
        if scalars or path:
            lines.append("")
        for key, value in tables.items():
            walk(value, path + [key])

    walk(data, [])
    text = "\n".join(lines).strip()
    return text + "\n"


def save_toml(path: Path, data: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(data), encoding="utf-8")


def parse_value(raw: str) -> Any:
    """Interpreta un valor escrito por el usuario en el CLI."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in ("true", "si", "sí", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    if raw.startswith("[") or raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return tomllib.loads(raw) if tomllib else raw
            except Exception:  # noqa: BLE001
                return raw
    if raw.startswith("[") is False and "," in raw:
        parts = [p.strip().strip('"').strip("'") for p in raw.split(",")]
        if all(parts):
            return parts
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw.strip('"').strip("'")
