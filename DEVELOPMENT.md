# QTrader 작업 안내 (개발자용)

국내·미국 주식 AI 퀀트 자동매매 데스크톱 앱. 각 사용자가 **자기 PC에서 자기 키로** 돌린다 (토스증권 Open API 중심, 한국투자증권도 지원).
남은 일과 다음 작업은 [docs/NEXT.md](docs/NEXT.md), 사용자 안내는 [README.md](README.md).

## 실행 · 시험
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e .   # 리눅스/맥: .venv/bin/python
.venv/Scripts/python -m pytest -q                                    # 전부 오프라인 (QTRADER_OFFLINE=1, 가짜 서버)
QTRADER_HOME=.devhome .venv/Scripts/python -m qtrader serve --no-browser   # http://127.0.0.1:8765
.venv/Scripts/python -m ruff check qtrader tests --select F
```
- 데이터 폴더: `QTRADER_HOME` (기본 `%APPDATA%\QTrader`). 개발은 `.devhome/` (git 에 안 올림).
- 키는 윈도우 자격 증명 관리자(keyring)에만 있다. 저장소·로그·채팅에 키를 넣지 않는다.
- 클라우드 세션에는 사용자 키가 없고, 토스 Open API 는 허용 IP 만 받는다 → **체험 데이터(키 없음)와 가짜 서버 테스트로 개발**하고, 실제 연결 확인은 사용자 PC에서.

## 구조
- `qtrader/engine.py` 주문 흐름(목표 비중 → 리스크 → 주문, 체결 동기화, 손절·익절 `check_exits`)
- `qtrader/scheduler.py` 2분 틱: 체결 확인·기업 이벤트·토스 서버 보호(OCO)·조건주문·장중 감시·장중 회의 → 한 종목 매수(`_intraday` → `engine.buy_one`), 정기 실행 전 AI 회의
- `qtrader/agents/` AI 투자 회의(`meeting.py`, 역할 `roles.py`), 큐비 비서(`assistant.py`, 스트리밍·도구 21개)
- `qtrader/toss.py` 토스 API (명세 v1.2.24, 호출 그룹별 속도 제한), `guard.py` OCO 서버 보호, `corp.py` 분할·병합 처리, `conditional.py` 조건주문
- `qtrader/screener.py` 후보 고르기(멀티팩터·DART 공시·재무·종목 기본 정보 거르기), `principles.py` 매수 체크리스트
- `qtrader/web/app.py` FastAPI, `qtrader/web/static/` 화면(프레임워크 없는 JS, 스크립트 순서는 index.html)
- `packaging/` PyInstaller 빌드(`build_exe.cmd`), 배포 압축(`release.cmd`), 자동 업데이트는 `qtrader/updater.py`

## 지켜야 할 것
- **화면 글은 쉬운 한국어**(초보자 기준). 커밋 메시지도 한국어로, 무엇을 왜 바꿨는지.
- **브랜치 이름에 `claude/` 를 붙이지 않는다.** 실무처럼 `종류/짧은-영어-설명`: `feature/`(새 기능) · `fix/`(버그) · `docs/`(문서) · `refactor/` · `test/` (예: `feature/paper-reset`, `fix/picks-popover`). 세션이 `claude/...` 브랜치를 정해 줘도 이 규칙으로 새 브랜치를 만들어 올린다.
- 실전 안전장치를 약하게 만들지 않는다: 실전 전환 확인·PIN·하루 손실 한도·긴급 정지·두 번 매도 방지(`guard.release` 먼저).
- **브라우저 기본 창(confirm/prompt/alert) 금지** — 앱 안 브라우저에서 바로 취소된다. `ask()` / `toast()` 를 쓴다 (테스트가 잡는다).
- 화면 스크립트끼리 최상위 const/let·함수 이름이 겹치면 안 된다, 화면이 부르는 `/api/...` 는 실제로 있어야 한다 (`tests/test_static.py`).
- 토스 명세에서: 미국 종목의 거래대금·시가총액은 **원화 환산값**, OCO 는 두 다리 모두 매도·지정가, 국내 조건주문은 KRX 정규장에서만 발동.
- Claude 모델·API 사용은 `qtrader/agents/llm.py` 기준(서버 대체 모델 `fallbacks="default"`, 프롬프트 캐시).
- 수익을 약속하는 말은 쓰지 않는다. 100% 승률은 불가능하고, 손실을 작게 끊는 규칙으로 돕는 도구다.
