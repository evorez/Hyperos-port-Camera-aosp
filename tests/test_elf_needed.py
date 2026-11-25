#!/usr/bin/env python3
"""Cross-check the module's toybox-only ELF parser against readelf.

The on-device diagnostics cannot use readelf (Android ships none) or awk/python,
so module/bin/elf_needed.sh parses ELF .dynamic with dd + od + tr only. This test
proves the parser agrees with binutils before the module ships.
"""
from __future__ import annotations

import glob
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSER = REPO_ROOT / "module" / "bin" / "elf_needed.sh"

CANDIDATE_GLOBS = [
    "/lib/x86_64-linux-gnu/lib*.so*",
    "/usr/lib/x86_64-linux-gnu/lib*.so*",
    "/lib/aarch64-linux-gnu/lib*.so*",
    "/usr/lib/aarch64-linux-gnu/lib*.so*",
]


def parser_needed(path: str) -> list[str]:
    out = subprocess.run(
        ["sh", str(PARSER), "-n", path],
        capture_output=True, text=True, timeout=120,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def readelf_needed(path: str) -> list[str]:
    out = subprocess.run(
        ["readelf", "-d", path], capture_output=True, text=True, timeout=120,
    )
    deps = []
    for line in out.stdout.splitlines():
        if "(NEEDED)" in line and "[" in line:
            deps.append(line.split("[", 1)[1].rstrip("] \n"))
    return deps


class ElfNeededParserTest(unittest.TestCase):
    def setUp(self) -> None:
        if not PARSER.is_file():
            self.skipTest("bin/elf_needed.sh missing")
        if not shutil.which("readelf"):
            self.skipTest("readelf not available on this host")

    def _samples(self, limit: int = 25) -> list[str]:
        seen: set[str] = set()
        samples: list[str] = []
        for pattern in CANDIDATE_GLOBS:
            for path in sorted(glob.glob(pattern)):
                real = str(Path(path).resolve())
                if real in seen or not Path(real).is_file():
                    continue
                seen.add(real)
                samples.append(path)
                if len(samples) >= limit:
                    return samples
        return samples

    def test_matches_readelf(self) -> None:
        samples = self._samples()
        if not samples:
            self.skipTest("no host shared libraries to compare against")

        mismatches = []
        for path in samples:
            want = sorted(readelf_needed(path))
            got = sorted(parser_needed(path))
            if want != got:
                mismatches.append(f"{path}\n  readelf={want}\n  parser ={got}")

        self.assertEqual([], mismatches, "parser disagrees with readelf:\n" + "\n".join(mismatches))

    def test_rejects_non_elf(self) -> None:
        bogus = REPO_ROOT / "tests" / ".not-an-elf"
        bogus.write_bytes(b"this is not an ELF file at all" * 8)
        try:
            out = subprocess.run(
                ["sh", str(PARSER), "-n", str(bogus)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(2, out.returncode)
            self.assertIn("not an ELF", out.stderr)
        finally:
            bogus.unlink(missing_ok=True)

    def test_missing_file(self) -> None:
        out = subprocess.run(
            ["sh", str(PARSER), "-n", "/nope/does-not-exist.so"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(2, out.returncode)


if __name__ == "__main__":
    unittest.main()
