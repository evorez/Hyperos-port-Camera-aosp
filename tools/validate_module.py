#!/usr/bin/env python3
"""
HYPEROS-CAM-AOSP Flashable Module ZIP Validator.

Verifies Magisk / KernelSU / KernelSU-Next / APatch compatibility, POSIX shell
syntax, Unix LF line endings, XML well-formedness, module.prop integrity, and
Android 14-16 privapp-permissions coverage against the bundled MiuiCamera.apk.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import List, Set

from axml_inspector import parse_axml_bytes

REQUIRED_ENTRIES = [
    "META-INF/com/google/android/update-binary",
    "META-INF/com/google/android/updater-script",
    "module.prop",
    "module_config.sh",
    "customize.sh",
    "post-fs-data.sh",
    "service.sh",
    "action.sh",
    "uninstall.sh",
    "system.prop",
    "sepolicy.rule",
    "system/etc/permissions/privapp-permissions-miuicamera.xml",
    "system/etc/sysconfig/hiddenapi-package-miuicamera.xml",
    "system/etc/sysconfig/miuicamera-power-whitelist.xml",
]

SHELL_ENTRIES = [
    "META-INF/com/google/android/update-binary",
    "customize.sh",
    "post-fs-data.sh",
    "service.sh",
    "action.sh",
    "uninstall.sh",
    "module_config.sh",
]


def validate_zip(zip_path: Path, allow_no_apk: bool = False) -> List[str]:
    errors: List[str] = []

    if not zip_path.is_file():
        return [f"ZIP file not found: {zip_path}"]

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

        # 1. Required files check
        for req in REQUIRED_ENTRIES:
            if req not in names:
                errors.append(f"Missing required module file: {req}")

        # 2. Check updater-script marker
        if "META-INF/com/google/android/updater-script" in names:
            us = zf.read("META-INF/com/google/android/updater-script").decode("utf-8", errors="replace").strip()
            if us != "#MAGISK":
                errors.append(f"updater-script must be '#MAGISK', got: {us!r}")

        # 3. Validate module.prop
        if "module.prop" in names:
            prop_bytes = zf.read("module.prop")
            if b"\r" in prop_bytes:
                errors.append("module.prop contains CR/CRLF line endings")
            prop_text = prop_bytes.decode("utf-8", errors="replace")
            if "{{" in prop_text or "}}" in prop_text:
                errors.append("module.prop contains unreplaced template placeholders")
            props = {}
            for line in prop_text.splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
            for req_key in ("id", "name", "version", "versionCode", "author", "description"):
                if not props.get(req_key):
                    errors.append(f"module.prop missing required field: {req_key}")
            if props.get("versionCode") and not props["versionCode"].isdigit():
                errors.append(f"module.prop versionCode must be numeric, got: {props['versionCode']!r}")

        # 4. Validate LF endings & POSIX shell syntax for all scripts
        with tempfile.TemporaryDirectory(prefix="hyperos_cam_val_") as tmp:
            for sh_entry in SHELL_ENTRIES:
                if sh_entry not in names:
                    continue
                raw = zf.read(sh_entry)
                if b"\r" in raw:
                    errors.append(f"Script contains CR/CRLF line endings: {sh_entry}")
                tmp_sh = Path(tmp) / Path(sh_entry).name
                tmp_sh.write_bytes(raw)
                res = subprocess.run(["sh", "-n", str(tmp_sh)], capture_output=True, text=True)
                if res.returncode != 0:
                    errors.append(f"POSIX shell syntax error in {sh_entry}: {res.stderr.strip()}")

        # 5. Validate all XML files for well-formedness
        for name in sorted(names):
            if name.endswith(".xml"):
                raw = zf.read(name)
                if b"\r" in raw:
                    errors.append(f"XML contains CR/CRLF line endings: {name}")
                try:
                    ET.fromstring(raw.decode("utf-8"))
                except ET.ParseError as exc:
                    errors.append(f"Malformed XML {name}: {exc}")

        # 6. Validate MiuiCamera.apk & privapp-permissions alignment
        apk_entry = "system/priv-app/MiuiCamera/MiuiCamera.apk"
        if apk_entry not in names:
            if not allow_no_apk:
                errors.append(f"Missing primary camera APK: {apk_entry}")
        else:
            with tempfile.TemporaryDirectory(prefix="hyperos_cam_apk_") as tmp:
                apk_tmp = Path(tmp) / "MiuiCamera.apk"
                apk_tmp.write_bytes(zf.read(apk_entry))
                try:
                    with zipfile.ZipFile(apk_tmp, "r") as azf:
                        if "AndroidManifest.xml" not in azf.namelist():
                            errors.append("MiuiCamera.apk is missing AndroidManifest.xml")
                        else:
                            parsed = parse_axml_bytes(azf.read("AndroidManifest.xml"))
                            apk_pkg = str(parsed["package"])
                            apk_perms: Set[str] = set(parsed["permissions"])  # type: ignore[arg-type]

                            # Verify privapp-permissions covers the APK package and permissions
                            if "system/etc/permissions/privapp-permissions-miuicamera.xml" in names:
                                root = ET.fromstring(
                                    zf.read("system/etc/permissions/privapp-permissions-miuicamera.xml").decode("utf-8")
                                )
                                priv_node = root.find("privapp-permissions")
                                if priv_node is None:
                                    errors.append("privapp-permissions-miuicamera.xml missing <privapp-permissions>")
                                else:
                                    xml_pkg = priv_node.attrib.get("package", "")
                                    if apk_pkg and xml_pkg != apk_pkg:
                                        errors.append(
                                            f"Package mismatch: APK is '{apk_pkg}' but privapp-permissions has '{xml_pkg}'"
                                        )
                                    xml_perms = {
                                        el.attrib["name"]
                                        for el in priv_node.findall("permission")
                                        if "name" in el.attrib
                                    }
                                    missing = apk_perms - xml_perms
                                    if missing:
                                        errors.append(
                                            f"privapp-permissions missing {len(missing)} APK permission(s): "
                                            + ", ".join(sorted(missing)[:5])
                                        )

                            # Verify hiddenapi whitelist includes APK package
                            if "system/etc/sysconfig/hiddenapi-package-miuicamera.xml" in names and apk_pkg:
                                root = ET.fromstring(
                                    zf.read("system/etc/sysconfig/hiddenapi-package-miuicamera.xml").decode("utf-8")
                                )
                                hidden_pkgs = {
                                    el.attrib.get("package", "")
                                    for el in root.findall("hidden-api-whitelisted-app")
                                }
                                if apk_pkg not in hidden_pkgs:
                                    errors.append(
                                        f"hiddenapi-package-miuicamera.xml does not whitelist '{apk_pkg}'"
                                    )
                except zipfile.BadZipFile:
                    errors.append("MiuiCamera.apk inside module ZIP is not a valid APK/ZIP archive")

    return errors


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate a built HYPEROS-CAM-AOSP flashable ZIP.")
    parser.add_argument("zip_path", help="Path to the built flashable module ZIP.")
    parser.add_argument(
        "--allow-no-apk",
        action="store_true",
        help="Do not fail if MiuiCamera.apk is absent (dry-run validation).",
    )
    args = parser.parse_args(argv)

    errs = validate_zip(Path(args.zip_path), allow_no_apk=args.allow_no_apk)
    if errs:
        print(f"[FAIL] Validation failed with {len(errs)} error(s):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"[PASS] Module ZIP validated cleanly: {args.zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
