# Project setup — user-created apps and shared UI

## Platform corrections — 2026-09-07

User feedback corrected the onboarding contract. Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) and [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md). Implemented one current master administrator per company with backend/database uniqueness, account editing, generated string codes/slugs, Bangladesh defaults, scoped Django admin forms, explicit `/platform/companies/`, centered responsive pages and readable four-space templates. No new environment variables. Existing data preserved; only the two shared demo memberships were ended with explicit user approval and audit entries. Full suite: 131 tests passed; the strengthened settings-admin valid-POST check also passed in the seven-test focused rerun. Browser creation/admin/feature flows and screenshots checked at 1440/768/375px. P1 remains in progress: next is company organization/scheduling writes and scoped authorization, then employee lifecycle pages. This supersedes earlier existing-account/role-picker examples.


> Current checkpoint (2026-09-07): P0 is verified; P1 platform onboarding is implemented, while company setup/employee writes and full scope enforcement remain outstanding. See PHASE_STATUS.md and PLATFORM_IMPLEMENTATION.md. Earlier statements about an uncreated project are historical planning context. Do not recreate scaffolding.

The user will create the project and app skeletons. The next chat verifies them and implements the remaining P0 foundation; it must not recreate scaffolding.

## Documentation destination

Use **docs/** directly in the new attendance project root. Copy all files and subdirectories from the current external_project_plans/attendance_management_system/ directory into it. The current directory is the source; no rename or move is being performed here.

```text
<attendance-project>/
  manage.py
  accounts/
  tenants/
  ... other domain apps ...
  base_template/
  docs/
    README.md
    NEXT_CHAT_START_HERE.md
    PHASE_STATUS.md
    IMPLEMENTATION_ROADMAP.md
    PROJECT_HANDOFF.md
    PROJECT_SETUP.md
    MODEL_FIELD_DICTIONARY.md
    DATABASE_MODEL_PLAN.md
    DATABASE_SCHEMA.md
    FULL_DATABASE_SCHEMA.md
    DEVICE_ATTENDANCE_POLICY.md
    LEAVE_AND_SALARY_MANAGEMENT.md
    ATTENDANCE_SCHEMA.dbml
    ATTENDANCE_SCHEMA.html
    ATTENDANCE_SCHEMA.svg
    schema_diagrams/
    scripts/
```

This is the intended destination layout, not a claim that the new application folders already exist. From the new project root, documentation rendering uses `node docs/scripts/build_schema.cjs`. The renderer and viewer use location-relative paths, so copying this structure preserves links. App-creation commands below still run from the directory containing manage.py, not from docs/.

## App inventory

Create **13 Django apps**: 12 domain apps plus the model-free `base_template` presentation app. The database design remains 86 domain models and 91 domain tables including implicit M2M tables. `base_template` adds no model/table and therefore does not appear as a table in the ER diagrams.

With Django installed in the active environment, run these from the new attendance project's directory containing manage.py. Skip any app already created.

```powershell
python manage.py startapp accounts
python manage.py startapp tenants
python manage.py startapp organization
python manage.py startapp employees
python manage.py startapp access_control
python manage.py startapp scheduling
python manage.py startapp devices
python manage.py startapp attendance
python manage.py startapp leaves
python manage.py startapp payroll
python manage.py startapp subscriptions
python manage.py startapp auditlog
python manage.py startapp base_template
```

Register the apps in INSTALLED_APPS after creating them. The next implementation step must create accounts.User and configure AUTH_USER_MODEL before generating/applying initial application migrations. Creating skeletons does not complete this custom-user work. Preserve any pre-existing migrations/data and inspect them if the project is not fresh.

## Required User model contract

This is an explicit user decision, not an optional naming suggestion:

- Define the application's custom authentication model with the exact class name **User** in the **accounts** app. Its model label is **accounts.User** and its default table name is **accounts_user**.
- Set the following in the attendance project's settings.py before initial migrations:

```python
AUTH_USER_MODEL = "accounts.User"
```

- In runtime code that needs the model class, use:

```python
from django.contrib.auth import get_user_model

User = get_user_model()
```

- Name the class User, not CustomUser, AccountUser or another alternative. Do not use/import the concrete default `django.contrib.auth.models.User` for this application's authentication or relations. All planned User references mean accounts.User.
- Using `AbstractUser` as the superclass is compatible with this decision: the concrete model remains the application's own accounts.User. The existing field proposal prefers AbstractUser; this naming clarification does not require a different inheritance strategy or additional model.
- For FK/O2O/M2M declarations to the authentication user, use `settings.AUTH_USER_MODEL` so relations remain swappable. This is the declarative equivalent of runtime `User = get_user_model()`, not a separate user identity. Use appropriate deletion behavior from the field dictionary.
- In data migrations use the historical model registry, e.g. `apps.get_model("accounts", "User")`, rather than importing the live runtime model. Keep the User model in accounts' first migration and use swappable migration dependencies where applicable.
- Before using get_user_model from future FastAPI integration, initialize Django's application registry appropriately. API request schemas must not introduce another persisted user model or independent business identity.

The next chat should verify the resolved runtime model label is accounts.User and inspect user relations before applying migrations. This contract adds no model/table; it makes the already-planned User identity unambiguous.

Do not create an additional Django app named FastAPI or install Django REST Framework as part of this setup. The user's selected future API framework is FastAPI. Its router/application modules can be added when API work is needed; they do not inherently need another Django app or another database model layer. [Django command reference](https://docs.djangoproject.com/en/5.2/ref/django-admin/#startapp), [FastAPI router organization](https://fastapi.tiangolo.com/tutorial/bigger-applications/).

## base_template responsibility

Use `base_template` for the shared page shell, navbar, footer, sidebar includes, shared UI components and common CSS/JavaScript. Keep employee/leave/payroll page templates within their owning domain app. Keep business calculations and permission decisions in domain/access services rather than this presentation app.

Suggested future layout (the startapp command does not create these directories):

```text
base_template/
  templates/
    base_template/
      base.html
      includes/
        navbar.html
        footer.html
        sidebar.html
        sidebars/
          platform.html
          company.html
          employee.html
  static/
    base_template/
      css/
        base.css
      js/
        base.js
```

Domain pages extend `base_template/base.html`. Namespaced template/static paths avoid collisions with other apps.

Different sidebars may live in the includes/sidebars directory when platform/company/employee layouts differ substantially. Prefer a shared menu structure filtered by effective permissions and feature access when only the links differ. Reuse common pieces so three groups do not acquire three independent copies of the entire base layout.

Select navigation through a reviewed mapping/context helper based on the active company membership, platform role, effective action permissions and enabled features. Do not rely solely on a designation/group label. Hiding a menu item is presentation; each page/API operation must independently enforce the same access and tenant boundaries. Do not derive template paths directly from arbitrary request values.

## Django with future FastAPI modules

User decision: **FastAPI, not Django REST Framework, is the preferred framework when API modules are required.** REST is an API style; FastAPI can implement REST endpoints. This does not change the modular-monolith decision or require an immediate microservice split.

Django initially owns page rendering, the ORM, migrations and core business services. Keep services callable from pages, workers and future FastAPI routers. Pydantic request/response schemas may describe public API contracts; they are not a second persistence model or migration authority.

When API work starts, introduce a small FastAPI entry point and domain routers around the existing services. Choose and test ASGI routing/deployment, Django initialization, sync/async ORM/transaction boundaries, authentication adaptation and tenant-context cleanup. Django's page middleware must not be assumed to automatically secure FastAPI endpoints. Enforce shared business permissions and company ownership explicitly on every API path. Do not add DRF as an intermediate step.

Public ERP API contracts, integration identities, versioning, external-ID mapping, pagination, idempotent writes and any webhook delivery remain integration-phase design work. Vendor device protocols are separate contracts. Sharing service rules keeps the option open without implementing speculative API endpoints now.
