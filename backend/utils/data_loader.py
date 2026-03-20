from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class DataRepository:
    root: Path

    @classmethod
    def from_project_root(cls, root: str | Path | None = None) -> "DataRepository":
        project_root = Path(root) if root else Path(__file__).resolve().parents[1]
        return cls(project_root / "data")

    def _read_json(self, relative_path: str) -> Any:
        with (self.root / relative_path).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _read_yaml(self, relative_path: str) -> Any:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load playbooks.")
        with (self.root / relative_path).open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def load_products(self) -> list[dict[str, Any]]:
        return self._read_json("products/appliances.json")

    def load_error_codes(self) -> dict[str, Any]:
        return self._read_json("error_codes.json")

    def load_quick_fixes(self) -> list[dict[str, Any]]:
        return self._read_json("quick_fixes.json")

    def load_symptoms(self) -> list[dict[str, Any]]:
        return self._read_json("symptoms.json")

    def load_playbooks(self) -> dict[str, Any]:
        return self._read_yaml("playbooks/appliance_playbooks.yaml")

    def load_manual_index(self) -> list[dict[str, Any]]:
        return self._read_json("manual_index.json")
