from dataclasses import dataclass

from integration.room_matching import match_rooms_to_systems


@dataclass
class Room:
    room_id: str
    label: str | None


@dataclass
class System:
    name: str


def test_matches_unique_exact_label_as_system_reference_only():
    result = match_rooms_to_systems([Room("ROOM-001", "G01")], [System("g-01")])
    assert result[0].status == "MATCHED_SYSTEM_REFERENCE_ONLY"
    assert result[0].system_name == "g-01"
    assert "SYSTEM_LEVEL_DATA_NOT_ROOM_LEVEL" in result[0].warnings


def test_does_not_infer_match_from_room_id_when_label_is_missing():
    result = match_rooms_to_systems([Room("G01", None)], [System("G01")])
    assert result[0].status == "UNMATCHED"
    assert result[0].system_name is None


def test_ambiguous_duplicate_system_names_require_manual_resolution():
    result = match_rooms_to_systems(
        [Room("ROOM-001", "Office 1")], [System("Office-1"), System("Office 1")]
    )
    assert result[0].status == "AMBIGUOUS"
    assert result[0].system_name is None


def test_dynamic_room_count_returns_one_result_per_room():
    rooms = [Room(f"ROOM-{i:03d}", f"R{i:03d}") for i in range(1, 31)]
    systems = [System(f"R{i:03d}") for i in range(1, 31)]
    result = match_rooms_to_systems(rooms, systems)
    assert len(result) == 30
    assert all(item.status == "MATCHED_SYSTEM_REFERENCE_ONLY" for item in result)


def test_missing_room_id_is_not_silently_accepted():
    result = match_rooms_to_systems([Room("", "G01")], [System("G01")])
    assert result[0].status == "INVALID_ROOM_ID"
    assert result[0].system_name is None
