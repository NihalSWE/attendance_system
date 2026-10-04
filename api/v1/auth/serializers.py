"""What the login endpoints take and give. Every field explained (the
documentation site shows it; the documentation test insists on it)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer

APP_CLIENTS = (("mobile", "Mobile app"), ("desktop", "Desktop app"))


# --- in ---

class LoginSerializer(StrictSerializer):
    email = serializers.EmailField(help_text="The login's email, as in the panels.")
    password = serializers.CharField(help_text="The login's password. Never logged.",
                                     trim_whitespace=False, max_length=256)
    client_type = serializers.ChoiceField(
        choices=APP_CLIENTS,
        help_text="Which kind of app this is. Both sign every request with the "
                  "signing_secret from the answer. (A browser frontend uses "
                  "POST /api/v1/auth/web/login instead.)")
    device_name = serializers.CharField(
        required=False, allow_blank=True, max_length=120, default="",
        help_text='Shown in the sessions list so the person recognises it, e.g. '
                  '"Rahim\'s Samsung A54".')


class WebLoginSerializer(StrictSerializer):
    email = serializers.EmailField(help_text="The login's email, as in the panels.")
    password = serializers.CharField(help_text="The login's password. Never logged.",
                                     trim_whitespace=False, max_length=256)
    device_name = serializers.CharField(
        required=False, allow_blank=True, max_length=120, default="",
        help_text='Shown in the sessions list, e.g. "Chrome on the office PC".')


class TwoStepLoginSerializer(StrictSerializer):
    challenge = serializers.CharField(
        max_length=200, help_text="The challenge from the login answer. Valid 10 minutes.")
    code = serializers.CharField(
        max_length=20,
        help_text="The 6-digit code from the authenticator app, the 6-digit code sent by "
                  "email, or one recovery code (e.g. 3f9a1c2e-7b4d5e6f). Each works once.")


class ChallengeSerializer(StrictSerializer):
    challenge = serializers.CharField(
        max_length=200, help_text="The challenge from the login answer. Valid 10 minutes.")


class TwoStepSetupInputSerializer(StrictSerializer):
    method = serializers.ChoiceField(
        choices=(("app", "Authenticator app"), ("email", "Code by email")),
        required=False, default="app",
        help_text="app (recommended): an authenticator app on the phone. email: a code is "
                  "emailed at each login instead - for someone who does not want an app.")
    code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default="",
        help_text="Only when two-step login is already on (changing the way): a current "
                  "code - from the app, by email, or a recovery code.")


class RefreshSerializer(StrictSerializer):
    refresh_token = serializers.CharField(
        max_length=200,
        help_text="The newest refresh token. It works once: keep the new one from "
                  "the answer and forget this one.")


class PasswordChangeSerializer(StrictSerializer):
    current_password = serializers.CharField(trim_whitespace=False, max_length=256,
                                             help_text="The password used now.")
    new_password = serializers.CharField(
        trim_whitespace=False, max_length=256,
        help_text="The new password: at least 8 characters, not only numbers, not "
                  "a common password, not like the email.")


class ForgotSerializer(StrictSerializer):
    email = serializers.EmailField(help_text="The login's email.")


class ResetSerializer(StrictSerializer):
    token = serializers.CharField(max_length=200,
                                  help_text="The reset code from the email (pr_…).")
    new_password = serializers.CharField(
        trim_whitespace=False, max_length=256,
        help_text="The new password: at least 8 characters, not only numbers, not "
                  "a common password, not like the email.")


class CodeSerializer(StrictSerializer):
    code = serializers.CharField(
        max_length=20, help_text="The current 6-digit code from the authenticator app "
                                 "(or a recovery code).")


class DisableTwoStepSerializer(StrictSerializer):
    password = serializers.CharField(trim_whitespace=False, max_length=256,
                                     help_text="The login's password.")
    code = serializers.CharField(max_length=20,
                                 help_text="The current 6-digit code from the authenticator "
                                           "app (or a recovery code).")


class EmptySerializer(StrictSerializer):
    """No body."""


# --- out ---

class TwoStepChallengeFields(serializers.Serializer):
    challenge = serializers.CharField(
        required=False, help_text="Only when two_step_required: send it with the code.")
    challenge_expires_in = serializers.IntegerField(
        required=False, help_text="Only when two_step_required: seconds left to send the code.")
    methods = serializers.ListField(
        child=serializers.CharField(help_text='"app", "email" or "recovery_code".'),
        required=False,
        help_text='Only when two_step_required: the ways this login can pass. "email" means '
                  "a code can be sent by email (POST /api/v1/auth/login/two-step/email-code).")
    email_sent_to = serializers.CharField(
        required=False,
        help_text="Only when a code was emailed already (two-step by email): where it went, "
                  "e.g. r***@example.com.")
    email_expires_in = serializers.IntegerField(
        required=False, help_text="Only with email_sent_to: seconds the emailed code works.")


class TokensSerializer(TwoStepChallengeFields):
    two_step_required = serializers.BooleanField(
        help_text="True when the login needs a two-step code next: then only the challenge "
                  "fields are present - send the code to POST /api/v1/auth/login/two-step.")
    session_id = serializers.CharField(
        required=False,
        help_text="This login's id (ses_…). Apps send it as X-Key-Id on every signed request.")
    access_token = serializers.CharField(
        required=False,
        help_text="Send as Authorization: Bearer <token> on every request. Valid 10 minutes.")
    access_expires_in = serializers.IntegerField(
        required=False, help_text="Seconds until the access token expires (600).")
    refresh_token = serializers.CharField(
        required=False,
        help_text="Swap it for a new pair before the access token expires "
                  "(POST /api/v1/auth/refresh). Works once. Valid 30 days.")
    refresh_expires_at = serializers.DateTimeField(
        required=False, help_text="When the refresh token - and so the session - expires.")
    signing_secret = serializers.CharField(
        required=False,
        help_text="Only on login (apps): the secret to sign every request with. Store "
                  "it in the system's secure storage; it is never sent again.")
    two_step_setup_required = serializers.BooleanField(
        required=False,
        help_text="True for an owner or company administrator without two-step login: "
                  "until it is set up, only two-step setup and logout work.")


class WebSessionSerializer(TwoStepChallengeFields):
    two_step_required = serializers.BooleanField(
        help_text="True when the login needs a two-step code next "
                  "(POST /api/v1/auth/web/login/two-step).")
    session_id = serializers.CharField(required=False, help_text="This login's id (ses_…).")
    access_expires_in = serializers.IntegerField(
        required=False,
        help_text="Seconds until the access cookie expires; refresh before then "
                  "(POST /api/v1/auth/web/refresh).")
    two_step_setup_required = serializers.BooleanField(
        required=False,
        help_text="True for an owner or company administrator without two-step login.")


class CsrfSerializer(serializers.Serializer):
    csrf_token = serializers.CharField(
        help_text="Send it as the X-CSRFToken header on every request that changes "
                  "data (it is also set as the csrftoken cookie).")


class MessageSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="What was done, in plain words.")


class CompanySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The company id - send it as X-Company when the "
                                            "login has several companies.")
    name = serializers.CharField(help_text="The company's name.")
    role = serializers.CharField(help_text="The login's role in it: owner, company_admin, hr, "
                                           "manager, payroll_manager, employee or auditor.")


class TwoStepStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(help_text="Two-step login is on for this login.")
    method = serializers.CharField(
        allow_null=True, help_text='"app" or "email" when it is on; null when it is off.')
    required = serializers.BooleanField(
        help_text="It must be on: the login is an owner or company administrator.")


class CurrentSessionSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="This session's id (ses_…).")
    client_type = serializers.CharField(help_text="mobile, desktop or web.")
    access_expires_at = serializers.DateTimeField(help_text="When the current access token expires.")


class MeSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="The login's email.")
    name = serializers.CharField(help_text="The person's name.")
    companies = CompanySerializer(many=True, help_text="Every active company of this login.")
    two_step = TwoStepStatusSerializer(help_text="Two-step login for this login.")
    session = CurrentSessionSerializer(help_text="The session this request came through.")


class SessionSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The session's id (ses_…).")
    client_type = serializers.CharField(help_text="mobile, desktop or web.")
    device_name = serializers.CharField(help_text="The name given at login (may be empty).")
    created_at = serializers.DateTimeField(help_text="When it logged in.")
    last_used_at = serializers.DateTimeField(help_text="When it was last used (to the minute).",
                                             allow_null=True)
    last_ip = serializers.CharField(help_text="The address it was last used from.",
                                    allow_null=True)
    current = serializers.BooleanField(help_text="True for the session this request came through.")


class StaffSessionSerializer(SessionSerializer):
    user_email = serializers.EmailField(help_text="Whose session it is.")


class TwoStepSetupSerializer(serializers.Serializer):
    method = serializers.CharField(help_text='The way being set up: "app" or "email".')
    secret = serializers.CharField(
        required=False,
        help_text="app only: the secret to type into the authenticator app, if it cannot scan.")
    otpauth_url = serializers.CharField(
        required=False,
        help_text="app only: the same as an otpauth:// link - show it as a QR code to scan.")
    email_sent_to = serializers.CharField(
        required=False, help_text="email only: where the code went, e.g. r***@example.com.")
    email_expires_in = serializers.IntegerField(
        required=False, help_text="email only: seconds the emailed code works.")


class EmailCodeSentSerializer(serializers.Serializer):
    email_sent_to = serializers.CharField(help_text="Where the code went, e.g. r***@example.com.")
    email_expires_in = serializers.IntegerField(
        help_text="Seconds the code works (600). It works once; a new one can be asked for "
                  "after a minute.")


class TwoStepOnSerializer(serializers.Serializer):
    method = serializers.CharField(help_text='The way now on: "app" or "email".')
    recovery_codes = serializers.ListField(
        child=serializers.CharField(help_text="One code, e.g. 3f9a1c2e-7b4d5e6f."),
        help_text="10 single-use codes for when the phone or the email is out of reach. "
                  "Shown once - keep them somewhere safe. Any older codes stop working.")


class RecoveryCodesSerializer(serializers.Serializer):
    recovery_codes = serializers.ListField(
        child=serializers.CharField(help_text="One code, e.g. 3f9a1c2e-7b4d5e6f."),
        help_text="10 single-use codes for when the phone is lost. Shown once - "
                  "keep them somewhere safe.")


class SignatureTestSerializer(serializers.Serializer):
    key_found = serializers.BooleanField(
        help_text="The X-Key-Id is a known API key or app session.")
    timestamp_ok = serializers.BooleanField(
        help_text="X-Timestamp is within 5 minutes of the server's time.")
    server_time = serializers.IntegerField(help_text="The server's time, Unix seconds.")
    nonce_ok = serializers.BooleanField(
        help_text="X-Nonce has the right form and has not been used by this key or session.")
    body_sha256 = serializers.CharField(help_text="The SHA-256 of the body the server received.")
    canonical_request = serializers.CharField(
        help_text="The canonical request the server built from your request - compare it, "
                  "line by line, with yours.")
    signature_ok = serializers.BooleanField(help_text="Your X-Signature matches.")
    problems = serializers.ListField(
        child=serializers.CharField(help_text="One thing to fix."),
        help_text="What is wrong, in plain words. Empty when the signature is right.")
