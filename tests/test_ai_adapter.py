import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from github_pm_agent.ai_adapter import AIAdapterManager
from github_pm_agent.models import AiRequest
from github_pm_agent.prompt_library import PromptLibrary
from github_pm_agent.session_store import SessionStore


class AIAdapterRenderTest(unittest.TestCase):
    def test_render_request_includes_artifact_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "prompts").mkdir()
            (root / "prompts" / "system.md").write_text("system", encoding="utf-8")
            (root / "prompts" / "task.md").write_text("task ${artifacts}", encoding="utf-8")
            (root / "runtime" / "artifacts").mkdir(parents=True)
            (root / "runtime" / "artifacts" / "brief.md").write_text("artifact body", encoding="utf-8")

            manager = AIAdapterManager(
                root,
                {"ai": {"providers": {"fake": {"type": "shell", "command": ["echo", "{}"]}}}},
                PromptLibrary(root),
                SessionStore(root / "runtime"),
            )
            request = AiRequest(
                provider="fake",
                model="gpt-test",
                system_prompt_path="prompts/system.md",
                prompt_path="prompts/task.md",
                artifact_refs=["runtime/artifacts/brief.md"],
            )

            rendered = manager._render_request(request)

            self.assertIn("artifact body", rendered)
            self.assertIn("# Attached Artifacts", rendered)


def _make_adapter(root: Path, provider_config: dict) -> AIAdapterManager:
    return AIAdapterManager(
        root,
        {"ai": {"providers": {"devenv": provider_config}}},
        PromptLibrary(root),
        SessionStore(root / "runtime"),
    )


def _make_request(model: str = "gpt-5.4") -> AiRequest:
    return AiRequest(provider="devenv", model=model, system_prompt_path="", prompt_path="prompts/task.md")


class DevEnvCapsProviderTest(unittest.TestCase):
    def _mock_response(self, body: str, exit_code: int = 0) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.read.return_value = body.encode("utf-8")
        mock_resp.headers = {"X-Exit-Code": str(exit_code)}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _make_adapter_with_mock_render(self, provider_config: dict) -> AIAdapterManager:
        """Return adapter whose _render_request is stubbed to return a fixed prompt string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = _make_adapter(Path(tmpdir), provider_config)
        adapter._render_request = MagicMock(return_value="stubbed prompt")  # type: ignore[method-assign]
        return adapter

    def test_successful_call_returns_content(self) -> None:
        adapter = self._make_adapter_with_mock_render({
            "type": "devenv_caps",
            "capability": "codex",
            "caps_url_env": "TEST_CAPS_URL",
        })
        with patch.dict("os.environ", {"TEST_CAPS_URL": "http://caps.local:9000"}):
            with patch("urllib.request.urlopen", return_value=self._mock_response("hello world")) as mock_open:
                resp = adapter.generate(_make_request())
        self.assertEqual(resp.content, "hello world")
        called_url = mock_open.call_args[0][0].full_url
        self.assertIn("/codex", called_url)

    def test_model_arg_passed_as_query_param(self) -> None:
        adapter = self._make_adapter_with_mock_render({
            "type": "devenv_caps",
            "capability": "codex",
            "caps_url_env": "TEST_CAPS_URL",
            "model_arg": "-c model=$model",
        })
        with patch.dict("os.environ", {"TEST_CAPS_URL": "http://caps.local:9000"}):
            with patch("urllib.request.urlopen", return_value=self._mock_response("ok")) as mock_open:
                adapter.generate(_make_request(model="gpt-5.4"))
        called_url = mock_open.call_args[0][0].full_url
        self.assertIn("args=", called_url)
        self.assertIn("gpt-5.4", called_url)

    def test_missing_caps_url_raises(self) -> None:
        import os
        os.environ.pop("NONEXISTENT_CAPS_URL", None)
        adapter = self._make_adapter_with_mock_render({
            "type": "devenv_caps",
            "capability": "codex",
            "caps_url_env": "NONEXISTENT_CAPS_URL",
        })
        with self.assertRaises(RuntimeError):
            adapter.generate(_make_request())

    def test_nonzero_exit_code_raises(self) -> None:
        adapter = self._make_adapter_with_mock_render({
            "type": "devenv_caps",
            "capability": "codex",
            "caps_url_env": "TEST_CAPS_URL",
        })
        with patch.dict("os.environ", {"TEST_CAPS_URL": "http://caps.local:9000"}):
            with patch("urllib.request.urlopen", return_value=self._mock_response("error output", exit_code=1)):
                with self.assertRaises(RuntimeError):
                    adapter.generate(_make_request())


if __name__ == "__main__":
    unittest.main()
