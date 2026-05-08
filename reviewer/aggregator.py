"""Merge static + LLM findings, dedupe, sort."""

from __future__ import annotations

from typing import Iterable

from .models import Finding, ProjectContext, Report, StaticSmell

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def merge(
    file_path: str,
    project: ProjectContext,
    llm_findings: Iterable[Finding],
    chunk_failures: Iterable[str] = (),
) -> Report:
    findings: list[Finding] = []

    for s in project.smells:
        findings.append(_smell_to_finding(s))

    findings.extend(llm_findings)

    findings = _dedupe(findings)
    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9),
        f.line,
        f.category,
        f.message,
    ))

    return Report(
        file_path=file_path,
        line_count=project.line_count,
        project=project,
        findings=findings,
        chunk_failures=list(chunk_failures),
    )


def _smell_to_finding(s: StaticSmell) -> Finding:
    return Finding(
        severity=s.severity,
        category=s.category,
        line=s.line,
        message=s.message,
        suggestion="",
        source="static",
        chunk_id=None,
    )


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: dict[tuple[int, str, str], Finding] = {}
    for f in findings:
        key = (f.line, f.category, _norm(f.message))
        prior = seen.get(key)
        if prior is None:
            seen[key] = f
            continue
        # Prefer static finding (deterministic) over LLM, and higher severity.
        if prior.source == "static" and f.source != "static":
            continue
        if f.source == "static" and prior.source != "static":
            seen[key] = f
            continue
        if SEVERITY_ORDER.get(f.severity, 9) < SEVERITY_ORDER.get(prior.severity, 9):
            seen[key] = f
    return list(seen.values())


def _norm(s: str) -> str:
    return " ".join(s.lower().split())[:120]
