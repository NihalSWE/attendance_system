"""Logging in, staying logged in, two-step login and passwords
(docs/api/00-PLAN.md, 2.3; the guide: docs/api/02-authentication.md).

Tokens are opaque random values; only their SHA-256 is stored. A session can
therefore be ended at once (sign out a device, change the password) - nothing
stays valid until it happens to expire.
"""

import datetime
import secrets

import pyotp
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from accounts.models import CompanyMembership
from accounts.services import get_active_memberships
from api.core import audit, email_codes
from api.core.crypto import decrypt, digest, encrypt, new_token, same
from api.core.errors import ApiError
from api.core.network import client_ip
from api.models import ApiSession, LoginAttempt, PasswordReset, TwoStep

User = get_user_model()

ADMIN_ROLES = (CompanyMembership.Role.OWNER, CompanyMembership.Role.COMPANY_ADMIN)
CHALLENGE_MINUTES = 10
RESET_MINUTES = 30
#: A refresh token presented again within this time is a retry (the answer was
#: lost on a bad connection), not a copy: it is rotated again, not punished.
RETRY_GRACE_SECONDS = 60
RECOVERY_CODES = 10
TOTP_ISSUER = "Attendance Management"


def access_minutes():
    return int(getattr(settings, "API_ACCESS_MINUTES", 10))


def refresh_days():
    return int(getattr(settings, "API_REFRESH_DAYS", 30))


def fingerprint(user):
    """Changes whenever the password changes - here or in the panels."""
    return digest("password:" + (user.password or ""))[:32]


def must_use_two_step(user):
    return get_active_memberships(user).filter(role__in=ADMIN_ROLES).exists()


def two_step_on(user):
    found = TwoStep.objects.filter(user=user, confirmed_at__isnull=False).first()
    return found


# --- lockout ------------------------------------------------------------------


def locked_for(email, ip):
    """Seconds this login (or address) stays locked; 0 when it may try."""
    now = timezone.now()
    last_success = (LoginAttempt.objects.filter(email=email, succeeded=True)
                    .order_by("-created_at").values_list("created_at", flat=True).first())
    since = last_success or now - datetime.timedelta(days=1)
    failures = LoginAttempt.objects.filter(email=email, succeeded=False, created_at__gt=since)
    until = None
    hour = failures.filter(created_at__gte=now - datetime.timedelta(hours=1))
    quarter = failures.filter(created_at__gte=now - datetime.timedelta(minutes=15))
    if hour.count() >= 10:
        until = hour.latest("created_at").created_at + datetime.timedelta(hours=1)
    elif quarter.count() >= 5:
        until = quarter.latest("created_at").created_at + datetime.timedelta(minutes=15)
    if ip:
        by_address = LoginAttempt.objects.filter(
            ip=ip, succeeded=False, created_at__gte=now - datetime.timedelta(minutes=15))
        if by_address.count() >= 20:
            address_until = by_address.latest("created_at").created_at + datetime.timedelta(minutes=15)
            until = max(until, address_until) if until else address_until
    return max(0, int((until - now).total_seconds())) if until else 0


def _attempt(request, email, succeeded, reason=""):
    LoginAttempt.objects.create(email=email, ip=client_ip(request), succeeded=succeeded,
                                reason=reason)


def _check_lock(request, email):
    wait = locked_for(email, client_ip(request))
    if wait:
        error = ApiError("login_locked", f"Too many failed tries. Try again in {wait} seconds.")
        error.retry_after = wait
        raise error


# --- sessions and tokens -------------------------------------------------------


def _new_session(request, user, client_type, device_name, state):
    session = ApiSession(
        public_id=new_token("ses", 12), user=user, client_type=client_type,
        device_name=(device_name or "")[:120], state=state,
        password_fingerprint=fingerprint(user), last_ip=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        last_used_at=timezone.now(),
    )
    return session


def _issue(session, *, with_secret=False):
    """New access and refresh tokens for an active session. Returns them -
    the only time they exist in plain text."""
    now = timezone.now()
    access, refresh = new_token("at"), new_token("rt")
    session.access_hash, session.refresh_hash = digest(access), digest(refresh)
    session.access_expires_at = now + datetime.timedelta(minutes=access_minutes())
    session.refresh_expires_at = now + datetime.timedelta(days=refresh_days())
    secret = ""
    if with_secret and session.client_type != ApiSession.ClientType.WEB:
        secret = new_token("ss", 32)
        session.signing_secret_encrypted = encrypt(secret)
    session.save()
    issued = {
        "session_id": session.public_id,
        "access_token": access,
        "access_expires_in": access_minutes() * 60,
        "refresh_token": refresh,
        "refresh_expires_at": session.refresh_expires_at,
        "two_step_setup_required": session.needs_two_step_setup,
    }
    if secret:
        issued["signing_secret"] = secret
    return issued


def end(session, reason):
    if session.state == ApiSession.State.ENDED:
        return
    session.state = ApiSession.State.ENDED
    session.ended_at, session.ended_reason = timezone.now(), reason[:120]
    session.access_hash = session.refresh_hash = session.challenge_hash = ""
    session.save(update_fields=["state", "ended_at", "ended_reason", "access_hash",
                                "refresh_hash", "challenge_hash"])


def end_all(user, reason, *, keep=None):
    for session in ApiSession.objects.filter(user=user).exclude(state=ApiSession.State.ENDED):
        if keep is None or session.pk != keep.pk:
            end(session, reason)


def still_valid(session):
    """Ends and refuses a session whose person can no longer use it."""
    user = session.user
    if session.state != ApiSession.State.ACTIVE:
        raise ApiError("session_ended")
    if not user.is_active or user.is_superuser:
        end(session, "login disabled")
        raise ApiError("session_ended")
    if session.password_fingerprint != fingerprint(user):
        end(session, "password changed")
        raise ApiError("session_ended")


# --- logging in ----------------------------------------------------------------


def login(request, *, email, password, client_type, device_name=""):
    """Email and password. Answers tokens, or a two-step challenge.

    Not one transaction as a whole: a refused try is recorded and must stay
    recorded (the lockout counts them) even though the answer is an error."""
    email = (email or "").strip().lower()
    _check_lock(request, email)
    found = User.objects.filter(email__iexact=email).first()
    # Always through authenticate(): it hashes even for an unknown email, so the
    # answer time does not tell which emails exist.
    user = authenticate(request=None, username=found.email if found else email, password=password)
    if user is None:
        _attempt(request, email, False, "wrong email or password")
        audit.record(request, "api.login_failed", user=found, data={"email": email})
        raise ApiError("invalid_credentials")
    if user.is_superuser:
        _attempt(request, email, False, "platform login")
        raise ApiError("platform_login_not_allowed")
    if not get_active_memberships(user).exists():
        _attempt(request, email, False, "no active company")
        raise ApiError("permission_denied", "This login belongs to no active company.")
    two_step = two_step_on(user)
    if two_step is not None:
        session = _new_session(request, user, client_type, device_name, ApiSession.State.TWO_STEP)
        challenge = new_token("ch", 24)
        session.challenge_hash = digest(challenge)
        session.challenge_expires_at = timezone.now() + datetime.timedelta(minutes=CHALLENGE_MINUTES)
        session.save()
        answer = {"two_step_required": True, "challenge": challenge,
                  "challenge_expires_in": CHALLENGE_MINUTES * 60,
                  "methods": methods_for(two_step)}
        if two_step.method == TwoStep.Method.EMAIL:
            try:                       # the code is on its way; a failure shows on a resend
                answer.update(email_codes.send(session))
            except ApiError:
                pass
        return answer
    with transaction.atomic():
        _attempt(request, email, True)
        session = _new_session(request, user, client_type, device_name, ApiSession.State.ACTIVE)
        session.needs_two_step_setup = must_use_two_step(user)
        issued = _issue(session, with_secret=True)
        audit.record(request, "api.login", user=user, obj=session,
                     data={"client_type": client_type, "device": session.device_name})
    return {"two_step_required": False, **issued}


def login_two_step(request, *, challenge, code):
    session = (ApiSession.objects.select_related("user")
               .filter(challenge_hash=digest(challenge or ""), state=ApiSession.State.TWO_STEP)
               .first())
    if session is None:
        raise ApiError("challenge_expired")
    email = session.user.email.lower()
    _check_lock(request, email)
    if session.challenge_expires_at is None or session.challenge_expires_at < timezone.now():
        end(session, "two-step challenge expired")
        raise ApiError("challenge_expired")
    two_step = two_step_on(session.user)
    if two_step is None or not check_code(two_step, code, session):
        _attempt(request, email, False, "wrong two-step code")
        audit.record(request, "api.two_step_failed", user=session.user, obj=session)
        raise ApiError("invalid_two_step_code")
    with transaction.atomic():
        # The challenge works once: claimed under a lock, so two requests with
        # the same code cannot both log in.
        claimed = ApiSession.objects.filter(pk=session.pk, state=ApiSession.State.TWO_STEP,
                                            challenge_hash=session.challenge_hash).update(
            state=ApiSession.State.ACTIVE, challenge_hash="", challenge_expires_at=None)
        if not claimed:
            raise ApiError("challenge_expired")
        session.refresh_from_db()
        _attempt(request, email, True)
        issued = _issue(session, with_secret=True)
        audit.record(request, "api.login", user=session.user, obj=session,
                     data={"client_type": session.client_type, "two_step": True})
    return {"two_step_required": False, **issued}


def refresh(request, *, token, signed_by=None):
    """Swap a refresh token for a new pair. ``signed_by`` checks an app
    session's signature (the access token may already have expired).

    Refusals that end a session (a reused token) are saved before the error
    is raised - they must stick."""
    hashed = digest(token or "")
    session = (ApiSession.objects.select_related("user")
               .filter(refresh_hash=hashed).exclude(refresh_hash="").first())
    if session is None:
        reused = (ApiSession.objects.select_related("user")
                  .filter(previous_refresh_hash=hashed, state=ApiSession.State.ACTIVE)
                  .exclude(previous_refresh_hash="").first())
        if reused is None:
            raise ApiError("invalid_token")
        grace = (reused.rotated_at is not None and
                 (timezone.now() - reused.rotated_at).total_seconds() <= RETRY_GRACE_SECONDS)
        if not grace:
            end(reused, "refresh token reused")
            audit.record(request, "api.refresh_token_reused", user=reused.user, obj=reused)
            raise ApiError("refresh_token_reused")
        session = reused                  # a retry within the grace: rotate again
    still_valid(session)
    if session.refresh_expires_at is None or session.refresh_expires_at < timezone.now():
        end(session, "refresh token expired")
        raise ApiError("session_ended")
    if signed_by is not None:
        signed_by(session)
    with transaction.atomic():
        locked = ApiSession.objects.select_for_update().get(pk=session.pk)
        if locked.refresh_hash not in (hashed, session.refresh_hash) or \
                locked.state != ApiSession.State.ACTIVE:
            raise ApiError("invalid_token")      # rotated by a parallel request meanwhile
        locked.previous_refresh_hash = hashed if locked.refresh_hash == hashed else \
            locked.previous_refresh_hash
        locked.rotated_at = timezone.now()
        locked.last_ip = client_ip(request)
        locked.last_used_at = timezone.now()
        return _issue(locked)


# --- two-step: the authenticator app (main) or a code by email ------------------


def methods_for(two_step):
    """The ways this login can pass two-step: "app", "email", "recovery_code"."""
    ways = ["app"] if two_step.method == TwoStep.Method.APP else []
    if two_step.method == TwoStep.Method.EMAIL or email_codes.available(two_step.user):
        ways.append("email")
    return ways + ["recovery_code"]


def _totp_ok(two_step, secret, code):
    """A current authenticator code; each works once (claimed in the database,
    so two requests cannot both use it)."""
    totp = pyotp.TOTP(secret)
    now = int(timezone.now().timestamp())
    for offset in (0, -1, 1):                     # the phone's clock may be a little off
        step = now // 30 + offset
        if same(totp.at(step * 30), code) and TwoStep.objects.filter(
                pk=two_step.pk, last_used_step__lt=step).update(last_used_step=step):
            two_step.last_used_step = step
            return True
    return False


def _recovery_ok(two_step, code):
    hashed = digest("recovery:" + code.lower())
    with transaction.atomic():
        row = TwoStep.objects.select_for_update().get(pk=two_step.pk)
        if hashed not in (row.recovery_hashes or []):
            return False
        row.recovery_hashes = [h for h in row.recovery_hashes if h != hashed]
        row.save(update_fields=["recovery_hashes"])
    two_step.recovery_hashes = row.recovery_hashes
    return True


def check_code(two_step, code, session=None):
    """A current authenticator code, the code emailed to this session, or an
    unused recovery code. Each works once. Commits its own bookkeeping, so call
    it outside a transaction that a refusal would roll back."""
    code = (code or "").strip().replace(" ", "")
    if code.isdigit() and len(code) == 6:
        secret = decrypt(two_step.secret_encrypted) if two_step.method == TwoStep.Method.APP else ""
        if secret and _totp_ok(two_step, secret, code):
            return True
        return session is not None and email_codes.check(session, code)
    return bool(code) and _recovery_ok(two_step, code)


def new_recovery_codes(two_step):
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODES)]
    two_step.recovery_hashes = [digest("recovery:" + code) for code in codes]
    two_step.save(update_fields=["recovery_hashes"])
    return codes


def email_code_for_challenge(request, challenge):
    """At login: send the two-step code by email instead of using the app."""
    session = (ApiSession.objects.select_related("user")
               .filter(challenge_hash=digest(challenge or ""), state=ApiSession.State.TWO_STEP)
               .first())
    if session is None or session.challenge_expires_at is None or \
            session.challenge_expires_at < timezone.now():
        raise ApiError("challenge_expired")
    _check_lock(request, session.user.email.lower())
    if two_step_on(session.user) is None:
        raise ApiError("challenge_expired")
    return email_codes.send(session)


def email_code_for_session(request, session):
    """Logged in: a code by email, to confirm email setup, change the way, turn
    two-step off or get new recovery codes."""
    if not TwoStep.objects.filter(user=session.user).exists():
        raise ApiError("conflict", "Two-step login is not set up: nothing needs a code.")
    return email_codes.send(session)


def two_step_setup(request, session, method=TwoStep.Method.APP, code=""):
    """Start two-step login with the app (a new secret to scan) or with email
    codes (a code is sent). When it is already on, this changes the way - after
    a current code proves it is the person - and the new way takes over only
    when it is confirmed."""
    user = session.user
    current = two_step_on(user)
    if current is not None:
        if not code:
            raise ApiError("validation_error", fields={"code": [
                "Two-step login is on: send a current code (app, email or recovery code) "
                "to change the way."]})
        if not check_code(current, code, session):
            raise ApiError("invalid_two_step_code")
    if method == TwoStep.Method.EMAIL and not email_codes.available(user):
        raise ApiError("email_not_available")
    secret = pyotp.random_base32() if method == TwoStep.Method.APP else ""
    stored = encrypt(secret) if secret else ""
    with transaction.atomic():
        if current is not None:
            current.pending_method, current.pending_secret_encrypted = method, stored
            current.save(update_fields=["pending_method", "pending_secret_encrypted"])
        else:
            TwoStep.objects.update_or_create(user=user, defaults={
                "method": method, "secret_encrypted": stored, "confirmed_at": None,
                "recovery_hashes": [], "last_used_step": 0, "pending_method": "",
                "pending_secret_encrypted": ""})
    answer = {"method": method}
    if secret:
        answer.update(secret=secret, otpauth_url=pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name=TOTP_ISSUER))
    else:
        answer.update(email_codes.send(session))
    return answer


def two_step_confirm(request, session, code):
    """The first code of the new way turns it on (or completes a change of
    way). Answers 10 new recovery codes."""
    user = session.user
    two_step = TwoStep.objects.filter(user=user).first()
    if two_step is None:
        raise ApiError("conflict", "Start with two-step setup.")
    changing = two_step.confirmed_at is not None
    if changing and not two_step.pending_method:
        raise ApiError("conflict", "Two-step login is already on.")
    method = two_step.pending_method if changing else two_step.method
    stored = two_step.pending_secret_encrypted if changing else two_step.secret_encrypted
    code = (code or "").strip().replace(" ", "")
    if method == TwoStep.Method.APP:
        ok = bool(stored) and _totp_ok(two_step, decrypt(stored), code)
    else:
        ok = email_codes.check(session, code)
    if not ok:
        raise ApiError("invalid_two_step_code")
    with transaction.atomic():
        two_step.method, two_step.secret_encrypted = method, stored
        two_step.pending_method = two_step.pending_secret_encrypted = ""
        two_step.confirmed_at = timezone.now()
        two_step.save()
        codes = new_recovery_codes(two_step)
        ApiSession.objects.filter(user=user, needs_two_step_setup=True).update(
            needs_two_step_setup=False)
        audit.record(request, "api.two_step_changed" if changing else "api.two_step_on",
                     user=user, data={"method": method})
    return {"method": method, "recovery_codes": codes}


def two_step_disable(request, session, password, code):
    user = session.user
    if must_use_two_step(user):
        raise ApiError("permission_denied", "Owners and company administrators must keep "
                                            "two-step login on.")
    two_step = two_step_on(user)
    if two_step is None:
        raise ApiError("conflict", "Two-step login is not on.")
    if not user.check_password(password or ""):
        raise ApiError("validation_error", fields={"password": ["The password is not right."]})
    if not check_code(two_step, code, session):
        raise ApiError("invalid_two_step_code")
    two_step.delete()
    audit.record(request, "api.two_step_off", user=user)


def two_step_recovery_codes(request, session, code):
    two_step = two_step_on(session.user)
    if two_step is None:
        raise ApiError("conflict", "Two-step login is not on.")
    if not check_code(two_step, code, session):
        raise ApiError("invalid_two_step_code")
    with transaction.atomic():
        codes = new_recovery_codes(two_step)
        audit.record(request, "api.two_step_recovery_codes", user=session.user)
    return {"recovery_codes": codes}


# --- passwords ------------------------------------------------------------------


@transaction.atomic
def change_password(request, session, current, new):
    user = session.user
    if not user.check_password(current or ""):
        raise ApiError("validation_error",
                       fields={"current_password": ["The current password is not right."]})
    _set_password(user, new, "new_password")
    session.password_fingerprint = fingerprint(user)
    session.save(update_fields=["password_fingerprint"])
    end_all(user, "password changed", keep=session)
    audit.record(request, "api.password_changed", user=user)


def _set_password(user, new, field):
    from django.core.exceptions import ValidationError

    try:
        validate_password(new or "", user)
    except ValidationError as exc:
        raise ApiError("validation_error", fields={field: list(exc.messages)}) from exc
    user.set_password(new)
    user.save(update_fields=["password"])


@transaction.atomic
def forgot_password(request, email):
    """Always the same answer, whether or not the email has a login - so the
    answer tells nobody which emails exist."""
    email = (email or "").strip().lower()
    user = User.objects.filter(email__iexact=email, is_active=True, is_superuser=False).first()
    if user is None or not get_active_memberships(user).exists():
        return
    token = new_token("pr", 24)
    PasswordReset.objects.create(user=user, token_hash=digest(token),
                                 expires_at=timezone.now() + datetime.timedelta(minutes=RESET_MINUTES))
    link = getattr(settings, "API_PASSWORD_RESET_URL", "") or ""
    where = (f"Open this link to choose a new password:\n{link.replace('{token}', token)}"
             if "{token}" in link else f"Your reset code is:\n{token}")
    send_mail(
        "Reset your password",
        f"Someone asked to reset the password for {user.email}.\n\n{where}\n\n"
        f"It works once and expires in {RESET_MINUTES} minutes. If you did not ask, ignore "
        "this email; your password stays as it is.",
        None, [user.email], fail_silently=True)
    audit.record(request, "api.password_reset_requested", user=user)


@transaction.atomic
def reset_password(request, token, new):
    reset = (PasswordReset.objects.select_for_update().select_related("user")
             .filter(token_hash=digest(token or ""), used_at__isnull=True).first())
    if reset is None or reset.expires_at < timezone.now() or not reset.user.is_active:
        raise ApiError("invalid_reset")
    _set_password(reset.user, new, "new_password")
    reset.used_at = timezone.now()
    reset.save(update_fields=["used_at"])
    end_all(reset.user, "password reset")
    audit.record(request, "api.password_reset", user=reset.user)
