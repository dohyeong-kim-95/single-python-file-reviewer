from reviewer.aggregator import merge
from reviewer.models import Finding, ProjectContext, StaticSmell


def _ctx(smells=()):
    return ProjectContext(source="", line_count=10, smells=list(smells))


def test_static_smells_become_findings():
    ctx = _ctx([
        StaticSmell(category="layout", line=5, message="m", severity="high"),
    ])
    rep = merge("f.py", ctx, llm_findings=[])
    assert len(rep.findings) == 1
    assert rep.findings[0].source == "static"


def test_static_dedupes_against_llm_with_same_key():
    ctx = _ctx([
        StaticSmell(category="layout", line=5, message="hello", severity="high"),
    ])
    llm = [Finding(severity="medium", category="layout", line=5,
                   message="HELLO", suggestion="x", source="llm")]
    rep = merge("f.py", ctx, llm_findings=llm)
    assert len(rep.findings) == 1
    # Static wins
    assert rep.findings[0].source == "static"


def test_severity_sort():
    ctx = _ctx()
    llm = [
        Finding(severity="low", category="x", line=1, message="a", suggestion="", source="llm"),
        Finding(severity="high", category="y", line=2, message="b", suggestion="", source="llm"),
        Finding(severity="medium", category="z", line=3, message="c", suggestion="", source="llm"),
    ]
    rep = merge("f.py", ctx, llm_findings=llm)
    sevs = [f.severity for f in rep.findings]
    assert sevs == ["high", "medium", "low"]
