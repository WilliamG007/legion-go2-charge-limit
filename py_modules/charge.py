"""Legion Go 2 Charge Limit through the kernel charging interfaces.

Desktop Mode Power Management writes the same nodes: Lenovo Long Life /
conservation (~80%) on, Standard / conservation off for a full charge.

Preferred ABI is ``charge_types`` (``Long_Life`` / ``Standard`` / ``Fast``).
``conservation_mode`` and ``charge_control_end_threshold`` are written too
when the kernel exposes them, so Desktop Mode and Game Mode stay in sync.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any, Callable

GO2_PRODUCTS = frozenset({"83N0", "83N1"})
DMI_VENDOR = "class/dmi/id/sys_vendor"
DMI_PRODUCT = "class/dmi/id/product_name"
POWER_SUPPLY = "class/power_supply"
CONSERVATION_RELATIVE = (
    "bus/platform/devices/VPC2004:00/conservation_mode",
    "bus/platform/drivers/ideapad_acpi/VPC2004:00/conservation_mode",
)
ALLOWED_MODES = frozenset({"Standard", "Fast", "Long_Life"})
NORMAL_MODES = frozenset({"Standard", "Fast"})
LIMIT_MODE = "Long_Life"
LIMIT_PERCENT = 80
FULL_PERCENT = 100
HW_TIMEOUT_S = 1.5
DEFAULTS: dict[str, Any] = {
    "managed": False,
    "enabled": False,
    "normal_mode": "Standard",
}

logger = logging.getLogger("legion-go2-charge-limit")


class ChargeError(RuntimeError):
    pass


class ModeConflict(ChargeError):
    """ideapad-laptop returns EINVAL when Rapid Charge and Conservation are both on."""


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def sanitize_settings(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("normal_mode", DEFAULTS["normal_mode"]))
    if mode not in NORMAL_MODES:
        mode = DEFAULTS["normal_mode"]
    return {
        "managed": _as_bool(data.get("managed"), DEFAULTS["managed"]),
        "enabled": _as_bool(data.get("enabled"), DEFAULTS["enabled"]),
        "normal_mode": mode,
    }


def run_with_timeout(func: Callable[[], Any], timeout: float = HW_TIMEOUT_S, default: Any = None) -> Any:
    """Run *func* in a daemon thread. EC sysfs can stall; the RPC loop must not wait on that."""
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = func()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return default
    if "error" in box:
        raise box["error"]
    return box.get("value", default)


class JsonStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return sanitize_settings(None)
        if isinstance(data, dict) and "settings" in data:
            data = data["settings"]
        return sanitize_settings(data)

    def save(self, settings: dict[str, Any]) -> None:
        clean = sanitize_settings(settings)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"settings": clean}, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, self.path)


def companion_present(plugin_dir: str) -> bool:
    try:
        plugins = Path(plugin_dir).resolve().parent
    except OSError:
        return False
    for name in ("LegionGo2Companion", "LegionGoCompanion"):
        if (plugins / name / "plugin.json").is_file():
            return True
    return False


def parse_charge_types(value: str) -> tuple[list[str], str]:
    tokens = value.split()
    if not tokens or any(not re.fullmatch(r"\[?[A-Za-z][A-Za-z_]*\]?", token) for token in tokens):
        raise ChargeError("The battery returned malformed charging modes.")
    if len(tokens) == 1 and tokens[0] in ALLOWED_MODES:
        return [tokens[0]], tokens[0]
    selected = [token[1:-1] for token in tokens if token.startswith("[") and token.endswith("]")]
    if len(selected) != 1 or any((token.startswith("[")) != (token.endswith("]")) for token in tokens):
        raise ChargeError("The battery did not report one active charging mode.")
    options = [token.strip("[]") for token in tokens]
    if len(options) != len(set(options)):
        raise ChargeError("The battery returned duplicate charging modes.")
    if selected[0] not in ALLOWED_MODES:
        raise ChargeError("The current charging mode cannot be used.")
    return options, selected[0]


def _error_status(message: str, product: str = "", companion: bool = False) -> dict[str, Any]:
    return {
        "success": False,
        "go2": product in GO2_PRODUCTS,
        "product": product,
        "companion": companion,
        "ready": False,
        "reason": message,
        "enabled": None,
        "capacity": None,
        "charging_status": None,
        "limit_percent": LIMIT_PERCENT,
        "current_mode": None,
        "backend": "",
        "settings": sanitize_settings(None),
        "last_error": message,
        "error": message,
    }


class ChargeController:
    def __init__(
        self,
        store: JsonStore,
        sys_root: str | Path = "/sys",
        companion: bool = False,
        product: str | None = None,
        log: logging.Logger | None = None,
    ):
        self.store = store
        self.sys_root = Path(sys_root)
        self.companion = companion
        self.log = log or logger
        self.settings = store.load()
        self.product = product if product is not None else self._dmi_product()
        self._lock = threading.RLock()
        self._cached: dict[str, Any] = {
            "success": True,
            "go2": self.go2(),
            "product": self.product,
            "companion": self.companion,
            "ready": False,
            "reason": "Starting",
            "enabled": None,
            "capacity": None,
            "charging_status": None,
            "limit_percent": LIMIT_PERCENT,
            "current_mode": None,
            "backend": "",
            "settings": dict(self.settings),
            "last_error": "",
            "error": "",
        }

    def go2(self) -> bool:
        return self.product in GO2_PRODUCTS

    def status(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self._cached)
            out["settings"] = dict(self.settings)
            out["companion"] = self.companion
            out["go2"] = self.go2()
            out["product"] = self.product
            return out

    def probe(self) -> dict[str, Any]:
        return run_with_timeout(self._probe, default=self.status()) or self.status()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        result = run_with_timeout(lambda: self._set_enabled(bool(enabled)), default=None)
        if result is None:
            return self._publish(ready=False, reason="The battery did not reply in time", success=False)
        return result

    def reapply(self) -> dict[str, Any]:
        result = run_with_timeout(self._reapply, default=None)
        if result is None:
            return self._publish(ready=False, reason="The battery did not reply in time", success=False)
        return result

    def _dmi_product(self) -> str:
        try:
            return self._read_text(self.sys_root / DMI_PRODUCT, 64)
        except OSError:
            return ""

    def _dmi_vendor(self) -> str:
        try:
            return self._read_text(self.sys_root / DMI_VENDOR, 64)
        except OSError:
            return ""

    def _read_text(self, path: Path, limit: int = 512) -> str:
        resolved = self._checked_path(path)
        with open(resolved, "r", encoding="ascii", errors="replace") as handle:
            return handle.read(limit).strip()

    def _write_text(self, path: Path, value: str) -> None:
        resolved = self._checked_path(path)
        payload = (value.strip() + "\n").encode("ascii")
        flags = os.O_WRONLY | os.O_TRUNC
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved, flags)
        try:
            if os.write(fd, payload) != len(payload):
                raise ChargeError("The kernel accepted only part of the charge limit setting.")
        finally:
            os.close(fd)

    def _checked_path(self, path: Path) -> Path:
        root = self.sys_root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ChargeError("Charge limit path resolves outside sysfs.")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ChargeError("Charge limit interface is not a regular sysfs attribute.")
        return resolved

    def _optional_text(self, path: Path) -> str | None:
        try:
            return self._read_text(path)
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _find_conservation(self) -> Path | None:
        for relative in CONSERVATION_RELATIVE:
            candidate = self.sys_root / relative
            try:
                self._checked_path(candidate)
            except (ChargeError, OSError):
                continue
            return candidate
        return None

    def _find_battery(self) -> Path:
        supply = self.sys_root / POWER_SUPPLY
        if not supply.is_dir():
            raise ChargeError("No system battery was found.")
        batteries: list[Path] = []
        for candidate in sorted(supply.iterdir()):
            try:
                if not candidate.is_dir():
                    continue
                if self._read_text(candidate / "type") != "Battery":
                    continue
            except OSError:
                continue
            scope = self._optional_text(candidate / "scope")
            if scope != "System" and (scope is not None or not re.fullmatch(r"BAT\d+", candidate.name)):
                continue
            present = self._optional_text(candidate / "present")
            if present == "0":
                continue
            batteries.append(candidate)
        if len(batteries) != 1:
            raise ChargeError(
                "No system battery was found."
                if not batteries
                else "More than one system battery was found."
            )
        return batteries[0]

    def _read_charge_types(self, path: Path) -> tuple[list[str], str]:
        try:
            raw = self._read_text(path)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise ModeConflict("Rapid Charge and Charge Limit are both on, so the charging mode cannot be read.") from exc
            raise
        return parse_charge_types(raw)

    def _discover(self) -> dict[str, Any]:
        if not self.go2():
            vendor = self._dmi_vendor()
            extra = f" This device reports {self.product or 'an unknown model'}."
            if vendor and vendor.upper() != "LENOVO":
                extra = f" This device reports {vendor} {self.product}.".rstrip()
            raise ChargeError(
                "This plugin is for the Legion Go 2 (83N0 / 83N1)." + extra
            )
        battery = self._find_battery()
        types_path = battery / "charge_types"
        threshold_path = battery / "charge_control_end_threshold"
        conservation = self._find_conservation()
        has_types = types_path.is_file()
        has_threshold = threshold_path.is_file()
        if not has_types and conservation is None and not has_threshold:
            raise ChargeError(
                "Charge Limit is not exposed by this kernel. Need charge_types, conservation_mode, or charge_control_end_threshold."
            )
        options: list[str] = []
        current_mode: str | None = None
        types_conflict = False
        if has_types:
            try:
                options, current_mode = self._read_charge_types(types_path)
            except ModeConflict:
                types_conflict = True
            except ChargeError:
                raise
            except OSError as exc:
                raise ChargeError(f"Could not read charging modes: {exc}") from exc
        enabled: bool | None = None
        backend = ""
        if current_mode is not None:
            enabled = current_mode == LIMIT_MODE
            backend = "charge_types"
        if conservation is not None:
            raw = self._read_text(conservation)
            if raw not in {"0", "1"}:
                raise ChargeError("conservation_mode returned an invalid value.")
            conservation_on = raw == "1"
            if enabled is None:
                enabled = conservation_on
                backend = "conservation_mode"
            elif types_conflict:
                enabled = conservation_on
                backend = "conservation_mode"
        if has_threshold:
            raw = self._read_text(threshold_path)
            try:
                end = int(raw)
            except ValueError as exc:
                raise ChargeError("charge_control_end_threshold returned an invalid value.") from exc
            if enabled is None:
                enabled = 0 < end < FULL_PERCENT
                backend = "charge_control_end_threshold"
        if types_conflict and enabled is None:
            enabled = True
            backend = "charge_types"
        if enabled is None:
            raise ChargeError("Charge Limit state could not be read.")
        capacity_raw = self._optional_text(battery / "capacity")
        capacity = None
        if capacity_raw is not None and capacity_raw.isdecimal() and 0 <= int(capacity_raw) <= 100:
            capacity = int(capacity_raw)
        charging_status = self._optional_text(battery / "status")
        return {
            "battery": battery,
            "types_path": types_path if has_types else None,
            "conservation": conservation,
            "threshold_path": threshold_path if has_threshold else None,
            "options": options,
            "current_mode": current_mode,
            "types_conflict": types_conflict,
            "enabled": enabled,
            "backend": backend,
            "capacity": capacity,
            "charging_status": charging_status,
        }

    def _write_mode(self, path: Path, mode: str) -> None:
        if mode not in ALLOWED_MODES:
            raise ChargeError("Unsupported charging mode.")
        self._write_text(path, mode)

    def _apply(self, enabled: bool, device: dict[str, Any], normal_mode: str) -> None:
        target_mode = LIMIT_MODE if enabled else normal_mode
        wrote = False
        errors: list[str] = []
        types_path: Path | None = device["types_path"]
        if types_path is not None:
            try:
                self._write_mode(types_path, target_mode)
                wrote = True
            except OSError as exc:
                errors.append(f"charge_types: {exc}")
        conservation: Path | None = device["conservation"]
        if conservation is not None:
            try:
                self._write_text(conservation, "1" if enabled else "0")
                wrote = True
            except OSError as exc:
                errors.append(f"conservation_mode: {exc}")
        threshold: Path | None = device["threshold_path"]
        if threshold is not None:
            try:
                self._write_text(threshold, str(LIMIT_PERCENT if enabled else FULL_PERCENT))
                wrote = True
            except OSError as exc:
                errors.append(f"charge_control_end_threshold: {exc}")
        if not wrote:
            raise ChargeError("Could not write Charge Limit. " + " ".join(errors))

        confirmed = self._discover()
        if bool(confirmed["enabled"]) != enabled:
            raise ChargeError("The battery did not confirm the Charge Limit setting.")
        if types_path is not None and confirmed["current_mode"] not in {None, target_mode} and not confirmed["types_conflict"]:
            raise ChargeError("The battery did not confirm the charging mode.")

    def _publish(self, **updates: Any) -> dict[str, Any]:
        with self._lock:
            self._cached.update(updates)
            self._cached["settings"] = dict(self.settings)
            self._cached["companion"] = self.companion
            self._cached["go2"] = self.go2()
            self._cached["product"] = self.product
            self._cached["limit_percent"] = LIMIT_PERCENT
            if self._cached.get("success") is False:
                self._cached["error"] = self._cached.get("last_error") or self._cached.get("reason") or ""
            else:
                self._cached["error"] = self._cached.get("last_error") or ""
            return dict(self._cached)

    def _probe(self) -> dict[str, Any]:
        try:
            device = self._discover()
        except Exception as exc:
            message = str(exc)
            self.log.warning("Charge Limit probe failed: %s", message)
            return self._publish(
                success=False,
                ready=False,
                reason=message,
                last_error=message,
                enabled=None,
                current_mode=None,
                backend="",
            )
        return self._publish(
            success=True,
            ready=True,
            reason="",
            last_error="",
            error="",
            enabled=device["enabled"],
            current_mode=device["current_mode"],
            backend=device["backend"],
            capacity=device["capacity"],
            charging_status=device["charging_status"],
        )

    def _set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            try:
                device = self._discover()
                current = device["current_mode"]
                if current in NORMAL_MODES:
                    self.settings["normal_mode"] = current
                elif self.settings["normal_mode"] not in NORMAL_MODES:
                    self.settings["normal_mode"] = "Standard"
                if device["types_path"] is not None and LIMIT_MODE not in device["options"] and device["options"]:
                    raise ChargeError("The battery does not advertise Long Life / Charge Limit.")
                self._apply(enabled, device, self.settings["normal_mode"])
                self.settings["managed"] = True
                self.settings["enabled"] = enabled
                self.store.save(self.settings)
            except Exception as exc:
                message = str(exc)
                self.log.error("Charge Limit set failed: %s", message)
                return self._publish(
                    success=False,
                    ready=self._cached.get("ready", False),
                    reason=message,
                    last_error=message,
                )
            return self._probe()

    def _reapply(self) -> dict[str, Any]:
        with self._lock:
            if not self.settings["managed"]:
                return self._probe()
            try:
                device = self._discover()
                if bool(device["enabled"]) == bool(self.settings["enabled"]) and not device["types_conflict"]:
                    return self._probe()
                self._apply(bool(self.settings["enabled"]), device, self.settings["normal_mode"])
            except Exception as exc:
                message = str(exc)
                self.log.warning("Charge Limit reapply failed: %s", message)
                return self._publish(
                    success=False,
                    ready=False,
                    reason=message,
                    last_error=message,
                )
            return self._probe()
