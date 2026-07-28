"""Read optional ETS project metadata for KNX mapping validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xknxproject import XKNXProj  # type: ignore[import-not-found]


def load_knx_project(path: str, password: str = "") -> Mapping[str, Any]:
    """Parse an ETS project without assigning any semantic device meaning."""
    return XKNXProj(path=path, password=password or None).parse()
