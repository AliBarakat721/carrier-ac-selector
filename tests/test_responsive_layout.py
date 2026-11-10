import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import _window_sizes_for_available_geometry


@pytest.mark.parametrize(
    ("available_width", "available_height"),
    [
        (1366, 768),   # common laptop display at 100% scaling
        (1093, 614),   # 1366x768 physical display at 125% scaling
        (910, 512),    # 1366x768 physical display at 150% scaling
        (1920, 1080),  # desktop display at 100% scaling
        (1280, 720),   # 1920x1080 physical display at 150% scaling
        (900, 600),    # compact usable desktop geometry
    ],
)
def test_window_sizes_stay_inside_available_geometry(
    available_width, available_height
):
    width, height, min_width, min_height = (
        _window_sizes_for_available_geometry(
            available_width, available_height
        )
    )

    assert 0 < min_width <= width <= available_width
    assert 0 < min_height <= height <= available_height
    assert width <= 1300
    assert height <= 820


@pytest.mark.parametrize(("width", "height"), [(0, 768), (1366, 0), (-1, 600)])
def test_window_sizes_reject_invalid_screen_geometry(width, height):
    with pytest.raises(ValueError, match="must be positive"):
        _window_sizes_for_available_geometry(width, height)
