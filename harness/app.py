from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import webbrowser

from .core import Store, Manager, Problem, validate_config

STATIC = Path(__file__).parent / "static"


class InstanceLock:
    """OS-released lock: a second server must not recover an active run."""
    def __init__(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self.file = open(Path(root) / "instance.lock", "a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise Problem("같은 데이터 폴더를 사용하는 Harness Studio가 이미 실행 중입니다.")

    def close(self):
        self.file.close()


def make_handler(manager):
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "HarnessStudio/0.1"

        def log_message(self, *_):
            pass

        def reply(self, status, data, content_type="application/json; charset=utf-8", download=None):
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(raw)

        def trusted(self):
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_port}"
            if host != expected:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == "http://" + expected

        def do_GET(self):
            if not self.trusted():
                return self.reply(403, {"error": "로컬 주소에서 접속해 주세요."})
            try:
                u = urlparse(self.path)
                path = u.path
                if path == "/api/state":
                    data = manager.store.state()
                    data.update(csrf=csrf, active=manager.active,
                                system={"codex": bool(manager.codex), "git": bool(shutil.which("git")),
                                        "python": sys.version.split()[0], "version": "0.1.0"})
                    return self.reply(200, data)
                if path.startswith("/api/runs/"):
                    parts = path.strip("/").split("/")
                    key = parts[2]
                    if len(parts) == 4 and parts[3] == "report":
                        return self.reply(200, manager.report(key).encode("utf-8"),
                                          "text/markdown; charset=utf-8", f"run-{key[:8]}.md")
                    if len(parts) == 4 and parts[3] == "diff":
                        return self.reply(200, {"diff": manager.diff(key)})
                    after = int(parse_qs(u.query).get("after", ["0"])[0])
                    if after < 0:
                        raise Problem("로그 위치는 0 이상이어야 합니다.")
                    return self.reply(200, manager.detail(key, after))
                names = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if path not in names:
                    return self.reply(404, {"error": "페이지를 찾을 수 없습니다."})
                name = names[path]
                types = {"html": "text/html", "css": "text/css", "js": "text/javascript"}
                return self.reply(200, (STATIC / name).read_bytes(), types[name.split(".")[-1]] + "; charset=utf-8")
            except (Problem, ValueError) as e:
                self.reply(400, {"error": str(e)})
            except Exception:
                self.reply(500, {"error": "요청 처리에 실패했습니다. 로컬 데이터와 실행 상태를 확인해 주세요."})

        def do_POST(self):
            if not self.trusted() or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf):
                return self.reply(403, {"error": "페이지를 새로 고친 후 다시 시도해 주세요."})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise Problem("요청 크기가 올바르지 않습니다.")
                if self.headers.get_content_type() != "application/json":
                    raise Problem("JSON 형식으로 요청해 주세요.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise Problem("객체 형태의 요청이 필요합니다.")
                path = urlparse(self.path).path
                if path == "/api/projects":
                    result = manager.project(data)
                elif path.startswith("/api/projects/"):
                    key = path.split("/")[-1]
                    manager.store.one("projects", key)
                    with manager.lock:
                        if manager.active:
                            raise Problem("실행 중에는 프로젝트 설정을 바꿀 수 없습니다.")
                        name, repo, config = validate_config(data)
                        manager.store.execute("UPDATE projects SET name=?,path=?,config=? WHERE id=?",
                                              (name, repo, json.dumps(config, ensure_ascii=False), key))
                    result = {"updated": True}
                elif path == "/api/tasks":
                    result = manager.task(data)
                elif path == "/api/start":
                    result = manager.start(data.get("task_id"), data.get("mode", "codex"))
                elif path == "/api/stop":
                    manager.stop(data.get("run_id"))
                    result = {"stopping": True}
                elif path == "/api/demo":
                    result = create_demo(manager)
                else:
                    return self.reply(404, {"error": "지원하지 않는 요청입니다."})
                self.reply(200, result)
            except sqlite3.IntegrityError:
                self.reply(400, {"error": "이미 등록된 저장소입니다. 기존 프로젝트를 확인하세요."})
            except (Problem, ValueError, TypeError) as e:
                self.reply(400, {"error": str(e)})
            except Exception:
                self.reply(500, {"error": "저장 또는 실행에 실패했습니다. 입력과 로컬 상태를 확인해 주세요."})

    return Handler


def create_demo(manager):
    with manager.lock:
        if manager.active:
            raise Problem("실행 중인 작업이 끝난 뒤 데모를 준비해 주세요.")
        repo = manager.store.root / "sample-repository"
        if not repo.exists():
            repo.mkdir()
            (repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            (repo / "test_calculator.py").write_text(
                "import unittest\nfrom calculator import add\n\nclass TestAdd(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
            (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            for argv in (["git", "init"], ["git", "add", "."],
                         ["git", "-c", "user.name=Harness Demo", "-c", "user.email=demo@localhost",
                          "commit", "-m", "Initialize isolated sample"]):
                subprocess.run(argv, cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        existing = manager.store.rows("SELECT id FROM projects WHERE path=?", (str(repo),))
        project = manager.store.one("projects", existing[0]["id"]) if existing else manager.project({
            "name": "시작하기 · 샘플 프로젝트", "path": str(repo), "allowed": ["calculator.py"],
            "commands": [["{python}", "-m", "unittest", "-v"]], "retries": 1, "timeout": 600,
            "rules": "함수 이름과 인자를 유지합니다. 테스트 파일을 변경하지 않습니다.",
        })
        task = manager.task({"project_id": project["id"], "title": "샘플 흐름 확인",
                             "prompt": "calculator.py의 add 함수에 간단한 설명 docstring을 추가하세요.",
                             "acceptance": "add(2, 3)의 결과는 5여야 합니다. 기존 테스트를 통과해야 합니다."})
        return {"project": project, "task": task}


def main():
    parser = argparse.ArgumentParser(description="Harness Studio local manager")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=str(Path.cwd() / ".harness"))
    parser.add_argument("--open", action="store_true", help="Open the local browser")
    args = parser.parse_args()
    lock = InstanceLock(args.data)
    manager = Manager(Store(args.data))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(manager))
    server.daemon_threads = True
    print(f"Harness Studio: http://127.0.0.1:{server.server_port}", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if manager.active:
            manager.stop(manager.active)
        if manager.thread:
            manager.thread.join(timeout=20)
        server.server_close()
        lock.close()


if __name__ == "__main__":
    main()
