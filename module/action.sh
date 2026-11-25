#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Manager Action Script (action.sh)
# Triggered via the "Action" button in Magisk v28+, KernelSU v1.0+,
# KernelSU-Next, and APatch. Runs a live health check, re-grants permissions,
# and clears camera cache.
# ==============================================================================

MODDIR="${0%/*}"
[ -f "$MODDIR/module_config.sh" ] && . "$MODDIR/module_config.sh"
: "${CAM_PKG:=com.android.camera}"

echo "================================================"
echo "  HYPEROS-CAM-AOSP Health Check & Maintenance"
echo "================================================"
echo "Device       : $(getprop ro.product.model) ($(getprop ro.product.vendor.device))"
echo "Android      : $(getprop ro.build.version.release) (API $(getprop ro.build.version.sdk))"
echo "SELinux      : $(getenforce 2>/dev/null || echo 'unknown')"
echo "Camera Pkg   : $CAM_PKG"

PKG_PATH="$(pm path "$CAM_PKG" 2>/dev/null)"
if [ -n "$PKG_PATH" ]; then
  echo "APK Status   : OK ($PKG_PATH)"
else
  echo "APK Status   : NOT DETECTED by PackageManager!"
  echo "  Tip: Reboot once or check logcat for PackageManager/privapp errors."
fi

if [ -f "/system/etc/permissions/privapp-permissions-miuicamera.xml" ]; then
  echo "PrivApp XML  : Mounted"
else
  echo "PrivApp XML  : MISSING!"
fi

if [ -f "/system/etc/sysconfig/hiddenapi-package-miuicamera.xml" ]; then
  echo "HiddenAPI XML: Mounted"
else
  echo "HiddenAPI XML: MISSING!"
fi

echo "Aux Cam List : $(getprop vendor.camera.aux.packagelist)"
echo "PrivApp List : $(getprop persist.vendor.camera.privapp.list)"
echo ""

echo "- Re-granting essential runtime permissions..."
if [ -n "$PKG_PATH" ]; then
  for perm in \
    android.permission.CAMERA \
    android.permission.RECORD_AUDIO \
    android.permission.READ_MEDIA_IMAGES \
    android.permission.READ_MEDIA_VIDEO \
    android.permission.POST_NOTIFICATIONS; do
    pm grant "$CAM_PKG" "$perm" >/dev/null 2>&1
  done
  appops set "$CAM_PKG" WRITE_SETTINGS allow >/dev/null 2>&1
  echo "  Done."
fi

echo "- Clearing camera app cache and restarting package..."
am force-stop "$CAM_PKG" >/dev/null 2>&1
rm -rf "/data/user/0/${CAM_PKG}/cache/"* 2>/dev/null
rm -rf "/data/user/0/${CAM_PKG}/code_cache/"* 2>/dev/null
echo "  Done."
echo ""
echo "- Collecting crash diagnostics bundle..."
DIAG_DIR="/sdcard/Download"
[ -d "$DIAG_DIR" ] || DIAG_DIR="/sdcard"
DIAG_FILE="$DIAG_DIR/GoldStock-diag-$(date +%Y%m%d-%H%M%S).txt"
{
  echo "Gold-Stock diagnostics ($(date))"
  echo "device=$(getprop ro.product.model) device=$(getprop ro.product.vendor.device)"
  echo "android=$(getprop ro.build.version.release) api=$(getprop ro.build.version.sdk) rom=$(getprop ro.build.display.id)"
  echo "selinux=$(getenforce 2>/dev/null) enforcing_privapp=$(getprop ro.control_privapp_permissions)"
  echo "module_status:"
  cat "$MODDIR/module.prop" 2>/dev/null
  echo "--- pm path ---"
  pm path "$CAM_PKG" 2>/dev/null
  echo "--- package flags (priv-app / signature / enabled) ---"
  dumpsys package "$CAM_PKG" 2>/dev/null | grep -iE "flags=|versionName|codePath|granted=true|userId" | head -n 25
  echo "--- mounted module files ---"
  ls -l /system/priv-app/MiuiCamera/ 2>/dev/null | head -n 10
  ls -l /system/etc/permissions/ 2>/dev/null | grep -i camera
  echo "--- crash buffer ---"
  logcat -b crash -d -t 400 2>/dev/null
  echo "--- camera/logcat filtered ---"
  logcat -d -t 1200 2>/dev/null | grep -iE "AndroidRuntime|FATAL|MiuiCamera|com\.android\.camera|UnsatisfiedLinkError|NoClassDefFoundError|VerifyError|ClassNotFoundException|CameraService|avc: denied" | tail -n 300
  echo "--- boot log ---"
  tail -n 120 "/data/adb/modules/${MODULE_ID:-hyperos_cam_aosp}/logs/boot.log" 2>/dev/null
} > "$DIAG_FILE" 2>&1

if [ -s "$DIAG_FILE" ]; then
  echo "  Saved: $DIAG_FILE"
  echo "  Share this file (or its 'AndroidRuntime' lines) to get the exact crash reason."
else
  echo "  Could not write diagnostics file; run adb logcat instead:"
  echo "  adb logcat -d | grep -iE 'AndroidRuntime|MiuiCamera|com.android.camera|CameraService'"
fi
echo "================================================"
exit 0
