# Módulos

Cómo agregar un juego, un comando, un filtro o un proveedor de IA.

---

## Agregar un juego

### 1. La lógica en `bot/modules/games.py`

```python
def mi_juego_start(state: StateStore, chat_id: int) -> OutgoingMessage:
    session = state.get_session(chat_id)
    session.game = "mi_juego"
    session.active = True
    session.data = {"contador": 0}
    state.save_session(session)
    return OutgoingMessage(text="¡Empecemos!")


def mi_juego_answer(state: StateStore, chat_id: int, text: str) -> Optional[OutgoingMessage]:
    session = state.get_session(chat_id)
    if not session.active or session.game != "mi_juego":
        return None
    data = dict(session.data)
    data["contador"] = int(data["contador"]) + 1
    session.data = data
    state.save_session(session)
    return OutgoingMessage(text=f"Llevás {data['contador']}")
```

### 2. El handler en `bot/cli.py`

En `_handle_command`:

```python
if cmd in ("mijuego",):
    return games.mi_juego_start(self.state, msg.chat_id)
```

Y si el juego necesita leer mensajes durante la partida, en `handle_message`,
junto a trivia:

```python
if session and session.game == "mi_juego":
    out = games.mi_juego_answer(self.state, msg.chat_id, msg.text)
    if out:
        return out
```

### 3. El comando en el clasificador

En `bot/modules/classifier.py`, agregá el nombre a `KNOWN_COMMANDS` para que
`/mijuego` caiga en `Intent.COMMAND` (si no, cae en `UNKNOWN` y el router
responde "no conozco el comando").

---

## Agregar un comando

1. En `bot/cli.py` → `_handle_command`, agregá el `if cmd in (...)`.
2. En `bot/modules/classifier.py` → `KNOWN_COMMANDS` (para que el router lo
   reconozca) y si es un tipo especial, en `classify()`.
3. Documentalo en el README y en `/help` (`_help_text`).

Ejemplo:

```python
if cmd in ("ping",):
    return OutgoingMessage(text="pong 🏓")
```

---

## Agregar un filtro

En `bot/core/filters.py`, `FilterPipeline.check()`, antes del `return`
final:

```python
# 7) links de invite (anti-spam)
if "t.me/+" in lower:
    return FilterVerdict(
        allowed=False, reason="link de invitación", action="warn"
    )
```

Acciones disponibles:

| action | qué hace el bot |
|---|---|
| `none` | deja pasar |
| `warn` | avisa en el log, no borra |
| `delete` | borra el mensaje |
| `mute` | borra y silencia al usuario |

Si el filtro necesita estado (por ejemplo, "3 strikes"), guardalo en
`self._recent` (ya hay un deque por chat para duplicados).

---

## Agregar un proveedor de IA

### Endpoint compatible con OpenAI (el caso común)

Basta con un bloque en `config.json`:

```json
"providers": {
  "mi-provider": {
    "style": "openai",
    "base_url": "https://mi-api.com/v1",
    "chat_path": "/chat/completions",
    "models": ["mi-modelo-1", "mi-modelo-2"],
    "enabled": true,
    "priority": 35,
    "timeout": 60
  }
}
```

Y la key en `data/credentials.json` → `api_keys["mi-provider"]`.

### Formato nuevo (no compatible con OpenAI)

1. Agregá el spec al catálogo en `bot/modules/ai/providers.py`:

```python
"miapi": ProviderSpec(
    "miapi", "Mi API", "miapi", "https://mi-api.com/v1",
    "/generate", ["mi-modelo"],
    "https://mi-api.com/keys", "MIAPI_API_KEY",
),
```

2. Implementá el formato en `ProviderClient`:

```python
def _miapi(self, cfg, model, messages, key, temperature, max_tokens, timeout, system):
    payload = {"model": model, "prompt": messages[-1]["content"]}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    url = endpoint_for(cfg, model)
    try:
        data = _post_json(url, headers, payload, timeout)
    except ProviderError as exc:
        self.registry.mark_failure(cfg.id, str(exc))
        return Completion(ok=False, error=str(exc), provider=cfg.id, model=model)
    self.registry.mark_success(cfg.id)
    return Completion(text=data.get("text", ""), provider=cfg.id, model=model)
```

3. Registralo en `ProviderClient.complete`:

```python
if style == "miapi":
    return self._miapi(cfg, model, messages, key, temperature, max_tokens, timeout, system)
```

4. Aggregate el default en `settings.py` → `DEFAULT_CONFIG["providers"]`.

---

## Agregar un backend de Telegram distinto

`bot/adapters/` tiene la interfaz. Un adapter nuevo debe:

1. Producir `Message` a partir de lo que le llegue.
2. Enviar `OutgoingMessage`.

Después, cambiá `FilosoBot.__init__` para usar tu adapter. Los módulos no se
tocan: no saben qué hay detrás.

---

## Agregar un backend de estado (DB en vez de JSON)

`bot/core/storage.py` expone `JsonStore` con `get/set/update/pop/all`.
Si lo reemplazás por SQLite o Redis, mantené esos métodos y nada más se
rompe.
