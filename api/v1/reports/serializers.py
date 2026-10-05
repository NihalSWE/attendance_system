"""What the reports and dashboard endpoints give."""

from rest_framework import serializers

from api.v1.company.serializers import RefSerializer


class ReportInfoSerializer(serializers.Serializer):
    slug = serializers.CharField(help_text="Its name in the address: /reports/{slug}.")
    title = serializers.CharField(help_text="e.g. Daily Attendance Report.")
    group = serializers.CharField(help_text="Its menu heading (may be empty).")
    period = serializers.CharField(help_text="day (on=), week (week_start=), month (year=, "
                                             "month=) or range (from=, to=).")
    max_days = serializers.IntegerField(help_text="The longest range it covers.")
    filters = serializers.ListField(child=serializers.CharField(),
                                    help_text="Its own filters, beyond branch_id, "
                                              "department_id and employee_id.")
    description = serializers.CharField(help_text="What it shows.")


class ReportColumnSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="The heading.")
    numeric = serializers.BooleanField(help_text="A number (sum it, right-align it).")
    kind = serializers.CharField(help_text="status (a badge), code (a day's letter), number, "
                                           "duration, or empty.")


class SummaryItemSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="What.")
    value = serializers.CharField(help_text="How many.")


class ReportSerializer(serializers.Serializer):
    slug = serializers.CharField(help_text="The report.")
    title = serializers.CharField(help_text="Its title.")
    period = serializers.CharField(help_text="The period, in words.")
    first = serializers.DateField(help_text="First day covered.")
    last = serializers.DateField(help_text="Last day covered.")
    filters = serializers.ListField(child=serializers.CharField(),
                                    help_text="The filters used, in words.")
    warnings = serializers.ListField(child=serializers.CharField(),
                                     help_text="e.g. a range shortened to the longest "
                                               "allowed.")
    columns = ReportColumnSerializer(many=True, help_text="The columns, in order.")
    summary = SummaryItemSerializer(many=True, help_text="Its totals.")
    legend = serializers.CharField(help_text="What the day letters mean (grids).")
    note = serializers.CharField(help_text="A note on how it is counted (may be empty).")
    count = serializers.IntegerField(help_text="Rows in all.")
    next = serializers.CharField(allow_null=True, help_text="The next page's address.")
    previous = serializers.CharField(allow_null=True, help_text="The previous page's address.")
    results = serializers.ListField(child=serializers.ListField(),
                                    help_text="This page's rows: one value per column.")


class StoppedDeviceSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The device id.")
    name = serializers.CharField(help_text="Its name.")
    branch = RefSerializer(help_text="Where.")
    last_seen_at = serializers.DateTimeField(allow_null=True, help_text="Last call-in.")
    detail = serializers.CharField(help_text="What it means, in words.")


class StillInSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="Who.")
    since = serializers.CharField(help_text="In since (HH:MM).")
    shift_ended = serializers.CharField(help_text="When their shift ended (HH:MM).")


class DashboardSerializer(serializers.Serializer):
    employees = serializers.IntegerField(help_text="Everyone on record.")
    active = serializers.IntegerField(help_text="Active.")
    probation = serializers.IntegerField(help_text="On probation.")
    resigned = serializers.IntegerField(help_text="Resigned.")
    branches = serializers.IntegerField(help_text="Branches.")
    departments = serializers.IntegerField(help_text="Departments.")
    recent = RefSerializer(many=True, help_text="The last people added.")
    stopped_devices = StoppedDeviceSerializer(many=True,
                                              help_text="Active devices not calling in.")
    still_in = StillInSerializer(many=True, help_text="Still in two hours after their shift "
                                                      "ended: working late, or gone without "
                                                      "scanning out?")
