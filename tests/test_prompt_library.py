import tempfile
import unittest
from pathlib import Path

from github_pm_agent.artifact_store import ArtifactStore
from github_pm_agent.prompt_library import PromptLibrary


class PromptLibraryTest(unittest.TestCase):
    def test_render_includes_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "prompts").mkdir()
            (root / "prompts" / "system.md").write_text("system", encoding="utf-8")
            (root / "prompts" / "task.md").write_text(
                "hello ${repo}\n${memory}\n${skills}\n${artifacts}",
                encoding="utf-8",
            )
            (root / "memory").mkdir()
            (root / "memory" / "note.md").write_text("memory body", encoding="utf-8")
            (root / "skills").mkdir()
            (root / "skills" / "note.md").write_text("skill body", encoding="utf-8")
            store = ArtifactStore(root / "runtime", project_root=root)
            store.save(
                "brief",
                body="artifact body",
                title="Project Brief",
                created_at="2026-03-19T00:00:00Z",
            )
            library = PromptLibrary(root)
            rendered = library.render(
                system_prompt_path="prompts/system.md",
                prompt_path="prompts/task.md",
                variables={"repo": "songsjun/example"},
                memory_refs=["memory/note.md"],
                skill_refs=["skills/note.md"],
                artifact_refs=store.latest_refs(["brief"]),
            )
            self.assertIn("songsjun/example", rendered)
            self.assertIn("memory body", rendered)
            self.assertIn("skill body", rendered)
            self.assertIn("artifact body", rendered)
            self.assertIn("# Attached Artifacts", rendered)

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
        self.assertIn(
            '"action_type": "comment|label|issue|assign|unassign|review_request|remove_reviewer|edit|milestone|draft|ready_for_review|merge|review_decision|rerun_workflow|cancel_workflow|create_release|create_discussion|update_discussion|project|state|none"',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
