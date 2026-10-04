# How the API is built (for the team)

This page is for whoever works on the API code. It explains where things are
and how to add an endpoint, so the API stays the same shape everywhere.

## Where things are

```
api/
  core/                 shared by every endpoint
    errors.py           the error catalogue + the error handler (one error shape)
    docs.py             @endpoint(...): the documentation declared on the endpoint
    registry.py         finds every endpoint from the URLs (for the site and tests)
    fields.py           a serializer as a field tree (the + trees on the site)
    samples.py          request examples in 8 languages
    views.py            ApiView: the base class (request id, Idempotency-Key)
    permissions.py      DenyAll (the default), Public
    pagination.py       page / page_size, at most 100
    throttling.py       rate limits per kind (public / read / write …)
    serializers.py      StrictSerializer: unknown fields refused
    company.py          which company a request acts for (X-Company)
  v1/
    urls.py             includes each area
    <area>/             one folder per area, always the same four files:
      serializers.py
      views.py
      urls.py
      (tests live in api/tests/)
  docs_site/            the documentation site (/api/docs/)
  templates/api/docs/   its pages
  static/api/docs/      its CSS and JS
  tests/                the API's tests, incl. the documentation test
docs/api/               the plan, the guide pages, the changelog
```

## The one rule

**An endpoint never changes data itself.** It authenticates, checks
permission, validates the input, then calls **the same service function the
panel uses** (`employees.services`, `attendance.services`, …) and returns the
result. So a business rule is written once and holds in the panels and the
API alike. If the panel has no service for something, write the service
first, use it from the panel view too, then from the API.

## Adding an endpoint, step by step

1. **Serializers** (`api/v1/<area>/serializers.py`): one for what comes in
   (inherit `StrictSerializer`), one for what goes out. **Every field gets a
   `help_text`** — it is what the documentation shows.
2. **The view** (`views.py`): inherit `ApiView`, set `permission_classes`
   (never leave the default) and `throttle_scope` (`read` / `write` / …).
   Decorate each method with `@endpoint(...)`:
   - `id` (the page address), `area`, `title`, `summary`;
   - `what_it_does` (2-4 bullets), `description` (the rules in plain words);
   - `roles` (who may use it, in the panels' words), `scopes` (API keys);
   - `params` (path / query / header), `request`, `response`, `paginated`;
   - `request_example`, `response_example` (realistic data);
   - `errors`: every catalogue code this endpoint can return.
   The method calls the service and returns the response serializer's data.
3. **A new error?** Add it to `api/core/errors.py` (code, status, title,
   meaning, fix, group, example). Never answer with a code that is not there.
4. **The URL** (`urls.py`) — no trailing slash.
5. **Tests** (`api/tests/`): works for the right caller · refused without
   login · refused without permission · another company's records invisible
   (404) · bad input gives the right error.
6. **The guide page** for the area (`docs/api/NN-<area>.md`) and a line in
   `docs/api/CHANGELOG.md`.
7. Run the API tests — **the documentation test fails** if the endpoint, any
   field or any error lacks documentation, or the view declares no
   permission.

## Checking your work

- `python manage.py test api --noinput`
- Open `/api/docs/` — the endpoint's page is generated from your declaration.
- `python manage.py spectacular --validate` — the OpenAPI schema is valid.
