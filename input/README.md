# Input Directory — Place Your Modded HyperOS Camera Files Here

Drop your modded APK(s) and any optional supporting files into the folders below, then run `./build.sh`.

## Directory Layout

| Folder | Required? | Destination in Module | Description |
| --- | --- | --- | --- |
| `input/apk/` | **Required** | `/system/priv-app/MiuiCamera/` | Place your modded `MiuiCamera.apk` here. If you also have `MiuiExtraPhoto.apk` (Leica watermark / AI portrait / clone) or `MiuiGallery.apk`, drop them here too — the builder auto-detects each APK by its manifest package name. |
| `input/lib64/` | Optional | `/system/lib64/` | Extra 64-bit shared libraries (`.so`) required by your modded camera build. *(Note: JNI `.so` files already bundled inside the APK are extracted automatically into `priv-app/<App>/lib/arm64/`.)* |
| `input/framework/` | Optional | `/system/framework/` | Extra `.jar` framework libraries (e.g., `miuicamera.jar`, `xiaomi-camera-extensions.jar`). Matching `<library>` XMLs in `input/etc/permissions/` are also picked up. |
| `input/etc/` | Optional | `/system/etc/` | Extra `/system/etc/` files (for example `device_features/<codename>.xml`, `public.libraries-xiaomi.txt`, or custom camera XMLs). Subfolder structure is preserved. |
| `input/vendor/` | Optional | `/system/vendor/` (mapped to `/vendor/`) | Optional vendor libraries or camera configs (e.g., `lib64/*.so`, `etc/camera/*`). Only include vendor blobs if your AOSP ROM does not already bundle them. |

## APK Auto-Detection Rules

When you run `./build.sh`, the builder inspects every `.apk` in `input/apk/` using its binary `AndroidManifest.xml`:

1. **Primary Camera APK**: Automatically matched if its package name is `com.android.camera` (or starts with `com.android.camera`) or if its filename contains `MiuiCamera` / `HyperOSCamera` / `Camera`, or if it is the sole APK in `input/apk/`.
2. **ExtraPhoto / Watermark Companion**: Matched if package name is `com.miui.extraphoto` or filename contains `ExtraPhoto`, placed in `/system/priv-app/MiuiExtraPhoto/`.
3. **Additional Companion APKs** (e.g., Gallery, Scanner): Automatically placed in `/system/priv-app/<ApkName>/` with matching privapp-permissions and hiddenapi allowlists.

## Quick Example

```bash
cp ~/Downloads/MiuiCamera_HyperOS_Mod.apk input/apk/MiuiCamera.apk
./build.sh
# Output: dist/HyperOS-Cam-AOSP-<version>-universal.zip
```
