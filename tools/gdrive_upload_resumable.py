#!/usr/bin/env python3
"""Stream a local file into a Google Drive resumable upload session.

The session URI comes from the Drive connector's `initiate_resumable_upload`
(it is signed, one-time, and valid ~7 days). Drive's resumable protocol needs a
sequence of PUTs with an explicit offset header; a single-shot upload of a
200 MB file routinely fails, hence chunking.

Usage:
    python3 tools/gdrive_upload_resumable.py --uri "$GDRIVE_UPLOAD_URI" --file dist/Gold-Stock.zip
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

CHUNK = 8 * 1024 * 1024


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", required=True)
    ap.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    uri = args.uri.strip()
    path = args.file
    total = os.path.getsize(path)
    print(f"Streaming {os.path.basename(path)} ({total} bytes) to Google Drive...")

    offset = 0
    with open(path, "rb") as fh:
        while offset < total:
            data = fh.read(min(CHUNK, total - offset))
            n = len(data)
            is_last = (offset + n) >= total
            req = urllib.request.Request(uri, data=data, method="PUT")
            req.add_header("Content-Length", str(n))
            req.add_header("X-Goog-Upload-Offset", str(offset))
            req.add_header("X-Goog-Upload-Command", "upload, finalize" if is_last else "upload")
            req.add_header("X-Goog-Upload-Total-Size", str(total))
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    status = resp.headers.get("X-Goog-Upload-Status", "")
                    body = resp.read().decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:300]
                print(f"::error::Drive upload HTTP {exc.code} at offset {offset}: {detail}")
                return 1
            offset += n
            print(f"  {offset}/{total} ({offset / total * 100:.1f}%) status={status}")
            if is_last:
                print("FINALIZED:", body[:300])
                if status != "final":
                    print(f"::error::expected final, got {status}")
                    return 1
    print("::notice::Drive upload finalized")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
