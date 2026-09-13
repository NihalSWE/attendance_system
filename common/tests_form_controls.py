"""Every date and time control on the site is ours, not the browser's.

UI_AND_ONBOARDING_CONVENTIONS.md: no browser-default pickers. That is easy to
honour when you write a form and easy to lose when you add a field to one, so
this walks every form in the project and fails on the ones the rule forbids.

It found the five that started it: ``installed_at`` on the device form, and
``effective_from`` / ``effective_to`` on the enrollment and device-department
forms, all rendering ``<input type="datetime-local">`` — the browser's own
calendar *and* its own clock in a single control.
"""

import datetime
import importlib
import inspect
import pkgutil

from django import forms
from django.apps import apps
from django.test import TestCase

from common.tenant import use_company
from tenants.models import Company

# Widgets whose type attribute is a browser-drawn control we do not allow.
FORBIDDEN_INPUT_TYPES = {"datetime-local", "time", "month", "week"}

# A date input is only acceptable with one of these, which is what swaps the
# browser's calendar for datepicker.js.
CALENDAR_MARKERS = ("data-datepicker", "data-daterange")


def _project_form_classes():
    """Every Form/ModelForm defined by a first-party app."""
    seen = {}
    for config in apps.get_app_configs():
        if "site-packages" in (config.path or "") or config.name.startswith("django."):
            continue
        for _finder, name, _ispkg in pkgutil.iter_modules([config.path]):
            if "form" not in name:
                continue
            try:
                module = importlib.import_module(f"{config.name}.{name}")
            except Exception:  # pragma: no cover - a broken module fails elsewhere
                continue
            for attr, value in vars(module).items():
                if (
                    inspect.isclass(value)
                    and issubclass(value, forms.BaseForm)
                    and value.__module__ == module.__name__
                ):
                    seen[f"{module.__name__}.{attr}"] = value
    return seen


def _input_kind(widget):
    """The rendered ``type=`` of an input widget.

    Django's ``Input.__init__`` pops ``type`` out of ``attrs`` into
    ``input_type``, so a widget built with ``attrs={"type": "datetime-local"}``
    has an empty-looking attrs dict and the real answer on the instance.
    Reading only attrs is how you write a sweep that passes on the very bug it
    was added for.
    """
    return (widget.attrs or {}).get("type") or getattr(widget, "input_type", "")


def _leaf_widgets(widget):
    """A widget, or each subwidget of a MultiWidget."""
    if isinstance(widget, forms.MultiWidget):
        for sub in widget.widgets:
            yield from _leaf_widgets(sub)
    else:
        yield widget


class NoBrowserDefaultPickerTests(TestCase):
    """A form declaring a date or datetime must use the project calendar."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            code="TZ", slug="tz", name="Timezone Co", timezone="Asia/Dhaka"
        )

    def _fields(self):
        """(label, field) for every declared field of every project form.

        Declared fields only: instantiating each form needs per-form arguments
        and a populated tenant, and the class-level declaration is where the
        widget is chosen anyway.
        """
        for path, form_class in sorted(_project_form_classes().items()):
            declared = getattr(form_class, "declared_fields", {})
            for name, field in declared.items():
                yield f"{path}.{name}", field

            # ModelForm fields generated from the model still get a widget
            # from Meta.widgets, which is the other place a native picker
            # can be introduced.
            meta = getattr(form_class, "Meta", None)
            for name, widget in (getattr(meta, "widgets", None) or {}).items():
                if name in declared:
                    continue
                yield f"{path}.Meta.widgets.{name}", forms.Field(widget=widget)

    def test_no_form_uses_a_browser_drawn_picker(self):
        offenders = []
        for label, field in self._fields():
            for widget in _leaf_widgets(field.widget):
                kind = _input_kind(widget)
                if kind in FORBIDDEN_INPUT_TYPES:
                    offenders.append(f"{label}: type={kind!r}")
        self.assertEqual(
            offenders,
            [],
            "These use a control the browser draws itself. Dates use the "
            "project calendar (data-datepicker); times are HH:MM text; a "
            "datetime is common.forms.CompanyDateTimeField.\n  "
            + "\n  ".join(offenders),
        )

    def test_every_date_input_is_wired_to_the_project_calendar(self):
        offenders = []
        for label, field in self._fields():
            for widget in _leaf_widgets(field.widget):
                attrs = widget.attrs or {}
                is_date = _input_kind(widget) == "date" or isinstance(
                    widget, forms.DateInput
                )
                if not is_date:
                    continue
                if not any(marker in attrs for marker in CALENDAR_MARKERS):
                    offenders.append(label)
        self.assertEqual(
            offenders,
            [],
            "These render a bare date input, so the browser draws its own "
            "calendar. Add data-datepicker (or data-daterange for a pair):\n  "
            + "\n  ".join(offenders),
        )

    def test_a_datetime_field_is_never_a_single_native_input(self):
        """A DateTimeField's widget must be split into our date + time boxes."""
        offenders = []
        for label, field in self._fields():
            if not isinstance(field, forms.DateTimeField):
                continue
            if not isinstance(field.widget, forms.MultiWidget):
                offenders.append(label)
        self.assertEqual(
            offenders,
            [],
            "A datetime needs the project calendar for the date and an HH:MM "
            "box for the time. Use common.forms.CompanyDateTimeField:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_guard_actually_catches_a_native_picker(self):
        """Proof the sweep is not passing because it looks at nothing."""

        class Regression(forms.Form):
            when = forms.DateTimeField(
                widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
            )

        widget = Regression.declared_fields["when"].widget
        self.assertIn(_input_kind(widget), FORBIDDEN_INPUT_TYPES)
        self.assertNotIsInstance(widget, forms.MultiWidget)

    def test_the_sweep_reaches_a_useful_number_of_forms(self):
        """A silent import failure would make every check above vacuous."""
        found = _project_form_classes()
        self.assertGreater(len(found), 15, sorted(found))
        self.assertIn("devices.forms.BiometricDeviceForm", found)
        self.assertIn("scheduling.forms.ShiftForm", found)


class CompanyDateTimeFieldTests(TestCase):
    """The replacement control: company time in, company time back out."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            code="TZ", slug="tz", name="Timezone Co", timezone="Asia/Dhaka"
        )

    def _field(self, **kwargs):
        from common.forms import CompanyDateTimeField

        return CompanyDateTimeField(**kwargs)

    def test_a_date_and_time_are_read_as_company_time(self):
        with use_company(self.company):
            value = self._field().clean(["2026-09-15", "09:30"])
        # Asia/Dhaka is UTC+6, so 09:30 local is 03:30 UTC.
        self.assertEqual(value.astimezone(datetime.timezone.utc).hour, 3)
        self.assertEqual(value.astimezone(datetime.timezone.utc).minute, 30)

    def test_a_blank_time_means_midnight(self):
        with use_company(self.company):
            value = self._field().clean(["2026-09-15", ""])
        self.assertEqual((value.hour, value.minute), (0, 0))

    def test_a_time_with_no_date_is_refused(self):
        with use_company(self.company):
            with self.assertRaises(forms.ValidationError):
                self._field().clean(["", "09:30"])

    def test_an_optional_field_accepts_both_boxes_empty(self):
        with use_company(self.company):
            self.assertIsNone(self._field(required=False).clean(["", ""]))

    def test_editing_shows_the_saved_time_in_company_time(self):
        """The round trip: what was stored in UTC comes back as local clock."""
        from common.forms import CompanyDateTimeField

        stored = datetime.datetime(2026, 9, 15, 3, 30, tzinfo=datetime.timezone.utc)
        with use_company(self.company):
            field = CompanyDateTimeField()
            self.assertEqual(field.widget.decompress(stored), [
                datetime.date(2026, 9, 15), "09:30",
            ])

    def test_the_date_half_carries_the_project_calendar(self):
        with use_company(self.company):
            date_widget, time_widget = self._field().widget.widgets
        self.assertIn("data-datepicker", date_widget.attrs)
        self.assertEqual(_input_kind(date_widget), "date")
        self.assertEqual(time_widget.attrs.get("placeholder"), "HH:MM")
        self.assertEqual(_input_kind(time_widget), "text")
