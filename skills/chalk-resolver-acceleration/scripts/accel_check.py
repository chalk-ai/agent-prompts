#!/usr/bin/env python3
"""Report chalk-lsp static-acceleration diagnostics for Chalk resolver files.

`chalk-lsp check` does not emit acceleration diagnostics -- they are only
published over the language-server protocol. This script is a minimal LSP
client: it starts its own `chalk-lsp` server, opens the requested files, and
prints the `chalk` diagnostics it publishes.

    ./accel_check.py neobank/resolvers.py
    ./accel_check.py --all
    ./accel_check.py --all --fail-on-findings

Requires `chalk-lsp` on PATH (curl -fsSL https://api.chalk.ai/lsp/install.sh | bash)
and a Chalk project root (a directory containing chalk.yml or chalk.yaml).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

PROJECT_MARKERS = ("chalk.yml", "chalk.yaml")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache", "node_modules"}
DIAGNOSTIC_SOURCE = "chalk"


def find_project_root(start: Path) -> Path:
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in PROJECT_MARKERS):
            return directory
    sys.exit(f"error: no {' or '.join(PROJECT_MARKERS)} found in {start} or any parent directory")


def server_argv(binary: str) -> list[str]:
    """v3 exposes the server as `server`; the pre-v3 crate used `lsp`."""
    help_text = subprocess.run(
        [binary, "--help"], capture_output=True, text=True, check=False
    ).stdout
    return [binary, "server" if "server" in help_text else "lsp"]


def to_uri(path: Path) -> str:
    return "file://" + pathname2url(str(path))


def from_uri(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


class Client:
    """Speaks just enough LSP to open files and collect published diagnostics."""

    def __init__(self, argv: list[str]) -> None:
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.next_id = 0
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self.last_message_at = time.monotonic()
        self.lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        stream = self.proc.stdout
        assert stream is not None
        while True:
            length = 0
            while True:
                line = stream.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                name, _, value = line.decode().partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            if length <= 0:
                continue
            try:
                message = json.loads(stream.read(length))
            except json.JSONDecodeError:
                continue
            with self.lock:
                self.last_message_at = time.monotonic()
                if message.get("method") == "textDocument/publishDiagnostics":
                    params = message["params"]
                    self.diagnostics[params["uri"]] = params.get("diagnostics", [])

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        stdin = self.proc.stdin
        assert stdin is not None
        stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> None:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self, root: Path) -> None:
        root_uri = to_uri(root)
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {"textDocument": {"publishDiagnostics": {"relatedInformation": True}}},
                "workspaceFolders": [{"uri": root_uri, "name": root.name}],
            },
        )
        self.wait_quiet(quiet_for=1.5, timeout=30.0)
        self.notify("initialized", {})

    def open(self, path: Path) -> None:
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": to_uri(path),
                    "languageId": "python",
                    "version": 1,
                    "text": path.read_text(encoding="utf-8", errors="replace"),
                }
            },
        )

    def wait_quiet(self, quiet_for: float, timeout: float) -> None:
        """Block until the server stops talking, or `timeout` elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                idle = time.monotonic() - self.last_message_at
            if idle >= quiet_for:
                return
            time.sleep(0.1)

    def shutdown(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=5)


def detail_of(diagnostic: dict[str, Any]) -> str:
    """The useful text lives in relatedInformation; `message` is a generic headline."""
    parts = [
        related["message"]
        for related in diagnostic.get("relatedInformation", [])
        if related.get("message")
    ]
    return " -- " + "; ".join(parts) if parts else ""


def python_files(root: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        found.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path, help="Python files to check")
    parser.add_argument("--all", action="store_true", help="check every .py file under the project root")
    parser.add_argument("--binary", default="chalk-lsp", help="chalk-lsp executable (default: chalk-lsp)")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for diagnostics")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--fail-on-findings", action="store_true", help="exit 1 if anything is reported")
    args = parser.parse_args()

    if not args.paths and not args.all:
        parser.error("pass one or more paths, or --all")
    if not shutil.which(args.binary):
        sys.exit(
            f"error: {args.binary} not found on PATH\n"
            "install it with: curl -fsSL https://api.chalk.ai/lsp/install.sh | bash"
        )

    root = find_project_root(Path.cwd().resolve())
    targets = python_files(root) if args.all else [p.resolve() for p in args.paths]
    targets = [p for p in targets if p.is_file()]
    if not targets:
        sys.exit("error: no Python files to check")

    client = Client(server_argv(args.binary))
    try:
        client.initialize(root)
        for path in targets:
            client.open(path)
        # Chalk's accel pass lands after ty's type diagnostics, so wait for a real lull.
        client.wait_quiet(quiet_for=3.0, timeout=args.timeout)
        published = dict(client.diagnostics)
    finally:
        client.shutdown()

    opened = {to_uri(p) for p in targets}
    findings = []
    for uri, diagnostics in sorted(published.items()):
        if uri not in opened:
            continue
        path = from_uri(uri)
        try:
            display = path.relative_to(root)
        except ValueError:
            display = path
        for diagnostic in diagnostics:
            if diagnostic.get("source") != DIAGNOSTIC_SOURCE:
                continue
            start = diagnostic["range"]["start"]
            findings.append(
                {
                    "path": str(display),
                    "line": start["line"] + 1,
                    "column": start["character"] + 1,
                    "code": diagnostic.get("code", ""),
                    "message": diagnostic.get("message", ""),
                    "detail": detail_of(diagnostic).removeprefix(" -- "),
                }
            )

    findings.sort(key=lambda f: (f["path"], f["line"], f["column"]))

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            suffix = f" -- {f['detail']}" if f["detail"] else ""
            print(f"{f['path']}:{f['line']}:{f['column']} [{f['code']}] {f['message']}{suffix}")
    print(f"{len(findings)} finding(s) across {len(targets)} file(s) checked", file=sys.stderr)

    return 1 if findings and args.fail_on_findings else 0


if __name__ == "__main__":
    sys.exit(main())
