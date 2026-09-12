"""Company structure, in two layers.

**Root-owned catalogues** — ``Department`` and ``Designation`` are platform-wide
lists maintained by the root operator. No company may write to them.

**Company adoption rows** — ``Branch``, ``CompanyDepartment`` and
``CompanyDesignation`` are tenant-owned. A company picks catalogue entries and
places them in its own branches; every company-specific fact (employees, shifts,
permissions, scopes) hangs off the adoption row, never off the catalogue row.

See MODEL_FIELD_DICTIONARY.md §6-8 and §84-85.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.choices import ActiveStatus, DeviceAttendanceScope
from common.models import ActorTracked, TenantOwned, TimeStamped


class Branch(TenantOwned, ActorTracked):
    """A physical location of a company. Every company has one default branch."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    # Defaults from Company at creation time; stored so later company changes
    # do not silently rewrite this branch's historical interpretation.
    timezone = models.CharField(max_length=64, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    # The employee's assigned/home-branch device policy, NOT the source device's
    # branch policy. Null means inherit CompanyAttendanceSettings.
    device_attendance_scope_override = models.CharField(
        max_length=32,
        choices=DeviceAttendanceScope.choices,
        null=True,
        blank=True,
    )

    opened_on = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "organization_branch"
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_branch_code_per_company"
            ),
            # At most one active default branch per company.
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(is_default=True, status=ActiveStatus.ACTIVE),
                name="uniq_active_default_branch_per_company",
            ),
        ]

    def __str__(self):
        return self.name



class Department(TimeStamped, ActorTracked):
    """Platform-wide catalogue of department names, owned by the root operator.

    Deliberately **not** TenantOwned. One "Human Resources" exists for the whole
    platform; companies adopt it through :class:`CompanyDepartment` rather than
    inventing their own spelling of it. That keeps cross-company reporting
    comparable and stops ten tenants creating ten near-identical rows.

    Nothing company-specific lives here — no branch, no head, no open/close
    dates. Those belong to the adoption row. See MODEL_FIELD_DICTIONARY.md §7.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "organization_department"
        ordering = ("name",)

    def __str__(self):
        return self.name


class Designation(TimeStamped, ActorTracked):
    """Platform-wide list of designations, owned by the root operator.

    Deliberately **independent of departments**. Root curates one flat list of
    names; which designations sit under which department is a company's own
    decision, recorded on :class:`CompanyDesignation`. So a single "Manager"
    exists for the whole platform, and one company may place it under Sales
    while another places it under Production.

    There is no parent/child hierarchy either: access ceilings are set on the
    department (see ``access_control.DepartmentPermission``), not by walking a
    chain of designations.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "organization_designation"
        ordering = ("name",)

    def __str__(self):
        return self.name


class CompanyDepartment(TenantOwned, ActorTracked):
    """One company's use of a catalogue department, inside one of its branches.

    This is the row everything company-specific points at — employees, shifts,
    permission scopes. The catalogue row is shared; this row is not, so a shift
    pattern set by one company can never leak into another.

    The name and code are read through to the catalogue on purpose. Storing a
    local copy would let it drift, which is exactly the duplication the
    catalogue exists to prevent.
    """

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="company_departments"
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="company_links"
    )
    # The employee who administers permissions for everyone in this department.
    # Nullable: a department may exist before its head is appointed.
    head = models.ForeignKey(
        "employees.Employee",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="headed_departments",
    )
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )
    opened_on = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "organization_company_department"
        ordering = ("branch__name", "department__name")
        constraints = [
            # A branch adopts each catalogue department at most once.
            models.UniqueConstraint(
                fields=["branch", "department"],
                name="uniq_companydepartment_per_branch",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "branch"]),
        ]

    def __str__(self):
        return f"{self.department.name} ({self.branch.name})"

    @property
    def code(self):
        return self.department.code

    @property
    def name(self):
        return self.department.name


class CompanyDesignation(TenantOwned, ActorTracked):
    """The company-wise department-designation relation.

    Root keeps departments and designations as two independent lists. This row
    is where one company says "in *our* Sales department, Manager is a
    designation people hold". Another company is free to place the same
    Manager under Production, and neither choice constrains the other.

    Because the relation lives here rather than on the root designation, any
    active designation may be assigned to any of the company's departments.
    """

    company_department = models.ForeignKey(
        CompanyDepartment, on_delete=models.PROTECT, related_name="designations"
    )
    designation = models.ForeignKey(
        Designation, on_delete=models.PROTECT, related_name="company_links"
    )
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "organization_company_designation"
        ordering = ("company_department__department__name", "designation__name")
        constraints = [
            models.UniqueConstraint(
                fields=["company_department", "designation"],
                name="uniq_companydesignation_per_department",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "company_department"]),
        ]

    def __str__(self):
        return self.designation.name

    @property
    def branch(self):
        return self.company_department.branch

    @property
    def code(self):
        return self.designation.code

    @property
    def name(self):
        return self.designation.name
