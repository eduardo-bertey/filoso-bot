# Configuración

Tres archivos, con una regla estricta:

| Archivo | Contenido | ¿Secreto? |
|---|---|---|
| `config/config.toml` | endpoints, chats, admins, juegos, listas de filtros | **no** |
| `config/rules.toml` | reglas de conducta (comportamiento) | **no** |
| `data/credentials.json` | token de Telegram + API keys | **sí, cifrado** |

Nunca pongas una API key en un `.toml`.

> `config.toml` y `rules.toml` se editan con el CLI:
> `python -m bot config set <ruta> <valor>` y `python -m bot rules set <ruta> <valor>`.

---

## `config.toml`

```toml
[bot]
name = "FilosoBot"
language = "es"
admin_ids = [11111111]
allowed_chats = [123456789, -1001234567890]
allow_all_chats = false

[telegram]
poll_timeout = 30
parse_mode = "Markdown"
max_message_length = 4000

[ai]
enabled = true
default_provider = "groq"
default_model = "llama-3.3-70b-versatile"
temperature = 0.7
max_tokens = 900
system_prompt = "Sos un asistente en Telegram..."
timeout = 60
cooldown_seconds = 300
context_messages = 6

[qa]
enabled = true
fallback_to_ai = true
data_file = "data/qa.json"

[filters]
blocked_words = ["spoiler"]
muted_users = []
allowed_domains = []

[providers.groq]
style = "openai"
base_url = "https://api.groq.com/openai/v1"
chat_path = "/chat/completions"
models = ["llama-3.3-70b-versatile"]
enabled = true
priority = 20
timeout = 45

[providers.openrouter]
style = "openai"
base_url = "https://openrouter.ai/api/v1"
chat_path = "/chat/completions"
models = ["meta-llama/llama-3.3-70b-instruct"]
enabled = false
priority = 20
timeout = 60

[providers.openrouter.extra_headers]
HTTP-Referer = "https://github.com/emanuelbertey/FilosoIA"
X-Title = "FilosoBot"
```

### `[bot]`

| Campo | Qué hace |
|---|---|
| `admin_ids` | ids de Telegram con permisos de configuración |
| `allowed_chats` | chats donde el bot responde (grupos usan id negativo) |
| `allow_all_chats` | `true` = responde en cualquier chat |

> `allowed_chats` vacío **y** `allow_all_chats` en `false` = no responde a nadie.
> Es el default seguro.

### `[telegram]`

| Campo | Default | Qué hace |
|---|---|---|
| `poll_timeout` | 30 | segundos de long polling |
| `parse_mode` | Markdown | `Markdown`, `MarkdownV2` o `HTML` |
| `max_message_length` | 4000 | tope duro de longitud |

### `[ai]`

| Campo | Default | Qué hace |
|---|---|---|
| `enabled` | false | si la IA participa |
| `default_provider` | "" | id del proveedor preferido |
| `default_model` | "" | modelo (si vacío, el primero del proveedor) |
| `temperature` | 0.7 | 0 = determinista, 1+ = creativo |
| `max_tokens` | 900 | tope de tokens de la respuesta |
| `system_prompt` | texto | instrucciones de sistema |
| `timeout` | 60 | segundos de espera por llamada |
| `cooldown_seconds` | 300 | cuánto queda fuera de juego un proveedor que falló |
| `context_messages` | 6 | mensajes de historial que se mandan como contexto |

### `[filters]` (las listas)

| Campo | Qué hace |
|---|---|
| `blocked_words` | palabras clave / malas palabras |
| `muted_users` | ids silenciados (no se responden) |
| `allowed_domains` | lista vacía = se permiten todos |

Los **comportamientos** de los filtros (borrar, silenciar, spam, límites) están
en `rules.toml` → ver [`REGLAS.md`](REGLAS.md).

### `[providers.<id>]`

| Campo | Qué hace |
|---|---|
| `style` | `openai`, `anthropic`, `gemini` u `ollama` (define el formato del body) |
| `base_url` | raíz del endpoint (sin la ruta) |
| `chat_path` | ruta del endpoint; acepta `{model}` (Gemini) |
| `models` | lista de modelos; el primero es el default |
| `enabled` | si se intenta usar |
| `priority` | menor = se prueba antes |
| `timeout` | segundos |
| `extra_headers` | cabeceras extra (ej: `HTTP-Referer` de OpenRouter) |
| `extra_body` | campos extra en el JSON del request |

### Endpoint compatible con OpenAI (cualquiera)

```toml
[providers.mi-servidor]
style = "openai"
base_url = "http://localhost:8000/v1"
chat_path = "/chat/completions"
models = ["mi-modelo"]
enabled = true
priority = 40
```

### Editar con el CLI

```bash
python -m bot config show
python -m bot config set ai.temperature 0.3
python -m bot config set providers.groq.enabled true
python -m bot config set providers.groq.models '["llama-3.3-70b-versatile","llama3-70b"]'
```

---

## `rules.toml`

Todo lo de conducta va acá. Ver [`REGLAS.md`](REGLAS.md) para la lista
completa y los ejemplos.

```toml
[general]
allow_offtopic = true
allow_unknown = true
ignore_bots = true
always_reply = false
require_mention = false
log_level = "INFO"

[reply]
max_length = 900
quote = true
parse_mode = "Markdown"

[games]
interactive = true
allow_commands_during_game = true
timeout_seconds = 600
allow_skip = true
rounds = 5

[qa]
enabled = true
fallback_to_ai = true
min_confidence = 0.34
allow_questions = true

[ai]
enabled = false
allow_without_command = true
send_history = true
warn_if_disabled = true
scheduler_interval = 300
scheduler_probe = true

[cleanup]
enabled = true
every_n_messages = 25
every_seconds = 300
delete_older_than_seconds = 3600
protect_commands = true

[filters]
enabled = true
delete_on_block = true
mute_on_block = false
block_links = false
max_chars = 2000

[filters.spam]
enabled = true
max_repeats = 6
duplicate_window = 20
max_duplicates = 3
```

---

## Credenciales

`data/credentials.json` (cifrado):

```json
{
  "telegram_token": "123456:ABC-DEF...",
  "api_keys": {
    "groq": [
      {
        "key": "gsk_...",
        "is_default": true,
        "label": "key #0",
        "uses": 12,
        "failures": 0,
        "last_error": "",
        "cooldown_until": 0.0,
        "blocked": false
      }
    ]
  }
}
```

Varias claves para el mismo proveedor = rotación automática. Gestor completo en
[`GESTOR-CLAVES.md`](GESTOR-CLAVES.md).

### Variables de entorno

| Variable | Para qué |
|---|---|
| `FILOSO_MASTER_PASSWORD` | clave maestra (evita tipearla) |
| `FILOSO_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Precedencia

1. `rules.toml` gana sobre `config.toml` para los campos que están en ambos
   (filtros, limpieza, juegos).
2. `config.toml` gana sobre los defaults internos.
3. La clave maestra nunca está en ningún `.toml`.

Los cambios se aplican al reiniciar el bot.
