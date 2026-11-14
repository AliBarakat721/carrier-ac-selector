"""Pickle-friendly CAD analysis tasks for worker processes.

This module intentionally contains no Qt imports. Functions here receive the
serializable CAD model and return plain Python/domain objects.
"""

from __future__ import annotations

from collections import Counter


def detect_rooms_task(document, fast_mode: bool = True, **options):
    """Run the expensive room detector in a child process."""
    from .space_detector import detect_spaces_with_review

    return detect_spaces_with_review(
        document,
        include_non_room_layer_candidates=not fast_mode,
        **options,
    )


def summarize_document_task(document) -> dict[str, object]:
    """Build cheap metadata while room geometry is being detected."""
    counts = Counter(entity.entity_type.upper() for entity in document.entities)
    return {
        "entity_count": len(document.entities),
        "layer_count": len(document.layers),
        "entity_counts": dict(counts),
        "units": document.units,
    }
