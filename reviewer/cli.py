"""CLI entry point: `python -m reviewer <abs-or-rel-path>.py`."""

from __future__ import annotations

import argparse
import logging
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from . import __version__
from .aggregator import merge
from .chunker import DEFAULT_MAX_CHARS, split
from .opencode_client import OpencodeClient, OpencodeConfig
from .reporter import render
from .static_analyzer import analyze


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    target = Path(args.file).expanduser().resolve()
    if not target.is_file():
        print(f"error: file not found: {target}", file=sys.stderr)
        return 2
    if target.suffix != ".py":
        print(f"error: not a .py file: {target}", file=sys.stderr)
        return 2

    source = target.read_text(encoding="utf-8")
    project = analyze(source)
    chunks = split(source, project, max_chars=args.token_budget)
    logging.info(
        "loaded %s (%d lines), %d chunk(s)", target, project.line_count, len(chunks)
    )

    llm_findings = []
    chunk_failures = []
    if not args.no_llm and chunks:
        client = OpencodeClient(OpencodeConfig(
            bin_path=args.opencode_bin,
            extra_args=tuple(shlex.split(args.opencode_extra_args or "")),
            timeout_sec=args.timeout,
        ))
        with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as ex:
            futures = {ex.submit(client.review_chunk, c): c for c in chunks}
            for fut in as_completed(futures):
                chunk = futures[fut]
                findings, err = fut.result()
                if err:
                    chunk_failures.append(f"{chunk.chunk_id}: {err}")
                    logging.warning("chunk %s failed: %s", chunk.chunk_id, err)
                llm_findings.extend(findings)

    report = merge(
        file_path=str(target),
        project=project,
        llm_findings=llm_findings,
        chunk_failures=chunk_failures,
    )
    output = render(report)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        logging.info("wrote %s", args.out)
    else:
        sys.stdout.write(output)

    return 0


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m reviewer",
        description="Single Python Tkinter file code review harness "
                    "for weak (Qwen-3.5 class) LLMs accessed via opencode.",
    )
    p.add_argument("file", help="absolute or relative path to a single .py file")
    p.add_argument("--out", help="write Markdown report to this path (default: stdout)")
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--token-budget", type=int, default=DEFAULT_MAX_CHARS,
                   help="approximate per-chunk character budget")
    p.add_argument("--opencode-bin", default="opencode")
    p.add_argument("--opencode-extra-args", default="",
                   help="extra args forwarded to opencode (model selection, etc.)")
    p.add_argument("--timeout", type=int, default=120,
                   help="per-chunk opencode timeout in seconds")
    p.add_argument("--no-llm", action="store_true",
                   help="skip LLM step; emit static-only report")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)
