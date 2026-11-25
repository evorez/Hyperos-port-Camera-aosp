#!/usr/bin/env python3
"""
Robust Google Drive / HTTP downloader for the HyperOS camera payload.

Google Drive stops serving large public files (>100 MB) with a plain curl: the
first response is either an HTML "virus scan" interstitial (needs the confirm
form POSTed back with its cookie + uuid) or an HTML quota page. This script
handles both, verifies the payload is a real ZIP/APK, and prints CI-readable
`::error::` annotations so failures are diagnosable from the Actions UI even
when raw logs are unavailable.

Usage:
    python3 tools/gdrive_download.py --id <DRIVE_FILE_ID> --out /tmp/payload.apk
    python3 tools/gdrive_download.py --url <DIRECT_URL> --out /tmp/payload.apk
    python3 tools/gdrive_download.py --id <ID> --out x.apk --expect-bytes 182801464
"""

from __future__ import annotations

import argparse
import html.parser
import http.cookiejar
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CHUNK = 1024 * 1024
HTML_SNIFF_LIMIT = 4096


class FormParser(html.parser.HTMLParser):
    """Extracts <form action=...> plus its <input name/value> pairs."""

    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.inputs: Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form" and self.action is None:
            self.action = attr.get("action") or ""
        elif tag.lower() == "input":
            name = attr.get("name")
            if name:
                self.inputs[name] = attr.get("value", "")


def annotate(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation (survives even when logs are unreadable)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        flat = " ".join(message.split())
        print(f"::{level}::{flat}", flush=True)
    else:
        print(f"[DL][{level}] {message}", flush=True)


def opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    try:
        import ssl

        ctx = ssl.create_default_context()
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    except Exception:  # pragma: no cover - ssl always available
        pass
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [("User-Agent", USER_AGENT), ("Accept", "*/*")]
    return op


def _sniff_html(data: bytes) -> bool:
    head = data[:HTML_SNIFF_LIMIT].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _save_stream(resp, out: Path) -> int:
    """Stream a response to disk, returning byte count."""
    total = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
            if total % (32 * CHUNK) < CHUNK:
                print(f"[DL] ... {total / (1024 * 1024):.1f} MB", flush=True)
    return total


def _describe(out: Path) -> str:
    size = out.stat().st_size if out.exists() else 0
    snippet = ""
    if out.exists() and size:
        with out.open("rb") as fh:
            raw = fh.read(HTML_SNIFF_LIMIT)
        if _sniff_html(raw):
            text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "ignore"))
            snippet = " | html=" + " ".join(text.split())[:300]
    return f"size={size}{snippet}"


def try_direct(op, url: str, out: Path) -> bool:
    """Plain GET. Returns True only when the body is a ZIP (not an HTML page)."""
    try:
        with op.open(url, timeout=180) as resp:
            first = resp.read(CHUNK)
            if _sniff_html(first):
                annotate("warning", f"direct GET returned HTML at {url.split('?')[0]}")
                return False
            out.parent.mkdir(parents=True, exist_ok=True)
            total = len(first)
            with out.open("wb") as fh:
                fh.write(first)
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    if total % (32 * CHUNK) < CHUNK:
                        print(f"[DL] ... {total / (1024 * 1024):.1f} MB", flush=True)
        print(f"[DL] direct GET wrote {total} bytes", flush=True)
        return zipfile.is_zipfile(out)
    except urllib.error.HTTPError as exc:
        annotate("warning", f"direct GET HTTP {exc.code} {exc.reason}")
    except Exception as exc:  # noqa: BLE001 - report and fall through
        annotate("warning", f"direct GET failed: {type(exc).__name__}: {exc}")
    return False


def try_confirm_form(op, file_id: str, out: Path) -> bool:
    """Fetch Drive interstitial, then POST the confirm form (handles >100 MB files)."""
    base = "https://drive.usercontent.google.com/download"
    query = urllib.parse.urlencode({"id": file_id, "export": "download", "confirm": "t"})
    url = f"{base}?{query}"
    try:
        with op.open(url, timeout=180) as resp:
            body = resp.read()
    except Exception as exc:  # noqa: BLE001
        annotate("warning", f"confirm-form bootstrap failed: {type(exc).__name__}: {exc}")
        return False

    if not _sniff_html(body):
        # Already got the file directly.
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
        return zipfile.is_zipfile(out)

    text = " ".join(re.sub(r"<[^>]+>", " ", body.decode("utf-8", "ignore")).split())
    annotate("warning", f"Drive interstitial text: {text[:400]}")

    parser = FormParser()
    parser.feed(body.decode("utf-8", "ignore"))
    action = parser.action or base
    fields = dict(parser.inputs)
    fields.setdefault("id", file_id)
    fields.setdefault("export", "download")
    fields.setdefault("confirm", "t")
    if action.startswith("/"):
        action = "https://drive.usercontent.google.com" + action
    print(f"[DL] Drive interstitial -> POST {action} with {sorted(fields)}", flush=True)

    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(action, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with op.open(req, timeout=300) as resp:
            first = resp.read(CHUNK)
            if _sniff_html(first):
                annotate("warning", "confirm-form POST also returned HTML (quota/permission page)")
                return False
            out.parent.mkdir(parents=True, exist_ok=True)
            total = len(first)
            with out.open("wb") as fh:
                fh.write(first)
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    if total % (32 * CHUNK) < CHUNK:
                        print(f"[DL] ... {total / (1024 * 1024):.1f} MB", flush=True)
        print(f"[DL] confirm-form POST wrote {total} bytes", flush=True)
        return zipfile.is_zipfile(out)
    except Exception as exc:  # noqa: BLE001
        annotate("warning", f"confirm-form POST failed: {type(exc).__name__}: {exc}")
        return False


def try_gdown(file_id: str, out: Path) -> bool:
    """Last resort: gdown (installed on demand)."""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "gdown"],
            check=True,
        )
    except Exception as exc:  # noqa: BLE001
        annotate("warning", f"gdown install failed: {type(exc).__name__}: {exc}")
        return False
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "gdown", f"https://drive.google.com/uc?id={file_id}", "-O", str(out)],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            tail = " ".join((proc.stderr or proc.stdout or "").split())[-400:]
            annotate("warning", f"gdown exit={proc.returncode}: {tail}")
            return False
        return zipfile.is_zipfile(out)
    except Exception as exc:  # noqa: BLE001
        annotate("warning", f"gdown failed: {type(exc).__name__}: {exc}")
        return False


def download(file_id: Optional[str], url: Optional[str], out: Path, expect_bytes: int = 0) -> int:
    out = out.resolve()
    if out.exists():
        out.unlink()
    op = opener()

    strategies = []
    if url:
        strategies.append(("direct-url", lambda: try_direct(op, url, out)))
    if file_id:
        strategies.append(
            ("drive-usercontent", lambda: try_direct(
                op,
                "https://drive.usercontent.google.com/download?"
                + urllib.parse.urlencode({"id": file_id, "export": "download", "confirm": "t"}),
                out,
            ))
        )
        strategies.append(("drive-confirm-form", lambda: try_confirm_form(op, file_id, out)))
        strategies.append(("gdown", lambda: try_gdown(file_id, out)))

    for name, fn in strategies:
        print(f"[DL] strategy: {name}", flush=True)
        ok = fn()
        if not ok:
            print(f"[DL] {name} -> unusable payload ({_describe(out)})", flush=True)
            continue
        size = out.stat().st_size
        if expect_bytes and abs(size - expect_bytes) > 1024:
            annotate(
                "warning",
                f"{name} produced {size} bytes, expected {expect_bytes} "
                f"(delta {size - expect_bytes}) - continuing anyway",
            )
        print(f"[DL] success via {name}: {size} bytes -> {out}", flush=True)
        return size

    annotate(
        "error",
        "Google Drive download failed after all strategies "
        f"(id={file_id or '-'}, url={url or '-'}, last={_describe(out)}). "
        "The file may not be publicly shared, or Drive quota is exhausted - "
        "re-share the file as 'anyone with the link' or pass apk_url/direct link.",
    )
    return 0


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Robust Drive/HTTP payload downloader.")
    ap.add_argument("--id", dest="file_id", default=None, help="Google Drive file ID")
    ap.add_argument("--url", dest="url", default=None, help="Direct HTTP(S) URL")
    ap.add_argument("--out", required=True, help="Destination file path")
    ap.add_argument("--expect-bytes", type=int, default=0, help="Optional expected size for sanity check")
    args = ap.parse_args(argv)

    if not args.file_id and not args.url:
        ap.error("provide --id or --url")

    size = download(args.file_id, args.url, Path(args.out), args.expect_bytes)
    if not size:
        return 1
    target = Path(args.out)
    if not zipfile.is_zipfile(target):
        annotate("error", f"Payload is not a valid ZIP/APK: {_describe(target)}")
        return 1
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    kind = "apk" if "AndroidManifest.xml" in names else "zip"
    print(f"[DL] payload kind={kind} entries={len(names)} size={size}", flush=True)
    out_kind = Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None
    if out_kind:
        with out_kind.open("a", encoding="utf-8") as fh:
            fh.write(f"payload_kind={kind}\n")
            fh.write(f"payload_bytes={size}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
