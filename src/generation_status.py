"""Failure-class vocabulary shared by the replay producer and the aggregator.

Why this is not in ``generator_v2``
-----------------------------------
These are sets of status strings.  They were originally defined in
``generator_v2`` next to the code that produces them, which meant the
*aggregator* -- a pure arithmetic module that only counts statuses -- had to
import the model SDK to read them.  That made an offline analysis script
depend on langchain being installed, and it made the CI test set larger than
the logic it covers.

Keeping the vocabulary here means the aggregator can be imported anywhere, and
the two sides still agree on the names because both import them from one place
rather than repeating the literals.
"""

from __future__ import annotations

# Failure classes that mean "the model was never asked successfully" as opposed
# to "the model answered and the answer was bad".
PROVIDER_FAILURE_CLASSES = frozenset(
    {"provider_timeout", "provider_rate_limit", "provider_error"}
)

# Of those, the subset caused by a latency ceiling rather than a broken service.
# The remedy differs: raise the ceiling, do not retry harder.
PROVIDER_LATENCY_CLASSES = frozenset({"provider_timeout"})

# The subset that indicates the service itself misbehaved (retry / switch host).
PROVIDER_HARD_ERROR_CLASSES = frozenset({"provider_rate_limit", "provider_error"})

# Reported on its own as well as being counted inside the hard-error set: a
# rate limit is a throughput problem (back off) while an error is a service
# problem (retry elsewhere), and the report separates them.
RATE_LIMIT_STATUS = "provider_rate_limit"

# The status of a row that produced an answer.  Named here so the aggregator
# does not compare against a bare string literal.
COMPLETED_STATUS = "completed"

# Failure classes worth retrying.  A timeout or a transient transport error is
# often a one-off stall; a rate limit needs backoff.  All three are retryable.
# ``invalid_response`` / ``evaluation_error`` are NOT: retrying a malformed
# answer re-rolls the dice instead of fixing the parser.
RETRYABLE_FAILURE_CLASSES = PROVIDER_FAILURE_CLASSES

__all__ = [
    "COMPLETED_STATUS",
    "PROVIDER_FAILURE_CLASSES",
    "PROVIDER_HARD_ERROR_CLASSES",
    "PROVIDER_LATENCY_CLASSES",
    "RATE_LIMIT_STATUS",
    "RETRYABLE_FAILURE_CLASSES",
]
