"""No page shows another company's data, whatever id is put in its address
(2026-10-18).

Every panel page whose address carries an id is opened by the administrator
of a second company, with the first company's ids. Ids are given per table
from 1, and the first company's rows were made first, so its rows are the
ones hit. The first company's unusual names must never come back, and no page
may crash. A page added later is swept too: nothing here lists pages by hand.
"""

import datetime
import itertools

from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.converters import IntConverter, UUIDConverter

from devices.tests_mapping import MappingCase
from organization.converters import EmployeeConverter
from scheduling import services as schedule

MARKERS = ("Zorvexa", "Quillfern Holdings")
SKIPPED_NAMESPACES = {"api", "admin", "catalogue", "platform"}


def _patterns(resolver=None, namespace="", route=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            space = entry.namespace or ""
            if space in SKIPPED_NAMESPACES or namespace.split(":")[0] in SKIPPED_NAMESPACES:
                continue
            yield from _patterns(entry, f"{namespace}{space}:" if space else namespace,
                                 route + str(entry.pattern))
        elif isinstance(entry, URLPattern) and entry.name:
            converters = getattr(entry.pattern, "converters", {})
            if converters:
                yield f"{namespace}{entry.name}", converters, route + str(entry.pattern)


class AddressSweepTests(MappingCase):
    def setUp(self):
        super().setUp()
        # The first company's names, which nobody else may ever see.
        self.company.name = "Quillfern Holdings"
        self.company.save(update_fields=["name"])
        self.secret = self.employee("Zorvexa", "Z-1")
        schedule.create_holiday(actor=self.admin, company_id=self.company.pk, values={
            "holiday_date": datetime.date(2026, 12, 16), "name": "Zorvexa Day"})
        self.upload("user uid=1\tcardno=\tpin=Z-1\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
                    "\tname=Zorvexa\tprivilege=0\tdisable=0\tverify=0\n")
        # The outsider: the administrator of a second company.
        from tenants.services import onboard_company

        from accounts.models import CompanyMembership, User

        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        self.outsider = User.objects.create_user(email="admin@other.test", password="pw-12345678")
        CompanyMembership.all_objects.create(company=other, user=self.outsider,
                                             role="company_admin", status="active")
        self.client.force_login(self.outsider)
        session = self.client.session
        session["active_company_id"] = other.pk
        session.save()

    def values(self, converter):
        if isinstance(converter, EmployeeConverter):
            return [self.secret.public_id, self.ajay.public_id]
        if isinstance(converter, UUIDConverter):
            return [self.device.public_id, self.secret.public_id]
        if isinstance(converter, IntConverter):
            return [1, 2, 3]
        return []

    def test_no_page_shows_another_companys_rows(self):
        checked, problems = 0, []
        for name, converters, route in _patterns():
            options = [self.values(converter) for converter in converters.values()]
            if not all(options):
                continue
            # The same position in every list: enough to hit each table's rows.
            for combo in itertools.islice(zip(*options), 3):
                try:
                    url = reverse(name, kwargs=dict(zip(converters, combo)))
                except Exception:  # noqa: BLE001 - an id the address cannot carry
                    continue
                response = self.client.get(url)
                checked += 1
                body = response.content.decode(errors="ignore") if response.status_code == 200 else ""
                if response.status_code >= 500:
                    problems.append(f"{url}: {response.status_code}")
                elif any(marker in body for marker in MARKERS):
                    problems.append(f"{url}: shows the other company's data")
        self.assertGreater(checked, 100)        # 264 when written
        self.assertEqual(problems, [])

    def test_a_plain_number_is_not_an_employee_address(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()
        self.assertEqual(self.client.get(f"/organization/employees/{self.secret.pk}/").status_code,
                         404)
        page = self.client.get(reverse("organization:employee_detail", args=[self.secret.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Zorvexa")        # the sweep's marker is on the page
        self.assertIn(str(self.secret.public_id), page.request["PATH_INFO"])

    def test_another_companys_employee_is_not_found(self):
        url = reverse("organization:employee_detail", args=[self.secret.public_id])
        self.assertEqual(self.client.get(url).status_code, 404)
