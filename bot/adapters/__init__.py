"""Adaptadores: todo lo que habla con el mundo exterior.

Sólo Telegram por ahora. La IA va en `bot/modules/ai` porque no es un
adaptador sino lógica con dependencias externas configurables.
"""
from .telegram import TelegramClient, TelegramError

__all__ = ["TelegramClient", "TelegramError"]
