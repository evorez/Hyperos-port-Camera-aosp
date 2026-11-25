#!/usr/bin/env python3
"""Analyze the native libraries inside MiuiCamera.apk — offline, no device needed.

The camera aborts at startup because `dlopen("libmialgoengine.so")` returns NULL
inside com.xiaomi.engine.MiCamAlgoInterfaceJNI.init(), and the device-side log
never captured the underlying dlerror. What the APK *itself* wants, however, can
be proven here without a phone:

  * which ABIs / libs the APK ships, and whether the algo shims are among them
  * DT_NEEDED / DT_SONAME / RUNPATH of every algo lib
  * every string in the JNI lib that names a shared object, plus any dlopen()
    target, absolute vendor path, or version string it compares against
  * imported (UND) vs exported symbols, i.e. what the vendor blob must provide

Usage:
    python3 tools/apk_native_report.py --apk in.apk --out analysis/apk_native_libs.txt
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List

ABI_PRIORITY = ["arm64", "arm64-v8a", "armeabi-v7a", "arm", "x86_64", "x86"]
ALGO_RE = re.compile(r"(mialgo|algoup|algo)", re.I)
SO_NAME_RE = re.compile(r"^lib[A-Za-z0-9_.+\-]*\.so(\.[0-9]+)*$")
INTERESTING_RE = re.compile(
    r"(lib[A-Za-z0-9_.+\-]*\.so|dlopen|dlsym|/vendor/|/odm/|/system/|/product/|"
    r"version|Version|VERSION|engine|Engine|algo|Algo|loadLibrary|System\.load)",
)


def run(cmd: List[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return out.stdout + out.stderr
    except Exception as exc:  # noqa: BLE001
        return f"<{cmd[0]} failed: {exc}>"


def readelf_dynamic(path: Path) -> str:
    if not shutil.which("readelf"):
        return "<readelf unavailable>"
    return run(["readelf", "-d", str(path)])


def dyn_syms(path: Path) -> tuple[List[str], List[str]]:
    """Return (imported, exported) plain symbol names."""
    if not shutil.which("readelf"):
        return [], []
    raw = run(["readelf", "--dyn-syms", "--wide", str(path)])
    imported, exported = [], []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        # Num: Value Size Type Bind Vis Ndx Name
        ndx, name = parts[6], parts[7]
        name = name.split("@", 1)[0]
        if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_.$]*$", name):
            continue
        (imported if ndx == "UND" else exported).append(name)
    return sorted(set(imported)), sorted(set(exported))


def strings_of(path: Path, pattern: re.Pattern[str], limit: int = 400) -> List[str]:
    if shutil.which("strings"):
        raw = run(["strings", "-a", str(path)])
    else:  # crude fallback
        data = path.read_bytes()
        raw = "\n".join(re.findall(rb"[\x20-\x7e]{5,}", data) and
                        [m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e]{5,}", data)])
    hits = []
    for line in raw.splitlines():
        line = line.strip()
        if line and pattern.search(line):
            hits.append(line)
    # de-duplicate, keep order
    seen, uniq = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
        if len(uniq) >= limit:
            break
    return uniq


def extract(apk: Path, dest: Path) -> dict[str, list[str]]:
    inventory: dict[str, list[str]] = {}
    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()
        for name in names:
            if not name.startswith("lib/") or name.count("/") < 2:
                continue
            if not name.endswith(".so"):
                continue
            abi, base = name.split("/")[1], name.split("/")[-1]
            inventory.setdefault(abi, []).append(base)
            if abi.startswith("arm64"):
                (dest / base).write_bytes(zf.read(name))
    return inventory


def main(argv: Iterable[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(list(argv))

    apk = Path(args.apk)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def w(text: str = "") -> None:
        lines.append(text)

    w("# APK native library analysis")
    w()
    w(f"APK: `{apk.name}` ({apk.stat().st_size} bytes, sha256 "
      f"`{hashlib.sha256(apk.read_bytes()).hexdigest()[:16]}…`)")
    w()

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        inventory = extract(apk, tmpdir)

        w("## ABIs present in the APK")
        w()
        for abi in sorted(inventory, key=lambda a: (ABI_PRIORITY.index(a) if a in ABI_PRIORITY else 99, a)):
            w(f"- `{abi}`: {len(inventory[abi])} shared objects")
        w()

        arm64 = sorted(p for p in tmpdir.iterdir() if p.suffix == ".so")
        w(f"## arm64 libs shipped in the APK ({len(arm64)})")
        w()
        w("| lib | size | sha256 (16) |")
        w("| --- | --- | --- |")
        for p in arm64:
            data = p.read_bytes()
            w(f"| `{p.name}` | {len(data)} | `{hashlib.sha256(data).hexdigest()[:16]}…` |")
        w()

        algo = [p for p in arm64 if ALGO_RE.search(p.name)]
        w(f"## Algo-related libs in the APK ({len(algo)})")
        w()
        if not algo:
            w("_none_ — every algo blob must come from the device's /vendor or /odm partition.")
        for p in algo:
            w(f"### `{p.name}`")
            w()
            w("```")
            w(readelf_dynamic(p).strip()[:4000])
            w("```")
            w()

        jni_candidates = [p for p in arm64 if "algoup_jni" in p.name] or algo
        for p in jni_candidates[:2]:
            w(f"## `{p.name}` — every string that names a library / dlopen target / version")
            w()
            w("```")
            for s in strings_of(p, INTERESTING_RE):
                w(s)
            w("```")
            w()

        w("## DT_NEEDED summary for all arm64 libs")
        w()
        w("| lib | NEEDED |")
        w("| --- | --- |")
        for p in arm64:
            deps = [ln.split("[", 1)[1].rstrip("]").strip()
                    for ln in readelf_dynamic(p).splitlines()
                    if "(NEEDED)" in ln and "[" in ln]
            w(f"| `{p.name}` | {', '.join(f'`{d}`' for d in deps) or '—'} |")
        w()

        w("## Imported vs exported symbols (algo libs)")
        w()
        for p in algo:
            imported, exported = dyn_syms(p)
            w(f"### `{p.name}`")
            w()
            w(f"- imported (UND) {len(imported)}: {', '.join(imported[:60]) or '—'}")
            w(f"- exported {len(exported)}: {', '.join(exported[:60]) or '—'}")
            w()

        w("## dlopen-relevant strings across all algo libs")
        w()
        w("```")
        for p in algo:
            hits = strings_of(p, re.compile(r"(^lib[A-Za-z0-9_.+\-]*\.so$|dlopen|dlsym|/vendor/lib64|/odm/lib64)"))
            if hits:
                w(f"--- {p.name}")
                for h in hits[:80]:
                    w(h)
        w("```")
        w()

    out.write_text("\n".join(lines) + "\n")
    print(f"[OK] wrote {out} ({len(lines)} lines)")
    soname_only = [ln for ln in lines if SO_NAME_RE.match(ln.strip())]
    print(f"[OK] {len(soname_only)} bare soname strings found across algo libs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
