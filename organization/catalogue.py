"""Helpers for creating a catalogue entry and adopting it into a company.

Departments and designations are root-owned catalogues: root curates the names,
and a company adopts the ones it uses into its own branches. That is two writes
where it used to be one, and the second write is easy to forget — which is how a
company-specific fact ends up hanging off a row every tenant shares.

These wrap the pair. In production the two halves belong to different people
(root curates, a company administrator adopts), so this module is for callers
that legitimately play both parts: the demo seeder and tests.

Real UI flows must not use these — they need authorization, audit and a form,
and they will live in ``organization.services``.
"""

from organization.models import (
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)


def ensure_department(code, name, **defaults):
    """Return the platform department for ``name``, creating it if new."""
    department, _ = Department.objects.get_or_create(
        name=name, defaults={"code": code, **defaults}
    )
    return department


def ensure_designation(code, name, **defaults):
    """Return the platform designation for ``name``, creating it if new.

    Takes no department: root keeps designations as a flat list, and which
    departments use one is each company's decision.
    """
    designation, _ = Designation.objects.get_or_create(
        name=name, defaults={"code": code, **defaults}
    )
    return designation


def adopt_department(branch, code, name, **kwargs):
    """Adopt a catalogue department into ``branch``, creating the entry if new.

    Requires an active tenant context, since the adoption row is tenant-owned.
    """
    return CompanyDepartment.objects.create(
        branch=branch, department=ensure_department(code, name), **kwargs
    )


def adopt_designation(company_department, code, name, **kwargs):
    """Assign a platform designation to one of the company's own departments."""
    designation = ensure_designation(code, name)
    return CompanyDesignation.objects.create(
        company_department=company_department, designation=designation, **kwargs
    )
