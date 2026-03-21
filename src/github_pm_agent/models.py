from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    event_id: str
    event_type: str
    source: str
    occurred_at: str
    repo: str
    actor: str
    url: str
    title: str
    body: str
    target_kind: str
    target_number: Optional[int]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "occurred_at": self.occurred_at,
            "repo": self.repo,
            "actor": self.actor,
            "url": self.url,
            "title": self.title,
            "body": self.body,
            "target_kind": self.target_kind,
            "target_number": self.target_number,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Event":
        return cls(
            event_id=payload["event_id"],
            event_type=payload["event_type"],
            source=payload["source"],
            occurred_at=payload["occurred_at"],
            repo=payload["repo"],
            actor=payload.get("actor", ""),
            url=payload.get("url", ""),
            title=payload.get("title", ""),
            body=payload.get("body", ""),
            target_kind=payload.get("target_kind", "unknown"),
            target_number=payload.get("target_number"),
            metadata=payload.get("metadata", {}),
        )


@dataclass
class AiRequest:
    provider: str
    model: str
    system_prompt_path: str
    prompt_path: str
    variables: Dict[str, Any] = field(default_factory=dict)
    file_refs: List[str] = field(default_factory=list)
    memory_refs: List[str] = field(default_factory=list)
    skill_refs: List[str] = field(default_factory=list)
    artifact_refs: List[str] = field(default_factory=list)
    output_template_path: Optional[str] = None
    output_schema_path: Optional[str] = None
    session_key: Optional[str] = None


@dataclass
class AiResponse:
    provider: str
    model: str
    content: str
    raw: Dict[str, Any] = field(default_factory=dict)
    session_key: Optional[str] = None


@dataclass
class ActionResult:
    executed: bool
    action_type: str
    target: Dict[str, Any]
    message: str
    raw: Dict[str, Any] = field(default_factory=dict)
