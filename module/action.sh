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
echo "If the camera still crashes, capture logs with:"
echo "  adb logcat -d | grep -iE 'AndroidRuntime|MiuiCamera|com.android.camera|CameraService'"
echo "================================================"
exit 0
