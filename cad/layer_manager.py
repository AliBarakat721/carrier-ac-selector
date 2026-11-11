"""Creation and validation of the standard HVAC DXF layers."""

from __future__ import annotations

from typing import Any

from .tag_config import CAD_LAYERS


def ensure_hvac_layers(document: Any) -> dict[str, str]:
    """Create missing HVAC layers and return their logical-name mapping.

    Existing layers are reused without changing their user-defined properties,
    so running an export repeatedly is safe and idempotent.
    """

    for layer_name in CAD_LAYERS.values():
        if layer_name not in document.layers:
            document.layers.add(layer_name)
    return dict(CAD_LAYERS)


def validate_hvac_layers(document: Any) -> list[str]:
    """Return standard layers that are still missing from a DXF document."""

    return [
        layer_name
        for layer_name in CAD_LAYERS.values()
        if layer_name not in document.layers
    ]
