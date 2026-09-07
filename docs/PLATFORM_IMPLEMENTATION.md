# P1 platform onboarding — 2026-09-07

This checkpoint delivers the platform-owner workflow. P1 as a whole remains in progress: company organization/schedule setup and employee write forms are next.

## UI workflow (corrected)

Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) for the binding project-wide rules and database explanation.

1. Root signs in and reaches `/platform/companies/`. `/platform/` redirects to this explicit list route.
2. **Create company:** name and contact fields only. The backend generates the string code and unique slug, uses BDT/BD/Asia/Dhaka defaults, and atomically creates Company, Head Office and CompanyAttendanceSettings. Existing codes/slugs are preserved.
3. **Create administrator:** email, name and password; creates one separate User and fixed company_admin membership. No account reuse or role selection. After creation, the company page offers **Edit administrator** instead.
4. **Edit administrator:** change name/email, optionally set a new password, or change membership status. Root never needs a company membership. Ending a membership retains its history.
5. **Edit company:** generated identifiers are read-only, address is a single-line input and phone shows +88. Hidden locale fields retain stored values.
6. **Change status / Change access:** status changes and dated feature grants remain audited. Fixed options use native selects; the feature catalogue uses Select2. Company feature access and user action permissions are separate checks.
7. Sign in with the company's master credentials to reach its workspace. Company setup and employee writes are still the next slice.

Company-wide read pages currently require unrestricted Owner/Company admin membership. Other roles and restricted scopes fail closed until the next slice adds proper action and row-scope enforcement. This replaces the previous unsafe assumption that every company member can view employee compensation. Existing company-side write/export buttons are explicitly disabled with a reason.

## Files and functions

| File | Created/changed responsibilities |
|---|---|
| `tenants/platform_services.py` | `require_platform_owner`, `platform_company_context`, `company_snapshot`, `validate_company_values`, `create_platform_company`, `ensure_feature_catalogue`, `update_platform_company`, `change_company_status`, `grant_company_administrator`, `update_company_membership`, `edit_company_administrator`, `set_company_feature` |
| `tenants/platform_views.py` | `platform_required`, `_integer`, `_form_page`; `company_list` (HTML + bounded DataTables JSON), `company_detail`, `company_create`, `company_edit`, `company_status`, `administrator_create`, `membership_edit`, `company_feature` |
| `tenants/forms.py` | `CompanyForm`, `CompanyStatusForm`, `AdministratorForm`, `CompanyFeatureForm`; shared `StyledFormMixin` and phone widget now live in common/forms.py; field/queryset validation, password confirmation and consistent widgets |
| `tenants/urls.py`, `config/urls.py` | Namespaced `/platform/` routes using company public UUIDs |
| `tenants/services.py` | `onboard_company(require_new=False)` preserves its existing retry contract; platform creation uses `require_new=True` so a concurrent same-code request cannot overwrite the existing company |
| `accounts/services.py` | `get_active_memberships` excludes suspended/inactive companies; `resolve_active_company_id` handles malformed session company IDs |
| `auditlog/models.py` | Planned `AuditLog` model, `AuditQuerySet`, `save`, `delete`, `action_label`; explicit snapshots, actor and object identity, optional correlation fields |
| `auditlog/services.py` | `record_platform_event`; allowlisted snapshots and readable object descriptions, no passwords |
| `auditlog/admin.py` | `AuditLogAdmin` permits root-only viewing, no add/edit/delete |
| `auditlog/migrations/0001_initial.py` | Creates the planned AuditLog table; existing migration files are preserved |
| `auditlog/migrations/0002_protect_audit_rows.py` | PostgreSQL trigger rejects UPDATE/DELETE of audit records, including raw SQL |
| `base_template/views.py` | `company_admin_required`, root dashboard routing; `switch_company` validates integer input and redirects to a fixed local destination |
| `base_template/context_processors.py` | `shell` supplies the explicit platform persona without assigning root a tenant |
| `tenants/templates/tenants/platform/` | Platform base, company list/detail and shared form page; domain pages belong to tenants |
| `base_template/templates/base_template/` | Shared shell chooses platform sidebar; topbar, POST logout, platform footer, accurate unavailable-state text |
| `base_template/static/base_template/css/platform.css` | Platform forms, detail cards, responsive navigation and footer |
| `base_template/static/base_template/css/vendor-controls.css` | Token-based themes for actual DataTables 2 and Select2 4 markup, including search, dropdowns and pagination |
| `base_template/static/base_template/js/platform.js` | DataTables initialization and mobile menu; shared forms.js initializes database-backed Select2 and phone/number behavior |
| `tenants/tests_platform.py` | HTTP/service/database regression coverage for the delivered workflow |
| `.gitignore` | Local QA artifacts excluded |

The shared base CSS and existing palette are reused. Only the new platform company table uses DataTables at this checkpoint; existing company lists remain server-rendered. Select2 single-selects are browser verified; the shared multiselect theme is present but no multiselect business workflow ships in this slice. Bootstrap is not needed for these pages.

Pinned CDN sources: [jQuery 3.7.1](https://releases.jquery.com/jquery/), [DataTables 2.3.4](https://cdn.datatables.net/2.3.4/), [Select2 installation](https://select2.org/getting-started/installation/) (4.0.13). Vendor default colour themes are not loaded. HTML forms and server pagination remain as fallback if the libraries cannot load.

## Environment and migrations

No new environment variables are required. `.env.example` remains unchanged. The existing POSTGRES_* settings are reused; no passwords or company data are added to environment files.

The original two auditlog migrations remain. Three additional migrations now change Company defaults, create the company-code sequence, and add current-administrator uniqueness constraints. No original migrations were rewritten, no development database was reset, and no demo data was created in the development database. The browser QA database is isolated from normal development data.

Audit rows are protected in model/queryset APIs and by PostgreSQL. Because actor deletion would require SET_NULL updates to historical rows, the trigger also blocks deletion of actors referenced by audit history: deactivate accounts/memberships instead. A future approved erasure mechanism would need an explicit exception design. Hash chaining is not implemented. This does not claim protection against a privileged database owner deliberately disabling the trigger.

## Verification and limitations

Final checks: **131/131 PostgreSQL tests passed** (existing coverage updated for the new contract plus seven correction/concurrency tests), Django
system check clean, no migration drift, and all migrations applied to the
existing development database. A separate browser context with JavaScript disabled
also displayed the company and the correct server-pagination count.

See PHASE_STATUS.md for final executed check results. Browser walkthrough used a separately migrated PostgreSQL database containing only an initial root account, then created the company and administrator through the UI without seed_demo. Screenshots and computed styles were inspected at 1440px, 768px and 375px. Server-side company counts, Select2 feature choices and native fixed choices, POST writes and company-admin login were exercised.

P1 is not complete. Outstanding: company organization/schedule writes, full membership/permission scopes including M2M validation and delegated grants, employee forms/detail/history, moving old feature pages out of base_template, remaining component groups, selective composite tenant foreign keys, job tenant handling when jobs are added, and RLS before pilot. Platform packages/subscriptions and audited support entry are not implemented. Enabling leave/attendance/payroll access does not implement those future workflows.

## Exact next implementation

Company setup slice: action and branch/department scope authorization; branch, department and designation create/edit; shifts, department shifts, weekly offs, holidays and attendance settings. Reuse the component themes and introduce date/modal controls only with real flows. Then employee hire/detail/transfer/revision/termination forms and the full P1 cold-start acceptance test. P2 starts after P1 acceptance.

## Correction file inventory

- `common/identifiers.py`: `unique_slug`, `prepare_company_identifiers`; sequence-backed code allocation and serialized slug collision handling.
- `common/forms.py`: `normalize_bd_phone`, `BangladeshPhoneInput`, `StyledFormMixin`.
- `common/admin.py`: shared root-only `TenantOwnedAdmin`, company-scoped FK/M2M form querysets and rendering; scheduling, organization, employee and membership admin registrations reuse it.
- `tenants/models.py`: Bangladesh defaults, generated identifiers on normal save, phone normalization; `tenants/admin.py` makes code/slug read-only.
- `accounts/models.py`: two conditional unique constraints for current master administrators.
- `tenants/platform_services.py`, `forms.py`, `platform_views.py`, `urls.py`: revised create/edit master workflow, protected identifiers and explicit list URL.
- `base_template/static/base_template/js/forms.js`, `css/input-conventions.css`: reusable phone/numeric controls including Django support admin numeric styling.
- `base_template/static/base_template/css/`, application templates: readable four-space formatting, centered containers, compact company overview, aligned employee/action columns and native fixed selects.
- `tenants/management/commands/seed_demo.py`: no longer creates the shared multi-company administrator.
- `.editorconfig`: four-space and UTF-8 conventions.
- `tenants/tests_platform_corrections.py`: actual admin GET/valid POST/cross-company rejection, generated identifiers, form tampering, uniqueness, edit/login and concurrent creation.

The user explicitly approved ending the two existing group.admin demo memberships. Both were ended through the audited service; Ayesha and Imran remain their companies' administrators. The user's Felan Tech administrator, all accounts, old identifiers and historical rows were preserved. No .env variables were added.

Final browser regression also opened and saved CompanyAttendanceSettings in Django admin; integer fields rejected letters and reported appearance=textfield. Form margins measured equal at 1440/768/375 pixels (164/20/12 pixels per side within the content area). The mobile company table scrolls locally without page overflow.
