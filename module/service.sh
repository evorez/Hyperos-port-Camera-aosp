#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Late Boot Service (service.sh)
# Waits for boot completion, grants initial runtime permissions once per module
# version, and writes a lightweight diagnostic log.
# ==============================================================================

MODDIR="${0%/*}"
[ -f "$MODDIR/module_config.sh" ] && . "$MODDIR/module_config.sh"
: "${CAM_PKG:=com.android.camera}"
: "${MODULE_VERSION_CODE:=10000}"
: "${AUTO_GRANT_PERMISSIONS:=true}"

# Wait for Android boot completion (max ~120s safeguard)
TRIES=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ "$TRIES" -lt 60 ]; do
  sleep 2
  TRIES=$((TRIES + 1))
done
sleep 3

# Wait briefly for PackageManager service to respond
PM_TRIES=0
while ! pm path "$CAM_PKG" >/dev/null 2>&1 && [ "$PM_TRIES" -lt 15 ]; do
  sleep 2
  PM_TRIES=$((PM_TRIES + 1))
done

LOG_DIR="$MODDIR/logs"
mkdir -p "$LOG_DIR" 2>/dev/null
LOG_FILE="$LOG_DIR/boot.log"

grant_pkg_permissions() {
  pkg="$1"
  [ -z "$pkg" ] && return 0
  pm path "$pkg" >/dev/null 2>&1 || return 0

  for perm in \
    android.permission.CAMERA \
    android.permission.RECORD_AUDIO \
    android.permission.ACCESS_FINE_LOCATION \
    android.permission.ACCESS_COARSE_LOCATION \
    android.permission.READ_MEDIA_IMAGES \
    android.permission.READ_MEDIA_VIDEO \
    android.permission.READ_MEDIA_AUDIO \
    android.permission.READ_MEDIA_VISUAL_USER_SELECTED \
    android.permission.POST_NOTIFICATIONS \
    android.permission.READ_EXTERNAL_STORAGE \
    android.permission.WRITE_EXTERNAL_STORAGE; do
    pm grant "$pkg" "$perm" >/dev/null 2>&1
  done

  # Allow screen brightness control / media write appops used by viewfinder
  appops set "$pkg" WRITE_SETTINGS allow >/dev/null 2>&1
  appops set "$pkg" SYSTEM_ALERT_WINDOW allow >/dev/null 2>&1
}

INIT_MARKER="$MODDIR/.initialized_${MODULE_VERSION_CODE}"
if [ "$AUTO_GRANT_PERMISSIONS" = "true" ] && [ ! -f "$INIT_MARKER" ]; then
  grant_pkg_permissions "$CAM_PKG"
  [ -n "$EXTRA_PKG" ] && grant_pkg_permissions "$EXTRA_PKG"
  touch "$INIT_MARKER" 2>/dev/null
fi

# Write diagnostic summary
{
  echo "=== HYPEROS-CAM-AOSP Boot Diagnostics ==="
  echo "Timestamp   : $(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 'unknown')"
  echo "Module Ver  : ${MODULE_VERSION:-unknown} (${MODULE_VERSION_CODE})"
  echo "Device      : $(getprop ro.product.model) ($(getprop ro.product.vendor.device))"
  echo "Android     : $(getprop ro.build.version.release) (API $(getprop ro.build.version.sdk))"
  echo "SELinux     : $(getenforce 2>/dev/null || echo 'unknown')"
  echo "Camera Pkg  : $CAM_PKG"
  echo "APK Path    : $(pm path "$CAM_PKG" 2>/dev/null || echo 'NOT INSTALLED')"
  echo "Aux List    : $(getprop vendor.camera.aux.packagelist)"
  echo "PrivApp List: $(getprop persist.vendor.camera.privapp.list)"
} > "$LOG_FILE" 2>/dev/null

exit 0
