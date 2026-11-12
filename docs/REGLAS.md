# Reglas de conducta (`config/rules.toml`)

Las reglas dijisten **qué hace el bot**, separado de **cómo está configurado**.

| Archivo | Contenido |
|---|---|
| `config/config.toml` | endpoints, claves de proveedor, chats, admins, juegos |
| `config/rules.toml` | reglas de conducta (comportamiento) |
| `data/credentials.json` | secretos cifrados (token + API keys) |

Todas las reglas se editan desde el CLI, sin tocar el archivo a mano.

---

## Ver las reglas

```bash
python -m bot rules
```

Salida:

```
📄 Reglas (C:\...\filoso-bot\config\rules.toml)

[general]
  general.allow_offtopic = True
      → Permitir mensajes de otros temas (los no clasificados)
  general.allow_unknown = True
      → Responder aunque no se entienda el mensaje
  ...

Editar:  python -m bot rules set <ruta> <valor>
```

## Cambiar una regla

```bash
python -m bot rules set games.interactive false
python -m bot rules set general.allow_offtopic true
python -m bot rules set filters.spam.max_repeats 10
python -m bot rules set reply.max_length 500
```

El valor se interpreta solo:

| Escribís | Se guarda |
|---|---|
| `true` / `si` / `yes` / `on` / `1` | `true` |
| `false` / `no` / `off` / `0` | `false` |
| `12` | número entero |
| `2.5` | decimal |
| `hola` | texto |
| `a, b, c` | lista de textos |

## Consultar una regla

```bash
python -m bot rules get games.interactive
```

## Restaurar los defaults

```bash
python -m bot rules reset
```

## Editar el archivo a mano

```bash
python -m bot rules edit
```

Imprime la ruta del `rules.toml` para abrirlo con tu editor. Recordá reiniciar
el bot después de cambiarlo.

---

## Todas las reglas

### `[general]`

| Regla | Default | Qué hace |
|---|---|---|
| `allow_offtopic` | `true` | Permite mensajes de otros temas (los que no se clasifican). Con `false` el bot sólo responde a comandos, preguntas, juegos y QA. |
| `allow_unknown` | `true` | Responde aunque no entienda el mensaje (va al fallback / a la IA). |
| `ignore_bots` | `true` | Ignora mensajes enviados por otros bots. |
| `always_reply` | `false` | Responde siempre, sin importar el tema. |
| `require_mention` | `false` | En grupos, sólo responde si lo mencionan (`@mi_bot`). |
| `log_level` | `INFO` | Nivel de log: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

### `[reply]`

| Regla | Default | Qué hace |
|---|---|---|
| `max_length` | `900` | Largo máximo de la respuesta (se recorta). |
| `quote` | `true` | Citar el mensaje original al responder. |
| `parse_mode` | `Markdown` | `Markdown`, `HTML` o vacío. |

### `[games]`

| Regla | Default | Qué hace |
|---|---|---|
| `interactive` | `true` | **Deja el juego interactivo**: mientras hay partida activa, los mensajes del chat se interpretan como respuesta del juego en vez de pasar por el router. |
| `allow_commands_during_game` | `true` | Permite ejecutar comandos (`/trivia`, `/help`) durante una partida. Con `false`, durante la partida sólo se atienden respuestas del juego. |
| `timeout_seconds` | `600` | Segundos de inactividad antes de dar la partida por terminada. `0` = sin timeout. |
| `allow_skip` | `true` | Permite abandonar la partida con `/salir`. |
| `rounds` | `5` | Rondas por partida de trivia. |

### `[qa]`

| Regla | Default | Qué hace |
|---|---|---|
| `enabled` | `true` | Activa el módulo de preguntas y respuestas. |
| `fallback_to_ai` | `true` | Si la pregunta no está en el banco local, deriva a la IA. |
| `min_confidence` | `0.34` | Confianza mínima para aceptar una búsqueda local. |
| `allow_questions` | `true` | Acepta preguntas libres (no sólo `/preguntas`). |

### `[ai]`

| Regla | Default | Qué hace |
|---|---|---|
| `enabled` | `false` | Activa el uso de IA. |
| `allow_without_command` | `true` | Usa IA aunque el mensaje no sea pregunta (temas libres). |
| `send_history` | `true` | Envía el historial del chat como contexto. `false` = cada pregunta va sola. |
| `warn_if_disabled` | `true` | Avisa cuando alguien pide IA pero está desactivada. |

### `[cleanup]`

| Regla | Default | Qué hace |
|---|---|---|
| `enabled` | `true` | Activa la limpieza automática. |
| `every_n_messages` | `25` | Evalúa la limpieza cada N mensajes recibidos. |
| `every_seconds` | `300` | Evalúa la limpieza cada N segundos (por si el chat queda quieto). |
| `delete_older_than_seconds` | `3600` | Antigüedad a partir de la cual se borra un mensaje. |
| `protect_commands` | `true` | No borra mensajes que sean comandos. |

### `[filters]`

| Regla | Default | Qué hace |
|---|---|---|
| `enabled` | `true` | Activa los filtros automáticos. |
| `delete_on_block` | `true` | Borra el mensaje bloqueado (`false` = sólo avisa en el log). |
| `mute_on_block` | `false` | Silencia al usuario que mandó el mensaje bloqueado. |
| `block_links` | `false` | Bloquea cualquier `http://` o `https://`. |
| `max_chars` | `2000` | Máximo de caracteres (0 = sin límite). Los mensajes más largos se recortan. |
| `spam.enabled` | `true` | Detecta spam. |
| `spam.max_repeats` | `6` | Máximo de caracteres repetidos seguidos (`aaaaaaa`). |
| `spam.duplicate_window` | `20` | Ventana en segundos para detectar duplicados. |
| `spam.max_duplicates` | `3` | Repetidos tolerados dentro de la ventana. |

Las listas (`blocked_words`, `muted_users`, `allowed_domains`) viven en
`config.toml` → sección `[filters]`, porque son datos y no comportamiento.

---

## Ejemplos

**Bot estricto que sólo responde a lo que le preguntan:**

```bash
python -m bot rules set general.allow_offtopic false
python -m bot rules set ai.allow_without_command false
python -m bot rules set general.require_mention true
```

**Juego totalmente bloqueante (durante la partida sólo el juego):**

```bash
python -m bot rules set games.allow_commands_during_game false
python -m bot rules set games.interactive true
```

**Limpieza agresiva:**

```bash
python -m bot rules set cleanup.every_n_messages 10
python -m bot rules set cleanup.every_seconds 60
python -m bot rules set cleanup.delete_older_than_seconds 300
```

**Sin IA y sin historial (privacidad):**

```bash
python -m bot rules set ai.enabled false
python -m bot rules set ai.send_history false
```

---

## Precedencia

1. `rules.toml` gana sobre `config.toml` para los campos que aparecen en
   ambos (filtros, limpieza, juegos).
2. `config.toml` gana sobre los defaults internos.
3. La clave maestra nunca está en ningún `.toml`.

Cambios: se aplican al reiniciar el bot.
