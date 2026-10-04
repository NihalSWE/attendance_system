"""Paging for every list: ``?page=`` and ``?page_size=`` (at most 100).

Answers ``{"count", "next", "previous", "results"}``.
"""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    #: Shown on every list endpoint's page.
    PARAMS = (
        ("page", "integer", "Page number, starting at 1.", 1),
        ("page_size", "integer", "Results per page: 25 unless given, 100 at most.", 25),
    )
