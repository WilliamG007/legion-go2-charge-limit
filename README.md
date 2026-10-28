# Legion Go 2 Charge Limit

A small [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the Lenovo Legion Go 2 (`83N0` / `83N1`) on SteamOS.

**Tested on:** SteamOS 3.9, Legion Go 2.

SteamOS exposes Charge Limit in Desktop Mode (Power Management → Advanced → Battery protection). Game Mode does not. This plugin is that same on/off control in the Quick Access overlay.

On: Lenovo Long Life / conservation, so charging stops at about **80%**.
Off: Standard charging to **100%**.

It writes the kernel interfaces Desktop Mode uses:

- `/sys/class/power_supply/BAT*/charge_types` (`Long_Life` / `Standard`)
- `/sys/bus/platform/devices/VPC2004:00/conservation_mode` when present
- `charge_control_end_threshold` when present

The firmware owns the 80% figure. The plugin does not drain a battery that is already above the limit.

## Install

1. Download `LegionGo2ChargeLimit.zip` from [Releases](https://github.com/WilliamG007/legion-go2-charge-limit/releases).
2. Copy the zip onto the Legion Go 2 internal disk, for example `/home/deck/` (Desktop Mode). Game Mode does not show USB sticks or FAT/exFAT SD cards in Decky’s file picker.
3. Game Mode → Quick Access → Decky → gear → **General** → enable Developer mode.
4. **Developer** → Install Plugin from ZIP → **Home** → the zip.
5. Reload Decky if the plugin does not appear.

Uninstall **Legion Go 2 Companion**, or turn off its Battery module, if you use that plugin. Both write the same charging mode.

## First check

- Toggle Charge Limit on while plugged in: charging stops around 80%, status shows Not charging.
- Toggle it off: charging resumes toward 100%.
- The Desktop Mode Power Management checkbox matches the Game Mode toggle.

## License

BSD-3-Clause
