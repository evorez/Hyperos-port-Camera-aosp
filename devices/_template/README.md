# Device Profile Template

To create a profile for a specific Xiaomi / Redmi / POCO device:

1. Copy this folder to `devices/<codename>/` (e.g., `devices/peridot/`).
2. Edit `device.prop` with any `resetprop` key=value lines needed for that device.
3. Optionally create a `system/` directory here to ship device-specific files, such as:
   - `system/etc/device_features/<codename>.xml`
   - `system/vendor/etc/camera/...`
   - `system/lib64/...`
4. Run `./build.sh`.
