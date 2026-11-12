# Gestor de API keys

Manejo de claves con la filosofía de [FilosoIA](https://github.com/emanuelbertey/FilosoIA):
varias keys por proveedor, rotación automática cuando una no responde,
cooldown ante caídas del servidor y cifrado en disco.

Ver [`FILOSOFIA.md`](FILOSOFIA.md) para el detalle de la equivalencia.

---

## Comandos

```bash
python -m bot keys list                    # estado de todas las keys
python -m bot keys add groq                # agregar (pide la clave)
python -m bot keys add groq gsk_AAAA       # agregar sin prompt
python -m bot keys remove groq 1           # quitar la #1
python -m bot keys default groq 2          # usar la #2 como principal
python -m bot keys rotate groq             # rotar a la siguiente
python -m bot keys test groq               # probar las claves por HTTP
python -m bot keys block groq 0            # bloquear una key a mano
python -m bot keys unblock groq 0          # desbloquearla
python -m bot usage                        # llamadas por hora y tokens
python -m bot scheduler                    # vigilante automático
```

## Listar

```
🔑 groq (Groq)
  [0] gsk_****            usos=12   fallos=0    DEFAULT
  [1] gsk_****            usos=3    fallos=1
  [2] gsk_****            usos=0    fallos=0    BLOQUEADA
        ultimo error: HTTP 401: invalid api key
  llamadas/h: 15 | tokens: 12480 in / 3312 out
```

Las claves **nunca** se muestran enteras.

## Agregar

```bash
python -m bot keys add groq
API key de groq: gsk_1234567890
✅ clave agregada a groq (#2)
   gsk_1234****
```

La clave se guarda **cifrada** en `data/credentials.json` (AES-256-GCM +
Argon2id, o ChaCha20 + PBKDF2 si no instalaste las dependencias opcionales).
Si el proveedor estaba en `config.toml` pero deshabilitado, se habilita solo.

## Probar

```bash
python -m bot keys test groq

🔍 groq
  [0] gsk_****   ✅ HTTP 200 (4821 bytes)
  [1] gsk_****   ❌ HTTP 401: {"error":{"message":"Invalid API key"}}
        tipo: auth
```

El probe pide `GET {base_url}/models` (o `/api/tags` en Ollama, `/models` en
Anthropic y Gemini). **No consume tokens**: sólo verifica que la key viva.

## Rotación automática

Cuando una llamada devuelve **401/403**:

1. La key se marca como fallida y **bloqueada**.
2. Se rota a la siguiente key del mismo proveedor.
3. Si no queda ninguna, se pasa al siguiente proveedor.
4. Si tampoco hay, el bot responde con un aviso y sigue andando.

Todo en la misma llamada: el usuario no ve el ciclo.

| Error | Acción |
|---|---|
| 401 / 403 / invalid key | bloquear la key + rotar |
| 429 / rate limit | no quemar la key; siguiente proveedor |
| 5xx | cooldown del proveedor (300 s) |
| red caída | siguiente proveedor |

## Cooldown

Un proveedor que devuelve 5xx queda **300 segundos** en cooldown. Durante ese
tiempo el bot no lo intenta. El cooldown se levanta solo y el vigilante lo
confirma con un log.

```bash
python -m bot providers     # muestra el cooldown restante
```

## Rate limit por hora

Cada proveedor tiene una ventana de 1 hora. Se cuentan las llamadas:

```bash
python -m bot usage

PROVEEDOR       CLAVES  LLAM/HORA  TOK IN     TOK OUT    COOLDOWN
groq            3       15         12480      3312       0s
openai          1       2          1400       300        0s
```

## Vigilante automático (`scheduler`)

```bash
python -m bot scheduler                 # cada 300 s, para siempre
python -m bot scheduler --interval 60  # cada minuto
python -m bot scheduler --once          # un solo chequeo
python -m bot scheduler --no-probe      # no probar por HTTP (sólo cooldowns)
```

Qué hace en cada ciclo:

1. Libera los cooldowns vencidos.
2. Prueba cada key habilitada contra el endpoint de modelos.
3. Las que dan 401/403: se bloquean y se rota a la siguiente.
4. Las que dan 5xx: cooldown del proveedor.
5. Guarda el estado.

El bot también levanta el vigilante solo al arrancar (`python -m bot run`).
Para desactivarlo: `python -m bot run --no-scheduler`.

Se puede regular desde las reglas:

```bash
python -m bot rules set ai.scheduler_interval 120
python -m bot rules set ai.scheduler_probe false
```

## Dónde se guardan

| Dato | Archivo | ¿Cifrado? |
|---|---|---|
| API keys | `data/credentials.json` | **sí** |
| Estado de salud, llamadas, tokens | `data/keys_state.json` | no (no tiene secretos) |

`data/keys_state.json` se puede borrar sin romper nada: se recrea.

## Cosas que no hace

- No manda la key a ningún lado salvo el proveedor correspondiente.
- No la imprime en logs ni en respuestas de Telegram.
- No la escribe en `config.toml`.
