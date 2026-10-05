"""Logging in and everything around it (docs/api/00-PLAN.md, Part 2;
the guide: docs/api/02-authentication.md)."""

import time

from django.middleware.csrf import get_token
from rest_framework.response import Response

from accounts.models import CompanyMembership
from accounts.services import get_active_memberships
from api.core import email_codes, logins, signing
from api.core.auth import ACCESS_COOKIE, REFRESH_COOKIE, cookie_options, enforce_csrf
from api.core.crypto import decrypt, same
from api.core.docs import PATH, Param, endpoint
from api.core.errors import ApiError
from api.core.permissions import IsCompanyAdmin, IsPerson, Public
from api.core.views import ApiView
from api.models import ApiKey, ApiSession, UsedNonce
from api.v1.auth import serializers as s

AREA = "auth"
LOGIN_ERRORS = ["validation_error", "unknown_field", "invalid_credentials", "login_locked",
                "platform_login_not_allowed", "permission_denied", "rate_limited", "server_error"]
TOKENS_EXAMPLE = {
    "two_step_required": False, "session_id": "ses_q7Hc2LkP9xWm",
    "access_token": "at_Xb3k…", "access_expires_in": 600, "refresh_token": "rt_9QfZ…",
    "refresh_expires_at": "2026-11-03T04:15:00Z", "signing_secret": "ss_hT6v…",
    "two_step_setup_required": False,
}
SESSION_EXAMPLE = {"id": "ses_q7Hc2LkP9xWm", "client_type": "mobile",
                   "device_name": "Rahim's Samsung A54", "created_at": "2026-10-04T10:15:00+06:00",
                   "last_used_at": "2026-10-04T11:02:00+06:00", "last_ip": "103.110.25.4",
                   "current": True}


def _session_row(session, current):
    return {"id": session.public_id, "client_type": session.client_type,
            "device_name": session.device_name, "created_at": session.created_at,
            "last_used_at": session.last_used_at, "last_ip": session.last_ip,
            "current": current is not None and session.pk == current.pk}


def _app_signature(request, session):
    """An app's refresh must be signed with its session secret."""
    if session.client_type not in (ApiSession.ClientType.MOBILE, ApiSession.ClientType.DESKTOP):
        raise ApiError("invalid_token", "This is a web session: refresh it with "
                                        "POST /api/v1/auth/web/refresh.")
    if not signing.has_headers(request):
        raise ApiError("signature_required")
    if (request.META.get("HTTP_X_KEY_ID") or "").strip() != session.public_id:
        raise ApiError("invalid_signature",
                       "X-Key-Id must be this session's id (the session_id from login).")
    signing.verify(request, session.public_id, [decrypt(session.signing_secret_encrypted)])


def _web_only(request, session):
    if session.client_type != ApiSession.ClientType.WEB:
        raise ApiError("invalid_token", "This is an app session: refresh it with "
                                        "POST /api/v1/auth/refresh.")


def _set_cookies(response, issued):
    options = cookie_options()
    response.set_cookie(ACCESS_COOKIE, issued["access_token"], path="/api/",
                        max_age=issued["access_expires_in"], **options)
    response.set_cookie(REFRESH_COOKIE, issued["refresh_token"], path="/api/v1/auth/web/",
                        max_age=logins.refresh_days() * 86400, **options)


def _clear_cookies(response):
    response.delete_cookie(ACCESS_COOKIE, path="/api/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/web/")


def _web_body(issued):
    if issued.get("two_step_required"):
        return issued
    return {"two_step_required": False, "session_id": issued["session_id"],
            "access_expires_in": issued["access_expires_in"],
            "two_step_setup_required": issued["two_step_setup_required"]}


# --- logging in (apps) -------------------------------------------------------------


class LoginView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-login", area=AREA, title="Log in (apps)",
        summary="Email and password in; tokens and a signing secret out.",
        what_it_does=[
            "Checks the email and password (the same login as the panels).",
            "Answers the session's tokens and its signing secret - or, when two-step "
            "login is on, a challenge for the authenticator code.",
            "Locks the login for a while after repeated wrong passwords.",
        ],
        description=(
            "For mobile and desktop apps. A browser frontend uses POST /api/v1/auth/web/login.\n\n"
            "On success keep three things in the system's secure storage: the access_token "
            "(sent as Authorization: Bearer on every request, valid 10 minutes), the "
            "refresh_token (swapped for a new pair before the access token expires, valid 30 "
            "days, works once) and the signing_secret (signs every request; never sent again). "
            "session_id is the X-Key-Id of your signed requests.\n\n"
            "When two-step login is on, the answer is only the challenge fields: send the "
            "challenge and a code to POST /api/v1/auth/login/two-step within 10 minutes. "
            "methods says which codes work: the authenticator app, a code by email (for an "
            "app user the backup - ask for it with POST /api/v1/auth/login/two-step/email-code; "
            "for a login set up with email codes it is sent already, see email_sent_to) and a "
            "recovery code.\n\n"
            "An owner or company administrator without two-step login gets "
            "two_step_setup_required: true - until they set it up, only two-step setup and "
            "logout work. 5 wrong passwords in 15 minutes lock the login for 15 minutes (10 in "
            "an hour: one hour). The platform owner keeps its panel and cannot log in here."
        ),
        roles=["Anyone with a company login"], auth="public",
        request=s.LoginSerializer, response=s.TokensSerializer,
        request_example={"email": "rahim@example.com", "password": "••••••••",
                         "client_type": "mobile", "device_name": "Rahim's Samsung A54"},
        response_example=TOKENS_EXAMPLE, errors=LOGIN_ERRORS,
    )
    def post(self, request):
        data = s.LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.login(request, **data.validated_data))


class LoginTwoStepView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-login-two-step", area=AREA, title="Log in: the two-step code (apps)",
        summary="The challenge and the authenticator code in; the tokens out.",
        what_it_does=[
            "Finishes a login that answered two_step_required: true.",
            "Takes the 6-digit authenticator code, the code sent by email, or one recovery code.",
        ],
        description=(
            "Send the challenge from POST /api/v1/auth/login with the current code from the "
            "authenticator app (or the code emailed by POST /api/v1/auth/login/two-step/email-code), "
            "within 10 minutes. Every code works once. Wrong codes count toward the same lockout as "
            "wrong passwords. The answer is the same as a login without two-step."
        ),
        roles=["Anyone with a company login"], auth="public",
        request=s.TwoStepLoginSerializer, response=s.TokensSerializer,
        request_example={"challenge": "ch_Jk3…", "code": "492013"},
        response_example=TOKENS_EXAMPLE,
        errors=["validation_error", "unknown_field", "challenge_expired",
                "invalid_two_step_code", "login_locked", "rate_limited", "server_error"],
    )
    def post(self, request):
        data = s.TwoStepLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.login_two_step(request, **data.validated_data))


class LoginEmailCodeView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-login-two-step-email-code", area=AREA, title="Log in: send the code by email",
        summary="The challenge in; a 6-digit code goes to the login's email.",
        what_it_does=[
            "Emails a two-step code for this login - the backup when the authenticator app "
            "cannot be used.",
            "Then send that code to the two-step step, as with the app's code.",
        ],
        description=(
            "For when the app cannot be used: the phone is lost or not at hand, the app was "
            "deleted - or to send a login set up with email codes another one. Show it as "
            "\"Email me a code instead\" on the code screen. Works for apps "
            "and browser frontends alike: finish with POST /api/v1/auth/login/two-step or "
            "POST /api/v1/auth/web/login/two-step. Once in, the person can move the app to a "
            "new phone (POST /api/v1/auth/two-step/setup with this code).\n\n"
            "The code works once, for 10 minutes, and stops after 5 wrong tries. One email a "
            "minute. It is sent through the company's mail account (Organisation -> Email "
            "settings) or the server's; with neither, the answer is email_not_available."
        ),
        roles=["Anyone with a company login, in the middle of logging in"], auth="public",
        sample_auth="none",
        request=s.ChallengeSerializer, response=s.EmailCodeSentSerializer,
        request_example={"challenge": "ch_Jk3…"},
        response_example={"email_sent_to": "r***@example.com", "email_expires_in": 600},
        errors=["validation_error", "unknown_field", "challenge_expired", "login_locked",
                "email_not_available", "email_not_sent", "rate_limited", "server_error"],
    )
    def post(self, request):
        data = s.ChallengeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.email_code_for_challenge(request, data.validated_data["challenge"]))


class RefreshView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-refresh", area=AREA, title="Refresh the tokens (apps)",
        summary="A refresh token in; a new access token and refresh token out.",
        what_it_does=[
            "Swaps the refresh token for a new pair - call it before the access token expires.",
            "Ends the whole session if an old refresh token is used again.",
        ],
        description=(
            "Sign this request with the session's signing secret (X-Key-Id is the session_id) "
            "but send no Authorization header - the access token may already have expired.\n\n"
            "Each refresh token works once: keep the new one and forget the old. If an old one "
            "is presented again, it may have been copied, so the session is ended "
            "(refresh_token_reused) and the person logs in again. One exception: sending the "
            "same refresh token again within 60 seconds counts as a retry after a lost "
            "answer, not a copy. The signing secret stays the same for the life of the session."
        ),
        roles=["The app holding the session"], auth="public", sample_auth="app-refresh",
        request=s.RefreshSerializer, response=s.TokensSerializer,
        request_example={"refresh_token": "rt_9QfZ…"},
        response_example={k: v for k, v in TOKENS_EXAMPLE.items() if k != "signing_secret"},
        errors=["validation_error", "unknown_field", "invalid_token", "session_ended",
                "refresh_token_reused", "signature_required", "missing_signature_headers",
                "invalid_signature", "timestamp_out_of_range", "replay_detected",
                "rate_limited", "server_error"],
    )
    def post(self, request):
        data = s.RefreshSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        issued = logins.refresh(request, token=data.validated_data["refresh_token"],
                                signed_by=lambda session: _app_signature(request, session))
        return Response({"two_step_required": False, **issued})


class LogoutView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "write"
    allowed_during_two_step_setup = True

    @endpoint(
        id="auth-logout", area=AREA, title="Log out",
        summary="Ends the session this request came through.",
        what_it_does=["Ends this session at once: its tokens and secret stop working.",
                      "For a browser frontend, also clears the cookies."],
        description=(
            "Ends only this session; the person's other devices stay logged in "
            "(POST /api/v1/auth/sessions/sign-out-others ends those). The app should forget "
            "its tokens and signing secret."
        ),
        roles=["A logged-in person (app or web)"],
        response=s.MessageSerializer, response_example={"detail": "Logged out."},
        errors=["not_authenticated", "invalid_token", "token_expired", "session_ended",
                "signature_required", "invalid_signature", "csrf_failed", "permission_denied",
                "rate_limited", "server_error"],
    )
    def post(self, request):
        logins.end(request.api_session, "logged out")
        response = Response({"detail": "Logged out."})
        if request.api_session.client_type == ApiSession.ClientType.WEB:
            _clear_cookies(response)
        return response


# --- logging in (browser frontend) --------------------------------------------------


class WebCsrfView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "public"

    @endpoint(
        id="auth-web-csrf", area=AREA, title="Get the CSRF token (web)",
        summary="The CSRF token a browser frontend sends with every change.",
        what_it_does=["Sets the csrftoken cookie and answers the same token.",
                      "Call it once when the frontend starts."],
        description=(
            "Browser frontends log in with cookies, which the browser sends by itself; the "
            "CSRF token proves a request really comes from your frontend. Send it as the "
            "X-CSRFToken header on every POST, PUT, PATCH and DELETE, including the login. "
            "Your frontend's address must be listed by the server (API_CORS_ORIGINS)."
        ),
        roles=["Anyone"], auth="public", sample_auth="none",
        response=s.CsrfSerializer, response_example={"csrf_token": "kJ8x…"},
        errors=["rate_limited", "server_error"],
    )
    def get(self, request):
        return Response({"csrf_token": get_token(request._request)})


class WebLoginView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-web-login", area=AREA, title="Log in (web)",
        summary="Email and password in; secure cookies set.",
        what_it_does=[
            "Checks the email and password, as POST /api/v1/auth/login does.",
            "Sets the session as Secure, HttpOnly cookies - page scripts cannot read them.",
        ],
        description=(
            "For a browser frontend (React, Vue, …). Send the X-CSRFToken header (from GET "
            "/api/v1/auth/web/csrf) and make the request with credentials included "
            '(fetch: credentials: "include"). No token is in the answer: the browser keeps the '
            "cookies and sends them by itself. Refresh before access_expires_in runs out with "
            "POST /api/v1/auth/web/refresh. Two-step login and the lockout work as for apps "
            "(POST /api/v1/auth/web/login/two-step)."
        ),
        roles=["Anyone with a company login"], auth="public", sample_auth="web",
        request=s.WebLoginSerializer, response=s.WebSessionSerializer,
        request_example={"email": "rahim@example.com", "password": "••••••••",
                         "device_name": "Chrome on the office PC"},
        response_example={"two_step_required": False, "session_id": "ses_q7Hc2LkP9xWm",
                          "access_expires_in": 600, "two_step_setup_required": False},
        errors=LOGIN_ERRORS + ["csrf_failed"],
    )
    def post(self, request):
        enforce_csrf(request)
        data = s.WebLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        issued = logins.login(request, client_type=ApiSession.ClientType.WEB,
                              **data.validated_data)
        response = Response(_web_body(issued))
        if not issued.get("two_step_required"):
            _set_cookies(response, issued)
        return response


class WebLoginTwoStepView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-web-login-two-step", area=AREA, title="Log in: the two-step code (web)",
        summary="The challenge and the authenticator code in; secure cookies set.",
        what_it_does=["Finishes a web login that answered two_step_required: true."],
        description=("As POST /api/v1/auth/login/two-step, for a browser frontend: the "
                     "session is set as cookies. Send the X-CSRFToken header."),
        roles=["Anyone with a company login"], auth="public", sample_auth="web",
        request=s.TwoStepLoginSerializer, response=s.WebSessionSerializer,
        request_example={"challenge": "ch_Jk3…", "code": "492013"},
        response_example={"two_step_required": False, "session_id": "ses_q7Hc2LkP9xWm",
                          "access_expires_in": 600, "two_step_setup_required": False},
        errors=["validation_error", "unknown_field", "challenge_expired",
                "invalid_two_step_code", "login_locked", "csrf_failed", "rate_limited",
                "server_error"],
    )
    def post(self, request):
        enforce_csrf(request)
        data = s.TwoStepLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        issued = logins.login_two_step(request, **data.validated_data)
        response = Response(_web_body(issued))
        _set_cookies(response, issued)
        return response


class WebRefreshView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-web-refresh", area=AREA, title="Refresh the session (web)",
        summary="Renews the session's cookies.",
        what_it_does=["Swaps the refresh cookie for new cookies - call it before "
                      "access_expires_in runs out."],
        description=("The refresh cookie is sent by the browser by itself (it only goes to "
                     "this address). Send the X-CSRFToken header. A refresh token used twice "
                     "ends the session, as for apps."),
        roles=["The browser holding the session"], auth="public", sample_auth="web",
        response=s.WebSessionSerializer,
        response_example={"two_step_required": False, "session_id": "ses_q7Hc2LkP9xWm",
                          "access_expires_in": 600, "two_step_setup_required": False},
        errors=["invalid_token", "session_ended", "refresh_token_reused", "csrf_failed",
                "rate_limited", "server_error"],
    )
    def post(self, request):
        enforce_csrf(request)
        token = request.COOKIES.get(REFRESH_COOKIE, "")
        if not token:
            raise ApiError("invalid_token", "No refresh cookie: log in again.")
        issued = logins.refresh(request, token=token,
                                signed_by=lambda session: _web_only(request, session))
        response = Response(_web_body({"two_step_required": False, **issued}))
        _set_cookies(response, issued)
        return response


# --- the person ------------------------------------------------------------------


class MeView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "read"
    allowed_during_two_step_setup = True

    @endpoint(
        id="auth-me", area=AREA, title="Who am I",
        summary="The logged-in person, their companies and roles, and this session.",
        what_it_does=["Says who is logged in and in which companies, with what role.",
                      "Says whether two-step login is on, and whether it must be."],
        description=("Call it after logging in to know which company ids to send as X-Company "
                     "(only needed when the login belongs to several) and what the person may "
                     "do - the role decides, exactly as in the panels."),
        roles=["A logged-in person (app or web)"],
        response=s.MeSerializer,
        response_example={
            "email": "rahim@example.com", "name": "Rahim Uddin",
            "companies": [{"id": 12, "name": "Acme Ltd", "role": "company_admin"}],
            "two_step": {"enabled": True, "method": "app", "email_backup": True,
                         "required": True},
            "session": {"id": "ses_q7Hc2LkP9xWm", "client_type": "mobile",
                        "access_expires_at": "2026-10-04T10:25:00Z"}},
        errors=["not_authenticated", "invalid_token", "token_expired", "session_ended",
                "signature_required", "invalid_signature", "timestamp_out_of_range",
                "replay_detected", "permission_denied", "rate_limited", "server_error"],
    )
    def get(self, request):
        user, session = request.user, request.api_session
        two_step = logins.two_step_on(user)
        companies = [{"id": m.company_id, "name": m.company.name, "role": m.role}
                     for m in get_active_memberships(user).select_related("company")
                     .order_by("company__name")]
        return Response(s.MeSerializer({
            "email": user.email, "name": user.get_full_name() or user.email,
            "companies": companies,
            "two_step": {"enabled": two_step is not None,
                         "method": two_step.method if two_step else None,
                         "email_backup": email_codes.available(user),
                         "required": logins.must_use_two_step(user)},
            "session": {"id": session.public_id, "client_type": session.client_type,
                        "access_expires_at": session.access_expires_at},
        }).data)


SESSION_ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
                  "signature_required", "invalid_signature", "two_step_setup_required",
                  "permission_denied", "rate_limited", "server_error"]


class SessionListView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "read"

    @endpoint(
        id="auth-sessions-list", area=AREA, title="My sessions",
        summary="Every device this login is logged in on.",
        what_it_does=["Lists the active sessions, newest use first, with device and address.",
                      "Marks the one this request came through."],
        description="Use it for a \"Where you're logged in\" screen with a sign-out button each.",
        roles=["A logged-in person (app or web)"], paginated=True,
        response=s.SessionSerializer, response_example={
            "count": 1, "next": None, "previous": None, "results": [SESSION_EXAMPLE]},
        errors=SESSION_ERRORS,
    )
    def get(self, request):
        sessions = ApiSession.objects.filter(user=request.user, state=ApiSession.State.ACTIVE) \
            .order_by("-last_used_at", "-pk")
        return self.paginated(request, [_session_row(x, request.api_session) for x in sessions],
                              s.SessionSerializer)


class SessionEndView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "write"

    @endpoint(
        id="auth-sessions-end", area=AREA, title="Sign out one of my sessions",
        summary="Ends one of this login's sessions - e.g. a lost phone.",
        what_it_does=["Ends that session at once: its tokens and secret stop working."],
        description="Only this login's own sessions. Ending the current one is the same as logging out.",
        roles=["A logged-in person (app or web)"],
        params=[Param("session_id", PATH, "string", "The session's id (ses_…), from My sessions.",
                      example="ses_q7Hc2LkP9xWm")],
        response=s.MessageSerializer, response_example={"detail": "Session ended."},
        errors=SESSION_ERRORS + ["not_found"],
    )
    def delete(self, request, session_id):
        session = ApiSession.objects.filter(user=request.user, public_id=session_id,
                                            state=ApiSession.State.ACTIVE).first()
        if session is None:
            raise ApiError("not_found")
        logins.end(session, "signed out from the sessions list")
        return Response({"detail": "Session ended."})


class SignOutOthersView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "write"

    @endpoint(
        id="auth-sessions-sign-out-others", area=AREA, title="Sign out everywhere else",
        summary="Ends every session of this login except the current one.",
        what_it_does=["Ends all the other sessions at once."],
        description="For \"I think someone has my password\": change it too.",
        roles=["A logged-in person (app or web)"],
        response=s.MessageSerializer, response_example={"detail": "3 other sessions ended."},
        errors=SESSION_ERRORS,
    )
    def post(self, request):
        others = ApiSession.objects.filter(user=request.user, state=ApiSession.State.ACTIVE) \
            .exclude(pk=request.api_session.pk)
        count = 0
        for session in others:
            logins.end(session, "signed out from another session")
            count += 1
        return Response({"detail": f"{count} other session{'s' if count != 1 else ''} ended."})


class CompanySessionListView(ApiView):
    permission_classes = [IsCompanyAdmin]
    throttle_scope = "read"
    company_required = True

    @endpoint(
        id="company-sessions-list", area=AREA, title="Staff sessions",
        summary="Every API session of the company's people.",
        what_it_does=["Lists the active sessions of everyone in the company.",
                      "For the owner or company administrator."],
        description=("Shows who is logged in through the API, on what and from where - to "
                     "sign out a lost device or someone who left."),
        roles=["Company owner or administrator"], paginated=True,
        response=s.StaffSessionSerializer, response_example={
            "count": 1, "next": None, "previous": None,
            "results": [{**SESSION_EXAMPLE, "current": False, "user_email": "karim@example.com"}]},
        errors=SESSION_ERRORS + ["validation_error"],
    )
    def get(self, request):
        sessions = ApiSession.objects.select_related("user").filter(
            state=ApiSession.State.ACTIVE,
            user__in=CompanyMembership.all_objects.filter(
                company_id=request.company_id, status=CompanyMembership.Status.ACTIVE)
            .values("user")).order_by("-last_used_at", "-pk")
        rows = [{**_session_row(x, request.api_session), "user_email": x.user.email}
                for x in sessions]
        return self.paginated(request, rows, s.StaffSessionSerializer)


class CompanySessionEndView(ApiView):
    permission_classes = [IsCompanyAdmin]
    throttle_scope = "write"
    company_required = True

    @endpoint(
        id="company-sessions-end", area=AREA, title="Sign out a staff session",
        summary="Ends one API session of someone in the company.",
        what_it_does=["Ends that session at once."],
        description="Only sessions of people in this company.",
        roles=["Company owner or administrator"],
        params=[Param("session_id", PATH, "string", "The session's id (ses_…), from Staff sessions.",
                      example="ses_q7Hc2LkP9xWm")],
        response=s.MessageSerializer, response_example={"detail": "Session ended."},
        errors=SESSION_ERRORS + ["not_found", "validation_error"],
    )
    def delete(self, request, session_id):
        session = ApiSession.objects.filter(
            public_id=session_id, state=ApiSession.State.ACTIVE,
            user__in=CompanyMembership.all_objects.filter(
                company_id=request.company_id, status=CompanyMembership.Status.ACTIVE)
            .values("user")).first()
        if session is None:
            raise ApiError("not_found")
        logins.end(session, "signed out by the company administrator")
        from api.core import audit

        audit.record(request, "api.session_ended_by_admin", user=request.user,
                     company_id=request.company_id, obj=session)
        return Response({"detail": "Session ended."})


# --- passwords ------------------------------------------------------------------


PASSWORD_ERRORS = ["validation_error", "unknown_field", "rate_limited", "server_error"]


class PasswordChangeView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "write"

    @endpoint(
        id="auth-password-change", area=AREA, title="Change my password",
        summary="The current password and a new one; every other session ends.",
        what_it_does=["Changes the login's password (the panels' password too).",
                      "Ends every other session; this one stays logged in."],
        description=("The new password follows the panels' rules: at least 8 characters, not "
                     "only numbers, not a common password, not like the email."),
        roles=["A logged-in person (app or web)"],
        request=s.PasswordChangeSerializer, response=s.MessageSerializer,
        request_example={"current_password": "••••••••", "new_password": "••••••••••"},
        response_example={"detail": "Password changed. Other sessions were ended."},
        errors=SESSION_ERRORS + ["validation_error", "unknown_field"],
    )
    def post(self, request):
        data = s.PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        logins.change_password(request, request.api_session, data.validated_data["current_password"],
                               data.validated_data["new_password"])
        return Response({"detail": "Password changed. Other sessions were ended."})


class PasswordForgotView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-password-forgot", area=AREA, title="Forgot password",
        summary="Emails a single-use reset code (or link), valid 30 minutes.",
        what_it_does=["Sends a reset to the email if it has a company login.",
                      "Always answers the same, so it tells nobody which emails exist."],
        description=("The email carries a link to your frontend's reset page when the server "
                     "is set up with one (API_PASSWORD_RESET_URL), otherwise the code itself. "
                     "Send it with the new password to POST /api/v1/auth/password/reset."),
        roles=["Anyone"], auth="public", response_status=202,
        request=s.ForgotSerializer, response=s.MessageSerializer,
        request_example={"email": "rahim@example.com"},
        response_example={"detail": "If this email has a login, a reset is on its way."},
        errors=PASSWORD_ERRORS,
    )
    def post(self, request):
        data = s.ForgotSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        logins.forgot_password(request, data.validated_data["email"])
        return Response({"detail": "If this email has a login, a reset is on its way."},
                        status=202)


class PasswordResetView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-password-reset", area=AREA, title="Reset the password",
        summary="The reset code and a new password; every session ends.",
        what_it_does=["Sets the new password (the panels' password too).",
                      "Ends every session of the login - log in again with the new password."],
        description="The code works once and expires 30 minutes after it was sent.",
        roles=["Anyone with a reset code"], auth="public",
        request=s.ResetSerializer, response=s.MessageSerializer,
        request_example={"token": "pr_Z2kq…", "new_password": "••••••••••"},
        response_example={"detail": "Password set. Log in with the new password."},
        errors=PASSWORD_ERRORS + ["invalid_reset"],
    )
    def post(self, request):
        data = s.ResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        logins.reset_password(request, data.validated_data["token"],
                              data.validated_data["new_password"])
        return Response({"detail": "Password set. Log in with the new password."})


# --- two-step login --------------------------------------------------------------


TWO_STEP_ERRORS = SESSION_ERRORS + ["conflict", "invalid_two_step_code", "validation_error",
                                    "unknown_field"]


class TwoStepSetupView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "login"
    allowed_during_two_step_setup = True

    @endpoint(
        id="auth-two-step-setup", area=AREA, title="Set up two-step login",
        summary="Start two-step login - the authenticator app (recommended) or email codes.",
        what_it_does=[
            'method "app" (recommended): answers a secret and an otpauth:// link to scan.',
            'method "email": a code is emailed now - for someone who does not want an app.',
            "Already on: changes the way, or moves the app to a new phone (send a current code).",
            "Nothing changes until it is confirmed with a code of the new way.",
        ],
        description=(
            "app: show otpauth_url as a QR code; the person scans it with an authenticator app "
            "(Google Authenticator, Microsoft Authenticator, Authy, …), then sends the 6-digit "
            "code it shows to POST /api/v1/auth/two-step/confirm. App users can also get a code "
            "by email at login when the app cannot be used - nothing to set up.\n\n"
            "email: a code is emailed now; send it to POST /api/v1/auth/two-step/confirm. From "
            "then on every login emails a code. Needs a mail account (the company's or the "
            "server's).\n\n"
            "Already on? Send a current code too - from the app, by email (POST "
            "/api/v1/auth/two-step/email-code) or a recovery code. The old way keeps working "
            "until the new one is confirmed. Required for owners and company administrators."),
        roles=["A logged-in person (app or web)"],
        request=s.TwoStepSetupInputSerializer, response=s.TwoStepSetupSerializer,
        request_example={"method": "app"},
        response_example={"method": "app", "replacing": False, "secret": "JBSWY3DPEHPK3PXP",
                          "otpauth_url": "otpauth://totp/Attendance%20Management:rahim%40example.com"
                                         "?secret=JBSWY3DPEHPK3PXP&issuer=Attendance%20Management"},
        errors=TWO_STEP_ERRORS,
    )
    def post(self, request):
        data = s.TwoStepSetupInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.two_step_setup(request, request.api_session,
                                              **data.validated_data))


class TwoStepEmailCodeView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "login"
    allowed_during_two_step_setup = True

    @endpoint(
        id="auth-two-step-email-code", area=AREA, title="Send me a code by email",
        summary="Emails a two-step code to the logged-in person.",
        what_it_does=["Emails a 6-digit code, for the two-step requests that need a code."],
        description=("Use it wherever a code from the app is asked for while logged in - "
                     "moving the app to a new phone, new recovery codes, turning two-step login "
                     "off. It works once, for 10 minutes; one email a minute."),
        roles=["A logged-in person who has started or set up two-step login"],
        response=s.EmailCodeSentSerializer,
        response_example={"email_sent_to": "r***@example.com", "email_expires_in": 600},
        errors=TWO_STEP_ERRORS + ["email_not_available", "email_not_sent"],
    )
    def post(self, request):
        return Response(logins.email_code_for_session(request, request.api_session))


class TwoStepConfirmView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "login"
    allowed_during_two_step_setup = True

    @endpoint(
        id="auth-two-step-confirm", area=AREA, title="Confirm two-step login",
        summary="The first code of the new way turns two-step login on.",
        what_it_does=["Turns two-step login on - or completes a change of way or phone.",
                      "Answers 10 recovery codes - shown once."],
        description=("Send the 6-digit code the authenticator app shows (method app) or the one "
                     "that was emailed (method email). From now on every login asks for a code; "
                     "an app user can get one by email when the app cannot be used (email_backup "
                     "says whether this server can send it). Keep the recovery codes somewhere "
                     "safe: each logs in once if nothing else works."),
        roles=["A logged-in person (app or web)"],
        request=s.CodeSerializer, response=s.TwoStepOnSerializer,
        request_example={"code": "492013"},
        response_example={"method": "app",
                          "recovery_codes": ["3f9a1c2e-7b4d5e6f", "a81c03d4-55e2f9b0"],
                          "email_backup": True},
        errors=TWO_STEP_ERRORS,
    )
    def post(self, request):
        data = s.CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.two_step_confirm(request, request.api_session,
                                                data.validated_data["code"]))


class TwoStepDisableView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "login"

    @endpoint(
        id="auth-two-step-disable", area=AREA, title="Turn two-step login off",
        summary="The password and a code turn two-step login off.",
        what_it_does=["Turns two-step login off for this login."],
        description="Not allowed for owners and company administrators: they must keep it on.",
        roles=["A logged-in person who is not an owner or administrator"],
        request=s.DisableTwoStepSerializer, response=s.MessageSerializer,
        request_example={"password": "••••••••", "code": "492013"},
        response_example={"detail": "Two-step login is off."},
        errors=TWO_STEP_ERRORS,
    )
    def post(self, request):
        data = s.DisableTwoStepSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        logins.two_step_disable(request, request.api_session, data.validated_data["password"],
                                data.validated_data["code"])
        return Response({"detail": "Two-step login is off."})


class RecoveryCodesView(ApiView):
    permission_classes = [IsPerson]
    throttle_scope = "login"

    @endpoint(
        id="auth-two-step-recovery-codes", area=AREA, title="New recovery codes",
        summary="A code (app or email) in; 10 new recovery codes out.",
        what_it_does=["Replaces the recovery codes - the old ones stop working."],
        description="Use it when the codes ran low or may have been seen by someone.",
        roles=["A logged-in person with two-step login on"],
        request=s.CodeSerializer, response=s.RecoveryCodesSerializer,
        request_example={"code": "492013"},
        response_example={"recovery_codes": ["3f9a1c2e-7b4d5e6f", "a81c03d4-55e2f9b0"]},
        errors=TWO_STEP_ERRORS,
    )
    def post(self, request):
        data = s.CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(logins.two_step_recovery_codes(request, request.api_session,
                                                       data.validated_data["code"]))


# --- signature help --------------------------------------------------------------


class SignatureTestView(ApiView):
    permission_classes = [Public]
    authentication_classes = []
    throttle_scope = "login"

    @endpoint(
        id="auth-signature-test", area=AREA, title="Test a signature",
        summary="Sign any request to this address; it says what is right and what is not.",
        what_it_does=[
            "Checks a signed request step by step: key, timestamp, nonce, body, signature.",
            "Shows the canonical request the server built, to compare with yours.",
        ],
        description=("Send a signed POST here, with any body, exactly as you would to a real "
                     "endpoint (with an API key, or an app session's id and secret). Nothing is "
                     "logged in or changed, and the nonce is not used up. When signature_ok is "
                     "false, compare canonical_request line by line with the one you signed - "
                     "the usual mistakes are the query not sorted, a different body (e.g. other "
                     "spacing in the JSON) or the timestamp in milliseconds instead of seconds."),
        roles=["Anyone"], auth="public", sample_auth="app",
        response=s.SignatureTestSerializer,
        response_example={"key_found": True, "timestamp_ok": True, "server_time": 1791100800,
                          "nonce_ok": True,
                          "body_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                          "canonical_request": "POST\n/api/v1/auth/signature-test\n\n1791100800\n"
                                               "a1b2c3d4e5f60718293a4b5c6d7e8f90\n44136fa3…",
                          "signature_ok": True, "problems": []},
        errors=["rate_limited", "server_error"],
    )
    def post(self, request):
        found = signing.parts(request)
        problems = []
        secret = ""
        key_id = found["key_id"]
        if key_id.startswith("ak_"):
            key = ApiKey.objects.filter(public_id=key_id, revoked_at__isnull=True).first()
            secret = decrypt(key.secret_encrypted) if key else ""
        elif key_id.startswith("ses_"):
            session = ApiSession.objects.filter(public_id=key_id,
                                                state=ApiSession.State.ACTIVE).first()
            secret = decrypt(session.signing_secret_encrypted) if session else ""
        if not key_id:
            problems.append("X-Key-Id is missing.")
        elif not secret:
            problems.append("X-Key-Id is not an active API key (ak_…) or app session (ses_…).")
        now = int(time.time())
        timestamp_ok = found["timestamp"].isdigit() and abs(now - int(found["timestamp"])) <= signing.WINDOW_SECONDS
        if not found["timestamp"]:
            problems.append("X-Timestamp is missing.")
        elif not timestamp_ok:
            problems.append(f"X-Timestamp must be Unix seconds within 5 minutes of {now}.")
        nonce_ok = bool(signing.NONCE_PATTERN.match(found["nonce"])) and not UsedNonce.objects.filter(
            owner=key_id, nonce=found["nonce"]).exists()
        if not nonce_ok:
            problems.append("X-Nonce must be 16-64 letters, digits, - or _, and new each time.")
        text = signing.request_canonical(request, found["timestamp"], found["nonce"])
        signature_ok = bool(secret) and bool(found["signature"]) and \
            same(signing.sign(secret, text), found["signature"])
        if secret and not signature_ok:
            problems.append("X-Signature does not match - compare canonical_request with yours.")
        return Response({"key_found": bool(secret), "timestamp_ok": timestamp_ok,
                         "server_time": now, "nonce_ok": nonce_ok,
                         "body_sha256": digest_body(request), "canonical_request": text,
                         "signature_ok": signature_ok, "problems": problems})


def digest_body(request):
    import hashlib

    return hashlib.sha256(request.body or b"").hexdigest()
