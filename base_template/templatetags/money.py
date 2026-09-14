"""Amounts for display: 30000 -> "30,000.00".

Presentation only; stored values and calculations are untouched. Use it for
every amount shown on a page:  {% load money %} {{ record.net_pay|money }}
"""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value, places=2):
    """Thousands separators and a fixed number of decimals; blanks pass through."""
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value))
        places = int(places)
    except (InvalidOperation, ValueError, TypeError):
        return value
    return f"{amount:,.{places}f}"


@register.filter
def hm(minutes):
    """Minutes as hours and minutes, as the attendance calendar shows them: 95 -> "1h 35m"."""
    try:
        minutes = int(minutes or 0)
    except (ValueError, TypeError):
        return minutes
    return f"{minutes // 60}h {minutes % 60}m"
