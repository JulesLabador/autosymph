"""Replay a real NDJSON log through ActivitySummarizer and print the Linear digest.

Usage:
    uv run python scripts/replay_summary.py <ndjson_path>
    uv run python scripts/replay_summary.py <ndjson_path> --verbose
"""

from __future__ import annotations

import sys
from pathlib import Path

from autosymph.logging.summarizer import ActivitySummarizer
from autosymph.runners.claude import ClaudeRunner


def replay(path: Path, verbose: bool = False) -> str:
    runner = ClaudeRunner()
    summarizer = ActivitySummarizer()
    parsed = 0
    forwarded = 0
    ingest_errors = 0

    with path.open() as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = runner.parse_event(line)
            except Exception as exc:
                if verbose:
                    print(f"  parse_event error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
                continue
            parsed += 1
            if event is None:
                continue
            forwarded += 1
            try:
                summarizer.ingest(event)
            except Exception as exc:
                ingest_errors += 1
                summarizer.errored = True
                if verbose:
                    print(f"  ingest error: {exc.__class__.__name__}: {exc}", file=sys.stderr)

    print(
        f"=== {path.name} === "
        f"parsed={parsed} forwarded={forwarded} ingest_errors={ingest_errors} "
        f"errored={summarizer.errored}",
    )
    out = summarizer.format_digest()
    if not out:
        print("(empty digest — would fall back to legacy timeline)")
    else:
        print(out)
    print()
    return out


def main() -> None:
    args = sys.argv[1:]
    verbose = "--verbose" in args
    args = [a for a in args if a != "--verbose"]
    if not args:
        print(__doc__)
        sys.exit(1)
    for arg in args:
        replay(Path(arg).expanduser(), verbose=verbose)


if __name__ == "__main__":
    main()
