import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from github_pm_agent.coding_session import CodingPlan, CodingSession, TestResult


def _completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class CodingSessionTest(unittest.TestCase):
    def _make_session(self):
        client = MagicMock()
        session = CodingSession(client, repo="acme/widgets", issue_number=42)
        session.work_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(session.work_dir), True)
        (session.work_dir / ".git").mkdir()
        return session, client

    def _make_plan(
        self,
        *,
        files: list[dict[str, str]] | None = None,
        test_command: str = "pytest -q",
        install_command: str = "pip install -r requirements.txt",
        branch_name: str = "feature/test-plan",
        commit_message: str = "Apply coding plan",
    ) -> CodingPlan:
        return CodingPlan(
            files=files or [],
            test_command=test_command,
            install_command=install_command,
            branch_name=branch_name,
            commit_message=commit_message,
        )

    def _apply_plan_run_command(self, command, *, cwd=None, env=None, check=True):
        if command == ["git", "branch", "--show-current"]:
            return _completed(stdout="main\n")
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return _completed(returncode=1)
        if command[:3] == ["git", "checkout", "-b"]:
            return _completed()
        if command == ["git", "add", "-A"]:
            return _completed()
        if command == ["git", "diff", "--cached", "--quiet"]:
            return _completed(returncode=1)
        if command[:2] == ["git", "commit"]:
            return _completed()
        raise AssertionError(f"unexpected command: {command!r}")

    def test_parse_plan_valid_json(self) -> None:
        payload = {
            "files": [{"path": "src/app.py", "content": "print('ok')\n"}],
            "test_command": "pytest tests/ -q",
            "install_command": "pip install -r requirements.txt",
            "branch_name": "feature/valid-json",
            "commit_message": "Add tests",
        }
        plan = CodingSession.parse_plan(f"before\n```json\n{json.dumps(payload)}\n```\nafter")

        self.assertIsInstance(plan, CodingPlan)
        assert plan is not None
        self.assertEqual(plan.files, payload["files"])
        self.assertEqual(plan.test_command, "pytest tests/ -q")
        self.assertEqual(plan.install_command, "pip install -r requirements.txt")
        self.assertEqual(plan.branch_name, "feature/valid-json")
        self.assertEqual(plan.commit_message, "Add tests")

    def test_parse_plan_raw_json(self) -> None:
        payload = {
            "files": [{"path": "README.md", "content": "hello\n"}],
            "test_command": "pytest -q",
            "install_command": "pip install -r requirements.txt",
            "branch_name": "feature/raw-json",
            "commit_message": "Update readme",
        }

        plan = CodingSession.parse_plan(json.dumps(payload))

        self.assertIsInstance(plan, CodingPlan)
        assert plan is not None
        self.assertEqual(plan.branch_name, "feature/raw-json")

    def test_parse_plan_missing_files_returns_none(self) -> None:
        payload = {
            "test_command": "pytest -q",
            "install_command": "pip install -r requirements.txt",
            "branch_name": "feature/missing-files",
            "commit_message": "No files",
        }

        self.assertIsNone(CodingSession.parse_plan(json.dumps(payload)))

    def test_parse_plan_empty_test_command_returns_none(self) -> None:
        payload = {
            "files": [{"path": "src/app.py", "content": "print('ok')\n"}],
            "test_command": "",
            "install_command": "pip install -r requirements.txt",
            "branch_name": "feature/empty-test",
            "commit_message": "Empty test command",
        }

        self.assertIsNone(CodingSession.parse_plan(json.dumps(payload)))

    def test_parse_plan_path_traversal_rejected(self) -> None:
        payload = {
            "files": [{"path": "../evil.py", "content": "print('x')\n"}],
            "test_command": "pytest -q",
            "install_command": "pip install -r requirements.txt",
            "branch_name": "feature/path-traversal",
            "commit_message": "Try path traversal",
        }

        plan = CodingSession.parse_plan(json.dumps(payload))

        self.assertIsInstance(plan, CodingPlan)
        assert plan is not None
        self.assertEqual(plan.files[0]["path"], "../evil.py")

    def test_apply_plan_writes_files(self) -> None:
        session, _client = self._make_session()
        plan = self._make_plan(
            files=[
                {"path": "src/module.py", "content": "value = 1\n"},
                {"path": "tests/test_module.py", "content": "assert True\n"},
            ]
        )

        with patch.object(session, "_run_command", side_effect=self._apply_plan_run_command):
            session.apply_plan(plan)

        self.assertTrue((session.work_dir / "src/module.py").exists())
        self.assertTrue((session.work_dir / "tests/test_module.py").exists())
        self.assertEqual((session.work_dir / "src/module.py").read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(
            (session.work_dir / "tests/test_module.py").read_text(encoding="utf-8"),
            "assert True\n",
        )

    def test_apply_plan_path_traversal_raises(self) -> None:
        session, _client = self._make_session()
        plan = self._make_plan(files=[{"path": "../../evil.py", "content": "x"}])

        with patch.object(session, "_run_command", side_effect=self._apply_plan_run_command):
            with self.assertRaisesRegex(RuntimeError, "outside repository"):
                session.apply_plan(plan)

    def test_apply_plan_exceeds_max_iterations(self) -> None:
        session, _client = self._make_session()
        session.MAX_ITERATIONS = 1
        plan = self._make_plan(files=[{"path": "src/module.py", "content": "value = 1\n"}])

        with patch.object(session, "_run_command", side_effect=self._apply_plan_run_command):
            session.apply_plan(plan)
            with self.assertRaisesRegex(RuntimeError, "max"):
                session.apply_plan(plan)

    def test_workspace_id_format(self) -> None:
        session = CodingSession(MagicMock(), repo="org/repo", issue_number=99)
        self.addCleanup(shutil.rmtree, str(session.work_dir), True)

        self.assertEqual(session.workspace_id, "issue-org-repo-99")

    def test_cleanup_removes_work_dir(self) -> None:
        session, client = self._make_session()
        (session.work_dir / "file.txt").write_text("data", encoding="utf-8")
        client.list_jobs.return_value = []
        client.delete_workspace.return_value = None

        session.cleanup()

        self.assertFalse(session.work_dir.exists())

    def test_cleanup_idempotent(self) -> None:
        session, client = self._make_session()
        client.list_jobs.return_value = []
        client.delete_workspace.return_value = None

        session.cleanup()
        session.cleanup()

        self.assertFalse(session.work_dir.exists())

    def test_run_tests_success(self) -> None:
        """Build-time test execution: passed when __TEST_EXIT_CODE__:0 found in build logs."""
        session, client = self._make_session()
        (session.work_dir / "README.md").write_text("test repo\n", encoding="utf-8")
        plan = self._make_plan(files=[])
        client.upload_context.return_value = "ctx-1"
        client.build_image.return_value = "j-build"
        client.wait_for_job.return_value = {"status": "done"}
        build_logs = (
            "Step 4/4 : RUN sh -c 'npm test'\n"
            " ---> Running in abc123\n"
            "1 passed, 0 failed\n"
            "__TEST_EXIT_CODE__:0\n"
            " ---> abc456\n"
            "Successfully built abc456\n"
        )
        client.get_logs.return_value = build_logs

        result = session.run_tests(plan)

        self.assertTrue(result.passed)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("1 passed, 0 failed", result.stdout)

    def test_run_tests_failure_returns_failed_result(self) -> None:
        """Build-time test execution: failed when __TEST_EXIT_CODE__:1 found in build logs."""
        session, client = self._make_session()
        (session.work_dir / "README.md").write_text("test repo\n", encoding="utf-8")
        plan = self._make_plan(files=[])
        client.upload_context.return_value = "ctx-1"
        client.build_image.return_value = "j-build"
        client.wait_for_job.return_value = {"status": "done"}
        build_logs = (
            "Step 4/4 : RUN sh -c 'npm test'\n"
            " ---> Running in abc123\n"
            "FAILED: 1 error\n"
            "__TEST_EXIT_CODE__:1\n"
        )
        client.get_logs.return_value = build_logs

        result = session.run_tests(plan)

        self.assertFalse(result.passed)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("FAILED: 1 error", result.stdout)

    def test_run_tests_success_with_buildkit_prefixed_logs(self) -> None:
        """BuildKit prefixes still allow parsing a passing sentinel and output."""
        session, client = self._make_session()
        (session.work_dir / "README.md").write_text("test repo\n", encoding="utf-8")
        plan = self._make_plan(files=[])
        client.upload_context.return_value = "ctx-1"
        client.build_image.return_value = "j-build"
        client.wait_for_job.return_value = {"status": "done"}
        build_logs = (
            "#9 [5/5] RUN sh -c 'npm test'; echo __TEST_EXIT_CODE__:$?\n"
            "#9 0.480 > test\n"
            "#9 1.603 ✓ tests/weather-api.test.js (2 tests) 7ms\n"
            "#9 2.697 Test Files  2 passed (2)\n"
            "#9 2.746 __TEST_EXIT_CODE__:0\n"
            "#9 DONE 2.8s\n"
        )
        client.get_logs.return_value = build_logs

        result = session.run_tests(plan)

        self.assertTrue(result.passed)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("2 passed", result.stdout)
        self.assertNotIn("__TEST_EXIT_CODE__:0", result.stdout)

    def test_setup_clones_with_auth_env_without_embedding_token_in_url(self) -> None:
        client = MagicMock()
        session = CodingSession(client, repo="acme/widgets", issue_number=42, github_token="secret-token")
        self.addCleanup(shutil.rmtree, str(session.work_dir), True)

        clone_calls = []

        def fake_run_command(command, *, cwd=None, env=None, check=True):
            clone_calls.append({"command": command, "cwd": cwd, "env": env, "check": check})
            if command[:2] == ["git", "clone"]:
                (session.work_dir / ".git").mkdir(parents=True, exist_ok=True)
            return _completed()

        with patch.object(session, "_run_command", side_effect=fake_run_command):
            session.setup()

        clone_call = clone_calls[0]
        self.assertEqual(
            clone_call["command"],
            ["git", "clone", "https://github.com/acme/widgets.git", str(session.work_dir)],
        )
        self.assertNotIn("secret-token", " ".join(clone_call["command"]))
        self.assertIsNotNone(clone_call["env"])
        self.assertEqual(clone_call["env"]["GIT_CONFIG_COUNT"], "1")
        self.assertIn("AUTHORIZATION: basic ", clone_call["env"]["GIT_CONFIG_VALUE_0"])


if __name__ == "__main__":
    unittest.main()
