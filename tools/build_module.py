#!/usr/bin/env python3
"""
HYPEROS-CAM-AOSP Universal Magisk / KernelSU / APatch Module Builder.

Packages user-provided modded HyperOS Camera APK(s) and optional libs/configs
with AOSP Android 14-16 permissions, hiddenapi allowlists, SEPolicy rules,
multi-device overlays, and universal root manager scripts into a verified
flashable ZIP archive.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from axml_inspector import ApkInfo, inspect_apk

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shell scripts and installer binaries that need LF endings & 0755 permissions inside the ZIP
EXEC_ZIP_PATHS = {
    "META-INF/com/google/android/update-binary",
    "customize.sh",
    "post-fs-data.sh",
    "service.sh",
    "action.sh",
    "uninstall.sh",
    "module_config.sh",
    "bin/elf_needed.sh",
}

TEXT_EXTENSIONS = {".sh", ".prop", ".rule", ".xml", ".txt", ".conf", ".json"}
TEXT_FILENAMES = {"update-binary", "updater-script", "module.prop", "system.prop", "sepolicy.rule"}

ABI_TO_ANDROID_DIR = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
}

# Standard AOSP framework libraries already present on AOSP ROMs (don't warn on these)
STANDARD_AOSP_LIBRARIES = {
    "org.apache.http.legacy",
    "android.test.base",
    "android.test.mock",
    "android.test.runner",
    "androidx.window.extensions",
    "androidx.window.sidecar",
    "com.android.future.usb.accessory",
}


def load_config_env(path: Path) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    if not path.is_file():
        return cfg
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        cfg[key] = val
    return cfg


def sanitize_filename_token(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return cleaned.strip("_") or "v1.0.0"


def sh_quote(val: str) -> str:
    escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def parse_baseline_permissions(xml_path: Path) -> Set[str]:
    perms: Set[str] = set()
    if not xml_path.is_file():
        return perms
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for perm_el in root.findall(".//permission"):
        name = perm_el.attrib.get("name")
        if name:
            perms.add(name)
    return perms


def build_privapp_xml(package: str, permissions: Set[str], comment: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {comment} -->",
        "<permissions>",
        f'    <privapp-permissions package="{package}">',
    ]
    for perm in sorted(permissions):
        lines.append(f'        <permission name="{perm}"/>')
    lines.extend(
        [
            "    </privapp-permissions>",
            "</permissions>",
            "",
        ]
    )
    return "\n".join(lines)


def build_hiddenapi_xml(packages: List[str]) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Allow HyperOS Modded Camera & companions to access Android hidden APIs on AOSP -->",
        "<config>",
    ]
    for pkg in packages:
        lines.append(f'    <hidden-api-whitelisted-app package="{pkg}"/>')
    lines.extend(["</config>", ""])
    return "\n".join(lines)


def build_power_whitelist_xml(packages: List[str]) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Prevent AOSP Doze from interrupting background HDR/Night/MiAlgo processing -->",
        "<config>",
    ]
    for pkg in packages:
        lines.append(f'    <allow-in-power-save package="{pkg}"/>')
        lines.append(f'    <allow-in-data-usage-save package="{pkg}"/>')
    lines.extend(["</config>", ""])
    return "\n".join(lines)


def classify_apks(apk_paths: List[Path]) -> Tuple[Optional[Tuple[Path, ApkInfo]], Optional[Tuple[Path, ApkInfo]], List[Tuple[Path, ApkInfo]]]:
    """Classify discovered APKs into (primary_camera, extraphoto, extra_companions)."""
    inspected: List[Tuple[Path, ApkInfo]] = []
    for p in sorted(apk_paths):
        info = inspect_apk(p)
        inspected.append((p, info))

    primary: Optional[Tuple[Path, ApkInfo]] = None
    extraphoto: Optional[Tuple[Path, ApkInfo]] = None
    others: List[Tuple[Path, ApkInfo]] = []

    # Pass 1: Match by package name
    remaining: List[Tuple[Path, ApkInfo]] = []
    for item in inspected:
        _p, info = item
        pkg = info.package.lower()
        if primary is None and (pkg == "com.android.camera" or pkg.startswith("com.android.camera.")):
            primary = item
        elif extraphoto is None and (pkg == "com.miui.extraphoto" or "extraphoto" in pkg):
            extraphoto = item
        else:
            remaining.append(item)

    # Pass 2: Match by filename if not found by package name
    still_remaining: List[Tuple[Path, ApkInfo]] = []
    for item in remaining:
        p, _info = item
        low = p.stem.lower()
        if primary is None and any(k in low for k in ("miuicamera", "hyperoscamera", "leicacam", "camera")):
            primary = item
        elif extraphoto is None and "extraphoto" in low:
            extraphoto = item
        else:
            still_remaining.append(item)

    # Pass 3: If still no primary and there is at least one APK, take the first one
    if primary is None and still_remaining:
        primary = still_remaining.pop(0)

    others = still_remaining
    return primary, extraphoto, others


def extract_apk_native_libs(apk_path: Path, app_dir: Path, mode: str) -> int:
    """Extract lib/<abi>/*.so from APK into <app_dir>/lib/<android_abi>/."""
    if mode == "never":
        return 0
    extracted = 0
    with zipfile.ZipFile(apk_path, "r") as zf:
        for info in zf.infolist():
            if not (info.filename.startswith("lib/") and info.filename.endswith(".so")):
                continue
            parts = info.filename.split("/")
            if len(parts) != 3:
                continue
            abi, so_name = parts[1], parts[2]
            android_abi = ABI_TO_ANDROID_DIR.get(abi)
            if not android_abi:
                continue
            dest = app_dir / "lib" / android_abi / so_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info.filename))
            extracted += 1
    return extracted


def copy_optional_tree(src_dir: Path, dst_dir: Path) -> int:
    """Copy all files (excluding .gitkeep and README.md) from src_dir to dst_dir."""
    count = 0
    if not src_dir.is_dir():
        return 0
    for item in src_dir.rglob("*"):
        if item.is_dir():
            continue
        if item.name in (".gitkeep", "README.md", ".DS_Store"):
            continue
        rel = item.relative_to(src_dir)
        target = dst_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def write_zip_from_dir(stage_dir: Path, out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage_dir.rglob("*")):
            if path.is_dir():
                continue
            rel_posix = path.relative_to(stage_dir).as_posix()
            raw = path.read_bytes()

            is_text = (path.suffix.lower() in TEXT_EXTENSIONS) or (path.name in TEXT_FILENAMES)
            if is_text:
                raw = normalize_lf(raw)

            zinfo = zipfile.ZipInfo(rel_posix)
            # Store already-compressed .apk files directly (faster builds & faster flashing on Android)
            if path.suffix.lower() == ".apk":
                zinfo.compress_type = zipfile.ZIP_STORED
            else:
                zinfo.compress_type = zipfile.ZIP_DEFLATED
            # Fixed reproducible timestamp (2026-01-01 00:00:00)
            zinfo.date_time = (2026, 1, 1, 0, 0, 0)
            mode = 0o755 if rel_posix in EXEC_ZIP_PATHS else 0o644
            zinfo.external_attr = (0o100000 | mode) << 16
            zf.writestr(zinfo, raw)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_module(
    repo_root: Path,
    extra_apks: List[Path],
    output_path: Optional[Path],
    allow_no_apk: bool = False,
) -> Path:
    cfg = load_config_env(repo_root / "config.env")

    module_id = cfg.get("MODULE_ID", "hyperos_cam_aosp")
    module_name = cfg.get("MODULE_NAME", "HyperOS Modded Camera for AOSP")
    module_author = cfg.get("MODULE_AUTHOR", "evorez")
    module_desc = cfg.get(
        "MODULE_DESCRIPTION",
        "Universal HyperOS/Leica modded camera module for Android 14-16 AOSP ROMs (Magisk / KernelSU / APatch).",
    )
    module_ver_cfg = cfg.get("MODULE_VERSION", "auto")
    module_ver_fallback = cfg.get("MODULE_VERSION_FALLBACK", "v1.0.0")
    module_vcode_cfg = cfg.get("MODULE_VERSION_CODE", "auto")
    module_vcode_fallback = cfg.get("MODULE_VERSION_CODE_FALLBACK", "10000")
    min_api = cfg.get("MIN_ANDROID_API", "34")
    max_api = cfg.get("MAX_ANDROID_API", "36")
    update_json = cfg.get("MODULE_UPDATE_JSON", "").strip()
    extract_libs_mode = cfg.get("EXTRACT_APK_LIBS", "auto").strip().lower()
    auto_merge_perms = cfg.get("AUTO_MERGE_PRIVAPP_PERMS", "true").strip().lower() == "true"
    replace_policy = cfg.get("REPLACE_POLICY", "conflict_only").strip()
    auto_grant_perms = cfg.get("AUTO_GRANT_PERMISSIONS", "true").strip().lower()
    output_prefix = cfg.get("OUTPUT_PREFIX", "HyperOS-Cam-AOSP")

    # Collect APKs from input/apk/ and any --apk CLI arguments
    apk_paths: List[Path] = []
    input_apk_dir = repo_root / "input" / "apk"
    if input_apk_dir.is_dir():
        apk_paths.extend(sorted(input_apk_dir.glob("*.apk")))
    for p in extra_apks:
        resolved = p.resolve()
        if resolved not in [x.resolve() for x in apk_paths]:
            apk_paths.append(resolved)

    if not apk_paths and not allow_no_apk:
        print(
            "\n[ERROR] No APK files found in input/apk/ (and no --apk argument provided).\n"
            "Place your modded HyperOS Camera APK into:\n"
            f"  {input_apk_dir / 'MiuiCamera.apk'}\n"
            "Then re-run: ./build.sh\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    primary, extraphoto, companions = classify_apks(apk_paths) if apk_paths else (None, None, [])

    cam_pkg = primary[1].package if (primary and primary[1].package) else "com.android.camera"
    extra_pkg = extraphoto[1].package if (extraphoto and extraphoto[1].package) else ""

    # Derive version / versionCode
    if module_ver_cfg == "auto" and primary and primary[1].version_name:
        vname = primary[1].version_name.strip()
        module_version = vname if vname.startswith(("-", "v", "V")) else f"v{vname}"
    elif module_ver_cfg != "auto":
        module_version = module_ver_cfg
    else:
        module_version = module_ver_fallback

    if module_vcode_cfg == "auto" and primary and primary[1].version_code:
        module_version_code = str(primary[1].version_code)
    elif module_vcode_cfg != "auto":
        module_version_code = module_vcode_cfg
    else:
        module_version_code = module_vcode_fallback

    print("============================================================")
    print(f" Building {module_name}")
    print(f" Version : {module_version} (code {module_version_code})")
    print(f" Targets : Android API {min_api}-{max_api} | Magisk / KSU / APatch")
    print("============================================================")

    with tempfile.TemporaryDirectory(prefix="hyperos_cam_build_") as tmp:
        stage = Path(tmp) / "module"
        shutil.copytree(repo_root / "module", stage)

        # Remove template file & unused optional privapp XML (recreated only if present)
        (stage / "module.prop.in").unlink(missing_ok=True)
        extra_perm_template = repo_root / "module" / "system" / "etc" / "permissions" / "privapp-permissions-miuiextraphoto.xml"
        (stage / "system" / "etc" / "permissions" / "privapp-permissions-miuiextraphoto.xml").unlink(missing_ok=True)

        # Render module.prop
        tpl = (repo_root / "module" / "module.prop.in").read_text(encoding="utf-8")
        update_json_line = f"\nupdateJson={update_json}" if update_json else ""
        module_prop_text = (
            tpl.replace("{{MODULE_ID}}", module_id)
            .replace("{{MODULE_NAME}}", module_name)
            .replace("{{MODULE_VERSION}}", module_version)
            .replace("{{MODULE_VERSION_CODE}}", module_version_code)
            .replace("{{MODULE_AUTHOR}}", module_author)
            .replace("{{MODULE_DESCRIPTION}}", module_desc)
            .replace("{{UPDATE_JSON_LINE}}", update_json_line)
        )
        (stage / "module.prop").write_text(module_prop_text, encoding="utf-8")

        # Copy imported system_extra first so our freshly generated AOSP XMLs always take precedence
        n_sys_extra = copy_optional_tree(repo_root / "input" / "system_extra", stage / "system")

        whitelisted_packages: List[str] = [cam_pkg]

        # Package Primary Camera APK
        cam_baseline_perms = parse_baseline_permissions(
            repo_root / "module" / "system" / "etc" / "permissions" / "privapp-permissions-miuicamera.xml"
        )
        if primary:
            apk_src, info = primary
            app_dir = stage / "system" / "priv-app" / "MiuiCamera"
            app_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(apk_src, app_dir / "MiuiCamera.apk")

            n_libs = extract_apk_native_libs(apk_src, app_dir, extract_libs_mode)
            print(f"[APK] Primary Camera : {apk_src.name}")
            print(f"      Package        : {info.package}")
            print(f"      Version        : {info.version_name} ({info.version_code})")
            print(f"      SDK            : min={info.min_sdk}, target={info.target_sdk}")
            print(f"      Permissions    : {len(info.permissions)} declared in manifest")
            print(f"      Native .so     : {n_libs} extracted to priv-app/MiuiCamera/lib/")

            for lib in info.uses_libraries:
                lib_name = str(lib["name"])
                if lib.get("required") and lib_name not in STANDARD_AOSP_LIBRARIES:
                    print(
                        f"  [WARN] APK declares required <uses-library>: '{lib_name}'. "
                        "Ensure your ROM has it or drop the matching JAR into input/framework/ + XML into input/etc/permissions/."
                    )

            merged_perms = set(cam_baseline_perms)
            if auto_merge_perms:
                merged_perms.update(info.permissions)
            (stage / "system" / "etc" / "permissions" / "privapp-permissions-miuicamera.xml").write_text(
                build_privapp_xml(
                    cam_pkg,
                    merged_perms,
                    f"Auto-merged privileged permissions for {cam_pkg} ({len(merged_perms)} entries)",
                ),
                encoding="utf-8",
            )

        # Package Optional MiuiExtraPhoto APK
        if extraphoto:
            apk_src, info = extraphoto
            app_dir = stage / "system" / "priv-app" / "MiuiExtraPhoto"
            app_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(apk_src, app_dir / "MiuiExtraPhoto.apk")
            n_libs = extract_apk_native_libs(apk_src, app_dir, extract_libs_mode)
            print(f"[APK] ExtraPhoto     : {apk_src.name} ({info.package}, {n_libs} native libs extracted)")

            extra_perms = parse_baseline_permissions(extra_perm_template)
            if auto_merge_perms:
                extra_perms.update(info.permissions)
            (stage / "system" / "etc" / "permissions" / "privapp-permissions-miuiextraphoto.xml").write_text(
                build_privapp_xml(
                    extra_pkg or "com.miui.extraphoto",
                    extra_perms,
                    f"Auto-merged privileged permissions for {extra_pkg or 'com.miui.extraphoto'}",
                ),
                encoding="utf-8",
            )
            if extra_pkg and extra_pkg not in whitelisted_packages:
                whitelisted_packages.append(extra_pkg)

        # Package any additional companion APKs
        for apk_src, info in companions:
            folder_name = sanitize_filename_token(apk_src.stem)
            app_dir = stage / "system" / "priv-app" / folder_name
            app_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(apk_src, app_dir / f"{folder_name}.apk")
            n_libs = extract_apk_native_libs(apk_src, app_dir, extract_libs_mode)
            print(f"[APK] Companion      : {apk_src.name} -> priv-app/{folder_name}/ ({info.package}, {n_libs} libs)")
            if info.package:
                if info.package not in whitelisted_packages:
                    whitelisted_packages.append(info.package)
                if info.permissions:
                    slug = sanitize_filename_token(folder_name.lower())
                    (stage / "system" / "etc" / "permissions" / f"privapp-permissions-{slug}.xml").write_text(
                        build_privapp_xml(
                            info.package,
                            set(info.permissions),
                            f"Auto-generated privileged permissions for companion {info.package}",
                        ),
                        encoding="utf-8",
                    )

        # Update sysconfig hiddenapi + power whitelist XMLs with all bundled packages
        (stage / "system" / "etc" / "sysconfig" / "hiddenapi-package-miuicamera.xml").write_text(
            build_hiddenapi_xml(whitelisted_packages), encoding="utf-8"
        )
        (stage / "system" / "etc" / "sysconfig" / "miuicamera-power-whitelist.xml").write_text(
            build_power_whitelist_xml(whitelisted_packages), encoding="utf-8"
        )

        # Copy optional user assets (app_libs, lib64, framework, etc, vendor)
        n_app_libs = 0
        app_libs_root = repo_root / "input" / "app_libs"
        if app_libs_root.is_dir():
            for app_dir_src in sorted(app_libs_root.iterdir()):
                if app_dir_src.is_dir():
                    n_app_libs += copy_optional_tree(
                        app_dir_src, stage / "system" / "priv-app" / app_dir_src.name / "lib"
                    )
        n_lib64 = copy_optional_tree(repo_root / "input" / "lib64", stage / "system" / "lib64")
        n_fw = copy_optional_tree(repo_root / "input" / "framework", stage / "system" / "framework")
        n_etc = copy_optional_tree(repo_root / "input" / "etc", stage / "system" / "etc")
        n_vendor = copy_optional_tree(repo_root / "input" / "vendor", stage / "system" / "vendor")
        if any((n_sys_extra, n_app_libs, n_lib64, n_fw, n_etc, n_vendor)):
            print(
                f"[OPT] Extra payloads : system_extra={n_sys_extra}, app_libs={n_app_libs}, "
                f"lib64={n_lib64}, framework={n_fw}, etc={n_etc}, vendor={n_vendor}"
            )

        # Copy device overlay profiles (excluding _template)
        devices_src = repo_root / "devices"
        bundled_devices: List[str] = []
        if devices_src.is_dir():
            for dev_dir in sorted(devices_src.iterdir()):
                if not dev_dir.is_dir() or dev_dir.name.startswith("_"):
                    continue
                copied = copy_optional_tree(dev_dir, stage / "devices" / dev_dir.name)
                if copied > 0:
                    bundled_devices.append(dev_dir.name)
        print(f"[DEV] Device profiles: {', '.join(bundled_devices) if bundled_devices else 'none'}")

        # Generate module_config.sh sourced by customize.sh, post-fs-data.sh, service.sh, action.sh, uninstall.sh
        module_config_lines = [
            "#!/system/bin/sh",
            "# Auto-generated at build time by tools/build_module.py",
            f"MODULE_ID={sh_quote(module_id)}",
            f"MODULE_NAME={sh_quote(module_name)}",
            f"MODULE_VERSION={sh_quote(module_version)}",
            f"MODULE_VERSION_CODE={sh_quote(module_version_code)}",
            f"CAM_PKG={sh_quote(cam_pkg)}",
            f"EXTRA_PKG={sh_quote(extra_pkg)}",
            f"MIN_ANDROID_API={sh_quote(min_api)}",
            f"MAX_ANDROID_API={sh_quote(max_api)}",
            f"REPLACE_POLICY={sh_quote(replace_policy)}",
            f"AUTO_GRANT_PERMISSIONS={sh_quote(auto_grant_perms)}",
            "",
        ]
        (stage / "module_config.sh").write_text("\n".join(module_config_lines), encoding="utf-8")

        # Determine output ZIP filename
        if output_path is None:
            ver_token = sanitize_filename_token(module_version).lstrip("-_")
            out_zip = repo_root / "dist" / f"{output_prefix}-{ver_token}-universal.zip"
        else:
            out_zip = output_path

        write_zip_from_dir(stage, out_zip)
        digest = sha256_file(out_zip)
        sha_path = out_zip.with_suffix(out_zip.suffix + ".sha256")
        sha_path.write_text(f"{digest}  {out_zip.name}\n", encoding="utf-8")

        size_mb = out_zip.stat().st_size / (1024 * 1024)
        print("------------------------------------------------------------")
        print(f"[OK] Built ZIP : {out_zip}")
        print(f"[OK] Size      : {size_mb:.2f} MB")
        print(f"[OK] SHA-256   : {digest}")
        print("------------------------------------------------------------")
        return out_zip


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Build an AOSP flashable Magisk/KernelSU/APatch module for HyperOS Modded Camera."
    )
    parser.add_argument(
        "--apk",
        action="append",
        default=[],
        help="Path to an APK file to include (can be specified multiple times; supplements input/apk/).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Custom output ZIP path (default: dist/<OUTPUT_PREFIX>-<version>-universal.zip).",
    )
    parser.add_argument(
        "--allow-no-apk",
        action="store_true",
        help="Allow building a template-only dry-run ZIP without an APK (for CI/testing).",
    )
    args = parser.parse_args(argv)

    extra_apks = [Path(p) for p in args.apk]
    out_path = Path(args.output) if args.output else None
    build_module(REPO_ROOT, extra_apks=extra_apks, output_path=out_path, allow_no_apk=args.allow_no_apk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
