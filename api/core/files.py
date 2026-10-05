"""Files in and out of the API.

In: a file travels inside the JSON as ``{"filename", "content_base64"}``, so
the request is signed like any other (docs/api/20-company-and-branches.md).
The panel's own form then checks it - type, size, that it really is a picture
or a PDF - exactly as an upload on the page.

Out: a stored file is answered as itself, privately (never cached by others,
never sniffed as another type) - the panels' rule for photos and documents.
"""

import base64
import binascii
import mimetypes

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import FileResponse

from api.core.errors import ApiError


def upload_from(filename, content_base64, field="content_base64"):
    """The decoded file, as if it had been uploaded on the panel."""
    try:
        content = base64.b64decode(content_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("validation_error", fields={field: ["This is not valid base64."]}) from None
    return SimpleUploadedFile((filename or "").strip()[:200], content)


def private_file(field_file, download_name=None):
    kind = mimetypes.guess_type(field_file.name)[0] or "application/octet-stream"
    reply = FileResponse(field_file.open("rb"), content_type=kind, filename=download_name or None)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply
