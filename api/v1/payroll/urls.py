from django.urls import path

from api.v1.payroll import views

MONTH = "payroll/months/<int:year>/<int:month>"

urlpatterns = [
    path(MONTH, views.MonthView.as_view(), name="payroll_month"),
    path(f"{MONTH}/payslips", views.PayslipListView.as_view(), name="payroll_month_payslips"),
    path(f"{MONTH}/generate", views.GenerateView.as_view(), name="payroll_generate"),
    path(f"{MONTH}/submit", views.SubmitView.as_view(), name="payroll_submit"),
    path(f"{MONTH}/approve", views.ApproveView.as_view(), name="payroll_approve"),
    path(f"{MONTH}/send-back", views.SendBackView.as_view(), name="payroll_send_back"),
    path(f"{MONTH}/reopen", views.ReopenView.as_view(), name="payroll_reopen"),
    path("payroll/payslips/<int:payslip_id>", views.PayslipView.as_view(), name="payslip"),
    path("payroll/payslips/<int:payslip_id>/pdf", views.PayslipPdfView.as_view(),
         name="payslip_pdf"),
    path("payroll/payslips/<int:payslip_id>/email", views.PayslipEmailView.as_view(),
         name="payslip_email"),
    path("payroll/payslips/<int:payslip_id>/adjustments", views.AdjustmentAddView.as_view(),
         name="payslip_adjustments"),
    path("payroll/payslips/<int:payslip_id>/corrections", views.CorrectionAddView.as_view(),
         name="payslip_corrections"),
    path("payroll/adjustments/<int:adjustment_id>", views.AdjustmentRemoveView.as_view(),
         name="payroll_adjustment"),
    path("payroll/penalties/<int:penalty_id>/waive", views.PenaltyWaiveView.as_view(),
         name="penalty_waive"),
    path("payroll/penalties/<int:penalty_id>/unwaive", views.PenaltyUnwaiveView.as_view(),
         name="penalty_unwaive"),
    path("payroll/overtime", views.OvertimeListView.as_view(), name="overtime"),
    path("payroll/overtime/<int:day_id>", views.OvertimeDayView.as_view(), name="overtime_day"),
    path("payroll/overtime/<int:day_id>/decide", views.OvertimeDecideView.as_view(),
         name="overtime_decide"),
    path("payroll/overtime/<int:day_id>/undo", views.OvertimeUndoView.as_view(),
         name="overtime_undo"),
]
