# Gold-Stock — HyperOS Camera for AOSP Android 16

**Deliverable:** [`Gold-Stock.zip`](https://github.com/evorez/Hyperos-port-Camera-aosp/releases/latest/download/Gold-Stock.zip) — Magisk / KernelSU / KernelSU-Next / APatch flashable module (193 MB, 57 entries).
**Release tag (current):** `build-20261125-132712` · SHA-256 `dc16bc689b590c50289640b23afaf1579e2c1d159cb95d8e2a2e17ef4bee912d`
**Diagnosed so far:** v1 copied `libmialgo*` only → still aborted; v2 added a dependency closure but skipped `/vendor|/odm` deps (bug); v3 (current) adds the `public.libraries.txt` overlay, copies the real private vendor deps, and enables bionic `debug.ld` so the exact `dlerror` is logged
**Earlier attempts:** `build-20261125-124558` (algo blobs only), `build-20261125-123507` (stock), `build-20261125-122038` (patched)
**Previous:** `build-20261125-123507` (stock, crashed on launch — libmialgoengine.so), `build-20261125-122038` (patched, crashed on launch)
**Previous build (patched, crashes on launch):** `build-20261125-122038`

## Confirmed crash root cause (device diag bundle, 2026-11-25)

Device: `gold` (2312DRAABI, MediaTek) · LineageOS 23 · Android 16 / API 36 · `userdebug`.

```text
Abort message: 'No pending exception expected: java.lang.RuntimeException: open libmialgoengine.so failed!
  at int com.xiaomi.engine.MiCamAlgoInterfaceJNI.init(java.lang.String)
  at void com.xiaomi.engine.MiCameraAlgo.init(android.content.Context)
  #07 /system/priv-app/MiuiCamera/lib/arm64/libcamera_algoup_jni.xiaomi.so (Java_com_xiaomi_engine_MiCamAlgoInterfaceJNI_init)
```

The stock HyperOS camera `dlopen()`s **libmialgoengine.so** from the vendor partition; on LineageOS the app's linker namespace cannot reach it, so ART aborts the process ~5 s after launch. This was **not** caused by the smali patch (the stock build reproduced it).

**Fix iterations:** v1 copied only `libmialgo*` blobs (still aborted — the blobs' own vendor dependencies were unreachable). v2 (current) bridges `libmialgo*.so`, `libcamera_algoup*.so`, `libcam_algoup*.so`, `libmtkcam_algo*.so`, `libcam.algo*.so` from `/vendor/lib64`, `/odm/lib64`, `/system/vendor/lib64`, `/vendor/lib64/camera`, `/odm/lib64/camera`, `/system_ext/lib64`, `/product/lib64` into `priv-app/MiuiCamera/lib/arm64` (and `/system/lib64`), then walks each blob's `DT_NEEDED` sonames and copies the missing vendor dependencies too (3 passes), logging everything to `<module>/algo_bridge.log` (printed by the Action button).

If the audit (`Action` button → "algo library audit") shows no `libmialgo*` blobs on the ROM, the blobs were stripped: pull `libmialgoengine.so` (+ `libmialgo_basic.so`, `libmialgo_utils.so`, `libmialgo_noise.so`) out of a HyperOS ROM for `gold` into `input/lib64/` and rebuild.

> Patch policy: `apply_patches=false` for now. The 60fps smali patch (`patches/gold_stock_61.json`) is unused until the camera actually launches.

**PHASE1_TAG:** `build-20261125-122038` · **SHA-256:** `2d3f1997df46d6e5bc722953b9f30a1ce6f4ba80d1ea18c10162c12ac3779c70`

**Google Drive release copy (current):**
- [`Gold-Stock.zip`](https://drive.google.com/file/d/1fdMigINoSS2uAXp0BZe22v_l_R3hj1jv/view?usp=drivesdk) — id `1fdMigINoSS2uAXp0BZe22v_l_R3hj1jv`, 202,748,685 bytes (public.libraries overlay + private vendor dep closure + linker debug)
- [`Gold-Stock.zip.sha256`](https://drive.google.com/file/d/1aGUY_mBPToHcGEPUSR1VXiuno9f5LLBf/view?usp=drivesdk) — id `1aGUY_mBPToHcGEPUSR1VXiuno9f5LLBf`
- Both are **private (owner-only)**, matching the existing `HyperOS-Cam-AOSP-...-universal.zip` on your Drive — share as "anyone with the link" when you publish.
**Source APK:** Drive `Gold-Stock` (id `1h7ugBvYZAJ05lIqT63opXrOt5dztcGMX`, 182,801,464 bytes) → `com.android.camera`, **versionName 6.1.000660.1** (code 610006601), minSdk 29 / targetSdk 34.
**Target:** AOSP Android 16 (API 36); module declares API 34–36. Generic device profile.

---

## Verified build facts (from the `verify-release` job)

| Check | Result |
|---|---|
| Published SHA-256 vs downloaded ZIP | ✅ match |
| Module structure (`validate_module.py`) | ✅ pass (shell syntax, LF endings, XML, privapp permissions vs bundled APK) |
| `module.prop` | `id=hyperos_cam_aosp`, `name=Gold-Stock HyperOS Camera`, `version=v6.1.000660.1`, `versionCode=610006601` |
| Bundled APK | `system/priv-app/MiuiCamera/MiuiCamera.apk` — `com.android.camera` 6.1.000660.1 |
| Native libs extracted to `lib/arm64` | 41 `.so` |
| Permission / sysconfig XMLs | 3 (`privapp-permissions-miuicamera.xml`, `hiddenapi-…`, `power-whitelist`) |
| 60fps AOSP fix in shipped DEX | ✅ `modify/AdditionalToolsConfig` + `get60FpsOperatingMode` present in `classes.dex`/`classes6.dex` |

Re-run any time (no Drive access needed):

```bash
gh workflow run build-module.yml --ref <branch> -f verify_tag=build-20261125-122038
```

---

## What was patched, and what could not be

The Drive `Gold-Stock` APK is the **stock HyperOS 6.1 camera**, not the Evorez/Universal modded **5.0** base that `patches/unlock_features.json` (the 72-patch list) was written against. `tools/smali_recon.py` decompiled the APK (37,346 smali files) and proved that most of those 72 patches target symbols that simply do not exist in this build:

| Patch group from the 72-patch list | Status on Gold-Stock 6.1 | Evidence |
|---|---|---|
| 60fps video freeze (HSR60 opmode `0x803c`) | ✅ **applied** — `smali/b3/a.smali` now calls `AdditionalToolsConfig.get60FpsOperatingMode()` (default `0x0` = AOSP opmode) | symbol present in shipped DEX |
| Evorez frosted-glass UI card, Additional Tools panel, Telegram/GitHub pills | ⚠️ classes compiled and shipped, but the settings-screen injection cannot be anchored (6.1 has different preference IDs and no `ph()`/`pref_universal` rows) → not wired into the UI | `.field`/`pref_*` scan |
| Disable "Universal Settings" / remove Lab, Universal, HolyBear rows | ❌ N/A — 6.1 stock has no `isSupportUniversalSettings`, `pref_universal`, `pref_lab` | recon: **no match** |
| Remove HolyBear / Sevtinge / ItzDFPlayer credit classes | ❌ N/A — strings `HolyBear`, `sevtinge`, `itzdfplayer` absent | recon: **no match** |
| Telegram link → `t.me/evorezlabs` | ❌ N/A — no `t.me/*` string exists in the stock APK | recon: **no match** |
| `MI PHONE` watermark → `Leica` | ❌ N/A — `MI PHONE` appears 0× in smali and 0× in `resources.arsc` (the build already ships Leica branding, 1,472 `Leica` arsc strings) | recon: **no match** |
| Scrub legacy credits in `resources.arsc` | ❌ N/A — no legacy credit strings present | recon |
| Device feature flags (`com/mi/device/Common.smali`: 4K UHD, HEIC, AI OCR, MFNR, LDC, 100x zoom, 0.5x UW…) | ❌ N/A as APK patches — that class does not exist in 6.1; gating lives in obfuscated config readers (`ca/*`, `b7/f0`) keyed by strings such as `isSupportDepurple`, `supportVideoSAT`, `is1080P60FpsEISSupported`. No JSON feature config inside `assets/` either (scanned all 87 assets). | recon |
| JPEG quality 100 | ✅ already 100 in the APK (`ReprocessData.DEFAULT_JPEG_QUALITY = 0x64`) **and** enforced by module props (`jpeg.quality=100`) | recon + `devices/generic/device.prop` |

The camera-relevant unlocks therefore happen at **module level** (AOSP compatibility layer), which is what makes a HyperOS camera survive on AOSP at all:

- `privapp-permissions-miuicamera.xml` auto-generated from the APK manifest (no `ro.control_privapp_permissions` bootloop).
- 41 JNI libs extracted to `/system/priv-app/MiuiCamera/lib/arm64`.
- `vendor.camera.aux.packagelist` / `privapp.list` appended non-destructively (aux lens registration alongside GCam).
- Generic profile props: `expose.aux=1`, `enableAdvanceFeatures=0x347`, `mfnr=1`, `jpeg.quality=100`, `zsl.mode=1`.
- Runtime permission auto-grant, hidden-API allowlist, power whitelist, `action.sh` health check.

> ⚠️ If you actually want the full Evorez 72-patch feature set (100x zoom, Leica watermark swap, 4K/1080p60 unlocks, 65 Mbps bitrates), it ships as the **5.0-based** `HyperOS-Cam-AOSP-Universal_Cam_Evorez-universal.zip` already in your Drive (SHA-256 `513e39f5…`). Those patches are written against that base, not against Gold-Stock 6.1.

---

## How the build runs

`Gold-Stock` is 182 MB — over the connector's 100 MB download cap and patching needs `apktool` + JDK + Android SDK, so the build runs on GitHub Actions:

```bash
gh workflow run build-module.yml --ref <branch> \
  -f gdrive_file_id=1h7ugBvYZAJ05lIqT63opXrOt5dztcGMX \
  -f apply_patches=true \
  -f patch_spec=patches/gold_stock_61.json \
  -f publish_release=true \
  -f output_name=Gold-Stock.zip
```

Pipeline: robust Drive download (`tools/gdrive_download.py`, handles the >100 MB confirm-form flow) → `apktool d --no-res` → `patches/gold_stock_61.json` (javac/d8 for `modify/*`, smali opmode fix) → `apktool b` → zipalign → apksigner v1+v2+v3 → `./build.sh --output dist/Gold-Stock.zip` → validate → GitHub Release.

**Drive sharing note:** the runner downloads anonymously, so the APK must be temporarily shared as *anyone with the link → reader*. It was made public for the build and **set back to private** afterwards.

## Flash it

1. Download `Gold-Stock.zip` (link at the top) and copy it to the phone.
2. Magisk / KernelSU / KernelSU-Next / APatch → **Modules → Install from storage** → pick the ZIP → reboot.
3. Open the camera. Tap the module **Action** button any time for a health check, permission re-grant, or cache clear.
4. If the camera FCs: `adb logcat -d | grep -iE "AndroidRuntime|MiuiCamera|UnsatisfiedLinkError|avc: denied"` and check `/data/adb/modules/hyperos_cam_aosp/logs/boot.log`.
   - `UnsatisfiedLinkError` → drop the missing `.so` into `input/lib64/` and rebuild.
   - Aux lenses missing → add vendor HAL props to `devices/<codename>/device.prop` and rebuild.
   - Want a stock AOSP camera hidden as well → create `/sdcard/hyperos_cam.conf` with `REPLACE_POLICY=replace_aosp` and re-flash (no rebuild needed).

## Repo tools added for this port

| Tool | Purpose |
|---|---|
| `tools/gdrive_download.py` | Multi-strategy Drive downloader (direct, confirm-form POST for >100 MB, gdown fallback) with CI annotations. |
| `tools/smali_recon.py` | Decompiles an APK and greps smali + assets + `resources.arsc` against `patches/recon_probes.json` to discover real anchors for a new camera version. |
| `patches/gold_stock_61.json` | The 6.1-tailored patch spec used for this build (recon-verified anchors). |
| `verify_tag` workflow input | Downloads a published release, checks SHA-256 + module structure + bundled APK + patch symbols, reports as annotations/comment. |
