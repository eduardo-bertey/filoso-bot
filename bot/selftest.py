"""Autotest del bot — todo en un solo archivo.

    python -m bot selftest

Verifica: crypto, persist (formato FilosoIA), clasificador, filtros,
limpieza, juegos, banco de QA, proveedores, gestor de claves, topics y CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FALLOS: list[str] = []


def check(nombre: str, cond: bool, detalle: str = "") -> bool:
    if cond:
        print(f"  OK   {nombre}")
    else:
        print(f"  FAIL {nombre}" + (f"  ({detalle})" if detalle else ""))
        FALLOS.append(f"{nombre}: {detalle}" if detalle else nombre)
    return cond


def seccion(titulo: str) -> None:
    print(f"\n=== {titulo} ===")


def run() -> int:
    from bot import crypto
    from bot.core.cleanup import CleanupService
    from bot.core.filters import FilterPipeline
    from bot.core.state import StateStore
    from bot.models import Intent, Message
    from bot.modules import games
    from bot.modules.ai.keys import KeyManager, probe_key
    from bot.modules.ai.providers import PROVIDER_SPECS, ProviderConfig, ProviderRegistry
    from bot.modules.classifier import classify
    from bot.modules.qa import QABank
    from bot.persist import Persist
    from bot.rules import RuleSet
    from bot.settings import CONFIG_PATH, RULES_PATH, load_settings

    data_dir = BASE / "data"
    data_dir.mkdir(exist_ok=True)

    # ---------------- crypto ----------------
    seccion("Crypto")
    sec = {"telegram_token": "123:ABC", "api_keys": {"groq": ["gsk_x"]}}
    blob = crypto.encrypt_payload(sec, "clave")
    check("roundtrip con password", crypto.decrypt_payload(blob, "clave") == sec)
    blob_plano = crypto.encrypt_payload(sec, None)
    check("roundtrip sin password", crypto.decrypt_payload(blob_plano, None) == sec)
    check("cipher disponible", crypto.available_cipher() in ("aes-256-gcm", "chacha20-hmac-sha256"))
    check("kdf disponible", crypto.available_kdf() in ("argon2id", "pbkdf2"))

    # ---------------- persist (formato FilosoIA) ----------------
    seccion("Persist (FilosoIA)")
    tmp = data_dir / "_selftest_persist.json"
    if tmp.exists():
        tmp.unlink()
    p = Persist(tmp, None)
    p.telegram_token = "123:ABC"
    p.add_key("groq", "k1")
    p.add_key("groq", "k2")
    p.set_default_key("groq", 1)
    check("default = k2", p.get_key("groq") == "k2")
    check("rotate -> k1", p.rotate_key("groq") == "k1")
    p.mark_error("groq", "HTTP 500")
    check("cooldown activo", p.is_on_cooldown("groq"))
    check("cooldown ~300s", 290 <= p.cooldown_remaining("groq") <= 300, str(p.cooldown_remaining("groq")))
    p.mark_healthy("groq")
    check("mark_healthy saca cooldown", not p.is_on_cooldown("groq"))
    p.track_call("groq")
    p.track_tokens("groq", 100, 50)
    check("calls/h", p.calls_in_window("groq") == 1)
    check("tokens", p.get_token_usage("groq") == (100, 50))
    p.block_model("groq", "malo")
    check("modelo bloqueado", p.is_model_blocked("groq", "malo"))
    p.unblock_model("groq", "malo")
    check("modelo desbloqueado", not p.is_model_blocked("groq", "malo"))
    p.add_custom_model("groq", "mio")
    check("modelo propio", p.get_custom_models("groq") == ["mio"])
    # sin password el archivo va plano (igual que FilosoIA sin password)
    check("archivo plano sin password", "providers" in json.loads(tmp.read_text(encoding="utf-8")))
    tmp_c = data_dir / "_selftest_persist_c.json"
    pc = Persist(tmp_c, "clave")
    pc.add_key("groq", "k1")
    check("archivo cifrado con password",
          json.loads(tmp_c.read_text(encoding="utf-8")).get("encrypted") is True)
    # y se puede volver a leer
    pc2 = Persist(tmp_c, "clave")
    check("relectura cifrada", pc2.keys("groq") == ["k1"])
    tmp_c.unlink()

    # ---------------- gestor de claves ----------------
    seccion("Gestor de claves")
    km = KeyManager(p)
    check("3 claves", km.count("groq") == 2, str(km.count("groq")))
    check("clasifica 401 -> auth", km.classify_error("HTTP 401: invalid api key") == "auth")
    check("clasifica 429 -> rate", km.classify_error("429 too many requests") == "rate")
    check("clasifica 503 -> server", km.classify_error("HTTP 503") == "server")
    antes = km.default_key("groq")
    kind = km.mark_failure("groq", "HTTP 401 invalid api key", antes)
    check("401 rota la key", kind == "auth" and km.default_key("groq") != antes)
    km.mark_failure("groq", "429 too many requests", km.default_key("groq"))
    check("429 no quema la key", not km.is_provider_cooled("groq"))
    km.mark_failure("groq", "HTTP 502", km.default_key("groq"))
    check("502 -> cooldown", km.is_provider_cooled("groq"))
    km.clear_provider_cooldown("groq")
    km.block("groq", 0)
    check("usable_keys salta bloqueada", len(km.usable_keys("groq")) == 1, str(km.usable_keys("groq")))
    check("key enmascarada", "*" in km.entries("groq")[0].masked())

    # ---------------- proveedores ----------------
    seccion("Proveedores")
    reg = ProviderRegistry(
        configs=[ProviderConfig(id="groq", enabled=True, priority=10),
                 ProviderConfig(id="openai", enabled=True, priority=20)],
        key_manager=km,
    )
    check("groq disponible", "groq" in reg.available_providers())
    check("openai sin key no disponible", "openai" not in reg.available_providers())
    check("13 proveedores (sin ollama)", "ollama" not in PROVIDER_SPECS, f"{len(PROVIDER_SPECS)} specs")
    for pid, spec in PROVIDER_SPECS.items():
        cfg = ProviderConfig(id=pid, style=spec.style, base_url=spec.base_url,
                             chat_path=spec.chat_path, models=list(spec.default_models))
        if not endpoint_ok(cfg, spec):
            check(f"endpoint {pid}", False)
            break
    else:
        check("endpoints de todos los proveedores", True)

    # ---------------- clasificador ----------------
    seccion("Clasificador")
    casos = [
        ("/ask que es un LLM", Intent.REQUEST),
        ("/request resumime", Intent.REQUEST),
        ("/help", Intent.COMMAND),
        ("/trivia", Intent.QA),
        ("/info", Intent.INFO),
        ("/jugar trivia", Intent.GAME),
        ("hola", Intent.SMALLTALK),
        ("¿que es python?", Intent.QUESTION),
        ("piedra papel tijera", Intent.GAME),
    ]
    for texto, esperado in casos:
        c = classify(Message(message_id=1, chat_id=1, text=texto, user_id=1))
        check(f"classify {texto!r}", c.intent == esperado, f"-> {c.intent}")

    # ---------------- filtros ----------------
    seccion("Filtros")
    fp = FilterPipeline({
        "enabled": True, "blocked_words": ["spoiler"], "delete_on_block": True,
        "muted_users": [999], "allowed_domains": ["ejemplo.com"], "block_links": False,
        "max_chars": 50,
        "spam": {"enabled": True, "max_repeats": 6, "duplicate_window": 20, "max_duplicates": 3},
    })
    v = fp.check(Message(message_id=1, chat_id=1, text="es un SPOILER", user_id=1))
    check("bloquea palabra", not v.allowed and v.action == "delete")
    v = fp.check(Message(message_id=1, chat_id=1, text="palabra " * 30, user_id=1))
    check("recorta largo", bool(v.modified_text))
    v = fp.check(Message(message_id=1, chat_id=1, text="hola", user_id=999))
    check("silencia usuario", not v.allowed)
    v = fp.check(Message(message_id=1, chat_id=1, text="mira ejemplo.com/x", user_id=1))
    check("deja pasar dominio permitido", v.allowed)

    # ---------------- limpieza ----------------
    seccion("Limpieza")
    cs = CleanupService(enabled=True, every_n_messages=5, every_seconds=60,
                        delete_older_than=100, protect_commands=True)
    cs.track(10, 111, True, "viejo")
    cs._tracked[10][0].created = time.time() - 500
    check("borra vencido", cs.purge_expired(10, lambda c, m: True) == 1)
    for i in range(5):
        cs.register_incoming(Message(message_id=i, chat_id=1, text="hola", user_id=1))
    check("dispara cada N mensajes", cs.due_by_messages())

    # ---------------- juegos ----------------
    seccion("Juegos")
    st = StateStore(data_dir / "_selftest_state.json")
    out = games.trivia_start(st, 42, 3)
    check("trivia arranca", "Trivia" in out.text)
    resp = games.trivia_answer(st, 42, "a")
    check("trivia responde", resp is not None)
    check("rps", "Empate" in games.rps_play("piedra").text or "Ganaste" in games.rps_play("piedra").text
          or "Perdiste" in games.rps_play("piedra").text)
    check("dados", "dado" in games.dice_roll(["3", "6"]).text)
    check("sin juego 'adivinar'", not hasattr(games, "guess_start"))

    # ---------------- QA ----------------
    seccion("Preguntas y respuestas")
    bank = QABank(BASE / "data" / "qa.json")
    check("banco con items", len(bank.items) > 5, str(len(bank.items)))
    res = bank.search("que es un transformer")
    check("busqueda local", bool(res))

    # ---------------- topics ----------------
    seccion("Topics")
    from bot.adapters.telegram import TelegramClient
    client = TelegramClient(token="123:ABC")
    upd = {"update_id": 1, "message": {
        "message_id": 55, "date": 1700000000,
        "chat": {"id": -1001234567890, "type": "supergroup"},
        "from": {"id": 42, "username": "juan"},
        "message_thread_id": 777, "text": "hola"}}
    msg = client.parse_update(upd)
    check("lee message_thread_id", msg.thread_id == 777, str(msg.thread_id))
    check("session_key con topic", msg.session_key == "-1001234567890:777")
    upd2 = json.loads(json.dumps(upd))
    upd2["message"].pop("message_thread_id")
    check("sin topic -> None", client.parse_update(upd2).thread_id is None)
    st.end_session(-1001234567890, 777)
    games.trivia_start(st, -1001234567890, 3, thread_id=777)
    check("partida por tema", st.get_session(-1001234567890, 777).active)
    check("otro tema sin partida", not st.get_session(-1001234567890, 888).active)

    # ---------------- reglas ----------------
    seccion("Reglas")
    rules = RuleSet.load(RULES_PATH)
    check("allow_offtopic por defecto", rules.get("general.allow_offtopic") is True)
    check("games.interactive por defecto", rules.get("games.interactive") is True)
    check("topics.reply_in_thread", rules.get("topics.reply_in_thread") is True)
    check("no existe max_tries (juego eliminado)", "games.max_tries" not in rules.known_paths())
    check("sin juego adivinar en reglas", not any("adivina" in r for r in rules.known_paths()))

    # ---------------- settings ----------------
    seccion("Settings")
    s = load_settings(None)
    check("providers por defecto", len(s.provider_configs()) >= 8)
    check("regla accesible", s.rule("general.allow_offtopic") is True)

    # ---------------- CLI ----------------
    seccion("CLI")
    from bot.cli import build_parser
    p = build_parser()
    subs = [a for a in p._actions if hasattr(a, "choices") and a.choices]
    nombres = sorted(subs[0].choices.keys())
    check("subcomandos", "rules" in nombres and "keys" in nombres and "errors" in nombres, str(nombres))
    for args in (["rules", "list"], ["status"], ["errors"], ["keys", "list"], ["usage"]):
        r = subprocess.run([sys.executable, "-m", "bot", *args], cwd=str(BASE),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(f"cli {' '.join(args)}", r.returncode == 0, r.stderr[-120:])

    # ---------------- limpieza de temporales ----------------
    for f in ("_selftest_persist.json", "_selftest_state.json"):
        pth = data_dir / f
        if pth.exists():
            pth.unlink()
    if tmp.exists():
        tmp.unlink()

    print("\n" + "=" * 60)
    if FALLOS:
        print(f"FALLOS ({len(FALLOS)}):")
        for f in FALLOS:
            print("  -", f)
        return 1
    print("✅ TODO OK")
    return 0


def endpoint_ok(cfg: ProviderConfig, spec) -> bool:
    from bot.modules.ai.providers import endpoint_for

    return endpoint_for(cfg, (spec.default_models or ["m"])[0]).startswith("http")


if __name__ == "__main__":
    sys.exit(run())
