# HYPEROS-CAM-AOSP — Gold-Stock Build

> **Gold-Stock** = HyperOS Camera APK (`Gold-Stock`, 182 MB, Drive ID `1h7ugBvYZAJ05lIqT63opXrOt5dztcGMX`) ported for **AOSP Android 16 (API 36)**.
> Output: **`Gold-Stock.zip`** — Magisk / KernelSU / APatch flashable module (Generic AOSP profile, 72 patches applied).
> See [`GOLD_STOCK_BUILD.md`](GOLD_STOCK_BUILD.md) for exact build + flash steps.

Universal **Magisk / KernelSU / KernelSU-Next / APatch** flashable module builder & runtime framework for installing a **modded HyperOS / Leica Camera APK** on **Android 14–16 AOSP ROMs** (LineageOS, PixelOS, crDroid, EvolutionX, DerpFest, Matrixx, etc.).

## Features

- **Universal Root Support**: Works out of the box on **Magisk**, **KernelSU**, **KernelSU-Next**, and **APatch** (including live **Action** button support via `action.sh`).
- **Android 14–16 (API 34–36) Bootloop Protection**:
  - Automatically inspects your modded APK's binary `AndroidManifest.xml` (pure Python — no `aapt2` or Java needed).
  - Auto-merges every declared permission into `/system/etc/permissions/privapp-permissions-miuicamera.xml` so AOSP's `ro.control_privapp_permissions=enforce` never causes a bootloop.
  - Auto-whitelists the detected package name(s) in `/system/etc/sysconfig/hiddenapi-package-miuicamera.xml` and power/Doze allowlists.
- **Automatic Native Library Extraction**: Automatically extracts bundled `lib/arm64-v8a/*.so` JNI libraries from your modded APK into `/system/priv-app/MiuiCamera/lib/arm64/` so AOSP `PackageManager` loads them reliably even if the APK was re-zipped with compressed `.so` files.
- **Non-Destructive Auxiliary Camera Registration**: `post-fs-data.sh` dynamically appends your camera package to `vendor.camera.aux.packagelist` and `persist.vendor.camera.privapp.list` without erasing GCam or ROM camera packages already listed there.
- **Multi-Device Overlay System**: Generic AOSP defaults out of the box, plus automatic codename matching from `devices/<codename>/` at flash time.
- **Built-In Validation & Diagnostics**: Every built ZIP is checked for POSIX shell syntax, Unix LF line endings, XML well-formedness, and permission alignment before you flash it.

---

## Quick Start (3 Steps)

### 1. Place your modded APK (and optional libs)

Copy your modded HyperOS / Leica Camera APK into `input/apk/`:

```bash
cp /path/to/your/MiuiCamera.apk input/apk/MiuiCamera.apk
```

Optional extras you can also drop in if your modded camera requires them:
- `input/apk/MiuiExtraPhoto.apk` — Optional Leica watermark / AI portrait companion APK
- `input/lib64/*.so` — Extra `/system/lib64/` libraries
- `input/framework/*.jar` — Extra `/system/framework/` JARs
- `input/etc/...` — Extra `/system/etc/` configs (e.g. `device_features/<codename>.xml`, `permissions/*.xml`)
- `input/vendor/...` — Optional `/vendor/` camera libraries or configs

*(See `input/README.md` for details.)*

### 2. Build the flashable module ZIP

```bash
./build.sh
```

Or pass an APK path directly without copying it first:

```bash
./build.sh --apk /path/to/MiuiCamera_Mod.apk
```

Your verified flashable ZIP and SHA-256 checksum are written to:

```text
dist/HyperOS-Cam-AOSP-<version>-universal.zip
dist/HyperOS-Cam-AOSP-<version>-universal.zip.sha256
```

### 3. Flash on your Android 14–16 AOSP device

1. Transfer the `.zip` from `dist/` to your phone.
2. Open **Magisk**, **KernelSU**, **KernelSU-Next**, or **APatch** -> **Modules** -> **Install from storage**.
3. Select the ZIP and reboot.
4. *(Optional)* Tap the **Action** button on the module card inside your root manager anytime to run a live health check, re-grant runtime permissions, or clear camera cache.

---

## Customizing Configuration

Edit `config.env` before running `./build.sh` to adjust:
- `MODULE_ID`, `MODULE_NAME`, `MODULE_AUTHOR`, `MODULE_DESCRIPTION`
- `MODULE_VERSION` / `MODULE_VERSION_CODE` (`auto` reads directly from your APK)
- `REPLACE_POLICY`:
  - `conflict_only` *(default)* — Only replaces duplicate `MiuiCamera` folders in other partitions.
  - `replace_aosp` — Also hides stock AOSP `Aperture` / `Camera2` / `Snap`.
  - `none` — Leaves all existing camera apps untouched.
- `EXTRACT_APK_LIBS`: `auto` *(default)*, `always`, or `never`
- `AUTO_GRANT_PERMISSIONS`: `true` *(default)* or `false`

Users can also override installer behavior on the phone without rebuilding by creating `/sdcard/hyperos_cam.conf`:

```ini
REPLACE_POLICY=replace_aosp
AUTO_GRANT_PERMISSIONS=true
DEVICE_PROFILE=generic
```

---

## Adding Device-Specific Overlays

See `devices/README.md`. Copy `devices/_template` to `devices/<codename>` (e.g. `devices/marble`, `devices/peridot`, `devices/fuxi`), add any device-specific `device.prop` entries or `system/` files, and re-run `./build.sh`.

---

## Inspecting an APK & Running Tests

Inspect any APK's manifest, permissions, required libraries, and bundled `.so` files:

```bash
python3 tools/axml_inspector.py /path/to/MiuiCamera.apk
```

Run the automated test suite:

```bash
python3 -m unittest discover -s tests -v
```

---

## Troubleshooting on AOSP

If the camera opens and closes immediately (FC) on your AOSP ROM:

1. Tap the module **Action** button in Magisk / KernelSU / APatch to check mount status and clear cache.
2. Check the boot diagnostic log at `/data/adb/modules/hyperos_cam_aosp/logs/boot.log`.
3. Capture the exact crash stack trace via ADB:
   ```bash
   adb logcat -d | grep -iE "AndroidRuntime|MiuiCamera|com\.android\.camera|UnsatisfiedLinkError|CameraService|avc: denied"
   ```
   - **`UnsatisfiedLinkError`**: A required `.so` library is missing — drop it into `input/lib64/` (or `input/vendor/lib64/`) and rebuild.
   - **`NoClassDefFoundError` / missing shared library**: Drop the missing framework `.jar` into `input/framework/` and its `<library>` XML into `input/etc/permissions/`.
   - **`avc: denied`**: Add the needed SELinux rule to `module/sepolicy.rule` and rebuild.
   - **Aux lenses (ultrawide / telephoto / macro) missing**: Create `devices/<codename>/device.prop` with your device's vendor camera HAL properties and rebuild.
