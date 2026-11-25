# APK Smali & Asset Patches (`patches/`)

Declarative JSON patch specs applied to `MiuiCamera.apk` via `tools/patch_apk.py` (using `apktool d --no-res` → safe smali/asset patching → `apktool b` → `zipalign -p 4` → `apksigner sign` v1+v2+v3).

## Included Patch Specs

- **`patches/unlock_features.json`** — 17 verified, hardware-safe feature unlocks in `com/mi/device/Common.smali` (HEIC capture, AI OCR, Dirty Lens Detection, Picture Flaw Check, Video Sky, Video Bokeh, Live Filter Compare, Video EIS, Super EIS, 4K UHD, 4K30 EIS, 1080p60, 1080p60 EIS, Fastshot, HQ Quick Shot, HDR Depurple, Front AI).

## Running via GitHub Actions

1. Open **Actions** → **Validate & Build HyperOS Camera AOSP Module** → **Run workflow**.
2. Provide `gdrive_file_id` (or `source_zip_url` / `apk_url`).
3. Check **`apply_patches`** = `true` (default spec: `patches/unlock_features.json`).
4. Check **`publish_release`** = `true` to get a direct download link on GitHub Releases.

## Running Locally (requires `apktool`, `zipalign`, `apksigner`, `keytool`)

```bash
python3 tools/patch_apk.py input/apk/MiuiCamera.apk \
  --patch-spec patches/unlock_features.json \
  --out-apk input/apk/MiuiCamera.apk \
  --patch-report dist/patch_report.md
./build.sh
```
