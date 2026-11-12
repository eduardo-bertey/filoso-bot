# FILOSOFÍA — clonada de [FilosoIA](https://github.com/emanuelbertey/FilosoIA)

Este documento es la traducción de la filosofía de FilosoIA al bot de Telegram.
Mismo criterio, mismo comportamiento, otro lenguaje (Rust → Python) y otro
contexto (terminal → Telegram).

---

## 1. Principio: nunca depender de una sola credencial

FilosoIA maneja **varias API keys por proveedor**, una de las cuales es la
*default*. Si esa falla, el sistema rota a la siguiente sin intervention del
usuario. Lo mismo hace FilosoBot:

```
python -m bot keys add groq gsk_AAAA
python -m bot keys add groq gsk_BBBB
python -m bot keys add groq gsk_CCCC
```

Desde ese momento, `groq` tiene 3 keys. La primera es la default. Si el
proveedor devuelve `401`, FilosoBot marca esa key como **bloqueada** y pasa a
la siguiente, en la misma llamada. El usuario no ve nada.

## 2. Clasificación del error (idéntica a `handle_error` de FilosoIA)

FilosoIA, en `src/ui.rs`:

```rust
pub fn handle_error(pname: &str, msg: &str) {
    if msg.contains("429 Too Many Requests") {
        println!("Rate limited. Will retry once...");
    } else if msg.contains("503") || msg.contains("502") || msg.contains("5") {
        persist().lock().unwrap().mark_error(pname, msg);   // cooldown
    } else if msg.contains("401") || msg.contains("403") || msg.contains("unauthorized") {
        persist().lock().unwrap().rotate_key(pname);          // rotar key
    } else {
        eprintln!("Error: {msg}");
    }
}
```

En FilosoBot, `bot/modules/ai/keys.py`:

```python
def classify_error(error: str) -> str:
    low = (error or "").lower()
    if any(m in low for m in AUTH_ERROR_MARKERS):   # 401 403 unauthorized forbidden permission
        return "auth"
    if any(m in low for m in RATE_MARKERS):         # 429 too many requests rate limit
        return "rate"
    if any(m in low for m in SERVER_MARKERS):       # 500 502 503 504 ...
        return "server"
    return "other"
```

| Tipo | Qué significa | Qué hace FilosoBot | Qué hace FilosoIA |
|---|---|---|---|
| `auth` | 401 / 403 / key inválida | marca la key como bloqueada y **rota a la siguiente** | `rotate_key()` |
| `rate` | 429 / rate limit | **no quema la key**; pasa al proveedor siguiente | reintenta una vez |
| `server` | 5xx | **cooldown del proveedor** (300 s) | `mark_error()` |
| `other` | cualquier otro | log y pasa al proveedor siguiente | log |

## 3. Cooldown

- **300 segundos** (constante `COOLDOWN_SECONDS`, igual que FilosoIA).
- Mientras el proveedor está en cooldown, `is_available()` devuelve `false`:
  el bot no lo intenta y pasa al siguiente.
- El cooldown se levanta solo cuando pasa el tiempo (`clear_provider_cooldown`).

## 4. Rate limit por hora

FilosoIA lleva la cuenta con `CALL_WINDOW_SECS = 3600`:

```rust
pub fn track_call(&mut self, provider: &str) {
    let state = self.states.entry(provider.to_string()).or_insert(...);
    if state.window_start.elapsed() > Duration::from_secs(CALL_WINDOW_SECS) {
        state.calls = 0;
        state.window_start = Instant::now();
    }
    state.calls += 1;
}
```

En FilosoBot: `KeyManager.track_call()` / `calls_in_window()`, misma ventana
de 1 hora. Se ve con:

```bash
python -m bot usage
```

## 5. Tracking de tokens

FilosoIA: `track_tokens(provider, prompt_tokens, completion_tokens)`.

En FilosoBot: `KeyManager.track_tokens()`, alimentado por el `usage` que
devuelve cada API. Se ve igual en `python -m bot usage`.

## 6. Modelos bloqueados y personalizados

FilosoIA tiene `/nofree` (bloquear modelo) y `/models add` (modelos propios).

En FilosoBot:

```bash
python -m bot config set providers.groq.models '["llama-3.3-70b-versatile"]'
```

y el gestor de claves lleva `block_model()` / `add_custom_model()` por
proveedor.

## 7. Cifrado de las credenciales

FilosoIA: `AES-256-GCM` + `Argon2` con salt fijo, password del usuario al
arrancar, y si se deja vacía → texto plano.

En FilosoBot es idéntico:

| | FilosoIA | FilosoBot |
|---|---|---|
| Cifrado | AES-256-GCM | AES-256-GCM (o ChaCha20+HMAC si no hay `cryptography`) |
| KDF | Argon2 | Argon2id (o PBKDF2-HMAC-SHA256) |
| Salt | fijo | aleatorio **por archivo** |
| Sin password | texto plano | texto plano (con aviso) |
| Archivo | `config.json` | `data/credentials.json` |

Las claves **nunca** se imprimen enteras: `python -m bot keys list` muestra
`gsk_****`.

## 8. Diferencias con FilosoIA

| | FilosoIA | FilosoBot | Por qué |
|---|---|---|---|
| Transacción | TUI (terminal) | Telegram | distinto canal |
| Sesiones | múltiples simultáneas | una por chat | Telegram |
| Tools | read/write/run/web search | no | no aplica |
| Function calling | sí | no (todavía) | opcional |
| Rotación de keys | al recibir 401/403 | al recibir 401/403 **+** proactiva (`keys test`, `scheduler`) | el bot no tiene a nadie mirando la pantalla |
| Vigilante | manual (`/config`) | automático (`scheduler`) | en Telegram no hay nadie para hacer `/config` |
| Modelos gratis | menú `/nofree` | `blocked_models` por proveedor | equivalente |

## 9. Lo que aporta FilosoBot y no estaba en FilosoIA

1. **Vigilante automático** (`scheduler`): cada N minutos prueba las claves
   contra el endpoint de modelos (no consume tokens), bloquea las muertas y
   rota.
2. **Clave por chat**: `/usar <proveedor>` fija proveedor/modelo sólo para ese
   chat, sin tocar el global.
3. **Sin IA el bot sigue vivo**: trivia, juegos y banco local de Q&A no
   dependen de ninguna key.
