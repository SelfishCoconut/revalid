"""Placeholder integration test proving the marker and CI job are wired."""

import pytest

import revalid


@pytest.mark.integration
def test_package_metadata() -> None:
    assert revalid.__version__
