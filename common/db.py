"""PostgreSQL helpers shared by effective-dated models.

Effective-dated history uses an inclusive start / exclusive end convention
(``effective_from <= instant < effective_to``); a null end means open-ended.
``TstzRange`` turns a (from, to) pair into a PostgreSQL ``tstzrange`` so
ExclusionConstraint can reject overlapping periods in the database itself,
which no application-level "does it overlap?" check can do safely under
concurrency.
"""

from django.contrib.postgres.fields import DateRangeField, DateTimeRangeField
from django.db.models import Func


class TstzRange(Func):
    """SQL ``tstzrange(lower, upper, bounds)`` as a Django expression.

    Pair it with ``RangeBoundary()`` (which defaults to '[)') so the range
    matches our inclusive-start/exclusive-end convention exactly. A NULL upper
    bound yields an unbounded range, i.e. an open-ended period.
    """

    function = "tstzrange"
    output_field = DateTimeRangeField()


class DateRange(Func):
    """SQL ``daterange(lower, upper, bounds)`` for date-only effective periods.

    Same convention as TstzRange: '[)' bounds, NULL upper means open-ended.
    Used where the business period is a calendar date rather than an instant
    (shift rosters, weekly-off rules).
    """

    function = "daterange"
    output_field = DateRangeField()
