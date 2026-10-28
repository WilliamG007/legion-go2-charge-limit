import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "py_modules"))

from charge import (
    ALLOWED_MODES,
    ChargeController,
    ChargeError,
    JsonStore,
    companion_present,
    parse_charge_types,
    sanitize_settings,
)


class KernelChargeController(ChargeController):
    """Writes charge_types the way the kernel echoes them: all modes, active in brackets."""

    def _write_text(self, path: Path, value: str) -> None:
        super()._write_text(path, value)
        mode = value.strip()
        if path.name == "charge_types" and mode in ALLOWED_MODES:
            tokens = [f"[{item}]" if item == mode else item for item in ("Standard", "Fast", "Long_Life")]
            path.write_text(" ".join(tokens) + "\n")


def write_attr(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if value.endswith("\n") else value + "\n")
    os.chmod(path, 0o644)


def make_sys(root: Path, product: str = "83N0", mode: str = "Standard", conservation: str | None = "0") -> Path:
    sys_root = root / "sys"
    write_attr(sys_root / "class/dmi/id/sys_vendor", "LENOVO")
    write_attr(sys_root / "class/dmi/id/product_name", product)
    battery = sys_root / "class/power_supply/BAT0"
    write_attr(battery / "type", "Battery")
    write_attr(battery / "scope", "System")
    write_attr(battery / "present", "1")
    write_attr(battery / "capacity", "73")
    write_attr(battery / "status", "Charging")
    options = ["Standard", "Fast", "Long_Life"]
    tokens = [f"[{item}]" if item == mode else item for item in options]
    write_attr(battery / "charge_types", " ".join(tokens))
    write_attr(battery / "charge_control_end_threshold", "80" if mode == "Long_Life" else "100")
    if conservation is not None:
        write_attr(
            sys_root / "bus/platform/devices/VPC2004:00/conservation_mode",
            conservation,
        )
    hid = sys_root / "class/power_supply/hid-controller"
    write_attr(hid / "type", "Battery")
    write_attr(hid / "scope", "Device")
    write_attr(hid / "capacity", "90")
    return sys_root


class ChargeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.sys_root = make_sys(self.root)
        self.store_path = self.root / "settings.json"
        self.charge = KernelChargeController(
            store=JsonStore(str(self.store_path)),
            sys_root=self.sys_root,
            product="83N0",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def types_text(self) -> str:
        return (self.sys_root / "class/power_supply/BAT0/charge_types").read_text().strip()

    def conservation_text(self) -> str:
        return (self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode").read_text().strip()

    def threshold_text(self) -> str:
        return (self.sys_root / "class/power_supply/BAT0/charge_control_end_threshold").read_text().strip()

    def test_parse_selected_long_life(self):
        options, current = parse_charge_types("Standard Fast [Long_Life]")
        self.assertEqual(current, "Long_Life")
        self.assertEqual(options, ["Standard", "Fast", "Long_Life"])

    def test_status_returns_before_probe(self):
        status = self.charge.status()
        self.assertTrue(status["go2"])
        self.assertEqual(status["product"], "83N0")
        self.assertEqual(status["reason"], "Starting")
        self.assertFalse(status["ready"])

    def test_probe_reads_hardware_without_writing(self):
        status = self.charge.probe()
        self.assertTrue(status["success"])
        self.assertTrue(status["ready"])
        self.assertFalse(status["enabled"])
        self.assertEqual(status["capacity"], 73)
        self.assertEqual(status["charging_status"], "Charging")
        self.assertEqual(status["current_mode"], "Standard")
        self.assertEqual(status["backend"], "charge_types")
        self.assertEqual(self.types_text(), "[Standard] Fast Long_Life")
        self.assertFalse(self.charge.settings["managed"])

    def test_enable_writes_long_life_conservation_and_threshold(self):
        status = self.charge.set_enabled(True)
        self.assertTrue(status["success"])
        self.assertTrue(status["enabled"])
        self.assertEqual(self.types_text(), "Standard Fast [Long_Life]")
        self.assertEqual(self.conservation_text(), "1")
        self.assertEqual(self.threshold_text(), "80")
        self.assertTrue(self.charge.settings["managed"])
        self.assertTrue(self.charge.settings["enabled"])
        saved = JsonStore(str(self.store_path)).load()
        self.assertTrue(saved["managed"])
        self.assertTrue(saved["enabled"])

    def test_disable_restores_standard_and_full_threshold(self):
        self.charge.set_enabled(True)
        # Hardware reports Long_Life after enable; tests rewrite the attribute as the raw write.
        write_attr(
            self.sys_root / "class/power_supply/BAT0/charge_types",
            "Standard Fast [Long_Life]",
        )
        status = self.charge.set_enabled(False)
        self.assertTrue(status["success"])
        self.assertFalse(status["enabled"])
        self.assertEqual(self.types_text(), "[Standard] Fast Long_Life")
        self.assertEqual(self.conservation_text(), "0")
        self.assertEqual(self.threshold_text(), "100")

    def test_disable_restores_fast_when_that_was_active(self):
        write_attr(
            self.sys_root / "class/power_supply/BAT0/charge_types",
            "Standard [Fast] Long_Life",
        )
        self.charge.probe()
        self.charge.set_enabled(True)
        write_attr(
            self.sys_root / "class/power_supply/BAT0/charge_types",
            "Standard Fast [Long_Life]",
        )
        self.charge.set_enabled(False)
        self.assertEqual(self.types_text(), "Standard [Fast] Long_Life")
        self.assertEqual(self.charge.settings["normal_mode"], "Fast")

    def test_unmanaged_reapply_does_not_write(self):
        before = self.types_text()
        self.charge.reapply()
        self.assertEqual(self.types_text(), before)
        self.assertFalse(self.charge.settings["managed"])

    def test_managed_reapply_restores_after_firmware_reset(self):
        self.charge.set_enabled(True)
        write_attr(
            self.sys_root / "class/power_supply/BAT0/charge_types",
            "[Standard] Fast Long_Life",
        )
        write_attr(self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode", "0")
        write_attr(self.sys_root / "class/power_supply/BAT0/charge_control_end_threshold", "100")
        status = self.charge.reapply()
        self.assertTrue(status["enabled"])
        self.assertEqual(self.conservation_text(), "1")
        self.assertEqual(self.threshold_text(), "80")

    def test_conservation_fallback_when_charge_types_missing(self):
        (self.sys_root / "class/power_supply/BAT0/charge_types").unlink()
        (self.sys_root / "class/power_supply/BAT0/charge_control_end_threshold").unlink()
        write_attr(self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode", "1")
        status = self.charge.probe()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["backend"], "conservation_mode")
        self.charge.set_enabled(False)
        self.assertEqual(self.conservation_text(), "0")

    def test_threshold_fallback(self):
        (self.sys_root / "class/power_supply/BAT0/charge_types").unlink()
        (self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode").unlink()
        write_attr(self.sys_root / "class/power_supply/BAT0/charge_control_end_threshold", "80")
        status = self.charge.probe()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["backend"], "charge_control_end_threshold")
        self.charge.set_enabled(False)
        self.assertEqual(self.threshold_text(), "100")

    def test_mode_conflict_uses_conservation(self):
        types_path = self.sys_root / "class/power_supply/BAT0/charge_types"
        write_attr(self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode", "1")
        original_read = ChargeController._read_text

        def flaky_read(controller, path, limit=512):
            if path == types_path:
                raise OSError(errno.EINVAL, "Invalid argument")
            return original_read(controller, path, limit)

        with mock.patch.object(ChargeController, "_read_text", flaky_read):
            status = self.charge.probe()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["backend"], "conservation_mode")

    def test_mode_conflict_write_selects_long_life(self):
        types_path = self.sys_root / "class/power_supply/BAT0/charge_types"
        write_attr(self.sys_root / "bus/platform/devices/VPC2004:00/conservation_mode", "1")
        original_read = ChargeController._read_text
        reads = {"types": 0}

        def conflict_once(controller, path, limit=512):
            if path == types_path:
                reads["types"] += 1
                if reads["types"] == 1:
                    raise OSError(errno.EINVAL, "Invalid argument")
            return original_read(controller, path, limit)

        with mock.patch.object(ChargeController, "_read_text", conflict_once):
            status = self.charge.set_enabled(True)
        self.assertTrue(status["success"])
        self.assertTrue(status["enabled"])
        self.assertEqual(self.types_text(), "Standard Fast [Long_Life]")

    def test_skips_hid_controller_battery(self):
        status = self.charge.probe()
        self.assertEqual(status["capacity"], 73)

    def test_wrong_product_is_rejected(self):
        other = KernelChargeController(
            store=JsonStore(str(self.root / "other.json")),
            sys_root=make_sys(self.root / "other", product="83E1"),
            product="83E1",
        )
        status = other.probe()
        self.assertFalse(status["go2"])
        self.assertFalse(status["ready"])
        self.assertIn("Legion Go 2", status["reason"])

    def test_companion_detection(self):
        plugins = self.root / "plugins"
        ours = plugins / "LegionGo2ChargeLimit"
        theirs = plugins / "LegionGo2Companion"
        ours.mkdir(parents=True)
        theirs.mkdir()
        (theirs / "plugin.json").write_text('{"name": "Legion Go 2 Companion"}\n')
        self.assertTrue(companion_present(str(ours)))
        self.assertFalse(companion_present(str(self.root / "empty" / "x")))

    def test_sanitize_rejects_unknown_mode(self):
        clean = sanitize_settings({"normal_mode": "Turbo", "enabled": "yes", "managed": True})
        self.assertEqual(clean["normal_mode"], "Standard")
        self.assertFalse(clean["enabled"])
        self.assertTrue(clean["managed"])

    def test_settings_round_trip(self):
        self.charge.set_enabled(True)
        restored = KernelChargeController(
            store=JsonStore(str(self.store_path)),
            sys_root=self.sys_root,
            product="83N0",
        )
        self.assertTrue(restored.settings["managed"])
        self.assertTrue(restored.settings["enabled"])

    def test_parse_rejects_malformed(self):
        with self.assertRaises(ChargeError):
            parse_charge_types("Standard [Fast Long_Life")
        with self.assertRaises(ChargeError):
            parse_charge_types("")

    def test_parse_single_written_mode(self):
        options, current = parse_charge_types("Long_Life")
        self.assertEqual(current, "Long_Life")
        self.assertEqual(options, ["Long_Life"])


if __name__ == "__main__":
    unittest.main()
