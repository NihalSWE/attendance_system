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


class MissedScanForm(StyledFormMixin, forms.Form):
    """An employee reporting a scan the device missed (N11)."""

    work_date = forms.DateField(
        label="Attendance day", widget=date_widget("Choose a date"),
        help_text="The day whose attendance is missing the scan.",
    )
    at = CompanyDateTimeField(
        label="When you scanned", placeholder="Choose a date",
        help_text="Usually the same date. On a night shift, a scan after midnight is on the next date.",
    )
    reason = forms.CharField(
        label="What happened",
        help_text="Whoever approves it reads this.",
        widget=forms.Textarea(attrs={
            "rows": 3, "placeholder": "e.g. Came back from tea with Karim and walked in on his scan",
        }),
    )

    def clean_at(self):
        if not (self.data.get(self.add_prefix("at") + "_1") or "").strip():
            raise forms.ValidationError("Enter the time of the scan.")
        return self.cleaned_data["at"]


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
