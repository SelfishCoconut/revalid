"""Placeholder unit test proving the unit level of the pyramid is wired."""

from revalid import health


def test_health() -> None:
    assert health() == "revalid ok"
