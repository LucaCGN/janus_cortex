from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def has_executable_event_token(candidate: Mapping[str, Any]) -> bool:
    """Return whether a candidate has the minimum canonical token identity.

    Full candidate-to-intent conversion belongs to issue #116. This identity
    helper exists for issue #110 parity tests because candidates without an
    event token must not be executable in replay or live paths.
    """

    return bool(candidate.get("event_token_key"))
