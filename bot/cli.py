"""Bot de Telegram: aplicacion y CLI.

Uso:

    python -m bot setup      # configuracion interactiva (token, chats, IA)
    python -m bot run        # arranca el bot
    python -m bot rules      # muestra las reglas (rules.toml)
    python -m bot rules set <ruta> <valor>   # edita una regla
    python -m bot config     # muestra config.toml
    python -m bot providers  # estado de proveedores de IA
    python -m bot status     # estado general
    python -m bot send "hola" --chat 123
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adapters.telegram import TelegramClient, TelegramError
from .core.cleanup import CleanupService
from .core.filters import FilterPipeline
from .core.router import ModuleResult, Router
from .core.state import StateStore
from .models import Intent, Message, OutgoingMessage
from .modules import games
from .modules.ai import (
    AIService,
    KeyManager,
    PROVIDER_SPECS,
    ProviderRegistry,
    Scheduler,
    endpoint_for,
    probe_key,
    spec_for,
)
from .modules.classifier import Classification
from .modules.qa import QABank, format_qa
from .persist import Persist
from .rules import RuleSet
from .settings import (
    BASE_DIR,
    CONFIG_PATH,
    CREDENTIALS_PATH,
    DATA_DIR,
    DEFAULT_CONFIG,
    RULES_PATH,
    Settings,
    config_exists,
    credentials_encrypted,
    credentials_exist,
    env_master_password,
    load_settings,
    rules_exist,
    save_config,
    save_credentials,
    save_rules,
)
from .toml_io import parse_value

log = logging.getLogger("filoso")


# ======================================================================
# BOOT
# ======================================================================
class FilosoBot:
    """El bot. Orchestra adapters + modulos."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.config = settings.config
        self.rules = settings.rules

        tg = self.config.get("telegram", {})
        self.telegram = TelegramClient(
            token=settings.telegram_token,
            parse_mode=str(self.rules.get("reply.parse_mode", tg.get("parse_mode", "Markdown"))),
            poll_timeout=int(tg.get("poll_timeout", 30)),
        )

        self.state = StateStore(DATA_DIR / "state.json")
        self.filters = FilterPipeline(settings.filters)

        cleanup_cfg = settings.cleanup
        self.cleanup = CleanupService(
            enabled=bool(cleanup_cfg.get("enabled", True)),
            every_n_messages=int(cleanup_cfg.get("every_n_messages", 25)),
            every_seconds=int(cleanup_cfg.get("every_seconds", 300)),
            delete_older_than=int(cleanup_cfg.get("delete_older_than_seconds", 3600)),
            protect_commands=bool(cleanup_cfg.get("protect_commands", True)),
        )
        # janitor con IA: la IA decide que borrar (fallback al cleanup clasico)
        self.janitor = AIJanitor(
            enabled=bool(cleanup_cfg.get("ai_enabled", False)),
            batch_size=int(cleanup_cfg.get("ai_batch_size", 30)),
            every_n_messages=int(cleanup_cfg.get("ai_every_n_messages", 25)),
            every_seconds=int(cleanup_cfg.get("ai_every_seconds", 600)),
            delete_only_bot=bool(cleanup_cfg.get("ai_delete_only_bot", True)),
            delete_older_than=int(cleanup_cfg.get("delete_older_than_seconds", 3600)),
        )

        ai_cfg = self.config.get("ai", {})
        # gestor de claves (filosofia FilosoIA): una sola fuente de verdad
        self.persist = settings.persist
        self.key_manager = KeyManager(self.persist)
        self.registry = ProviderRegistry(
            configs=settings.provider_configs(),
            keys_by_provider={
                pid: settings.api_keys(pid) for pid in (self.config.get("providers", {}) or {})
            },
            cooldown_seconds=float(ai_cfg.get("cooldown_seconds", 300)),
            key_manager=self.key_manager,
        )
        self.ai = AIService(
            registry=self.registry,
            default_provider=ai_cfg.get("default_provider", "") or "",
            default_model=ai_cfg.get("default_model", "") or "",
            temperature=float(ai_cfg.get("temperature", 0.7)),
            max_tokens=int(ai_cfg.get("max_tokens", 900)),
            timeout=float(ai_cfg.get("timeout", 60)),
            system_prompt=ai_cfg.get("system_prompt", "") or "",
            history_size=int(ai_cfg.get("context_messages", 6)),
        )

        qa_cfg = self.config.get("qa", {})
        qa_file = BASE_DIR / qa_cfg.get("data_file", "data/qa.json")
        self.qa_bank = QABank(qa_file)

        self.router = Router()
        self._register_modules()
        self._running = False
        self.scheduler: Optional[Scheduler] = None

    # ------------------------------------------------------------------
    # reglas
    # ------------------------------------------------------------------
    def rule(self, path: str, default: Any = None) -> Any:
        return self.rules.get(path, default)

    # ------------------------------------------------------------------
    # registro de modulos
    # ------------------------------------------------------------------
    def _register_modules(self) -> None:
        r = self.router
        r.register(Intent.COMMAND, self._handle_command)
        r.register(Intent.INFO, self._handle_info)
        r.register(Intent.GAME, self._handle_game)
        r.register(Intent.QA, self._handle_qa)
        r.register(Intent.REQUEST, self._handle_ai)
        r.register(Intent.QUESTION, self._handle_ai)
        r.register(Intent.SMALLTALK, self._handle_smalltalk)
        r.set_fallback(self._handle_fallback)

    # ------------------------------------------------------------------
    # modulos
    # ------------------------------------------------------------------
    def _handle_command(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        cmd = cls.command
        args = cls.args

        if cmd in ("start", "help", "ayuda"):
            return OutgoingMessage(text=self._help_text())
        if cmd in ("info", "status"):
            return OutgoingMessage(text=self._info_text())
        if cmd in ("proveedores", "providers", "modelos"):
            return OutgoingMessage(text=self._providers_text())
        if cmd in ("reglas", "rules"):
            return OutgoingMessage(text=self._rules_text())
        if cmd in ("tema", "topic"):
            return OutgoingMessage(text=self._topic_text(msg))
        if cmd in ("topics", "temas"):
            return OutgoingMessage(text=self._topics_text())
        if cmd in ("allowed", "permitidos"):
            return self._allowed_text(msg, args)

        # ---- comandos clonados de FilosoIA ----
        if cmd == "keys":
            if not self.is_admin(msg):
                return OutgoingMessage(text="No podés ver las claves.")
            return OutgoingMessage(text=self._keys_text())
        if cmd == "config":
            if not self.is_admin(msg):
                return OutgoingMessage(text="No podés ver la configuración.")
            return OutgoingMessage(text=self._config_text())
        if cmd == "nofree":
            return self._block_model(msg, args, block=True)
        if cmd == "free":
            return self._block_model(msg, args, block=False)
        if cmd == "models":
            return OutgoingMessage(text=self._models_text())
        if cmd == "list":
            return OutgoingMessage(text=self._list_sessions_text())
        if cmd == "switch":
            return self._switch_session(msg, args)
        if cmd == "close":
            return self._close_session(msg, args)
        if cmd in ("menu",):
            return OutgoingMessage(text=self._providers_text())
        if cmd in ("quit",):
            self.state.end_session(msg.chat_id, msg.thread_id)
            return OutgoingMessage(text="👋 Chau.")
        if cmd in ("usar", "use"):
            if not args:
                return OutgoingMessage(text="Uso: `/usar <proveedor> [modelo]`")
            provider = args[0].lower()
            model = args[1] if len(args) > 1 else ""
            if self.registry.get(provider) is None:
                return OutgoingMessage(text=f"Proveedor desconocido: `{provider}`")
            self.ai.force_for_chat(msg.chat_id, provider, model)
            modelo = model or self.ai.model_for(provider)
            return OutgoingMessage(
                text=f"✅ Ahora uso *{provider}* / `{modelo}` en este chat.\n"
                f"(`/usar` sin argumentos vuelve al proveedor por defecto.)"
            )
        if cmd in ("limpiar", "clear"):
            self.state.clear_history(msg.chat_id, msg.thread_id)
            self.cleanup._tracked.pop(msg.chat_id, None)
            return OutgoingMessage(text="🧹 Historial de este chat borrado.")
        if cmd in ("filtros", "filters"):
            return OutgoingMessage(text=self._filters_text())
        if cmd in ("cleanup", "limpieza", "janitor"):
            return self._cleanup_text(msg)
        if cmd in ("trivia", "quiz", "preguntas"):
            rounds = int(self.rule("games.rounds", 5))
            return games.trivia_start(self.state, msg.chat_id, rounds, msg.thread_id)
        if cmd in ("rps", "piedrapapeltijera"):
            return OutgoingMessage(
                text="👊 Elegí *piedra*, *papel* o *tijera* (ej: `piedra`)."
            )
        if cmd in ("dado", "dados"):
            return games.dice_roll(args)
        if cmd in ("salir", "stop", "cancel"):
            self.state.end_session(msg.chat_id, msg.thread_id)
            return OutgoingMessage(text="👋 Partida terminada.")
        if cmd in ("stats",):
            return OutgoingMessage(text=self._info_text())

        # comandos de IA: /ask, /request, /ia, /ai, /preguntar
        if cmd in ("ask", "request", "ia", "ai", "preguntar", "prompt"):
            return self._handle_ai(msg, cls)

        return OutgoingMessage(
            text=f"No conozco el comando `/{cmd}`.\nMandá `/help` para ver la lista."
        )

    def _handle_info(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        low = (cls.text or "").lower()
        if "proveedor" in low or "provider" in low or "modelo" in low:
            return OutgoingMessage(text=self._providers_text())
        if "regla" in low or "rule" in low:
            return OutgoingMessage(text=self._rules_text())
        if "filtro" in low:
            return OutgoingMessage(text=self._filters_text())
        return OutgoingMessage(text=self._info_text())

    def _handle_game(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        low = (cls.text or "").lower()
        if "trivia" in low or "quiz" in low or "pregunta" in low:
            return games.trivia_start(self.state, msg.chat_id, int(self.rule("games.rounds", 5)), msg.thread_id)
        if "tijera" in low or "piedra" in low or "papel" in low or "rps" in low:
            return games.rps_play(low)
        if "dado" in low:
            return games.dice_roll([])
        return OutgoingMessage(
            text="Juegos: `/trivia`, `piedra|papel|tijera`, `/dado [n] [caras]`"
        )

    def _handle_qa(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        if not self.rule("qa.enabled", True):
            return None
        pregunta = (cls.text or "").strip()
        if not pregunta or pregunta.lower() in ("trivia", "quiz", "preguntas"):
            return games.trivia_start(self.state, msg.chat_id, int(self.rule("games.rounds", 5)), msg.thread_id)

        found = self.qa_bank.search(pregunta)
        if found:
            return OutgoingMessage(text=format_qa(found[0]))

        if self.rule("qa.fallback_to_ai", True) and self.ai.is_enabled():
            return self._handle_ai(msg, cls)

        return OutgoingMessage(
            text="No tengo esa respuesta en el banco local. Probá `/trivia` o "
            "preguntale a la IA con `/ask ...`."
        )

    def _handle_ai(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        if not self.rule("ai.enabled", False) and not self.config.get("ai", {}).get("enabled"):
            if self.rule("ai.warn_if_disabled", True):
                log.warning(
                    "piden IA pero está desactivada. Activar con: "
                    "python -m bot rules set ai.enabled true (y un proveedor en setup)"
                )
                return OutgoingMessage(text="La IA está desactivada en este momento.")
            return None

        prompt = (cls.text or "").strip()
        if not prompt:
            return OutgoingMessage(text="¿Me preguntás algo? Ej: `/ask qué es un LLM`")

        history = (
            self.state.history(msg.chat_id, thread_id=msg.thread_id)
            if self.rule("ai.send_history", True)
            else []
        )
        comp = self.ai.ask(prompt, chat_id=msg.chat_id, history=history)
        if not comp.ok:
            # el motivo tecnico va al log y al CLI, no al chat
            log.warning(
                "IA fallo (chat=%s provider=%s): %s | intentos=%s",
                msg.chat_id, comp.provider, comp.error, comp.attempts,
            )
            # la regla_reply.show_error_details SOLO aplica a Telegram.
            # en la CLI el motivo se muestra siempre (ver `python -m bot errors`).
            if self.rule("reply.show_error_details", False) and self.is_admin(msg):
                return OutgoingMessage(text=f"⚠️ No pude responder: {comp.error}")
            return OutgoingMessage(
                text="Ahora no puedo responder. Intentá de nuevo en un rato. 🙏"
            )

        self.state.push_history(msg.chat_id, "user", prompt, thread_id=msg.thread_id)
        self.state.push_history(msg.chat_id, "assistant", comp.text, thread_id=msg.thread_id)
        firma = f"\n\n_({comp.provider}/{comp.model})_"
        texto = (comp.text or "").strip() + firma
        max_len = int(self.rule("reply.max_length", 900))
        if max_len and len(texto) > max_len:
            texto = texto[: max_len - 1] + "…"
        return OutgoingMessage(text=texto)

    def _handle_smalltalk(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        low = (cls.text or "").lower()
        if "hola" in low or "buenas" in low or "hey" in low:
            return OutgoingMessage(
                text="¡Hola! 👋 Soy FilosoBot.\n"
                "Preguntame algo, jugá una trivia o escribí `/help`."
            )
        if "gracias" in low:
            return OutgoingMessage(text="¡De nada! 🙌")
        if "chau" in low or "adios" in low or "adiós" in low:
            return OutgoingMessage(text="¡Chau! 👋")
        return OutgoingMessage(text="¿En qué te puedo ayudar? Escribí `/help`.")

    def _handle_fallback(self, msg: Message, cls: Classification) -> Optional[OutgoingMessage]:
        # regla "allow_offtopic": mensajes de otros temas
        if not self.rule("general.allow_offtopic", True):
            return None
        if not self.rule("general.allow_unknown", True):
            return None
        if self.ai.is_enabled() and self.rule("ai.allow_without_command", True):
            return self._handle_ai(msg, cls)
        return OutgoingMessage(
            text="No entendí. Escribí `/help` para ver lo que sé hacer."
        )

    # ------------------------------------------------------------------
    # textos
    # ------------------------------------------------------------------
    def _help_text(self) -> str:
        return (
            "🤖 *FilosoBot*\n\n"
            "*Mensajes*\n"
            "`/ask <pregunta>` — pregunto a la IA\n"
            "`/preguntas` — trivia de preguntas\n"
            "`/info` — estado del bot\n"
            "`/proveedores` — proveedores de IA\n"
            "`/reglas` — reglas activas\n"
            "`/usar <proveedor> [modelo]` — cambio de modelo en este chat\n"
            "`/limpiar` — borro el historial del chat\n"
            "`/filtros` — filtros activos\n"
            "`/cleanup` — estado de la limpieza (con IA o clasica)\n"
            "*Juegos*\n"
            "`/trivia` — trivia\n"
            "`piedra|papel|tijera` — piedra papel tijera\n"
            "`/dado [n] [caras]` — tiro dados\n"
            "`/salir` — termino la partida\n\n"
            "Sin comandos también funciona: escribí una pregunta y te contesto."
        )

    def _info_text(self) -> str:
        ai_cfg = self.config.get("ai", {})
        disponibles = self.registry.available_providers()
        return (
            "ℹ️ *Estado de FilosoBot*\n"
            f"Proveedores disponibles: {', '.join(disponibles) if disponibles else 'ninguno'}\n"
            f"IA habilitada: {'sí' if ai_cfg.get('enabled') else 'no'}\n"
            f"Proveedor por defecto: {ai_cfg.get('default_provider') or '-'}\n"
            f"Filtros: {'activos' if self.settings.filters.get('enabled') else 'desactivados'}\n"
            f"Palabras bloqueadas: {len(self.settings.filters.get('blocked_words') or [])}\n"
            f"Temas permitidos (offtopic): {'sí' if self.rule('general.allow_offtopic', True) else 'no'}\n"
            f"Juego interactivo: {'sí' if self.rule('games.interactive', True) else 'no'}\n"
            f"Limpieza: cada {self.cleanup.every_n_messages} mensajes / "
            f"cada {self.cleanup.every_seconds}s, borra > {self.cleanup.delete_older_than}s\n"
            f"Mensajes recibidos: {self.cleanup._counter}"
        )

    def _providers_text(self) -> str:
        lineas = self.ai.status_lines()
        if not lineas:
            return "No hay proveedores configurados."
        return "🧠 *Proveedores*\n" + "\n".join(lineas)

    # ------------------------------------------------------------------
    # comandos clonados de FilosoIA
    # ------------------------------------------------------------------
    def _keys_text(self) -> str:
        """Clone de `/keys` de FilosoIA (las claves salen enmascaradas)."""
        lineas = ["🔑 *Claves por proveedor*"]
        for provider in self.key_manager.providers():
            lineas.append(f"\n*{provider}*")
            for st in self.key_manager.status(provider):
                flags = []
                if st["default"]:
                    flags.append("DEFAULT")
                if st["blocked"]:
                    flags.append("BLOQUEADA")
                if st["cooldown"]:
                    flags.append(f"cooldown {st['cooldown']}s")
                linea = f"  [{st['index']}] {st['masked']}  usos={st['uses']} fallos={st['failures']}"
                if flags:
                    linea += "  " + " ".join(flags)
                lineas.append(linea)
                if st["last_error"]:
                    lineas.append(f"      ultimo error: {st['last_error'][:70]}")
        if len(lineas) == 1:
            lineas.append("\n_(sin claves cargadas)_")
        return "\n".join(lineas)

    def _config_text(self) -> str:
        """Clone de `/config` de FilosoIA: estado por proveedor."""
        lineas = ["⚙️ *Configuración*"]
        for st in self.registry.status():
            estado = "ok" if st["available"] else ("cooldown" if st["cooldown_until"] > time.time() else "inactivo")
            pt, ct = self.key_manager.token_usage(st["id"])
            lineas.append(
                f"• *{st['label']}*: {estado} | keys: {st['keys']} "
                f"| bloqueados: {len(st['blocked_models'])} "
                f"| tokens: {pt}in/{ct}out"
            )
            if st["last_error"]:
                lineas.append(f"   error: {st['last_error'][:70]}")
        lineas.append("")
        lineas.append(f"IA: {'activada' if self.config.get('ai', {}).get('enabled') else 'desactivada'}"
                      f" | default: {self.config.get('ai', {}).get('default_provider') or '-'}")
        lineas.append(f"Reglas: {len(self.rules.known_paths())} | "
                      f"Limpieza: cada {self.cleanup.every_n_messages} msgs / {self.cleanup.every_seconds}s")
        return "\n".join(lineas)

    def _models_text(self) -> str:
        """Clone de `/models` de FilosoIA: modelos por proveedor."""
        lineas = ["🧠 *Modelos*"]
        for cfg in self.registry.configs():
            bloqueados = self.key_manager.blocked_models(cfg.id)
            libres = [m for m in cfg.models if m not in bloqueados and m not in cfg.blocked_models]
            lineas.append(f"\n*{cfg.label}* ({cfg.id})")
            lineas.append(f"  endpoint: {endpoint_for(cfg, (cfg.models or [''])[0])}")
            if libres:
                lineas.append("  modelos: " + ", ".join(f"`{m}`" for m in libres))
            else:
                lineas.append("  modelos: (ninguno)")
            if bloqueados:
                lineas.append("  bloqueados: " + ", ".join(bloqueados))
            custom = self.key_manager.custom_models(cfg.id)
            if custom:
                lineas.append("  propios: " + ", ".join(custom))
        return "\n".join(lineas)

    def _list_sessions_text(self) -> str:
        """Clone de `/list` de FilosoIA: sesiones (aquí = proveedores por chat)."""
        disponibles = [c for c in self.registry.configs() if c.enabled]
        lineas = ["🗂 *Sesiones (proveedores)*", "Usá `/switch <N>` para usar uno en este chat.", ""]
        for i, cfg in enumerate(disponibles, start=1):
            modelo = self.ai.model_for(cfg.id)
            estado = "disponible" if self.registry.is_available(cfg.id) else "no disponible"
            lineas.append(f"  [{i}] {cfg.label} (`{cfg.id}`) — {modelo} — {estado}")
        if not disponibles:
            lineas.append("  (no hay proveedores habilitados)")
        return "\n".join(lineas)

    def _block_model(self, msg: Message, args: List[str], block: bool) -> OutgoingMessage:
        """Clone de `/nofree` y `/free` de FilosoIA."""
        provider = self.registry.get(self.ai.default_provider)
        pid = provider.id if provider else ""
        modelo = args[0] if args else ""
        if not modelo:
            return OutgoingMessage(
                text="Uso: `/nofree <modelo>` (o `/free <modelo>`).\n"
                f"Proveedor actual: `{pid or '-'}`\n"
                "Modelos: " + (", ".join(f"`{m}`" for m in (provider.models if provider else [])) or "-")
            )
        if not pid:
            return OutgoingMessage(text="No hay proveedor configurado.")
        if block:
            self.key_manager.block_model(pid, modelo)
            self.key_manager.save()
            return OutgoingMessage(text=f"🚫 Modelo `{modelo}` bloqueado en `{pid}`.")
        self.key_manager.unblock_model(pid, modelo)
        self.key_manager.save()
        return OutgoingMessage(text=f"✅ Modelo `{modelo}` desbloqueado en `{pid}`.")

    def _switch_session(self, msg: Message, args: List[str]) -> OutgoingMessage:
        """Clone de `/switch <N>` de FilosoIA."""
        if not args:
            return OutgoingMessage(text=self._list_sessions_text())
        try:
            n = int(args[0])
        except ValueError:
            return OutgoingMessage(text="Uso: `/switch <N>` (N es el número de la sesión).")
        disponibles = [c for c in self.registry.configs() if c.enabled]
        if n < 1 or n > len(disponibles):
            return OutgoingMessage(text=f"Número inválido. Hay {len(disponibles)} sesiones.")
        cfg = disponibles[n - 1]
        modelo = self.ai.model_for(cfg.id)
        self.ai.force_for_chat(msg.chat_id, cfg.id, modelo)
        return OutgoingMessage(
            text=f"🔄 Cambiado a *{cfg.label}* / `{modelo}` en este chat."
        )

    def _close_session(self, msg: Message, args: List[str]) -> OutgoingMessage:
        """Clone de `/close <N>` de FilosoIA."""
        self.ai.clear_for_chat(msg.chat_id)
        return OutgoingMessage(
            text="🔒 Sesión cerrada en este chat (vuelve el proveedor por defecto)."
        )

    def _topic_text(self, msg: Message) -> str:
        if msg.thread_id is None:
            return (
                "💬 Este chat no tiene temas (o no estamos en un supergrupo con temas).\n"
                f"Chat id: `{msg.chat_id}`\n"
                f"Tipo: `{msg.chat_type}`"
            )
        return (
            "🧵 *Tema actual*\n"
            f"Chat (supergrupo) id: `{msg.chat_id}`\n"
            f"Tema (thread) id: `{msg.thread_id}`\n"
            f"Tipo: `{msg.chat_type}`\n\n"
            "Las respuestas del bot van a este mismo tema, y cada tema tiene su "
            "propia partida y su propio historial."
        )

    def _topics_text(self) -> str:
        """Muestra los topics configurados y como cambiarlos."""
        chats = self.settings.allowed_chats
        threads = self.config.get("bot", {}).get("allowed_threads", []) or []
        lineas = ["🧵 *Topics*"]
        lineas.append(f"Chats permitidos: {', '.join(str(c) for c in chats) or 'ninguno'}")
        lineas.append(
            f"Topics permitidos: {', '.join(str(t) for t in threads) or 'todos'}"
        )
        lineas.append("")
        lineas.append(
            "Para cambiarlo: `python -m bot config set bot.allowed_threads '[\"12\",\"45\"]'`"
        )
        lineas.append("En un supergrupo con temas, mandá `/tema` y te digo el id del topic.")
        return "\n".join(lineas)

    def _allowed_text(self, msg: Message, args: List[str]) -> OutgoingMessage:
        """Gestiona los chats permitidos desde Telegram (solo admins)."""
        if not self.is_admin(msg):
            return OutgoingMessage(text="Solo los admins pueden cambiar los chats permitidos.")
        cfg = self.config.setdefault("bot", {})
        allowed = list(cfg.get("allowed_chats", []) or [])

        if not args:
            return OutgoingMessage(
                text="📋 *Chats permitidos*\n"
                + ("\n".join(f"  - `{c}`" for c in allowed) if allowed else "  (ninguno: responde en todos)")
                + "\n\nUso:\n"
                "  `/allowed add -1001234567890`\n"
                "  `/allowed remove -1001234567890`\n"
                "  `/allowed all` (responde en cualquier chat)"
            )

        accion = args[0].lower()
        if accion == "add" and len(args) >= 2:
            try:
                cid = int(args[1])
            except ValueError:
                return OutgoingMessage(text="El id tiene que ser un número.")
            if cid not in allowed:
                allowed.append(cid)
            cfg["allowed_chats"] = allowed
            cfg["allow_all_chats"] = False
            save_config(self.config)
            return OutgoingMessage(text=f"✅ Agregado `{cid}`.\nPermitidos: {', '.join(str(c) for c in allowed)}")
        if accion == "remove" and len(args) >= 2:
            try:
                cid = int(args[1])
            except ValueError:
                return OutgoingMessage(text="El id tiene que ser un número.")
            if cid in allowed:
                allowed.remove(cid)
            cfg["allowed_chats"] = allowed
            save_config(self.config)
            return OutgoingMessage(text=f"🗑 Quitado `{cid}`.\nPermitidos: {', '.join(str(c) for c in allowed) or 'ninguno'}")
        if accion == "all":
            cfg["allowed_chats"] = []
            cfg["allow_all_chats"] = True
            save_config(self.config)
            return OutgoingMessage(text="✅ Ahora responde en cualquier chat.")
        return OutgoingMessage(text="Uso: `/allowed` | `/allowed add <id>` | `/allowed remove <id>` | `/allowed all`")

    def _rules_text(self) -> str:
        lineas = ["🛠 *Reglas activas*"]
        for ruta, valor, desc in self.rules.flat():
            if not desc:
                continue
            lineas.append(f"• `{ruta}` = `{valor}` — {desc}")
        return "\n".join(lineas)

    def _cleanup_text(self, msg: Message) -> OutgoingMessage:
        stats = self.janitor.stats(msg.chat_id)
        return OutgoingMessage(
            text=(
                "🧹 *Limpieza*\n"
                f"Modo IA: {'activado' if stats['enabled'] else 'desactivado'}\n"
                f" tracked: {stats['tracked']} mensajes\n"
                f" cada {stats['every_n_messages']} mensajes / cada {stats['every_seconds']}s\n"
                f" lote: {stats['batch_size']} mensajes por pasada\n"
                f" borra solo mensajes propios: {'sí' if self.janitor.delete_only_bot else 'no'}"
            )
        )

    def _filters_text(self) -> str:
        f = self.settings.filters
        bloqueadas = f.get("blocked_words") or []
        return (
            "🛡 *Filtros*\n"
            f"Activos: {'sí' if f.get('enabled') else 'no'}\n"
            f"Palabras bloqueadas: {len(bloqueadas)}"
            + (f" ({', '.join(bloqueadas[:10])})" if bloqueadas else "")
            + f"\nUsuarios silenciados: {len(f.get('muted_users') or [])}"
            f"\nBloquear links: {'sí' if f.get('block_links') else 'no'}"
            f"\nMáx. caracteres: {f.get('max_chars')}"
        )

    # ------------------------------------------------------------------
    # ciclo
    # ------------------------------------------------------------------
    def chat_allowed(self, msg: Message) -> bool:
        bot_cfg = self.config.get("bot", {})
        if bot_cfg.get("allow_all_chats"):
            return True
        allowed = self.settings.allowed_chats
        if not allowed:
            return False
        return msg.chat_id in allowed

    def is_admin(self, msg: Message) -> bool:
        admins = self.settings.admin_ids
        return bool(admins) and msg.user_id in admins

    def _session_expired(self, session) -> bool:
        timeout = int(self.rule("games.timeout_seconds", 600))
        if timeout <= 0:
            return False
        return (time.time() - session.updated_at) > timeout

    def handle_message(self, msg: Message) -> Optional[OutgoingMessage]:
        """Procesa un mensaje: filtros -> reglas -> router -> respuesta."""
        self.cleanup.register_incoming(msg)

        verdict = self.filters.check(msg)
        if not verdict.allowed:
            log.info("mensaje bloqueado (%s): %s", verdict.action, verdict.reason)
            if verdict.action in ("delete", "mute") and msg.message_id:
                self.telegram.delete_message(msg.chat_id, msg.message_id)
            return None

        if not self.chat_allowed(msg):
            log.info("chat no permitido: %s", msg.chat_id)
            return None

        # topics: si hay lista de topics permitidos, se filtra
        allowed_threads = self.config.get("bot", {}).get("allowed_threads", []) or []
        if msg.thread_id is not None and allowed_threads:
            try:
                permitidos = {int(t) for t in allowed_threads}
            except (TypeError, ValueError):
                permitidos = set()
            if permitidos and msg.thread_id not in permitidos:
                log.info("topic %s no permitido", msg.thread_id)
                return None

        # reglas de temas (supergrupo con foros)
        if msg.thread_id is not None and not self.rule("topics.enabled", True):
            log.info("tema deshabilitado, no respondo en thread %s", msg.thread_id)
            return None

        # regla "require_mention": en grupos, sólo responde si lo nombran
        if self.rule("general.require_mention", False) and msg.chat_type in ("group", "supergroup"):
            mention = f"@{self.telegram.bot_username or ''}".lower()
            if mention not in msg.text.lower():
                return None

        # partida activa: el mensaje puede ser la respuesta del juego
        thread_para_sesion = msg.thread_id if self.rule("topics.per_topic_session", True) else None
        session = self.state.active_session(msg.chat_id, thread_para_sesion)
        if session:
            if self._session_expired(session):
                self.state.end_session(msg.chat_id)
                session = None
            elif self.rule("games.interactive", True):
                # "allow_commands_during_game": si el mensaje es un comando,
                # el comando gana; si no, va al juego.
                es_comando = msg.is_command
                if not (es_comando and not self.rule("games.allow_commands_during_game", True)):
                    if session.game == "trivia":
                        out = games.trivia_answer(self.state, msg.chat_id, msg.text, thread_para_sesion)
                        if out:
                            return out

        result = self.router.dispatch(msg)
        if result.error:
            log.warning("error interno (chat=%s): %s", msg.chat_id, result.error)
            # la regla_reply.show_error_details SOLO aplica a Telegram.
            # en la CLI el motivo se muestra siempre (ver `python -m bot errors`).
            if self.rule("reply.show_error_details", False) and self.is_admin(msg):
                return OutgoingMessage(text=f"⚠️ Error interno: {result.error[:200]}")
            return OutgoingMessage(text="Algo salió mal. Intentá de nuevo en un rato. 🙏")
        return result.output

    def run_once(self) -> int:
        """Un ciclo de polling. Devuelve cuantos mensajes procesó."""
        updates = self.telegram.get_updates()
        count = 0
        for update in updates:
            msg = self.telegram.parse_update(update)
            if msg is None:
                continue
            count += 1
            self.janitor.register_incoming(msg)
            try:
                out = self.handle_message(msg)
            except Exception:  # noqa: BLE001
                log.exception("error procesando mensaje")
                continue
            if out and out.text.strip():
                if out.thread_id is None and self.rule("topics.reply_in_thread", True):
                    out.thread_id = msg.thread_id
                sent = self.telegram.send(msg.chat_id, out)
                if sent:
                    self.cleanup.track(msg.chat_id, int(sent.get("message_id", 0)), True, out.text)
        return count

    def run_forever(self, scheduler: bool = True) -> None:
        self._running = True
        me = self.telegram.get_me()
        log.info("bot conectado como @%s", me.get("username"))
        print(f"✅ Bot en linea como @{me.get('username')}")
        print("   Ctrl+C para salir.\n")

        if scheduler:
            self.scheduler = Scheduler(
                manager=self.key_manager,
                provider_configs=self.registry.configs(),
                interval=float(self.rule("ai.scheduler_interval", 300)),
                probe=bool(self.rule("ai.scheduler_probe", True)),
            )
            self.scheduler.start()

        while self._running:
            try:
                self.run_once()
            except TelegramError as exc:
                log.warning("error de telegram: %s", exc)
                time.sleep(3)
            except KeyboardInterrupt:
                print("\n👋 Chau.")
                break

            if self.janitor.enabled and self.janitor.maybe_run():
                self._run_janitor_ai()
            elif self.cleanup.maybe_run():
                self._run_cleanup()

        if self.scheduler:
            self.scheduler.stop()

    def _run_cleanup(self) -> None:
        for chat_id in list(self.cleanup._tracked.keys()):
            self.cleanup.purge_expired(chat_id, self.telegram.delete_message)

    def _run_janitor_ai(self) -> None:
        for chat_id in list(self.janitor._tracked.keys()):
            try:
                n = self.janitor.run(chat_id, self.ai, self.telegram.delete_message)
                if n:
                    print(f"janitor: {n} mensaje(s) borrado(s) por IA en chat {chat_id}")
            except Exception:  # noqa: BLE001
                log.exception("janitor fallo en chat %s", chat_id)


# ======================================================================
# CLI
# ======================================================================
# dependencias opcionales: si faltan, se instalan solas
OPTIONAL_DEPS = [
    ("cryptography", "cryptography>=42.0"),   # AES-256-GCM (formato FilosoIA)
    ("argon2", "argon2-cffi>=23.1"),          # Argon2id (misma derivacion que FilosoIA)
]


def ensure_dependencies(auto_install: bool = True) -> List[str]:
    """Instala automaticamente las dependencias opcionales que falten.

    Devuelve la lista de modulos que siguen faltando.
    """
    import importlib

    faltan: List[str] = []
    for module, paquete in OPTIONAL_DEPS:
        try:
            importlib.import_module(module)
        except ImportError:
            faltan.append(module)

    if not faltan:
        return []

    if not auto_install:
        return faltan

    for module, paquete in OPTIONAL_DEPS:
        if module not in faltan:
            continue
        print(f"📦 Instalando {paquete} (dependencia opcional)...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", paquete],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            print(f"   ✅ {paquete} instalada")
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠️  No se pudo instalar {paquete}: {exc}")
            print(f"      Instalala a mano: pip install {paquete}")

    # verificar
    siguen: List[str] = []
    for module in faltan:
        try:
            importlib.import_module(module)
        except ImportError:
            siguen.append(module)
    if siguen:
        print(
            "   Sin esas deps el bot anda igual, pero no puede leer/escribir "
            "el config.json cifrado con el formato de FilosoIA."
        )
    return siguen


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or default


def _ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(f"{prompt}: ").strip()
    except EOFError:
        return ""


def _ask_yes(prompt: str, default: bool = False) -> bool:
    hint = "S/n" if default else "s/N"
    value = _ask(f"{prompt} ({hint})")
    if not value:
        return default
    return value.strip().lower() in ("s", "si", "sí", "y", "yes", "true", "1")


def _master_password(confirm: bool = True) -> Optional[str]:
    print("\n🔐 *Clave maestra* (cifra las credenciales guardadas).")
    print("   Dejala vacia si preferis guardar todo en texto plano (no recomendado).")
    pwd = _ask_secret("Clave maestra")
    if pwd and confirm:
        again = _ask_secret("Repetir clave maestra")
        if pwd != again:
            print("❌ Las claves no coinciden.")
            return None
    return pwd


def cmd_setup(args: argparse.Namespace) -> int:
    """Asistente de configuracion: va preguntando paso a paso."""
    print()
    print("=" * 62)
    print("  🤖  FilosoBot - asistente de configuracion")
    print("=" * 62)
    print("  Te voy a hacer unas preguntas. Si no sabés algo, dejalo")
    print("  vacio y lo configuramos despues.")
    print()

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        from .toml_io import load_toml

        config = load_toml(CONFIG_PATH)

    rules = RuleSet.load(RULES_PATH)

    print("-" * 62)
    print("PASO 1 de 4 - Token de Telegram")
    print("  Abri @BotFather en Telegram -> /newbot -> te da un token.")
    print("  Se ve asi: 123456789:AAH... (no lo pongas en el chat ni en el repo)")
    env_token = os.environ.get("FILOSO_TELEGRAM_TOKEN")
    env_file = os.environ.get("FILOSO_TELEGRAM_TOKEN_FILE")
    if env_token:
        token = env_token
        print(f"  ✅ Token leído de FILOSO_TELEGRAM_TOKEN ({crypto_mask(env_token)})")
    elif env_file:
        try:
            token = open(env_file, encoding="utf-8").read().strip()
            print(f"  ✅ Token leído de {env_file} ({crypto_mask(token)})")
        except OSError as exc:
            print(f"  ⚠️  no se pudo leer {env_file}: {exc}")
            token = _ask_secret("  Token del bot")
    else:
        print("  (opciones para no tipear:")
        print("     set FILOSO_TELEGRAM_TOKEN=123:ABC...")
        print("     set FILOSO_TELEGRAM_TOKEN_FILE=token.txt)")
        token = _ask_secret("  Token del bot")
    if not token:
        print("  ❌ Sin token no se puede arrancar. Aborto.")
        return 1
    print("  ✅ Token guardado.")

    print()
    print("-" * 62)
    print("PASO 2 de 4 - Chats donde responde el bot")
    print("  Id numerico del chat. Varios separados por coma.")
    print("  - supergrupo: empieza con -100 (ej: -1001234567890)")
    print("  - grupo/privado: numero positivo (ej: 123456789)")
    print("  Si lo deixas vacio, responde en cualquier chat.")
    chats_raw = _ask("  Chats permitidos")
    chats: List[int] = []
    for parte in chats_raw.replace(";", ",").split(","):
        parte = parte.strip()
        if parte:
            try:
                chats.append(int(parte))
            except ValueError:
                print(f"   ⚠️  '{parte}' no es un id valido, se ignora.")

    print("  (Opcional) Ids de los admins. Esos pueden ver motivos tecnicos.")
    admins_raw = _ask("  Admins")

    print("  (Opcional) Topics: ids de tema del supergrupo, separados por coma.")
    print("  Vacio = el bot responde en todos los temas de los chats que permitiste.")
    print("  Como los exemples: 12, 45  (son los numeros de los topics, no del grupo)")
    threads_raw = _ask("  Topics permitidos")
    threads: List[int] = []
    for parte in threads_raw.replace(";", ",").split(","):
        parte = parte.strip()
        if parte:
            try:
                threads.append(int(parte))
            except ValueError:
                print(f"   ⚠️  '{parte}' no es un id de tema, se ignora.")
    if threads:
        config.setdefault("bot", {})["allowed_threads"] = threads
        print(f"  ✅ Topics permitidos: {', '.join(str(t) for t in threads)}")
    else:
        config.setdefault("bot", {})["allowed_threads"] = []
        print("  ✅ Todos los topics.")
    admins: List[int] = []
    for parte in admins_raw.replace(";", ",").split(","):
        parte = parte.strip()
        if parte:
            try:
                admins.append(int(parte))
            except ValueError:
                pass

    config.setdefault("bot", {})["allowed_chats"] = chats
    config["bot"]["admin_ids"] = admins
    if not chats:
        config["bot"]["allow_all_chats"] = True
        print("   Sin chats cargados: el bot respondera en cualquier chat.")

    # ---------------- reglas ----------------
    print()
    print("-" * 62)
    print("PASO 3 de 4 - Reglas de conducta")
    print(f"  Archivo: {RULES_PATH}")
    print("  Ejemplos: permitir mensajes de otros temas, dejar el juego")
    print("  interactivo, cada cuanto borrar mensajes viejos...")
    print("  Las podés cambiar cuando quieras con: python -m bot rules set <ruta> <valor>")
    if _ask_yes("  ¿Querés ajustarlas ahora?", False):
        _edit_rules_interactive(rules)
    else:
        print("  ✅ Dejo los valores por defecto (los cambiás después con `bot rules`).")

    # ---------------- IA ----------------
    print()
    print("-" * 62)
    print("PASO 4 de 4 - IA (opcional)")
    print("  Sin IA el bot igual funciona: trivia, juegos y preguntas locales.")
    print("  Con IA, responde preguntas libres.")
    credentials: Dict[str, Any] = {}
    if CREDENTIALS_PATH.exists():
        print("  Ya hay credenciales guardadas; se conservan y se pueden reescribir.")
    add_ai = _ask_yes("  ¿Querés configurar un proveedor de IA ahora?", True)

    if add_ai:
        print("\n   Proveedores disponibles:")
        for pid, spec in PROVIDER_SPECS.items():
            modelos = ", ".join(spec.default_models[:2]) or "-"
            print(f"     - {pid:12s} {spec.label:24s} modelos: {modelos}")
        provider = _ask("Proveedor (id de la lista, o 'skip')").lower().strip()
        if provider and provider != "skip":
            spec = PROVIDER_SPECS.get(provider)
            if spec is None:
                print(f"   ⚠️  Proveedor desconocido: {provider}. Se omite la IA.")
            else:
                print(f"   Endpoint: {spec.base_url}{spec.chat_path}")
                print(f"   Key: {spec.key_url or '(no aplica)'}")
                key = ""
                if spec.style != "ollama":
                    print(f"  Podes cargar varias keys de {spec.label}: si una falla,")
                    print("  el bot prueba la siguiente solo.")
                    while True:
                        k = _ask_secret(f"  API key de {spec.label} (vacio para terminar)")
                        if not k:
                            break
                        key = k
                        credentials.setdefault("api_keys", {}).setdefault(provider, []).append(k)
                        if not _ask_yes("  ¿Cargar otra?", False):
                            break
                model = _ask("Modelo", spec.default_models[0] if spec.default_models else "")
                cfg = config.setdefault("providers", {}).setdefault(provider, {})
                cfg["enabled"] = True
                cfg["base_url"] = spec.base_url
                cfg["chat_path"] = spec.chat_path
                cfg["style"] = spec.style
                if model:
                    cfg["models"] = [model]
                config.setdefault("ai", {})["enabled"] = True
                config["ai"]["default_provider"] = provider
                config["ai"]["default_model"] = model
                rules.set("ai.enabled", True)
                credentials.setdefault("api_keys", {}).setdefault(provider, [])
                if key and key not in credentials["api_keys"][provider]:
                    credentials["api_keys"][provider].append(key)
                print(f"   ✅ {spec.label} habilitado.")

    # ---------------- guardar ----------------
    pwd = _master_password()
    if pwd is None:
        return 1

    save_config(config)
    save_rules(rules)
    save_credentials(credentials, pwd or None)
    # el token de telegram tambien vive en el persist cifrado
    persist = Persist(CREDENTIALS_PATH, pwd or None)
    persist.telegram_token = token
    persist.save()

    print()
    print("=" * 62)
    print("  ✅ LISTO")
    print("=" * 62)
    print(f"  Config       : {CONFIG_PATH}")
    print(f"  Reglas       : {RULES_PATH}")
    print(f"  Credenciales : {CREDENTIALS_PATH} ({'cifradas' if pwd else 'SIN CIFRAR'})")
    print(f"  Chats        : {', '.join(str(c) for c in chats) if chats else 'todos'}")
    print(f"  IA           : {config.get('ai', {}).get('default_provider') or 'desactivada'}")
    print()
    print("  Para arrancar el bot:")
    print("      python -m bot run")
    print()
    print("  Para ver estado / reglas / errores:")
    print("      python -m bot status")
    print("      python -m bot rules")
    print("      python -m bot errors")
    print("=" * 62)
    return 0


def _edit_rules_interactive(rules: RuleSet) -> None:
    """Editor simple de reglas por seccion."""
    print("\n   Secciones:")
    for sec in rules.sections():
        print(f"     - {sec}")
    while True:
        seccion = _ask("Seccion a editar (ej: general, games, filters)").strip()
        if not seccion or seccion.lower() in ("salir", "exit", "q", "/menu", "/quit"):
            print("  ↩ volviendo al panel")
            return
        if seccion.startswith("/"):
            print("  ↩ volviendo al panel")
            return
        if seccion not in rules.data:
            print(f"   ⚠️  Seccion desconocida: {seccion}")
            continue
        for ruta, valor, desc in rules.flat():
            if ruta.startswith(seccion + "."):
                print(f"     {ruta} = {valor}  ({desc})")
        ruta = _ask("Ruta a cambiar (ej: games.interactive)").strip()
        if not ruta or ruta.startswith("/"):
            print("  ↩ volviendo al panel")
            return
        valor = _ask("Nuevo valor")
        rules.set(ruta, parse_value(valor))
        print(f"   ✅ {ruta} = {rules.get(ruta)}")


def cmd_rules(args: argparse.Namespace) -> int:
    """Muestra y edita las reglas."""
    rules = RuleSet.load(RULES_PATH)

    if args.rules_command == "set":
        if len(args.path) < 2:
            print("Uso: python -m bot rules set <ruta> <valor>")
            print("Ej:  python -m bot rules set games.interactive false")
            return 1
        ruta = args.path[0]
        valor = parse_value(args.path[1])
        if ruta not in rules.known_paths() and not ruta.startswith(tuple(rules.sections())):
            print(f"⚠️  Ruta desconocida: {ruta}")
            print("Rutas disponibles:")
            for r in rules.known_paths():
                print("   ", r)
            return 1
        rules.set(ruta, valor)
        rules.save()
        print(f"✅ {ruta} = {rules.get(ruta)}  (guardado en {RULES_PATH})")
        return 0

    if args.rules_command == "get":
        if not args.path:
            print("Uso: python -m bot rules get <ruta>")
            return 1
        print(f"{args.path[0]} = {rules.get(args.path[0])}")
        return 0

    if args.rules_command == "reset":
        rules.reset()
        rules.save()
        print(f"✅ Reglas restauradas en {RULES_PATH}")
        return 0

    if args.rules_command == "edit":
        print(f"Editá el archivo: {RULES_PATH}")
        print("Después reiniciá el bot para que los cambios se apliquen.")
        return 0

    # default: listar
    print(f"📄 Reglas ({RULES_PATH})")
    for seccion in rules.sections():
        print(f"\n[{seccion}]")
        for ruta, valor, desc in rules.flat():
            if ruta.startswith(seccion + "."):
                print(f"  {ruta} = {valor}")
                if desc:
                    print(f"      → {desc}")
    print("\nEditar:  python -m bot rules set <ruta> <valor>")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Muestra la configuracion (sin secretos)."""
    config = load_toml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    if args.config_command == "set":
        if len(args.path) < 2:
            print("Uso: python -m bot config set <ruta> <valor>")
            return 1
        from .toml_io import save_toml

        node = config
        partes = args.path[0].split(".")
        for p in partes[:-1]:
            node = node.setdefault(p, {})
        node[partes[-1]] = parse_value(args.path[1])
        save_toml(CONFIG_PATH, config)
        print(f"✅ {args.path[0]} = {node[partes[-1]]}  (guardado en {CONFIG_PATH})")
        return 0

    print(f"📄 Config ({CONFIG_PATH})")
    print(json.dumps(config, ensure_ascii=False, indent=2))
    print("\nNota: las API keys NUNCA van acá (van cifradas en data/credentials.json).")
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    """Menu interactivo: todo se configura desde aca, sin argumentos."""
    from .toml_io import load_toml, save_toml

    print("FilosoBot (Ctrl+C para salir)")
    print("  /run /config /addkey /keys /api /models /nofree /free")
    print("  /topics /rules /status /errors /usage /selftest /help /quit")
    print()

    while True:
        op = _ask("Comando").strip()
        if not op:
            continue

        # ---- comandos con "/" (estilo FilosoIA) ----
        if op.startswith("/"):
            cmd = op[1:].split()[0].lower() if len(op) > 1 else ""
            resto = op[1:].split()[1:]
            if cmd in ("help", "h", "ayuda"):
                _menu_help()
                continue
            if cmd in ("quit", "q", "exit", "salir"):
                print("👋 Chau.")
                return 0
            if cmd in ("run", "start", "arrancar"):
                _menu_run(args)
                continue
            elif cmd in ("config", "cfg", "configurar"):
                cmd_setup(args)
                continue
            elif cmd in ("addkey", "keyadd", "key"):
                _menu_add_key()
                continue
            elif cmd in ("keys", "claves"):
                _menu_keys()
                continue
            elif cmd in ("api", "provider", "proveedor"):
                _menu_api()
                continue
            elif cmd in ("models", "modelos"):
                _menu_models()
                continue
            elif cmd in ("nofree", "free"):
                _menu_block_model(resto)
                continue
            elif cmd in ("topics", "topic", "temas"):
                _menu_topics()
                continue
            elif cmd in ("rules", "reglas"):
                _menu_rules()
                continue
            elif cmd in ("status", "estado"):
                cmd_status(args)
                continue
            elif cmd in ("errors", "errores"):
                cmd_errors(args)
                continue
            elif cmd in ("usage", "uso"):
                cmd_usage(args)
                continue
            elif cmd in ("selftest", "test"):
                from .selftest import run

                run()
                continue
            else:
                print(f"  Comando desconocido: /{cmd}. Escribí /help.")
                continue

        if op in ("0", "salir", "exit", "q"):
            print("👋 Chau.")
            return 0

        if not op.startswith("/"):
            print(f"Comando desconocido: {op}  (escribí /help)")
            continue



        if op == "1":
            pwd = env_master_password()
            if pwd is None and credentials_encrypted():
                pwd = _ask_secret("Clave maestra")
            settings = load_settings(pwd)
            if not settings.telegram_token:
                print("  ⚠️  No hay token. Primero la opcion 2.")
                continue
            bot = FilosoBot(settings)
            try:
                bot.run_forever()
            except KeyboardInterrupt:
                print("\n👋 Chau.")
            return 0

        if op == "2":
            cmd_setup(args)
            continue

        if op == "3":
            _menu_add_key()
            continue

        if op == "4":
            _menu_keys()
            continue

        if op == "5":
            _menu_topics()
            continue

        if op == "6":
            _menu_rules()
            continue

        if op == "7":
            cmd_status(args)
            cmd_errors(args)
            continue

        if op == "8":
            from .selftest import run

            run()
            continue

        print("  Opción no válida.")


def _menu_add_key() -> None:
    """Agrega una API key (pide proveedor y clave)."""
    settings, manager = _load_key_manager()
    print()
    print("  Proveedores:")
    for pid, spec in PROVIDER_SPECS.items():
        print(f"    - {pid:12s} {spec.label}")
    provider = _ask("  Proveedor (id)").lower().strip()
    if not provider:
        print("  Cancelado.")
        return
    while True:
        key = _ask_secret(f"  API key de {provider} (vacio para terminar)")
        if not key:
            break
        try:
            entry = manager.add(provider, key)
        except ValueError as exc:
            print(f"  ❌ {exc}")
            continue
        print(f"  ✅ Agregada: {entry.masked()}  (total {manager.count(provider)} claves)")
        cfg = settings.config.setdefault("providers", {}).get(provider)
        if cfg is not None and not cfg.get("enabled"):
            cfg["enabled"] = True
            save_config(settings.config)
            print(f"     proveedor {provider} habilitado")
        if not _ask_yes("  ¿Agregar otra?", False):
            break


def _menu_keys() -> None:
    """Ver y probar las claves."""
    settings, manager = _load_key_manager()
    print()
    if not manager.providers():
        print("  No hay claves cargadas.")
        return
    for provider in manager.providers():
        print(f"\n  {provider}")
        for st in manager.status(provider):
            flags = []
            if st["default"]:
                flags.append("DEFAULT")
            if st["blocked"]:
                flags.append("BLOQUEADA")
            if st["cooldown"]:
                flags.append(f"cooldown {st['cooldown']}s")
            print(
                f"    [{st['index']}] {st['masked']:<18s} usos={st['uses']:<4d} "
                f"fallos={st['failures']:<3d} {' '.join(flags)}"
            )
            if st["last_error"]:
                print(f"         error: {st['last_error'][:80]}")
    if _ask_yes("  ¿Probar las claves por HTTP?", False):
        for pid in manager.providers():
            cfg = settings.config.get("providers", {}).get(pid)
            if not cfg:
                continue
            pcfg = ProviderConfig.from_dict({"id": pid, **cfg})
            print(f"\n  {pid}")
            for st in manager.status(pid):
                entry = manager.entries(pid)[st["index"]]
                ok, detail = probe_key(pcfg, entry.key)
                print(f"    [{st['index']}] {st['masked']:<18s} {'OK ' if ok else 'FALLA'} {detail[:60]}")


def _menu_topics() -> None:
    """Agregar, quitar o listar topics."""
    from .toml_io import load_toml, save_toml

    config = load_toml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    bot_cfg = config.setdefault("bot", {})
    threads = list(bot_cfg.get("allowed_threads", []) or [])
    chats = list(bot_cfg.get("allowed_chats", []) or [])
    print()
    print(f"  Chats permitidos : {', '.join(str(c) for c in chats) or 'ninguno'}")
    print(f"  Topics permitidos: {', '.join(str(t) for t in threads) or 'todos'}")

    accion = _ask("  (a) agregar  (q) quitar  (l) lista  (enter) salir").strip().lower()
    if accion in ("a", "agregar"):
        nuevo = _ask("  Id del topic (separados por coma)")
        for parte in nuevo.replace(";", ",").split(","):
            parte = parte.strip()
            if parte.isdigit() and int(parte) not in threads:
                threads.append(int(parte))
        bot_cfg["allowed_threads"] = threads
        save_toml(CONFIG_PATH, config)
        print(f"  ✅ Topics: {', '.join(str(t) for t in threads) or 'todos'}")
    elif accion in ("q", "quitar"):
        quitar = _ask("  Id a quitar (separados por coma)")
        for parte in quitar.replace(";", ",").split(","):
            parte = parte.strip()
            if parte.isdigit() and int(parte) in threads:
                threads.remove(int(parte))
        bot_cfg["allowed_threads"] = threads
        save_toml(CONFIG_PATH, config)
        print(f"  ✅ Topics: {', '.join(str(t) for t in threads) or 'todos'}")
    elif accion in ("c", "chats"):
        nuevo = _ask("  Chats permitidos (separados por coma, vacio = todos)")
        lista = []
        for parte in nuevo.replace(";", ",").split(","):
            parte = parte.strip()
            if parte.lstrip("-").isdigit():
                lista.append(int(parte))
        bot_cfg["allowed_chats"] = lista
        bot_cfg["allow_all_chats"] = not lista
        save_toml(CONFIG_PATH, config)
        print(f"  ✅ Chats: {', '.join(str(c) for c in lista) or 'todos'}")


def _set_ai_param(nombre: str, valor: Any) -> None:
    """Cambia un parametro de IA y lo guarda en config.toml."""
    from .toml_io import load_toml, save_toml

    config = load_toml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    config.setdefault("ai", {})[nombre] = valor
    save_toml(CONFIG_PATH, config)


def _reset_historial() -> None:
    """Limpia el historial de la sesion de terminal."""
    from .core.state import StateStore
    from .settings import DATA_DIR

    st = StateStore(DATA_DIR / "state.json")
    st.clear_history(0)


def _menu_api() -> None:
    """Menú de proveedores y modelos (como el de FilosoIA)."""
    settings = load_settings(env_master_password())
    print()
    print("  Proveedores:")
    lista = sorted(PROVIDER_SPECS.items(), key=lambda kv: kv[1].label)
    for i, (pid, spec) in enumerate(lista, start=1):
        cfg = settings.config.get("providers", {}).get(pid, {})
        estado = "ON" if cfg.get("enabled") else "off"
        modelos = ", ".join(spec.default_models[:2]) or "-"
        print(f"    {i:2d}) {spec.label:24s} [{estado}]  {modelos}")
    print(f"    {len(lista) + 1:2d}) Solo los que ya tenes configurados")
    print("     0) Volver")
    op = _ask("  Numero").strip()
    if not op.isdigit() or int(op) == 0:
        return

    if int(op) == len(lista) + 1:
        print("  (los que ya tenes con clave)")
        return

    pid = lista[int(op) - 1][0]
    spec = PROVIDER_SPECS[pid]
    cfg = settings.config.setdefault("providers", {}).setdefault(pid, {})
    cfg["enabled"] = True
    cfg.setdefault("base_url", spec.base_url)
    cfg.setdefault("chat_path", spec.chat_path)
    cfg.setdefault("style", spec.style)
    if spec.default_models:
        cfg.setdefault("models", list(spec.default_models))
    settings.config.setdefault("ai", {})["enabled"] = True
    settings.config["ai"]["default_provider"] = pid
    save_config(settings.config)

    print(f"\n  {spec.label}")
    print(f"  Endpoint: {spec.base_url}{spec.chat_path}")
    print(f"  Key: {spec.key_url or '(no aplica)'}")
    modelos = cfg.get("models") or spec.default_models
    for i, m in enumerate(modelos, start=1):
        print(f"    {i}) {m}")
    if not modelos:
        modelo = _ask("  Modelo").strip()
        if modelo:
            cfg["models"] = [modelo]
            settings.config["ai"]["default_model"] = modelo
            save_config(settings.config)
        print("  ✅ Proveedor activado.")
        return
    op_m = _ask("  Numero de modelo (enter = el primero)").strip()
    modelo = modelos[0]
    if op_m.isdigit() and 1 <= int(op_m) <= len(modelos):
        modelo = modelos[int(op_m) - 1]
    cfg["models"] = [modelo] + [m for m in modelos if m != modelo]
    settings.config["ai"]["default_model"] = modelo
    save_config(settings.config)
    print(f"  ✅ {spec.label} activado con modelo `{modelo}`.")
    print("     Para cargar la key: opción 3 del menú (/addkey).")


def _menu_run(args: argparse.Namespace) -> None:
    """Arranca el bot."""
    pwd = env_master_password()
    if pwd is None and credentials_encrypted():
        pwd = _ask_secret("Clave maestra")
    settings = load_settings(pwd)
    if not settings.telegram_token:
        print("  ⚠️  No hay token. Usá /config.")
        return
    bot = FilosoBot(settings)
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        print("\n👋 Chau.")


def _menu_help() -> None:
    print()
    print("  Comandos:")
    print("    /help              esta ayuda")
    print("    /run               arrancar el bot")
    print("    /config            configurar (token, chats, topics, IA)")
    print("    /keys              ver las claves")
    print("    /addkey            agregar una API key")
    print("    /api               elegir proveedor y modelo")
    print("    /models            ver los modelos")
    print("    /nofree <modelo>   bloquear un modelo")
    print("    /free <modelo>     desbloquear un modelo")
    print("    /topics            agregar o quitar topics")
    print("    /allowed           chats permitidos (add/remove/all)")
    print("    /rules             ver y cambiar las reglas")
    print("    /status            estado")
    print("    /errors            ultimos errores")
    print("    /usage             llamadas por hora y tokens")
    print("    /temp <n>         cambiar la temperatura")
    print("    /max <n>          max tokens de la respuesta")
    print("    /ctx <n>          mensajes de historial")
    print("    /sys <texto>      cambiar el system prompt")
    print("    /reset            limpiar el historial")
    print("    /selftest          verificar que todo anda")
    print("    /quit              salir")


def _menu_models() -> None:
    settings = load_settings(env_master_password())
    print()
    for cfg in sorted(settings.config.get("providers", {}).values(),
                      key=lambda c: c.get("id", "")):
        pid = cfg.get("id", "")
        blocked = settings.provider_configs()
        print(f"  {pid:12s} {cfg.get('base_url', '')}")
        for m in cfg.get("models", []) or []:
            marca = ""
            if m in (settings.rules.get("providers.blocked_models") or []):
                marca = " (bloqueado)"
            print(f"      - {m}{marca}")


def _menu_block_model(resto: List[str]) -> None:
    """Bloquea / desbloquea un modelo del proveedor por defecto."""
    settings, manager = _load_key_manager()
    default = settings.config.get("ai", {}).get("default_provider", "")
    if not default:
        print("  No hay proveedor configurado (usá /api).")
        return
    if not resto:
        print(f"  Uso: /nofree <modelo>   (proveedor: {default})")
        print(f"  Modelos: {', '.join(settings.config.get('providers', {}).get(default, {}).get('models', []))}")
        return
    modelo = resto[0]
    bloqueados = manager.blocked_models(default)
    if modelo in bloqueados:
        manager.unblock_model(default, modelo)
        manager.save()
        print(f"  ✅ `{modelo}` desbloqueado.")
    else:
        manager.block_model(default, modelo)
        manager.save()
        print(f"  🚫 `{modelo}` bloqueado en {default}.")


def _menu_rules() -> None:
    """Ver y cambiar las reglas."""
    rules = RuleSet.load(RULES_PATH)
    print()
    for seccion in rules.sections():
        print(f"  [{seccion}]")
        for ruta, valor, desc in rules.flat():
            if ruta.startswith(seccion + "."):
                print(f"    {ruta} = {valor}")
                if desc:
                    print(f"        -> {desc}")
    if not _ask_yes("  ¿Cambiar alguna?", False):
        return
    while True:
        ruta = _ask("  Ruta (ej: games.interactive)").strip()
        if not ruta:
            break
        if ruta not in rules.known_paths():
            print(f"  ⚠️  Ruta desconocida. Ejemplos: {', '.join(rules.known_paths()[:4])}...")
            continue
        valor = _ask(f"  Nuevo valor de {ruta}")
        rules.set(ruta, parse_value(valor))
        rules.save()
        print(f"  ✅ {ruta} = {rules.get(ruta)}")


def cmd_selftest(args: argparse.Namespace) -> int:
    """Verificacion completa (crypto, persist, juegos, topics, CLI...)."""
    from .selftest import run

    return run()


def cmd_run(args: argparse.Namespace) -> int:
    pwd = env_master_password()
    if pwd is None and credentials_encrypted():
        pwd = _ask_secret("Clave maestra (para leer las credenciales)")
    settings = load_settings(pwd)
    if not settings.telegram_token:
        print("❌ No hay token de Telegram. Corré primero:  python -m bot setup")
        return 1
    bot = FilosoBot(settings)
    try:
        bot.run_forever(scheduler=not getattr(args, "no_scheduler", False))
    except KeyboardInterrupt:
        pass
    return 0


# ======================================================================
# GESTOR DE API KEYS
# ======================================================================
def _load_key_manager():
    """Carga settings + gestor de claves (pide la clave maestra si hace falta)."""
    pwd = env_master_password()
    if pwd is None and credentials_encrypted():
        pwd = _ask_secret("Clave maestra")
    settings = load_settings(pwd)
    manager = KeyManager(settings.persist)
    return settings, manager


def cmd_keys(args: argparse.Namespace) -> int:
    """Gestor de API keys: listar, agregar, quitar, rotar, probar."""
    sub = args.keys_command
    settings, manager = _load_key_manager()

    if sub == "list":
        if not manager.providers():
            print("No hay claves cargadas.")
            print("Agregá una con:  python -m bot keys add <proveedor>")
            return 0
        for provider in manager.providers():
            spec = spec_for(provider)
            print(f"\n🔑 {provider} ({spec.label if spec else provider})")
            for st in manager.status(provider):
                flags = []
                if st["default"]:
                    flags.append("DEFAULT")
                if st["blocked"]:
                    flags.append("BLOQUEADA")
                if st["cooldown"]:
                    flags.append(f"cooldown {st['cooldown']}s")
                print(
                    f"  [{st['index']}] {st['masked']:<20s} usos={st['uses']:<4d} "
                    f"fallos={st['failures']:<3d} {' '.join(flags)}"
                )
                if st["last_error"]:
                    print(f"        ultimo error: {st['last_error'][:80]}")
            calls = manager.calls_in_window(provider)
            pt, ct = manager.token_usage(provider)
            print(f"  llamadas/h: {calls} | tokens: {pt} in / {ct} out")
        return 0

    if sub == "add":
        if not args.path:
            print("Uso: python -m bot keys add <proveedor> [clave]")
            print("Ej:  python -m bot keys add groq gsk_...")
            return 1
        provider = args.path[0].lower()
        key = args.path[1] if len(args.path) > 1 else _ask_secret(f"API key de {provider}")
        if not key:
            print("❌ clave vacía")
            return 1
        try:
            entry = manager.add(provider, key)
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
        print(f"✅ clave agregada a {provider} (#{manager.count(provider) - 1})")
        # habilitar el proveedor si esta configurado
        cfg = settings.config.setdefault("providers", {}).get(provider)
        if cfg and not cfg.get("enabled"):
            cfg["enabled"] = True
            save_config(settings.config)
            print(f"   proveedor {provider} habilitado")
        print(f"   {entry.masked()}  {'(default)' if entry.is_default else ''}")
        return 0

    if sub == "remove":
        if len(args.path) < 2:
            print("Uso: python -m bot keys remove <proveedor> <indice>")
            return 1
        ok = manager.remove(args.path[0].lower(), int(args.path[1]))
        print("✅ clave eliminada" if ok else "❌ indice o proveedor inexistente")
        return 0 if ok else 1

    if sub == "default":
        if len(args.path) < 2:
            print("Uso: python -m bot keys default <proveedor> <indice>")
            return 1
        ok = manager.set_default(args.path[0].lower(), int(args.path[1]))
        print("✅ clave por defecto cambiada" if ok else "❌ indice inexistente")
        return 0 if ok else 1

    if sub == "rotate":
        if not args.path:
            print("Uso: python -m bot keys rotate <proveedor>")
            return 1
        provider = args.path[0].lower()
        nueva = manager.rotate(provider)
        if nueva:
            print(f"🔄 {provider}: ahora usa {crypto_mask(nueva)}")
        else:
            print("❌ no hay claves para rotar")
        return 0

    if sub == "block":
        if len(args.path) < 2:
            print("Uso: python -m bot keys block <proveedor> <indice>")
            return 1
        ok = manager.block(args.path[0].lower(), int(args.path[1]))
        print("✅ clave bloqueada" if ok else "❌ indice inexistente")
        return 0 if ok else 1

    if sub == "unblock":
        if len(args.path) < 2:
            print("Uso: python -m bot keys unblock <proveedor> <indice>")
            return 1
        ok = manager.unblock(args.path[0].lower(), int(args.path[1]))
        print("✅ clave desbloqueada" if ok else "❌ indice inexistente")
        return 0 if ok else 1

    if sub == "test":
        provider = args.path[0].lower() if args.path else ""
        proveedores = [provider] if provider else manager.providers()
        if not proveedores:
            print("No hay claves para probar.")
            return 0
        for pid in proveedores:
            cfg = settings.config.get("providers", {}).get(pid)
            if not cfg:
                print(f"⚠️  {pid}: no está en config.toml, se prueba igual")
                cfg = {"id": pid, "style": "openai",
                       "base_url": (spec_for(pid).base_url if spec_for(pid) else ""),
                       "chat_path": "/models"}
            pcfg = ProviderConfig.from_dict({"id": pid, **cfg})
            print(f"\n🔍 {pid}")
            for st in manager.status(pid):
                entry = manager.entries(pid)[st["index"]]
                ok, detail = probe_key(pcfg, entry.key)
                print(f"  [{st['index']}] {st['masked']:<20s} {'✅' if ok else '❌'} {detail[:70]}")
                if not ok:
                    kind = manager.classify_error(detail)
                    print(f"        tipo: {kind}")
        return 0

    print("Uso: python -m bot keys list|add|remove|default|rotate|test|block|unblock")
    return 1


def crypto_mask(value: str) -> str:
    from . import crypto

    return crypto.mask_secret(value)


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Programador de claves: vigila y rota solo."""
    settings, manager = _load_key_manager()
    registry = ProviderRegistry(
        configs=settings.provider_configs(),
        key_manager=manager,
        cooldown_seconds=float(settings.get("ai.cooldown_seconds", 300)),
    )
    scheduler = Scheduler(
        manager=manager,
        provider_configs=registry.configs(),
        interval=float(args.interval),
        probe=not args.no_probe,
        on_event=lambda m: print(f"  {m}"),
    )
    if args.once:
        report = scheduler.tick()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        print("\n👋 Scheduler detenido.")
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    """Llamadas por hora y tokens por proveedor."""
    settings, manager = _load_key_manager()
    print(f"{'PROVEEDOR':14s} {'CLAVES':7s} {'LLAM/HORA':11s} {'TOK IN':9s} {'TOK OUT':9s} COOLDOWN")
    for provider in manager.providers():
        pt, ct = manager.token_usage(provider)
        print(
            f"{provider:14s} {manager.count(provider):<7d} "
            f"{manager.calls_in_window(provider):<11d} {pt:<9d} {ct:<9d} "
            f"{max(0, int(manager.provider_cooldown(provider) - time.time()))}s"
        )
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    pwd = env_master_password()
    if pwd is None and credentials_encrypted():
        pwd = _ask_secret("Clave maestra")
    settings = load_settings(pwd)
    registry = ProviderRegistry(
        configs=settings.provider_configs(),
        keys_by_provider={
            pid: settings.api_keys(pid) for pid in (settings.config.get("providers", {}) or {})
        },
    )
    print(f"{'PROVEEDOR':14s} {'ACTIVO':7s} {'DISPONIBLE':11s} {'CLAVES':7s} MODELOS")
    for st in registry.status():
        print(
            f"{st['id']:14s} {'sí' if st['enabled'] else 'no':7s} "
            f"{'sí' if st['available'] else 'no':11s} {st['keys']:<7d} "
            f"{', '.join(st['models'][:3]) or '-'}"
        )
        if st["last_error"]:
            print(f"    ultimo error: {st['last_error'][:100]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(f"config      : {CONFIG_PATH} {'(existe)' if CONFIG_PATH.exists() else '(NO EXISTE)'}")
    print(f"reglas      : {RULES_PATH} {'(existe)' if rules_exist() else '(NO EXISTE)'}")
    print(f"credenciales: {CREDENTIALS_PATH} {'(existe)' if credentials_exist() else '(NO EXISTE)'}")
    pwd = env_master_password()
    if credentials_exist():
        try:
            persist = load_settings(pwd).persist
            token = persist.telegram_token if persist else ""
            print(f"token telegram: {'configurado' if token else 'vacio'}")
            for pid in (persist.provider_ids() if persist else []):
                n = len(persist.keys(pid))
                pt, ct = persist.get_token_usage(pid)
                print(f"api key {pid:12s}: {n} clave(s) | tokens {pt}in/{ct}out")
        except Exception as exc:  # noqa: BLE001
            print(f"no se pudieron leer las credenciales: {exc}")
    return 0


def cmd_topics(args: argparse.Namespace) -> int:
    """Topics permitidos: agregar, quitar o listar (desde la CLI)."""
    from .toml_io import load_toml, save_toml

    config = load_toml(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    bot_cfg = config.setdefault("bot", {})
    threads = list(bot_cfg.get("allowed_threads", []) or [])
    accion = args.topics_command

    if accion == "list":
        chats = bot_cfg.get("allowed_chats", []) or []
        print(f"Chats permitidos   : {', '.join(str(c) for c in chats) or 'ninguno'}")
        print(f"Topics permitidos  : {', '.join(str(t) for t in threads) or 'todos'}")
        print(f"allow_all_chats    : {bot_cfg.get('allow_all_chats', False)}")
        return 0

    if accion in ("add", "remove") and not args.path:
        print("Uso: python -m bot topics add <id>   (o remove <id>)")
        return 1

    if accion == "add":
        for parte in args.path:
            if not parte.isdigit():
                print(f"'{parte}' no es un id de topic")
                continue
            t = int(parte)
            if t not in threads:
                threads.append(t)
        bot_cfg["allowed_threads"] = threads
        save_toml(CONFIG_PATH, config)
        print(f"✅ Topics permitidos: {', '.join(str(t) for t in threads) or 'todos'}")
        return 0

    if accion == "remove":
        for parte in args.path:
            try:
                t = int(parte)
            except ValueError:
                print(f"'{parte}' no es un id de topic")
                continue
            if t in threads:
                threads.remove(t)
        bot_cfg["allowed_threads"] = threads
        save_toml(CONFIG_PATH, config)
        print(f"✅ Topics permitidos: {', '.join(str(t) for t in threads) or 'todos'}")
        return 0

    print("Uso: python -m bot topics list|add|remove")
    return 1


def cmd_errors(args: argparse.Namespace) -> int:
    """Ultimos errores: siempre visibles en la CLI (a diferencia del chat)."""
    settings, manager = _load_key_manager()
    registry = ProviderRegistry(
        configs=settings.provider_configs(),
        key_manager=manager,
        cooldown_seconds=float(settings.get("ai.cooldown_seconds", 300)),
    )
    print("🧾 Ultimos errores (solo CLI)\n")
    algo = False
    for st in registry.status():
        if st["last_error"]:
            algo = True
            cd = max(0, int(st["cooldown_until"] - time.time()))
            print(f"❌ {st['label']} ({st['id']})")
            print(f"   error  : {st['last_error']}")
            print(f"   cooldown: {cd}s")
            print(f"   endpoint: {st['endpoint']}")
            print(f"   claves  : {st['usable_keys']}/{st['keys']} usables\n")
    for provider in manager.providers():
        for st in manager.status(provider):
            if st["last_error"]:
                algo = True
                flags = []
                if st["blocked"]:
                    flags.append("BLOQUEADA")
                if st["cooldown"]:
                    flags.append(f"cooldown {st['cooldown']}s")
                print(f"🔑 {provider} key #{st['index']} {st['masked']}")
                print(f"   error : {st['last_error']}")
                print(f"   usos  : {st['uses']} | fallos: {st['failures']} {' '.join(flags)}\n")
    if not algo:
        print("Sin errores registrados. Todo bien. ✅")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    pwd = env_master_password()
    if pwd is None and credentials_encrypted():
        pwd = _ask_secret("Clave maestra")
    settings = load_settings(pwd)
    if not settings.telegram_token:
        print("❌ No hay token configurado.")
        return 1
    client = TelegramClient(token=settings.telegram_token)
    chat_id = int(args.chat)
    client.send_message(chat_id, args.text)
    print(f"✅ enviado a {chat_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot", description="FilosoBot - bot de Telegram modular"
    )
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="configuracion interactiva")
    p_setup.set_defaults(func=cmd_setup)

    p_run = sub.add_parser("run", help="arrancar el bot")
    p_run.add_argument("--no-scheduler", action="store_true",
                       help="no arrancar el vigilante de claves")
    p_run.set_defaults(func=cmd_run)

    p_keys = sub.add_parser("keys", help="gestor de API keys")
    p_keys.add_argument(
        "keys_command",
        nargs="?",
        default="list",
        choices=["list", "add", "remove", "default", "rotate", "test", "block", "unblock"],
    )
    p_keys.add_argument("path", nargs="*")
    p_keys.set_defaults(func=cmd_keys)

    p_sched = sub.add_parser("scheduler", help="vigilante de claves (rotacion automatica)")
    p_sched.add_argument("--interval", type=float, default=300.0,
                         help="segundos entre chequeos (default 300)")
    p_sched.add_argument("--once", action="store_true", help="un solo chequeo y salir")
    p_sched.add_argument("--no-probe", action="store_true", help="no probar las claves por HTTP")
    p_sched.set_defaults(func=cmd_scheduler)

    p_usage = sub.add_parser("usage", help="llamadas por hora y tokens por proveedor")
    p_usage.set_defaults(func=cmd_usage)

    p_err = sub.add_parser("errors", help="ultimos errores de proveedores y claves (solo CLI)")
    p_err.set_defaults(func=cmd_errors)

    p_topics = sub.add_parser("topics", help="topics permitidos (add/remove/list)")
    p_topics.add_argument("topics_command", nargs="?", default="list",
                          choices=["list", "add", "remove"])
    p_topics.add_argument("path", nargs="*")
    p_topics.set_defaults(func=cmd_topics)

    p_menu = sub.add_parser("menu", help="menu interactivo (sin argumentos)")
    p_menu.set_defaults(func=cmd_menu)

    p_self = sub.add_parser("selftest", help="verificacion completa del bot")
    p_self.set_defaults(func=cmd_selftest)

    p_rules = sub.add_parser("rules", help="reglas de conducta (rules.toml)")
    p_rules.add_argument(
        "rules_command",
        nargs="?",
        default="list",
        choices=["list", "set", "get", "reset", "edit"],
    )
    p_rules.add_argument("path", nargs="*")
    p_rules.set_defaults(func=cmd_rules)

    p_config = sub.add_parser("config", help="configuracion (config.toml)")
    p_config.add_argument(
        "config_command", nargs="?", default="show", choices=["show", "set"]
    )
    p_config.add_argument("path", nargs="*")
    p_config.set_defaults(func=cmd_config)

    p_prov = sub.add_parser("providers", help="estado de proveedores de IA")
    p_prov.set_defaults(func=cmd_providers)

    p_status = sub.add_parser("status", help="estado de la configuracion")
    p_status.set_defaults(func=cmd_status)

    p_send = sub.add_parser("send", help="enviar un mensaje de prueba")
    p_send.add_argument("text")
    p_send.add_argument("--chat", required=True)
    p_send.set_defaults(func=cmd_send)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # la consola de Windows (cp1252) no aguanta los emojis: fuerzo utf-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # sin argumentos: menu interactivo (todo se configura desde ahi)
        ensure_dependencies(auto_install=True)
        args = parser.parse_args(["menu"])
        return int(args.func(args) or 0)

    ensure_dependencies(auto_install=True)

    level = os.environ.get("FILOSO_LOG_LEVEL", "INFO")
    log_dir = DATA_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_dir / "filoso-bot.log", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
