"""Classify failures at a known feature boundary.

This predicate never grants sampling admission, source health or an ACK. Callers
separately prove which feature failed. Optional views keep core reads, event
reduction and history writes outside their guards; failed event I/O cannot
certify a replacement event prefix.
"""
import errno

from collector import BattleChanged, UnsupportedVersion


def can_contain_feature_failure(error):
    """Keep resource exhaustion, cancellation and lifecycle signals visible."""
    if not isinstance(error, Exception) or isinstance(error, (
            MemoryError, SystemError, InterruptedError, BattleChanged, UnsupportedVersion)):
        return False
    if isinstance(error, OSError) and (
            error.errno == errno.ENOMEM or getattr(error, 'winerror', None) in (8, 14)):
        return False
    return True
