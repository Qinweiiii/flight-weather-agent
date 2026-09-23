"""Lightweight skill registry.

The registry is intentionally declarative and small. It documents what skills
exist, what permissions they need, and where their Python wrapper lives. Runtime
orchestration stays inside the existing LangGraph workflow.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass(frozen=True)
class SkillSpec:
    name: str
    type: str
    description: str
    entrypoint: str
    permissions: List[str]
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SkillSpec":
        required = ["name", "type", "description", "entrypoint"]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError(f"Skill spec missing required fields: {missing}")

        return cls(
            name=str(payload["name"]),
            type=str(payload["type"]),
            description=str(payload["description"]),
            entrypoint=str(payload["entrypoint"]),
            permissions=list(payload.get("permissions", [])),
            input_schema=dict(payload.get("input_schema", {})),
            output_schema=dict(payload.get("output_schema", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "permissions": self.permissions,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


class SkillRegistry:
    """Read-only registry for declared tools and skills."""

    def __init__(self, specs: List[SkillSpec], version: int = 1, description: str = ""):
        self.version = version
        self.description = description
        if len({spec.name for spec in specs}) != len(specs):
            raise ValueError('Duplicate skill name')
        self._specs = {spec.name: spec for spec in specs}
        self._handlers = {}

    def bind(self, name, handler):
        self.get(name)
        if not callable(handler):
            raise TypeError('Skill handler must be callable')
        self._handlers[name] = handler

    def invoke(self, name, granted_permissions, **params):
        spec = self.get(name)
        if set(spec.permissions) - set(granted_permissions):
            raise PermissionError(f'Missing skill permission: {name}')
        if set(params) - set(spec.input_schema):
            raise ValueError(f'Unknown inputs for {name}')
        if name not in self._handlers:
            raise ValueError(f'Unbound skill: {name}')
        return self._handlers[name](**params)

    @classmethod
    def from_file(cls, path: str | Path) -> "SkillRegistry":
        registry_path = Path(path)
        with registry_path.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}

        specs = [SkillSpec.from_dict(item) for item in payload.get("skills", [])]
        return cls(
            specs=specs,
            version=int(payload.get("version", 1)),
            description=str(payload.get("description", "")),
        )

    def list_specs(self, type_: Optional[str] = None) -> List[Dict[str, Any]]:
        specs = self._specs.values()
        if type_:
            specs = [spec for spec in specs if spec.type == type_]
        return [spec.to_dict() for spec in specs]

    def get(self, name: str) -> SkillSpec:
        if name not in self._specs:
            raise KeyError(f"Unknown skill: {name}")
        return self._specs[name]

    def summary(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "count": len(self._specs),
            "skills": self.list_specs(),
        }


def load_skill_registry(path: str | Path = "config/skill_registry.yaml") -> SkillRegistry:
    return SkillRegistry.from_file(path)
