"""Módulos de negocio: clasificador, IA, juegos, preguntas.

Cada módulo es independiente y se registra en el router.
"""
from .classifier import Classification, classify, Intent

__all__ = ["Classification", "classify", "Intent"]
