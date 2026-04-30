#!/bin/bash
# Discover the active autosymph status API base URL.

set -euo pipefail

python3 - "$@" <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

TIMEOUT = float(os.environ.get("AUTOSYMPH_STATUS_TIMEOUT", "1.0"))


def normalize(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return f"http://127.0.0.1:{value}"
    if value.startswith(("http://", "https://")):
        return value[:-len("/status")] if value.endswith("/status") else value.rstrip("/")
    return None


def valid(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/status", timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    return isinstance(data, dict) and ("projects" in data or "orchestrator_count" in data)


def server_ports_from_yaml(path: Path) -> Iterable[int]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return

    in_server = False
    server_indent = 0
    for line in lines:
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if re.match(r"^\s*server\s*:\s*$", stripped):
            in_server = True
            server_indent = indent
            continue
        if in_server and indent <= server_indent:
            in_server = False
        if in_server:
            match = re.match(r"^\s*port\s*:\s*([0-9]+)\s*$", stripped)
            if match:
                yield int(match.group(1))


def candidates(argv: list[str]) -> Iterable[str]:
    if argv:
        value = normalize(argv[0])
        if value:
            yield value

    for key in ("AUTOSYMPH_STATUS_URL", "AUTOSYMPH_STATUS_PORT"):
        value = normalize(os.environ.get(key, ""))
        if value:
            yield value

    status_file = Path(
        os.environ.get("AUTOSYMPH_STATUS_FILE", "~/.autosymph/status-api.json")
    ).expanduser()
    try:
        data = json.loads(status_file.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    for key in ("url", "base_url"):
        value = normalize(str(data.get(key, "")))
        if value:
            yield value
    if "port" in data:
        value = normalize(str(data["port"]))
        if value:
            yield value

    config_dir = Path(
        os.environ.get("AUTOSYMPH_CONFIG_DIR", "~/.autosymph/config")
    ).expanduser()
    if config_dir.exists():
        yaml_paths = sorted(config_dir.rglob("*.yaml")) + sorted(config_dir.rglob("*.yml"))
        for path in yaml_paths:
            for port in server_ports_from_yaml(path):
                value = normalize(str(port))
                if value:
                    yield value

    yield "http://127.0.0.1:4200"
    for port in range(4201, 4211):
        yield f"http://127.0.0.1:{port}"


seen: set[str] = set()
for candidate in candidates(sys.argv[1:]):
    if candidate in seen:
        continue
    seen.add(candidate)
    if valid(candidate):
        print(candidate)
        raise SystemExit(0)

print("autosymph status API not found", file=sys.stderr)
raise SystemExit(1)
PY
