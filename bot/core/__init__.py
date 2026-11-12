"""Núcleo del bot: router, filtros, limpieza y estado.

Esta capa no conoce Telegram ni proveedores de IA: sólo recibe
`Message` y devuelve `OutgoingMessage`.
"""
from .router import Router, ModuleResult
from .filters import FilterPipeline
from .cleanup import CleanupService
from .state import StateStore

__all__ = ["Router", "ModuleResult", "FilterPipeline", "CleanupService", "StateStore"]
