from __future__ import annotations
import base64
import hashlib
import http.server
import json
import mmap
import os
import re
import secrets
import shutil
import sys
import time
import tkinter as tk
import urllib.parse
import webbrowser
from collections import Counter
from datetime import datetime
from tkinter import ttk

import requests

CLIENT_ID: str | None = None  # 시작 시 detect_client_id()로 설정
AUTH_URL     = "https://claude.ai/oauth/authorize"
TOKEN_URL    = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "http://localhost:8765/callback"
SCOPE        = "user:profile user:inference"
USAGE_URL    = "https://api.anthropic.com/api/oauth/usage"
TOKEN_FILE   = os.path.expanduser("~/.claude_oauth.json")


def detect_client_id() -> str | None:
    """설치된 Claude Code CLI 바이너리/번들에서 OAuth client_id를 추출."""
    claude_path = shutil.which("claude")
    if not claude_path:
        return None
    real_path = os.path.realpath(claude_path)
    if not os.path.isfile(real_path):
        return None

    pattern = re.compile(
        rb'client_id["\']?\s*[:=]\s*["\']?'
        rb'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
        re.IGNORECASE,
    )

    # 바이너리에는 OAuth 흐름이 여러 개 들어있을 수 있으므로
    # 이 스크립트가 호출하는 도메인(claude.ai / platform.claude.com)이
    # 같은 영역에 등장하는 client_id를 우선 선택한다.
    preferred: list[str] = []
    fallback: list[str] = []
    try:
        with open(real_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                size = len(mm)
                for m in pattern.finditer(mm):
                    uid = m.group(1).decode()
                    window = mm[max(0, m.start() - 200):min(size, m.end() + 200)]
                    if b"claude.ai" in window or b"platform.claude.com" in window:
                        preferred.append(uid)
                    else:
                        fallback.append(uid)
    except (OSError, ValueError):
        return None

    pool = preferred or fallback
    if not pool:
        return None
    return Counter(pool).most_common(1)[0][0]


def show_install_required():
    """Claude Code 미설치 시 안내 창만 띄우고 사용량은 표시하지 않음."""
    root = tk.Tk()
    root.title("Claude 사용량")
    root.geometry("420x180")

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text="Claude Code 설치 필요",
        font=("TkDefaultFont", 14, "bold"),
        foreground="#c00",
    ).pack(pady=(8, 4))
    ttk.Label(
        frame,
        text="이 도구는 설치된 Claude Code CLI에서 OAuth client ID를 읽어 사용합니다.\n"
             "Claude Code를 먼저 설치한 뒤 다시 실행하세요.",
        justify="center",
        foreground="#444",
    ).pack(pady=(0, 12))
    ttk.Button(frame, text="닫기", command=root.destroy).pack()

    root.mainloop()


def generate_pkce():
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            self.server.auth_code = params["code"][0].split("#")[0]
            self.server.error = None
            body = b"<h2>Login success. Close this tab.</h2>"
        else:
            self.server.auth_code = None
            self.server.error = params.get("error", ["unknown"])[0]
            body = b"<h2>Login failed. Check terminal.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def wait_for_callback() -> str:
    server = http.server.HTTPServer(("localhost", 8765), CallbackHandler)
    server.auth_code = None
    server.error = None
    server.handle_request()
    if server.error:
        raise RuntimeError(f"OAuth 오류: {server.error}")
    return server.auth_code


def exchange_code(code: str, verifier: str, state: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        json={
            "grant_type":    "authorization_code",
            "code":          code,
            "state":         state,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "code_verifier": verifier,
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )
    resp.raise_for_status()
    return resp.json()


def refresh_tokens(refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        json={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     CLIENT_ID,
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )
    resp.raise_for_status()
    return resp.json()


def save_tokens(tokens: dict):
    # 저장 시점의 Unix timestamp를 함께 기록
    tokens["saved_at"] = int(time.time())
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def load_tokens() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return None


def is_token_expired(tokens: dict) -> bool:
    saved_at  = tokens.get("saved_at", 0)
    expires_in = tokens.get("expires_in", 28800)  # 기본 8시간
    # 만료 5분 전부터 갱신
    return time.time() >= saved_at + expires_in - 300


def get_valid_token(tokens: dict) -> str:
    """만료 여부를 확인하고 필요하면 자동 갱신 후 access_token 반환"""
    if not is_token_expired(tokens):
        return tokens["access_token"]

    print("access_token 만료 — refresh_token으로 갱신 중...")
    new_tokens = refresh_tokens(tokens["refresh_token"])
    save_tokens(new_tokens)

    # 전역 tokens 딕셔너리도 갱신
    tokens.update(new_tokens)
    print("토큰 갱신 완료")
    return tokens["access_token"]


def login() -> dict:
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    params = {
        "code":                  "true",
        "client_id":             CLIENT_ID,
        "response_type":         "code",
        "redirect_uri":          REDIRECT_URI,
        "scope":                 SCOPE,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("브라우저를 열어 Claude에 로그인합니다...")
    print(f"\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    print("로그인 완료를 기다리는 중...")
    code = wait_for_callback()

    print("토큰 교환 중...")
    return exchange_code(code, verifier, state)


def fetch_usage(access_token: str) -> dict:
    resp = requests.get(
        USAGE_URL,
        headers={
            "Authorization":  f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent":     "claude-code/2.0.32",
        },
    )
    resp.raise_for_status()
    return resp.json()


def extract_percent(section: dict | None) -> float | None:
    """usage JSON 한 섹션에서 사용량 퍼센트 값을 추출."""
    if not isinstance(section, dict):
        return None
    for key in ("utilization", "percentage", "percent", "used_percent", "usage_percent"):
        if key in section:
            value = section[key]
            if isinstance(value, (int, float)):
                # 0~1 범위면 100배, 그 외엔 그대로
                return value * 100 if value <= 1 else float(value)
    return None


def format_reset(section: dict | None, now: datetime | None = None) -> str:
    if not isinstance(section, dict):
        return ""
    for key in ("resets_at", "reset_at", "reset"):
        value = section.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return f"리셋: {value}"
        current = now
        if current is None:
            current = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        elif dt.tzinfo:
            if current.tzinfo is None:
                current = current.astimezone()
            current = current.astimezone(dt.tzinfo)
        elif current.tzinfo is not None:
            current = current.astimezone().replace(tzinfo=None)
        remaining = max(0, int((dt - current).total_seconds()))
        days, rem = divmod(remaining, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        if days > 0:
            return f"리셋까지: {days}일 {hours}시간 {minutes}분 {seconds}초"
        return f"리셋까지: {hours}시간 {minutes}분 {seconds}초"
    return ""


class UsageApp:
    REFRESH_MS = 300_000  # 5분
    STATUS_TICK_MS = 1_000  # 1초

    def __init__(self, root: tk.Tk, tokens: dict):
        self.root = root
        self.tokens = tokens
        self.last_refresh_at: datetime | None = None
        self.last_error: str | None = None
        self.last_five_usage: dict | None = None
        self.last_seven_usage: dict | None = None

        root.title("Claude 사용량")
        root.geometry("420x260")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Green.Horizontal.TProgressbar",
            background="#2ca02c",
            troughcolor="#e6e6e6",
            bordercolor="#e6e6e6",
            lightcolor="#2ca02c",
            darkcolor="#2ca02c",
            borderwidth=0,
            thickness=14,
        )

        main = ttk.Frame(root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        main.columnconfigure(1, weight=0)

        self.five_hour_label = ttk.Label(main, text="-", font=("TkDefaultFont", 12, "bold"))
        self.five_hour_label.grid(row=0, column=0, sticky="w")
        self.five_hour_period = ttk.Label(main, text="5시간", foreground="#888")
        self.five_hour_period.grid(row=0, column=1, sticky="e")
        self.five_hour_bar = ttk.Progressbar(main, maximum=100, length=380, style="Green.Horizontal.TProgressbar")
        self.five_hour_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 2))
        self.five_hour_reset = ttk.Label(main, text="", foreground="#666")
        self.five_hour_reset.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 12))

        self.seven_day_label = ttk.Label(main, text="-", font=("TkDefaultFont", 12, "bold"))
        self.seven_day_label.grid(row=3, column=0, sticky="w")
        self.seven_day_period = ttk.Label(main, text="7일", foreground="#888")
        self.seven_day_period.grid(row=3, column=1, sticky="e")
        self.seven_day_bar = ttk.Progressbar(main, maximum=100, length=380, style="Green.Horizontal.TProgressbar")
        self.seven_day_bar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 2))
        self.seven_day_reset = ttk.Label(main, text="", foreground="#666")
        self.seven_day_reset.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 12))

        footer = ttk.Frame(main)
        footer.grid(row=6, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(footer, text="갱신 대기 중...", foreground="#666")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.always_on_top = tk.BooleanVar(value=False)
        self.topmost_check = ttk.Checkbutton(
            footer,
            text="항상 위",
            variable=self.always_on_top,
            command=self._toggle_topmost,
        )
        self.topmost_check.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.refresh_btn = ttk.Button(footer, text="새로고침", command=self.refresh)
        self.refresh_btn.grid(row=0, column=2, sticky="e")

        self.refresh()
        self._tick_status()

        root.update_idletasks()
        root.minsize(root.winfo_width(), root.winfo_height())

    def _toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.always_on_top.get()))

    def refresh(self):
        try:
            access_token = get_valid_token(self.tokens)
            usage = fetch_usage(access_token)
        except Exception as exc:
            self.last_error = str(exc)
        else:
            self._apply_usage(usage)
            self.last_refresh_at = datetime.now()
            self.last_error = None
        finally:
            self._update_status_text()
            self.root.after(self.REFRESH_MS, self.refresh)

    def _tick_status(self):
        self._update_status_text()
        self.root.after(self.STATUS_TICK_MS, self._tick_status)

    def _update_status_text(self):
        if self.last_error:
            self.status_label.config(text=f"오류: {self.last_error}", foreground="#c00")
            return
        if self.last_refresh_at is None:
            self.status_label.config(text="갱신 대기 중...", foreground="#666")
            return
        now = datetime.now()
        minutes = int((now - self.last_refresh_at).total_seconds() // 60)
        text = "마지막 갱신: 방금" if minutes <= 0 else f"마지막 갱신: {minutes}분 전"
        self.status_label.config(text=text, foreground="#666")
        self._update_reset_labels(now.astimezone())

    def _update_reset_labels(self, now: datetime):
        self.five_hour_reset.config(text=format_reset(self.last_five_usage, now=now))
        self.seven_day_reset.config(text=format_reset(self.last_seven_usage, now=now))

    def _apply_usage(self, usage: dict):
        five = usage.get("five_hour")
        seven = usage.get("seven_day")
        self.last_five_usage = five if isinstance(five, dict) else None
        self.last_seven_usage = seven if isinstance(seven, dict) else None

        five_pct = extract_percent(five)
        seven_pct = extract_percent(seven)

        if five_pct is None:
            self.five_hour_label.config(text="데이터 없음")
            self.five_hour_bar["value"] = 0
        else:
            self.five_hour_label.config(text=f"{five_pct:.0f}%")
            self.five_hour_bar["value"] = max(0.0, min(100.0, five_pct))
        self.five_hour_reset.config(text=format_reset(self.last_five_usage))

        if seven_pct is None:
            self.seven_day_label.config(text="데이터 없음")
            self.seven_day_bar["value"] = 0
        else:
            self.seven_day_label.config(text=f"{seven_pct:.0f}%")
            self.seven_day_bar["value"] = max(0.0, min(100.0, seven_pct))
        self.seven_day_reset.config(text=format_reset(self.last_seven_usage))


if __name__ == "__main__":
    if "--logout" in sys.argv:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
            print("토큰 삭제됨.")
        else:
            print("저장된 토큰 없음.")
        sys.exit(0)

    CLIENT_ID = detect_client_id()
    if CLIENT_ID is None:
        show_install_required()
        sys.exit(0)

    tokens = load_tokens()
    if not tokens:
        tokens = login()
        save_tokens(tokens)

    root = tk.Tk()
    UsageApp(root, tokens)
    root.mainloop()
