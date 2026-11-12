#!/usr/bin/env python3
"""FilosoBot — punto de entrada.

    python run.py

Si el bot ya esta configurado, arranca. Si no, abre el asistente
(te pregunta el token de Telegram, los chats, si queres IA y la clave maestra).
"""
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from bot.cli import main  # noqa: E402
from bot.config import Config  # noqa: E402

if __name__ == "__main__":
    print(f"[filoso-bot] {Config.resumen()}")
    sys.exit(main())
