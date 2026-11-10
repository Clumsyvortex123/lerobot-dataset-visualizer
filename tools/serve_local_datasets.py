#!/usr/bin/env python3
"""Serve local LeRobot datasets at the Hugging Face URL layout the
lerobot-dataset-visualizer reads (its DATASET_URL):

    GET [/datasets]/<org>/<dataset>/resolve/main/<path>  ->  ROOT/<dataset>/<path>

With HTTP Range (video seeking), HEAD and CORS (the browser fetches videos and
parquet directly). Read-only; paths are confined to ROOT.

    python3 tools/serve_local_datasets.py --root ~/agents/issue-7/data/lerobot --port 8765
"""

import argparse
import mimetypes
import os
import re
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class Handler(BaseHTTPRequestHandler):
    root: Path

    def _resolve(self) -> Path | None:
        parts = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).strip("/").split("/")
        if parts and parts[0] == "datasets":  # the Hub layout: <endpoint>/datasets/<org>/<dataset>/...
            parts = parts[1:]
        # org / dataset / resolve / <revision> / path...
        if len(parts) < 5 or parts[2] != "resolve":
            return None
        target = (self.root / parts[1] / "/".join(parts[4:])).resolve()
        if self.root not in target.parents or not target.is_file():
            return None
        return target

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, Authorization, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.end_headers()

    def do_HEAD(self):
        self._serve(body=False)

    def do_GET(self):
        self._serve(body=True)

    def _serve(self, body: bool):
        path = self._resolve()
        if path is None:
            self.send_response(HTTPStatus.NOT_FOUND)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        m = RANGE.match(self.headers.get("Range", ""))
        if m and size:
            a, b = m.groups()
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
            elif b:
                start, end = max(0, size - int(b)), size - 1
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._cors()
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".parquet":
            ctype = "application/vnd.apache.parquet"
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body:
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def log_message(self, fmt, *args):  # quieter: only errors
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    Handler.root = a.root.expanduser().resolve()
    print(f"serving {Handler.root} at http://{a.host}:{a.port}/<org>/<dataset>/resolve/main/...", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    os.umask(0o022)
    main()
