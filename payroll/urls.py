from django.urls import path

from payroll import views

app_name = "payroll"

urlpatterns = [
    path("", views.payroll_home, name="payroll_home"),
    path("generate/", views.payroll_generate, name="payroll_generate"),
    path("payslips/<int:pk>/", views.payslip, name="payslip"),
]
