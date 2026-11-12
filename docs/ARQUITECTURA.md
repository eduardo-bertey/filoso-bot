# Arquitectura

## El principio

**Telegram no se mezcla con la IA, y la IA no se mezcla con los juegos.**

Cada capa tiene una responsabilidad y una dirección de dependencia:

```
        Telegram  ──────────────►  bot/adapters/telegram.py
                                      │
                                      │  produce Message (dominio)
                                      ▼
        Router  ───────────────►  bot/core/router.py
                                      │
                             ┌────────┼────────┬──────────┐
                             ▼        ▼        ▼          ▼
                       classifier   games     qa      modules/ai
                             │        │        │          │
                             └────────┴────────┴──────────┘
                                      │
                                      │  produce OutgoingMessage (dominio)
                                      ▼
        Telegram  ◄──────────────  bot/adapters/telegram.py
```

Los módulos de negocio **nunca** importan `bot.adapters`. Si lo hicieran, la
modularidad es fiction: no podrías testear el juego sin Telegram.

## Módulos

### `bot/adapters/telegram.py`
El único que habla con `api.telegram.org`.

- `get_updates()` → long polling.
- `parse_update(update)` → `Message` (traduce el JSON de Telegram al dominio).
- `send(chat_id, OutgoingMessage)` → envía.
- `delete_message(chat_id, message_id)` → borra.

### `bot/core/router.py`
Tabla de ruteo por `Intent`. Cada módulo se registra con
`router.register(Intent.XXX, handler)`. El router:

1. Clasifica el mensaje (`modules/classifier.py`).
2. Prueba los handlers de esa intención en orden.
3. Si ninguno devuelve nada, prueba el `fallback`.
4. Una excepción en un módulo se loguea y **no** tumba el bot.

### `bot/core/filters.py`
Pipeline que corre **antes** del router. Devuelve un `FilterVerdict`:

- `allowed=True/False`
- `action`: `none | delete | mute | warn`
- `modified_text`: mensaje recortado, si hubo que recortarlo.

### `bot/core/cleanup.py`
Servicio de borrado automático. Lleva la cuenta de mensajes recibidos y de
mensajes enviados por chat, y decide cuándo borrar:

- `due_by_messages()` → cada N mensajes.
- `due_by_time()` → cada N segundos.
- `purge_expired()` → borra lo que superó la edad máxima.

Respeta la regla de Telegram: los mensajes de otros usuarios sólo se borran
pasadas las 48 horas; los nuestros, al instante.

### `bot/core/state.py` / `storage.py`
Persistencia JSON con escritura atómica (`os.replace`) y lock de hilo.
Guarda: sesiones de juego, contadores, historial de conversación por chat.

### `bot/modules/classifier.py`
`classify(msg) -> Classification` con el `Intent`:

| Intent | Ejemplos | Quién lo atiende |
|---|---|---|
| `COMMAND` | `/algo` | `_handle_command` |
| `REQUEST` | `/ask x`, texto largo | IA |
| `QUESTION` | "¿qué es...?" | IA |
| `INFO` | "info", "estado" | `_handle_info` |
| `GAME` | "jugar trivia" | juegos |
| `QA` | "preguntame", "trivia" | QA / trivia |
| `SMALLTALK` | "hola" | `_handle_smalltalk` |
| `UNKNOWN` | "asdf" | fallback (IA si hay) |

Determinista, sin IA: sólo reglas y palabras clave. Así el bot puede
funcionar aunque no haya ningún proveedor configurado.

### `bot/modules/ai/providers.py`
Catálogo + disponibilidad.

- `PROVIDER_SPECS`: `id → ProviderSpec` (label, estilo, endpoint, modelos por
  defecto, URL para sacar la key, variable de entorno).
- `endpoint_for(cfg, model)`: arma la URL final (resuelve `{model}`).
- `ProviderRegistry`: sabe qué proveedores hay, si están disponibles, rota
  claves, aplica cooldown.
- `ProviderClient`: una llamada HTTP por formato (`openai`, `anthropic`,
  `gemini`, `ollama`).

### `bot/modules/ai/service.py`
`AIService.ask(prompt, chat_id, history)`:

1. Arma la lista de candidatos: proveedor forzado para el chat → proveedor por
   defecto → el resto por prioridad.
2. Filtra los que no están disponibles.
3. Prueba uno por uno. El primero que responde gana.
4. El que falla queda en cooldown y se pasa al siguiente.

### `bot/modules/games.py`
`trivia`, `rps` (piedra papel tijera) y `dice`.
Cada juego usa `StateStore` para el estado por chat.

### `bot/modules/qa.py`
Banco local de preguntas y respuestas (`data/qa.json`) con búsqueda por
palabras. Si no encuentra nada y hay IA configurada, deriva.

## Flujo de un mensaje

```
update (Telegram JSON)
  -> telegram.parse_update()      -> Message
  -> cleanup.register_incoming()  -> cuenta para la limpieza
  -> filters.check()              -> FilterVerdict
       | bloqueado -> delete/mute, fin
  -> chat_allowed()               -> si el chat no está permitido, fin
  -> ¿hay partida activa?         -> games.trivia_answer
  -> router.dispatch()            -> ModuleResult
       -> cli.FilosoBot.handle_ai / handle_game / ...
  -> OutgoingMessage
  -> telegram.send()
  -> cleanup.track()               -> se puede borrar despues
```

## Dónde tocar para extender

| Quiero... | Toco... |
|---|---|
| un juego nuevo | `bot/modules/games.py` + un handler en `cli.py` |
| un proveedor de IA nuevo | `PROVIDER_SPECS` + un `style` en `ProviderClient` |
| un filtro nuevo | `bot/core/filters.py` (método `check`) |
| un comando nuevo | `cli.py` → `_handle_command` + `KNOWN_COMMANDS` en `classifier.py` |
| cambiar la limpieza | `bot/core/cleanup.py` + `config.json → filters.cleanup` |
| un backend de Telegram distinto | `bot/adapters/` (nuevo adapter, mismo `Message`) |
