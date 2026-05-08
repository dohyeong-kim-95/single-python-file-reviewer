"""CLI entry point: `python -m reviewer <abs-or-rel-path>.py`.

Default behavior:
- Creates ./reviews/<YYYYMMDD-HHMMSS>_<stem>/ as a per-run artifacts directory.
- Writes report.md, static_context.json, dropped_findings.jsonl (if any),
  and chunks/<id>.{prompt,stdout,parsed,error}.* there.
- --out overrides the report path; artifacts still go under ./reviews/.
- --no-artifacts disables the per-chunk artifact dump (only report stays).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import __version__
from .aggregator import merge
from .chunker import DEFAULT_MAX_CHARS, split
from .io_utils import read_source_text
from .models import ChunkResult, ProjectContext, RejectedFinding
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

    source, used_enc = read_source_text(target)
    if used_enc != "utf-8-sig":
        logging.info("decoded %s using fallback encoding %s", target, used_enc)
    project = analyze(source)
    chunks = split(source, project, max_chars=args.token_budget)
    logging.info(
        "loaded %s (%d lines), %d chunk(s)", target, project.line_count, len(chunks)
    )

    run_dir = _make_run_dir(target, args)
    if run_dir is not None:
        _write_static_context(run_dir, project)

    chunk_results: list[ChunkResult] = []
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
                # Defensive: any exception from review_chunk (decode errors,
                # subprocess oddities, AST surprises) becomes a recorded
                # ChunkResult failure so the rest of the run still completes.
                try:
                    result: ChunkResult = fut.result()
                except Exception as e:
                    logging.warning(
                        "chunk %s raised %s: %s",
                        chunk.chunk_id, type(e).__name__, e,
                    )
                    result = ChunkResult(
                        chunk_id=chunk.chunk_id,
                        prompt="",
                        stdout="",
                        parsed=None,
                        error=f"{type(e).__name__}: {e}",
                    )
                chunk_results.append(result)
                if result.error:
                    logging.warning("chunk %s failed: %s", chunk.chunk_id, result.error)
                if run_dir is not None:
                    try:
                        _write_chunk_artifacts(run_dir, result)
                    except Exception as e:
                        logging.warning(
                            "could not write artifacts for chunk %s: %s",
                            chunk.chunk_id, e,
                        )

    llm_findings = [f for r in chunk_results for f in r.findings]
    rejected: list[RejectedFinding] = [rj for r in chunk_results for rj in r.rejected]
    chunk_failures = [
        f"{r.chunk_id}: {r.error}" for r in chunk_results if r.error
    ]

    if run_dir is not None and rejected:
        _write_dropped(run_dir, rejected)

    report = merge(
        file_path=str(target),
        project=project,
        llm_findings=llm_findings,
        chunk_failures=chunk_failures,
        rejected_count=len(rejected),
    )
    output = render(report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    elif run_dir is not None:
        out_path = run_dir / "report.md"
    else:
        # --no-artifacts and no --out: dump next to cwd/reviews/ flat.
        flat = Path.cwd() / "reviews"
        flat.mkdir(parents=True, exist_ok=True)
        out_path = flat / f"{target.stem}.md"
    out_path.write_text(output, encoding="utf-8")
    logging.info("wrote %s", out_path)
    if run_dir is not None and run_dir != out_path.parent:
        logging.info("artifacts in %s", run_dir)

    return 0


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def _make_run_dir(target: Path, args: argparse.Namespace) -> Optional[Path]:
    if args.no_artifacts:
        return None
    base = Path(args.artifacts_root) if args.artifacts_root else (Path.cwd() / "reviews")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = base / f"{ts}_{target.stem}"
    (run_dir / "chunks").mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_static_context(run_dir: Path, project: ProjectContext) -> None:
    """Dump the AST-derived structural context (no source body) for debugging."""
    payload = {
        "line_count": project.line_count,
        "has_mainloop": project.has_mainloop,
        "has_wm_delete_protocol": project.has_wm_delete_protocol,
        "uses_update": project.uses_update,
        "uses_update_idletasks": project.uses_update_idletasks,
        "widgets": [_asdict_safe(w) for w in project.widget_tree],
        "bindings": [_asdict_safe(b) for b in project.bindings],
        "smells": [_asdict_safe(s) for s in project.smells],
        "classes": [
            {
                "name": c.name, "lineno": c.lineno, "end_lineno": c.end_lineno,
                "bases": list(c.bases),
                "methods": [
                    {"name": m.name, "qualname": m.qualname,
                     "lineno": m.lineno, "end_lineno": m.end_lineno}
                    for m in c.methods
                ],
            } for c in project.classes
        ],
        "top_level_funcs": [
            {"name": f.name, "lineno": f.lineno, "end_lineno": f.end_lineno}
            for f in project.top_level_funcs
        ],
        "handler_inbound": {
            name: [_asdict_safe(b) for b in lst]
            for name, lst in project.handler_inbound.items()
        },
    }
    (run_dir / "static_context.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_chunk_artifacts(run_dir: Path, result: ChunkResult) -> None:
    safe_id = _slug(result.chunk_id)
    chunks_dir = run_dir / "chunks"
    # errors="replace" keeps artifact dumps robust against weird bytes
    # that might already have leaked through earlier (defense in depth).
    (chunks_dir / f"{safe_id}.prompt.txt").write_text(
        result.prompt, encoding="utf-8", errors="replace",
    )
    (chunks_dir / f"{safe_id}.stdout.txt").write_text(
        result.stdout, encoding="utf-8", errors="replace",
    )
    if result.parsed is not None:
        (chunks_dir / f"{safe_id}.parsed.json").write_text(
            json.dumps(result.parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="replace",
        )
    if result.error:
        (chunks_dir / f"{safe_id}.error.txt").write_text(
            result.error, encoding="utf-8", errors="replace",
        )


def _write_dropped(run_dir: Path, rejected: list[RejectedFinding]) -> None:
    path = run_dir / "dropped_findings.jsonl"
    with path.open("w", encoding="utf-8") as fp:
        for r in rejected:
            fp.write(json.dumps({
                "chunk_id": r.chunk_id,
                "reason": r.reason,
                "raw": r.raw,
            }, ensure_ascii=False) + "\n")


def _asdict_safe(obj) -> dict:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-+" else "_" for c in s)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m reviewer",
        description="Single Python Tkinter file code review harness "
                    "for weak (Qwen-3.5 class) LLMs accessed via opencode.",
    )
    p.add_argument("file", help="absolute or relative path to a single .py file")
    p.add_argument("--out", help="write Markdown report to this path "
                                  "(default: <run_dir>/report.md)")
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
    p.add_argument("--no-artifacts", action="store_true",
                   help="disable per-run artifacts directory")
    p.add_argument("--artifacts-root", default=None,
                   help="parent directory for run dirs (default: ./reviews)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)
