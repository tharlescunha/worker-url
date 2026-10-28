"""Out/2026: o worker informa no heartbeat o usuário do Windows em que está rodando."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime import services  # noqa: E402
from app.runtime.task_client import HEARTBEAT_PATH, TaskApiClient  # noqa: E402


class _FakeHttp:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict) -> dict:
        self.posts.append((path, payload))
        return {}


class HeartbeatOsUserTests(unittest.TestCase):
    def test_heartbeat_sends_os_user_only_when_known(self):
        http = _FakeHttp()
        api = TaskApiClient(client=http, runner_uuid="u", runner_token="t")

        api.heartbeat(ip="10.0.0.5", running_tasks=1, os_user="ALZ\svc.rpa")
        api.heartbeat(ip="10.0.0.5", running_tasks=1)

        self.assertEqual(http.posts[0][0], HEARTBEAT_PATH)
        self.assertEqual(http.posts[0][1]["os_user"], "ALZ\svc.rpa")
        self.assertNotIn("os_user", http.posts[1][1])

    def test_current_os_user_has_the_windows_user(self):
        user = services.current_os_user()
        self.assertTrue(user)
        self.assertNotIn("$", user)
