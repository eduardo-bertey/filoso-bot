"""Proveedores de IA: endpoints, disponibilidad, rotación de claves.

Modulo clave del proyecto: "determina los endpoint para IA, conectar
diferentes proveedores por disponibilidad".

- `PROVIDER_SPECS`: catalogo de proveedores con su endpoint por defecto.
- `ProviderRegistry`: sabe que proveedores hay, cuales estan disponibles
  (por clave, cooldown y salud) y prueba en orden de prioridad.
- `ProviderClient`: una llamada HTTP con el formato de cada API.

La gestion de claves vive en `bot/modules/ai/keys.py` (KeyManager). El
registry la consulta si se le pasa, asi hay una sola fuente de verdad.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...models import Completion, ProviderConfig

log = logging.getLogger("filoso.ai")


@dataclass
class ProviderSpec:
    """Catalogo estatico: endpoint y formato de cada proveedor."""

    id: str
    label: str
    style: str                      # openai | anthropic | gemini | ollama
    base_url: str
    chat_path: str = "/chat/completions"
    default_models: List[str] = field(default_factory=list)
    key_url: str = ""
    env_var: str = ""
    notes: str = ""


PROVIDER_SPECS: Dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        "openai", "OpenAI", "openai", "https://api.openai.com/v1",
        "/chat/completions", ["gpt-4o-mini", "gpt-4o"], "https://platform.openai.com/api-keys",
        "OPENAI_API_KEY",
    ),
    "openrouter": ProviderSpec(
        "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
        "/chat/completions", ["meta-llama/llama-3.3-70b-instruct"],
        "https://openrouter.ai/keys", "OPENROUTER_API_KEY",
        "Agrega HTTP-Referer y X-Title (configurables en config.toml).",
    ),
    "groq": ProviderSpec(
        "groq", "Groq", "openai", "https://api.groq.com/openai/v1",
        "/chat/completions", ["llama-3.3-70b-versatile"],
        "https://console.groq.com/keys", "GROQ_API_KEY", "muy rapido, modelos gratuitos.",
    ),
    "mistral": ProviderSpec(
        "mistral", "Mistral", "openai", "https://api.mistral.ai/v1",
        "/chat/completions", ["mistral-small-latest"],
        "https://console.mistral.ai/keys", "MISTRAL_API_KEY",
    ),
    "deepseek": ProviderSpec(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
        "/chat/completions", ["deepseek-chat"], "https://platform.deepseek.com/api_keys",
        "DEEPSEEK_API_KEY",
    ),
    "nvidia": ProviderSpec(
        "nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1",
        "/chat/completions", ["meta/llama-llama-3.3-70b-instruct"],
        "https://build.nvidia.com", "NVIDIA_API_KEY",
    ),
    "cerebras": ProviderSpec(
        "cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1",
        "/chat/completions", ["llama-3.1-8b"],
        "https://cloud.cerebras.ai", "CEREBRAS_API_KEY",
    ),
    "github": ProviderSpec(
        "github", "GitHub Models", "openai", "https://models.github.ai/inference",
        "/chat/completions", ["openai/gpt-4.1-mini"],
        "https://github.com/settings/tokens", "GITHUB_TOKEN",
    ),
    "cloudflare": ProviderSpec(
        "cloudflare", "Cloudflare AI Gateway", "openai",
        "https://gateway.ai.cloudflare.com/v1", "/chat/completions", [],
        "https://dash.cloudflare.com", "CLOUDFLARE_API_KEY",
    ),
    "anthropic": ProviderSpec(
        "anthropic", "Anthropic", "anthropic", "https://api.anthropic.com/v1",
        "/messages", ["claude-3-5-haiku-latest"],
        "https://console.anthropic.com/settings/keys", "ANTHROPIC_API_KEY",
    ),
    "gemini": ProviderSpec(
        "gemini", "Google Gemini", "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "/models/{model}:generateContent", ["gemini-2.0-flash"],
        "https://aistudio.google.com/apikey", "GEMINI_API_KEY",
    ),
    "llm7": ProviderSpec(
        "llm7", "LLM7.io", "openai", "https://api.llm7.io/v1", "/chat/completions",
        ["gpt-4.1-mini"], "https://token.llm7.io", "LLM7_API_KEY", "Key gratuita.",
    ),
    "kilo": ProviderSpec(
        "kilo", "Kilo Code", "openai", "https://api.kilo.ai/v1", "/chat/completions",
        [], "https://app.kilo.ai", "KILO_API_KEY",
    ),
}


def spec_for(provider_id: str) -> Optional[ProviderSpec]:
    return PROVIDER_SPECS.get(provider_id)


def endpoint_for(cfg: ProviderConfig, model: str = "") -> str:
    """Construye la URL final del endpoint para un proveedor + modelo."""
    path = cfg.chat_path or "/chat/completions"
    if "{model}" in path:
        path = path.replace("{model}", model)
    elif cfg.style == "gemini":
        path = f"/models/{model}:generateContent"
    base = (cfg.base_url or "").rstrip("/")
    return f"{base}{path if path.startswith('/') else '/' + path}"


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int = 0, auth_error: bool = False):
        super().__init__(message)
        self.status = status
        self.auth_error = auth_error


def _post_json(
    url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float
) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        auth = exc.code in (401, 403)
        raise ProviderError(f"HTTP {exc.code}: {raw[:300]}", exc.code, auth) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"red: {exc.reason}", 0, False) from exc


class ProviderRegistry:
    """Administra proveedores, claves, disponibilidad y rotacion."""

    def __init__(
        self,
        configs: List[ProviderConfig],
        keys_by_provider: Optional[Dict[str, List[str]]] = None,
        cooldown_seconds: float = 300.0,
        key_manager: Any = None,   # bot.modules.ai.keys.KeyManager (duck typing)
    ):
        self.cooldown_seconds = cooldown_seconds
        self.key_manager = key_manager
        self._configs: Dict[str, ProviderConfig] = {c.id: c for c in configs}
        self._keys: Dict[str, List[str]] = {
            k: list(v) for k, v in (keys_by_provider or {}).items()
        }
        self._key_index: Dict[str, int] = {k: 0 for k in self._keys}
        self._calls: Dict[str, int] = {k: 0 for k in self._configs}

    # ------------------------------------------------------------------
    def configs(self) -> List[ProviderConfig]:
        return sorted(self._configs.values(), key=lambda c: (c.priority, c.id))

    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        return self._configs.get(provider_id)

    def keys_for(self, provider_id: str) -> List[str]:
        if self.key_manager is not None:
            return self.key_manager.usable_keys(provider_id)
        return self._keys.get(provider_id, [])

    def all_keys(self, provider_id: str) -> List[str]:
        if self.key_manager is not None:
            return self.key_manager.keys(provider_id)
        return self._keys.get(provider_id, [])

    def add_key(self, provider_id: str, key: str) -> None:
        keys = self._keys.setdefault(provider_id, [])
        if key not in keys:
            keys.append(key)

    def set_enabled(self, provider_id: str, enabled: bool) -> None:
        if provider_id in self._configs:
            self._configs[provider_id].enabled = enabled

    def in_cooldown(self, provider_id: str) -> bool:
        cfg = self._configs.get(provider_id)
        if cfg and cfg.cooldown_until > time.time():
            return True
        if self.key_manager is not None:
            return self.key_manager.is_provider_cooled(provider_id)
        return False

    def mark_failure(self, provider_id: str, error: str) -> None:
        cfg = self._configs.get(provider_id)
        if cfg:
            cfg.healthy = False
            cfg.last_error = error[:300]
            cfg.cooldown_until = time.time() + self.cooldown_seconds

    def mark_success(self, provider_id: str) -> None:
        cfg = self._configs.get(provider_id)
        if cfg:
            cfg.healthy = True
            cfg.last_error = ""
            cfg.cooldown_until = 0.0

    def is_available(self, provider_id: str) -> bool:
        """Disponible = habilitado + tiene clave utilizable + no esta en cooldown."""
        cfg = self._configs.get(provider_id)
        if not cfg or not cfg.enabled:
            return False
        if self.in_cooldown(provider_id):
            return False
        spec = spec_for(provider_id)
        needs_key = not (spec and spec.style == "ollama")
        if needs_key and not self.keys_for(provider_id):
            return False
        return True

    def available_providers(self) -> List[str]:
        return [c.id for c in self.configs() if self.is_available(c.id)]

    def next_key(self, provider_id: str) -> Optional[str]:
        """Clave a usar ahora (rotacion)."""
        if self.key_manager is not None:
            return self.key_manager.next_usable_key(provider_id)
        keys = self.keys_for(provider_id)
        if not keys:
            return None
        idx = self._key_index.get(provider_id, 0) % len(keys)
        self._key_index[provider_id] = (idx + 1) % len(keys)
        return keys[idx]

    def status(self) -> List[Dict[str, Any]]:
        out = []
        for cfg in self.configs():
            spec = spec_for(cfg.id)
            out.append(
                {
                    "id": cfg.id,
                    "label": spec.label if spec else cfg.id,
                    "enabled": cfg.enabled,
                    "available": self.is_available(cfg.id),
                    "healthy": cfg.healthy,
                    "keys": len(self.all_keys(cfg.id)),
                    "usable_keys": len(self.keys_for(cfg.id)),
                    "models": [m for m in cfg.models if m not in cfg.blocked_models],
                    "blocked_models": cfg.blocked_models,
                    "cooldown_until": cfg.cooldown_until,
                    "last_error": cfg.last_error,
                    "endpoint": endpoint_for(cfg, (cfg.models or [""])[0]),
                    "calls": self._calls.get(cfg.id, 0),
                }
            )
        return out


class ProviderClient:
    """Ejecuta una completación contra un proveedor, rotando claves.

    Filosofia FilosoIA:

      * 401/403 -> esa key no sirve: se marca y se prueba la SIGUIENTE,
      * 429     -> rate limit: no se quema la key, se pasa al proveedor
                   siguiente (o se reintenta),
      * 5xx     -> el proveedor esta caido: cooldown y siguiente proveedor,
      * ok      -> la key queda como sana y se cuentan llamadas y tokens.
    """

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    # ------------------------------------------------------------------
    def complete(
        self,
        provider_id: str,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 900,
        timeout: float = 60.0,
        system: str = "",
    ) -> Completion:
        cfg = self.registry.get(provider_id)
        if cfg is None:
            return Completion(ok=False, error=f"proveedor desconocido: {provider_id}")

        spec = spec_for(provider_id)
        style = cfg.style or (spec.style if spec else "openai")

        keys = self.registry.keys_for(provider_id)
        if not keys and style != "ollama":
            return Completion(ok=False, error=f"{provider_id}: sin claves disponibles", provider=provider_id, model=model)
        if not keys:
            keys = [""]

        attempts: List[str] = []
        last_error = ""

        for key in keys:
            try:
                comp = self._call(style, cfg, model, messages, key, temperature, max_tokens, timeout, system)
            except ProviderError as exc:
                last_error = str(exc)
                attempts.append(f"{provider_id}: {last_error[:80]}")
                kind = self._handle_failure(provider_id, key, last_error)
                if kind == "auth":
                    continue          # probar la siguiente key
                break                 # rate/server: no insistir con este proveedor
            else:
                if comp.ok and comp.text.strip():
                    self._handle_success(provider_id, key, comp)
                    comp.attempts = attempts
                    return comp
                last_error = comp.error or "respuesta vacía"
                attempts.append(f"{provider_id}: {last_error[:80]}")
                break

        self.registry.mark_failure(provider_id, last_error)
        return Completion(
            ok=False, error=last_error or "sin respuesta", provider=provider_id,
            model=model, attempts=attempts,
        )

    # ------------------------------------------------------------------
    def _handle_failure(self, provider_id: str, key: str, error: str) -> str:
        """Aplica la regla de FilosoIA y devuelve el tipo de error."""
        km = self.registry.key_manager
        if km is not None:
            kind = km.mark_failure(provider_id, error, key)
            if kind == "auth":
                log.warning("%s: key no responde (%s) -> rotando", provider_id, error[:80])
                return "auth"
            if kind == "rate":
                log.warning("%s: rate limit, no quemo la key", provider_id)
                return "rate"
            if kind == "server":
                km.set_provider_cooldown(provider_id, self.registry.cooldown_seconds)
                log.warning("%s: error de servidor -> cooldown", provider_id)
                return "server"
            return "other"
        self.registry.mark_failure(provider_id, error)
        return "other"

    def _handle_success(self, provider_id: str, key: str, comp: Completion) -> None:
        self.registry.mark_success(provider_id)
        km = self.registry.key_manager
        if km is not None:
            km.mark_success(provider_id, key)
            km.track_call(provider_id)
            usage = comp.usage or {}
            km.track_tokens(
                provider_id,
                int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
                int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
            )

    # ------------------------------------------------------------------
    # llamadas por formato
    # ------------------------------------------------------------------
    def _call(self, style, cfg, model, messages, key, temperature, max_tokens, timeout, system) -> Completion:
        if style == "openai":
            return self._openai(cfg, model, messages, key, temperature, max_tokens, timeout, system)
        if style == "anthropic":
            return self._anthropic(cfg, model, messages, key, temperature, max_tokens, timeout, system)
        if style == "gemini":
            return self._gemini(cfg, model, messages, key, temperature, max_tokens, timeout, system)
        if style == "ollama":
            return self._ollama(cfg, model, messages, temperature, timeout, system)
        raise ProviderError(f"formato de proveedor desconocido: {style}")

    def _openai(self, cfg, model, messages, key, temperature, max_tokens, timeout, system):
        payload: Dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        payload.update(cfg.extra_body or {})
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(cfg.extra_headers or {})
        data = _post_json(endpoint_for(cfg, model), headers, payload, timeout)
        text = ""
        try:
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except (IndexError, AttributeError):
            pass
        return Completion(text=text or "", provider=cfg.id, model=model, usage=data.get("usage", {}) or {})

    def _anthropic(self, cfg, model, messages, key, temperature, max_tokens, timeout, system):
        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": list(messages),
        }
        if system:
            payload["system"] = system
        payload.update(cfg.extra_body or {})
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if key:
            headers["x-api-key"] = key
        headers.update(cfg.extra_headers or {})
        data = _post_json(endpoint_for(cfg, model), headers, payload, timeout)
        text = ""
        try:
            text = (data.get("content") or [{}])[0].get("text", "")
        except (IndexError, AttributeError):
            pass
        return Completion(text=text or "", provider=cfg.id, model=model, usage=data.get("usage", {}) or {})

    def _gemini(self, cfg, model, messages, key, temperature, max_tokens, timeout, system):
        contents = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        payload.update(cfg.extra_body or {})
        headers = {"Content-Type": "application/json"}
        headers.update(cfg.extra_headers or {})
        url = endpoint_for(cfg, model)
        if key:
            url = f"{url}?key={key}"
        data = _post_json(url, headers, payload, timeout)
        text = ""
        try:
            text = (
                (data.get("candidates") or [{}])[0].get("content", {})
                .get("parts", [{}])[0].get("text", "")
            )
        except (IndexError, AttributeError, KeyError):
            pass
        return Completion(text=text or "", provider=cfg.id, model=model)

    def _ollama(self, cfg, model, messages, temperature, timeout, system):
        payload: Dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + list(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        payload.update(cfg.extra_body or {})
        headers = {"Content-Type": "application/json"}
        headers.update(cfg.extra_headers or {})
        data = _post_json(endpoint_for(cfg, model), headers, payload, timeout)
        text = ""
        try:
            text = (data.get("message") or {}).get("content", "")
        except (IndexError, AttributeError):
            pass
        return Completion(text=text or "", provider=cfg.id, model=model)
