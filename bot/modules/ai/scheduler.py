"""Programador (scheduler) de API keys.

Es el "vigilante" del bot:

  * cada `interval` segundos revisa los proveedores habilitados,
  * prueba las claves de cada uno (endpoint de modelos, no consume tokens),
  * las que fallan con 401/403 quedan bloqueadas y se rota a la siguiente,
  * las que fallan con 5xx ponen el proveedor en cooldown,
  * libera las que estaban en cooldown y ya pasó el tiempo,
  * registra llamadas por hora y tokens consumidos.

Se puede usar:

  * como hilo dentro del bot (`FilosoBot` lo levanta con `start_scheduler`),
  * como proceso aparte:  `python -m bot scheduler`.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ...models import ProviderConfig
from .keys import COOLDOWN_SECONDS, KeyManager, probe_key

log = logging.getLogger("filoso.scheduler")


class Scheduler:
    """Vigilante de claves y proveedores."""

    def __init__(
        self,
        manager: KeyManager,
        provider_configs: List[ProviderConfig],
        interval: float = 300.0,
        probe: bool = True,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.manager = manager
        self.provider_configs = provider_configs
        self.interval = interval
        self.probe = probe
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def _emit(self, message: str) -> None:
        log.info("%s", message)
        if self.on_event:
            try:
                self.on_event(message)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def tick(self) -> Dict[str, Any]:
        """Un ciclo de verificación. Devuelve un resumen."""
        now = time.time()
        report: Dict[str, Any] = {"checked": [], "rotated": [], "cooled": [], "released": []}

        for cfg in self.provider_configs:
            if not cfg.enabled:
                continue
            provider = cfg.id

            # 1) liberar cooldowns vencidos
            if self.manager.provider_cooldown(provider) and self.manager.provider_cooldown(provider) <= now:
                self.manager.clear_provider_cooldown(provider)
                report["released"].append(provider)
                self._emit(f"🔓 {provider}: cooldown terminado, vuelve a estar disponible")

            # 2) probar claves
            if not self.probe:
                continue
            for i, entry in enumerate(self.manager.entries(provider)):
                if not entry.key or entry.blocked:
                    continue
                if entry.cooldown_until > now:
                    continue
                ok, detail = probe_key(cfg, entry.key)
                if ok:
                    entry.last_error = ""
                    entry.failures = 0
                    report["checked"].append(f"{provider}#{i} ok")
                    continue

                # fallo: aplicar la filosofía de FilosoIA
                kind = self.manager.classify_error(detail)
                entry.failures += 1
                entry.last_error = detail[:200]
                if kind == "auth":
                    entry.blocked = True
                    self.manager.rotate(provider)
                    report["rotated"].append(f"{provider}#{i}")
                    self._emit(f"🔑 {provider}: key #{i} no responde ({detail[:60]}) -> roto a la siguiente")
                elif kind == "server":
                    self.manager.set_provider_cooldown(provider, COOLDOWN_SECONDS)
                    report["cooled"].append(provider)
                    self._emit(f"⏳ {provider}: error de servidor -> cooldown {COOLDOWN_SECONDS}s")
                else:
                    self._emit(f"⚠️ {provider}: key #{i} fallo ({detail[:60]})")

        self.manager.save()
        return report

    # ------------------------------------------------------------------
    def run_forever(self) -> None:
        print(f"⏱ Scheduler de claves activo (cada {self.interval:.0f}s). Ctrl+C para salir.")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick del scheduler fallo")
            self._stop.wait(self.interval)

    def start(self) -> None:
        """Arranca el scheduler en segundo plano."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="scheduler")
        self._thread.start()
        log.info("scheduler arrancado (cada %.0fs)", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
