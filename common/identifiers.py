"""Reusable generated identifiers. Call inside an atomic write operation."""
from django.db import connection
from django.utils.text import slugify


def unique_slug(queryset, value, *, field="slug", max_length=64):
    """Callers serialize allocation; the unique constraint remains the backstop."""
    base = slugify(value)[:max_length].strip("-") or "item"
    candidate = base
    suffix = 0
    while queryset.filter(**{field: candidate}).exists():
        suffix += 1
        ending = f"-{suffix}"
        candidate = base[:max_length - len(ending)].rstrip("-") + ending
    return candidate


def prepare_company_identifiers(company):
    if company.code and company.slug:
        return
    with connection.cursor() as cursor:
        # Serializes generated slugs even when the company table is empty.
        cursor.execute("SELECT pg_advisory_xact_lock(10001, 1)")
        if not company.code:
            while True:
                cursor.execute("SELECT nextval('tenants_company_code_seq')")
                company.code = str(cursor.fetchone()[0])
                if not type(company).objects.filter(code=company.code).exists():
                    break
    if not company.slug:
        company.slug = unique_slug(type(company).objects, company.name)
