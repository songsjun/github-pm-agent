import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
