"""Cache-busting: a static file's URL changes when the file changes."""

import os
import re
import shutil
import tempfile
from pathlib import Path

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import User
from base_template.staticfiles import VersionedStaticFilesStorage


def render_static(name):
    return Template("{% load static %}{% static name %}").render(Context({"name": name}))


class DevelopmentUrlTests(SimpleTestCase):
    """runserver and tests: no collected files, so the URL carries ?v=<hash>."""

    def setUp(self):
        self.source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.source)
        self.file = self.source / "probe.css"
        self.file.write_text("a { color: red; }")
        dirs = override_settings(STATICFILES_DIRS=[str(self.source)])
        dirs.enable()
        self.addCleanup(dirs.disable)

    def test_a_static_link_carries_the_file_version(self):
        self.assertRegex(render_static("probe.css"), r"^/static/probe\.css\?v=[0-9a-f]{12}$")

    def test_editing_the_file_changes_the_url(self):
        before = render_static("probe.css")
        self.file.write_text("a { color: blue; }")
        # Make sure the modification time moves even on a coarse clock.
        stat = self.file.stat()
        os.utime(self.file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        after = render_static("probe.css")
        self.assertNotEqual(before, after)
        self.assertTrue(after.startswith("/static/probe.css?v="))

    def test_an_unchanged_file_keeps_its_url(self):
        self.assertEqual(render_static("probe.css"), render_static("probe.css"))

    @override_settings(DEBUG=True)
    def test_debug_mode_uses_the_version_too(self):
        self.assertIn("probe.css?v=", render_static("probe.css"))

    def test_a_missing_file_still_renders_a_plain_link(self):
        self.assertEqual(render_static("nowhere.css"), "/static/nowhere.css")

    def test_the_project_stylesheets_are_versioned(self):
        self.assertRegex(
            render_static("base_template/css/shell.css"),
            r"^/static/base_template/css/shell\.css\?v=[0-9a-f]{12}$",
        )


class DeployedUrlTests(SimpleTestCase):
    """After collectstatic, the manifest gives every file a content-hashed name."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        root = override_settings(STATIC_ROOT=str(self.root), DEBUG=False)
        root.enable()
        self.addCleanup(root.disable)
        call_command("collectstatic", interactive=False, verbosity=0)

    def test_collected_files_are_served_under_hashed_names(self):
        url = VersionedStaticFilesStorage().url("base_template/css/shell.css")
        self.assertRegex(url, r"^/static/base_template/css/shell\.[0-9a-f]{12}\.css$")
        self.assertTrue((self.root / url.removeprefix("/static/")).exists())

    def test_the_static_tag_uses_the_hashed_name(self):
        self.assertTrue(staticfiles_storage.hashed_files)
        self.assertRegex(
            render_static("base_template/js/datepicker.js"),
            r"^/static/base_template/js/datepicker\.[0-9a-f]{12}\.js$",
        )

    def test_a_file_added_after_collectstatic_does_not_break_the_page(self):
        url = VersionedStaticFilesStorage().url("added/later.css")
        self.assertEqual(url, "/static/added/later.css")



class PageLinkTests(TestCase):
    """A real page through the base template: every local CSS/JS link is versioned."""

    def test_every_page_link_changes_with_its_file(self):
        root = User.objects.create_superuser(email="root@example.test", password="pw")
        self.client.force_login(root)
        response = self.client.get("/platform/companies/")
        self.assertEqual(response.status_code, 200)
        links = re.findall(r'(?:href|src)="(/static/[^"]+)"', response.content.decode())
        self.assertTrue(links)
        for link in links:
            with self.subTest(link=link):
                self.assertRegex(link, r"\?v=[0-9a-f]{12}$")
