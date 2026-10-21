"""Local task intake for a chat running on the connected host.

Registers tasks only. Execution remains in the existing reviewed workflow.
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("로컬 서버의 리디렉션은 허용하지 않습니다.")


class Bridge:
    def __init__(self, port=8765):
        if not 1 <= port <= 65535:
            raise ValueError("포트 범위가 올바르지 않습니다.")
        self.base = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, body=None, csrf=None):
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        req = Request(self.base + path, data=payload, headers=headers)
        with self.opener.open(req, timeout=10) as response:
            return json.load(response)

    def status(self):
        state = self.request("/api/state")
        # The local CSRF token is intentionally never returned to chat output.
        return {key: state[key] for key in ("projects", "tasks", "runs", "active")}

    def register(self, data):
        required = ("project_id", "title", "prompt", "acceptance")
        if not isinstance(data, dict) or any(
            not isinstance(data.get(k), str) or not data[k].strip() for k in required
        ):
            raise ValueError("project_id, title, prompt, acceptance 문자열이 필요합니다.")
        for field, limit in (("title", 200), ("prompt", 20000), ("acceptance", 12000)):
            if len(data[field]) > limit or "\0" in data[field]:
                raise ValueError(f"{field}: 최대 {limit}자이며 NUL 문자는 허용하지 않습니다.")
        state = self.request("/api/state")
        if not any(p["id"] == data["project_id"] for p in state["projects"]):
            raise ValueError("연결된 프로젝트 ID를 먼저 확인하세요.")
        # No start call, no retries: uncertain writes must be checked in status.
        return self.request("/api/tasks", {k: data[k] for k in required}, state["csrf"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="연결된 PC의 하네스 작업 등록·상태 조회")
    parser.add_argument("--port", type=int, default=8765)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    register = sub.add_parser("register")
    register.add_argument("file", type=Path, help="UTF-8 JSON 작업 지시 파일")
    args = parser.parse_args(argv)
    try:
        bridge = Bridge(args.port)
        if args.command == "register" and args.file.stat().st_size > 65536:
            raise ValueError("작업 파일은 64KiB 이하로 작성하세요.")
        result = bridge.status() if args.command == "status" else bridge.register(
            json.loads(args.file.read_text(encoding="utf-8-sig"))
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, HTTPError, URLError) as exc:
        print(f"처리 실패: {exc}. 등록 요청이었다면 재시도 전에 상태를 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
