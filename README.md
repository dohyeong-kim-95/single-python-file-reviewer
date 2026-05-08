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
# 절대경로/상대경로 모두 허용. 결과는 기본적으로 ./reviews/<파일명>.md 에 저장.
python -m reviewer /path/to/big_app.py
# -> ./reviews/big_app.md 가 생성됨 (없으면 reviews/ 폴더 자동 생성)

# 출력 경로를 직접 지정
python -m reviewer ./big_app.py --no-llm --out report.md

# opencode 호출 옵션 전달
python -m reviewer big_app.py \
    --opencode-bin opencode \
    --opencode-extra-args "--model qwen2.5-coder" \
    --max-workers 4 --timeout 120
```

입력 파일 인코딩은 UTF-8 → UTF-8-SIG → CP949 → EUC-KR → latin-1 순으로 자동
폴백한다 (한국어 윈도우에서 작성된 CP949 파일도 그대로 읽힘). UTF-8이 아닌
인코딩으로 읽으면 INFO 로그에 표시된다.

`opencode` 가 표준입력으로 프롬프트를 받을 수 있다고 가정한다. 다른 호출
방식이 필요하면 `reviewer/opencode_client.py`의 `_run_once` 만 수정하면 된다.

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
