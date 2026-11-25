#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Universal Installer (Magisk / KernelSU / KernelSU-Next / APatch)
# ==============================================================================

SKIPUNZIP=0

# Load build-time module configuration
[ -f "$MODPATH/module_config.sh" ] && . "$MODPATH/module_config.sh"

# Defaults if module_config.sh was absent
: "${MODULE_NAME:=HyperOS Modded Camera for AOSP}"
: "${MODULE_VERSION:=v1.0.0}"
: "${CAM_PKG:=com.android.camera}"
: "${MIN_ANDROID_API:=34}"
: "${MAX_ANDROID_API:=36}"
: "${REPLACE_POLICY:=conflict_only}"
: "${AUTO_GRANT_PERMISSIONS:=true}"

# Optional user override file on /sdcard/hyperos_cam.conf (safe key=value parser)
USER_CONF="/sdcard/hyperos_cam.conf"
if [ -f "$USER_CONF" ]; then
  while IFS='=' read -r k v; do
    case "$k" in
      ''|'#'*) continue ;;
      REPLACE_POLICY) REPLACE_POLICY="$v" ;;
      AUTO_GRANT_PERMISSIONS) AUTO_GRANT_PERMISSIONS="$v" ;;
      DEVICE_PROFILE) USER_DEVICE_PROFILE="$v" ;;
    esac
  done < "$USER_CONF"
fi

# Detect root manager
ROOT_MGR="Unknown"
if [ -n "$APATCH" ] || [ "$APATCH" = "true" ]; then
  ROOT_MGR="APatch (${APATCH_VER:-unknown})"
elif [ -n "$KSU_NEXT" ] || [ "$KSU_NEXT" = "true" ]; then
  ROOT_MGR="KernelSU-Next (${KSU_VER:-unknown})"
elif [ -n "$KSU" ] || [ "$KSU" = "true" ]; then
  ROOT_MGR="KernelSU (${KSU_VER:-unknown})"
elif [ -n "$MAGISK_VER" ]; then
  ROOT_MGR="Magisk (${MAGISK_VER} / ${MAGISK_VER_CODE:-0})"
fi

# Detect device info
SDK_VER="${API:-$(getprop ro.build.version.sdk)}"
ANDROID_REL="$(getprop ro.build.version.release)"
DEVICE_CODENAME="$(getprop ro.product.vendor.device)"
[ -z "$DEVICE_CODENAME" ] && DEVICE_CODENAME="$(getprop ro.product.device)"
[ -z "$DEVICE_CODENAME" ] && DEVICE_CODENAME="$(getprop ro.build.product)"
DEVICE_CODENAME="$(echo "$DEVICE_CODENAME" | tr 'A-Z' 'a-z')"
DEVICE_MODEL="$(getprop ro.product.model)"
ROM_FINGERPRINT="$(getprop ro.build.display.id)"

ui_print "================================================"
ui_print "  $MODULE_NAME"
ui_print "  Version : $MODULE_VERSION"
ui_print "  Package : $CAM_PKG"
ui_print "================================================"
ui_print "- Root Manager : $ROOT_MGR"
ui_print "- Device       : $DEVICE_MODEL ($DEVICE_CODENAME)"
ui_print "- Android      : $ANDROID_REL (API $SDK_VER)"
ui_print "- Architecture : ${ARCH:-unknown}"
ui_print "- ROM Build    : $ROM_FINGERPRINT"

# 1. Verify architecture (HyperOS Camera requires ARM64)
if [ "$ARCH" != "arm64" ]; then
  abort "! Unsupported architecture: $ARCH. HyperOS Camera requires arm64 (ARM64-v8a)."
fi

# 2. Verify Android API range (Android 14-16 / API 34-36)
if [ -n "$SDK_VER" ]; then
  if [ "$SDK_VER" -lt "$MIN_ANDROID_API" ]; then
    abort "! Unsupported Android API $SDK_VER. Minimum required is API $MIN_ANDROID_API (Android 14)."
  fi
  if [ "$SDK_VER" -gt "$MAX_ANDROID_API" ]; then
    ui_print "! Warning: Android API $SDK_VER is newer than tested API $MAX_ANDROID_API."
    ui_print "  Proceeding anyway; if the camera fails to launch, update the modded APK."
  fi
fi

# 3. Warn if flashed on stock MIUI / HyperOS ROM instead of AOSP
MIUI_VER="$(getprop ro.miui.ui.version.name)"
HYPEROS_VER="$(getprop ro.mi.os.version.name)"
if [ -n "$MIUI_VER" ] || [ -n "$HYPEROS_VER" ]; then
  ui_print "! Warning: MIUI/HyperOS system properties detected (${HYPEROS_VER:-$MIUI_VER})."
  ui_print "  This module is designed for AOSP ROMs. Continuing installation..."
fi

# 4. Verify primary camera APK exists in module payload
if [ ! -f "$MODPATH/system/priv-app/MiuiCamera/MiuiCamera.apk" ]; then
  abort "! MiuiCamera.apk is missing from this ZIP! Build the module with your modded APK in input/apk/ first."
fi

# 5. Apply device-specific overlay (if matched) or fallback to generic
SELECTED_PROFILE="generic"
if [ -n "$USER_DEVICE_PROFILE" ] && [ -d "$MODPATH/devices/$USER_DEVICE_PROFILE" ]; then
  SELECTED_PROFILE="$USER_DEVICE_PROFILE"
elif [ -n "$DEVICE_CODENAME" ] && [ -d "$MODPATH/devices/$DEVICE_CODENAME" ]; then
  SELECTED_PROFILE="$DEVICE_CODENAME"
fi

ui_print "- Applying device profile: $SELECTED_PROFILE"
if [ -f "$MODPATH/devices/$SELECTED_PROFILE/device.prop" ]; then
  cp -f "$MODPATH/devices/$SELECTED_PROFILE/device.prop" "$MODPATH/active_device.prop"
fi
if [ -d "$MODPATH/devices/$SELECTED_PROFILE/system" ]; then
  ui_print "- Merging device-specific system overlay files..."
  cp -af "$MODPATH/devices/$SELECTED_PROFILE/system/." "$MODPATH/system/"
fi
# Remove unused device overlay folders from installed module
rm -rf "$MODPATH/devices"

# Persist effective runtime config if overridden by /sdcard/hyperos_cam.conf
if [ -f "$USER_CONF" ]; then
  ui_print "- Applied user overrides from $USER_CONF"
  {
    echo "REPLACE_POLICY=\"$REPLACE_POLICY\""
    echo "AUTO_GRANT_PERMISSIONS=\"$AUTO_GRANT_PERMISSIONS\""
    echo "ACTIVE_DEVICE_PROFILE=\"$SELECTED_PROFILE\""
  } >> "$MODPATH/module_config.sh"
fi

# 6. Build REPLACE list dynamically for existing conflicting camera directories
REPLACE=""
add_replace_if_exists() {
  target_rel="$1"
  # Check actual mounted path on device (without leading /system for /product and /system_ext)
  case "$target_rel" in
    /system/product/*)
      real_path="${target_rel#/system}"
      ;;
    /system/system_ext/*)
      real_path="${target_rel#/system}"
      ;;
    *)
      real_path="$target_rel"
      ;;
  esac
  if [ -d "$real_path" ] || [ -d "$target_rel" ]; then
    ui_print "  -> Replacing conflicting camera dir: $target_rel"
    REPLACE="${REPLACE}
${target_rel}"
  fi
}

if [ "$REPLACE_POLICY" != "none" ]; then
  ui_print "- Checking for conflicting camera packages (policy: $REPLACE_POLICY)..."
  # Always check for pre-existing MiuiCamera directories in other partitions to avoid duplicate package scans
  for base in /system/product/priv-app /system/product/app /system/system_ext/priv-app /system/system_ext/app /system/app; do
    add_replace_if_exists "$base/MiuiCamera"
  done

  if [ "$REPLACE_POLICY" = "replace_aosp" ]; then
    for base in /system/priv-app /system/app /system/product/priv-app /system/product/app /system/system_ext/priv-app /system/system_ext/app; do
      for app in Aperture Camera2 Snap GrapheneCamera CameraGo; do
        add_replace_if_exists "$base/$app"
      done
    done
  fi
fi

# 6.5 Bridge HyperOS vendor algorithm libraries into the camera's native lib dir
# Stock HyperOS Camera 6.x loads libmialgoengine.so and friends from the vendor
# partition (com.xiaomi.engine.MiCamAlgoInterfaceJNI -> dlopen). AOSP/LineageOS
# ROMs usually still carry those blobs under /vendor/lib64 or /odm/lib64 but the
# camera app cannot reach them from its own linker namespace, which aborts the
# process at startup with "open libmialgoengine.so failed!". Copy every matching
# vendor blob into the app's private lib dir so dlopen resolves it locally.
ui_print "- Bridging HyperOS vendor algorithm libraries (libmialgo*)..."
ALGO_LIB_DIR="$MODPATH/system/priv-app/MiuiCamera/lib/arm64"
ALGO_LIB_DIR_ALT="$MODPATH/system/lib64"
ALGO_COUNT=0
ALGO_FOUND=""

for src_dir in /vendor/lib64 /odm/lib64 /system/vendor/lib64 /vendor/lib64/camera \
               /odm/lib64/camera /system_ext/lib64 /product/lib64; do
  [ -d "$src_dir" ] || continue
  for pattern in "libmialgo*.so" "libmialgo*.so.*" "libcamera_algoup*.so" \
                 "libcam_algoup*.so" "libmtkcam_algo*.so" "libcam.algo*.so"; do
    for lib in "$src_dir"/$pattern; do
      [ -f "$lib" ] || continue
      lib_name="${lib##*/}"
      [ -f "$ALGO_LIB_DIR/$lib_name" ] && continue
      mkdir -p "$ALGO_LIB_DIR" "$ALGO_LIB_DIR_ALT"
      if cp -f "$lib" "$ALGO_LIB_DIR/$lib_name" 2>/dev/null; then
        cp -f "$lib" "$ALGO_LIB_DIR_ALT/$lib_name" 2>/dev/null || true
        ALGO_COUNT=$((ALGO_COUNT + 1))
        ALGO_FOUND="$ALGO_FOUND $lib_name"
        ui_print "  + $lib_name (from $src_dir)"
      fi
    done
  done
done

if [ "$ALGO_COUNT" -gt 0 ]; then
  ui_print "  Bridged $ALGO_COUNT vendor algo lib(s):$ALGO_FOUND"
else
  ui_print "  ! No libmialgo* vendor blob found on this ROM."
  ui_print "    The stock camera aborts in MiCameraAlgo.init without libmialgoengine.so."
  ui_print "    Fix: pull libmialgoengine.so (+ libmialgo_basic/utils/noise) from your"
  ui_print "    HyperOS ROM's /vendor/lib64 and drop them into input/lib64/, then rebuild."
fi

# 7. Set strict permissions & SELinux contexts
ui_print "- Setting file permissions and SELinux contexts..."
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm_recursive "$MODPATH/system" 0 0 0755 0644 u:object_r:system_file:s0

if [ -d "$MODPATH/system/lib64" ]; then
  set_perm_recursive "$MODPATH/system/lib64" 0 0 0755 0644 u:object_r:system_lib_file:s0
fi

if [ -d "$MODPATH/system/vendor" ]; then
  set_perm_recursive "$MODPATH/system/vendor" 0 2000 0755 0644 u:object_r:vendor_file:s0
  if [ -d "$MODPATH/system/vendor/etc" ]; then
    set_perm_recursive "$MODPATH/system/vendor/etc" 0 0 0755 0644 u:object_r:vendor_configs_file:s0
  fi
fi

for script in post-fs-data.sh service.sh action.sh uninstall.sh; do
  [ -f "$MODPATH/$script" ] && set_perm "$MODPATH/$script" 0 0 0755
done

# 8. Clear Android package cache and stale MiuiCamera dalvik-cache
ui_print "- Clearing package cache to register privileged permissions..."
rm -rf /data/system/package_cache/* 2>/dev/null
rm -rf /data/dalvik-cache/arm64/*MiuiCamera* 2>/dev/null
rm -rf /data/dalvik-cache/arm/*MiuiCamera* 2>/dev/null
rm -rf /data/resource-cache/*MiuiCamera* 2>/dev/null

ui_print "================================================"
ui_print "  Universal Xiomeme-Leica Camera mod By 𝙴𝚟𝚘𝚛𝚎𝚣"
ui_print "  For more Builds: https://t.me/evorezlabs"
ui_print "  GitHub: @evorez | Web: Evorez.in"
ui_print "  Installation complete! Reboot to activate."
ui_print "================================================"

# Redirect user to Telegram channel after flash (safe no-op in recovery)
if [ -x /system/bin/am ]; then
  ui_print "- Opening https://t.me/evorezlabs ..."
  /system/bin/am start --user 0 -a android.intent.action.VIEW -d "https://t.me/evorezlabs" >/dev/null 2>&1 || \
  /system/bin/am start -a android.intent.action.VIEW -d "https://t.me/evorezlabs" >/dev/null 2>&1 || true
fi
