"""Forms for fixing a day by hand (plan step N5).

Validation of what a fix may do lives in attendance.correction_services; these
only collect the input with the project's own controls.
"""

from django import forms

from common.forms import CompanyDateTimeField, StyledFormMixin, date_widget

REASON_HELP = "Kept with the change, so the next person reading the day knows why."


def _reason_field(placeholder):
    return forms.CharField(
        label="Reason",
        help_text=REASON_HELP,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": placeholder}),
    )


class AddScanForm(StyledFormMixin, forms.Form):
    at = CompanyDateTimeField(label="When they scanned", placeholder="Choose a date")
    reason = _reason_field("e.g. The device was offline at the front door")

    def __init__(self, *args, day=None, **kwargs):
        super().__init__(*args, **kwargs)
        if day is not None and not self.is_bound:
            # The date is almost always the day being fixed; only the time is new.
            self.initial.setdefault("at", day)

    def clean_at(self):
        # The shared field reads a blank time as midnight, which suits an
        # effective date but would quietly invent a 00:00 scan here.
        if not (self.data.get(self.add_prefix("at") + "_1") or "").strip():
            raise forms.ValidationError("Enter the time of the scan.")
        return self.cleaned_data["at"]


class ChangeStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(
        label="The day was",
        choices=(
            ("present", "Present"),
            ("half_day", "Half day"),
            ("absent", "Absent"),
        ),
    )
    reason = _reason_field("e.g. Worked at the client's office all day")


class AcceptReviewForm(StyledFormMixin, forms.Form):
    reason = _reason_field("e.g. Confirmed with their manager they left at 18:00")


class WithdrawForm(StyledFormMixin, forms.Form):
    note = forms.CharField(
        label="Why it is being withdrawn", required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional"}),
    )


#: The cases a form offers (the old one-scan "A missed scan" is not asked any more).
MISSING_KINDS = [
    ("check_in", "Missing check-in"),
    ("check_out", "Missing check-out"),
    ("both", "Missing check-in and check-out"),
    ("whole_day", "A whole missing day (the shift's times)"),
]


class MissedScanForm(StyledFormMixin, forms.Form):
    """A missing scan, or a missing day (N11; the cases named 2026-09-26).
    The employee's own, or entered for them (``EnterMissingForm``)."""

    kind = forms.ChoiceField(
        label="What is missing", choices=MISSING_KINDS, initial="check_in", required=False,
        widget=forms.Select(attrs={"data-missing-kind": ""}),
    )
    # Asked only for a whole missing day (Nihal, 2026-09-29): for a scan, the
    # day is the scan's own date - see scan_requests.attendance_day.
    work_date = forms.DateField(
        label="Day", widget=date_widget("Choose a date"), required=False,
        help_text="The day that is missing. The shift's start and end are used.",
    )
    at = CompanyDateTimeField(
        label="When you scanned", placeholder="Choose a date", required=False,
        help_text=("The date and time of the scan. After midnight on a night shift, pick the "
                   "next date: it still counts for the day the shift started."),
    )
    at_out = CompanyDateTimeField(
        label="Check-out", placeholder="Choose a date", required=False,
        help_text="When both are missing: the check-out.",
    )
    reason = forms.CharField(
        label="What happened",
        help_text="Whoever approves it reads this.",
        widget=forms.Textarea(attrs={
            "rows": 3, "placeholder": "e.g. Came back from tea with Karim and walked in on his scan",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Which times each case shows (missed_scan_kind.js hides the rest).
        for name in ("at", "at_out"):
            for part in self.fields[name].widget.widgets:
                part.attrs["data-missing-time"] = name
        self.fields["work_date"].widget.attrs["data-missing-time"] = "work_date"

    def _time_given(self, name):
        return bool((self.data.get(self.add_prefix(name) + "_1") or "").strip())

    def clean(self):
        data = super().clean()
        # Sent without a case (as before the cases were named): one scan.
        kind = data["kind"] = data.get("kind") or "scan"
        if kind == "whole_day":
            data["at"] = data["at_out"] = None     # the shift's times, worked out later
            if not data.get("work_date") and "work_date" not in self.errors:
                self.add_error("work_date", "Choose the day that is missing.")
            return data
        # A scan's day is the day it counts on, worked out from its date and
        # time; a date left in the hidden Day box must not overrule it.
        data["work_date"] = None
        if not self._time_given("at"):
            self.add_error("at", "Enter the time of the scan.")
        if kind == "both" and not self._time_given("at_out"):
            self.add_error("at_out", "Enter the check-out time.")
        if kind != "both":
            data["at_out"] = None
        return data


class EnterMissingForm(MissedScanForm):
    """The same, entered for someone by HR or their manager.

    ``approve_now``: the owner or company admin may approve what they enter
    (nobody is above them), so they are offered to do it in the same step -
    ticked, so their entry changes the attendance at once (2026-09-28)."""

    def __init__(self, *args, approve_now=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["at"].label = "Time of the scan (the check-in when both are missing)"
        self.fields["reason"].widget.attrs["placeholder"] = (
            "e.g. Came in with the visitors at the side gate; confirmed by the guard")
        if approve_now:
            self.fields["approve_now"] = forms.BooleanField(
                required=False, initial=True, label="Approve it now",
                help_text="The attendance changes at once. Untick to leave it for someone "
                          "else who may fix attendance to approve.")


class DecideMissedScanForm(StyledFormMixin, forms.Form):
    note = forms.CharField(
        label="Note", required=False,
        help_text="Needed when rejecting. The employee can read it.",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Checked with the guard at the gate"}),
    )



def _range_input(key, placeholder):
    """Two inputs sharing a data-daterange key become one range picker."""
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={
            "type": "date", "data-daterange": key,
            "data-placeholder": placeholder, "data-presets": "none",
        },
    )


class DailyListFilterForm(StyledFormMixin, forms.Form):
    """Narrow the Daily list to one day, or a from/to range (plan step N12).

    Precedence in the view: a single ``on`` date wins; else a ``from``/``to``
    range; else the month selectors. Blank means "use the month".
    """

    on = forms.DateField(
        required=False, label="On date", widget=date_widget("Any day"),
        help_text="Show just this day.",
    )
    date_from = forms.DateField(
        required=False, label="From", widget=_range_input("att-range", "From"),
    )
    date_to = forms.DateField(
        required=False, label="To", widget=_range_input("att-range", "To"),
    )

    #: A range wider than this is refused: the whole span is recalculated on
    #: read, so an unbounded range would be a heavy query (mirrors Re-check).
    MAX_SPAN_DAYS = 366

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end:
            if end < start:
                self.add_error("date_to", "The end date is before the start date.")
            elif (end - start).days + 1 > self.MAX_SPAN_DAYS:
                self.add_error("date_to", "Choose a range of at most 366 days.")
        return cleaned

    def window(self):
        """The (start, end) the filter selects, or None to use the month."""
        on = self.cleaned_data.get("on")
        if on:
            return on, on
        start, end = self.cleaned_data.get("date_from"), self.cleaned_data.get("date_to")
        if start or end:
            start = start or end
            end = end or start
            return start, end
        return None
