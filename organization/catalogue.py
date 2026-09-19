"""Helpers for creating a company's departments and designations.

Departments and designations are company-owned again (see PHASE_STATUS.md): a
department lives in a branch, a designation lives in a department. These wrap the
plain creates for callers that legitimately create structure directly — the demo
seeder and tests. Real UI flows go through ``organization.services`` with
authorization, audit and a form.

The names ``adopt_department`` / ``adopt_designation`` are kept for their many
callers; there is no longer a separate root catalogue to "adopt" from — each
call simply creates the company's own row. Requires an active tenant context.
"""

from organization.models import Department, Designation


def adopt_department(branch, code, name, **kwargs):
    """Create a department in ``branch`` (company comes from the tenant context)."""
    return Department.objects.create(branch=branch, code=code, name=name, **kwargs)


def adopt_designation(department, code, name, **kwargs):
    """Create a designation inside ``department``."""
    return Designation.objects.create(
        department=department, code=code, name=name, **kwargs
    )
