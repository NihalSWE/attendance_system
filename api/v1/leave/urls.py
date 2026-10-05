from django.urls import path

from api.v1.leave import views

urlpatterns = [
    path("leave/types", views.LeaveTypeListView.as_view(), name="leave_types"),
    path("leave/types/defaults", views.LeaveTypeDefaultsView.as_view(),
         name="leave_type_defaults"),
    path("leave/types/<int:leave_type_id>", views.LeaveTypeView.as_view(), name="leave_type"),
    path("leave/types/<int:leave_type_id>/status", views.LeaveTypeStatusView.as_view(),
         name="leave_type_status"),
    path("leave/policies", views.PolicyListView.as_view(), name="leave_policies"),
    path("leave/policies/<int:policy_id>", views.PolicyView.as_view(), name="leave_policy"),
    path("leave/policies/<int:policy_id>/status", views.PolicyStatusView.as_view(),
         name="leave_policy_status"),
    path("leave/policies/<int:policy_id>/versions", views.PolicyVersionsView.as_view(),
         name="leave_policy_versions"),
    path("leave/policies/<int:policy_id>/versions/<int:version_id>",
         views.PolicyVersionView.as_view(), name="leave_policy_version"),
    path("leave/balances", views.BalanceListView.as_view(), name="leave_balances"),
    path("leave/records", views.RecordListView.as_view(), name="leave_records"),
    path("leave/records/<int:leave_id>", views.RecordView.as_view(), name="leave_record"),
    path("leave/records/<int:leave_id>/amend", views.AmendView.as_view(),
         name="leave_record_amend"),
    path("leave/records/<int:leave_id>/cancel", views.CancelView.as_view(),
         name="leave_record_cancel"),
    path("leave/records/<int:leave_id>/document", views.DocumentView.as_view(),
         name="leave_record_document"),
    path("leave/requests", views.RequestListView.as_view(), name="leave_requests"),
    path("leave/requests/<int:request_id>", views.RequestView.as_view(), name="leave_request"),
    path("leave/requests/<int:request_id>/decide", views.DecideView.as_view(),
         name="leave_request_decide"),
]
