# claude_usage_monitor

Claude 사용량(5시간/7일 한도)을 데스크톱 창에서 확인하는 작은 Tk GUI 도구.

설치된 Claude Code CLI에서 OAuth `client_id`를 직접 읽어 같은 자격증명 흐름으로 로그인하고, `https://api.anthropic.com/api/oauth/usage` 엔드포인트의 응답을 5분 간격으로 갱신해 표시함.

## 주요 기능

- 시작 시 Claude Code CLI 설치 여부 검사. 미설치면 **"Claude Code 설치 필요"** 안내 창만 띄움
- 설치된 Claude Code 바이너리에서 OAuth `client_id`(UUID)를 추출해 그대로 사용 — 스크립트에 별도 등록·하드코딩 불필요
- PKCE + 로컬 콜백(`http://localhost:8765/callback`)으로 브라우저 로그인 1회 후 `~/.claude_oauth.json`에 토큰 저장
- `access_token` 만료 5분 전부터 `refresh_token`으로 자동 갱신
- 5시간/7일 사용률 막대그래프, 리셋까지 남은 시간, "항상 위" 토글, 마지막 갱신 시각 표시

## 요구 사항

- Python 3.10 이상 (`str | None` 형태의 타입 힌트와 `from __future__ import annotations` 사용)
- Tkinter (Ubuntu 기준 `sudo apt install python3-tk`)
- `requests`
- 설치된 [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) — 이 도구는 CLI 바이너리에서 client_id를 읽으므로 반드시 사전 설치 필요

## 설치 및 실행

```bash
pip install requests
python3 scripts/check_claude_usage.py &
```

처음 실행하면 브라우저가 열려 Claude 로그인 페이지로 이동함. 로그인 완료 후 자동으로 콜백을 받아 토큰을 `~/.claude_oauth.json`에 저장하고 사용량 창이 뜸.

로그아웃(저장된 토큰 삭제):

```bash
python3 scripts/check_claude_usage.py --logout
```

## 테스트 환경

| 항목 | 값 |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| 커널 | Linux 6.17.0-20-generic |
| Python | 3.12.3 |
| Claude Code CLI | 2.1.121 (네이티브 인스톨러, ELF 단일 바이너리 약 247MB) |
| 설치 경로 | `~/.local/bin/claude` → `~/.local/share/claude/versions/2.1.121` |

이 환경에서 client_id 자동 탐지가 정상 동작함을 확인 (`9d1c250a-e61b-44d9-88ed-5944d1962f5e` 추출, claude.ai/platform.claude.com 컨텍스트 매칭).

## 한계

- 패키징 방식에 따라 `claude` 명령이 셸 래퍼 스크립트로 설치된 경우 realpath가 스크립트 텍스트를 가리켜 UUID를 못 찾을 수 있음. 이 경우 안내 창이 뜸
- 사용량 응답 스키마가 바뀌면 `extract_percent` / `format_reset`이 인식하는 키 목록(`utilization`/`percentage`/`resets_at` 등)을 갱신해야 함
- Anthropic 비공식 OAuth 흐름·엔드포인트에 의존하므로 사양이 바뀌면 작동하지 않을 수 있음
