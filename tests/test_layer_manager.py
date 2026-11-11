import ezdxf

from cad.layer_manager import ensure_hvac_layers, validate_hvac_layers
from cad.tag_config import CAD_LAYERS


def test_hvac_layers_are_created_idempotently():
    drawing = ezdxf.new("R2018")

    ensure_hvac_layers(drawing)
    first_names = sorted(layer.dxf.name for layer in drawing.layers)
    ensure_hvac_layers(drawing)
    second_names = sorted(layer.dxf.name for layer in drawing.layers)

    assert first_names == second_names
    assert validate_hvac_layers(drawing) == []
    assert set(CAD_LAYERS.values()).issubset(set(second_names))
