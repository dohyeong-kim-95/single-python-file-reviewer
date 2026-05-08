# single-python-file-reviewer

5000+ 라인의 단일 Python Tkinter 파일을 **약한 LLM(Qwen 3.5급)** 으로
리뷰하기 위한 코드 리뷰 하네스. 모델 호출은 OpenAI API 등 직접 HTTP가
아니라 `opencode` CLI를 subprocess로 호출하는 방식으로 수행한다.

## 핵심 아이디어

약한 모델은 5000라인을 한 번에 추론할 수 없고, 위젯 트리/이벤트 그래프
같은 구조 분석도 신뢰하기 어렵다. 따라서 하네스가 무거운 일을 대신한다.

```
.py file
  └── static_analyzer (AST: 위젯 트리, 이벤트, 정적 스멜)
        └── chunker (클래스/메서드 단위로 잘라 사전 분석 컨텍스트 부착)
              └── opencode_client (각 청크마다 subprocess 호출, JSON 추출)
                    └── aggregator (정적 + LLM finding 병합·중복제거)
                          └── reporter (Markdown)
```

## 사용법

```bash
# 절대경로/상대경로 모두 허용. 매 실행마다 timestamped run dir 가 생성된다.
python -m reviewer /path/to/big_app.py
# -> ./reviews/<YYYYMMDD-HHMMSS>_big_app/
#       report.md
#       static_context.json
#       chunks/<chunk_id>.{prompt,stdout,parsed,error}.*
#       dropped_findings.jsonl   (검증 실패한 LLM finding 이 있을 때만)

# 출력 경로를 직접 지정 (artifacts dir 는 그대로 reviews/ 아래에 생성됨)
python -m reviewer ./big_app.py --no-llm --out report.md

# artifacts 끄기
python -m reviewer big_app.py --no-artifacts

# opencode 호출 옵션 전달
python -m reviewer big_app.py \
    --opencode-bin opencode \
    --opencode-extra-args "--model qwen2.5-coder" \
    --max-workers 4 --timeout 120
```

`./reviews/` 는 `.gitignore` 에 등록되어 있으므로 커밋되지 않는다.

입력 파일 인코딩은 UTF-8 → UTF-8-SIG → CP949 → EUC-KR → latin-1 순으로 자동
폴백한다 (한국어 윈도우에서 작성된 CP949 파일도 그대로 읽힘). UTF-8이 아닌
인코딩으로 읽으면 INFO 로그에 표시된다.

`opencode` 가 표준입력으로 프롬프트를 받을 수 있다고 가정한다. 다른 호출
방식이 필요하면 `reviewer/opencode_client.py`의 `_run_once` 만 수정하면 된다.

## LLM finding 검증

약한 모델이 만들어낸 finding 은 다음 두 단계를 모두 통과해야 최종 리포트에
실린다.

1. **line range** — `line` 값이 해당 청크의 라인 범위 안이어야 한다. 범위 밖
   값은 클램프하지 않고 그대로 폐기한다.
2. **evidence** — `evidence` 필드(해당 라인 원문 발췌)가 청크 코드의
   `line ± 2` 라인 안에서 공백 정규화 후 substring 으로 나타나야 한다.

폐기된 항목은 final report 에서 제외되지만 `dropped_findings.jsonl` 에
원본 페이로드와 폐기 사유(`out-of-range`, `evidence-missing`, `schema`)가
함께 기록된다. Summary 섹션에 폐기 개수만 표시된다.

## Inbound context

5000+ 라인 단일 파일 Tkinter 코드는 `__init__` 에서 `bind`/`command`/`after`/
`trace`/`protocol` 로 핸들러를 등록하고 실제 로직은 다른 메서드에 두는 패턴이
많다. 약한 LLM 이 호출 관계를 추적하지 못해 오판하는 일을 줄이기 위해, 각
청크 프롬프트에는 “이 청크의 메서드가 다른 어디에서 핸들러로 등록되는지”
표(inbound)가 첨부된다. (1차 MVP 기준 Tkinter 핸들러 등록만; 일반
`self.<attr>` 호출 그래프는 추후.)

## 구성 요소

| 모듈 | 역할 |
| --- | --- |
| `reviewer/static_analyzer.py` | AST 기반 위젯/이벤트/스멜 추출 |
| `reviewer/chunker.py`         | 클래스/메서드 경계로 청크 분할 + 컨텍스트 슬라이스 |
| `reviewer/prompts.py`         | system prompt + Tkinter 체크리스트 |
| `reviewer/opencode_client.py` | opencode CLI subprocess 래퍼, JSON 추출 |
| `reviewer/aggregator.py`      | 정적 + LLM finding 병합/중복제거/정렬 |
| `reviewer/reporter.py`        | Markdown 리포트 렌더 |
| `reviewer/io_utils.py`        | 소스 파일 인코딩 폴백 (UTF-8 → CP949 → ...) |
| `reviewer/cli.py`             | argparse 진입점, 병렬 실행 |

## 검사하는 Tkinter 패턴 (초기)

- 같은 부모에 `pack` + `grid` 혼용 (Tkinter 무한 루프)
- `command=fn()` 즉시 호출 실수
- `PhotoImage` 결과를 self 등에 보관하지 않아 GC됨
- `after()` 로 자기 자신을 종료 조건 없이 재호출
- 이벤트 핸들러 안에서 `time.sleep` / `requests.*` 등 블로킹 호출
- `update()` 직접 호출 (`update_idletasks()` 권장)
- `mainloop()` 사용하면서 `WM_DELETE_WINDOW` 프로토콜 미설정

이외 항목은 `reviewer/prompts.py::TKINTER_CHECKLIST` 에 추가하면 LLM도 함께 검사한다.

## 테스트

```bash
pytest -q
# 합성 5000+ 라인 픽스처 재생성
python tests/fixtures/make_synthetic_5k.py
```
