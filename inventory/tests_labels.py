"""Phase 2: labels and scanning.

"Done when" a printed label scans back to the right item + batch + expiry +
quantity: ``LabelDecodeTests`` renders the PDF at a label printer's 203 dpi
and reads it with a barcode decoder (when zxing-cpp is installed), and
``LookupTests`` checks every kind of scan resolves.
"""

import datetime
import re
import unittest

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import label_pdf, labels, lookup, masters
from inventory.models import Batch, BarcodeLabel, LabelPrint, Location
from inventory.testing import DAY, D, InventoryFixture

GS = "\x1d"


class LabelFixture(InventoryFixture):
    def setUp(self):
        super().setUp()
        self.batch = Batch.all_objects.create(
            company=self.company, item=self.sugar, batch_no="251001-01", source="opening",
            mfg_date=datetime.date(2026, 9, 20), expiry_date=datetime.date(2027, 9, 19))
        self.bin = masters.create(masters.LOCATION, actor=self.owner, company_id=self.company.pk,
                                  values={"warehouse": self.rms, "code": "R1-S1", "description": ""})
        masters.add_pack_unit(actor=self.owner, company_id=self.company.pk, item_id=self.sugar.pk,
                              values={"uom": self.bag, "factor": D("50"), "barcode": "18941234567891"})

    def packages(self, count=2, qty="50", uom=None, **extra):
        return labels.package_labels(
            actor=self.owner, company_id=self.company.pk, item_id=self.sugar.pk,
            batch_id=self.batch.pk, uom_id=(uom or self.kg).pk, quantity=D(qty), count=count,
            warehouse_id=self.rms.pk, location_id=self.bin.pk, **extra)


class LabelServiceTests(LabelFixture, TestCase):
    def test_codes_follow_on_and_carry_the_company(self):
        first, second = self.packages()
        self.assertEqual((first.code, second.code),
                         (f"P{self.company.pk}-000001", f"P{self.company.pk}-000002"))
        [bin_label] = labels.location_labels(actor=self.owner, company_id=self.company.pk,
                                             location_ids=[self.bin.pk])
        self.assertEqual(bin_label.code, f"L{self.company.pk}-000001")

    def test_item_label_is_its_barcode_or_code_once(self):
        label = labels.item_label(actor=self.owner, company_id=self.company.pk, item_id=self.sugar.pk)
        self.assertEqual(label.code, "RM-0001")
        again = labels.item_label(actor=self.owner, company_id=self.company.pk, item_id=self.sugar.pk)
        self.assertEqual(label.pk, again.pk)

    def test_one_label_per_bin_and_batch(self):
        a = labels.location_labels(actor=self.owner, company_id=self.company.pk, location_ids=[self.bin.pk])
        b = labels.location_labels(actor=self.owner, company_id=self.company.pk, location_ids=[self.bin.pk])
        self.assertEqual(a[0].pk, b[0].pk)
        c = labels.batch_label(actor=self.owner, company_id=self.company.pk, batch_id=self.batch.pk)
        d = labels.batch_label(actor=self.owner, company_id=self.company.pk, batch_id=self.batch.pk)
        self.assertEqual(c.pk, d.pk)

    def test_package_rules(self):
        with self.assertRaisesMessage(ValidationError, "counted in KG or its pack units"):
            self.packages(uom=self.pcs)
        with self.assertRaisesMessage(ValidationError, "Between 1 and 500"):
            self.packages(count=0)
        with self.assertRaisesMessage(ValidationError, "names its batch"):
            labels.package_labels(actor=self.owner, company_id=self.company.pk,
                                  item_id=self.sugar.pk, uom_id=self.kg.pk, quantity=D("5"))

    def test_print_reprint_needs_a_reason_and_void_never_prints(self):
        [label] = self.packages(count=1)
        pdf = labels.print_labels(actor=self.owner, company_id=self.company.pk,
                                  label_ids=[label.pk], copies=2)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), 2)
        with self.assertRaisesMessage(ValidationError, "say why they are printed again"):
            labels.print_labels(actor=self.owner, company_id=self.company.pk, label_ids=[label.pk])
        labels.print_labels(actor=self.owner, company_id=self.company.pk, label_ids=[label.pk],
                            reason="Torn")
        label.refresh_from_db()
        self.assertEqual(label.printed_count, 3)
        self.assertEqual(list(LabelPrint.all_objects.filter(label=label).order_by("pk")
                              .values_list("reprint", "reason")), [(False, ""), (True, "Torn")])
        labels.void_label(actor=self.owner, company_id=self.company.pk, label_id=label.pk,
                          reason="Stuck on the wrong bag")
        with self.assertRaisesMessage(ValidationError, "Void labels are not printed"):
            labels.print_labels(actor=self.owner, company_id=self.company.pk, label_ids=[label.pk],
                                reason="x")

    def test_bin_labels_need_the_permission_in_that_branch(self):
        user = self.member("keeper@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="K")
        with self.assertRaises(PermissionDenied):
            labels.location_labels(actor=user, company_id=self.company.pk, location_ids=[self.bin.pk])
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.labels", branch_ids=[self.branch.pk])
        labels.location_labels(actor=user, company_id=self.company.pk, location_ids=[self.bin.pk])

    def test_every_size_renders(self):
        made = self.packages(count=1) + [labels.item_label(actor=self.owner, company_id=self.company.pk,
                                                           item_id=self.sugar.pk)]
        for small in ("50x25", "40x30", "60x40"):
            for large in ("100x50", "100x75", "75x50"):
                pdf = label_pdf.render(made, small=small, large=large, gs1=True)
                self.assertTrue(pdf.startswith(b"%PDF"), (small, large))


class LookupTests(LabelFixture, TestCase):
    def find(self, code, today=DAY):
        return lookup.lookup(self.company.pk, code, today=today)

    def test_package_by_code_and_by_its_qr(self):
        [label] = self.packages(count=1, qty="2", uom=self.bag)
        for scanned in (label.code, label_pdf.qr_payload(label)):
            found = self.find(scanned)
            self.assertTrue(found["found"], scanned)
            self.assertEqual((found["kind"], found["item"]["code"], found["batch"]["batch_no"],
                              found["batch"]["expiry_date"], found["quantity"], found["uom"],
                              found["quantity_base"], found["location"]["code"]),
                             ("package", "RM-0001", "251001-01", "2027-09-19", "2", "BAG", "100", "R1-S1"))

    def test_item_pack_and_bin(self):
        self.assertEqual(self.find("rm-0001")["kind"], "item")
        pack = self.find("18941234567891")
        self.assertEqual((pack["kind"], pack["uom"], pack["quantity_base"]), ("pack", "BAG", "50"))
        [bin_label] = labels.location_labels(actor=self.owner, company_id=self.company.pk,
                                             location_ids=[self.bin.pk])
        self.assertEqual(self.find(bin_label.code)["message"], "Bin R1-S1 in RMS")

    def test_gs1_in_every_form_a_scanner_sends(self):
        self.sugar.barcode = "8941234567892"
        self.sugar.save()
        for scanned in (
                # Symbology prefix ]C1, fixed fields, then the batch last.
                "]C1" + "0108941234567892" + "17270919" + "3100000050" + "10251001-01",
                # A leading group separator; the batch ends at the next separator.
                GS + "0108941234567892" + "17270919" + "10251001-01" + GS + "3125",
                # The written form under the bars.
                "(01)08941234567892(17)270919(10)251001-01(3100)000050"):
            found = self.find(scanned)
            self.assertTrue(found["found"], scanned)
            self.assertEqual(found["item"]["code"], "RM-0001", scanned)
            self.assertEqual(found["batch"]["batch_no"], "251001-01", scanned)
        self.assertEqual(self.find("(01)08941234567892(3100)000050")["quantity"], "50")

    def test_warnings_and_refusals(self):
        self.batch.status = Batch.Status.QUARANTINE
        self.batch.save()
        [label] = self.packages(count=1)
        self.assertIn("quarantine", self.find(label.code)["warnings"][0])
        self.batch.status = Batch.Status.RELEASED
        self.batch.save()
        self.assertIn("expired", self.find(label.code, today=datetime.date(2028, 1, 1))["warnings"][0])
        labels.void_label(actor=self.owner, company_id=self.company.pk, label_id=label.pk, reason="Torn off")
        void = self.find(label.code)
        self.assertEqual((void["found"], void["message"]), (False, f"Label {label.code} is void: Torn off"))
        self.assertFalse(self.find("NOPE-1")["found"])
        self.assertEqual(self.find(f"P{self.company.pk + 999}-000001")["found"], False)

    def test_another_companys_label_is_named(self):
        from tenants import platform_services

        other = platform_services.create_platform_company(actor=self.root, values={"name": "Other"})
        found = self.find(f"P{other.pk}-000001")
        self.assertEqual(found["message"], f"Label P{other.pk}-000001 belongs to another company.")


class LabelPageTests(LabelFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_pages_open(self):
        for name in ("inventory:labels", "inventory:scan"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        page = self.client.get(reverse("inventory:item_detail", args=[self.sugar.pk]))
        self.assertContains(page, "Print item label")
        self.assertContains(self.client.get(reverse("inventory:scan")), 'data-scan-box')

    def test_package_labels_by_posting_the_form(self):
        url = reverse("inventory:package_labels", args=[self.sugar.pk])
        self.assertContains(self.client.get(url, {"batch": self.batch.pk}), "Package labels")
        response = self.client.post(url + "?source_type=opening_stock_line&source_id=9", {
            "batch": self.batch.pk, "count": "3", "quantity": "50", "uom": self.kg.pk,
            "warehouse": self.rms.pk, "location": self.bin.pk})
        self.assertEqual(response["Content-Type"], "application/pdf")
        made = BarcodeLabel.all_objects.filter(kind="package")
        self.assertEqual(made.count(), 3)
        self.assertEqual({(m.source_type, m.source_id, m.printed_count) for m in made},
                         {("opening_stock_line", 9, 1)})
        refused = self.client.post(url, {"batch": self.batch.pk, "count": "1", "quantity": "1",
                                         "uom": self.kg.pk, "warehouse": self.fgw.pk,
                                         "location": self.bin.pk})
        self.assertContains(refused, "This bin is in another store")

    def test_print_reprint_and_void_from_the_register(self):
        response = self.client.post(reverse("inventory:item_label_print", args=[self.sugar.pk]),
                                    {"copies": "1"})
        self.assertEqual(response["Content-Type"], "application/pdf")
        label = BarcodeLabel.all_objects.get(kind="item")
        refused = self.client.post(reverse("inventory:label_print", args=[label.pk]),
                                   {"copies": "1", "next": reverse("inventory:labels")})
        self.assertRedirects(refused, reverse("inventory:labels"))
        again = self.client.post(reverse("inventory:label_print", args=[label.pk]),
                                 {"copies": "2", "reason": "Faded"})
        self.assertEqual(again["Content-Type"], "application/pdf")
        self.client.post(reverse("inventory:label_void", args=[label.pk]), {"reason": "Replaced"})
        label.refresh_from_db()
        self.assertEqual((label.status, label.printed_count), ("void", 3))

    def test_bin_labels_for_a_store(self):
        response = self.client.post(reverse("inventory:bin_labels_print") + f"?store={self.rms.pk}",
                                    {"copies": "1"})
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(BarcodeLabel.all_objects.filter(kind="location", location=self.bin).exists())

    def test_scan_lookup_json(self):
        [label] = self.packages(count=1)
        data = self.client.get(reverse("inventory:scan_lookup"), {"code": label.code}).json()
        self.assertEqual((data["found"], data["quantity"]), (True, "50"))

    def test_viewing_is_not_printing(self):
        user = self.member("viewer@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="V")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.view", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("inventory:scan_lookup"), {"code": "RM-0001"}).status_code, 200)
        page = self.client.get(reverse("inventory:item_detail", args=[self.sugar.pk]))
        self.assertNotContains(page, "Print item label")
        response = self.client.post(reverse("inventory:item_label_print", args=[self.sugar.pk]),
                                    {"copies": "1"})
        self.assertEqual(response.status_code, 403)

    def test_without_the_module_the_lookup_answers_nothing(self):
        self.enable_module(False)
        self.assertEqual(self.client.get(reverse("inventory:scan_lookup"), {"code": "RM-0001"}).status_code, 404)


try:
    import pypdfium2
    import zxingcpp
except ImportError:  # optional: the decoder is not in requirements.txt
    zxingcpp = None


@unittest.skipUnless(zxingcpp, "zxing-cpp and pypdfium2 are not installed")
class LabelDecodeTests(LabelFixture, TestCase):
    """The PDF rendered as a 203 dpi printer prints it, read back by a decoder."""

    def read(self, pdf):
        document = pypdfium2.PdfDocument(pdf)
        return [{(r.format.name, r.text) for r in zxingcpp.read_barcodes(document[i].render(scale=203 / 72).to_pil())}
                for i in range(len(document))]

    def test_every_label_scans_back(self):
        self.sugar.barcode = "8941234567892"
        self.sugar.save()
        [package] = self.packages(count=1)
        [bin_label] = labels.location_labels(actor=self.owner, company_id=self.company.pk,
                                             location_ids=[self.bin.pk])
        item_label = labels.item_label(actor=self.owner, company_id=self.company.pk, item_id=self.bearing.pk)
        rows = BarcodeLabel.all_objects.filter(pk__in=[package.pk, bin_label.pk, item_label.pk]).order_by("pk")
        for small in ("50x25", "40x30", "60x40"):
            pages = self.read(label_pdf.render(list(rows), small=small, large="100x50", gs1=True))
            texts = [{text for _f, text in page} for page in pages]
            self.assertIn(package.code, texts[0], small)
            self.assertIn(bin_label.code, texts[1], small)
            self.assertIn("SP-0001", texts[2], small)
            for page in texts:
                for text in page:
                    self.assertTrue(lookup.lookup(self.company.pk, text, today=DAY)["found"], text)
        package_page = texts[0]
        self.assertIn("(01)08941234567892(17)270919(3100)000050(10)251001-01", package_page)
