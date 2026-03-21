from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from github_pm_agent.utils import append_jsonl, ensure_dir, load_text, parse_iso8601, read_jsonl, utc_now_iso


ARTIFACT_KINDS: Sequence[str] = (
    "brief",
    "spec_review",
    "release_readiness",
    "retro_summary",
)


@dataclass
class ArtifactRecord:
    kind: str
    title: str
    summary: str
    path: str
    created_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "path": self.path,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ArtifactRecord":
        return cls(
            kind=str(payload.get("kind", "")),
            title=str(payload.get("title", "")),
            summary=str(payload.get("summary", "")),
            path=str(payload.get("path", "")),
            created_at=str(payload.get("created_at", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


class ArtifactStore:
    def __init__(self, runtime_dir: Path, project_root: Optional[Path] = None) -> None:
        self.runtime_dir = runtime_dir.resolve()
        self.project_root = Path(project_root or runtime_dir.parent).resolve()
        self.artifacts_dir = self.runtime_dir / "artifacts"
        self.index_path = self.artifacts_dir / "index.jsonl"
        ensure_dir(self.artifacts_dir)

    def save(
        self,
        kind: str,
        body: str,
        title: str = "",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> ArtifactRecord:
        self._validate_kind(kind)
        created_at = created_at or utc_now_iso()
        metadata = dict(metadata or {})
        title = title.strip() or self._default_title(kind)
        summary = summary.strip()
        relative_path = self._relative_artifact_path(kind, title, created_at)
        full_path = self.runtime_dir / relative_path
        full_path = self._dedupe_path(full_path)
        ensure_dir(full_path.parent)
        full_path.write_text(
            self._render_document(kind, title, summary, created_at, metadata, body),
            encoding="utf-8",
        )
        record = ArtifactRecord(
            kind=kind,
            title=title,
            summary=summary,
            path=str(full_path.relative_to(self.runtime_dir)),
            created_at=created_at,
            metadata=metadata,
        )
        append_jsonl(self.index_path, record.to_dict())
        return record

    def latest(self, kind: str) -> Optional[ArtifactRecord]:
        self._validate_kind(kind)
        latest_record: Optional[ArtifactRecord] = None
        for record in self.records():
            if record.kind != kind:
                continue
            if latest_record is None:
                latest_record = record
                continue
            if parse_iso8601(record.created_at) >= parse_iso8601(latest_record.created_at):
                latest_record = record
        return latest_record

    def records(self) -> List[ArtifactRecord]:
        return [ArtifactRecord.from_dict(item) for item in read_jsonl(self.index_path)]

    def latest_refs(self, kinds: Iterable[str]) -> List[str]:
        refs: List[str] = []
        seen = set()
        for kind in kinds:
            if kind in seen:
                continue
            seen.add(kind)
            record = self.latest(kind)
            if record is None:
                continue
            refs.append(self._prompt_ref(record))
        return refs

    def read(self, record: ArtifactRecord) -> str:
        return load_text(self.runtime_dir / record.path)

    def latest_content(self, kind: str) -> str:
        record = self.latest(kind)
        if record is None:
            return ""
        return self.read(record)

    def _render_document(
        self,
        kind: str,
        title: str,
        summary: str,
        created_at: str,
        metadata: Dict[str, Any],
        body: str,
    ) -> str:
        lines = [
            f"# {title}",
            "",
            f"- Kind: {kind}",
            f"- Created At: {created_at}",
        ]
        if summary:
            lines.append(f"- Summary: {summary}")
        if metadata:
            lines.append(f"- Metadata: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}")
        body = body.strip()
        if body:
            lines.extend(["", body])
        return "\n".join(lines).rstrip() + "\n"

    def _prompt_ref(self, record: ArtifactRecord) -> str:
        full_path = (self.runtime_dir / record.path).resolve()
        try:
            return str(full_path.relative_to(self.project_root))
        except ValueError:
            return str(full_path)

    def _relative_artifact_path(self, kind: str, title: str, created_at: str) -> Path:
        stamp = self._timestamp_token(created_at)
        slug = self._slugify(title)
        return Path("artifacts") / kind / f"{stamp}__{slug}.md"

    def _dedupe_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _default_title(self, kind: str) -> str:
        return kind.replace("_", " ").title()

    def _timestamp_token(self, created_at: str) -> str:
        return re.sub(r"[^0-9A-Za-z]+", "", created_at)

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug[:64] or "artifact"

    def _validate_kind(self, kind: str) -> None:
        if kind not in ARTIFACT_KINDS:
            allowed = ", ".join(ARTIFACT_KINDS)
            raise ValueError(f"Unsupported artifact kind: {kind}. Allowed kinds: {allowed}")
