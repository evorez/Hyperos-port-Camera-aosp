#!/system/bin/sh
# ==============================================================================
# HYPEROS-CAM-AOSP Manager Action Script (action.sh)
# Triggered via the "Action" button in Magisk v28+, KernelSU v1.0+,
# KernelSU-Next, and APatch.
#
# v5: reproduces the camera launch, captures the *raw* linker error from a
#     cleared logcat, dumps the real DT_NEEDED tree (no readelf needed), and
#     packs everything (logs + ELF reports + the actual blobs) into one archive
#     on /sdcard so the exact cause can be proven instead of guessed.
# ==============================================================================

MODDIR="${0%/*}"
[ -f "$MODDIR/module_config.sh" ] && . "$MODDIR/module_config.sh"
: "${CAM_PKG:=com.android.camera}"
: "${MODDIR:=/data/adb/modules/hyperos_cam_aosp}"
MODULE_ID_LOCAL="${MODULE_ID:-hyperos_cam_aosp}"
APP_LIB_DIR="/system/priv-app/MiuiCamera/lib/arm64"
ELFDUMP="$MODDIR/bin/elf_needed.sh"
[ -f "$ELFDUMP" ] || ELFDUMP="/data/adb/modules/$MODULE_ID_LOCAL/bin/elf_needed.sh"

DIAG_DIR="/sdcard/Download"
[ -d "$DIAG_DIR" ] || DIAG_DIR="/sdcard"
TS="$(date +%Y%m%d-%H%M%S)"
WORK="$DIAG_DIR/.gsdiag-$TS"
rm -rf "$WORK" 2>/dev/null
if ! mkdir -p "$WORK/libs" 2>/dev/null; then
  WORK="/data/local/tmp/.gsdiag-$TS"
  mkdir -p "$WORK/libs" 2>/dev/null
fi
SUMMARY="$DIAG_DIR/GoldStock-diag-$TS.txt"
ARCHIVE="$DIAG_DIR/GoldStock-evidence-$TS.tar.gz"

echo "=================================================="
echo "  HYPEROS-CAM-AOSP Deep Diagnostics (v5)"
echo "=================================================="
echo "Device       : $(getprop ro.product.model) ($(getprop ro.product.vendor.device))"
echo "Android      : $(getprop ro.build.version.release) (API $(getprop ro.build.version.sdk))"
echo "ROM          : $(getprop ro.build.display.id)"
echo "SELinux      : $(getenforce 2>/dev/null || echo 'unknown')"
echo "Root         : $(id -u 2>/dev/null)"
echo "Camera Pkg   : $CAM_PKG"
echo ""

# ------------------------------------------------------------------------------
# 0. resetprop locator + linker debug properties
# ------------------------------------------------------------------------------
RESETPROP=""
for c in /data/adb/magisk/resetprop /data/adb/ksu/bin/resetprop /data/adb/ap/bin/resetprop; do
  [ -x "$c" ] && { RESETPROP="$c"; break; }
done
command -v resetprop >/dev/null 2>&1 && RESETPROP="resetprop"

echo "- Linker debug properties (bionic prints 'dlopen failed: …' to logcat tag 'linker')"
echo "    debug.ld.all          = $(getprop debug.ld.all)"
echo "    debug.ld.$CAM_PKG = $(getprop "debug.ld.$CAM_PKG")"
if [ -n "$RESETPROP" ]; then
  "$RESETPROP" -n debug.ld.all "dlopen,dlerror" 2>/dev/null
  "$RESETPROP" -n "debug.ld.$CAM_PKG" "dlopen,dlerror" 2>/dev/null
  echo "    -> re-applied: debug.ld.all=$(getprop debug.ld.all)"
else
  echo "    -> resetprop not found; property change skipped"
fi
echo "    NOTE: app processes are forked from zygote, so the linker reads these"
echo "          properties at zygote start. If they were not set at boot (post-fs-data)"
echo "          they only take effect after the next reboot."
echo ""

echo "- Re-granting essential runtime permissions..."
for perm in android.permission.CAMERA android.permission.RECORD_AUDIO \
            android.permission.READ_MEDIA_IMAGES android.permission.READ_MEDIA_VIDEO \
            android.permission.POST_NOTIFICATIONS; do
  pm grant "$CAM_PKG" "$perm" >/dev/null 2>&1
done
appops set "$CAM_PKG" WRITE_SETTINGS allow >/dev/null 2>&1
echo "  Done."

# ------------------------------------------------------------------------------
# 1. Reproduce the launch with a clean log buffer, capture the real linker error
# ------------------------------------------------------------------------------
echo ""
echo "- Reproducing camera launch with cleared log buffers..."
am force-stop "$CAM_PKG" >/dev/null 2>&1
rm -rf "/data/user/0/${CAM_PKG}/cache/"* 2>/dev/null
logcat -b all -c >/dev/null 2>&1 || echo "    (logcat clear failed — buffers may be polluted)"
sleep 1

ACT="$(cmd package resolve-activity --brief -c android.intent.category.LAUNCHER "$CAM_PKG" 2>/dev/null | tail -n 1)"
echo "    launcher activity: ${ACT:-<unresolved>}"
case "$ACT" in
  */*) am start -W -n "$ACT" >/dev/null 2>&1 ;;
  *)   monkey -p "$CAM_PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 ;;
esac

i=0
PID_SEEN=""
while [ "$i" -lt 12 ]; do
  sleep 1
  p="$(pidof "$CAM_PKG" 2>/dev/null)"
  [ -n "$p" ] && PID_SEEN="$p"
  i=$((i + 1))
done
echo "    pid observed: ${PID_SEEN:-<none — app never started>}"

logcat -b all -d -v threadtime > "$WORK/logcat_all.txt" 2>/dev/null
logcat -b crash -d -v threadtime > "$WORK/logcat_crash.txt" 2>/dev/null

LINKER_HITS="$WORK/linker_hits.txt"
grep -iE "linker|dlopen|dlerror|not accessible for the namespace|cannot locate symbol|is not found|needed by|library .* not found|UnsatisfiedLinkError|open libmialgo" \
  "$WORK/logcat_all.txt" 2>/dev/null | tail -n 200 > "$LINKER_HITS"
n_hits="$(grep -c . "$LINKER_HITS" 2>/dev/null)"
echo "    linker-related log lines: ${n_hits:-0}"
if [ "${n_hits:-0}" -gt 0 ]; then
  echo "    --- first 12 ---"
  head -n 12 "$LINKER_HITS" | sed 's/^/      /'
fi

# ------------------------------------------------------------------------------
# 2. ELF truth: real DT_NEEDED trees + every copy + namespace reachability
# ------------------------------------------------------------------------------
echo ""
echo "- Parsing real DT_NEEDED trees (toybox-only ELF parser)..."
SCAN_DIRS="/vendor/lib64 /odm/lib64 /system/vendor/lib64 /vendor/lib64/camera /odm/lib64/camera /vendor/lib64/hw /odm/lib64/hw /system_ext/lib64 /product/lib64 /system/lib64 $APP_LIB_DIR"
PUBLICS="/system/etc/public.libraries.txt /vendor/etc/public.libraries.txt /odm/etc/public.libraries.txt /product/etc/public.libraries.txt"

elf_needed_of() { [ -f "$ELFDUMP" ] && sh "$ELFDUMP" -n "$1" 2>/dev/null || echo ""; }

# candidate algo libs present anywhere
: > "$WORK/libs_found.txt"
for d in $SCAN_DIRS; do
  [ -d "$d" ] || continue
  ls "$d" 2>/dev/null | grep -E "^(libmialgo|libcamera_algoup|libcam_algoup|libcam\.algo|libmtkcam_algo)" | while read -r n; do
    echo "$d/$n" >> "$WORK/libs_found.txt"
  done
done
sort -u "$WORK/libs_found.txt" > "$WORK/libs_found.sorted" 2>/dev/null
mv -f "$WORK/libs_found.sorted" "$WORK/libs_found.txt" 2>/dev/null

REPORT="$WORK/needed_report.txt"
: > "$REPORT"
ALL_NEEDED="$WORK/all_needed.txt"
: > "$ALL_NEEDED"

if [ -f "$ELFDUMP" ]; then
  while read -r lib; do
    [ -f "$lib" ] || continue
    {
      echo "================================================================"
      echo "LIB      ${lib##*/}"
      echo "PATH     $lib"
      echo "SIZE     $(wc -c < "$lib" 2>/dev/null | tr -d ' ')  SHA256 $(sha256sum "$lib" 2>/dev/null | cut -d' ' -f1)"
      echo "CONTEXT  $(ls -lZ "$lib" 2>/dev/null | sed 's/  */ /g' | cut -d' ' -f5)"
    } >> "$REPORT"
    sh "$ELFDUMP" "$lib" 2>/dev/null | sed 's/^/  /' >> "$REPORT"
    for dep in $(elf_needed_of "$lib"); do
      echo "$dep" >> "$ALL_NEEDED"
      hit=""
      for d in $SCAN_DIRS; do
        if [ -f "$d/$dep" ]; then
          pub="no"
          for pf in $PUBLICS; do
            grep -qx "$dep" "$pf" 2>/dev/null && pub="yes($pf)"
          done
          hit="$hit $d/$dep[$pub]"
        fi
      done
      [ -z "$hit" ] && hit=" <<< NOT-FOUND-ANYWHERE"
      echo "  NEEDED ${dep}${hit}" >> "$REPORT"
    done
  done < "$WORK/libs_found.txt"
  sort -u "$ALL_NEEDED" > "$WORK/all_needed.sorted" 2>/dev/null
  mv -f "$WORK/all_needed.sorted" "$ALL_NEEDED" 2>/dev/null
else
  echo "  ! bin/elf_needed.sh missing from the module — no ELF parsing possible" >> "$REPORT"
fi

# which of those sonames the app's linker namespace can actually reach
LDCONF="$WORK/ld.config.txt"
if [ -f /linkerconfig/ld.config.txt ]; then
  cp -f /linkerconfig/ld.config.txt "$LDCONF" 2>/dev/null
else
  cat /system/etc/ld.config.txt /vendor/etc/ld.config.txt /odm/etc/ld.config.txt > "$LDCONF" 2>/dev/null
fi
NS_REPORT="$WORK/namespace_report.txt"
{
  echo "=== namespace reachability for the app (priv_app) ==="
  echo "--- ld.config.txt: priv_app section ---"
  grep -nE "namespace|permitted.paths|search.paths|isolated" "$LDCONF" 2>/dev/null | head -n 40
  echo ""
  echo "--- where each NEEDED soname exists (app lib dir = app namespace search path) ---"
  while read -r dep; do
    [ -z "$dep" ] && continue
    inapp="no"
    [ -f "$APP_LIB_DIR/$dep" ] && inapp="YES"
    sysonly=""
    for d in /system/lib64 /system_ext/lib64 /product/lib64; do
      [ -f "$d/$dep" ] && sysonly="$sysonly $d"
    done
    vend=""
    for d in /vendor/lib64 /odm/lib64; do
      [ -f "$d/$dep" ] && vend="$vend $d"
    done
    echo "$dep  appdir=$inapp  system:$sysonly  vendor:$vend"
  done < "$ALL_NEEDED"
} > "$NS_REPORT" 2>&1

echo "    parsed $(grep -c '^LIB ' "$REPORT" 2>/dev/null) lib(s), $(grep -c '^NEEDED ' "$REPORT" 2>/dev/null) NEEDED edges"

# ------------------------------------------------------------------------------
# 3. Collect the actual blobs so the ELF facts can be verified off-device
# ------------------------------------------------------------------------------
echo "- Collecting the blobs themselves (for symbol/version analysis)..."
copied=0; bytes=0
while read -r lib; do
  [ -f "$lib" ] || continue
  case "$lib" in
    "$APP_LIB_DIR"/*) ;;                # already have the closure copy under libs/
  esac
  sz=$(wc -c < "$lib" 2>/dev/null | tr -d ' ')
  [ "${sz:-0}" -gt 4000000 ] && continue
  [ "$copied" -ge 40 ] && break
  cp -f "$lib" "$WORK/libs/${lib##*/}" 2>/dev/null && copied=$((copied + 1)) && bytes=$((bytes + sz))
done < "$WORK/libs_found.txt"

# ------------------------------------------------------------------------------
# 4. Whole-system context dump
# ------------------------------------------------------------------------------
{
  echo "Gold-Stock deep diagnostics ($(date))"
  echo "device=$(getprop ro.product.model) codename=$(getprop ro.product.vendor.device)"
  echo "android=$(getprop ro.build.version.release) api=$(getprop ro.build.version.sdk) rom=$(getprop ro.build.display.id)"
  echo "selinux=$(getenforce 2>/dev/null) privapp_mode=$(getprop ro.control_privapp_permissions)"
  echo "root_uid=$(id -u)"
  echo "debug.ld.all=$(getprop debug.ld.all)"
  echo "debug.ld.$CAM_PKG=$(getprop "debug.ld.$CAM_PKG")"
  echo ""
  echo "--- install health ---"
  echo "APK        : $(pm path "$CAM_PKG" 2>/dev/null | head -n1 || echo NOT-DETECTED)"
  echo "privapp xml: $([ -f /system/etc/permissions/privapp-permissions-miuicamera.xml ] && echo mounted || echo MISSING)"
  echo "hiddenapi  : $([ -f /system/etc/sysconfig/hiddenapi-package-miuicamera.xml ] && echo mounted || echo MISSING)"
  echo "aux pkglist: $(getprop vendor.camera.aux.packagelist)"
  echo "privapp lst: $(getprop persist.vendor.camera.privapp.list)"
  echo ""
  echo "--- module.prop ---"
  cat "$MODDIR/module.prop" 2>/dev/null
  echo ""
  echo "--- pm path / package ---"
  pm path "$CAM_PKG" 2>/dev/null
  dumpsys package "$CAM_PKG" 2>/dev/null | grep -iE "versionName|codePath|nativeLibrary|flags=|primaryCpuAbi|extractNativeLibs" | head -n 15
  echo ""
  echo "--- app native lib dir ($APP_LIB_DIR) ---"
  ls -lZ "$APP_LIB_DIR" 2>/dev/null | head -n 80
  echo ""
  echo "--- module overlay of the app dir (magic mount) ---"
  ls -l /data/adb/modules/$MODULE_ID_LOCAL/system/priv-app/MiuiCamera/lib/arm64/ 2>/dev/null | head -n 40
  echo ""
  echo "--- public.libraries.txt files (is our overlay mounted?) ---"
  for pf in $PUBLICS; do
    [ -f "$pf" ] || continue
    echo "$pf: $(wc -l < "$pf" 2>/dev/null) lines, mialgo entries:"
    grep -iE "mialgo|algoup" "$pf" 2>/dev/null | sed 's/^/    /'
  done
  echo "module ships /system/vendor/etc/public.libraries.txt: $([ -f /data/adb/modules/$MODULE_ID_LOCAL/system/vendor/etc/public.libraries.txt ] && echo yes || echo no)"
  echo ""
  echo "--- install-time algo bridge log ---"
  cat "/data/adb/modules/$MODULE_ID_LOCAL/algo_bridge.log" 2>/dev/null | tail -n 60
  echo ""
  echo "--- SELinux denials (camera / linker / vendor_file) ---"
  grep "avc:" "$WORK/logcat_all.txt" 2>/dev/null | grep -iE "camera|mialgo|algoup|vendor_file|system_file" | tail -n 20
  echo "(denial count total: $(grep -c 'avc: *denied' "$WORK/logcat_all.txt" 2>/dev/null))"
  echo ""
  echo "--- crash buffer (raw) ---"
  cat "$WORK/logcat_crash.txt" 2>/dev/null | tail -n 120
  echo ""
  echo "--- linker / dlopen lines (raw) ---"
  cat "$LINKER_HITS" 2>/dev/null
  echo ""
  echo "--- DT_NEEDED report ---"
  cat "$REPORT" 2>/dev/null
  echo ""
  echo "--- namespace report ---"
  cat "$NS_REPORT" 2>/dev/null
  echo ""
  echo "--- logcat: camera + ART lines ---"
  grep -iE "AndroidRuntime|FATAL|MiuiCamera|com\.android\.camera|CameraService|Zygote" "$WORK/logcat_all.txt" 2>/dev/null | tail -n 250
} > "$SUMMARY" 2>&1

# ------------------------------------------------------------------------------
# 5. Pack everything
# ------------------------------------------------------------------------------
cp -f "$SUMMARY" "$WORK/summary.txt" 2>/dev/null
cp -f "$REPORT" "$WORK/../" 2>/dev/null
( cd "$DIAG_DIR" && tar czf "$ARCHIVE" ".gsdiag-$TS" 2>/dev/null )
[ -f "$ARCHIVE" ] || ( cd "$(dirname "$WORK")" && tar czf "$ARCHIVE" "$(basename "$WORK")" 2>/dev/null )

echo ""
echo "=================================================="
echo "  Evidence collected"
echo "=================================================="
if [ -s "$SUMMARY" ]; then
  echo "  Summary  : $SUMMARY   ($(wc -c < "$SUMMARY" | tr -d ' ') bytes)"
  echo "             ^ attach THIS to the chat"
fi
if [ -s "$ARCHIVE" ]; then
  echo "  Archive  : $ARCHIVE   ($(wc -c < "$ARCHIVE" | tr -d ' ') bytes, $copied blobs)"
  echo "             ^ attach this too if the summary is not enough"
else
  echo "  Archive  : could not be created; blobs left in $WORK"
fi
echo ""
echo "  Key lines to look at in the summary:"
echo "    'linker-related log lines' + the dlopen line under '--- linker / dlopen lines ---'"
echo "    '<soname>  appdir=… system:… vendor:…'  (namespace reachability)"
echo "    'NEEDED <soname> … <<< NOT-FOUND-ANYWHERE'"
echo "=================================================="
exit 0
