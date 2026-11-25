#!/usr/bin/env python3
"""End-to-end test suite for HYPEROS-CAM-AOSP AXML parser, builder, and validator."""

from __future__ import annotations

import shutil
import json
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from axml_inspector import (  # noqa: E402
    RES_STRING_POOL_TYPE,
    RES_XML_RESOURCE_MAP_TYPE,
    RES_XML_START_ELEMENT_TYPE,
    RES_XML_TYPE,
    TYPE_INT_BOOLEAN,
    TYPE_INT_DEC,
    TYPE_STRING,
    UTF8_FLAG,
    inspect_apk,
    parse_axml_bytes,
)
from build_module import build_module  # noqa: E402
from import_source_zip import import_source_zip  # noqa: E402
from patch_apk import (  # noqa: E402
    apply_patch_spec,
    inject_evorez_card_hooks,
    patch_axml_string,
    patch_smali_bool_method,
    patch_smali_key_fallback,
    scrub_arsc_credits_inplace,
)
from validate_module import validate_zip  # noqa: E402


def _build_synthetic_axml(
    package: str,
    version_code: int,
    version_name: str,
    min_sdk: int,
    target_sdk: int,
    permissions: List[str],
    extract_native_libs: bool = True,
    use_utf8: bool = True,
) -> bytes:
    """Construct a spec-compliant Android binary XML (AXML) AndroidManifest.xml."""
    strings: List[str] = []

    def sid(s: str) -> int:
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    # Pre-register attribute names and tag names
    android_ns = sid("http://schemas.android.com/apk/res/android")
    tag_manifest = sid("manifest")
    tag_uses_sdk = sid("uses-sdk")
    tag_uses_perm = sid("uses-permission")
    tag_app = sid("application")

    attr_package = sid("package")
    attr_vcode = sid("versionCode")
    attr_vname = sid("versionName")
    attr_minsdk = sid("minSdkVersion")
    attr_targetsdk = sid("targetSdkVersion")
    attr_name = sid("name")
    attr_extract = sid("extractNativeLibs")

    val_pkg = sid(package)
    val_vname = sid(version_name)
    perm_ids = [sid(p) for p in permissions]

    def make_start_elem(tag_idx: int, attrs: List[Tuple[int, int, int, int, int]]) -> bytes:
        # attrs: (ns_idx, name_idx, raw_val_idx, val_type, val_data)
        attr_bytes = b"".join(
            struct.pack("<iiiHBBI", ns, name, raw_v, 8, 0, vtype, vdata)
            for (ns, name, raw_v, vtype, vdata) in attrs
        )
        # ResXMLTree_attrExt (20 bytes): ns, name, attrStart=20, attrSize=20, attrCount, id/class/style=0
        ext = struct.pack("<iiHHHHHH", -1, tag_idx, 20, 20, len(attrs), 0, 0, 0)
        # ResXMLTree_node header (16 bytes): type, headerSize=16, size, lineNumber=1, comment=-1
        total_size = 16 + len(ext) + len(attr_bytes)
        header = struct.pack("<HHIIi", RES_XML_START_ELEMENT_TYPE, 16, total_size, 1, -1)
        return header + ext + attr_bytes

    elements = [
        make_start_elem(
            tag_manifest,
            [
                (-1, attr_package, val_pkg, TYPE_STRING, val_pkg),
                (android_ns, attr_vcode, -1, TYPE_INT_DEC, version_code),
                (android_ns, attr_vname, val_vname, TYPE_STRING, val_vname),
            ],
        ),
        make_start_elem(
            tag_uses_sdk,
            [
                (android_ns, attr_minsdk, -1, TYPE_INT_DEC, min_sdk),
                (android_ns, attr_targetsdk, -1, TYPE_INT_DEC, target_sdk),
            ],
        ),
        make_start_elem(
            tag_app,
            [
                (android_ns, attr_extract, -1, TYPE_INT_BOOLEAN, 1 if extract_native_libs else 0),
            ],
        ),
    ]
    for pid in perm_ids:
        elements.append(
            make_start_elem(
                tag_uses_perm,
                [(android_ns, attr_name, pid, TYPE_STRING, pid)],
            )
        )

    # Build String Pool chunk
    encoded_strings = bytearray()
    offsets: List[int] = []
    for s in strings:
        offsets.append(len(encoded_strings))
        if use_utf8:
            utf8 = s.encode("utf-8")
            encoded_strings.append(len(s))
            encoded_strings.append(len(utf8))
            encoded_strings.extend(utf8)
            encoded_strings.append(0)
        else:
            utf16 = s.encode("utf-16-le")
            encoded_strings.extend(struct.pack("<H", len(s)))
            encoded_strings.extend(utf16)
            encoded_strings.extend(b"\x00\x00")
    while len(encoded_strings) % 4 != 0:
        encoded_strings.append(0)

    sp_header_size = 28
    strings_start = sp_header_size + len(strings) * 4
    sp_chunk_size = strings_start + len(encoded_strings)
    sp_flags = UTF8_FLAG if use_utf8 else 0
    sp_chunk = (
        struct.pack("<HHIIIIII", RES_STRING_POOL_TYPE, sp_header_size, sp_chunk_size, len(strings), 0, sp_flags, strings_start, 0)
        + b"".join(struct.pack("<I", off) for off in offsets)
        + bytes(encoded_strings)
    )

    # Resource map chunk (empty)
    rm_chunk = struct.pack("<HHI", RES_XML_RESOURCE_MAP_TYPE, 8, 8)

    body = sp_chunk + rm_chunk + b"".join(elements)
    root_header = struct.pack("<HHI", RES_XML_TYPE, 8, 8 + len(body))
    return root_header + body


def _make_synthetic_apk(
    dest: Path,
    package: str = "com.android.camera",
    version_code: int = 530004200,
    version_name: str = "5.3.000420.0",
    permissions: List[str] | None = None,
    so_files: Dict[str, bytes] | None = None,
    use_utf8: bool = True,
) -> Path:
    perms = permissions or [
        "android.permission.CAMERA",
        "android.permission.SYSTEM_CAMERA",
        "android.permission.WRITE_SECURE_SETTINGS",
        "com.xiaomi.camera.permission.CUSTOM_LEICA_TEST",
    ]
    axml = _build_synthetic_axml(
        package=package,
        version_code=version_code,
        version_name=version_name,
        min_sdk=34,
        target_sdk=35,
        permissions=perms,
        use_utf8=use_utf8,
    )
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("AndroidManifest.xml", axml)
        zf.writestr("classes.dex", b"dex\n039\x00")
        for rel_path, content in (so_files or {}).items():
            zf.writestr(rel_path, content)
    return dest


class TestHyperOSCamPipeline(unittest.TestCase):
    def test_axml_parser_utf8_and_utf16(self) -> None:
        for use_utf8 in (True, False):
            axml = _build_synthetic_axml(
                package="com.android.camera",
                version_code=540001000,
                version_name="5.4.000100.0",
                min_sdk=34,
                target_sdk=36,
                permissions=["android.permission.CAMERA", "android.permission.SYSTEM_CAMERA"],
                use_utf8=use_utf8,
            )
            parsed = parse_axml_bytes(axml)
            self.assertEqual(parsed["package"], "com.android.camera")
            self.assertEqual(parsed["version_code"], 540001000)
            self.assertEqual(parsed["version_name"], "5.4.000100.0")
            self.assertEqual(parsed["min_sdk"], 34)
            self.assertEqual(parsed["target_sdk"], 36)
            self.assertIn("android.permission.SYSTEM_CAMERA", parsed["permissions"])

    def test_full_build_and_validate_with_apk_and_extraphoto(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyperos_cam_test_") as tmp_str:
            tmp = Path(tmp_str)
            cam_apk = _make_synthetic_apk(
                tmp / "MiuiCamera_Mod_Leica.apk",
                package="com.android.camera",
                version_code=530004200,
                version_name="5.3.000420.0",
                permissions=[
                    "android.permission.CAMERA",
                    "android.permission.SYSTEM_CAMERA",
                    "android.permission.CAPTURE_AUDIO_OUTPUT",
                    "com.xiaomi.camera.permission.LEICA_PRO",
                ],
                so_files={
                    "lib/arm64-v8a/libcamera_algoup_jni.xiaomi.so": b"\x7fELFfake_arm64_so",
                    "lib/arm64-v8a/libmialgo_leica.so": b"\x7fELFfake_leica_so",
                },
            )
            extra_apk = _make_synthetic_apk(
                tmp / "MiuiExtraPhoto.apk",
                package="com.miui.extraphoto",
                version_code=10200,
                version_name="1.0.2",
                permissions=["android.permission.WRITE_MEDIA_STORAGE"],
                so_files={"lib/arm64-v8a/libextra_watermark.so": b"\x7fELFfake_extra"},
            )
            out_zip = tmp / "HyperOS-Cam-AOSP-test.zip"

            built = build_module(
                REPO_ROOT,
                extra_apks=[cam_apk, extra_apk],
                output_path=out_zip,
                allow_no_apk=False,
            )
            self.assertEqual(built, out_zip)
            self.assertTrue(out_zip.is_file())
            self.assertTrue(out_zip.with_suffix(".zip.sha256").is_file())

            # Run validator
            errors = validate_zip(out_zip, allow_no_apk=False)
            self.assertEqual(errors, [], f"Validation errors: {errors}")

            # Verify contents inside ZIP
            with zipfile.ZipFile(out_zip, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("system/priv-app/MiuiCamera/MiuiCamera.apk", names)
                self.assertIn("system/priv-app/MiuiCamera/lib/arm64/libcamera_algoup_jni.xiaomi.so", names)
                self.assertIn("system/priv-app/MiuiCamera/lib/arm64/libmialgo_leica.so", names)
                self.assertIn("system/priv-app/MiuiExtraPhoto/MiuiExtraPhoto.apk", names)
                self.assertIn("system/priv-app/MiuiExtraPhoto/lib/arm64/libextra_watermark.so", names)
                self.assertIn("system/etc/permissions/privapp-permissions-miuiextraphoto.xml", names)
                self.assertIn("devices/generic/device.prop", names)
                self.assertNotIn("devices/_template/device.prop", names)

                # Check custom permission was auto-merged into privapp-permissions
                priv_xml = zf.read("system/etc/permissions/privapp-permissions-miuicamera.xml").decode("utf-8")
                self.assertIn("com.xiaomi.camera.permission.LEICA_PRO", priv_xml)

                # Check module.prop derived version from APK
                mprop = zf.read("module.prop").decode("utf-8")
                self.assertIn("version=v5.3.000420.0", mprop)
                self.assertIn("versionCode=530004200", mprop)

                # Check hiddenapi includes both camera and extraphoto
                hidden_xml = zf.read("system/etc/sysconfig/hiddenapi-package-miuicamera.xml").decode("utf-8")
                self.assertIn('package="com.android.camera"', hidden_xml)
                self.assertIn('package="com.miui.extraphoto"', hidden_xml)

                # Check executable permissions on scripts
                for sh_name in ("customize.sh", "post-fs-data.sh", "service.sh", "action.sh", "uninstall.sh"):
                    mode = (zf.getinfo(sh_name).external_attr >> 16) & 0o777
                    self.assertEqual(mode, 0o755, f"{sh_name} should be 0755, got {oct(mode)}")

    def test_custom_cloned_camera_package_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyperos_cam_clone_") as tmp_str:
            tmp = Path(tmp_str)
            cloned_apk = _make_synthetic_apk(
                tmp / "HyperOSCamera_Cloned.apk",
                package="com.android.camera.leica",
                version_code=600001000,
                version_name="6.0.000100.0",
                permissions=["android.permission.CAMERA", "android.permission.SYSTEM_CAMERA"],
            )
            out_zip = tmp / "HyperOS-Cam-Cloned.zip"
            build_module(REPO_ROOT, extra_apks=[cloned_apk], output_path=out_zip)
            errors = validate_zip(out_zip, allow_no_apk=False)
            self.assertEqual(errors, [], f"Validation errors: {errors}")

            with zipfile.ZipFile(out_zip, "r") as zf:
                cfg_sh = zf.read("module_config.sh").decode("utf-8")
                self.assertIn('CAM_PKG="com.android.camera.leica"', cfg_sh)
                root = ET.fromstring(
                    zf.read("system/etc/permissions/privapp-permissions-miuicamera.xml").decode("utf-8")
                )
                self.assertEqual(root.find("privapp-permissions").attrib["package"], "com.android.camera.leica")  # type: ignore[union-attr]

    def test_dry_run_template_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyperos_cam_dry_") as tmp_str:
            out_zip = Path(tmp_str) / "dryrun.zip"
            build_module(REPO_ROOT, extra_apks=[], output_path=out_zip, allow_no_apk=True)
            errors = validate_zip(out_zip, allow_no_apk=True)
            self.assertEqual(errors, [], f"Dry-run validation errors: {errors}")

    def test_import_source_zip_and_repackage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyperos_cam_import_") as tmp_str:
            tmp = Path(tmp_str)
            # Create a temporary repo copy so importing into input/ doesn't touch real repo
            fake_repo = tmp / "repo"
            shutil.copytree(REPO_ROOT / "module", fake_repo / "module")
            shutil.copytree(REPO_ROOT / "devices", fake_repo / "devices")
            shutil.copy2(REPO_ROOT / "config.env", fake_repo / "config.env")
            (fake_repo / "input" / "apk").mkdir(parents=True, exist_ok=True)

            cam_apk = _make_synthetic_apk(
                tmp / "MiuiCamera.apk",
                package="com.android.camera",
                version_code=500083000,
                version_name="5.0.230706.0",
                permissions=[
                    "android.permission.CAMERA",
                    "android.permission.SYSTEM_CAMERA",
                    "com.xiaomi.camera.permission.LEICA_IMPORT",
                ],
                so_files={"lib/arm64-v8a/libcam_inside.so": b"\x7fELFinside"},
            )
            src_zip = tmp / "AOSP-MagiskKSU_Leica-5.0_test.zip"
            with zipfile.ZipFile(src_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("module.prop", "id=old_leica\nname=Old Leica\nversion=5.0\nversionCode=50\n")
                zf.writestr("customize.sh", "# old script\n")
                zf.write(cam_apk, "system/system_ext/priv-app/MiuiCamera/MiuiCamera.apk")
                zf.writestr("system/system_ext/priv-app/MiuiCamera/lib/arm64/libcam_adjacent.so", b"\x7fELFadj")
                zf.writestr("system/system_ext/framework/miuicamera.jar", b"PK\x03\x04jar")
                zf.writestr(
                    "system/system_ext/etc/permissions/miuicamera.xml",
                    '<permissions><library name="miuicamera" file="/system_ext/framework/miuicamera.jar"/></permissions>\n',
                )
                zf.writestr(
                    "system/etc/permissions/privapp-permissions-miuicamera.xml",
                    '<permissions><privapp-permissions package="com.android.camera"><permission name="android.permission.CAMERA"/></privapp-permissions></permissions>\n',
                )
                zf.writestr("system/vendor/lib64/libxiaomi_vendor_test.so", b"\x7fELFvendor")

            counts = import_source_zip(src_zip, fake_repo)
            self.assertEqual(counts["apks"], 1)
            self.assertEqual(counts["app_libs"], 1)
            self.assertEqual(counts["skipped_xml"], 1)

            out_zip = tmp / "repackaged.zip"
            build_module(fake_repo, extra_apks=[], output_path=out_zip)
            errors = validate_zip(out_zip, allow_no_apk=False)
            self.assertEqual(errors, [], f"Validation errors: {errors}")

            with zipfile.ZipFile(out_zip, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("system/priv-app/MiuiCamera/MiuiCamera.apk", names)
                self.assertIn("system/priv-app/MiuiCamera/lib/arm64/libcam_inside.so", names)
                self.assertIn("system/priv-app/MiuiCamera/lib/arm64/libcam_adjacent.so", names)
                self.assertIn("system/system_ext/framework/miuicamera.jar", names)
                self.assertIn("system/system_ext/etc/permissions/miuicamera.xml", names)
                self.assertIn("system/vendor/lib64/libxiaomi_vendor_test.so", names)
                # Ensure legacy privapp-permissions was replaced with full auto-merged one
                priv_xml = zf.read("system/etc/permissions/privapp-permissions-miuicamera.xml").decode("utf-8")
                self.assertIn("com.xiaomi.camera.permission.LEICA_IMPORT", priv_xml)
                self.assertIn("android.permission.SYSTEM_CAMERA", priv_xml)

    def test_smali_patcher_safe_overrides(self) -> None:
        sample_smali = (
            ".class public Lcom/mi/config/DataItemFeature;\n"
            ".super Ljava/lang/Object;\n\n"
            ".method public isSupportLeicaWatermark()Z\n"
            "    .locals 2\n"
            "    const/4 v0, 0x0\n"
            "    return v0\n"
            ".end method\n\n"
            ".method public a()Z\n"
            "    .locals 2\n"
            '    const-string v0, "support_camera_lab"\n'
            "    invoke-virtual {p0, v0}, Lcom/mi/config/DataItemFeature;->getBool(Ljava/lang/String;)Z\n"
            "    move-result v0\n"
            "    return v0\n"
            ".end method\n"
        )
        patched, changed = patch_smali_bool_method(sample_smali, "isSupportLeicaWatermark", True)
        self.assertTrue(changed)
        self.assertIn("const/4 v0, 0x1", patched)

        patched2, changed2 = patch_smali_key_fallback(patched, "support_camera_lab", True)
        self.assertTrue(changed2)
        self.assertEqual(patched2.count("const/4 v0, 0x1"), 2)

        with tempfile.TemporaryDirectory(prefix="hyperos_patch_spec_") as tmp_str:
            tmp = Path(tmp_str)
            smali_file = tmp / "smali" / "com" / "mi" / "config" / "DataItemFeature.smali"
            smali_file.parent.mkdir(parents=True, exist_ok=True)
            smali_file.write_text(sample_smali, encoding="utf-8")

            spec_file = tmp / "spec.json"
            spec_file.write_text(
                '{"patches": ['
                '{"id": "leica_wm", "type": "smali_method_bool", "class_contains": "DataItemFeature", "method": "isSupportLeicaWatermark", "return": true},'
                '{"id": "lab_key", "type": "smali_key_bool", "class_contains": "DataItemFeature", "key": "support_camera_lab", "return": true}'
                "]}",
                encoding="utf-8",
            )
            applied = apply_patch_spec(tmp, spec_file)
            self.assertEqual(len(applied), 2)

    def test_evorez_card_hook_and_arsc_scrub(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyperos_evorez_card_") as tmp_str:
            tmp = Path(tmp_str)
            cpf = tmp / "smali_classes7" / "com" / "android" / "camera" / "fragment" / "settings" / "CameraPreferenceFragment.smali"
            cpf.parent.mkdir(parents=True, exist_ok=True)
            cpf.write_text(
                "    const v1, 0x7f130da2\n\n"
                '    const-string v2, "pref_lab"\n\n'
                "    invoke-virtual {p0, v0, v2, v1}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->xd(Landroidx/preference/PreferenceGroup;Ljava/lang/String;I)Lcom/android/camera/ui/ValuePreference;\n\n"
                "    const v1, 0x7f130da0\n\n"
                '    const-string v2, "pref_universal"\n\n'
                "    invoke-virtual {p0, v0, v2, v1}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->xd(Landroidx/preference/PreferenceGroup;Ljava/lang/String;I)Lcom/android/camera/ui/ValuePreference;\n\n"
                "    const v1, 0x7f130d29\n\n"
                "    const v2, 0x7f130d2a\n\n"
                '    const-string v3, "pref_privacy"\n\n'
                "    invoke-virtual {p0, v0, v3, v1, v2}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->kd(Landroidx/preference/PreferenceGroup;Ljava/lang/String;II)V\n\n"
                "    invoke-direct {p0, v0}, Lcom/android/camera/fragment/settings/CameraPreferenceFragment;->ph(Landroidx/preference/PreferenceCategory;)V\n\n"
                "    return-void\n",
                encoding="utf-8",
            )
            hits = inject_evorez_card_hooks(tmp)
            self.assertEqual(len(hits), 4)
            updated = cpf.read_text(encoding="utf-8")
            self.assertNotIn("pref_lab", updated)
            self.assertNotIn("pref_universal", updated)
            self.assertNotIn("0x7f130d29", updated)
            self.assertIn("Lmodify/EvorezCardPreference;->attachTo(Landroidx/preference/PreferenceScreen;)V", updated)

            arsc = tmp / "resources.arsc"
            orig_bytes = b"\x00\x01Telegram: @HolyBearHome\x00PayPal: @HolyBear0610\x00HolyBear\x00"
            arsc.write_bytes(orig_bytes)
            n = scrub_arsc_credits_inplace(
                arsc,
                [
                    (b"Telegram: @HolyBearHome", b"https://t.me/evorezlabs"),
                    (b"PayPal: @HolyBear0610", b"Web: Evorez.in       "),
                    (b"HolyBear", b"Evorez  "),
                ],
            )
            self.assertEqual(n, 3)
            new_bytes = arsc.read_bytes()
            self.assertEqual(len(new_bytes), len(orig_bytes))
            self.assertNotIn(b"HolyBear", new_bytes)
            self.assertIn(b"https://t.me/evorezlabs", new_bytes)

    def test_unlock_spec_safety_and_05x_zoom(self) -> None:
        spec = json.loads((REPO_ROOT / "patches" / "unlock_features.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in spec["patches"]}
        # Ensure MIUI proprietary 60fps EIS overrides stay disabled (prevents 60fps freeze on AOSP)
        self.assertFalse(by_id["fix_60fps_eis_freeze"]["return"])
        self.assertNotIn("unlock_1080p60_eis", by_id)
        self.assertNotIn("unlock_super_eis", by_id)
        self.assertNotIn("unlock_movie_solid_eis", by_id)
        # Ensure 0.5x + telephoto + 100x zoom & 60fps opmode freeze fix are present
        self.assertIn("0.5:1.0:2.0:5.0", by_id["outer_zoom_buttons_common_05x_tele"]["replace"])
        self.assertIn("0x3f000000", by_id["zoom_range_all_modes_05x_to_100x"]["replace"])
        self.assertIn("0x42c80000", by_id["zoom_range_all_modes_05x_to_100x"]["replace"])
        self.assertIn("get60FpsOperatingMode", by_id["fix_60fps_video_opmode_freeze"]["replace"])
        # Ensure version replacement, lab/universal removal, and Leica watermark are present
        self.assertEqual(by_id["replace_package_version_name"]["replace"], "-Universal Cam Evorez")
        self.assertFalse(by_id["disable_universal_settings_flag"]["return"])
        self.assertEqual(by_id["watermark_replace_mi_phone_upper"]["replace"], '"Leica"')

    def test_axml_version_name_patching_utf8_and_utf16(self) -> None:
        for utf8 in (False, True):
            raw_axml = _build_synthetic_axml(
                package="com.android.camera",
                version_name="Universal_5.0_Beta_8.3",
                version_code=500083000,
                min_sdk=34,
                target_sdk=35,
                permissions=["android.permission.CAMERA"],
                use_utf8=utf8,
            )
            patched_axml, hits = patch_axml_string(raw_axml, "Universal_5.0_Beta_8.3", "-Universal Cam Evorez")
            self.assertEqual(hits, 1)
            with tempfile.TemporaryDirectory(prefix="hyperos_axml_patch_") as tmp_str:
                apk = Path(tmp_str) / "test.apk"
                with zipfile.ZipFile(apk, "w") as zf:
                    zf.writestr("AndroidManifest.xml", patched_axml)
                info = inspect_apk(apk)
                self.assertEqual(info.package, "com.android.camera")
                self.assertEqual(info.version_name, "-Universal Cam Evorez")
                self.assertEqual(info.version_code, 500083000)
                self.assertEqual(info.permissions, ["android.permission.CAMERA"])


if __name__ == "__main__":
    unittest.main()
