"""Placeholder system test proving the marker and nightly job are wired.

Real system tests will run the full retest flow against dockerized lab
targets (OWASP Juice Shop / DVWA) with known ground truth.
"""

import pytest

import revalid


@pytest.mark.system
def test_system_level_runs() -> None:
    assert revalid.health() == "revalid ok"
