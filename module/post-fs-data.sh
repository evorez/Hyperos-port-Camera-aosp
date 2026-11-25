#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Early Boot Script (post-fs-data.sh)
# Runs before Zygote starts. Dynamically merges auxiliary camera package lists
# and applies device-specific camera properties without clobbering ROM props.
# ==============================================================================

MODDIR="${0%/*}"
[ -f "$MODDIR/module_config.sh" ] && . "$MODDIR/module_config.sh"
: "${CAM_PKG:=com.android.camera}"

# Locate resetprop across Magisk, KernelSU, KernelSU-Next, and APatch
RESETPROP=""
if command -v resetprop >/dev/null 2>&1; then
  RESETPROP="resetprop"
else
  for candidate in /data/adb/magisk/resetprop /data/adb/ksu/bin/resetprop /data/adb/ap/bin/resetprop /system/bin/resetprop; do
    if [ -x "$candidate" ]; then
      RESETPROP="$candidate"
      break
    fi
  done
fi

[ -z "$RESETPROP" ] && exit 0

# Helper: append a package name to a comma-separated property if not already present
append_pkg_prop() {
  prop_name="$1"
  pkg_to_add="$2"
  [ -z "$pkg_to_add" ] && return 0

  current_val="$($RESETPROP "$prop_name" 2>/dev/null)"
  if [ -z "$current_val" ]; then
    "$RESETPROP" -n "$prop_name" "$pkg_to_add" 2>/dev/null
  else
    case ",${current_val}," in
      *",${pkg_to_add},"*)
        # Already present; preserve existing list untouched
        ;;
      *)
        "$RESETPROP" -n "$prop_name" "${current_val},${pkg_to_add}" 2>/dev/null
        ;;
    esac
  fi
}

# Dynamically register camera package(s) in AOSP & Qualcomm/MediaTek aux camera lists
for aux_prop in \
  vendor.camera.aux.packagelist \
  persist.vendor.camera.privapp.list \
  persist.sys.camera.aux.packagelist \
  camera.aux.packagelist; do
  append_pkg_prop "$aux_prop" "$CAM_PKG"
  [ -n "$EXTRA_PKG" ] && append_pkg_prop "$aux_prop" "$EXTRA_PKG"
done

# Apply active device profile properties (from devices/<codename>/device.prop or generic)
if [ -f "$MODDIR/active_device.prop" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    # Strip trailing CR if any and skip comments/blank lines
    line="$(echo "$line" | tr -d '\r')"
    case "$line" in
      ''|'#'*) continue ;;
    esac
    prop_key="${line%%=*}"
    prop_val="${line#*=}"
    if [ -n "$prop_key" ] && [ "$prop_key" != "$line" ]; then
      "$RESETPROP" -n "$prop_key" "$prop_val" 2>/dev/null
    fi
  done < "$MODDIR/active_device.prop"
fi

exit 0
