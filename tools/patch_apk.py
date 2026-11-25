#!/usr/bin/env python3
"""
HYPEROS-CAM-AOSP APK Inspector & Safe Smali/Asset Patcher.

Uses apktool (--no-res) to safely inspect and patch MiuiCamera.apk bytecode
(smali) and assets without breaking MIUI's proprietary resource table, then
rebuilds, zipaligns (4KB page-aligned), and signs (v1+v2+v3) with apksigner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Keywords used during --inspect to locate feature flag classes/methods
FEATURE_CLASS_KEYWORDS = (
    "dataitemfeature",
    "camerasettings",
    "leica",
    "watermark",
    "universal",
    "holybear",
    "sevtinge",
    "feature",
    "lab",
    "promode",
    "masterfilter",
    "film",
    "supermoon",
    "config/device",
    "mi/config",
)

FEATURE_METHOD_KEYWORDS = (
    "leica",
    "watermark",
    "lab",
    "raw",
    "pro",
    "4k",
    "60fps",
    "film",
    "filter",
    "master",
    "clone",
    "moon",
    "night",
    "vlog",
    "hdr",
    "10bit",
    "log",
    "macro",
    "liveshot",
    "motionphoto",
    "dualvideo",
    "longexposure",
    "document",
    "histogram",
    "peak",
    "zebra",
    "teleprompter",
    "parallel",
    "shutter",
    "sound",
    "custom",
    "ai",
    "debug",
    "experimental",
)

METHOD_HEADER_RE = re.compile(
    r"^\.method\s+([^\n]*?)\s+([A-Za-z0-9_$-]+)\(\)Z\s*$",
    re.MULTILINE,
)


def _run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, capture_output=False)


def _find_tool(name: str, extra_paths: List[str] | None = None) -> str:
    found = shutil.which(name)
    if found:
        return found
    for p in extra_paths or []:
        if Path(p).is_file():
            return p
    # Search Android SDK build-tools if installed (common on GitHub Actions ubuntu-latest)
    sdk_root = Path("/usr/local/lib/android/sdk/build-tools")
    if sdk_root.is_dir():
        versions = sorted(sdk_root.iterdir(), reverse=True)
        for v in versions:
            cand = v / name
            if cand.is_file():
                return str(cand)
    raise FileNotFoundError(f"Required tool not found on PATH or Android SDK: {name}")


def decompile_no_res(apk_path: Path, out_dir: Path, apktool_bin: str) -> None:
    """Decompile DEX/smali + assets while keeping resources.arsc & res/ binary-intact."""
    cmd = [apktool_bin, "d", "--no-res", "-f", str(apk_path), "-o", str(out_dir)]
    _run(cmd)


def _find_smali_files(decompiled_dir: Path) -> List[Path]:
    files: List[Path] = []
    for sub in sorted(decompiled_dir.iterdir()):
        if sub.is_dir() and (sub.name == "smali" or sub.name.startswith("smali_classes")):
            files.extend(sub.rglob("*.smali"))
    return files


def inspect_decompiled_apk(apk_path: Path, decompiled_dir: Path) -> Dict[str, Any]:
    """Scan decompiled smali + assets + original APK structure for feature classes & flags."""
    smali_files = _find_smali_files(decompiled_dir)

    interesting_classes: List[Dict[str, Any]] = []
    readable_bool_methods: List[Dict[str, str]] = []
    feature_string_keys: set[str] = set()
    pref_xml_files: List[str] = []
    asset_files: List[str] = []

    with zipfile.ZipFile(apk_path, "r") as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("assets/"):
                asset_files.append(name)
            elif name.startswith("res/xml/") or ("pref" in name.lower() and name.endswith(".xml")):
                pref_xml_files.append(name)

    key_pattern = re.compile(
        r'"((?:support_|is_support_|pref_camera_|leica_|camera_lab_|miui_camera_|c_0x)[A-Za-z0-9_.-]{2,64})"'
    )

    for sf in smali_files:
        rel = sf.relative_to(decompiled_dir).as_posix()
        low_rel = rel.lower()
        try:
            text = sf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # Collect interesting string keys from relevant packages
        if any(k in low_rel for k in ("camera", "miui", "xiaomi", "config", "leica", "universal", "mod")):
            for m in key_pattern.finditer(text):
                if len(feature_string_keys) < 500:
                    feature_string_keys.add(m.group(1))

        # Check if class name matches feature keywords
        is_interesting_class = any(k in low_rel for k in FEATURE_CLASS_KEYWORDS)
        matched_methods: List[str] = []

        for m in METHOD_HEADER_RE.finditer(text):
            modifiers = m.group(1)
            mname = m.group(2)
            if "abstract" in modifiers or "native" in modifiers:
                continue
            low_m = mname.lower()
            if len(mname) >= 4 and any(k in low_m for k in FEATURE_METHOD_KEYWORDS):
                matched_methods.append(mname)
                if len(readable_bool_methods) < 400:
                    readable_bool_methods.append({"file": rel, "method": mname, "modifiers": modifiers.strip()})

        if is_interesting_class or len(matched_methods) >= 3:
            # Extract class descriptor from first line
            first_line = text.splitlines()[0] if text else ""
            interesting_classes.append(
                {
                    "file": rel,
                    "class_header": first_line,
                    "bool_method_count": len(matched_methods),
                    "sample_methods": matched_methods[:25],
                }
            )

    interesting_classes.sort(key=lambda x: (-x["bool_method_count"], x["file"]))

    # Specifically inspect com/mi/device/Common.smali (base class for all devices)
    common_false_methods: List[str] = []
    common_true_methods: List[str] = []
    common_other_methods: List[str] = []
    common_sample_snippet = ""
    common_file_rel = ""
    dataitem_classes: List[str] = []

    method_block_re = re.compile(
        r"^\.method\s+([^\n]*?)\s+([A-Za-z0-9_$-]+)\(\)Z\s*\n(.*?)^\.end method\b",
        re.MULTILINE | re.DOTALL,
    )
    const_false_re = re.compile(r"const/4\s+[vp]\d+,\s*0x0\b")
    const_true_re = re.compile(r"const/4\s+[vp]\d+,\s*0x1\b")

    for sf in smali_files:
        rel = sf.relative_to(decompiled_dir).as_posix()
        low_rel = rel.lower()
        if "dataitemfeature" in low_rel or "com/mi/config/" in low_rel:
            dataitem_classes.append(rel)
        if low_rel.endswith("/com/mi/device/common.smali"):
            common_file_rel = rel
            text = sf.read_text(encoding="utf-8", errors="ignore")
            common_sample_snippet = "\n".join(text.splitlines()[:60])
            for m in method_block_re.finditer(text):
                modifiers, mname, body = m.group(1), m.group(2), m.group(3)
                if "abstract" in modifiers or "native" in modifiers:
                    continue
                low_m = mname.lower()
                if any(k in low_m for k in FEATURE_METHOD_KEYWORDS) or low_m.startswith(("issupport", "support", "enable", "show", "allow")):
                    has_0 = bool(const_false_re.search(body))
                    has_1 = bool(const_true_re.search(body))
                    if has_0 and not has_1 and "invoke-" not in body:
                        common_false_methods.append(mname)
                    elif has_1 and not has_0 and "invoke-" not in body:
                        common_true_methods.append(mname)
                    else:
                        common_other_methods.append(mname)

    return {
        "apk": apk_path.name,
        "total_smali_files": len(smali_files),
        "asset_count": len(asset_files),
        "sample_assets": asset_files[:80],
        "pref_xml_files": pref_xml_files[:80],
        "interesting_classes": interesting_classes[:60],
        "readable_bool_methods_count": len(readable_bool_methods),
        "sample_bool_methods": readable_bool_methods[:120],
        "feature_string_keys": sorted(feature_string_keys)[:150],
        "common_file": common_file_rel,
        "common_false_methods": sorted(common_false_methods),
        "common_true_methods": sorted(common_true_methods),
        "common_other_methods": sorted(common_other_methods),
        "common_sample_snippet": common_sample_snippet,
        "dataitem_classes": sorted(dataitem_classes)[:40],
    }


def inspect_credits_and_settings(apk_path: Path, decompiled_dir: Path) -> str:
    """Find all HolyBear / credit / modification-description / Telegram strings & PreferenceFragment classes."""
    smali_files = _find_smali_files(decompiled_dir)
    credit_terms = (
        "holybear",
        "holy bear",
        "holy_bear",
        "modification",
        "t.me/",
        "telegram",
        "sevtinge",
        "universal_5.0",
        "beta_8",
        "leica mod",
        "4pda",
        "coolapk",
        "mod_desc",
        "mod_about",
    )

    smali_hits: List[str] = []
    pref_fragments: List[str] = []
    arsc_hits: List[str] = []

    for sf in smali_files:
        rel = sf.relative_to(decompiled_dir).as_posix()
        try:
            text = sf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        low = text.lower()

        if "preference" in rel.lower() and ("oncreatepreferences" in low or "addpreferencesfromresource" in low or "findpreference" in low):
            pref_fragments.append(rel)

        for term in credit_terms:
            if term in low:
                matched_lits = re.findall(r'"([^"\n]{3,180})"', text)
                relevant = [s for s in matched_lits if any(t in s.lower() for t in credit_terms)]
                if relevant:
                    sample = " | ".join(relevant[:6])
                    smali_hits.append(f"- `{rel}`: {sample}")
                else:
                    smali_hits.append(f"- `{rel}` (matched `{term}`)")
                break

    with zipfile.ZipFile(apk_path, "r") as zf:
        if "resources.arsc" in zf.namelist():
            arsc = zf.read("resources.arsc")
            for pat in (
                rb"[ -~]{0,50}[Hh]oly[\s_]*[Bb]ear[ -~]{0,100}",
                rb"[ -~]{0,40}[Mm]odification[ -~]{0,100}",
                rb"https?://t\.me/[A-Za-z0-9_/+.-]{2,60}",
                rb"https?://github\.com/[A-Za-z0-9_/+.-]{2,60}",
                rb"[ -~]{0,30}[Ss]evtinge[ -~]{0,80}",
                rb"[ -~]{0,30}[Uu]niversal.{0,20}[Bb]eta[ -~]{0,80}",
            ):
                for m in re.finditer(pat, arsc):
                    s = m.group(0).decode("utf-8", errors="ignore").strip()
                    if s and s not in arsc_hits and len(arsc_hits) < 120:
                        arsc_hits.append(s)

        xml_files = [
            n
            for n in sorted(zf.namelist())
            if n.startswith("res/xml/")
            or (n.startswith("res/layout/") and any(k in n.lower() for k in ("pref", "about", "setting", "universal", "mod", "leica")))
        ]

    lines = ["## Credits, Modification Description & Settings Inspection"]
    lines.append(f"### Smali matches ({len(smali_hits)})")
    lines.extend(smali_hits[:80] or ["- None"])
    lines.append(f"\n### PreferenceFragment classes ({len(pref_fragments)})")
    lines.extend([f"- `{p}`" for p in sorted(pref_fragments)[:40]] or ["- None"])
    lines.append(f"\n### `resources.arsc` matches ({len(arsc_hits)})")
    lines.extend([f"- `{s}`" for s in arsc_hits[:60]] or ["- None"])
    lines.append(f"\n### Preference / Settings XML resources ({len(xml_files)})")
    lines.extend([f"- `{x}`" for x in xml_files[:50]] or ["- None"])
    return "\n".join(lines) + "\n"


def format_inspect_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"## APK Inspection Report: `{report['apk']}`",
        f"- **Total smali files**: {report['total_smali_files']}",
        f"- **Assets**: {report['asset_count']} files",
        f"- **Base device class**: `{report.get('common_file') or 'not found'}`",
        f"- **`Common.smali` constant FALSE (`0x0`)**: {len(report.get('common_false_methods', []))}",
        f"- **`Common.smali` constant TRUE (`0x1`)**: {len(report.get('common_true_methods', []))}",
        f"- **`Common.smali` dynamic/other `()Z`**: {len(report.get('common_other_methods', []))}",
        "",
        "### `Common.smali` Header Sample",
        "```smali",
        report.get("common_sample_snippet", ""),
        "```",
        "",
        "### `com/mi/device/Common.smali` — Currently Disabled (`false`) Feature Methods (first 180)",
    ]
    for m in report.get("common_false_methods", [])[:180]:
        lines.append(f"- `{m}()Z`")

    lines.append("\n### `com/mi/device/Common.smali` — Dynamic/Hooked `()Z` Feature Methods (first 80)")
    for m in report.get("common_other_methods", [])[:80]:
        lines.append(f"- `{m}()Z`")

    lines.append("\n### `com/mi/device/Common.smali` — Already Enabled (`true`) Feature Methods (first 80)")
    for m in report.get("common_true_methods", [])[:80]:
        lines.append(f"- `{m}()Z`")

    return "\n".join(lines) + "\n"


def patch_smali_method_delegate(
    smali_text: str,
    method_sig: str,
    target_static_call: str,
) -> Tuple[str, bool]:
    """Replace the body of a zero-arg primitive method (e.g. `getMaxZoom()F` or `isSupportOCR()Z`)
    with a clean static delegation to `target_static_call` (e.g. `Lmodify/AdditionalToolsConfig;->getMaxZoom()F`)."""
    if ".interface " in smali_text[:200]:
        return smali_text, False

    pattern = re.compile(
        rf"(^\.method\s+([^\n]*?)\s+{re.escape(method_sig)}\s*\n)(.*?)(^\.end method\b)",
        re.MULTILINE | re.DOTALL,
    )
    changed = False

    def _repl(match: re.Match[str]) -> str:
        nonlocal changed
        header, modifiers, end_tag = match.group(1), match.group(2), match.group(4)
        if "abstract" in modifiers or "native" in modifiers:
            return match.group(0)
        changed = True
        return (
            f"{header}    .locals 1\n\n"
            f"    invoke-static {{}}, {target_static_call}\n\n"
            f"    move-result v0\n\n"
            f"    return v0\n"
            f"{end_tag}"
        )

    new_text = pattern.sub(_repl, smali_text, count=1)
    return new_text, changed


def patch_smali_bool_method(smali_text: str, method_name: str, return_value: bool) -> Tuple[str, bool]:
    """Safely replace the body of a non-abstract, non-native `method_name()Z` method
    with `.locals 1 / const/4 v0, 0x1|0x0 / return v0`."""
    if ".interface " in smali_text[:200]:
        return smali_text, False

    pattern = re.compile(
        rf"(^\.method\s+([^\n]*?)\s+{re.escape(method_name)}\(\)Z\s*\n)(.*?)(^\.end method\b)",
        re.MULTILINE | re.DOTALL,
    )

    changed = False

    def _repl(match: re.Match[str]) -> str:
        nonlocal changed
        header = match.group(1)
        modifiers = match.group(2)
        end_tag = match.group(4)
        if "abstract" in modifiers or "native" in modifiers:
            return match.group(0)
        val_hex = "0x1" if return_value else "0x0"
        changed = True
        return f"{header}    .locals 1\n\n    const/4 v0, {val_hex}\n\n    return v0\n{end_tag}"

    new_text = pattern.sub(_repl, smali_text, count=1)
    return new_text, changed


def patch_smali_key_fallback(smali_text: str, key_name: str, return_value: bool) -> Tuple[str, bool]:
    """Patch a `()Z` method whose body references a specific feature key string literal
    (useful when Xiaomi R8-obfuscates DataItemFeature method names into short names like a()/b())."""
    if ".interface " in smali_text[:200]:
        return smali_text, False
    if f'"{key_name}"' not in smali_text:
        return smali_text, False

    method_re = re.compile(
        r"(^\.method\s+([^\n]*?)\s+([A-Za-z0-9_$-]+)\(\)Z\s*\n)(.*?)(^\.end method\b)",
        re.MULTILINE | re.DOTALL,
    )
    changed = False

    def _repl(match: re.Match[str]) -> str:
        nonlocal changed
        header, modifiers, _mname, body, end_tag = (
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5),
        )
        if "abstract" in modifiers or "native" in modifiers:
            return match.group(0)
        if f'"{key_name}"' not in body:
            return match.group(0)
        # Only patch short getter-style methods (< 40 lines) so we never clobber complex init methods
        if body.count("\n") > 40:
            return match.group(0)
        val_hex = "0x1" if return_value else "0x0"
        changed = True
        return f"{header}    .locals 1\n\n    const/4 v0, {val_hex}\n\n    return v0\n{end_tag}"

    new_text = method_re.sub(_repl, smali_text)
    return new_text, changed


def _find_android_jar() -> str:
    platforms = Path("/usr/local/lib/android/sdk/platforms")
    if platforms.is_dir():
        dirs = sorted(platforms.glob("android-*"), reverse=True)
        for d in dirs:
            jar = d / "android.jar"
            if jar.is_file():
                return str(jar)
    android_home = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "")
    if android_home.is_dir():
        for d in sorted((android_home / "platforms").glob("android-*"), reverse=True):
            jar = d / "android.jar"
            if jar.is_file():
                return str(jar)
    raise FileNotFoundError("android.jar not found in Android SDK platforms/")


def compile_java_to_smali(
    java_src_dir: Path,
    java_stubs_dir: Path,
    orig_apk: Path,
    target_smali_root: Path,
    apktool_bin: str,
) -> List[str]:
    """Compile custom Java sources against android.jar + stubs -> d8 -> apktool smali."""
    javac_bin = _find_tool("javac")
    d8_bin = _find_tool("d8")
    android_jar = _find_android_jar()

    src_files = sorted(java_src_dir.rglob("*.java"))
    stub_files = sorted(java_stubs_dir.rglob("*.java")) if java_stubs_dir.is_dir() else []
    if not src_files:
        return []

    with tempfile.TemporaryDirectory(prefix="hyperos_java_smali_") as tmp_str:
        tmp = Path(tmp_str)
        classes_dir = tmp / "classes"
        dex_dir = tmp / "dex"
        classes_dir.mkdir()
        dex_dir.mkdir()

        cmd_javac = [
            javac_bin,
            "-source",
            "8",
            "-target",
            "8",
            "-encoding",
            "UTF-8",
            "-cp",
            android_jar,
            "-d",
            str(classes_dir),
            *[str(p) for p in stub_files + src_files],
        ]
        _run(cmd_javac)

        # Collect only compiled classes for our real sources (exclude androidx stubs)
        real_packages = {p.relative_to(java_src_dir).parts[0] for p in src_files}
        real_classes: List[str] = []
        for pkg in sorted(real_packages):
            pkg_dir = classes_dir / pkg
            if pkg_dir.is_dir():
                real_classes.extend(str(c) for c in sorted(pkg_dir.rglob("*.class")))

        if not real_classes:
            raise RuntimeError("javac produced no .class files for custom Java patches")

        _run([d8_bin, "--min-api", "26", "--lib", android_jar, "--output", str(dex_dir), *real_classes])

        # Wrap classes.dex + original AndroidManifest.xml into a tiny APK so apktool disassembles it identically
        mini_apk = tmp / "mini.apk"
        with zipfile.ZipFile(orig_apk, "r") as ozf:
            manifest_bytes = ozf.read("AndroidManifest.xml")
        with zipfile.ZipFile(mini_apk, "w", zipfile.ZIP_STORED) as mzf:
            mzf.writestr("AndroidManifest.xml", manifest_bytes)
            mzf.write(dex_dir / "classes.dex", "classes.dex")

        mini_out = tmp / "mini_out"
        _run([apktool_bin, "d", "--no-res", "-f", str(mini_apk), "-o", str(mini_out)])

        injected: List[str] = []
        for smali_file in sorted((mini_out / "smali").rglob("*.smali")):
            rel = smali_file.relative_to(mini_out / "smali")
            dest = target_smali_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(smali_file, dest)
            injected.append(rel.as_posix())
        return injected


def scrub_arsc_credits_inplace(arsc_path: Path, replacements: List[Tuple[bytes, bytes]]) -> int:
    """Safely replace exact ASCII credit strings inside binary resources.arsc with
    IDENTICAL byte-length ASCII strings so every ARSC length prefix and offset stays 100% valid."""
    if not arsc_path.is_file():
        return 0
    raw = arsc_path.read_bytes()
    orig_len = len(raw)
    total_hits = 0
    for old_b, new_b in replacements:
        if len(old_b) != len(new_b):
            raise ValueError(f"ARSC in-place replacement length mismatch: {len(old_b)} != {len(new_b)} ({old_b!r})")
        count = raw.count(old_b)
        if count:
            raw = raw.replace(old_b, new_b)
            total_hits += count
    if len(raw) != orig_len:
        raise RuntimeError("ARSC byte length changed unexpectedly!")
    if total_hits:
        arsc_path.write_bytes(raw)
    return total_hits


def _encode_axml_len8(n: int) -> bytes:
    if n >= 0x80:
        return bytes([((n >> 8) & 0x7F) | 0x80, n & 0xFF])
    return bytes([n])


def _decode_axml_len8(data: bytes, pos: int) -> Tuple[int, int]:
    b0 = data[pos]
    if b0 & 0x80:
        b1 = data[pos + 1]
        return ((b0 & 0x7F) << 8) | b1, pos + 2
    return b0, pos + 1


def _encode_axml_len16(n: int) -> bytes:
    if n >= 0x8000:
        return struct.pack("<HH", ((n >> 16) & 0x7FFF) | 0x8000, n & 0xFFFF)
    return struct.pack("<H", n)


def _decode_axml_len16(data: bytes, pos: int) -> Tuple[int, int]:
    v0 = struct.unpack_from("<H", data, pos)[0]
    if v0 & 0x8000:
        v1 = struct.unpack_from("<H", data, pos + 2)[0]
        return ((v0 & 0x7FFF) << 16) | v1, pos + 4
    return v0, pos + 2


def patch_axml_string(axml_bytes: bytes, old_sub: str, new_sub: str) -> Tuple[bytes, int]:
    """Safely replace substring(s) inside a binary AndroidManifest.xml (AXML) string pool,
    preserving exact chunk structure, encoding (UTF-8 or UTF-16LE), styles, and 4-byte alignment."""
    if len(axml_bytes) < 36:
        raise ValueError("Invalid AXML: too short")
    root_type, root_hdr_size, _root_size = struct.unpack_from("<HHI", axml_bytes, 0)
    if root_type != 0x0003:
        raise ValueError(f"Unexpected AXML root chunk type: 0x{root_type:04x}")

    sp_off = root_hdr_size
    sp_type, sp_hdr_size, sp_chunk_size = struct.unpack_from("<HHI", axml_bytes, sp_off)
    if sp_type != 0x0001:
        raise ValueError(f"Expected string pool chunk (0x0001), got 0x{sp_type:04x}")

    string_count, style_count, flags, strings_start, styles_start = struct.unpack_from(
        "<IIIII", axml_bytes, sp_off + 8
    )
    is_utf8 = bool(flags & 0x00000100)

    offsets_base = sp_off + sp_hdr_size
    string_offsets = [
        struct.unpack_from("<I", axml_bytes, offsets_base + i * 4)[0]
        for i in range(string_count)
    ]
    style_offsets_bytes = axml_bytes[
        offsets_base + string_count * 4 : offsets_base + (string_count + style_count) * 4
    ]

    strings_abs = sp_off + strings_start
    strings: List[str] = []
    for rel_off in string_offsets:
        p = strings_abs + rel_off
        if is_utf8:
            _u16_len, p = _decode_axml_len8(axml_bytes, p)
            u8_len, p = _decode_axml_len8(axml_bytes, p)
            strings.append(axml_bytes[p : p + u8_len].decode("utf-8", errors="surrogatepass"))
        else:
            u16_len, p = _decode_axml_len16(axml_bytes, p)
            strings.append(axml_bytes[p : p + u16_len * 2].decode("utf-16le", errors="surrogatepass"))

    hits = 0
    new_strings: List[str] = []
    for s in strings:
        if old_sub in s:
            new_strings.append(s.replace(old_sub, new_sub))
            hits += 1
        else:
            new_strings.append(s)

    if hits == 0:
        return axml_bytes, 0

    # Rebuild encoded string buffer & offset table
    new_offsets: List[int] = []
    buf = bytearray()
    for s in new_strings:
        new_offsets.append(len(buf))
        u16_bytes = s.encode("utf-16le", errors="surrogatepass")
        u16_units = len(u16_bytes) // 2
        if is_utf8:
            u8_bytes = s.encode("utf-8", errors="surrogatepass")
            buf.extend(_encode_axml_len8(u16_units))
            buf.extend(_encode_axml_len8(len(u8_bytes)))
            buf.extend(u8_bytes)
            buf.append(0)
        else:
            buf.extend(_encode_axml_len16(u16_units))
            buf.extend(u16_bytes)
            buf.extend(b"\x00\x00")

    while len(buf) % 4 != 0:
        buf.append(0)

    # Preserve styles data if present
    if styles_start != 0 and style_count > 0:
        styles_abs = sp_off + styles_start
        styles_data = axml_bytes[styles_abs : sp_off + sp_chunk_size]
        new_styles_start = strings_start + len(buf)
    else:
        styles_data = b""
        new_styles_start = 0

    new_sp_body = bytearray()
    for off in new_offsets:
        new_sp_body.extend(struct.pack("<I", off))
    new_sp_body.extend(style_offsets_bytes)
    # Keep exact strings_start padding if any
    header_plus_offsets = sp_hdr_size + len(new_sp_body)
    if header_plus_offsets < strings_start:
        new_sp_body.extend(b"\x00" * (strings_start - header_plus_offsets))
    new_sp_body.extend(buf)
    new_sp_body.extend(styles_data)
    while (sp_hdr_size + len(new_sp_body)) % 4 != 0:
        new_sp_body.append(0)

    # Clear SORTED_FLAG (bit 0) since replacing a string can change lexicographical order
    new_flags = flags & ~0x00000001
    new_sp_chunk_size = sp_hdr_size + len(new_sp_body)
    new_sp_header = struct.pack(
        "<HHIIIIII",
        sp_type,
        sp_hdr_size,
        new_sp_chunk_size,
        string_count,
        style_count,
        new_flags,
        strings_start,
        new_styles_start,
    )
    if sp_hdr_size > len(new_sp_header):
        new_sp_header += axml_bytes[sp_off + len(new_sp_header) : sp_off + sp_hdr_size]

    rest_chunks = axml_bytes[sp_off + sp_chunk_size :]
    new_total_size = root_hdr_size + new_sp_chunk_size + len(rest_chunks)
    new_root_header = struct.pack("<HHI", root_type, root_hdr_size, new_total_size)
    if root_hdr_size > 8:
        new_root_header += axml_bytes[8:root_hdr_size]

    rebuilt = new_root_header + new_sp_header + bytes(new_sp_body) + rest_chunks
    return rebuilt, hits


def inject_evorez_card_hooks(decompiled_dir: Path) -> List[Dict[str, Any]]:
    """Remove legacy HolyBear pref_privacy, Lab Settings (pref_lab), and Universal Settings
    (pref_universal) entries, and attach EvorezCardPreference at the bottom of Camera Settings."""
    results: List[Dict[str, Any]] = []
    smali_files = _find_smali_files(decompiled_dir)

    # 1. Patch CameraPreferenceFragment.smali
    for sf in smali_files:
        rel = sf.relative_to(decompiled_dir).as_posix()
        if not rel.endswith("/com/android/camera/fragment/settings/CameraPreferenceFragment.smali"):
            continue
        text = sf.read_text(encoding="utf-8", errors="ignore")
        modified = False

        # Remove pref_lab (Lab Settings)
        lab_pref_re = re.compile(
            r"\s*const v1, 0x7f130da2\s*\n\s*const-string v2, \"pref_lab\"\s*\n\s*invoke-virtual \{p0, v0, v2, v1\}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->xd\(Landroidx/preference/PreferenceGroup;Ljava/lang/String;I\)Lcom/android/camera/ui/ValuePreference;\s*\n",
            re.MULTILINE,
        )
        new_text, n_lab = lab_pref_re.subn("\n", text)
        if n_lab > 0:
            text = new_text
            modified = True
            results.append(
                {
                    "id": "remove_lab_settings_pref",
                    "file": rel,
                    "target": "Removed pref_lab (Lab Settings)",
                    "description": "Removed Lab Settings entry from Camera Settings",
                }
            )

        # Remove pref_universal (Universal Settings)
        univ_pref_re = re.compile(
            r"\s*const v1, 0x7f130da0\s*\n\s*const-string v2, \"pref_universal\"\s*\n\s*invoke-virtual \{p0, v0, v2, v1\}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->xd\(Landroidx/preference/PreferenceGroup;Ljava/lang/String;I\)Lcom/android/camera/ui/ValuePreference;\s*\n",
            re.MULTILINE,
        )
        new_text, n_univ = univ_pref_re.subn("\n", text)
        if n_univ > 0:
            text = new_text
            modified = True
            results.append(
                {
                    "id": "remove_universal_settings_pref",
                    "file": rel,
                    "target": "Removed pref_universal (Universal Settings)",
                    "description": "Removed Universal Settings entry from Camera Settings",
                }
            )

        # Remove the legacy pref_privacy (Modification Description [Original author: @HolyBearHome]) block
        legacy_pref_re = re.compile(
            r"\s*const v1, 0x7f130d29\s*\n\s*const v2, 0x7f130d2a\s*\n\s*const-string v3, \"pref_privacy\"\s*\n\s*invoke-virtual \{p0, v0, v3, v1, v2\}, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->kd\(Landroidx/preference/PreferenceGroup;Ljava/lang/String;II\)V\s*\n",
            re.MULTILINE,
        )
        new_text, n_removed = legacy_pref_re.subn("\n", text)
        if n_removed > 0:
            text = new_text
            modified = True
            results.append(
                {
                    "id": "remove_legacy_holybear_pref",
                    "file": rel,
                    "target": "Removed legacy pref_privacy (HolyBear Modification Description)",
                    "description": "Removed old HolyBear Modification Description & PayPal/Telegram credit item",
                }
            )

        # Attach EvorezCardPreference at the bottom right after ph(v0)
        ph_anchor = "invoke-direct {p0, v0}, Lcom/android/camera/fragment/settings/CameraPreferenceFragment;->ph(Landroidx/preference/PreferenceCategory;)V"
        if ph_anchor in text and "Lmodify/EvorezCardPreference;->attachTo(" not in text:
            inject_block = (
                ph_anchor
                + "\n\n    iget-object v1, p0, Lcom/android/camera/fragment/settings/BasePreferenceFragment;->mPreferenceGroup:Landroidx/preference/PreferenceScreen;\n\n"
                + "    invoke-static {v1}, Lmodify/EvorezCardPreference;->attachTo(Landroidx/preference/PreferenceScreen;)V"
            )
            text = text.replace(ph_anchor, inject_block, 1)
            modified = True
            results.append(
                {
                    "id": "inject_evorez_bottom_card",
                    "file": rel,
                    "target": "CameraPreferenceFragment -> EvorezCardPreference.attachTo(mPreferenceGroup)",
                    "description": "Added frosted glass Evorez bottom card with Telegram & GitHub pills",
                }
            )

        if modified:
            sf.write_text(text, encoding="utf-8")

    return results


def apply_patch_spec(
    decompiled_dir: Path,
    spec_path: Path,
    orig_apk: Optional[Path] = None,
    apktool_bin: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply declarative patch rules from a JSON spec file and return applied patch log."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    rules = spec.get("patches", [])
    smali_files = _find_smali_files(decompiled_dir)
    applied: List[Dict[str, Any]] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rtype = rule.get("type")
        rule_id = rule.get("id", "unnamed")
        desc = rule.get("description", "")

        if rtype == "smali_method_bool":
            class_sub = rule["class_contains"].lower()
            method_name = rule["method"]
            ret_val = bool(rule["return"])
            hits = 0
            for sf in smali_files:
                rel = sf.relative_to(decompiled_dir).as_posix()
                if class_sub not in rel.lower():
                    continue
                old_text = sf.read_text(encoding="utf-8", errors="ignore")
                new_text, changed = patch_smali_bool_method(old_text, method_name, ret_val)
                if changed:
                    sf.write_text(new_text, encoding="utf-8")
                    hits += 1
                    applied.append({"id": rule_id, "file": rel, "target": f"{method_name}()Z -> {ret_val}", "description": desc})
            if hits == 0:
                print(f"[PATCH] Skipped (no match): {rule_id} ({class_sub} :: {method_name})")

        elif rtype == "smali_method_delegate":
            class_sub = rule["class_contains"].lower()
            method_sig = rule["method_sig"]
            target_call = rule["target"]
            hits = 0
            for sf in smali_files:
                rel = sf.relative_to(decompiled_dir).as_posix()
                if class_sub not in rel.lower():
                    continue
                old_text = sf.read_text(encoding="utf-8", errors="ignore")
                new_text, changed = patch_smali_method_delegate(old_text, method_sig, target_call)
                if changed:
                    sf.write_text(new_text, encoding="utf-8")
                    hits += 1
                    applied.append({"id": rule_id, "file": rel, "target": f"{method_sig} -> {target_call}", "description": desc})
            if hits == 0:
                print(f"[PATCH] Skipped (no match): {rule_id} ({class_sub} :: {method_sig})")

        elif rtype == "smali_key_bool":
            class_sub = rule.get("class_contains", "").lower()
            key_name = rule["key"]
            ret_val = bool(rule["return"])
            hits = 0
            for sf in smali_files:
                rel = sf.relative_to(decompiled_dir).as_posix()
                if class_sub and class_sub not in rel.lower():
                    continue
                old_text = sf.read_text(encoding="utf-8", errors="ignore")
                new_text, changed = patch_smali_key_fallback(old_text, key_name, ret_val)
                if changed:
                    sf.write_text(new_text, encoding="utf-8")
                    hits += 1
                    applied.append({"id": rule_id, "file": rel, "target": f'key "{key_name}" -> {ret_val}', "description": desc})
            if hits == 0:
                print(f"[PATCH] Skipped (no match): {rule_id} (key={key_name})")

        elif rtype == "smali_replace":
            class_sub = rule.get("class_contains", "").lower()
            find_str = rule["find"]
            repl_str = rule["replace"]
            hits = 0
            hit_files: List[str] = []
            for sf in smali_files:
                rel = sf.relative_to(decompiled_dir).as_posix()
                if class_sub and class_sub not in rel.lower():
                    continue
                old_text = sf.read_text(encoding="utf-8", errors="ignore")
                if find_str in old_text:
                    new_text = old_text.replace(find_str, repl_str)
                    sf.write_text(new_text, encoding="utf-8")
                    hits += 1
                    hit_files.append(rel)
            if hits == 0:
                print(f"[PATCH] Skipped (no match): {rule_id}")
            elif hits <= 4:
                for rel in hit_files:
                    applied.append({"id": rule_id, "file": rel, "target": "smali_replace", "description": desc})
            else:
                applied.append({"id": rule_id, "file": f"{hits} smali files ({hit_files[0]}, ...)", "target": f"{find_str} -> {repl_str}", "description": desc})

        elif rtype == "axml_replace":
            find_list = rule.get("find_any") or [rule["find"]]
            repl_str = rule["replace"]
            manifest_candidates = [
                decompiled_dir / "AndroidManifest.xml",
                decompiled_dir / "original" / "AndroidManifest.xml",
            ]
            total_hits = 0
            matched_find = ""
            for mf in manifest_candidates:
                if not mf.is_file():
                    continue
                raw = mf.read_bytes()
                for find_str in find_list:
                    if raw[:2] == b"\x03\x00":
                        new_raw, h = patch_axml_string(raw, find_str, repl_str)
                        if h:
                            raw = new_raw
                            mf.write_bytes(raw)
                            total_hits += h
                            matched_find = find_str
                    else:
                        txt = raw.decode("utf-8", errors="ignore")
                        if find_str in txt:
                            raw = txt.replace(find_str, repl_str).encode("utf-8")
                            mf.write_bytes(raw)
                            total_hits += 1
                            matched_find = find_str
            yml = decompiled_dir / "apktool.yml"
            if yml.is_file():
                ytxt = yml.read_text(encoding="utf-8", errors="ignore")
                for find_str in find_list:
                    if find_str in ytxt:
                        ytxt = ytxt.replace(find_str, repl_str)
                        yml.write_text(ytxt, encoding="utf-8")
            if total_hits:
                applied.append({"id": rule_id, "file": "AndroidManifest.xml", "target": f"{matched_find} -> {repl_str}", "description": desc})
            else:
                print(f"[PATCH] Skipped (no match): {rule_id}")

        elif rtype == "asset_json_set":
            asset_glob = rule["asset_glob"]
            key_path = rule["key_path"]
            value = rule["value"]
            for af in sorted(decompiled_dir.glob(asset_glob)):
                if not af.is_file():
                    continue
                try:
                    data = json.loads(af.read_text(encoding="utf-8"))
                except Exception:
                    continue
                cur = data
                parts = key_path.split(".")
                for p in parts[:-1]:
                    if not isinstance(cur, dict):
                        cur = None
                        break
                    cur = cur.setdefault(p, {})
                if isinstance(cur, dict):
                    cur[parts[-1]] = value
                    af.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    rel = af.relative_to(decompiled_dir).as_posix()
                    applied.append({"id": rule_id, "file": rel, "target": f"{key_path}={value!r}", "description": desc})

        elif rtype == "asset_xml_bool":
            asset_glob = rule["asset_glob"]
            bool_name = rule["name"]
            value = "true" if rule["value"] else "false"
            for af in sorted(decompiled_dir.glob(asset_glob)):
                if not af.is_file():
                    continue
                try:
                    tree = ET.parse(af)
                    root = tree.getroot()
                except Exception:
                    continue
                updated = False
                for el in root.findall(f".//bool[@name='{bool_name}']"):
                    el.text = value
                    updated = True
                if not updated:
                    new_el = ET.SubElement(root, "bool", {"name": bool_name})
                    new_el.text = value
                    updated = True
                if updated:
                    tree.write(af, encoding="utf-8", xml_declaration=True)
                    rel = af.relative_to(decompiled_dir).as_posix()
                    applied.append({"id": rule_id, "file": rel, "target": f'<bool name="{bool_name}">{value}</bool>', "description": desc})

        elif rtype == "compile_java_smali":
            if orig_apk is None or apktool_bin is None:
                print(f"[PATCH] Skipped {rule_id}: requires orig_apk and apktool_bin")
                continue
            java_src = REPO_ROOT / rule["java_src_dir"]
            java_stubs = REPO_ROOT / rule.get("java_stubs_dir", "patches/java_stubs")
            # Place compiled smali into the highest smali_classesN folder to avoid DEX 64K limit
            smali_roots = sorted(
                [d for d in decompiled_dir.iterdir() if d.is_dir() and (d.name == "smali" or d.name.startswith("smali_classes"))],
                key=lambda p: int(p.name.replace("smali_classes", "")) if p.name.startswith("smali_classes") else 1,
            )
            target_root = smali_roots[-1] if smali_roots else (decompiled_dir / "smali")
            injected_list = compile_java_to_smali(java_src, java_stubs, orig_apk, target_root, apktool_bin)
            for inj in injected_list:
                applied.append({"id": rule_id, "file": f"{target_root.name}/{inj}", "target": "compiled Java -> smali", "description": desc})
            smali_files = _find_smali_files(decompiled_dir)

        elif rtype == "remove_smali_files":
            patterns = [p.lower() for p in rule.get("path_contains", [])]
            removed = 0
            for sf in list(smali_files):
                rel = sf.relative_to(decompiled_dir).as_posix()
                if any(pat in rel.lower() for pat in patterns):
                    sf.unlink(missing_ok=True)
                    smali_files.remove(sf)
                    removed += 1
                    applied.append({"id": rule_id, "file": rel, "target": "removed credit class", "description": desc})
            if removed == 0:
                print(f"[PATCH] Skipped (no match): {rule_id}")

        elif rtype == "inject_evorez_card":
            card_hits = inject_evorez_card_hooks(decompiled_dir)
            applied.extend(card_hits)

        elif rtype == "scrub_arsc_credits":
            arsc_file = decompiled_dir / "resources.arsc"
            pairs: List[Tuple[bytes, bytes]] = []
            for item in rule.get("replacements", []):
                old_b = item["old"].encode("utf-8")
                new_b = item["new"].encode("utf-8")
                if len(new_b) < len(old_b):
                    new_b = new_b + b" " * (len(old_b) - len(new_b))
                elif len(new_b) > len(old_b):
                    raise ValueError(f"ARSC replacement longer than original: {item['new']!r} > {item['old']!r}")
                pairs.append((old_b, new_b))
            n_scrubbed = scrub_arsc_credits_inplace(arsc_file, pairs)
            if n_scrubbed:
                applied.append({"id": rule_id, "file": "resources.arsc", "target": f"scrubbed {n_scrubbed} legacy credit string(s)", "description": desc})

    return applied


def rebuild_align_sign(
    decompiled_dir: Path,
    out_apk: Path,
    apktool_bin: str,
    keystore_path: Optional[Path] = None,
) -> None:
    """Rebuild APK with apktool, 4KB page-align with zipalign, and sign (v1+v2+v3) with apksigner."""
    zipalign_bin = _find_tool("zipalign")
    apksigner_bin = _find_tool("apksigner")
    keytool_bin = _find_tool("keytool")

    with tempfile.TemporaryDirectory(prefix="hyperos_sign_") as tmp_str:
        tmp = Path(tmp_str)
        unsigned_apk = tmp / "unsigned.apk"
        aligned_apk = tmp / "aligned.apk"

        _run([apktool_bin, "b", str(decompiled_dir), "-o", str(unsigned_apk)])
        _run([zipalign_bin, "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)])

        if keystore_path is None or not keystore_path.is_file():
            ks = tmp / "hyperos_cam_aosp.jks"
            _run(
                [
                    keytool_bin,
                    "-genkeypair",
                    "-v",
                    "-keystore",
                    str(ks),
                    "-storepass",
                    "hyperoscam",
                    "-keypass",
                    "hyperoscam",
                    "-alias",
                    "hyperoscam",
                    "-keyalg",
                    "RSA",
                    "-keysize",
                    "2048",
                    "-validity",
                    "10000",
                    "-dname",
                    "CN=HYPEROS-CAM-AOSP, OU=Mod, O=AOSP, L=Global, S=Global, C=US",
                ]
            )
            ks_pass = "pass:hyperoscam"
            ks_alias = "hyperoscam"
        else:
            ks = keystore_path
            ks_pass = "pass:hyperoscam"
            ks_alias = "hyperoscam"

        out_apk.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                apksigner_bin,
                "sign",
                "--ks",
                str(ks),
                "--ks-key-alias",
                ks_alias,
                "--ks-pass",
                ks_pass,
                "--key-pass",
                ks_pass,
                "--v1-signing-enabled",
                "true",
                "--v2-signing-enabled",
                "true",
                "--v3-signing-enabled",
                "true",
                "--out",
                str(out_apk),
                str(aligned_apk),
            ]
        )
        _run([apksigner_bin, "verify", "--verbose", str(out_apk)])


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Inspect or safely patch MiuiCamera.apk smali & assets.")
    parser.add_argument("apk", help="Path to MiuiCamera.apk")
    parser.add_argument("--apktool", default="apktool", help="Path to apktool executable (default: apktool)")
    parser.add_argument("--inspect-json", default=None, help="Write JSON inspection report to this path")
    parser.add_argument("--inspect-md", default=None, help="Write Markdown inspection report to this path")
    parser.add_argument("--patch-spec", default=None, help="Path to JSON patch spec (e.g. patches/unlock_features.json)")
    parser.add_argument("--out-apk", default=None, help="Output path for rebuilt & signed APK (required with --patch-spec)")
    parser.add_argument("--patch-report", default=None, help="Write applied patches Markdown summary here")
    args = parser.parse_args(argv)

    apk_path = Path(args.apk).resolve()
    if not apk_path.is_file():
        print(f"[ERROR] APK not found: {apk_path}", file=sys.stderr)
        return 1

    apktool_bin = _find_tool(args.apktool)

    with tempfile.TemporaryDirectory(prefix="hyperos_apktool_") as tmp_str:
        decompiled_dir = Path(tmp_str) / "apk_src"
        decompile_no_res(apk_path, decompiled_dir, apktool_bin)

        if args.inspect_json or args.inspect_md:
            report = inspect_decompiled_apk(apk_path, decompiled_dir)
            if args.inspect_json:
                Path(args.inspect_json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(f"[INSPECT] Wrote JSON report: {args.inspect_json}")
            if args.inspect_md:
                md = inspect_credits_and_settings(apk_path, decompiled_dir) + "\n" + format_inspect_markdown(report)
                Path(args.inspect_md).write_text(md, encoding="utf-8")
                print(f"[INSPECT] Wrote Markdown report: {args.inspect_md}")

        if args.patch_spec:
            if not args.out_apk:
                print("[ERROR] --out-apk is required when using --patch-spec", file=sys.stderr)
                return 1
            applied = apply_patch_spec(
                decompiled_dir,
                Path(args.patch_spec),
                orig_apk=apk_path,
                apktool_bin=apktool_bin,
            )
            print(f"[PATCH] Applied {len(applied)} patch(es).")
            for entry in applied:
                print(f"  -> [{entry['id']}] {entry['file']}: {entry['target']}")

            if args.patch_report:
                md_lines = [f"### Applied APK Patches ({len(applied)})", ""]
                if not applied:
                    md_lines.append("- No patch rules matched this APK.")
                else:
                    for entry in applied:
                        md_lines.append(f"- **{entry['id']}** (`{entry['file']}`): `{entry['target']}` — {entry['description']}")
                Path(args.patch_report).write_text("\n".join(md_lines) + "\n", encoding="utf-8")

            if applied:
                rebuild_align_sign(decompiled_dir, Path(args.out_apk), apktool_bin)
                print(f"[OK] Rebuilt & signed patched APK: {args.out_apk}")
            else:
                print("[INFO] Zero patches applied; copying original APK unchanged.")
                shutil.copy2(apk_path, Path(args.out_apk))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
