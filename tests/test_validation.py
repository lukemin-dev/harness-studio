import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core import validate_config, allowed_path, parse_review, Problem, Halt
from harness.bridge import Bridge


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = dict(path=str(self.root), name="Example", allowed=["src/*.xml"])
        self.git = patch("harness.core.git", side_effect=lambda p, *a: str(self.root) if "--show-toplevel" in a else "abc")
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_reject_invalid_config_matrix(self):
        cases = [
            ("path", ""), ("path", None), ("name", None), ("name", []),
            ("allowed", [" "]), ("allowed", ["a\0"]), ("allowed", ["../a"]),
            ("allowed", [".git/config"]), ("allowed", [".harness/x"]),
            ("allowed", ["a"] * 201), ("allowed", ["a" * 1001]),
            ("commands", [["x\0"]]), ("commands", [["x"] * 129]),
            ("commands", [["x" * 16001]]), ("commands", "python"),
            ("retries", True), ("timeout", 12.5), ("timeout", "12.5"),
            ("model", None), ("rules", {}), ("rules", "x" * 12001),
            ("rules", "\0"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=str(value)[:20]):
                with self.assertRaises(Problem):
                    validate_config({**self.data, key: value})

    def test_preserves_valid_config_and_deduplicates(self):
        _, _, config = validate_config({**self.data, "allowed": ["src/*.xml", "src/*.xml"],
                                        "timeout": "600", "retries": 0})
        self.assertEqual(config["allowed"], ["src/*.xml"])
        self.assertEqual(config["timeout"], 600)
        self.assertFalse(allowed_path(".git/config", ["*"]))
        self.assertFalse(allowed_path(".harness/secret", ["*"]))
        self.assertTrue(allowed_path("src/a.xml", ["src/*.xml"]))

    def test_review_rejects_extra_keys_and_empty_summary(self):
        file = self.root / "review.json"
        for data in (dict(passed=True, summary="", findings=[]),
                     dict(passed=True, summary="OK", findings=[], extra=1)):
            file.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(Halt):
                parse_review(file)

    def test_bridge_rejects_large_task_before_network(self):
        bridge = Bridge()
        with patch.object(bridge, "request") as request:
            with self.assertRaises(ValueError):
                bridge.register(dict(project_id="p", title="x"*201, prompt="p", acceptance="a"))
            request.assert_not_called()
