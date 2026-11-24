"""Wall-clock budgets of the tests, scaled for slow machines.

Some tests assert that a refusal or a hostile input finishes within a time, mostly one second.  The budget guards against
blow-up (quadratic parsing, catastrophic backtracking, a missing size check), where the real time would be tens of
seconds or more.  The numbers were measured on a developer machine.  A shared CI runner is several times slower and
stalls now and then: on the first GitHub runs two one-second budgets were missed at 1.18 s and 1.20 s by calls that take
a few milliseconds locally.

``ENG_TOOLKIT_TEST_TIME_FACTOR`` multiplies every budget (default 1, so a developer machine keeps the strict numbers);
the CI workflow sets it to 5, which still fails on a blow-up.
"""

import os


def budget(seconds: float) -> float:
    """``seconds`` times ``ENG_TOOLKIT_TEST_TIME_FACTOR`` (at least 1)."""
    try:
        factor = float(os.environ.get("ENG_TOOLKIT_TEST_TIME_FACTOR", "1"))
    except ValueError:
        factor = 1.0
    return seconds * max(factor, 1.0)
