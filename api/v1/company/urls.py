from django.urls import path

from api.v1.company import views

urlpatterns = [
    path("company", views.CompanyView.as_view(), name="company"),
    path("company/logo", views.CompanyLogoView.as_view(), name="company_logo"),
    path("company/mail-settings", views.MailSettingsView.as_view(), name="company_mail"),
    path("company/mail-settings/test", views.MailTestView.as_view(), name="company_mail_test"),
    path("branches", views.BranchListView.as_view(), name="branches"),
    path("branches/<int:branch_id>", views.BranchDetailView.as_view(), name="branch"),
    path("branches/<int:branch_id>/status", views.BranchStatusView.as_view(),
         name="branch_status"),
    path("departments", views.DepartmentListView.as_view(), name="departments"),
    path("departments/copy", views.DepartmentCopyView.as_view(), name="departments_copy"),
    path("departments/<int:department_id>", views.DepartmentDetailView.as_view(),
         name="department"),
    path("departments/<int:department_id>/status", views.DepartmentStatusView.as_view(),
         name="department_status"),
    path("designations", views.DesignationListView.as_view(), name="designations"),
    path("designations/<int:designation_id>", views.DesignationDetailView.as_view(),
         name="designation"),
    path("designations/<int:designation_id>/status", views.DesignationStatusView.as_view(),
         name="designation_status"),
]
