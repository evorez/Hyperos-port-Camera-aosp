#!/usr/bin/env python3
"""
Pure-Python Android APK & Binary XML (AXML) Inspector.

Parses AndroidManifest.xml directly from an APK without requiring aapt2/java,
extracting package name, versionCode/versionName, SDK levels, declared
permissions, required shared libraries, and bundled native .so libraries.
"""

from __future__ import annotations

import dataclasses
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Android binary XML chunk types (frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h)
RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_RESOURCE_MAP_TYPE = 0x0180

# String pool flags
UTF8_FLAG = 1 << 8

# TypedValue data types
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

# Standard android.R.attr resource IDs (fallback if attribute names are stripped)
ANDROID_ATTR_IDS = {
    0x01010003: "name",
    0x0101020C: "minSdkVersion",
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x01010270: "targetSdkVersion",
    0x0101028E: "required",
    0x010104EA: "extractNativeLibs",
}


@dataclasses.dataclass
class ApkInfo:
    path: str
    package: str
    version_code: Optional[int]
    version_name: Optional[str]
    min_sdk: Optional[int]
    target_sdk: Optional[int]
    extract_native_libs: Optional[bool]
    permissions: List[str]
    uses_libraries: List[Dict[str, object]]
    native_libs: Dict[str, List[Dict[str, object]]]

    def to_dict(self) -> Dict[str, object]:
        return dataclasses.asdict(self)


def _read_u8_len(data: bytes, offset: int) -> Tuple[int, int]:
    """Read UTF-8 string pool length (1 or 2 bytes)."""
    first = data[offset]
    if first & 0x80:
        second = data[offset + 1]
        return ((first & 0x7F) << 8) | second, offset + 2
    return first, offset + 1


def _read_u16_len(data: bytes, offset: int) -> Tuple[int, int]:
    """Read UTF-16 string pool length (2 or 4 bytes)."""
    first = struct.unpack_from("<H", data, offset)[0]
    if first & 0x8000:
        second = struct.unpack_from("<H", data, offset + 2)[0]
        return ((first & 0x7FFF) << 16) | second, offset + 4
    return first, offset + 2


def _parse_string_pool(chunk: bytes) -> List[str]:
    if len(chunk) < 28:
        raise ValueError("Truncated AXML string pool header")

    _ctype, header_size, _chunk_size, string_count, _style_count, flags, strings_start, _styles_start = (
        struct.unpack_from("<HHIIIIII", chunk, 0)
    )
    is_utf8 = bool(flags & UTF8_FLAG)

    offsets = [
        struct.unpack_from("<I", chunk, header_size + i * 4)[0]
        for i in range(string_count)
    ]

    strings: List[str] = []
    for rel_off in offsets:
        pos = strings_start + rel_off
        if pos >= len(chunk):
            strings.append("")
            continue
        if is_utf8:
            # First length is UTF-16 char count, second is UTF-8 byte count
            _char_len, pos = _read_u8_len(chunk, pos)
            byte_len, pos = _read_u8_len(chunk, pos)
            raw = chunk[pos : pos + byte_len]
            strings.append(raw.decode("utf-8", errors="replace"))
        else:
            char_len, pos = _read_u16_len(chunk, pos)
            raw = chunk[pos : pos + char_len * 2]
            strings.append(raw.decode("utf-16-le", errors="replace"))
    return strings


def parse_axml_bytes(data: bytes) -> Dict[str, object]:
    """Parse binary AndroidManifest.xml bytes into structured manifest metadata."""
    if len(data) < 8:
        raise ValueError("Invalid AXML: file too short")

    root_type, root_header_size, _root_size = struct.unpack_from("<HHI", data, 0)
    if root_type != RES_XML_TYPE:
        raise ValueError(f"Expected RES_XML_TYPE (0x0003), got 0x{root_type:04x}")

    strings: List[str] = []
    res_map: List[int] = []

    package = ""
    version_code: Optional[int] = None
    version_name: Optional[str] = None
    min_sdk: Optional[int] = None
    target_sdk: Optional[int] = None
    extract_native_libs: Optional[bool] = None
    permissions: List[str] = []
    uses_libraries: List[Dict[str, object]] = []

    pos = root_header_size
    total_len = len(data)

    while pos + 8 <= total_len:
        ctype, header_size, chunk_size = struct.unpack_from("<HHI", data, pos)
        if chunk_size < 8 or pos + chunk_size > total_len:
            break

        chunk = data[pos : pos + chunk_size]

        if ctype == RES_STRING_POOL_TYPE:
            strings = _parse_string_pool(chunk)
        elif ctype == RES_XML_RESOURCE_MAP_TYPE:
            count = (chunk_size - header_size) // 4
            res_map = [
                struct.unpack_from("<I", chunk, header_size + i * 4)[0]
                for i in range(count)
            ]
        elif ctype == RES_XML_START_ELEMENT_TYPE:
            # ResXMLTree_node (16 bytes header) + ResXMLTree_attrExt (20 bytes)
            ext_off = header_size
            if ext_off + 20 <= len(chunk):
                _ns_idx, name_idx, attr_start, attr_size, attr_count, _id_idx, _class_idx, _style_idx = (
                    struct.unpack_from("<iiHHHHHH", chunk, ext_off)
                )
                tag = strings[name_idx] if 0 <= name_idx < len(strings) else ""

                attrs: Dict[str, object] = {}
                cur = ext_off + attr_start
                for _ in range(attr_count):
                    if cur + 20 > len(chunk):
                        break
                    _a_ns, a_name_idx, a_raw_val, _val_size, _zero, val_type, val_data = (
                        struct.unpack_from("<iiiHBBI", chunk, cur)
                    )
                    cur += attr_size

                    attr_name = ""
                    if 0 <= a_name_idx < len(strings) and strings[a_name_idx]:
                        attr_name = strings[a_name_idx]
                    elif 0 <= a_name_idx < len(res_map):
                        attr_name = ANDROID_ATTR_IDS.get(res_map[a_name_idx], "")

                    if not attr_name:
                        continue

                    if val_type == TYPE_STRING and 0 <= val_data < len(strings):
                        val: object = strings[val_data]
                    elif 0 <= a_raw_val < len(strings) and val_type == TYPE_STRING:
                        val = strings[a_raw_val]
                    elif val_type == TYPE_INT_BOOLEAN:
                        val = val_data != 0
                    elif val_type in (TYPE_INT_DEC, TYPE_INT_HEX):
                        # Signed 32-bit int
                        val = val_data if val_data < 0x80000000 else val_data - 0x100000000
                    elif 0 <= a_raw_val < len(strings):
                        val = strings[a_raw_val]
                    else:
                        val = val_data

                    attrs[attr_name] = val

                if tag == "manifest":
                    if isinstance(attrs.get("package"), str):
                        package = str(attrs["package"])
                    if isinstance(attrs.get("versionCode"), int):
                        version_code = int(attrs["versionCode"])
                    if attrs.get("versionName") is not None:
                        version_name = str(attrs["versionName"])
                elif tag == "uses-sdk":
                    if isinstance(attrs.get("minSdkVersion"), int):
                        min_sdk = int(attrs["minSdkVersion"])
                    if isinstance(attrs.get("targetSdkVersion"), int):
                        target_sdk = int(attrs["targetSdkVersion"])
                elif tag == "application":
                    if isinstance(attrs.get("extractNativeLibs"), bool):
                        extract_native_libs = bool(attrs["extractNativeLibs"])
                elif tag in ("uses-permission", "uses-permission-sdk-23"):
                    perm_name = attrs.get("name")
                    if isinstance(perm_name, str) and perm_name and perm_name not in permissions:
                        permissions.append(perm_name)
                elif tag == "uses-library":
                    lib_name = attrs.get("name")
                    if isinstance(lib_name, str) and lib_name:
                        required = attrs.get("required")
                        uses_libraries.append(
                            {
                                "name": lib_name,
                                "required": True if required is None else bool(required),
                            }
                        )

        pos += chunk_size

    return {
        "package": package,
        "version_code": version_code,
        "version_name": version_name,
        "min_sdk": min_sdk,
        "target_sdk": target_sdk,
        "extract_native_libs": extract_native_libs,
        "permissions": sorted(permissions),
        "uses_libraries": uses_libraries,
    }


def inspect_apk(apk_path: Path) -> ApkInfo:
    """Inspect an APK file and return manifest metadata and bundled native libs."""
    apk_path = Path(apk_path)
    if not apk_path.is_file():
        raise FileNotFoundError(f"APK not found: {apk_path}")

    with zipfile.ZipFile(apk_path, "r") as zf:
        if "AndroidManifest.xml" not in zf.namelist():
            raise ValueError(f"{apk_path.name} is not a valid APK (missing AndroidManifest.xml)")

        manifest_bytes = zf.read("AndroidManifest.xml")
        parsed = parse_axml_bytes(manifest_bytes)

        native_libs: Dict[str, List[Dict[str, object]]] = {}
        for info in zf.infolist():
            if info.filename.startswith("lib/") and info.filename.endswith(".so"):
                parts = info.filename.split("/")
                if len(parts) == 3:
                    abi, so_name = parts[1], parts[2]
                    native_libs.setdefault(abi, []).append(
                        {
                            "name": so_name,
                            "zip_path": info.filename,
                            "compressed": info.compress_type != zipfile.ZIP_STORED,
                            "size": info.file_size,
                        }
                    )

    return ApkInfo(
        path=str(apk_path),
        package=str(parsed["package"]),
        version_code=parsed["version_code"],  # type: ignore[arg-type]
        version_name=parsed["version_name"],  # type: ignore[arg-type]
        min_sdk=parsed["min_sdk"],  # type: ignore[arg-type]
        target_sdk=parsed["target_sdk"],  # type: ignore[arg-type]
        extract_native_libs=parsed["extract_native_libs"],  # type: ignore[arg-type]
        permissions=list(parsed["permissions"]),  # type: ignore[arg-type]
        uses_libraries=list(parsed["uses_libraries"]),  # type: ignore[arg-type]
        native_libs=native_libs,
    )


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 tools/axml_inspector.py <path/to/app.apk>", file=sys.stderr)
        return 1
    info = inspect_apk(Path(argv[1]))
    print(json.dumps(info.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
