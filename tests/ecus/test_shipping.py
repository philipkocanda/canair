import pytest

from canair.ecus.shipping import param_ships


@pytest.mark.parametrize(
    "param,expected",
    [
        ({"expression": "B0"}, True),
        ({"expression": "B0", "ship": True}, True),
        ({"expression": "B0", "ship": False}, False),
        ({"expression": "B0", "enabled": False}, True),
        (None, True),
    ],
)
def test_param_ships(param, expected):
    assert param_ships(param) is expected
