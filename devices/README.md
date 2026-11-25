# Multi-Device Overlay Profiles

This module works generically on any ARM64 Android 14–16 AOSP ROM, and also supports **per-device overlays** matched automatically at flash time using `ro.product.vendor.device` / `ro.product.device`.

## How It Works

1. Every directory inside `devices/` (except `_template`) is bundled into the flashable ZIP.
2. When you flash the module in Magisk / KernelSU / APatch:
   - `customize.sh` detects your device codename (for example `marble`, `peridot`, `fuxi`, `mondrian`, `garnet`, `aurora`, etc.).
   - If `devices/<codename>/` exists, its `device.prop` and optional `system/` overlay files are activated.
   - Otherwise, `devices/generic/device.prop` is used.
   - Unused device folders are removed from the installed module directory to keep storage clean.

## Adding a Device Profile

1. Copy `devices/_template` to `devices/<codename>` (lowercase codename matching `getprop ro.product.vendor.device`):
   ```bash
   cp -r devices/_template devices/marble
   ```
2. Edit `devices/marble/device.prop` with any device-specific camera/Leica/watermark properties.
3. Optionally place device-specific files under `devices/marble/system/` (for example `devices/marble/system/etc/device_features/marble.xml`).
4. Rebuild the ZIP with `./build.sh`.

## Runtime User Overrides (No Rebuild Required)

Users can also place a plain key=value file at `/sdcard/hyperos_cam.conf` before flashing to override installer options:

```ini
REPLACE_POLICY=replace_aosp
AUTO_GRANT_PERMISSIONS=true
DEVICE_PROFILE=marble
```
