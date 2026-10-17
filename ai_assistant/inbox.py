"""Reading the order inbox (IMAP): new emails become messages to read.

Only unread mail is taken, at most ``BATCH`` a run; each is marked read once
saved, and an email seen before (same Message-ID) is skipped. The company's
own mailbox, with the password kept encrypted like the mail password.
"""

import email
import imaplib
import re
from email.policy import default as email_policy
from email.utils import parsedate_to_datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from ai_assistant.claude import decrypt
from ai_assistant.models import InboundMessage

BATCH = 50
TIMEOUT = 30

#: ``(host, port) -> IMAP connection``; replaced in tests.
IMAP_FACTORY = None


def _connect(host, port):
    if IMAP_FACTORY is not None:
        return IMAP_FACTORY(host, port)
    return imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)


def _text(mail):
    """The message's plain text: the text/plain part, else the HTML with the tags taken out."""
    part = mail.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.S | re.I)
        content = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", content, flags=re.I)
        content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"[ \t]+", " ", content).strip()


def fetch(own):
    """Save the unread emails of ``own`` (AiSettings). Returns the new messages."""
    from ai_assistant.intake import receive

    if not (own.inbox_on and own.imap_host and own.imap_username):
        return []
    made = []
    try:
        box = _connect(own.imap_host, own.imap_port)
        box.login(own.imap_username, decrypt(own.imap_password_encrypted))
        box.select(own.imap_folder or "INBOX")
        _status, found = box.search(None, "UNSEEN")
        for number in (found[0].split() if found and found[0] else [])[:BATCH]:
            _status, parts = box.fetch(number, "(RFC822)")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            mail = email.message_from_bytes(raw, policy=email_policy)
            try:
                received = parsedate_to_datetime(mail["Date"]) if mail["Date"] else timezone.now()
            except (TypeError, ValueError):
                received = timezone.now()
            try:
                message = receive(company_id=own.company_id, channel=InboundMessage.Channel.EMAIL,
                                  body=_text(mail), sender=str(mail["From"] or ""), subject=str(mail["Subject"] or ""),
                                  message_id=str(mail["Message-ID"] or ""), received_at=received)
            except ValidationError:
                message = None          # an empty email: nothing to read
            if message is not None:
                made.append(message)
            box.store(number, "+FLAGS", "\\Seen")
        box.logout()
    except (imaplib.IMAP4.error, OSError) as exc:
        own.last_inbox_error = f"The inbox could not be read: {exc}"[:500]
    else:
        own.last_inbox_error = ""
    own.last_inbox_check = timezone.now()
    own.save(update_fields=["last_inbox_check", "last_inbox_error", "updated_at"])
    return made
