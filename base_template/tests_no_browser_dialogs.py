"""No browser confirm()/alert()/prompt() boxes: confirmations use the project modal.

Ajay, 2026-09-19: "use modern modal no matter what". A form asks with
``data-confirm`` (base_template/static/base_template/js/confirm.js).
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BROWSER_BOX = re.compile(r"\b(?:window\.)?(?:confirm|alert|prompt)\s*\(")
SKIP = {"venv", ".git", "node_modules", "staticfiles", ".kilo", "media"}


class NoBrowserDialogTests(SimpleTestCase):
    def test_templates_and_scripts_use_the_modal(self):
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".js"} or SKIP & set(path.relative_to(root).parts):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if BROWSER_BOX.search(line) and "vendor" not in path.parts:
                    offenders.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(offenders, [], "Use data-confirm and the modal, not the browser's box.")
