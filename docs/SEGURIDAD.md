# Seguridad

## Modelo de amenazas

| Amenaza | Mitigación |
|---|---|
| API key filtrada | se guarda cifrada, nunca en `config.json`, nunca en el repo |
| Token de Telegram filtrado | idem, cifrado |
| Chat no autorizado usando el bot | `allowed_chats` (vacío = no responde a nadie) |
| Abuso por palabras | `blocked_words`, `muted_users` |
| Flood/spam | `spam.*`, `max_chars`, rate limit implícito por `poll_timeout` |
| Filtrar datos del grupo en logs | los logs no incluyen el texto completo de los mensajes |
| Manipular el archivo cifrado | AES-GCM / HMAC detectan la alteración y abortan |
| Quebrar la clave maestra | Argon2id (o PBKDF2 con 200k iteraciones) + salt por archivo |

---

## Cifrado de credenciales

Modelo igual al de [FilosoIA](https://github.com/emanuelbertey/FilosoIA):
**cifrado autenticado** (no sólo cifrado).

```
clave maestra
   └─ KDF (Argon2id o PBKDF2-HMAC-SHA256) + salt aleatorio por archivo
        └─ clave de 256 bits
             ├─ AES-256-GCM  (si hay `cryptography`)
             └─ ChaCha20 + HMAC-SHA256 (encrypt-then-MAC, Python puro)
                  └─ nonce aleatorio de 12 bytes + tag de 16 bytes
```

Detalles que importan:

- **Nonce aleatorio** por cifrado: nunca se repite.
- **Salt aleatorio** por archivo: dos archivos con la misma clave maestra
  producen claves distintas.
- **AAD** (datos autenticados) incluye versión + KDF + cifrador: si alguien
  cambia esos campos, el descifrado falla.
- **Tag de autenticación**: cualquier modificación del archivo cifrado
  produce un error, no basura silenciosa.
- La clave maestra **nunca** se guarda: sólo se deriva.

### Sin clave maestra

Si en `setup` dejás la clave vacía, las credenciales se guardan en texto plano
con un aviso explícito en el archivo:

```json
{ "version": 1, "cipher": "none", "warning": "SIN CIFRAR: se guardó en texto plano" }
```

No es lo recomendado. Si podés, usá clave maestra o la variable de entorno
`FILOSO_MASTER_PASSWORD`.

### Backups

`data/credentials.json` cifrado se puede respaldar tal cual. La clave maestra
**no**: si la perdés, no hay recuperación.

---

## Buenas prácticas

### Nunca

- No pongas la key en `config.json`.
- No subas `data/credentials.json` al repo (ya está en `.gitignore`).
- No imprimas la key en logs ni en respuestas del chat.
- No uses el token de Telegram en otro servicio.

### Sí

- Usá `allowed_chats` con lista explícita.
- Dejá `delete_on_block` en `true` para palabras prohibidas.
- Rotá las claves: el bot ya rota entre las que le des en el mismo proveedor.
- Configurá `admin_ids` para que sólo vos toques la configuración.
- Instalá `cryptography` y `argon2-cffi` si podés:

```bash
pip install cryptography argon2-cffi
```

### Permisos de archivo

`crypto.save_credentials()` hace `chmod 600` en Linux/macOS. En Windows los
permisos de archivo son distintos: si el equipo es compartido, guardá la
carpeta en un perfil de usuario con permisos restringidos.

---

## Uso de la IA

- El bot manda al proveedor: el prompt, el historial reciente del chat
  (últimos `context_messages`) y el `system_prompt`.
- **El historial es del chat**, se borra con `/limpiar`.
- Si el chat es un grupo, se manda igual: usá `allowed_chats` con criterio.
- `max_tokens` y `timeout` acotan el costo y el tiempo de cada llamada.
- El cooldown (300 s) evita martillar un provider caído.

Si no querés que el historial se mande, poné `context_messages: 0`.

---

## Logs

- Nivel configurable con `FILOSO_LOG_LEVEL` (default `INFO`).
- Los logs incluyen chat id, user id e intent, **no** el contenido del mensaje.
- En `DEBUG` se loguea más; no lo uses en producción sin revisar.
