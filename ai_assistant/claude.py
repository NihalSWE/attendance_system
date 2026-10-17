"""Calling Claude for the order assistant: the company's own key (encrypted),
every call logged with its tokens and estimated cost, and a daily spending
limit checked before each call.

Tests (and anything else) can swap ``CLIENT_FACTORY`` for a fake client; no
code outside this module talks to the Anthropic SDK.
"""

import base64
import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from ai_assistant.models import AiCall, AiSettings

#: US$ per million tokens: input, output, cache read, cache write (5-minute).
#: Estimates from Anthropic's price list (2026-10); the real bill is Anthropic's.
PRICES = {
    "claude-opus-5-5": (Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10"), Decimal("0.20"), Decimal("2.50")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50"), Decimal("0.01"), Decimal("0.125")),
}
MILLION = Decimal("1000000")
#: Models that take the server-side refusal fallback (Haiku has none).
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: ``(api_key) -> client``; replaced in tests.
CLIENT_FACTORY = None


class AiUnavailable(ValidationError):
    """The AI cannot be called now (off, no key, limit reached) - the order waits for a person."""


# --- secrets ----------------------------------------------------------------

def _cipher():
    from cryptography.fernet import Fernet

    # Its own key derived from SECRET_KEY, apart from the mail password's.
    digest = hashlib.sha256(b"ai-assistant-secrets:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret):
    return _cipher().encrypt(secret.encode()).decode("ascii") if secret else ""


def decrypt(token):
    from cryptography.fernet import InvalidToken

    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode()
    except InvalidToken as exc:
        raise AiUnavailable("The saved key can no longer be read (the server's secret key changed). "
                            "Enter it again under AI assistant settings.") from exc


# --- settings and spending --------------------------------------------------

def settings_for(company_id):
    return AiSettings.all_objects.filter(company_id=company_id).first() or AiSettings(company_id=company_id)


def spent_today(company_id):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return AiCall.all_objects.filter(company_id=company_id, created_at__gte=start).aggregate(
        total=Sum("cost"))["total"] or Decimal("0")


def cost_of(model, usage):
    price_in, price_out, price_read, price_write = PRICES.get(model, PRICES["claude-opus-5-5"])
    return ((usage["input_tokens"] * price_in + usage["output_tokens"] * price_out
             + usage["cache_read_tokens"] * price_read + usage["cache_write_tokens"] * price_write)
            / MILLION).quantize(Decimal("0.000001"))


def check_available(own):
    """Why the AI cannot be called for this company now, raised; nothing when it can."""
    if not own.enabled:
        raise AiUnavailable("The AI assistant is off for this company.")
    if not own.consent_received:
        raise AiUnavailable("The client's consent to send order text to the AI provider is not recorded.")
    if not own.has_key:
        raise AiUnavailable("No AI key is saved (AI assistant settings).")
    if spent_today(own.company_id) >= own.daily_cost_cap:
        raise AiUnavailable(f"Today's AI spending limit (US$ {own.daily_cost_cap}) is reached.")


def _client(api_key):
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY(api_key)
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=2)


def _usage(response):
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def ask_json(*, company_id, kind, system, content, schema, message=None, max_tokens=16000):
    """One structured-output call: ``system`` (cached), the user ``content``,
    and the JSON ``schema`` the answer must follow. Returns the parsed dict.

    Logs an ``AiCall`` whatever happens. Raises ``AiUnavailable`` when the AI
    cannot be called, ``ValidationError`` when the call failed or was declined.
    """
    own = settings_for(company_id)
    check_available(own)
    import anthropic

    call = AiCall(company_id=company_id, kind=kind, message=message, model=own.model)
    request = {
        "model": own.model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": own.effort, "format": {"type": "json_schema", "schema": schema}},
    }
    if own.model in FALLBACK_MODELS:
        # On a policy decline the API re-runs the request on a fallback model.
        request.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        client = _client(decrypt(own.api_key_encrypted))
        response = client.beta.messages.create(**request)
    except AiUnavailable:
        raise
    except anthropic.AuthenticationError:
        call.error = "The AI key was refused: check it under AI assistant settings."
    except anthropic.RateLimitError:
        call.error = "The AI provider is busy (rate limit): try again in a minute."
    except anthropic.APIStatusError as exc:
        call.error = f"The AI provider answered with an error ({exc.status_code})."
    except anthropic.APIConnectionError:
        call.error = "The AI provider could not be reached."
    else:
        usage = _usage(response)
        call.served_by = getattr(response, "model", "") or ""
        call.request_id = getattr(response, "_request_id", "") or ""
        call.stop_reason = getattr(response, "stop_reason", "") or ""
        call.input_tokens, call.output_tokens = usage["input_tokens"], usage["output_tokens"]
        call.cache_read_tokens, call.cache_write_tokens = usage["cache_read_tokens"], usage["cache_write_tokens"]
        call.cost = cost_of(call.served_by or own.model, usage)
        text = next((block.text for block in response.content if getattr(block, "type", "") == "text"), "")
        call.output = text[:20000]
        if call.stop_reason == "refusal":
            call.error = "The AI declined to read this message."
        elif call.stop_reason == "max_tokens":
            call.error = "The answer was cut off (too long)."
        else:
            try:
                data = json.loads(text)
            except ValueError:
                call.error = "The AI's answer was not valid JSON."
            else:
                call.save()
                return data
    call.save()
    raise ValidationError(call.error)
