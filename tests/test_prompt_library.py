import tempfile
import unittest
from pathlib import Path

from github_pm_agent.prompt_library import PromptLibrary


class PromptLibraryTest(unittest.TestCase):
    def test_render_includes_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "prompts").mkdir()
            (root / "prompts" / "system.md").write_text("system", encoding="utf-8")
            (root / "prompts" / "task.md").write_text("hello ${repo}\n${memory}", encoding="utf-8")
            (root / "memory").mkdir()
            (root / "memory" / "note.md").write_text("memory body", encoding="utf-8")
            library = PromptLibrary(root)
            rendered = library.render(
                system_prompt_path="prompts/system.md",
                prompt_path="prompts/task.md",
                variables={"repo": "songsjun/example"},
                memory_refs=["memory/note.md"],
            )
            self.assertIn("songsjun/example", rendered)
            self.assertIn("memory body", rendered)

    def test_render_real_stage_prompt_with_skills_and_output_template(self) -> None:
        root = Path("/Users/sjunsong/Workspace/github-pm-agent")
        library = PromptLibrary(root)

        rendered = library.render(
            system_prompt_path="prompts/system/pm.md",
            prompt_path="prompts/actions/intake_clarify.md",
            variables={
                "repo": "songsjun/example",
                "event_type": "issue_comment",
                "event_payload": '{"title": "Clarify this request"}',
            },
            memory_refs=["memory/README.md"],
            skill_refs=["skills/clarify.md", "skills/pm-core.md"],
            output_template_path="templates/output/action_plan.json",
        )

        self.assertIn("# System", rendered)
        self.assertIn("# Prompt", rendered)
        self.assertIn("Treat this as an intake / clarification event.", rendered)
        self.assertIn("Clarify Skill", rendered)
        self.assertIn("PM Core Skill", rendered)
        self.assertIn('"action_type": "comment|label|issue|assign|review_request|state|none"', rendered)


if __name__ == "__main__":
    unittest.main()
