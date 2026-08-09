from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml


def _normalize_topic(value: str | None) -> str:
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    plain = re.sub(r"[_-]+", " ", plain)
    return re.sub(r"\s+", " ", plain).strip().lower()


class FAQStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            loaded = {}
        self.data = loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _find(mapping: Any, normalized: str) -> Any:
        if not isinstance(mapping, dict):
            return None
        for key, value in mapping.items():
            if _normalize_topic(str(key)) == normalized:
                return value
        return None

    @staticmethod
    def _find_by_keyword(mapping: Any, normalized: str) -> Any:
        if not isinstance(mapping, dict):
            return None
        for key, value in mapping.items():
            normalized_key = _normalize_topic(str(key))
            if not normalized_key:
                continue
            pattern = rf"(?<!\w){re.escape(normalized_key)}(?!\w)"
            if re.search(pattern, normalized):
                return value
        return None

    def get(self, topic: str) -> str:
        normalized = _normalize_topic(topic or "geral")
        topics = self.data.get("topics", {})

        value = self._find(self.data, normalized)
        if value is None:
            value = self._find(topics, normalized)
        if value is None:
            value = self._find_by_keyword(topics, normalized)
        if value is None:
            value = self._find_by_keyword(self.data, normalized)

        if isinstance(value, (dict, list)):
            return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip()
        return str(value or "")

    def summary(self) -> str:
        visible = {key: value for key, value in self.data.items() if key not in {"internal", "secrets"}}
        return yaml.safe_dump(visible, allow_unicode=True, sort_keys=False).strip()
