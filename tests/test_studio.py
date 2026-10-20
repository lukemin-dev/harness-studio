import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from harness.core import Store, Manager, Problem, Halt, git, parse_review, changed_files
from harness.app import create_demo, make_handler


class ScriptedManager(Manager):
    """Substitute only the AI process; real Git, files, tests and gates still run."""
    behavior = "pass"
    calls = 0

    def command(self, run, argv, cwd, deadline, input_text=None):
        if argv[0] != "test-codex":
            return super().command(run, argv, cwd, deadline, input_text)
        root = Path(cwd)
        if "--output-schema" in argv:
            if self.behavior == "review_mutation":
                (root / "calculator.py").write_text("def add(a,b): return 0\n")
            output = Path(argv[argv.index("-o") + 1])
            output.write_text(json.dumps({"passed": True, "summary": "Reviewed", "findings": []}))
        else:
            self.calls += 1
            target = root / ("forbidden.py" if self.behavior == "scope" else "calculator.py")
            value = "0" if self.behavior == "fail" or self.behavior == "retry" and self.calls == 1 else "a + b"
            target.write_text(f'def add(a, b):\n    """Add two values."""\n    return {value}\n')
        return 0


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = ScriptedManager(Store(Path(self.temp.name) / "data"), codex="test-codex")
        self.sample = create_demo(self.manager)

    def tearDown(self):
        if self.manager.active:
            self.manager.stop(self.manager.active)
        if self.manager.thread:
            self.manager.thread.join(10)
        self.temp.cleanup()

    def run_task(self):
        run = self.manager.start(self.sample["task"]["id"])
        self.manager.thread.join(20)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.store.one("runs", run["id"])

    def test_real_worktree_tests_review_and_original_unchanged(self):
        original = Path(self.sample["project"]["path"]) / "calculator.py"
        before = original.read_bytes()
        run = self.run_task()
        self.assertEqual(run["status"], "verified", run["summary"])
        self.assertEqual(run["result"]["checks"][0]["exit_code"], 0)
        self.assertFalse(run["result"]["db_verified"])
        self.assertEqual(original.read_bytes(), before)
        self.assertIn("docstring", self.manager.report(run["id"]))
        self.assertIn("Add two values", self.manager.diff(run["id"]))

    def test_scope_violation_blocks_completion(self):
        self.manager.behavior = "scope"
        run = self.run_task()
        self.assertEqual(run["status"], "blocked")
        self.assertIn("forbidden.py", run["summary"])

    def test_failed_tests_retry_bounded(self):
        self.manager.behavior = "fail"
        run = self.run_task()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["attempt"], 2)
        self.assertEqual(self.manager.calls, 2)
        self.assertIsNone(run["result"]["review"])

    def test_retry_can_repair_failure(self):
        self.manager.behavior = "retry"
        run = self.run_task()
        self.assertEqual(run["status"], "verified", run["summary"])
        self.assertEqual(run["attempt"], 2)

    def test_reviewer_cannot_change_candidate(self):
        self.manager.behavior = "review_mutation"
        run = self.run_task()
        self.assertEqual(run["status"], "blocked")
        self.assertIn("검수 중", run["summary"])

    def test_dirty_source_rejected(self):
        (Path(self.sample["project"]["path"]) / "new.txt").write_text("pending")
        with self.assertRaises(Problem):
            self.manager.start(self.sample["task"]["id"])

    def test_demo_cancellation_and_single_run_gate(self):
        run = self.manager.start(self.sample["task"]["id"], "demo")
        with self.assertRaises(Problem):
            self.manager.start(self.sample["task"]["id"], "demo")
        self.manager.stop(run["id"])
        self.manager.thread.join(5)
        self.assertEqual(self.manager.store.one("runs", run["id"])["status"], "cancelled")
        self.assertIsNone(self.manager.active)

    def test_restart_marks_incomplete_run(self):
        run = self.manager.start(self.sample["task"]["id"], "demo")
        self.manager.stop(run["id"])
        self.manager.thread.join(5)
        self.manager.store.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
        self.manager.store.recover()
        self.assertEqual(self.manager.store.one("runs", run["id"])["status"], "interrupted")

    def test_filename_whitespace_preserved(self):
        repo = Path(self.sample["project"]["path"])
        (repo / " leading.txt").write_text("data")
        self.assertIn(" leading.txt", changed_files(repo))

    def test_conflicting_review_rejected(self):
        f = Path(self.temp.name) / "review.json"
        f.write_text(json.dumps({"passed": True, "summary": "", "findings": ["error"]}))
        with self.assertRaises(Halt):
            parse_review(f)

    def test_child_timeout_including_blocked_stdin(self):
        start = time.monotonic()
        with self.assertRaises(Halt):
            self.manager.command("timeout", [sys.executable, "-c", "import time; time.sleep(30)"],
                                 self.sample["project"]["path"], time.monotonic() + .5,
                                 "x" * 500000)
        self.assertLess(time.monotonic() - start, 8)

    def test_codex_credit_failure_is_explained(self):
        code = 'import json; print(json.dumps({"type":"turn.failed","error":{"message":"Your workspace is out of credits."}})); raise SystemExit(1)'
        result = self.manager.command("credit", [sys.executable, "-c", code],
                                      self.sample["project"]["path"], time.monotonic() + 10)
        self.assertEqual(result, 1)
        self.assertIn("크레딧이 부족", self.manager.codex_failure("수정", result))

    def test_http_origin_csrf_and_static(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.manager))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/state") as response:
                state = json.load(response)
            for headers in ({}, {"X-CSRF-Token": state["csrf"], "Origin": "https://untrusted.example"}):
                req = urllib.request.Request(base + "/api/tasks", data=b"{}", headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 403)
            payload = json.dumps({"project_id": self.sample["project"]["id"], "title": "HTTP",
                                  "prompt": "Document add", "acceptance": "Tests pass"}).encode()
            req = urllib.request.Request(base + "/api/tasks", data=payload,
                                         headers={"X-CSRF-Token": state["csrf"]})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(json.load(response)["title"], "HTTP")
            with urllib.request.urlopen(base + "/") as response:
                self.assertIn(b"Harness Studio", response.read())
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
