# Branch status — `feature/organisation-catalogue-ui`

Notes for this branch only. The long-running roadmap status lives in
`docs/PHASE_STATUS.md`, which this branch deliberately does not touch — Ajay's
session owns `docs/`.

## Designations are independent of departments

Root now keeps **two flat lists**: departments and designations. Neither knows
about the other. "Manager" is created once for the whole platform.

Each **company** decides which of its own designations sit under which of its
own departments. That relation lives on `CompanyDesignation`
(`company_department` → `designation`), so one company can put Manager under
Sales *and* under Production, and another company can put the same Manager
somewhere else entirely. Nothing about one company's choice constrains
another's.

Migration `organization/0004_designation_independent_of_department.py` is
additive and collapses the old per-department duplicates. It is reversible:
the reverse re-files each designation under the department of its first
company placement.

## Wording

User-visible text now uses:

| Not this | This |
|---|---|
| Hire / Hire an employee | **Create employee**, **Edit employee** |
| Job title, title, position, role | **Designation** |
| Adopt / adopted / adoption | **Add department**, **Assign designations**, **Departments** |
| Catalogue, platform catalogue | nothing in company screens; **Departments** / **Designations** in the root panel |

Internal names deliberately kept: the models `CompanyDepartment` and
`CompanyDesignation`, and the `adoption_*` / `catalogue_*` module and URL
**namespace** names. Only what a person reads changed. The root URL *paths*
did change — the screens now live at `/platform/departments/` and
`/platform/designations/` rather than under `/platform/catalogue/`, because the
address bar is something the operator reads.

### Wording still open

Three choices worth a second opinion; none of them blocks anything.

1. **"Platform lists"** — the breadcrumb on the root department/designation
   forms. It is the one place with no better single word for "the two root
   lists together". "Platform" alone reads like the company list.
2. **"Add a department" (company) vs "Add department" (root)** — the same
   button label now appears on both surfaces meaning two different things:
   root *creates* a name for everyone, a company *starts using* one in a
   branch. The screens around them make it clear, but if it reads ambiguous
   in use, the company one could become "Use a department here".
3. **"USED BY" column** on the root lists — it counts company rows, so a
   single company that puts Manager under two of its departments counts as 2.
   That is the honest number for "how many places is this in use", but if it
   should read as "how many companies", the annotation needs
   `distinct=True` on the company rather than the link.
