"""Static file URLs that change whenever the file changes (cache-busting).

Without this a browser keeps using its cached copy of a CSS or JavaScript file
after the file is edited, and the page looks unfixed until somebody presses
Ctrl+F5.

- **Deployed** (``DEBUG=False`` after ``collectstatic``): Django's manifest
  storage names every collected file after its content
  (``shell.1a2b3c4d5e6f.css``), so a changed file gets a new URL and an old URL
  can be cached for as long as the server likes.
- **Development** (``runserver`` with ``DEBUG=True``) and tests: nothing is
  collected, so the URL carries ``?v=`` and a hash of the source file instead
  (``shell.css?v=1a2b3c4d5e6f``). The hash is recomputed only when the file's
  modification time changes, so an unchanged file costs one ``stat`` per link.
"""

import hashlib
import os

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import (
    ManifestStaticFilesStorage,
    StaticFilesStorage,
)


class VersionedStaticFilesStorage(ManifestStaticFilesStorage):
    # path -> (modification time, short content hash). Shared by every
    # instance: the storage object is rebuilt when settings change in tests,
    # but a file's content hash does not depend on the instance.
    _versions = {}

    def url(self, name, force=False):
        if not settings.DEBUG and self.hashed_files:
            try:
                return super().url(name, force)
            except ValueError:
                # Not in the manifest: a file added after the last
                # collectstatic. Fall through rather than break the page.
                pass
        return self.versioned_url(name)

    def versioned_url(self, name):
        plain = StaticFilesStorage.url(self, name)
        if "?" in name or "#" in name:
            return plain
        version = self._version(name)
        return f"{plain}?v={version}" if version else plain

    def _version(self, name):
        path = finders.find(name)
        if not path:
            return ""
        try:
            modified = os.stat(path).st_mtime_ns
        except OSError:
            return ""
        cached = self._versions.get(path)
        if cached and cached[0] == modified:
            return cached[1]
        with open(path, "rb") as handle:
            digest = hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()[:12]
        self._versions[path] = (modified, digest)
        return digest
