"""The whole API, swept (docs/api/00-PLAN.md 2.5, 2.9): every documented
endpoint is called by someone who should not get in, and must refuse.

Each phase tests its own endpoints; this walks all of them at once, so an
endpoint added later that forgets its gate fails here.
"""

from api.core.permissions import Public
from api.core.registry import endpoints
from api.tests.test_attendance import AttendanceApiTestCase

#: A value for every path parameter that is not a plain number.
SAMPLES = {"slug": "daily-attendance", "date": "2026-10-05", "key_id": "key_x", "pin": "1",
           "session_id": "ses_x", "device_id": "00000000-0000-0000-0000-000000000000",
           "message_id": "00000000-0000-0000-0000-000000000000"}

#: Open to every login in the company, as the panel's My account is: each
#: answers only the caller's own record (or, for a login without one, nothing).
EVERY_LOGIN = ("/api/v1/me", "/api/v1/reports")


def _path(doc):
    path = doc.path
    for name, value in SAMPLES.items():
        path = path.replace("{" + name + "}", value)
    while "{" in path:
        start, end = path.index("{"), path.index("}")
        path = path[:start] + "999999" + path[end + 1:]
    return path


def _guarded(doc):
    return Public not in getattr(doc.view, "permission_classes", ()) and doc.area != "auth"


class SweepTests(AttendanceApiTestCase):
    def test_nothing_answers_without_a_login(self):
        for doc in endpoints():
            if not _guarded(doc):
                continue
            with self.subTest(endpoint=f"{doc.method} {doc.path}"):
                response = self.call(doc.method, _path(doc), {})
                self.assertEqual(response.status_code, 401, response.content[:200])

    def test_an_employee_without_grants_reaches_only_their_own_pages(self):
        self.person("staff@example.test", role="employee")
        staff = self.logged_in("staff@example.test")
        for doc in endpoints():
            if not _guarded(doc) or doc.path.startswith(EVERY_LOGIN):
                continue
            with self.subTest(endpoint=f"{doc.method} {doc.path}"):
                response = self.call(doc.method, _path(doc), {}, session=staff)
                self.assertIn(response.status_code, (403, 404), response.content[:300])

    def test_no_endpoint_fails_on_ids_that_do_not_exist(self):
        """The company's administrator, reading with made-up ids: a clean
        answer every time - never a server error."""
        for doc in endpoints():
            if doc.method != "GET" or not _guarded(doc):
                continue
            with self.subTest(endpoint=f"GET {doc.path}"):
                response = self.call("GET", _path(doc), session=self.admin)
                self.assertLess(response.status_code, 500, response.content[:300])

    def test_another_companys_records_are_not_found(self):
        """Ids from another company answer 404, as if they did not exist."""
        from employees.services import create_employee
        from organization.catalogue import adopt_department, adopt_designation
        from common.tenant import use_company
        import datetime
        from decimal import Decimal

        with use_company(self.other):
            department = adopt_department(self.other_branch, "OPS", "Operations")
            designation = adopt_designation(department, "MGR", "Manager")
        theirs = create_employee(
            company=self.other, first_name="Karim", employee_code="X1",
            branch=self.other_branch, department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("10000"))["employee"]
        for path in (f"/api/v1/employees/{theirs.pk}", f"/api/v1/employees/{theirs.pk}/history",
                     f"/api/v1/branches/{self.other_branch.pk}",
                     f"/api/v1/departments/{department.pk}",
                     f"/api/v1/employees/{theirs.pk}/components",
                     f"/api/v1/attendance/days/{theirs.pk}/2026-10-05"):
            with self.subTest(path=path):
                response = self.call("GET", path, session=self.admin)
                self.assertEqual(response.status_code, 404, response.content[:300])


class SecurityLogTests(AttendanceApiTestCase):
    def test_refused_signatures_and_replays_are_logged_without_secrets(self):
        from api.tests.test_auth import signature_headers

        session = self.admin
        headers = signature_headers(session["signing_secret"], session["session_id"],
                                    "GET", "/api/v1/auth/me", "", "")
        headers["HTTP_AUTHORIZATION"] = "Bearer " + session["access_token"]
        wrong = {**headers, "HTTP_X_SIGNATURE": "0" * 64}
        with self.assertLogs("api.security", "WARNING") as logged:
            self.assertEqual(self.client.get("/api/v1/auth/me", **wrong).status_code, 401)
            self.assertEqual(self.client.get("/api/v1/auth/me", **headers).status_code, 200)
            replay = self.client.get("/api/v1/auth/me", **headers)
        self.assertEqual(self.code_of(replay), "replay_detected")
        text = "\n".join(logged.output)
        self.assertIn("invalid_signature", text)
        self.assertIn("replay_detected", text)
        for secret in (session["signing_secret"], session["access_token"],
                       headers["HTTP_X_SIGNATURE"]):
            self.assertNotIn(secret, text)
