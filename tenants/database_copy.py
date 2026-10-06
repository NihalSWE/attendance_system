"""Shared by the dump_database and load_database commands (2026-10-06)."""

import contextlib


@contextlib.contextmanager
def every_company():
    """Every company's rows, for the length of a whole-database copy.

    The tenant-scoped managers refuse to read without a company - on purpose,
    so ordinary code never mixes companies. A whole-database copy is the one
    job that must handle every company at once: ``dumpdata --all`` covers each
    table's own rows, but a row's many-to-many links (a login's branches, say)
    are read - and, when loading, written - through the linked model's scoped
    manager. For that command's run only, the scope is lifted; it is put back
    afterwards, whatever happens.
    """
    from django.db import models

    from common.models import TenantManager

    scoped = TenantManager.get_queryset
    TenantManager.get_queryset = lambda manager: models.Manager.get_queryset(manager)
    try:
        yield
    finally:
        TenantManager.get_queryset = scoped
