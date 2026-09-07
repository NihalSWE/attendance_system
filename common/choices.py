"""Shared controlled enums.

Store stable codes, never display labels (MODEL_FIELD_DICTIONARY.md Conventions).
These live in common/ because several apps share them; putting them in one domain
app would force unrelated apps to import it.
"""

from django.db import models


class ActiveStatus(models.TextChoices):
    """Generic active/inactive lifecycle used by organization records."""

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class DeviceAttendanceScope(models.TextChoices):
    """Which devices an employee's punches count on.

    Effective precedence (DEVICE_ATTENDANCE_POLICY.md): dated EmployeeAssignment
    override wins, then the assigned/home Branch override, then the company
    default in CompanyAttendanceSettings. A null override means "inherit".
    """

    ASSIGNED_DEVICES = "assigned_devices", "Assigned devices only"
    DEPARTMENT_DEVICES = "department_devices", "Department devices"
    BRANCH_DEVICES = "branch_devices", "Branch devices"
    COMPANY_DEVICES = "company_devices", "Company devices"
