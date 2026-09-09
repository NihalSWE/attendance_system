# External Attendance Management Project

> **Important:** This folder is not part of the current Polymer project. It contains planning notes for a separate, future multi-tenant attendance management system.

No application code, migrations, settings, or dependencies from this folder should be connected to the current project.

In the new attendance repository, the documentation directory will be **docs/** directly under the project root. Copy the **contents** of the current external_project_plans/attendance_management_system/ directory into docs/, including all Markdown files, schema files, schema_diagrams/ and scripts/. The old external_project_plans and attendance_management_system wrapper folders are not required in the destination. The current source directory has not been renamed or moved.

The new entry point is **docs/NEXT_CHAT_START_HERE.md**. Documentation links remain relative to their containing files, so they continue to work after copying the contents together.

## Documents

- **Apps and shared UI:** [Project setup](PROJECT_SETUP.md) — commands for all 13 apps, including the model-free base_template app; shared template/static layout; FastAPI integration direction.

- **Start implementation here:** [Next-chat starting instructions](NEXT_CHAT_START_HERE.md) — reading order, exact first task, preserved decisions and a copyable opening prompt.
- **Phased delivery plan:** [Implementation roadmap](IMPLEMENTATION_ROADMAP.md) — initial leave/payroll/salary scope, phase dependencies, device-free work, real-device verification and completion criteria.
- **Live handoff checkpoint:** [Phase status](PHASE_STATUS.md) — all implementation phases currently not started; records the next action and the evidence each future session must update.

- **Device attendance rules:** [Device attendance policy](DEVICE_ATTENDANCE_POLICY.md) — restricted devices versus any eligible department/branch/company device; employee/branch/company precedence; enrollment versus permission; IN/OUT across different devices; historical/offline decisions; concrete examples.

- **Whole schema with every field:** [Interactive viewer](ATTENDANCE_SCHEMA.html) — search tables, zoom, and highlight FK relationships. It works locally in a browser with no external service.
- **Diagram files:** [Full SVG](ATTENDANCE_SCHEMA.svg), [editable DBML](ATTENDANCE_SCHEMA.dbml), and [complete field-level Markdown](FULL_DATABASE_SCHEMA.md). The Markdown document links to 12 smaller app SVG diagrams. Every applicable shared field and all five implicit M2M tables are expanded.

- **Business context for another chat:** [Project handoff](PROJECT_HANDOFF.md) — original device problems, confirmed business requirements, changed decisions, basic versus advanced scope, implementation gaps, and the next deliverables. Read it after the starting instructions and roadmap.
- **Then read:** [Database model plan](DATABASE_MODEL_PLAN.md) — plain-language explanations of all 86 models, grouped into 12 domain apps. base_template is the 13th app and has no models. Every model has a purpose, its related models, and a practical example. Includes employee-code history and comparisons of easily confused tables.
- **Then read for implementation detail:** [Leave and salary management](LEAVE_AND_SALARY_MANAGEMENT.md) — leave and payroll field lists, approval flows, leave balances, salary payments and dues, loans, advances, LFA, reversals, and calculation rules.
- **Field contract:** [Model field dictionary](MODEL_FIELD_DICTIONARY.md) — proposed fields for all 86 models, with Django field names/types, FK/O2O/M2M relations, nullability, status choices, deletion behavior, and important constraints. This is documentation, not model code.
- **Database-wide map:** [Proposed PostgreSQL schema](DATABASE_SCHEMA.md) — ER diagrams, all 86 relational tables, 5 implicit M2M junction tables, tenant ownership paths, critical constraints, indexes, idempotency, retention, and scaling notes.

The model explanations describe a proposed design. Supporting records are created automatically where appropriate, and advanced features are optional; basic operation does not require configuring every table.

The current priority is a first release with useful leave, payroll, payment/dues, adjustment and simple advance workflows. A physical device is expected in roughly a week but is not currently available. The roadmap proceeds with simulated/manual attendance and verifies the real adapter in a separate phase. Advanced leave, LFA, loans and richer salary configuration remain planned extensions; they are not implicitly implemented just because their schema is documented.

The user will create the Django project and 13 app skeletons, including base_template. The next chat should verify that setup and continue unfinished foundation/domain work. Architecture is a modular Django monolith; the user's preferred framework for API modules is FastAPI, introduced when required over reusable domain services. The earlier DRF recommendation is superseded. The updated continuation prompt is in NEXT_CHAT_START_HERE.md.

The authentication model must be named exactly **accounts.User**, configured through `AUTH_USER_MODEL = "accounts.User"`. Runtime code uses `User = get_user_model()`. The default concrete Django User and alternative names such as CustomUser are excluded. PROJECT_SETUP.md records the relation/migration conventions and the continuation prompt repeats this requirement.

No Django project, Python model classes, migrations, SQL schema, or connection to the surrounding Polymer project has been created. These Markdown files are intended to be copied together into the future attendance repository or supplied together to another chat.

Preserve schema_diagrams/ and scripts/ within docs/ to retain diagrams and the offline viewer. From the new project root, `node docs/scripts/build_schema.cjs` regenerates documentation from docs/MODEL_FIELD_DICTIONARY.md; it does not create or connect to a database. The script resolves paths relative to its location. The diagram contains 91 domain tables, 1,639 columns, and 465 FK relations after the device-scope additions. Django framework/authentication infrastructure is outside this count.
