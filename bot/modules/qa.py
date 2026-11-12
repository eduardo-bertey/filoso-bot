"""Módulo de preguntas y respuestas (trivia + banco local + IA opcional).

Dos modos:

  * `/preguntas` -> trivia de preguntas multiple choice (datos locales).
  * pregunta libre -> busca en el banco local (`data/qa.json`) y si no
    encuentra nada, deriva a la IA (si hay proveedor disponible).
"""
from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Message, OutgoingMessage

log = logging.getLogger("filoso.qa")


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[¿?¡!.,;:\"'()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class QABank:
    """Banco local de preguntas/respuestas (JSON)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.items = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("banco QA invalido: %s", exc)
            self.items = []
            return
        if isinstance(data, dict):
            data = data.get("items", [])
        self.items = [i for i in data if isinstance(i, dict) and i.get("q")]

    def search(self, question: str, limit: int = 1) -> List[Dict[str, Any]]:
        """Busca por coincidencia de palabras (sin IA)."""
        q = normalize(question)
        if not q:
            return []
        words = [w for w in q.split() if len(w) > 3]
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in self.items:
            haystack = normalize(
                f"{item.get('q', '')} {item.get('tags', '')} {item.get('a', '')}"
            )
            hits = sum(1 for w in words if w in haystack)
            score = hits / max(1, len(words))
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [item for score, item in scored[:limit] if score >= 0.34]

    def random_item(self, exclude: Optional[str] = None) -> Optional[Dict[str, Any]]:
        pool = [i for i in self.items if i.get("q") != exclude]
        if not pool:
            return None
        return random.choice(pool)


def format_qa(item: Dict[str, Any]) -> str:
    lineas = [f"❓ *{item.get('q', '')}*", "", str(item.get("a", ""))]
    tags = item.get("tags")
    if tags:
        if isinstance(tags, list):
            tags = ", ".join(str(t) for t in tags)
        lineas.append("")
        lineas.append(f"_{tags}_")
    return "\n".join(lineas)
