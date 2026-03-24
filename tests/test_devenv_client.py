import os
import urllib.error
import unittest
from unittest.mock import MagicMock, patch

from github_pm_agent.devenv_client import DevEnvClient, DevEnvError


def _mock_response(body: bytes | str, status: int = 200):
    if isinstance(body, str):
        body = body.encode()
    m = MagicMock()
    m.__enter__ = lambda self: self
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = body
    m.status = status
    return m


class DevEnvClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = DevEnvClient("http://devenv.test")

    def test_health_returns_true(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"status": "ok"}')):
            self.assertTrue(self.client.health())

    def test_health_returns_false(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"status": "degraded"}')):
            self.assertFalse(self.client.health())

    def test_create_workspace(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"name": "ws1"}')):
            self.assertEqual(self.client.create_workspace("ws1"), {"name": "ws1"})

    def test_delete_workspace_no_exception(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response(b"", 204)):
            self.client.delete_workspace("ws1")

    def test_upload_context_returns_id(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"id": "ctx-123"}')):
            self.assertEqual(self.client.upload_context(b"context"), "ctx-123")

    def test_upload_context_missing_id_raises(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"id": ""}')):
            with self.assertRaises(DevEnvError):
                self.client.upload_context(b"context")

    def test_build_image_returns_job_id(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"job_id": "j-abc"}')):
            self.assertEqual(self.client.build_image("ctx-1", "image:latest"), "j-abc")

    def test_run_container_returns_job_id(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('{"job_id": "j-xyz"}')):
            self.assertEqual(self.client.run_container("image:latest"), "j-xyz")

    def test_exec_in_job(self) -> None:
        payload = '{"exit_code": 0, "stdout": "ok", "stderr": ""}'
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = self.client.exec_in_job("j-1", ["echo", "ok"])

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "ok")
        self.assertEqual(result["stderr"], "")

    def test_get_logs(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response(b"line1\nline2")):
            self.assertEqual(self.client.get_logs("j-1"), "line1\nline2")

    def test_list_jobs(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response('[{"job_id": "j-1"}]')):
            result = self.client.list_jobs()

        self.assertIsInstance(result, list)
        self.assertEqual(result, [{"job_id": "j-1"}])

    def test_inspect_job(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_mock_response('{"job_id": "j-1", "status": "running"}'),
        ):
            result = self.client.inspect_job("j-1")

        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "running")

    def test_stop_job_no_exception(self) -> None:
        with patch("urllib.request.urlopen", return_value=_mock_response(b"", 200)):
            self.client.stop_job("j-1")

    def test_http_error_raises_devenv_error(self) -> None:
        error = urllib.error.HTTPError("http://devenv.test/health", 500, "err", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(DevEnvError):
                self.client.health()

    def test_url_error_raises_devenv_error(self) -> None:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaises(DevEnvError):
                self.client.health()

    def test_wait_for_job_done(self) -> None:
        with patch.object(self.client, "inspect_job", return_value={"status": "done"}):
            result = self.client.wait_for_job("j-1", poll_interval=0, timeout=1)

        self.assertEqual(result, {"status": "done"})

    def test_wait_for_job_timeout(self) -> None:
        with patch.object(self.client, "inspect_job", return_value={"status": "running"}), patch(
            "github_pm_agent.devenv_client.time.sleep"
        ), patch(
            "github_pm_agent.devenv_client.time.monotonic",
            side_effect=[0.0, 0.0, 0.002],
        ):
            with self.assertRaises(DevEnvError):
                self.client.wait_for_job("j-1", poll_interval=0, timeout=0.001)

    def test_wait_for_job_failed_raises(self) -> None:
        with patch.object(self.client, "inspect_job", return_value={"status": "failed", "error": "oom"}):
            with self.assertRaisesRegex(DevEnvError, "failed"):
                self.client.wait_for_job("j-1", poll_interval=0, timeout=1)

    def test_server_url_from_env(self) -> None:
        with patch.dict(os.environ, {"DEVENV_SERVER": "http://custom:9999"}):
            client = DevEnvClient("")

        self.assertEqual(client.server_url, "http://custom:9999")


if __name__ == "__main__":
    unittest.main()
