"""Navigation helpers for the shared shell."""

from django import template
from django.urls import resolve

register = template.Library()


@register.simple_tag
def active(request, *url_names):
    """Return "is-active" when the current URL matches one of url_names.

    Menu highlighting is presentation only — every view still enforces its own
    permission and tenant scope independently.
    """
    try:
        current = resolve(request.path_info).url_name
    except Exception:
        return ""
    return "is-active" if current in url_names else ""
