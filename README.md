# FilosoBot

Bot de Telegram **modular** en Python: separa Telegram de la IA, y separa cada
responsabilidad en su propio módulo.

```
filoso-bot/
├── bot/
│   ├── adapters/        # habla con Telegram (nada más)
│   │   └── telegram.py
│   ├── core/            # router, filtros, limpieza, estado
│   │   ├── router.py
│   │   ├── filters.py
│   │   ├── cleanup.py
│   │   ├── state.py
│   │   └── storage.py
│   ├── modules/
│   │   ├── classifier.py # determina el TIPO de mensaje
│   │   ├── ai/          # proveedores, endpoints, claves, scheduler
│   │   ├── games.py     # trivia, piedra papel tijera, dados
│   │   └── qa.py        # banco de preguntas y respuestas local
│   ├── models.py        # dataclasses de dominio
│   ├── crypto.py        # credenciales cifradas (AES-GCM / ChaCha20 + Argon2/PBKDF2)
│   ├── rules.py         # reglas de conducta (rules.toml)
│   ├── settings.py      # carga de config
│   ├── toml_io.py       # leer/escribir TOML
│   └── cli.py           # CLI
├── config/
│   ├── config.toml      # endpoints, chats, admins (NO secretos)
│   └── rules.toml       # reglas de conducta (editable por CLI)
├── data/                # credenciales cifradas + estado + banco QA
└── docs/
```

**Regla de oro:** los módulos de negocio nunca llaman a la API de Telegram.
Devuelven un `OutgoingMessage` y el adapter lo envía. La IA es un módulo más:
si no hay proveedor configurado, el bot sigue funcionando con juegos, trivia y
respuestas locales.

---

## Requisitos

- Python 3.11 o superior (hace falta `tomllib`).
- **Cero dependencias obligatorias**: todo usa la stdlib (`urllib`, `json`,
  `hashlib`, `tomllib`).
- Opcional: `cryptography` (AES-256-GCM) y `argon2-cffi` (Argon2id) para cifrado
  y KDF más fuertes. Sin ellos el bot usa ChaCha20 + PBKDF2 y funciona igual.

```bash
pip install -r requirements.txt   # opcional, recomendado
```

---

## Puesta en marcha

```bash
cd filoso-bot
python -m bot setup     # interactivo: token, chats, IA, clave maestra
python -m bot run       # arranca el bot
```

`setup` te pregunta, en este orden:

1. **Token del bot** (lo sacás de [@BotFather](https://t.me/BotFather) con `/newbot`).
2. **Chats permitidos**: ids numéricos separados por coma. Vacío = responde en cualquier chat.
3. **Admins**: ids que pueden usar comandos de configuración.
4. **Reglas**: opcionalmente las ajustás ahí mismo.
5. **¿Querés IA?** Si decís que sí, elegís proveedor (OpenAI, OpenRouter, Groq,
   Mistral, DeepSeek, NVIDIA, Cerebras, Anthropic, Gemini, Ollama, GitHub
   Models, Cloudflare, LLM7, Kilo), pegás la API key y el modelo.
6. **Clave maestra** para cifrar las credenciales. Si la dejás vacía, se guardan
   en texto plano (con aviso).

Después:

```bash
python -m bot status       # qué hay configurado
python -m bot rules        # reglas de conducta
python -m bot keys list    # estado de las API keys
python -m bot errors       # últimos errores (siempre visible en la CLI)
python -m bot providers    # estado de proveedores de IA
python -m bot send "hola" --chat 123456789
```

---

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/help` | lista de comandos |
| `/ask <pregunta>` | pregunta a la IA (alias `/request`, `/ia`, `/preguntar`) |
| `/preguntas` | trivia de preguntas múltiples |
| `/info` | estado del bot |
| `/proveedores` | proveedores de IA y su disponibilidad |
| `/reglas` | reglas activas |
| `/usar <proveedor> [modelo]` | fija proveedor/modelo **para ese chat** |
| `/limpiar` | borra el historial de contexto del chat |
| `/filtros` | muestra los filtros activos |
| `/trivia` | trivia |
| `piedra` / `papel` / `tijera` | piedra papel tijera |
| `/dado [n] [caras]` | tira dados |
| `/salir` | termina la partida en curso |

Sin comando también funciona: cualquier mensaje con `?` va a la IA, y
`/preguntas` activa la trivia.

---

## Gestor de API keys (filosofía FilosoIA)

Varias claves por proveedor, una default. Si una no responde (401/403), el bot
la bloquea y **prueba la siguiente en la misma llamada**, sin que el usuario
se entere.

```bash
python -m bot keys add groq            # agregar
python -m bot keys list                # ver (enmascaradas)
python -m bot keys test groq           # probar por HTTP (no consume tokens)
python -m bot keys rotate groq         # rotar a mano
python -m bot keys remove groq 1       # quitar
python -m bot usage                    # llamadas por hora y tokens
python -m bot scheduler                # vigilante automático
```

| Error | Acción |
|---|---|
| 401 / 403 | bloquear esa key y probar la siguiente |
| 429 | no quemar la key; pasar al proveedor siguiente |
| 5xx | cooldown del proveedor (300 s) |

Todo en [`docs/GESTOR-CLAVES.md`](docs/GESTOR-CLAVES.md) y la equivalencia con
FilosoIA en [`docs/FILOSOFIA.md`](docs/FILOSOFIA.md).

---

## Configuración: `config.toml` + `rules.toml`

| Archivo | Qué guarda |
|---|---|
| `config/config.toml` | endpoints, chats, admins, listas de filtros |
| `config/rules.toml` | **reglas de conducta** |
| `data/credentials.json` | token + API keys, **cifrados** |

Las reglas se editan desde el CLI:

```bash
python -m bot rules                                     # listar
python -m bot rules set games.interactive false         # editar
python -m bot rules get games.interactive
python -m bot rules reset                               # defaults
```

Reglas que importan:

| Regla | Default | Qué hace |
|---|---|---|
| `general.allow_offtopic` | `true` | permite mensajes de otros temas |
| `general.require_mention` | `false` | en grupos, sólo responde si lo nombran |
| `games.interactive` | `true` | el juego sigue interactivo durante la partida |
| `games.allow_commands_during_game` | `true` | se pueden usar comandos durante la partida |
| `games.timeout_seconds` | `600` | inactividad antes de terminar la partida |
| `ai.enabled` | `false` | activa la IA |
| `ai.send_history` | `true` | manda el historial del chat como contexto |
| `cleanup.every_n_messages` | `25` | evalúa limpieza cada N mensajes |
| `cleanup.every_seconds` | `300` | evalúa limpieza cada N segundos |
| `cleanup.delete_older_than_seconds` | `3600` | antigüedad para borrar |
| `filters.blocked_words` (en config) | `[]` | palabras clave / malas palabras |

Lista completa: [`docs/REGLAS.md`](docs/REGLAS.md).

---

## Modo IA vs modo local

El bot arranca **siempre** con lo local. La IA es un extra:

| Sin IA configurada | Con IA configurada |
|---|---|
| trivia, juegos, preguntas del banco local, comandos | todo lo anterior + respuestas libres |

Si la IA falla (sin clave, provider caído, cooldown), el bot lo dice y sigue
funcionando. Nunca se filtra la API key en una respuesta.

---

## Limpieza automática de mensajes

- **cada N mensajes** (`cleanup.every_n_messages`) → se evalúa si hay que borrar.
- **cada N segundos** (`cleanup.every_seconds`) → se evalúa también.
- Se borran los mensajes con más de `cleanup.delete_older_than_seconds`.
- Los mensajes **propios** se borran al instante; los de otros usuarios sólo
  si pasaron las 48 h que exige Telegram.

## Filtros automáticos

- `blocked_words`: palabras clave / malas palabras → se borra o se avisa.
- `muted_users`: usuarios silenciados (el bot no responde).
- `allowed_domains`: si está vacío se permiten todos; con lista, sólo esos.
- `block_links`: bloquea cualquier `http://` o `https://`.
- `max_chars`: recorta mensajes largos.
- `spam.max_repeats`: detecta caracteres repetidos (`aaaaaaa`).
- `spam.max_duplicates` + `duplicate_window`: detecta el mismo mensaje repetido.

---

## Proveedores de IA y disponibilidad

`bot/modules/ai/providers.py` tiene el catálogo (`PROVIDER_SPECS`) con el
endpoint por defecto de cada uno. `ProviderRegistry` decide:

1. Se prueban **por prioridad** (menor número primero).
2. Un proveedor sin clave, deshabilitado o en **cooldown** no se intenta.
3. Si uno falla, queda en cooldown (300 s por defecto) y se pasa al siguiente.
4. Dentro de un proveedor, si hay **varias claves**, se rotan.
5. `401/403` = error de autenticación → se prueba otra clave.

Endpoints soportados:

| Proveedor | Endpoint | Formato |
|---|---|---|
| OpenAI | `https://api.openai.com/v1/chat/completions` | openai |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` | openai |
| Groq | `https://api.groq.com/openai/v1/chat/completions` | openai |
| Mistral | `https://api.mistral.ai/v1/chat/completions` | openai |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` | openai |
| NVIDIA | `https://integrate.api.nvidia.com/v1/chat/completions` | openai |
| Cerebras | `https://api.cerebras.ai/v1/chat/completions` | openai |
| GitHub Models | `https://models.github.ai/inference/chat/completions` | openai |
| Cloudflare | `https://gateway.ai.cloudflare.com/v1/chat/completions` | openai |
| Anthropic | `https://api.anthropic.com/v1/messages` | anthropic |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | gemini |
| Ollama | `http://localhost:11434/api/chat` | ollama |
| LLM7.io | `https://api.llm7.io/v1/chat/completions` | openai |
| Kilo Code | `https://api.kilo.ai/v1/chat/completions` | openai |

Podés agregar cualquier endpoint compatible con OpenAI definiéndolo en
`config.toml` → `[providers.<id>]`.

---

## Credenciales y cifrado

Igual que [FilosoIA](https://github.com/emanuelbertey/FilosoIA): las API keys se
guardan **cifradas** y separadas de la configuración.

- `config/config.toml` → **nunca** lleva secretos.
- `data/credentials.json` → token de Telegram + API keys, cifrados.
- Cifrado: **AES-256-GCM** si instalás `cryptography`; si no,
  **ChaCha20 + HMAC-SHA256** (encrypt-then-MAC) en Python puro.
- Clave maestra derivada con **Argon2id** si instalás `argon2-cffi`; si no,
  **PBKDF2-HMAC-SHA256** (200.000 iteraciones).
- Salt aleatorio por archivo y nonce aleatorio por cifrado.
- La clave maestra se puede pasar por variable de entorno
  `FILOSO_MASTER_PASSWORD` para no tipearla.

```bash
python -m bot setup   # clave maestra
export FILOSO_MASTER_PASSWORD="mi-clave"   # opcional, Linux/macOS
```

> Si perdés la clave maestra, las credenciales cifradas no se pueden recuperar.
> Borra `data/credentials.json` y volvé a correr `setup`.

---

## Documentación

- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — cómo encajan los módulos.
- [`docs/CONFIGURACION.md`](docs/CONFIGURACION.md) — cada campo de los `.toml`.
- [`docs/REGLAS.md`](docs/REGLAS.md) — reglas de conducta y cómo editarlas.
- [`docs/GESTOR-CLAVES.md`](docs/GESTOR-CLAVES.md) — API keys y rotación.
- [`docs/FILOSOFIA.md`](docs/FILOSOFIA.md) — clon de la filosofía de FilosoIA.
- [`docs/MODULOS.md`](docs/MODULOS.md) — cómo agregar un módulo o un juego.
- [`docs/SEGURIDAD.md`](docs/SEGURIDAD.md) — modelo de amenazas y buenas prácticas.

---

## Archivos que genera el bot

```
config/config.toml        # ajustes (editables a mano o por CLI)
config/rules.toml         # reglas (editables por CLI)
data/credentials.json     # secretos cifrados
data/keys_state.json      # salud de claves, llamadas/hora, tokens
data/state.json           # sesiones de juego + contadores + historial
data/qa.json              # banco de preguntas y respuestas
```

`data/state.json`, `data/keys_state.json` y `data/qa.json` se pueden borrar sin
romper nada: el bot los recrea.
