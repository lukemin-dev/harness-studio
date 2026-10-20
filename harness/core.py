"""Persistence, isolated execution, review gates, and cancellation.

Only explicitly registered local repositories and argv test commands run here.
This application is a local operator tool, not a multi-tenant execution service.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


class Problem(ValueError):
    pass


class Halt(Exception):
    pass


class Cancelled(Halt):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "studio.sqlite3"
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
              config TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
              prompt TEXT NOT NULL, acceptance TEXT NOT NULL, status TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL,
              mode TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
              workspace TEXT, base TEXT, summary TEXT, result TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              kind TEXT NOT NULL, message TEXT NOT NULL, created TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def rows(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, table, key):
        if table not in {"tasks", "runs", "projects"}:
            raise Problem("잘못된 데이터 종류입니다.")
        rows = self.rows(f"SELECT * FROM {table} WHERE id=?", (key,))
        if not rows:
            raise Problem("항목을 찾을 수 없습니다.")
        item = rows[0]
        for field in ("config", "result"):
            if field in item:
                item[field] = json.loads(item[field])
        return item

    def execute(self, sql, args=()):
        with self.connect() as c:
            c.execute(sql, args)

    def event(self, run, kind, message):
        # Large output is bounded, not presented as a complete transcript.
        self.execute("INSERT INTO events(run_id,kind,message,created) VALUES(?,?,?,?)",
                     (run, kind, str(message)[:12000], now()))

    def state(self):
        projects = self.rows("SELECT * FROM projects ORDER BY created DESC")
        for p in projects:
            p["config"] = json.loads(p["config"])
        return {
            "projects": projects,
            "tasks": self.rows("SELECT * FROM tasks ORDER BY created DESC"),
            "runs": self.rows("SELECT id,task_id,status,mode,stage,attempt,workspace,summary,created,updated FROM runs ORDER BY created DESC LIMIT 100"),
        }

    def recover(self):
        with self.connect() as c:
            c.execute("""UPDATE tasks SET status='interrupted', updated=?
                WHERE id IN (SELECT task_id FROM runs WHERE status='running')""", (now(),))
            c.execute("""UPDATE runs SET status='interrupted',stage='interrupted',
                summary='관리 프로그램이 종료되어 중단되었습니다. 자동 재실행하지 않습니다.',updated=?
                WHERE status='running'""", (now(),))


def git(path, *args):
    p = subprocess.run(["git", "-C", str(path), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode:
        raise Problem(p.stderr.strip()[:2000] or "Git 작업에 실패했습니다.")
    return p.stdout if "-z" in args else p.stdout.rstrip("\r\n")


def validate_config(data):
    path = Path(str(data.get("path", ""))).expanduser().resolve()
    if not path.is_dir():
        raise Problem("실제 Git 저장소 폴더를 입력해 주세요.")
    top = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    if top != path:
        raise Problem(f"저장소 최상위 폴더를 지정해 주세요: {top}")
    git(path, "rev-parse", "HEAD")
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 100:
        raise Problem("프로젝트 이름은 1~100자로 입력해 주세요.")
    allowed = data.get("allowed", [])
    if not isinstance(allowed, list) or not allowed or any(
        not isinstance(x, str) or not x or x.startswith(("/", "\\")) or
        ".." in x.split("/") or "\\" in x or ":" in x for x in allowed
    ):
        raise Problem("수정 허용 파일을 저장소 기준 상대 경로 패턴으로 입력해 주세요.")
    commands = data.get("commands", [])
    if not isinstance(commands, list) or len(commands) > 12:
        raise Problem("검사 명령은 최대 12개입니다.")
    for cmd in commands:
        if not isinstance(cmd, list) or not cmd or any(not isinstance(x, str) or not x for x in cmd):
            raise Problem('검사 명령은 [["python", "-m", "unittest"]] 형태입니다.')
    try:
        retries, timeout = int(data.get("retries", 1)), int(data.get("timeout", 900))
    except (TypeError, ValueError):
        raise Problem("재시도·제한시간은 정수로 입력해 주세요.")
    if not 0 <= retries <= 3 or not 10 <= timeout <= 7200:
        raise Problem("재시도는 0~3회, 전체 제한시간은 10~7200초입니다.")
    model = str(data.get("model", "")).strip()
    if len(model) > 100 or any(c.isspace() for c in model):
        raise Problem("모델 이름은 공백 없이 100자 이하로 입력해 주세요.")
    return name, str(path), {
        "allowed": allowed, "commands": commands, "retries": retries, "timeout": timeout,
        "rules": str(data.get("rules", ""))[:12000], "model": model,
    }


def allowed_path(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def changed_files(workspace):
    # Includes tracked, staged, deleted and untracked files, with NUL-safe paths.
    tracked = git(workspace, "diff", "--name-only", "-z", "HEAD")
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(set(x for x in (tracked + "\0" + untracked).split("\0") if x))


def fingerprint(workspace):
    result = {}
    for name in changed_files(workspace):
        path = Path(workspace) / name
        if path.is_symlink():
            result[name] = "link:" + os.readlink(path)
        elif path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as f:
                for part in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(part)
            result[name] = h.hexdigest()
        else:
            result[name] = "<deleted>"
    return result


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "summary", "findings"], "additionalProperties": False,
}


def parse_review(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise Halt("검수 결과 JSON을 읽을 수 없습니다.") from e
    if (not isinstance(result, dict) or type(result.get("passed")) is not bool or
        not isinstance(result.get("summary"), str) or
        not isinstance(result.get("findings"), list) or
        any(not isinstance(x, str) for x in result["findings"])):
        raise Halt("검수 결과 형식이 잘못되어 완료 처리하지 않습니다.")
    if result["passed"] and result["findings"]:
        raise Halt("검수 통과 표시와 지적 사항이 충돌합니다.")
    return result


class Manager:
    def __init__(self, store, codex=None):
        self.store = store
        self.codex = codex or shutil.which("codex")
        self.lock = threading.Lock()
        self.active = None
        self.stop_flag = threading.Event()
        self.process = None
        self.command_error = ""
        self.thread = None
        self.store.recover()

    def project(self, data):
        name, path, config = validate_config(data)
        key = ident()
        try:
            self.store.execute("INSERT INTO projects VALUES(?,?,?,?,?)",
                               (key, name, path, json.dumps(config, ensure_ascii=False), now()))
        except sqlite3.IntegrityError:
            raise Problem("이미 등록된 저장소입니다.")
        return self.store.one("projects", key)

    def task(self, data):
        project = self.store.one("projects", data.get("project_id"))
        title, prompt = str(data.get("title", "")).strip(), str(data.get("prompt", "")).strip()
        acceptance = str(data.get("acceptance", "")).strip()
        if not title or not prompt or not acceptance or len(title) > 200 or len(prompt) > 20000 or len(acceptance) > 12000:
            raise Problem("제목·작업 지시·완료 기준을 입력해 주세요. 입력 길이도 확인해 주세요.")
        key = ident()
        self.store.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
                           (key, project["id"], title, prompt, acceptance, "todo", now(), now()))
        return self.store.one("tasks", key)

    def start(self, task_id, mode="codex"):
        if mode not in {"codex", "demo"}:
            raise Problem("실행 모드가 올바르지 않습니다.")
        with self.lock:
            if self.active:
                raise Problem("다른 작업이 실행 중입니다. 종료 후 실행해 주세요.")
            task = self.store.one("tasks", task_id)
            project = self.store.one("projects", task["project_id"])
            if mode == "codex" and not self.codex:
                raise Problem("Codex CLI를 찾지 못했습니다. 설치 후 다시 실행해 주세요.")
            if mode == "codex" and git(project["path"], "status", "--porcelain"):
                raise Problem("원본 저장소에 미커밋 변경이 있습니다. 먼저 보존·정리해 주세요. 실행은 HEAD 커밋을 기준으로 합니다.")
            key = ident()
            self.store.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (key, task_id, "running", mode, "queued", 0, None, None, "",
                                "{}", now(), now()))
            self.store.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (now(), task_id))
            self.active = key
            self.stop_flag.clear()
            self.thread = threading.Thread(target=self._run, args=(key, task, project, mode), daemon=True)
            self.thread.start()
            return self.store.one("runs", key)

    def stop(self, run_id):
        with self.lock:
            if self.active != run_id:
                raise Problem("실행 중인 작업이 아닙니다.")
            self.stop_flag.set()
            process = self.process
        if process:
            self._kill(process)
        self.store.event(run_id, "stop", "사용자가 중단을 요청했습니다.")

    @staticmethod
    def _kill(p):
        if p.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def check(self, deadline):
        if self.stop_flag.is_set():
            raise Cancelled("사용자 요청으로 중단했습니다.")
        if time.monotonic() > deadline:
            raise Halt("전체 실행 제한시간을 초과했습니다.")

    def stage(self, run, stage, message):
        self.store.execute("UPDATE runs SET stage=?,updated=? WHERE id=?", (stage, now(), run))
        self.store.event(run, stage, message)

    def command(self, run, argv, cwd, deadline, input_text=None):
        self.check(deadline)
        self.command_error = ""
        # No shell interpolation; no elevated or bypass flags.
        flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **flags)
        with self.lock:
            self.process = p
        lines = queue.Queue()
        def read():
            try:
                while True:
                    raw = p.stdout.readline(16384)
                    if not raw:
                        break
                    lines.put(raw.decode("utf-8", errors="replace"))
            finally:
                lines.put(None)
        threading.Thread(target=read, daemon=True).start()
        def write_input():
            try:
                if input_text:
                    p.stdin.write(input_text.encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass
            finally:
                p.stdin.close()
        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        try:
            ended = False
            count = 0
            while not ended or p.poll() is None:
                self.check(deadline)
                try:
                    line = lines.get(timeout=.15)
                    if line is None:
                        ended = True
                    else:
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict) and entry.get("type") in {"error", "turn.failed"}:
                                detail = entry.get("error", {})
                                message = entry.get("message") or (detail.get("message") if isinstance(detail, dict) else detail)
                                if message:
                                    self.command_error = str(message)[:1500]
                        except ValueError:
                            pass
                        count += 1
                        if count <= 1500:
                            self.store.event(run, "output", line.rstrip())
                        elif count == 1501:
                            self.store.event(run, "output", "출력 한도를 초과하여 이후 로그를 생략합니다.")
                except queue.Empty:
                    pass
            self.check(deadline)
            return p.wait()
        finally:
            self._kill(p)
            p.wait(timeout=15)
            writer.join(timeout=2)
            with self.lock:
                self.process = None
            if p.stdout:
                p.stdout.close()

    def _run(self, run, task, project, mode):
        deadline = time.monotonic() + project["config"]["timeout"]
        result = {"checks": [], "review": None, "changed_files": [],
                  "db_verified": False, "ui_verified": False}
        status, summary = "failed", ""
        try:
            if mode == "demo":
                # Exercises lifecycle without touching the registered source or calling AI.
                for stage in ("preflight", "worker", "tests", "reviewer"):
                    self.stage(run, stage, "데모: " + stage + " 흐름 시뮬레이션")
                    for _ in range(8):
                        self.check(deadline)
                        time.sleep(.1)
                result["simulation"] = True
                status, summary = "demo_done", "시뮬레이션 완료. 실제 코드·AI·DB 테스트는 실행하지 않았습니다."
            else:
                status, summary = self.real_run(run, task, project, deadline, result)
        except Cancelled as e:
            status, summary = "cancelled", str(e)
        except (Problem, Halt, OSError, subprocess.SubprocessError) as e:
            status, summary = "blocked", str(e)
        except Exception as e:
            status, summary = "failed", f"{type(e).__name__}: {e}"
        finally:
            result["summary"] = summary
            self.store.execute("UPDATE runs SET status=?,stage=?,summary=?,result=?,updated=? WHERE id=?",
                               (status, status, summary, json.dumps(result, ensure_ascii=False), now(), run))
            self.store.execute("UPDATE tasks SET status=?,updated=? WHERE id=?", (status, now(), task["id"]))
            self.store.event(run, status, summary)
            with self.lock:
                self.active = None

    def real_run(self, run, task, project, deadline, result):
        config = project["config"]
        model_args = ["--model", config["model"]] if config.get("model") else []
        self.stage(run, "preflight", "저장소 상태와 기준 커밋 확인")
        base = git(project["path"], "rev-parse", "HEAD")
        workspace = self.store.root / "workspaces" / run
        workspace.parent.mkdir(exist_ok=True)
        if self.command(run, ["git", "-C", project["path"], "worktree", "add", "--detach", str(workspace), base],
                        project["path"], deadline):
            raise Halt("별도 작업 폴더를 만들지 못했습니다.")
        self.store.execute("UPDATE runs SET workspace=?,base=? WHERE id=?", (str(workspace), base, run))
        artifact = self.store.root / "runs" / run
        artifact.mkdir(parents=True)
        schema = artifact / "review-schema.json"
        schema.write_text(json.dumps(REVIEW_SCHEMA), encoding="utf-8")
        feedback = ""
        for attempt in range(config["retries"] + 1):
            self.check(deadline)
            self.store.execute("UPDATE runs SET attempt=? WHERE id=?", (attempt + 1, run))
            self.stage(run, "worker", f"수정 담당 실행 ({attempt + 1}/{config['retries'] + 1})")
            prompt = (
                f"작업: {task['title']}\n{task['prompt']}\n완료 기준:\n{task['acceptance']}\n"
                f"프로젝트 규칙:\n{config['rules']}\n허용 파일 패턴: {config['allowed']}\n"
                f"등록된 검사 명령: {json.dumps(config['commands'], ensure_ascii=False)}\n"
                f"실행 가능한 Python: {sys.executable} (검사 명령의 {{python}}에 사용)\n"
                "기존 AGENTS.md를 따르세요. 허용 파일 외 수정 금지. Git commit/push/reset 금지. "
                "DB/외부 시스템에 쓰기 금지. 자격 증명을 출력하지 마세요. "
                "검사 규칙·테스트를 통과 목적으로 약화하지 마세요. 마지막에 변경과 미검증 항목을 보고하세요.\n"
                f"이전 검수 지적:\n{feedback}"
            )
            rc = self.command(run, [self.codex, "-a", "never", "exec", "--sandbox", "workspace-write",
                                   "-C", str(workspace), "--json", *model_args, "-"], workspace, deadline, prompt)
            if rc:
                raise Halt(self.codex_failure("수정", rc))
            self.guard(workspace, base, config["allowed"])
            before = fingerprint(workspace)
            result["changed_files"] = list(before)
            if not before:
                return "no_changes", "변경 파일이 없습니다. 작업 범위와 필요 여부를 확인해 주세요."
            self.stage(run, "tests", "등록된 검사 명령 실행")
            checks = []
            for argv in config["commands"]:
                resolved = [sys.executable if x == "{python}" else x for x in argv]
                code = self.command(run, resolved, workspace, deadline)
                checks.append({"command": argv, "exit_code": code})
            result["checks"] = checks
            # Tests cannot silently alter the candidate under review.
            self.guard(workspace, base, config["allowed"])
            if fingerprint(workspace) != before:
                raise Halt("검사 명령이 소스 파일을 변경했습니다. 변경 내역을 확인해 주세요.")
            if any(c["exit_code"] != 0 for c in checks):
                feedback = "검사 실패: " + json.dumps(checks, ensure_ascii=False)
                self.store.event(run, "retry", feedback)
                if attempt == config["retries"]:
                    return "failed", "검사가 실패했고 재시도 한도에 도달했습니다."
                continue
            self.stage(run, "reviewer", "별도 읽기 전용 검수 담당 실행")
            review_file = artifact / f"review-{attempt + 1}.json"
            review_prompt = (
                f"독립 검수 담당입니다. 기준 커밋 {base} 대비 현재 변경을 검수하세요. 파일 수정 금지.\n"
                f"작업:\n{task['prompt']}\n완료 기준:\n{task['acceptance']}\n규칙:\n{config['rules']}\n"
                f"수정 허용 패턴:{config['allowed']}\n검사 결과:{json.dumps(checks, ensure_ascii=False)}\n"
                f"실행 가능한 Python: {sys.executable}\n"
                "작업자의 완료 주장에 의존하지 말고 직접 diff와 관련 소스를 확인하세요. "
                "문법 오류, 동작 변경, 누락, 구조 변경, 검사 약화를 찾으세요. "
                "passed는 지적 사항이 없을 때만 true. 미실행 DB/화면 검증을 통과했다고 쓰지 마세요."
            )
            rc = self.command(run, [self.codex, "-a", "never", "exec", "--sandbox", "read-only",
                                   "-C", str(workspace), "--json", "--output-schema", str(schema),
                                   "-o", str(review_file), *model_args, "-"], workspace, deadline, review_prompt)
            if rc:
                raise Halt(self.codex_failure("검수", rc))
            self.guard(workspace, base, config["allowed"])
            if fingerprint(workspace) != before:
                raise Halt("검수 중 파일이 변경되어 결과를 인정하지 않습니다.")
            review = parse_review(review_file)
            result["review"] = review
            if review["passed"]:
                return ("verified" if checks else "reviewed",
                        "소스 검수와 등록된 검사 통과. DB·화면 검증 여부는 별도 확인입니다."
                        if checks else "소스 검수 통과. 검사 명령이 없어 실행 검증은 대기입니다.")
            feedback = json.dumps(review, ensure_ascii=False)
            self.store.event(run, "retry", feedback)
        return "failed", "독립 검수를 통과하지 못했고 재시도 한도에 도달했습니다."

    def codex_failure(self, role, code):
        error = self.command_error.lower()
        if "out of credits" in error:
            reason = "워크스페이스 크레딧이 부족합니다. 계정 사용량 또는 관리자 설정을 확인해 주세요."
        elif "not supported" in error and "model" in error:
            reason = "현재 계정에서 지원하지 않는 모델입니다. 프로젝트의 Codex 모델 설정을 확인해 주세요."
        elif "rate limit" in error or "usage limit" in error:
            reason = "계정 사용량 제한에 도달했습니다. 사용 가능 시점을 확인해 주세요."
        else:
            reason = "실행 로그에서 인증·사용량·실행 오류를 확인해 주세요."
        return f"Codex {role} 담당 종료 코드 {code}. {reason}"

    @staticmethod
    def guard(workspace, base, patterns):
        if git(workspace, "rev-parse", "HEAD") != base:
            raise Halt("작업 중 기준 커밋이 변경되었습니다. 자동 통합하지 않습니다.")
        invalid = [p for p in changed_files(workspace) if not allowed_path(p, patterns)]
        if invalid:
            raise Halt("허용 범위 밖의 파일 변경: " + ", ".join(invalid))

    def detail(self, key, after=0):
        run = self.store.one("runs", key)
        run["events"] = self.store.rows("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 300",
                                       (key, after))
        return run

    def diff(self, key):
        run = self.store.one("runs", key)
        if not run["workspace"]:
            return "작업 폴더가 없습니다. 데모는 소스를 변경하지 않습니다."
        text = git(run["workspace"], "diff", "--no-ext-diff", "--stat", "HEAD")
        text += "\n\n" + git(run["workspace"], "diff", "--no-ext-diff", "--no-color", "HEAD")
        untracked = git(run["workspace"], "ls-files", "--others", "--exclude-standard")
        return (text + "\n\n새 파일 목록 (내용은 작업 폴더에서 확인):\n" + untracked)[:200000]

    def report(self, key):
        r = self.store.one("runs", key)
        t = self.store.one("tasks", r["task_id"])
        return (f"# {t['title']}\n\n상태: {r['status']}\n모드: {r['mode']}\n"
                f"기준 커밋: {r['base'] or '-'}\n작업 폴더: {r['workspace'] or '-'}\n\n"
                f"{r['summary']}\n\n## 작업 지시\n{t['prompt']}\n\n## 완료 기준\n{t['acceptance']}\n\n"
                "## 검사 증거\n```json\n" + json.dumps(r["result"], ensure_ascii=False, indent=2) + "\n```\n")
