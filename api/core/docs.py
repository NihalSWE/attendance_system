"""Documentation declared on the endpoint itself (docs/api/00-PLAN.md, 4.2).

Every endpoint method is decorated with ``@endpoint(...)``: its title,
summary, what it does, description, who may use it, parameters, request and
response serializers, examples and the errors it can return. From that one
declaration come:

- its page on the documentation site (api/docs_site),
- its entry in the OpenAPI schema behind the Swagger link (extend_schema),
- the documentation test's checks (api/tests/test_documentation.py).

So a page can never describe something the endpoint does not do.

Example::

    class PingView(ApiView):
        permission_classes = [Public]
        throttle_scope = "public"

        @endpoint(
            id="ping", area="start", title="Ping",
            summary="Is the API up, and what time is it on the server.",
            what_it_does=["Answers at once with the server's time."],
            description="...",
            roles=["Anyone - no login needed"],
            response=PingSerializer, response_example={...},
            errors=["rate_limited"],
        )
        def get(self, request): ...
"""

from dataclasses import dataclass, field

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema

#: The documentation site's menu, in this order. Each phase adds its area.
AREAS = (
    ("start", "Start here"),
    ("auth", "Logging in"),
    ("company", "Company & branches"),
    ("employees", "Employees"),
    ("shifts", "Shifts & calendar"),
    ("devices", "Devices"),
    ("device_flow", "Devices: data flow"),
    ("attendance", "Attendance"),
    ("leave", "Leave"),
    ("salary", "Salary"),
    ("me", "My account"),
    ("reports", "Reports"),
    ("integrations", "Integrations"),
)
AREA_TITLES = dict(AREAS)

#: Where a parameter travels.
PATH, QUERY, HEADER = "path", "query", "header"


@dataclass
class Param:
    name: str
    location: str           # PATH, QUERY or HEADER
    type: str               # "string", "integer", "boolean", "string (date)", ...
    description: str
    required: bool = False
    example: object = None


@dataclass
class EndpointDoc:
    id: str                 # unique slug: the page address /api/docs/endpoint/<id>/
    area: str               # one of AREAS
    title: str
    summary: str
    what_it_does: list
    description: str
    roles: list             # who may use it, in the panels' words
    errors: list            # catalogue codes
    scopes: list = field(default_factory=list)   # API key scopes (phase 1)
    params: list = field(default_factory=list)   # Param, for path / query / header
    request: object = None  # serializer class for the body
    response: object = None  # serializer class for a success answer
    response_status: int = 200
    paginated: bool = False
    request_example: object = None
    response_example: object = None
    auth: str = "login"     # "public", or "login" (phase 1: token / signature / key)
    # Filled in by the registry:
    method: str = ""
    path: str = ""
    view: object = None


def endpoint(**kwargs):
    """Declare an endpoint method's documentation, and tell Swagger the same."""
    doc = EndpointDoc(**kwargs)

    def decorate(method):
        method._api_doc = doc
        parameters = [
            OpenApiParameter(
                p.name, location={PATH: OpenApiParameter.PATH, QUERY: OpenApiParameter.QUERY,
                                  HEADER: OpenApiParameter.HEADER}[p.location],
                required=p.required or p.location == PATH, description=p.description,
            )
            for p in doc.params
        ]
        examples = []
        if doc.request_example is not None:
            examples.append(OpenApiExample("Request", value=doc.request_example,
                                           request_only=True))
        if doc.response_example is not None:
            examples.append(OpenApiExample("Response", value=doc.response_example,
                                           response_only=True,
                                           status_codes=[str(doc.response_status)]))
        responses = {doc.response_status: doc.response} if doc.response else None
        return extend_schema(
            operation_id=doc.id, summary=doc.title, description=doc.summary + "\n\n"
            + doc.description, tags=[AREA_TITLES.get(doc.area, doc.area)],
            request=doc.request, responses=responses, parameters=parameters or None,
            examples=examples or None,
        )(method)

    return decorate
