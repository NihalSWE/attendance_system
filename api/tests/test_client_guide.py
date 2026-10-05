"""The client in docs/api/120-build-a-client.md, run as written against a live
server - so the guide's code keeps working."""

import re
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase

from accounts.models import CompanyMembership, User
from api.tests.test_auth import PASSWORD
from tenants.services import onboard_company

GUIDE = Path(settings.BASE_DIR) / "docs" / "api" / "120-build-a-client.md"


def guide_client(base):
    """The guide's Python block, with BASE pointed at ``base``."""
    code = re.search(r"```python\n(.*?)```", GUIDE.read_text(encoding="utf-8"), re.S).group(1)
    code = code.split('if __name__ == "__main__":')[0]
    space = {"__name__": "guide_client"}
    exec(compile(code, str(GUIDE), "exec"), space)  # noqa: S102 - our own document
    space["BASE"] = base
    return space["Client"]


class ClientGuideTests(LiveServerTestCase):
    def test_the_guides_client_logs_in_signs_refreshes_and_reads(self):
        company = onboard_company(code="CLI", slug="cli", name="Client Ltd")
        user = User.objects.create_user(email="hr@client.test", password=PASSWORD)
        CompanyMembership.all_objects.create(company=company, user=user, role="hr",
                                             status="active")
        Client = guide_client(self.live_server_url)
        api = Client("hr@client.test", PASSWORD, device="Guide test")
        me = api.call("GET", "/api/v1/auth/me")
        self.assertEqual(me["companies"][0]["name"], "Client Ltd")
        today = api.call("GET", "/api/v1/attendance", query="on=2026-10-05")
        self.assertEqual(today["results"], [])
        api.access_until = 0                     # as if 9 minutes had passed
        self.assertEqual(api.call("GET", "/api/v1/auth/me")["email"], "hr@client.test")
        api.call("POST", "/api/v1/auth/logout")
        with self.assertRaisesRegex(RuntimeError, "session_ended|invalid_token"):
            api.call("GET", "/api/v1/auth/me")
