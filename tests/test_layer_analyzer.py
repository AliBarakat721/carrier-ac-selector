"""Tests for lexical layer classification (Phase 3, step 1).

The classifier is a *suggestion engine*.  These tests lock the behaviour that
matters for room detection:

* standard architectural layer names are recognised,
* Arabic layer names are recognised after folding,
* unknown names are never guessed (OTHER + LOW),
* ambiguous names expose their alternatives instead of hiding them,
* a user override always wins and is recorded as such.
"""

from __future__ import annotations

import pytest

from cad.layer_analyzer import (
    ClassificationConfidence,
    LayerClass,
    LayerMapping,
    build_layer_mapping,
    classify_layer,
    classify_layers,
    normalize_layer_name,
)


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("A-WALL", LayerClass.WALLS),
        ("WALLS", LayerClass.WALLS),
        ("S-WALL-FULL", LayerClass.WALLS),
        ("A-WALL-PART", LayerClass.WALLS),
        ("A-DOOR", LayerClass.DOORS),
        ("A-GLAZ", LayerClass.WINDOWS),
        ("A-FURN", LayerClass.FURNITURE),
        ("I-FURNITURE", LayerClass.FURNITURE),
        ("A-ANNO-DIMS", LayerClass.DIMENSIONS),
        ("A-ANNO-TEXT", LayerClass.TEXT_LABELS),
        ("ROOM-TEXT", LayerClass.TEXT_LABELS),
        ("S-COLS", LayerClass.COLUMNS),
        ("ROOM-BOUNDARY", LayerClass.ROOM_BOUNDARIES),
        ("SPACES", LayerClass.ROOM_BOUNDARIES),
    ],
)
def test_standard_layer_names_are_classified(layer, expected):
    assert classify_layer(layer).layer_class is expected


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("حوائط", LayerClass.WALLS),
        ("جدران", LayerClass.WALLS),
        ("أبواب", LayerClass.DOORS),
        ("شبابيك", LayerClass.WINDOWS),
        ("أثاث", LayerClass.FURNITURE),
        ("غرف", LayerClass.ROOM_BOUNDARIES),
        ("نصوص", LayerClass.TEXT_LABELS),
        ("أعمدة", LayerClass.COLUMNS),
    ],
)
def test_arabic_layer_names_are_classified(layer, expected):
    assert classify_layer(layer).layer_class is expected


def test_arabic_normalization_folds_letter_variants_and_diacritics():
    assert normalize_layer_name("أعمِدة") == normalize_layer_name("اعمده")
    assert normalize_layer_name("غُرَف") == "غرف"


def test_unknown_layer_is_other_with_low_confidence():
    result = classify_layer("Layer1")

    assert result.layer_class is LayerClass.OTHER
    assert result.confidence is ClassificationConfidence.LOW
    assert result.method == "no_keyword"
    assert result.needs_user_confirmation is True


def test_empty_layer_name_does_not_raise():
    result = classify_layer("")

    assert result.layer_class is LayerClass.OTHER
    assert result.method == "empty_name"


def test_ambiguous_layer_reports_alternatives_instead_of_guessing():
    # "ROOM-WALL" scores equally for walls and room boundaries.
    result = classify_layer("ROOM-WALL")

    assert result.layer_class in {LayerClass.WALLS, LayerClass.ROOM_BOUNDARIES}
    assert result.alternatives
    assert result.is_ambiguous is True
    assert result.needs_user_confirmation is True


def test_exact_token_match_is_high_confidence():
    result = classify_layer("WALLS")

    assert result.confidence is ClassificationConfidence.HIGH
    assert result.method == "exact_token"
    assert result.matched_keywords == ("walls",)


def test_user_override_wins_and_is_marked_as_such():
    mapping = build_layer_mapping(["Layer1", "WALLS"])
    overridden = mapping.with_override("Layer1", LayerClass.WALLS)

    assert mapping.layer_class("Layer1") is LayerClass.OTHER
    assert overridden.layer_class("Layer1") is LayerClass.WALLS
    assert overridden.classification("Layer1").method == "user_override"
    assert overridden.classification("Layer1").needs_user_confirmation is False


def test_override_accepts_plain_strings_for_serialisation():
    mapping = build_layer_mapping(["Layer1"], {"Layer1": "FURNITURE"})

    assert mapping.layer_class("Layer1") is LayerClass.FURNITURE


def test_wall_label_and_excluded_layer_sets_are_disjoint():
    mapping = build_layer_mapping(
        ["A-WALL", "ROOMS", "A-FURN", "A-ANNO-TEXT", "A-DOOR", "A-GLAZ", "S-COLS"]
    )

    walls = mapping.wall_layers()
    labels = mapping.label_layers()
    excluded = mapping.excluded_layers()

    assert walls == {"a-wall", "rooms"}
    assert labels == {"a-anno-text"}
    # Text layers are excluded from room production as well, but they are the
    # only source of room labels, so they are a subset of `excluded`.
    assert excluded == {"a-furn", "a-door", "a-glaz", "s-cols", "a-anno-text"}
    assert labels <= excluded
    assert not walls & excluded


def test_layers_of_is_case_insensitive():
    mapping = build_layer_mapping(["A-Wall"])

    assert "a-wall" in mapping.wall_layers()


def test_unconfirmed_layers_lists_everything_needing_review():
    mapping = build_layer_mapping(["WALLS", "Layer1", "ROOM-WALL"])

    unconfirmed = {item.layer for item in mapping.unconfirmed_layers()}

    assert "WALLS" not in unconfirmed
    assert {"Layer1", "ROOM-WALL"} <= unconfirmed


def test_rows_are_plain_serialisable_dicts():
    mapping = build_layer_mapping(["A-WALL", "Layer1"])

    rows = mapping.rows()

    assert [row["layer"] for row in rows] == ["A-WALL", "Layer1"]
    assert rows[0]["layer_class"] == "WALLS"
    assert rows[0]["needs_confirmation"] is False
    assert rows[1]["needs_confirmation"] is True
    assert all(isinstance(value, (str, bool)) for row in rows for value in row.values())


def test_classify_layers_preserves_original_spelling():
    result = classify_layers(["A-Wall", "a-wall", "A-WALL"])

    assert list(result) == ["A-Wall", "a-wall", "A-WALL"]
    assert all(item.layer_class is LayerClass.WALLS for item in result.values())


def test_layer_mapping_defaults_are_independent_instances():
    first = LayerMapping()
    second = LayerMapping()

    first.overrides["X"] = LayerClass.WALLS

    assert second.overrides == {}
