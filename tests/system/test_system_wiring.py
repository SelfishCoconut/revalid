"""System-level smoke check: the ``system`` marker and its nightly job are wired.

The real system coverage lives in the sibling modules --- ``test_retest_session_system``
drives a full gated retest against the dockerized Juice Shop lab and asserts the egress
lock from inside the sandbox. This module only proves the level itself runs and that the
package is importable in the environment the nightly job builds.
"""

import pytest

import revalid


@pytest.mark.system
def test_system_level_runs() -> None:
    assert revalid.__version__  # the installed package reports a real version
