from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Dict, Iterable, List

from github_pm_agent.utils import load_text


class PromptLibrary:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def _path(self, relative_path: str) -> Path:
        return self.project_root / relative_path

    def _render_refs(self, refs: Iterable[str]) -> str:
        chunks: List[str] = []
        for ref in refs:
            path = self._path(ref)
            chunks.append(f"FILE: {ref}\n{load_text(path).strip()}\n")
        return "\n".join(chunk for chunk in chunks if chunk.strip())

    def load_template(self, relative_path: str) -> str:
        return load_text(self._path(relative_path)).strip()

    def render(
        self,
        system_prompt_path: str,
        prompt_path: str,
        variables: Dict[str, str],
        output_template_path: str = "",
        file_refs: Iterable[str] = (),
        memory_refs: Iterable[str] = (),
        skill_refs: Iterable[str] = (),
        transcript: str = "",
    ) -> str:
        base = self.load_template(prompt_path)
        system_prompt = self.load_template(system_prompt_path)
        output_template = self.load_template(output_template_path) if output_template_path else ""
        payload = {
            "output_template": output_template,
            "memory": self._render_refs(memory_refs),
            "skills": self._render_refs(skill_refs),
            **{key: str(value) for key, value in variables.items()},
        }
        rendered = Template(base).safe_substitute(payload)

        files_block = self._render_refs(file_refs)
        sections = [
            "# System",
            system_prompt,
            "",
            "# Prompt",
            rendered,
        ]
        if transcript:
            sections.extend(["", "# Session Transcript", transcript])
        if files_block:
            sections.extend(["", "# Attached Files", files_block])
        return "\n".join(sections).strip() + "\n"

