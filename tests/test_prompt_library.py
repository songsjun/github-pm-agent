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


if __name__ == "__main__":
    unittest.main()
