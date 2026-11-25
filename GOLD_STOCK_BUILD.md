# Gold-Stock — HyperOS Camera Port for AOSP Android 16

**Input:** `Gold-Stock` APK (182,801,464 bytes, Drive ID `1h7ugBvYZAJ05lIqT63opXrOt5dztcGMX`) — HyperOS / Leica Camera (`com.android.camera`).
**Output:** `Gold-Stock.zip` — Magisk / KernelSU / KernelSU-Next / APatch flashable module.
**Target:** AOSP Android 16 (API 36), also supports Android 14–15 (API 34–36). Generic device profile.
**Patches:** All 72 patches from `patches/unlock_features.json` applied via `tools/patch_apk.py` (apktool `--no-res` → smali patch → rebuild → zipalign → apksigner v1+v2+v3).

## Why GitHub Actions?

`Gold-Stock` is 182 MB — larger than the 100 MB Drive-connector download limit in this sandbox, and patching needs `apktool` + Java + Android SDK `build-tools` (`zipalign`, `apksigner`, `aapt2`) which are not installed here. The repo workflow (`.github/workflows/build-module.yml`) already downloads the APK directly from Drive in the runner and does the full patch → build → release flow. So the build is triggered via `workflow_dispatch`, not locally.

## Build (triggered)

```bash
gh workflow run build-module.yml \
  --ref arena/47dba66b-hyperos-port-camera-aosp \
  -f gdrive_file_id=1h7ugBvYZAJ05lIqT63opXrOt5dztcGMX \
  -f apply_patches=true \
  -f patch_spec=patches/unlock_features.json \
  -f publish_release=true
```

What the runner does:
1. Downloads `Gold-Stock` from Drive (`curl` → `gdown` fallback), detects APK payload → `input/apk/MiuiCamera.apk`.
2. `tools/patch_apk.py` — applies 72 patches, rebuilds + signs, verifies `versionName == "-Universal Cam Evorez"`.
3. `./build.sh` — packages `dist/Gold-Stock-<version>-universal.zip` + `.sha256`, validates (shell syntax, LF, XML, permissions).
4. Publishes GitHub Release `build-<UTC timestamp>` with ZIP + checksum + patch report.

Final delivery is renamed to **`Gold-Stock.zip`** and uploaded back to Drive.

## Local validation (no APK needed)

```bash
python3 -m unittest discover -s tests -v
./build.sh --allow-no-apk --output dist/template-dryrun.zip
```

## Flash on phone (Android 16 AOSP, rooted)

1. Copy `Gold-Stock.zip` to phone.
2. Magisk / KernelSU / KernelSU-Next / APatch → Modules → Install from storage → select ZIP → reboot.
3. Open Camera — HyperOS/Leica camera should appear. Tap module **Action** button anytime for health check / permission re-grant / cache clear.
4. If camera FCs: check `/data/adb/modules/hyperos_cam_aosp/logs/boot.log` and `adb logcat -d | grep -iE "AndroidRuntime|MiuiCamera|UnsatisfiedLinkError|avc: denied"`.

## Config (this build)

- `config.env`: `OUTPUT_PREFIX="Gold-Stock"`, `MODULE_NAME="Gold-Stock HyperOS Camera"`, `MIN_ANDROID_API=34`, `MAX_ANDROID_API=36`, `REPLACE_POLICY=conflict_only`, `EXTRACT_APK_LIBS=auto`, `AUTO_GRANT_PERMISSIONS=true`.
- Device profile: `devices/generic/` (aux expose, MFNR, 100% JPEG, ZSL).
- On-phone override (no rebuild): create `/sdcard/hyperos_cam.conf` with `REPLACE_POLICY=replace_aosp` if you want stock AOSP camera hidden too.
