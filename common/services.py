"""Shared service-layer helpers.

Services are the single place business writes happen. Views, management commands,
Celery tasks and a future FastAPI router all call these rather than manipulating
models directly, so validation and transaction boundaries cannot be bypassed by
one caller forgetting them.
"""


def create_validated(model, **kwargs):
    """Build, fully validate, then save an instance.

    ``full_clean()`` is what triggers model ``clean()`` — including
    ``TenantOwned.validate_tenant_consistency()``, which rejects a foreign key
    pointing at another company's row. Calling ``Model.objects.create()``
    directly skips all of that, so services must go through here.
    """
    instance = model(**kwargs)
    instance.full_clean()
    instance.save()
    return instance
