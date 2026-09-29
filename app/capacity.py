from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any


_STORAGE_CAPACITY_RE = re.compile(
    r"\b(?P<size>\d+(?:[.,]\d+)?)\s*(?P<unit>TB|GB|G)\b",
    flags=re.IGNORECASE,
)
_IPHONE_E_MODEL_RE = re.compile(r"^iphone\s*(?P<model>\d+)\s*e$")
_IPHONE_BASE_MODEL_RE = re.compile(r"^iphone\s*(?P<model>\d+)$")


def capacity_sort_key(value: Any) -> tuple[int, float]:
    """Return a numeric storage key, placing capacities we cannot parse last."""
    match = _STORAGE_CAPACITY_RE.search(str(value or ""))
    if match is None:
        return (1, 0.0)
    size = float(match.group("size").replace(",", "."))
    unit = match.group("unit").lower()
    if unit in {"gb", "g"} and size == 500:
        size = 512
    if unit == "tb":
        size *= 1024
    return (0, size)


def capacity_free_name(value: Any) -> str:
    """Normalize a product name without an embedded storage capacity."""
    name = _STORAGE_CAPACITY_RE.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", name).strip().lower()


def product_model_sort_key(value: Any) -> tuple[str, int, str]:
    """Place an iPhone E edition before its matching base generation."""
    model_name = capacity_free_name(value)
    e_match = _IPHONE_E_MODEL_RE.fullmatch(model_name)
    if e_match is not None:
        return (f"iphone {e_match.group('model')}", 0, model_name)

    base_match = _IPHONE_BASE_MODEL_RE.fullmatch(model_name)
    if base_match is not None:
        return (f"iphone {base_match.group('model')}", 1, model_name)
    return (model_name, 0, model_name)


def _move_e_iphone_models_before_base(model_order: list[str]) -> list[str]:
    ordered = list(model_order)
    for model_name in model_order:
        match = _IPHONE_E_MODEL_RE.fullmatch(model_name)
        if match is None:
            continue
        base_name = f"iphone {match.group('model')}"
        if model_name not in ordered or base_name not in ordered:
            continue
        if ordered.index(model_name) > ordered.index(base_name):
            ordered.remove(model_name)
            ordered.insert(ordered.index(base_name), model_name)
    return ordered


def sort_capacity_variants(items: Iterable[Any]) -> list[Any]:
    """Sort capacities within models; place E editions before matching base models."""
    groups: dict[str, list[Any]] = defaultdict(list)
    group_order: list[str] = []
    for item in items:
        name = str(getattr(item, "name", "") or "")
        model_key = capacity_free_name(name)
        if model_key not in groups:
            group_order.append(model_key)
        groups[model_key].append(item)

    ordered: list[Any] = []
    for model_key in _move_e_iphone_models_before_base(group_order):
        group = groups[model_key]

        def item_key(item: Any) -> tuple[tuple[int, float], str, str]:
            capacity = getattr(item, "capacity", None)
            if not capacity or str(capacity).strip() == "-":
                capacity = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}"
            color = getattr(item, "color", None) or getattr(item, "colors", None) or ""
            return (
                capacity_sort_key(capacity),
                str(color).lower(),
                str(getattr(item, "condition", "") or "").lower(),
            )

        ordered.extend(sorted(group, key=item_key))
    return ordered
