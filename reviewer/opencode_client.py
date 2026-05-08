"""Subprocess wrapper around the `opencode` CLI.

The harness treats opencode as a stateless `prompt -> text` function:
- Each call gets a fresh temporary working directory so opencode's
  session/history (if any) cannot leak across chunks.
- The user prompt is fed via stdin (most opencode-like CLIs accept this);
  if a future opencode build requires a flag, change `_run_once()` only.
- The response is parsed by extracting the first balanced JSON object,
  because weak models often wrap output in chatter or code fences.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from .models import Chunk, Finding
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

    def review_chunk(self, chunk: Chunk) -> tuple[list[Finding], Optional[str]]:
        """Returns (findings, error_message). error_message is None on success."""
        user_prompt = build_user_prompt(chunk)
        full_prompt = SYSTEM_PROMPT + "\n\n" + user_prompt

        last_error: Optional[str] = None
        for attempt in range(self.config.retries + 1):
            try:
                stdout = self._run_once(full_prompt)
                payload = _extract_json(stdout)
                if payload is None:
                    last_error = "JSON 객체를 추출하지 못함"
                    full_prompt = (
                        SYSTEM_PROMPT
                        + "\n\n중요: 이전 응답이 JSON이 아니었습니다. JSON 객체 1개만 출력하세요.\n\n"
                        + user_prompt
                    )
                    continue
                return _payload_to_findings(payload, chunk), None
            except subprocess.TimeoutExpired:
                last_error = f"opencode timeout > {self.config.timeout_sec}s"
            except OpencodeError as e:
                last_error = str(e)
            except Exception as e:  # pragma: no cover - defensive
                last_error = f"{type(e).__name__}: {e}"
        return [], last_error

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
# Output parsing
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


def _payload_to_findings(payload: dict, chunk: Chunk) -> list[Finding]:
    out: list[Finding] = []
    raw = payload.get("findings") or []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "low")).lower()
        if sev not in ("info", "low", "medium", "high"):
            sev = "low"
        try:
            line = int(item.get("line", chunk.start_line))
        except (TypeError, ValueError):
            line = chunk.start_line
        # Clamp to the chunk range so a hallucinated line number doesn't
        # make it into the report unchecked.
        line = max(chunk.start_line, min(chunk.end_line, line))
        out.append(Finding(
            severity=sev,  # type: ignore[arg-type]
            category=str(item.get("category", "review"))[:64],
            line=line,
            message=str(item.get("message", "")).strip()[:500],
            suggestion=str(item.get("suggestion", "")).strip()[:500],
            source="llm",
            chunk_id=chunk.chunk_id,
        ))
    return out
