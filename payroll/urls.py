from django.urls import path

from payroll import views

app_name = "payroll"

urlpatterns = [
    path("", views.payroll_home, name="payroll_home"),
    path("generate/", views.payroll_generate, name="payroll_generate"),
    # Draft -> submit -> approve (finalise) -> undo; or send a submitted month back.
    path("submit/", views.payroll_submit, name="payroll_submit"),
    path("finalise/", views.payroll_finalise, name="payroll_finalise"),
    path("return/", views.payroll_return, name="payroll_return"),
    path("finalise/undo/", views.payroll_reopen, name="payroll_reopen"),
    path("settings/", views.salary_settings, name="salary_settings"),
    path("components/", views.component_list, name="component_list"),
    path("components/<int:pk>/edit/", views.component_edit, name="component_edit"),
    path("components/<int:pk>/status/", views.component_status, name="component_status"),
    path("settings/penalties/add/", views.penalty_rule_create, name="penalty_rule_create"),
    path("settings/penalties/<int:pk>/change/", views.penalty_rule_change, name="penalty_rule_change"),
    path("settings/penalties/<int:pk>/stop/", views.penalty_rule_stop, name="penalty_rule_stop"),
    path("penalties/<int:pk>/waive/", views.penalty_waive, name="penalty_waive"),
    path("payslips/<int:pk>/", views.payslip, name="payslip"),
    path("payslips/<int:pk>/adjustments/add/", views.payslip_adjustment_add, name="payslip_adjustment_add"),
    path("adjustments/<int:pk>/remove/", views.payslip_adjustment_remove, name="payslip_adjustment_remove"),
    path("overtime/", views.overtime_list, name="overtime_list"),
    path("overtime/<int:pk>/", views.overtime_decide, name="overtime_decide"),
    path("overtime/<int:pk>/undo/", views.overtime_undo, name="overtime_undo"),
]
