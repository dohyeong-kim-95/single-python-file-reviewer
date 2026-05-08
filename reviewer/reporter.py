"""Render a Report to Markdown."""

from __future__ import annotations

from collections import Counter

from .models import Report

SEVERITY_BADGE = {
    "high": "🔴 high",
    "medium": "🟠 medium",
    "low": "🟡 low",
    "info": "🔵 info",
}


def render(report: Report) -> str:
    parts: list[str] = []
    parts.append(f"# Tkinter Code Review — {report.file_path}\n")
    parts.append(_summary_section(report))
    parts.append(_structure_section(report))
    parts.append(_findings_section(report))
    if report.chunk_failures:
        parts.append(_failures_section(report))
    return "\n\n".join(parts).rstrip() + "\n"


def _summary_section(report: Report) -> str:
    sev_counts = Counter(f.severity for f in report.findings)
    src_counts = Counter(f.source for f in report.findings)
    rejected_line = (
        f"  \n- LLM이 보고했으나 evidence/range 검증 실패로 폐기된 항목: "
        f"**{report.rejected_count}** (artifacts/dropped_findings.jsonl 참고)"
        if report.rejected_count else ""
    )
    line = (
        f"- 파일 라인 수: **{report.line_count}**  \n"
        f"- 발견 항목: **{len(report.findings)}** "
        f"(high {sev_counts.get('high', 0)}, "
        f"medium {sev_counts.get('medium', 0)}, "
        f"low {sev_counts.get('low', 0)}, "
        f"info {sev_counts.get('info', 0)})  \n"
        f"- 출처: static {src_counts.get('static', 0)}, "
        f"llm {src_counts.get('llm', 0)}"
        f"{rejected_line}"
    )
    return f"## Summary\n\n{line}"


def _structure_section(report: Report) -> str:
    p = report.project
    rows: list[str] = []
    rows.append("## Tkinter 구조 요약")
    rows.append("")
    rows.append(f"- mainloop 사용: **{p.has_mainloop}**")
    rows.append(f"- WM_DELETE_WINDOW protocol: **{p.has_wm_delete_protocol}**")
    rows.append(
        f"- update / update_idletasks: **{p.uses_update}** / **{p.uses_update_idletasks}**"
    )
    rows.append(f"- 클래스 수: **{len(p.classes)}**, 최상위 함수 수: **{len(p.top_level_funcs)}**")
    rows.append("")

    rows.append("### 위젯 트리 (생성 지점 기준)")
    rows.append("")
    if p.widget_tree:
        rows.append("| line | var | class | parent |")
        rows.append("| --- | --- | --- | --- |")
        for w in p.widget_tree:
            rows.append(f"| {w.line} | {_e(w.var_name)} | {w.widget_class} | {_e(str(w.parent_var))} |")
    else:
        rows.append("_(위젯 생성 호출이 발견되지 않음)_")
    rows.append("")

    rows.append("### 이벤트 / 콜백 바인딩")
    rows.append("")
    if p.bindings:
        rows.append("| line | kind | widget | sequence | handler |")
        rows.append("| --- | --- | --- | --- | --- |")
        for b in p.bindings:
            rows.append(
                f"| {b.line} | {b.kind} | {_e(str(b.widget_var))} | "
                f"{_e(str(b.sequence))} | {_e(b.handler_repr)} |"
            )
    else:
        rows.append("_(바인딩 없음)_")
    return "\n".join(rows)


def _findings_section(report: Report) -> str:
    if not report.findings:
        return "## Findings\n\n_(발견 항목 없음)_"
    rows: list[str] = ["## Findings"]
    by_sev: dict[str, list] = {"high": [], "medium": [], "low": [], "info": []}
    for f in report.findings:
        by_sev.setdefault(f.severity, []).append(f)
    for sev in ("high", "medium", "low", "info"):
        items = by_sev.get(sev) or []
        if not items:
            continue
        rows.append(f"\n### {SEVERITY_BADGE[sev]} ({len(items)})")
        rows.append("")
        rows.append("| line | category | source | message | suggestion |")
        rows.append("| --- | --- | --- | --- | --- |")
        for f in items:
            rows.append(
                f"| {report.file_path}:{f.line} | {_e(f.category)} | {f.source} | "
                f"{_e(f.message)} | {_e(f.suggestion)} |"
            )
    return "\n".join(rows)


def _failures_section(report: Report) -> str:
    items = "\n".join(f"- {f}" for f in report.chunk_failures)
    return f"## 청크 실패\n\n{items}"


def _e(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
