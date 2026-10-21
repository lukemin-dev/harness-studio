import unittest
from unittest.mock import Mock

from harness.bridge import Bridge


class BridgeTests(unittest.TestCase):
    def test_status_does_not_disclose_csrf(self):
        bridge = Bridge()
        bridge.request = Mock(return_value=dict(
            csrf="secret", projects=[], tasks=[], runs=[], active=None))
        self.assertNotIn("csrf", bridge.status())

    def test_register_never_starts_and_only_sends_task_fields(self):
        bridge = Bridge()
        bridge.request = Mock(side_effect=[
            {"csrf": "token", "projects": [{"id": "p"}]},
            {"id": "t", "status": "todo"},
        ])
        data = dict(project_id="p", title="분석", prompt="확인", acceptance="보고", mode="codex")
        self.assertEqual(bridge.register(data)["status"], "todo")
        self.assertEqual([c.args[0] for c in bridge.request.call_args_list],
                         ["/api/state", "/api/tasks"])
        self.assertNotIn("mode", bridge.request.call_args_list[1].args[1])

    def test_unknown_project_and_missing_fields_do_not_write(self):
        bridge = Bridge()
        bridge.request = Mock(return_value={"csrf": "token", "projects": []})
        with self.assertRaises(ValueError):
            bridge.register({})
        bridge.request.assert_not_called()
        with self.assertRaises(ValueError):
            bridge.register(dict(project_id="p", title="t", prompt="p", acceptance="a"))
        self.assertEqual(bridge.request.call_count, 1)

    def test_failed_registration_is_not_retried(self):
        bridge = Bridge()
        bridge.request = Mock(side_effect=[
            {"csrf": "token", "projects": [{"id": "p"}]}, TimeoutError("timeout")])
        with self.assertRaises(TimeoutError):
            bridge.register(dict(project_id="p", title="t", prompt="p", acceptance="a"))
        self.assertEqual(bridge.request.call_count, 2)
