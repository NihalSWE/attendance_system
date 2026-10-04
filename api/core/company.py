"""Which company a request acts for.

A person in several companies sends ``X-Company: <company id>``; it must be a
company they are an active member of. With one company the header may be left
out. An API key belongs to one company. ``ApiView`` applies it to every
endpoint that acts for a company.
"""

from accounts.services import get_active_memberships
from api.core.errors import ApiError

HEADER = "HTTP_X_COMPANY"


def resolve_company(request, user):
    """The company id the request acts for, or raise ``ApiError``."""
    # The panels' own rule: active memberships of trial or active companies.
    memberships = list(get_active_memberships(user).values_list("company_id", flat=True))
    asked = (request.META.get(HEADER) or "").strip()
    if asked:
        if not asked.isdigit() or int(asked) not in memberships:
            # Not "forbidden": whether that company exists is not revealed.
            raise ApiError("not_found", "No such company for this login.")
        return int(asked)
    if len(memberships) == 1:
        return memberships[0]
    if not memberships:
        raise ApiError("permission_denied", "This login belongs to no company.")
    raise ApiError("validation_error", "This login belongs to several companies.",
                   {"X-Company": ["Send the X-Company header with the company id."]})
