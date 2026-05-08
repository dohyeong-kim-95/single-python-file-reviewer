"""Subprocess wrapper around the `opencode` CLI.

Treats opencode as a stateless `prompt -> text` function and validates
the response strictly:
- The first balanced JSON object is extracted from arbitrary CLI output.
- Each finding's `line` must fall inside the chunk's range.
- Each finding's `evidence` must appear (substring, whitespace-collapsed)
  in the chunk source within ±2 lines of the reported line.
- Findings that fail validation are NOT clamped or invented; they are
  returned as RejectedFinding entries so the caller can write them to
  artifacts and exclude them from the user-facing report.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from .models import Chunk, ChunkResult, Finding, RejectedFinding
from .prompts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)


@dataclass
class OpencodeConfig:
    bin_path: str = "opencode"
    extra_args: tuple[str, ...] = ()
    timeout_sec: int = 120
    retries: int = 1  # one retry on JSON parse failure


class OpencodeError(RuntimeError):
    pass


class OpencodeClient:
    def __init__(self, config: OpencodeConfig) -> None:
        self.config = config
        if shutil.which(config.bin_path) is None:
            log.warning(
                "opencode binary %r not found on PATH at construction time; "
                "calls will fail until it is available.",
                config.bin_path,
            )

    def review_chunk(self, chunk: Chunk) -> ChunkResult:
        user_prompt = build_user_prompt(chunk)
        full_prompt = SYSTEM_PROMPT + "\n\n" + user_prompt

        last_stdout = ""
        last_error: Optional[str] = None
        payload: Optional[dict] = None
        for attempt in range(self.config.retries + 1):
            try:
                last_stdout = self._run_once(full_prompt)
                payload = _extract_json(last_stdout)
                if payload is None:
                    last_error = "JSON 객체를 추출하지 못함"
                    full_prompt = (
                        SYSTEM_PROMPT
                        + "\n\n중요: 이전 응답이 JSON이 아니었습니다. JSON 객체 1개만 출력하세요.\n\n"
                        + user_prompt
                    )
                    continue
                last_error = None
                break
            except subprocess.TimeoutExpired:
                last_error = f"opencode timeout > {self.config.timeout_sec}s"
            except OpencodeError as e:
                last_error = str(e)
            except Exception as e:  # pragma: no cover - defensive
                last_error = f"{type(e).__name__}: {e}"

        if payload is None:
            return ChunkResult(
                chunk_id=chunk.chunk_id,
                prompt=full_prompt,
                stdout=last_stdout,
                parsed=None,
                findings=[],
                rejected=[],
                error=last_error,
            )

        findings, rejected = _validate_payload(payload, chunk)
        return ChunkResult(
            chunk_id=chunk.chunk_id,
            prompt=full_prompt,
            stdout=last_stdout,
            parsed=payload,
            findings=findings,
            rejected=rejected,
            error=None,
        )

    def _run_once(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="reviewer-opencode-") as tmp:
            cmd = [self.config.bin_path, *self.config.extra_args]
            log.debug("opencode cmd=%s cwd=%s", cmd, tmp)
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_sec,
                cwd=tmp,
                check=False,
            )
            if proc.returncode != 0:
                raise OpencodeError(
                    f"opencode exited {proc.returncode}: {proc.stderr.strip()[:500]}"
                )
            return proc.stdout


# ---------------------------------------------------------------------------
# Output parsing & validation
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[dict]:
    """Extract the first balanced JSON object from arbitrary CLI output."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    blob = text[start:i + 1]
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError:
                        start = -1
                        continue
    return None


_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


def _validate_payload(payload: dict, chunk: Chunk) -> tuple[list[Finding], list[RejectedFinding]]:
    findings: list[Finding] = []
    rejected: list[RejectedFinding] = []
    raw = payload.get("findings") or []
    if not isinstance(raw, list):
        return findings, [RejectedFinding(
            chunk_id=chunk.chunk_id, reason="schema",
            raw={"error": "findings field is not a list", "payload": payload},
        )]

    chunk_lines = chunk.code.splitlines()

    for item in raw:
        if not isinstance(item, dict):
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="schema",
                raw={"error": "finding is not an object", "value": item},
            ))
            continue

        sev = str(item.get("severity", "")).lower()
        if sev not in ("info", "low", "medium", "high"):
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="schema",
                raw={"error": f"bad severity: {item.get('severity')!r}", "value": item},
            ))
            continue

        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="schema",
                raw={"error": "line missing or non-integer", "value": item},
            ))
            continue

        if line < chunk.start_line or line > chunk.end_line:
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="out-of-range",
                raw={
                    "error": f"line {line} outside chunk range "
                             f"[{chunk.start_line}, {chunk.end_line}]",
                    "value": item,
                },
            ))
            continue

        evidence = str(item.get("evidence", "")).strip()
        if not evidence:
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="evidence-missing",
                raw={"error": "evidence is empty", "value": item},
            ))
            continue

        if not _evidence_matches(evidence, line, chunk.start_line, chunk_lines):
            rejected.append(RejectedFinding(
                chunk_id=chunk.chunk_id, reason="evidence-missing",
                raw={
                    "error": "evidence not found in chunk near reported line",
                    "value": item,
                },
            ))
            continue

        confidence = str(item.get("confidence", "medium")).lower()
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"

        findings.append(Finding(
            severity=sev,  # type: ignore[arg-type]
            category=str(item.get("category", "review"))[:64],
            line=line,
            message=str(item.get("message", "")).strip()[:500],
            suggestion=str(item.get("suggestion", "")).strip()[:500],
            source="llm",
            chunk_id=chunk.chunk_id,
            confidence=confidence,
            evidence=evidence[:200],
        ))

    return findings, rejected


def _evidence_matches(evidence: str, line: int, chunk_start: int, lines: list[str]) -> bool:
    """True iff `evidence` (whitespace-normalized) is a substring of any source
    line within ±2 of `line` (also whitespace-normalized).
    """
    needle = _norm_ws(evidence)
    if not needle:
        return False
    rel = line - chunk_start
    for off in (0, -1, 1, -2, 2):
        idx = rel + off
        if 0 <= idx < len(lines):
            if needle in _norm_ws(lines[idx]):
                return True
    return False
