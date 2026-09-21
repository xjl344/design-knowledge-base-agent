"""JSON checkpoints for resumable local demo sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> str:
    """Keep non-JSON state readable while preserving the object's type name."""
    name = type(value).__name__
    return f"{name}({value!r})"


class CheckpointManager:
    def __init__(self, checkpoint_dir: Path) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe_id = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_ ").strip()
        if not safe_id:
            raise ValueError("invalid session_id")
        return self.checkpoint_dir / f"{safe_id}.json"

    def save(self, session_id: str, state: dict[str, Any]) -> Path:
        path = self._path(session_id)
        path.write_text(json.dumps(state, ensure_ascii=False, default=_json_default, indent=2), encoding="utf-8")
        return path

    def load(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
