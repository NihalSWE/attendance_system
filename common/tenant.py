"""Per-request / per-task tenant context.

Holds the "current company" for the active unit of work in a
``contextvars.ContextVar``. contextvars are isolated per thread *and* per async
task and do not leak across them, which makes them the correct primitive for
tenant context under WSGI threads, ASGI, and Celery workers alike.

Web middleware (P1 auth slice) and the Celery base task set the current company
at the start of work and clear it at the end. The tenant-scoped manager
(``common.models.TenantManager``) reads it to filter every query. Bootstrap code
that must run *before* a company is known (e.g. resolving which companies a user
belongs to) queries through the unscoped ``all_objects`` manager instead.
"""

import contextvars
from contextlib import contextmanager

# None means "no active tenant" — the scoped manager fails loud in that state.
_current_company_id = contextvars.ContextVar("current_company_id", default=None)


def set_current_company_id(company_id):
    """Set the active company id; returns a token for reset()."""
    return _current_company_id.set(company_id)


def get_current_company_id():
    """Return the active company id, or None if no tenant context is set."""
    return _current_company_id.get()


def clear_current_company_id(token=None):
    """Clear the active company. Pass the token from set() to restore precisely."""
    if token is not None:
        _current_company_id.reset(token)
    else:
        _current_company_id.set(None)


@contextmanager
def use_company(company):
    """Scope a block of code to a company (tests, background jobs, shell).

    Accepts a Company instance or a raw id. Restores the previous context on
    exit, so nested/overlapping scopes behave correctly.
    """
    company_id = getattr(company, "pk", company)
    token = _current_company_id.set(company_id)
    try:
        yield
    finally:
        _current_company_id.reset(token)
