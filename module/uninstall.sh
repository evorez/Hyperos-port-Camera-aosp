#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Clean Uninstall Script (uninstall.sh)
# ==============================================================================

MODDIR="${0%/*}"
[ -f "$MODDIR/module_config.sh" ] && . "$MODDIR/module_config.sh"
: "${CAM_PKG:=com.android.camera}"

# Clear Android package cache and camera ART/dalvik caches so AOSP restores cleanly
rm -rf /data/system/package_cache/* 2>/dev/null
rm -rf /data/dalvik-cache/arm64/*MiuiCamera* 2>/dev/null
rm -rf /data/dalvik-cache/arm/*MiuiCamera* 2>/dev/null
rm -rf /data/resource-cache/*MiuiCamera* 2>/dev/null
rm -rf "/data/user/0/${CAM_PKG}/cache/"* 2>/dev/null

exit 0
