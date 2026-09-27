"""A leave's supporting document (Ajay's handover, 2026-09-27).

One per leave - a medical certificate, a letter - kept on the leave itself
(``LeaveRequest.attachment``), not a table of its own: Ajay set the line at
"a new table means stop and ask". Added when leave is recorded, requested or
changed; a leave type can require one ("Needs a document").

PDF, JPG, PNG or WEBP, up to 5 MB, checked to be what it says; stored under a
random name, the old file removed when replaced. Opened only by the employee,
whoever may see that leave on the Leave list, and whoever may decide it.
"""

import uuid

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile

KINDS = {"pdf": "pdf", "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}
MAX_BYTES = 5 * 1024 * 1024


class DocumentField(forms.FileField):
    def __init__(self, **kwargs):
        kwargs.setdefault("label", "Document")
        kwargs.setdefault("required", False)
        kwargs.setdefault("help_text", "A certificate or letter, if there is one: PDF, JPG, PNG "
                                       "or WEBP, up to 5 MB.")
        super().__init__(**kwargs)

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        if not upload or not hasattr(upload, "read"):
            return upload
        suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if suffix not in KINDS:
            raise ValidationError("Use a PDF, or a JPG, PNG or WEBP picture.")
        if upload.size > MAX_BYTES:
            raise ValidationError("That file is over 5 MB. Use a smaller one.")
        if KINDS[suffix] == "pdf":
            head = upload.read(5)
            upload.seek(0)
            if head != b"%PDF-":
                raise ValidationError("That file is not a PDF we can read.")
            upload.kind = "pdf"
            return upload
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(upload) as picture:
                picture.verify()
                kind = (picture.format or "").lower()
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise ValidationError("That file is not a picture we can read.") from exc
        if kind not in ("jpeg", "png", "webp"):
            raise ValidationError("Use a PDF, or a JPG, PNG or WEBP picture.")
        upload.seek(0)
        upload.kind = kind
        return upload


def require_if_needed(leave_type, document, request=None):
    """A leave type marked "Needs a document" is refused without one (a
    changed leave keeps the one it has)."""
    has = bool(document) or bool(request is not None and request.attachment)
    if leave_type.requires_attachment_by_default and not has:
        raise ValidationError({"document": (
            f"{leave_type.name} needs a document - a certificate or letter. Attach it here.")})


def store(request, upload):
    """Keep ``upload`` on the leave, replacing any earlier one. Call inside the
    company and the transaction that wrote the leave."""
    if not upload:
        return
    old = request.attachment.name if request.attachment else ""
    extension = {"jpeg": "jpg"}.get(upload.kind, upload.kind)
    request.attachment.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(upload.read()),
                            save=False)
    request.attachment_name = upload.name[:255]
    request.save(update_fields=["attachment", "attachment_name", "updated_at"])
    if old and old != request.attachment.name:
        request.attachment.storage.delete(old)


def may_open(user, company_id, request):
    """The employee, whoever may see that leave on the Leave list, and whoever
    may decide it."""
    if request.employee.user_id is not None and request.employee.user_id == user.pk:
        return True
    from attendance.access import in_scope
    from leaves import workflow
    from leaves.views import _list_scope

    try:
        _company_wide, scope, _record = _list_scope(user, company_id)
    except PermissionDenied:
        scope = None
    placed = request.submission_assignment
    if scope is not None and in_scope(placed.branch_id, placed.department_id, scope):
        return True
    try:
        member = workflow.reviewer(user, company_id)
    except PermissionDenied:
        return False
    return workflow.reviewable(member).filter(pk=request.pk).exists()
