#!/usr/bin/env python3
"""
Import payloads from an existing HyperOS / Leica Camera AOSP module ZIP
into HYPEROS-CAM-AOSP's input/ staging tree.

Automatically discovers:
  - Camera & companion APKs (MiuiCamera, MiuiExtraPhoto, Gallery, etc.)
    from system/, system_ext/, or product/ priv-app & app directories
  - Pre-extracted JNI .so libraries next to those APKs
  - Shared libraries (lib64, lib), framework JARs, <library> permission XMLs,
    device_features XMLs, overlays, watermarks/media, fonts, and vendor/odm blobs
  - Handles nested .tar / .tar.gz / .tar.xz archives if present inside the source ZIP

Skips old module boilerplate (customize.sh, post-fs-data.sh, service.sh,
module.prop, META-INF) and camera privapp/hiddenapi XMLs that build_module.py
regenerates cleanly for Android 14-16.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from axml_inspector import inspect_apk

REPO_ROOT = Path(__file__).resolve().parent.parent

# Top-level module scripts/metadata from the source ZIP that our framework replaces
SKIP_TOP_LEVEL = {
    "meta-inf",
    "customize.sh",
    "post-fs-data.sh",
    "service.sh",
    "action.sh",
    "uninstall.sh",
    "module.prop",
    "module_config.sh",
    "sepolicy.rule",
    "system.prop",
    "install.sh",
    "config.sh",
}

PARTITION_PREFIXES = (
    "system/system_ext/",
    "system/product/",
    "system/vendor/",
    "system/odm/",
    "system/",
    "system_ext/",
    "product/",
    "vendor/",
    "odm/",
)


def _normalize_system_rel(rel_posix: str) -> Optional[str]:
    """Map partition paths like vendor/... or system/vendor/... into path relative to /system/."""
    p = rel_posix.lstrip("./")
    if p.startswith("system/"):
        return p[len("system/") :]
    for part in ("system_ext/", "product/", "vendor/", "odm/"):
        if p.startswith(part):
            return p
    return None


def _is_camera_or_companion_apk(sys_rel: str) -> bool:
    """Return True if sys_rel points to an APK in priv-app/ or app/ (excluding overlays)."""
    low = sys_rel.lower()
    if not low.endswith(".apk"):
        return False
    if "/overlay/" in f"/{low}":
        return False
    parts = sys_rel.split("/")
    return ("priv-app" in parts) or ("app" in parts)


def _should_skip_privapp_xml(sys_rel: str, raw_bytes: bytes) -> bool:
    """Skip source privapp/hiddenapi XMLs for camera packages (regenerated cleanly by build_module.py),
    while PRESERVING <library .../> framework XMLs and unrelated configs."""
    low = sys_rel.lower()
    if not low.endswith(".xml"):
        return False
    try:
        text = raw_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return False

    # Always keep framework <library> declarations!
    if "<library " in text:
        return False

    if "etc/permissions/" in low and "<privapp-permissions" in text:
        if "com.android.camera" in text or "com.miui.extraphoto" in text or "miuicamera" in low:
            return True
    if "etc/sysconfig/" in low and ("hidden-api-whitelisted-app" in text or "allow-in-power-save" in text):
        if "com.android.camera" in text or "miuicamera" in low:
            return True
    return False


def _extract_all_files(source_zip: Path, work_dir: Path) -> None:
    """Extract source ZIP and any nested tar archives inside it into work_dir."""
    with zipfile.ZipFile(source_zip, "r") as zf:
        zf.extractall(work_dir)

    # Check for nested tar archives (some camera modules bundle system.tar.xz / system.tar.gz)
    for tar_path in list(work_dir.rglob("*")):
        if not tar_path.is_file():
            continue
        low = tar_path.name.lower()
        if low.endswith((".tar.xz", ".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            print(f"[IMPORT] Extracting nested archive: {tar_path.relative_to(work_dir)}")
            try:
                with tarfile.open(tar_path, "r:*") as tf:
                    tf.extractall(tar_path.parent)
                tar_path.unlink()
            except Exception as exc:
                print(f"[WARN] Could not extract nested archive {tar_path.name}: {exc}")


def import_source_zip(source_zip: Path, repo_root: Path, clean_input: bool = True) -> Dict[str, int]:
    source_zip = Path(source_zip)
    if not source_zip.is_file():
        raise FileNotFoundError(f"Source ZIP not found: {source_zip}")

    input_dir = repo_root / "input"
    apk_out = input_dir / "apk"
    app_libs_out = input_dir / "app_libs"
    sys_extra_out = input_dir / "system_extra"

    if clean_input:
        for d in (apk_out, app_libs_out, sys_extra_out, input_dir / "lib64", input_dir / "framework", input_dir / "etc", input_dir / "vendor"):
            if d.is_dir():
                for child in d.iterdir():
                    if child.name == ".gitkeep":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()

    apk_out.mkdir(parents=True, exist_ok=True)
    app_libs_out.mkdir(parents=True, exist_ok=True)
    sys_extra_out.mkdir(parents=True, exist_ok=True)

    counts = {
        "apks": 0,
        "app_libs": 0,
        "system_files": 0,
        "skipped_xml": 0,
    }

    with tempfile.TemporaryDirectory(prefix="hyperos_import_") as tmp_str:
        work = Path(tmp_str)
        _extract_all_files(source_zip, work)

        # Log source module.prop / system.prop if present for transparency
        for info_name in ("module.prop", "system.prop"):
            cand = work / info_name
            if cand.is_file():
                print(f"\n--- Source {info_name} ---")
                print(cand.read_text(encoding="utf-8", errors="replace").strip())
                print("------------------------")

        # Track parent folders of imported APKs so we also capture their adjacent lib/arm64/*.so
        apk_dirs: Dict[Path, str] = {}

        all_files = sorted(p for p in work.rglob("*") if p.is_file())

        # Pass 1: Discover and import APKs
        for fpath in all_files:
            rel_posix = fpath.relative_to(work).as_posix()
            top = rel_posix.split("/", 1)[0].lower()
            if top in SKIP_TOP_LEVEL:
                continue

            sys_rel = _normalize_system_rel(rel_posix)
            is_top_apk = (sys_rel is None) and rel_posix.lower().endswith(".apk")

            if (sys_rel is not None and _is_camera_or_companion_apk(sys_rel)) or is_top_apk:
                try:
                    info = inspect_apk(fpath)
                except Exception as exc:
                    print(f"[WARN] Skipping unreadable APK {rel_posix}: {exc}")
                    continue

                pkg = info.package.lower()
                if pkg == "com.android.camera" or pkg.startswith("com.android.camera."):
                    dest_name = "MiuiCamera.apk"
                    app_key = "MiuiCamera"
                elif pkg == "com.miui.extraphoto" or "extraphoto" in pkg:
                    dest_name = "MiuiExtraPhoto.apk"
                    app_key = "MiuiExtraPhoto"
                else:
                    dest_name = fpath.name
                    app_key = fpath.stem

                dest = apk_out / dest_name
                shutil.copy2(fpath, dest)
                apk_dirs[fpath.parent] = app_key
                counts["apks"] += 1
                print(
                    f"[IMPORT] APK: {rel_posix} -> input/apk/{dest_name} "
                    f"(pkg={info.package}, ver={info.version_name}, code={info.version_code}, perms={len(info.permissions)})"
                )

        # Pass 2: Import adjacent app native libs and all remaining system/vendor/product/system_ext files
        for fpath in all_files:
            rel_posix = fpath.relative_to(work).as_posix()
            top = rel_posix.split("/", 1)[0].lower()
            if top in SKIP_TOP_LEVEL:
                continue

            # Check if file is inside an imported APK's directory (e.g., priv-app/MiuiCamera/lib/arm64/*.so)
            handled_by_apk_dir = False
            for apk_parent, app_key in apk_dirs.items():
                try:
                    sub = fpath.relative_to(apk_parent)
                except ValueError:
                    continue
                handled_by_apk_dir = True
                sub_posix = sub.as_posix()
                if sub_posix.lower().endswith(".apk"):
                    break
                if sub_posix.startswith("lib/") and sub_posix.endswith(".so"):
                    # sub_posix is like lib/arm64/foo.so -> input/app_libs/<app_key>/arm64/foo.so
                    rel_after_lib = sub_posix[len("lib/") :]
                    dest = app_libs_out / app_key / rel_after_lib
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fpath, dest)
                    counts["app_libs"] += 1
                break

            if handled_by_apk_dir:
                continue

            sys_rel = _normalize_system_rel(rel_posix)
            if sys_rel is None:
                continue

            raw = fpath.read_bytes()
            if _should_skip_privapp_xml(sys_rel, raw):
                counts["skipped_xml"] += 1
                print(f"[IMPORT] Skipping legacy camera XML (will regenerate): system/{sys_rel}")
                continue

            dest = sys_extra_out / sys_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fpath, dest)
            counts["system_files"] += 1

        # Merge any extra safe camera props from source system.prop into devices/generic/device.prop comment-free?
        # Keep our clean defaults and save source props for reference in logs/inspection.

    print(
        f"\n[IMPORT] Done: apks={counts['apks']}, app_libs={counts['app_libs']}, "
        f"system_files={counts['system_files']}, skipped_legacy_xml={counts['skipped_xml']}"
    )
    if counts["apks"] == 0:
        raise RuntimeError("No camera APK was found inside the source ZIP!")
    return counts


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Import payloads from an existing HyperOS/Leica Camera ZIP.")
    parser.add_argument("source_zip", help="Path to source ZIP file.")
    parser.add_argument("--no-clean", action="store_true", help="Do not clean existing input/ files first.")
    args = parser.parse_args(argv)
    import_source_zip(Path(args.source_zip), REPO_ROOT, clean_input=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
