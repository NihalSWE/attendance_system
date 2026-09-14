"""The supported-hardware catalogue: SenseFace 3A (plan step A16)."""

from importlib import import_module

from django.apps import apps
from django.test import TestCase

from common.tenant import use_company
from devices.forms import BiometricDeviceForm
from devices.models import DeviceModel
from tenants.services import onboard_company

seeding = import_module("devices.migrations.0004_seed_senseface_3a")


class SenseFace3ATests(TestCase):
    def test_the_migration_adds_it_once(self):
        seeding.seed(apps, None)
        seeding.seed(apps, None)
        model = DeviceModel.objects.get(vendor__code="zkteco", model_code="senseface-3a")
        self.assertEqual((model.name, model.protocol), ("SenseFace 3A", "adms_push"))
        self.assertEqual(model.vendor.adapter_key, "zkteco_adms_push")
        self.assertEqual(
            DeviceModel.objects.filter(model_code="senseface-3a").count(), 1
        )
        model.full_clean()  # the capability keys are the known ones

    def test_it_can_be_chosen_when_registering_a_device(self):
        seeding.seed(apps, None)
        company = onboard_company(code="CAT", slug="cat", name="Catalogue Ltd")
        with use_company(company):
            choices = BiometricDeviceForm().fields["device_model"].queryset
            self.assertTrue(choices.filter(model_code="senseface-3a").exists())
