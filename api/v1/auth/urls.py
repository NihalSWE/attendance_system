from django.urls import path

from api.v1.auth import views

urlpatterns = [
    path("auth/login", views.LoginView.as_view(), name="auth_login"),
    path("auth/login/two-step", views.LoginTwoStepView.as_view(), name="auth_login_two_step"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth_refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth_logout"),
    path("auth/web/csrf", views.WebCsrfView.as_view(), name="auth_web_csrf"),
    path("auth/web/login", views.WebLoginView.as_view(), name="auth_web_login"),
    path("auth/web/login/two-step", views.WebLoginTwoStepView.as_view(),
         name="auth_web_login_two_step"),
    path("auth/web/refresh", views.WebRefreshView.as_view(), name="auth_web_refresh"),
    path("auth/me", views.MeView.as_view(), name="auth_me"),
    path("auth/sessions", views.SessionListView.as_view(), name="auth_sessions"),
    path("auth/sessions/sign-out-others", views.SignOutOthersView.as_view(),
         name="auth_sessions_sign_out_others"),
    path("auth/sessions/<str:session_id>", views.SessionEndView.as_view(), name="auth_session"),
    path("auth/password/change", views.PasswordChangeView.as_view(), name="auth_password_change"),
    path("auth/password/forgot", views.PasswordForgotView.as_view(), name="auth_password_forgot"),
    path("auth/password/reset", views.PasswordResetView.as_view(), name="auth_password_reset"),
    path("auth/two-step/setup", views.TwoStepSetupView.as_view(), name="auth_two_step_setup"),
    path("auth/two-step/confirm", views.TwoStepConfirmView.as_view(),
         name="auth_two_step_confirm"),
    path("auth/two-step/disable", views.TwoStepDisableView.as_view(),
         name="auth_two_step_disable"),
    path("auth/two-step/recovery-codes", views.RecoveryCodesView.as_view(),
         name="auth_two_step_recovery_codes"),
    path("auth/signature-test", views.SignatureTestView.as_view(), name="auth_signature_test"),
    path("company/sessions", views.CompanySessionListView.as_view(), name="company_sessions"),
    path("company/sessions/<str:session_id>", views.CompanySessionEndView.as_view(),
         name="company_session"),
]
