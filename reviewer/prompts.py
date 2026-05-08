"""Prompt templates and the Tkinter checklist passed to the weak LLM.

Design notes:
- The model is roughly Qwen-3.5 class. Keep the prompt very short, very
  concrete, and demand JSON-only output with a fixed schema.
- We pass pre-computed structural context (widget tree, bindings, smells)
  so the model does not need to "find" them from raw code.
"""

from __future__ import annotations

from .models import Chunk

TKINTER_CHECKLIST: list[dict[str, str]] = [
    {
        "id": "geom-mix",
        "title": "같은 부모에 pack/grid 혼용",
        "hint": "한 부모 위젯의 자식들이 pack과 grid를 동시에 사용하면 Tkinter가 무한 루프에 빠집니다.",
    },
    {
        "id": "command-call",
        "title": "command=fn() 형태의 즉시 호출 실수",
        "hint": "command=fn 으로 콜백을 넘겨야 합니다. fn() 은 결과(보통 None)를 콜백으로 등록합니다.",
    },
    {
        "id": "lost-image",
        "title": "PhotoImage / ImageTk 결과를 self 등 영속 참조에 보관하지 않음",
        "hint": "지역 변수에만 두면 GC로 이미지가 사라집니다.",
    },
    {
        "id": "after-loop",
        "title": "after()로 자기 자신을 무한 재호출",
        "hint": "종료 조건 없이 after(...,self.tick) 류의 호출이 있으면 위험합니다.",
    },
    {
        "id": "blocking-handler",
        "title": "이벤트 핸들러 안에서 블로킹 호출(time.sleep, requests.* 등)",
        "hint": "UI 스레드를 멈추므로 백그라운드 스레드 + after()로 갱신해야 합니다.",
    },
    {
        "id": "update-call",
        "title": ".update() 직접 호출",
        "hint": "update_idletasks() 를 권장. update()는 재귀 이벤트 처리 위험이 있습니다.",
    },
    {
        "id": "missing-wm-delete",
        "title": "WM_DELETE_WINDOW 프로토콜 미설정",
        "hint": "리소스 정리 누수가 발생할 수 있습니다.",
    },
    {
        "id": "thread-widget",
        "title": "비-메인 스레드에서 위젯 직접 조작",
        "hint": "Tkinter 위젯은 메인 스레드에서만 다뤄야 합니다. queue + after() 패턴이 안전합니다.",
    },
    {
        "id": "trace-leak",
        "title": "StringVar.trace 또는 bind 누적 (해제 없음)",
        "hint": "동일 위젯을 재사용하며 같은 sequence를 반복 bind 하면 핸들러가 중복 호출됩니다.",
    },
    {
        "id": "global-state",
        "title": "전역 가변 상태 사용",
        "hint": "5000+ line 단일 파일에서 전역 변수는 동시성·테스트 가능성을 해칩니다.",
    },
]


SYSTEM_PROMPT = (
    "당신은 Python Tkinter 코드 리뷰어입니다. "
    "주어진 코드 조각과 사전 분석 컨텍스트만 보고 결함을 찾아주세요. "
    "다음 규칙을 반드시 지킵니다:\n"
    "1) 출력은 단 하나의 JSON 객체. 그 외 텍스트·마크다운·코드펜스 금지.\n"
    "2) 스키마: {\"findings\": [{\"severity\": \"low|medium|high\", "
    "\"category\": str, \"line\": int, \"message\": str, \"suggestion\": str}]}.\n"
    "3) line 은 청크의 절대 라인 번호 (1부터 시작, 파일 기준).\n"
    "4) 확신이 없으면 보고하지 말고 빈 findings 배열을 반환하세요.\n"
    "5) 체크리스트 외 내용을 자유롭게 추가해도 되지만, 반드시 라인 번호를 명시해야 합니다."
)


def build_user_prompt(chunk: Chunk) -> str:
    checklist = "\n".join(
        f"- [{item['id']}] {item['title']}: {item['hint']}"
        for item in TKINTER_CHECKLIST
    )
    notes = "\n".join(f"- {n}" for n in chunk.context.notes) or "_(없음)_"
    return f"""# 청크 정보
- 제목: {chunk.title}
- 라인 범위: {chunk.start_line} ~ {chunk.end_line}

# 사전 분석: 이 청크의 위젯
{chunk.context.widget_tree_md}

# 사전 분석: 이 청크의 이벤트/콜백
{chunk.context.bindings_md}

# 사전 분석: 이 청크에 이미 발견된 정적 스멜 (참고용, 중복 보고 금지)
{chunk.context.smells_md}

# 프로젝트 전체 메모
{notes}

# 체크리스트 (이 항목 위주로 검사)
{checklist}

# 코드 (라인 번호 포함)
```python
{_with_line_numbers(chunk)}
```

위 지침에 따라 JSON만 출력하세요.
"""


def _with_line_numbers(chunk: Chunk) -> str:
    out = []
    for i, line in enumerate(chunk.code.splitlines()):
        out.append(f"{chunk.start_line + i:5d}  {line}")
    return "\n".join(out)
