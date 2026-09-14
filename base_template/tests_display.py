"""Display helpers: amounts with thousands separators, and dependent fields."""

from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from base_template.templatetags.money import money


class MoneyTests(SimpleTestCase):
    def test_amounts_get_thousands_separators(self):
        self.assertEqual(money(Decimal("30000")), "30,000.00")
        self.assertEqual(money("1234567.5"), "1,234,567.50")
        self.assertEqual(money(Decimal("-2500.456")), "-2,500.46")
        self.assertEqual(money(0), "0.00")

    def test_blanks_and_text_pass_through(self):
        self.assertEqual(money(None), "")
        self.assertEqual(money(""), "")
        self.assertEqual(money("n/a"), "n/a")

    def test_the_template_filter(self):
        html = Template("{% load money %}{{ amount|money }}").render(Context({"amount": Decimal("98765.4")}))
        self.assertEqual(html, "98,765.40")


class DependentFieldTests(SimpleTestCase):
    def test_penalty_rule_fields_say_when_they_apply(self):
        from payroll.forms import PenaltyRuleForm

        form = PenaltyRuleForm()
        self.assertEqual(
            form.fields["required_occurrences"].widget.attrs["data-show-when"],
            "occurrence_mode:within_period,consecutive_workdays",
        )
        self.assertNotIn("absence", form.fields["threshold_minutes"].widget.attrs["data-show-when"])
        self.assertIn("fixed_amount=Amount to deduct", form.fields["deduction_value"].widget.attrs["data-label-when"])

    def test_the_fixed_days_field_only_for_a_fixed_number_of_days(self):
        from payroll.forms import SalaryRulesForm

        attrs = SalaryRulesForm().fields["monthly_divisor"].widget.attrs
        self.assertEqual(attrs["data-show-when"], "monthly_proration_method:fixed_30")

    def test_branches_only_for_a_branch_manager(self):
        from organization.employee_edit_forms import GiveLoginForm, LoginRoleForm

        for form in (GiveLoginForm(), LoginRoleForm()):
            with self.subTest(form=type(form).__name__):
                self.assertEqual(form.fields["login_branches"].widget.attrs["data-show-when"], "login_role:manager")
