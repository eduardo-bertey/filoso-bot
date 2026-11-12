"""Cifrado de credenciales.

Referencia: FilosoIA (Rust) usa AES-256-GCM + Argon2 con salt fijo.

Acá mantenemos el mismo concepto pero en Python y SIN dependencias
obligatorias:

  * KDF: Argon2id si está `argon2` instalado; si no, PBKDF2-HMAC-SHA256.
  * Cifrado: AES-256-GCM si está `cryptography` instalado; si no,
    ChaCha20 + HMAC-SHA256 (encrypt-then-MAC) en Python puro.
  * Sin clave maestra -> texto plano (con aviso), igual que FilosoIA.

Formato del archivo de credenciales (JSON):

    {
      "version": 1,
      "kdf": "argon2id" | "pbkdf2",
      "salt": "<base64>",
      "cipher": "aes-256-gcm" | "chacha20-hmac-sha256" | "none",
      "data": "<base64>"
    }
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import secrets
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

VERSION = 1
ARGON2_SALT_INFO = b"filoso-bot-credentials-salt-v1"


# --------------------------------------------------------------------------
# Instalacion automatica de las dependencias opcionales
# --------------------------------------------------------------------------
_AUTO_INSTALLED: set = set()

DEPENDENCIES = {
    "cryptography": "cryptography>=42.0",
    "argon2": "argon2-cffi>=23.1",
}


def ensure_dep(module: str) -> bool:
    """Importa `module`; si no esta, la instala sola con pip.

    Devuelve True si el modulo queda disponible.
    """
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        pass

    if module in _AUTO_INSTALLED:
        return False
    _AUTO_INSTALLED.add(module)

    paquete = DEPENDENCIES.get(module, module)
    print(f"[crypto] falta {module}: instalando {paquete}...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", paquete],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        importlib.invalidate_caches()
        importlib.import_module(module)
        print(f"[crypto] {paquete} instalada")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[crypto] no se pudo instalar {paquete}: {exc}")
        return False


# --------------------------------------------------------------------------
# Instalacion automatica de las dependencias opcionales
# --------------------------------------------------------------------------
_AUTO_INSTALLED: set = set()

DEPENDENCIES = {
    "cryptography": "cryptography>=42.0",
    "argon2": "argon2-cffi>=23.1",
}


def ensure_dep(module: str) -> bool:
    """Importa `module`; si no esta, la instala sola con pip.

    Devuelve True si el modulo queda disponible.
    """
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        pass

    if module in _AUTO_INSTALLED:
        return False
    _AUTO_INSTALLED.add(module)

    paquete = DEPENDENCIES.get(module, module)
    print(f"[crypto] falta {module}: instalando {paquete}...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", paquete],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        importlib.invalidate_caches()
        importlib.import_module(module)
        print(f"[crypto] {paquete} instalada")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[crypto] no se pudo instalar {paquete}: {exc}")
        return False

# --- compatibilidad con el config.json de FilosoIA (Rust) ---
# FilosoIA (src/persist.rs) deriva la clave asi:
#     Argon2::default().hash_password_into(password, SALT=b"opencode-chat-salt-v1")
#     AES-256-GCM, nonce de 12 bytes pegado al ciphertext, sin AAD
# Con los mismos parametros, el bot abre ese archivo y FilosoIA abre el del bot.
FILOSOIA_SALT = b"opencode-chat-salt-v1"
FILOSOIA_TIME_COST = 2
FILOSOIA_MEMORY_COST = 19456
FILOSOIA_PARALLELISM = 1


def _derive_argon2_filosofia(password: str) -> bytes:
    if not ensure_dep("argon2"):
        raise RuntimeError("argon2 no disponible (pip install argon2-cffi)")
    from argon2.low_level import Type, hash_secret_raw

    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=FILOSOIA_SALT,
        time_cost=FILOSOIA_TIME_COST,
        memory_cost=FILOSOIA_MEMORY_COST,
        parallelism=FILOSOIA_PARALLELISM,
        hash_len=32,
        type=Type.ID,
    )


def encrypt_filosofia(payload: Dict[str, Any], password: str) -> str:
    """Cifra igual que FilosoIA: base64(nonce || ciphertext AES-256-GCM)."""
    if not ensure_dep("cryptography"):
        raise RuntimeError("cryptography no disponible (pip install cryptography)")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_argon2_filosofia(password)
    nonce = secrets.token_bytes(12)
    plain = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, plain, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_filosofia(data_b64: str, password: str) -> Dict[str, Any]:
    """Lee un config.json cifrado por FilosoIA."""
    if not ensure_dep("cryptography"):
        raise RuntimeError("cryptography no disponible (pip install cryptography)")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _derive_argon2_filosofia(password)
    raw = base64.b64decode(data_b64)
    if len(raw) < 13:
        raise ValueError("archivo cifrado invalido")
    plain = AESGCM(key).decrypt(raw[:12], raw[12:], None)
    return json.loads(plain.decode("utf-8"))


# --------------------------------------------------------------------------
# Derivación de clave
# --------------------------------------------------------------------------
def _derive_argon2(password: str, salt: bytes) -> bytes:
    if not ensure_dep("argon2"):
        raise RuntimeError("argon2 no disponible (pip install argon2-cffi)")
    try:
        from argon2.low_level import hash_secret_raw, Type

        return hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=ARGON2_SALT_INFO + salt,
            time_cost=3,
            memory_cost=64 * 1024,
            parallelism=4,
            hash_len=32,
            type=Type.ID,
        )
    except Exception:
        raise RuntimeError("argon2 no disponible")


def _derive_pbkdf2(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 200_000, dklen=32
    )


def derive_key(password: str, salt: bytes, kdf: str) -> bytes:
    if kdf == "argon2id":
        return _derive_argon2(password, salt)
    if kdf == "pbkdf2":
        return _derive_pbkdf2(password, salt)
    raise ValueError(f"KDF desconocido: {kdf}")


def available_kdf() -> str:
    if ensure_dep("argon2"):
        return "argon2id"
    return "pbkdf2"


def available_cipher() -> str:
    if ensure_dep("cryptography"):
        return "aes-256-gcm"
    return "chacha20-hmac-sha256"


# --------------------------------------------------------------------------
# ChaCha20 en Python puro (RFC 8439) + HMAC-SHA256
# --------------------------------------------------------------------------
def _rotl32(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    state += list(struct.unpack("<8I", key))
    state += [counter & 0xFFFFFFFF]
    state += list(struct.unpack("<3I", nonce))
    x = list(state)

    def quarter(a: int, b: int, c: int, d: int) -> None:
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF
        x[d] = _rotl32(x[d] ^ x[a], 16)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF
        x[b] = _rotl32(x[b] ^ x[c], 12)
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF
        x[d] = _rotl32(x[d] ^ x[a], 8)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF
        x[b] = _rotl32(x[b] ^ x[c], 7)

    for _ in range(10):
        quarter(0, 4, 8, 12)
        quarter(1, 5, 9, 13)
        quarter(2, 6, 10, 14)
        quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15)
        quarter(1, 6, 11, 12)
        quarter(2, 7, 8, 13)
        quarter(3, 4, 9, 14)

    out = b"".join(
        struct.pack("<I", (x[i] + state[i]) & 0xFFFFFFFF) for i in range(16)
    )
    return out


def _chacha20_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    counter = 0
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, counter, nonce)
        chunk = data[offset:offset + 64]
        out += bytes(a ^ b for a, b in zip(chunk, block))
        counter += 1
    return bytes(out)


def _aes_gcm_seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if not ensure_dep("cryptography"):
        raise RuntimeError("cryptography no disponible (pip install cryptography)")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad or None)


def _aes_gcm_open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if not ensure_dep("cryptography"):
        raise RuntimeError("cryptography no disponible (pip install cryptography)")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, ct = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ct, aad or None)


def _chacha_seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    enc_key = hmac.new(key, b"filoso-enc", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"filoso-mac", hashlib.sha256).digest()
    ct = _chacha20_xor(enc_key, nonce, plaintext)
    tag = hmac.new(mac_key, nonce + aad + ct, hashlib.sha256).digest()[:16]
    return nonce + ct + tag


def _chacha_open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    enc_key = hmac.new(key, b"filoso-enc", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"filoso-mac", hashlib.sha256).digest()
    nonce, ct, tag = blob[:12], blob[12:-16], blob[-16:]
    expected = hmac.new(mac_key, nonce + aad + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("integridad fallida: credenciales corruptas o clave incorrecta")
    return _chacha20_xor(enc_key, nonce, ct)


# --------------------------------------------------------------------------
# API de alto nivel
# --------------------------------------------------------------------------
def encrypt_payload(data: Dict[str, Any], password: Optional[str]) -> Dict[str, Any]:
    """Cifra un dict. Sin password -> texto plano (con aviso en el `cipher`)."""
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    if not password:
        return {
            "version": VERSION,
            "kdf": "none",
            "salt": "",
            "cipher": "none",
            "data": base64.b64encode(payload).decode("ascii"),
            "warning": "SIN CIFRAR: se guardó en texto plano",
        }

    salt = secrets.token_bytes(16)
    kdf = available_kdf()
    cipher = available_cipher()
    key = derive_key(password, salt, kdf)
    nonce = secrets.token_bytes(12)
    aad = f"filoso-bot|{VERSION}|{kdf}|{cipher}".encode("utf-8")

    if cipher == "aes-256-gcm":
        blob = _aes_gcm_seal(key, nonce, payload, aad)
    else:
        blob = _chacha_seal(key, nonce, payload, aad)

    return {
        "version": VERSION,
        "kdf": kdf,
        "salt": base64.b64encode(salt).decode("ascii"),
        "cipher": cipher,
        "data": base64.b64encode(blob).decode("ascii"),
    }


def decrypt_payload(blob: Dict[str, Any], password: Optional[str]) -> Dict[str, Any]:
    """Revierte `encrypt_payload`."""
    cipher = blob.get("cipher", "none")
    data_b64 = blob.get("data", "")

    if cipher == "none":
        return json.loads(base64.b64decode(data_b64).decode("utf-8"))

    if not password:
        raise RuntimeError(
            "Las credenciales están cifradas pero no se pasó clave maestra."
        )

    salt = base64.b64decode(blob["salt"])
    key = derive_key(password, salt, blob["kdf"])
    raw = base64.b64decode(data_b64)
    aad = f"filoso-bot|{blob.get('version', VERSION)}|{blob['kdf']}|{cipher}".encode(
        "utf-8"
    )

    if cipher == "aes-256-gcm":
        plain = _aes_gcm_open(key, raw, aad)
    elif cipher == "chacha20-hmac-sha256":
        plain = _chacha_open(key, raw, aad)
    else:
        raise ValueError(f"Cifrado desconocido: {cipher}")

    return json.loads(plain.decode("utf-8"))


def save_credentials(path: Path, data: Dict[str, Any], password: Optional[str]) -> None:
    """Guarda credenciales cifradas y protege el archivo lo mejor posible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = encrypt_payload(data, password)
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass  # Windows: se ignora


def load_credentials(path: Path, password: Optional[str]) -> Dict[str, Any]:
    if not path.exists():
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    return decrypt_payload(blob, password)


def mask_secret(value: str, keep: int = 4) -> str:
    """Oculta un secreto para mostrarlo por pantalla."""
    if not value:
        return "(vacio)"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)}"


def checksum(data: Dict[str, Any]) -> str:
    """Huella corta para detectar cambios de configuración."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


def _self_test() -> Tuple[bool, str]:  # pragma: no cover - util de desarrollo
    secret = {"telegram_token": "123456:ABC", "api_keys": {"openai": "sk-test"}}
    for kdf in ("pbkdf2",):
        for cipher in ("chacha20-hmac-sha256",):
            blob = encrypt_payload(secret, "clave-de-prueba")
            blob["kdf"], blob["cipher"] = kdf, cipher
            # forzamos el cifrado pedido re-cifrando con esa combinacion
            salt = base64.b64decode(blob["salt"])
            key = derive_key("clave-de-prueba", salt, kdf)
            payload = json.dumps(secret, ensure_ascii=False).encode("utf-8")
            nonce = secrets.token_bytes(12)
            aad = f"filoso-bot|{VERSION}|{kdf}|{cipher}".encode("utf-8")
            raw = (
                _chacha_seal(key, nonce, payload, aad)
                if cipher.startswith("chacha")
                else _aes_gcm_seal(key, nonce, payload, aad)
            )
            blob["data"] = base64.b64encode(raw).decode("ascii")
            back = decrypt_payload(blob, "clave-de-prueba")
            if back != secret:
                return False, f"fallo en {kdf}/{cipher}"
    return True, "ok"


if __name__ == "__main__":  # pragma: no cover
    ok, msg = _self_test()
    print(f"crypto self-test: {msg}")
    print(f"kdf disponible: {available_kdf()} | cifrador: {available_cipher()}")
