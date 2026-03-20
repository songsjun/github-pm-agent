from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class RoleRegistry:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._cache: Dict[str, Dict[str, Any]] = {}

    def load(self, role: str) -> Dict[str, Any]:
        if role not in self._cache:
            role_dir = self.project_root / "roles" / role
            permissions_path = role_dir / "permissions.json"
            permissions = json.loads(permissions_path.read_text()) if permissions_path.exists() else {}
            self._cache[role] = {
                "role": role,
                "role_dir": role_dir,
                "permissions": permissions,
                "system_prompt_path": str(role_dir / "system.md")
                if (role_dir / "system.md").exists()
                else "prompts/system/pm.md",
                "skill_refs": [str(p.relative_to(self.project_root)) for p in (role_dir / "skills").glob("*.md")]
                if (role_dir / "skills").exists()
                else ["skills/pm-core.md"],
            }
        return self._cache[role]

    def exists(self, role: str) -> bool:
        return (self.project_root / "roles" / role).is_dir()
