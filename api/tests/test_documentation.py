"""The documentation can never fall behind the code (docs/api/00-PLAN.md, 4.3).

Fails the build when:
- a route method has no ``@endpoint`` declaration;
- a declaration lacks its summary, what it does, description, roles, a
  response example (or a request example when it takes a body), or names an
  area that is not in the menu;
- any request or response field, at any depth, has no explanation;
- an endpoint names an error that is not in the catalogue, or a catalogue
  error lacks its title, meaning, fix or group;
- a view leaves the permission to the default (it must say who may use it),
  or has no rate-limit kind;
- two endpoints share a page address.
"""

from django.test import SimpleTestCase
from django.urls import reverse

from api.core import errors as catalogue
from api.core.docs import AREA_TITLES
from api.core.fields import tree, walk
from api.core.registry import endpoints, undocumented
from api.core.throttling import SCOPES


class DocumentationTests(SimpleTestCase):
    def test_every_route_method_is_documented(self):
        missing = [f"{method} {path} ({cls.__name__})" for cls, method, path in undocumented()]
        self.assertEqual(missing, [], "Undocumented endpoints - add @endpoint(...)")

    def test_every_declaration_is_complete(self):
        problems = []
        for doc in endpoints():
            name = f"{doc.method} {doc.path}"
            for attribute in ("id", "title", "summary", "description"):
                if not str(getattr(doc, attribute) or "").strip():
                    problems.append(f"{name}: no {attribute}")
            if not doc.what_it_does:
                problems.append(f"{name}: no 'what it does'")
            if not doc.roles:
                problems.append(f"{name}: no roles (who may use it)")
            if doc.area not in AREA_TITLES:
                problems.append(f"{name}: unknown area {doc.area!r}")
            if doc.response is not None and doc.response_example is None:
                problems.append(f"{name}: no response example")
            if doc.request is not None and doc.request_example is None:
                problems.append(f"{name}: no request example")
            if not doc.errors:
                problems.append(f"{name}: no errors listed")
            for param in doc.params:
                if not param.description.strip():
                    problems.append(f"{name}: parameter {param.name} has no description")
        self.assertEqual(problems, [])

    def test_every_field_is_explained(self):
        problems = []
        for doc in endpoints():
            for mode, serializer in (("request", doc.request), ("response", doc.response)):
                for path, node in walk(tree(serializer, mode)):
                    if not node["description"].strip():
                        problems.append(f"{doc.method} {doc.path} {mode}: {path}")
        self.assertEqual(problems, [], "Fields without help_text")

    def test_every_error_is_in_the_catalogue_and_explained(self):
        unknown = [f"{doc.method} {doc.path}: {code}" for doc in endpoints()
                   for code in doc.errors if code not in catalogue.CATALOGUE]
        self.assertEqual(unknown, [], "Errors not in api/core/errors.py")
        for spec in catalogue.CATALOGUE.values():
            with self.subTest(code=spec.code):
                for attribute in ("title", "meaning", "fix"):
                    self.assertTrue(getattr(spec, attribute).strip())
                self.assertIn(spec.group, catalogue.GROUPS)
                self.assertTrue(400 <= spec.status <= 599)

    def test_every_view_says_who_may_use_it_and_its_rate(self):
        problems = []
        for doc in endpoints():
            if "permission_classes" not in vars(doc.view):
                problems.append(f"{doc.view.__name__}: permission_classes left to the default")
            if getattr(doc.view, "throttle_scope", None) not in SCOPES:
                problems.append(f"{doc.view.__name__}: no known throttle_scope")
        self.assertEqual(problems, [])

    def test_page_addresses_are_unique_and_every_page_exists(self):
        ids = [doc.id for doc in endpoints()]
        self.assertEqual(len(ids), len(set(ids)))
        for endpoint_id in ids:
            response = self.client.get(reverse("api:docs_endpoint", args=[endpoint_id]))
            self.assertEqual(response.status_code, 200, endpoint_id)


class PostmanCollectionTests(SimpleTestCase):
    """The Postman collection (docs/api/07-postman.md) has every endpoint."""

    def test_the_collection_has_every_endpoint_and_signs_the_right_ones(self):
        from api.core.registry import endpoints
        from api.core.samples import mode_of

        response = self.client.get("/api/docs/postman.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        data = response.json()
        self.assertIn("v2.1.0", data["info"]["schema"])
        items = {}
        for folder in data["item"]:
            for item in folder["item"]:
                items[item["name"]] = item
        docs = endpoints()
        self.assertEqual(len(items), len(docs))
        for doc in docs:
            headers = {h["key"] for h in items[doc.title]["request"]["header"]}
            signed = mode_of(doc) in ("app", "app-refresh")
            self.assertEqual("X-Signature" in headers, signed, doc.id)
        login = items["Log in (apps)"]
        self.assertIn("{{email}}", login["request"]["body"]["raw"])
        self.assertEqual(login["event"][0]["listen"], "test")
        self.assertIn("X-Signature", "\n".join(data["event"][0]["script"]["exec"]))

    def test_the_guide_pages_open(self):
        for path in ("/api/docs/authentication/", "/api/docs/security/", "/api/docs/postman/",
                     "/api/docs/guides/company-and-branches/", "/api/docs/guides/employees/",
                     "/api/docs/guides/shifts-and-calendar/",
                     "/api/docs/guides/devices-setup/",
                     "/api/docs/guides/devices-data-flow/",
                     "/api/docs/guides/attendance/", "/api/docs/guides/leave/",
                     "/api/docs/guides/salary/", "/api/docs/guides/employee-app/",
                     "/api/docs/guides/reports/", "/api/docs/guides/integrations/",
                     "/api/docs/guides/build-a-client/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


class SchemaNameTests(SimpleTestCase):
    def test_no_two_serializers_share_a_name(self):
        """Swagger names each shape after its serializer: two different
        serializers with one name would make the schema wrong."""
        from rest_framework import serializers as rf

        from api.core.registry import endpoints

        seen, clashes = {}, []

        def visit(serializer_class):
            if serializer_class is None:
                return
            name = serializer_class.__name__.removesuffix("Serializer")
            if seen.setdefault(name, serializer_class) is not serializer_class:
                clashes.append(f"{name}: {seen[name].__module__} and {serializer_class.__module__}")
                return
            for field in serializer_class().fields.values():
                child = getattr(field, "child", field)
                if isinstance(child, rf.BaseSerializer):
                    visit(type(child))

        for doc in endpoints():
            visit(doc.request)
            visit(doc.response)
        self.assertEqual(clashes, [])
