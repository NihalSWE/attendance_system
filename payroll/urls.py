from django.urls import path

from payroll import views

app_name = "payroll"

urlpatterns = [
    path("", views.payroll_home, name="payroll_home"),
    path("generate/", views.payroll_generate, name="payroll_generate"),
    path("finalise/", views.payroll_finalise, name="payroll_finalise"),
    path("finalise/undo/", views.payroll_reopen, name="payroll_reopen"),
    path("settings/", views.salary_settings, name="salary_settings"),
    path("settings/penalties/add/", views.penalty_rule_create, name="penalty_rule_create"),
    path("settings/penalties/<int:pk>/change/", views.penalty_rule_change, name="penalty_rule_change"),
    path("settings/penalties/<int:pk>/stop/", views.penalty_rule_stop, name="penalty_rule_stop"),
    path("penalties/<int:pk>/waive/", views.penalty_waive, name="penalty_waive"),
    path("payslips/<int:pk>/", views.payslip, name="payslip"),
    path("overtime/", views.overtime_list, name="overtime_list"),
    path("overtime/<int:pk>/", views.overtime_decide, name="overtime_decide"),
    path("overtime/<int:pk>/undo/", views.overtime_undo, name="overtime_undo"),
]
