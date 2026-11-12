"""FilosoBot — bot de Telegram modular.

Capas (no se mezclan entre sí):

    adapters/  -> habla con Telegram (nada más)
    modules/   -> lógica de negocio (mensajes, juegos, IA, filtros)
    core/      -> router, estado y pipeline común

La respuesta del modelo SIEMPRE pasa por `modules/ai` y vuelve al
dispatcher como texto plano; ningún módulo de negocio conoce Telegram.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
