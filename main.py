import asyncio
import os
import sys
import traceback

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (PLUGIN_DIR, os.path.join(PLUGIN_DIR, "py_modules")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import decky

_IMPORT_ERROR = ""
try:
    from charge import ChargeController, JsonStore, companion_present
except Exception as exc:
    ChargeController = None  # type: ignore
    JsonStore = None  # type: ignore
    companion_present = None  # type: ignore
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    try:
        decky.logger.error("Failed to import charge backend:\n%s", traceback.format_exc())
    except Exception:
        pass


def _error_status(message: str) -> dict:
    return {
        "success": False,
        "go2": False,
        "product": "",
        "companion": False,
        "ready": False,
        "reason": message,
        "enabled": None,
        "capacity": None,
        "charging_status": None,
        "limit_percent": 80,
        "current_mode": None,
        "backend": "",
        "settings": {"managed": False, "enabled": False, "normal_mode": "Standard"},
        "last_error": message,
        "error": message,
    }


class Plugin:
    def _controller(self):
        if _IMPORT_ERROR or ChargeController is None or JsonStore is None:
            raise RuntimeError(_IMPORT_ERROR or "Charge Limit backend failed to import")
        charge = getattr(self, "charge", None)
        if charge is not None:
            return charge
        settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        plugin_dir = getattr(decky, "DECKY_PLUGIN_DIR", PLUGIN_DIR)
        self.charge = ChargeController(
            store=JsonStore(settings_path),
            companion=bool(companion_present(plugin_dir)),
            log=decky.logger,
        )
        return self.charge

    async def _offload(self, func):
        return await asyncio.get_running_loop().run_in_executor(None, func)

    async def _main(self):
        if _IMPORT_ERROR:
            decky.logger.error("Backend import failed: %s", _IMPORT_ERROR)
            return
        try:
            charge = self._controller()
            decky.logger.info(
                "Legion Go 2 Charge Limit started (product=%s go2=%s companion=%s)",
                charge.product,
                charge.go2(),
                charge.companion,
            )
            self._task = asyncio.create_task(self._run())
        except Exception:
            decky.logger.error("Failed to start Charge Limit backend:\n%s", traceback.format_exc())

    async def _run(self):
        try:
            await self._offload(self._controller().reapply)
            while True:
                await asyncio.sleep(8)
                await self._offload(self._controller().probe)
        except asyncio.CancelledError:
            raise
        except Exception:
            decky.logger.error("Charge Limit loop failed:\n%s", traceback.format_exc())

    async def _unload(self):
        task = getattr(self, "_task", None)
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        decky.logger.info("Legion Go 2 Charge Limit unloaded")

    async def get_status(self):
        if _IMPORT_ERROR:
            return _error_status(_IMPORT_ERROR)
        try:
            return self._controller().status()
        except Exception as exc:
            decky.logger.error("get_status failed:\n%s", traceback.format_exc())
            return _error_status(f"{type(exc).__name__}: {exc}")

    async def set_enabled(self, enabled: bool):
        return await self._offload(lambda: self._controller().set_enabled(bool(enabled)))

    async def reapply(self):
        return await self._offload(self._controller().reapply)

    async def resume(self):
        return await self._offload(self._controller().reapply)
