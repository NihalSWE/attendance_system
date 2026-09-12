"""Monthly payroll: period, run, one record per employee, and its lines.

Thin slice of the 2026-09-12 salary fast-track. Names follow
MODEL_FIELD_DICTIONARY.md §60-62 and §65; policies, salary structures,
approval steps, payments and corrections are listed in docs/PHASE_STATUS.md as
not built yet.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import ActorTracked, TenantOwned

ZERO = Decimal("0.00")


class PayrollPeriod(TenantOwned, ActorTracked):
    name = models.CharField(max_length=64)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "payroll_period"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "start_date", "end_date"],
                name="uniq_payroll_period_per_company",
            ),
        ]

    def __str__(self):
        return self.name


class PayrollRun(TenantOwned, ActorTracked):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Finalised"

    payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.PROTECT, related_name="runs"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="generated_payroll_runs",
    )
    calculation_finished_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="posted_payroll_runs",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    totals_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_run"
        constraints = [
            # One finalised run per period: a second one needs the correction
            # path, which is not built yet.
            models.UniqueConstraint(
                fields=["company", "payroll_period"],
                condition=models.Q(status="posted"),
                name="uniq_posted_run_per_period",
            ),
        ]

    def __str__(self):
        return f"Payroll {self.payroll_period}"


class PayrollRecord(TenantOwned):
    payroll_run = models.ForeignKey(
        PayrollRun, on_delete=models.PROTECT, related_name="records"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="payroll_records"
    )
    employee_assignment_at_period_end = models.ForeignKey(
        "employees.EmployeeAssignment", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payroll_records",
    )
    currency = models.CharField(max_length=3, default="BDT")
    gross_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    calculation_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_record"
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "employee"], name="uniq_payroll_record_per_run"
            ),
        ]

    def __str__(self):
        return f"{self.employee_id} {self.net_pay}"


class PayrollLine(TenantOwned):
    class LineType(models.TextChoices):
        EARNING = "earning", "Earning"
        DEDUCTION = "deduction", "Deduction"

    payroll_record = models.ForeignKey(
        PayrollRecord, on_delete=models.PROTECT, related_name="lines"
    )
    line_type = models.CharField(max_length=16, choices=LineType.choices)
    code = models.CharField(max_length=32)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    rate = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_manual = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "payroll_line"
        ordering = ("sequence", "pk")

    def __str__(self):
        return f"{self.code} {self.amount}"
