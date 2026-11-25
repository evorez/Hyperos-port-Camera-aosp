#!/usr/bin/env python3
"""
Smali / resource recon for adapting patch anchors to a different camera build.

Decompiles an APK with `apktool d --no-res` and greps every smali file plus
resources.arsc / assets for a set of probe patterns, then writes a Markdown
report describing WHERE each pattern lives (class path + snippet). This is how
anchors for a new HyperOS camera version (e.g. 6.1.x) are discovered without a
local java toolchain.

Usage:
    python3 tools/smali_recon.py input/apk/MiuiCamera.apk \
        --out /tmp/recon.md --probes patches/recon_probes.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List

MAX_EXAMPLES = 6
SNIPPET = 150


def decompile(apk: Path, out_dir: Path, apktool: str) -> None:
    cmd = [apktool, "d", "--no-res", "-f", str(apk), "-o", str(out_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"apktool failed ({proc.returncode}): "
            + " ".join((proc.stderr or proc.stdout or "").split())[-800:]
        )


def smali_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and (sub.name == "smali" or sub.name.startswith("smali_classes")):
            files.extend(sorted(sub.rglob("*.smali")))
    return files


def _probe_regex(pattern: str) -> re.Pattern:
    # Treat a pattern as a regex when it contains regex metacharacters
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def grep_smali(files: Iterable[Path], root: Path, pattern: str) -> List[Dict[str, str]]:
    rx = _probe_regex(pattern)
    hits: List[Dict[str, str]] = []
    for sf in files:
        try:
            text = sf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not rx.search(text):
            continue
        rel = sf.relative_to(root).as_posix()
        m = rx.search(text)
        line_start = text.rfind("\n", 0, m.start()) + 1 if m else 0
        line_end = text.find("\n", m.end()) if m else len(text)
        snippet = text[line_start:line_end].strip()[:SNIPPET]
        hits.append({"file": rel, "snippet": snippet})
        if len(hits) >= MAX_EXAMPLES * 4:
            break
    return hits


def grep_binary_strings(apk: Path, members: List[str], pattern: str) -> int:
    """Count occurrences of a pattern in resources.arsc / given zip members."""
    rx = _probe_regex(pattern)
    total = 0
    try:
        with zipfile.ZipFile(apk) as zf:
            for name in members:
                if name not in zf.namelist():
                    continue
                raw = zf.read(name)
                for enc in ("utf-8", "utf-16le"):
                    try:
                        text = raw.decode(enc, errors="ignore")
                    except Exception:  # noqa: BLE001
                        continue
                    total += len(rx.findall(text))
    except Exception:  # noqa: BLE001
        return 0
    return total


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Recon smali/resources for patch anchors.")
    ap.add_argument("apk")
    ap.add_argument("--out", required=True, help="Markdown report path")
    ap.add_argument("--probes", required=True, help="JSON file with probe definitions")
    ap.add_argument("--apktool", default="apktool")
    args = ap.parse_args(argv)

    apk = Path(args.apk)
    probes: Dict = json.loads(Path(args.probes).read_text(encoding="utf-8"))
    groups = probes.get("groups", [])

    md: List[str] = ["### Smali / resource recon", "", f"APK: `{apk.name}` ({apk.stat().st_size:,} bytes)", ""]

    with tempfile.TemporaryDirectory(prefix="recon_") as tmp:
        root = Path(tmp) / "dec"
        decompile(apk, root, args.apktool)
        files = smali_files(root)
        dirs = [d.name for d in sorted(root.iterdir()) if d.is_dir() and d.name.startswith("smali")]
        md.append(f"- smali files: **{len(files)}**")
        md.append(f"- smali roots: {', '.join(dirs) if dirs else 'none'}")
        md.append("")

        for group in groups:
            md.append(f"#### {group.get('id', 'group')}")
            if group.get("description"):
                md.append(group["description"])
            md.append("")
            for probe in group.get("probes", []):
                pid = probe.get("id", "probe")
                pattern = probe["pattern"]
                where = probe.get("where", "smali")
                if where == "arsc":
                    count = grep_binary_strings(apk, ["resources.arsc"], pattern)
                    md.append(f"- `{pid}` — arsc matches: **{count}** (pattern `{pattern}`)")
                    continue
                hits = grep_smali(files, root, pattern)
                if not hits:
                    md.append(f"- `{pid}` — **no match** (pattern `{pattern}`)")
                    continue
                uniq_files = sorted({h["file"] for h in hits})
                md.append(f"- `{pid}` — {len(uniq_files)} file(s) match `{pattern}`")
                for h in hits[:MAX_EXAMPLES]:
                    md.append(f"    - `{h['file']}`: `{h['snippet']}`")
            md.append("")

    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[RECON] wrote {args.out} ({len(md)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
