"""Bulk employee import from a CSV or Excel file (Ajay, 2026-09-20).

A new company arrives with 400-600 people in a spreadsheet. Typing them in one
at a time is not an option, and neither is writing half of them: the whole file
lands or none of it does.

The shape:

1. **Download the template** — the exact column headings, with two example rows
   and a sheet of notes. Nothing else is accepted as a heading.
2. **Upload** — the file is read and every row is checked, but *nothing is
   written*. The result is a preview naming every bad row and why.
3. **Confirm** — only when every row is good. The rows are re-checked from the
   preview stored in the session (never trusted as they stand, because a
   session is still the reader's own input) and written in one transaction,
   with one audit line for the import.

Branch, department and designation are **matched** against what the company
already has, by name or by code, and never created: a typo in the spreadsheet
must not quietly invent a department. The Employee ID is the number the
terminals will know the person by, so it has to be digits.

Who may import: ``employees.edit`` in the branch a row names, and, because
creating someone sets their pay, ``salary.prepare`` in that branch as well —
exactly what the one-at-a-time Create employee page asks for. A branch manager
holds both in their own branches, so they import into their own branches only.
"""

import csv
import datetime
import io
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from access_control.branch_access import ALL_BRANCHES, branches_for
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from employees.models import EmployeeAssignment, EmployeeCompensation
from employees.services import create_employee
from organization.models import Branch, Department, Designation
from organization.services import require_company_membership

#: (key, heading, required). The heading is what the file must say, and what
#: the downloaded template writes.
COLUMNS = (
    ("employee_id", "Employee ID", True),
    ("name", "Name", True),
    ("branch", "Branch", True),
    ("department", "Department", True),
    ("designation", "Designation", True),
    ("joining_date", "Joining date", True),
    ("pay_basis", "Pay basis", False),
    ("base_rate", "Base rate", False),
    ("phone", "Phone", False),
    ("email", "Email", False),
)
HEADINGS = [heading for _key, heading, _required in COLUMNS]
REQUIRED = [heading for _key, heading, required in COLUMNS if required]

#: One upload. Comfortably above the 400-600 a new company brings, and low
#: enough that a wrong file is refused instead of tying up a worker.
MAX_ROWS = 2000

PAY_BASES = {value for value, _label in EmployeeCompensation.PayBasis.choices}

#: Day first, because that is how the dates in these spreadsheets are written.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")

EXAMPLE_ROWS = (
    ("445962", "Ajay Kumar", "Head Office", "Software", "Developer",
     "2026-01-15", "monthly", "45000", "01700000000", "ajay@example.com"),
    ("445963", "Dia Rahman", "Head Office", "Software", "Team Lead",
     "2026-02-01", "", "", "", ""),
)

NOTES = (
    "Fill in one row per person and keep the headings exactly as they are.",
    "Employee ID: digits only. This is the number the attendance terminals "
    "will know the person by, so it must not already belong to someone whose "
    "placement is still open.",
    "Name: the full name. Everything before the last space is the first name.",
    "Branch, Department and Designation: they must already exist in the "
    "software, and are matched on their name or their code. A department must "
    "belong to the branch on the same row, and a designation to the department.",
    "Joining date: 2026-01-31, or 31/01/2026 (day first).",
    "Pay basis and Base rate may be left empty; the import page then asks you "
    "for one default to use for those rows. Pay basis is monthly, daily or hourly.",
    "Phone and Email may be left empty.",
)


# --- reading a file --------------------------------------------------------


def _text(value):
    """One cell as trimmed text, however the file spelled it."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float):
        # Excel hands back 445962.0 for a number typed as 445962.
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value).strip()


def _rows_from_csv(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 decodes anything
        raise ValidationError({"upload": "That file is not readable as text."})
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _rows_from_xlsx(data):
    try:
        from openpyxl import load_workbook
    except ModuleNotFoundError:  # pragma: no cover - listed in requirements.txt
        raise ValidationError({
            "upload": "Excel support is not installed on this server. Save the "
                      "file as CSV and upload that instead."
        })
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValidationError({
            "upload": "That file could not be opened as an Excel workbook. If it "
                      "is an old .xls file, save it as .xlsx or CSV first."
        })
    try:
        sheet = book.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        book.close()


def read_file(upload):
    """``list[dict]`` of raw cell text, one per data row, with its line number.

    Raises ValidationError with a reader-facing message when the file itself is
    wrong: unreadable, missing a heading, empty, or too long.
    """
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    if not data:
        raise ValidationError({"upload": "That file is empty."})
    if name.endswith(".xls"):
        raise ValidationError({
            "upload": "The old .xls format cannot be read. Open it and save it "
                      "as .xlsx or CSV, then upload that."
        })
    if name.endswith((".xlsx", ".xlsm")):
        table = _rows_from_xlsx(data)
    elif name.endswith((".csv", ".txt")):
        table = _rows_from_csv(data)
    else:
        raise ValidationError({
            "upload": "Upload a .csv or .xlsx file — that is what the template "
                      "downloads as."
        })

    table = [row for row in table if any(_text(cell) for cell in row)]
    if not table:
        raise ValidationError({"upload": "That file has no rows in it."})

    heading_row, *body = table
    headings = [_text(cell) for cell in heading_row]
    lookup = {heading.casefold(): index for index, heading in enumerate(headings) if heading}
    missing = [name for name in REQUIRED if name.casefold() not in lookup]
    if missing:
        raise ValidationError({
            "upload": "The first row must be the column headings from the "
                      f"template. Missing: {', '.join(missing)}."
        })
    if len(body) > MAX_ROWS:
        raise ValidationError({
            "upload": f"That file has {len(body)} rows. Split it into files of "
                      f"{MAX_ROWS} or fewer and import them one after another."
        })
    if not body:
        raise ValidationError({"upload": "That file has headings but no people in it."})

    rows = []
    for offset, cells in enumerate(body):
        row = {"line": offset + 2}
        for key, heading, _required in COLUMNS:
            index = lookup.get(heading.casefold())
            row[key] = _text(cells[index]) if index is not None and index < len(cells) else ""
        rows.append(row)
    return rows


# --- checking the rows -----------------------------------------------------


def import_scope(user, company_id):
    """``(membership, branch_ids)`` for someone who may import; refuses if none."""
    membership = require_company_membership(user, company_id)
    branch_ids = branches_for(user, company_id, "employees.edit")
    if not branch_ids:
        raise PermissionDenied(
            "Importing employees requires owner, company administrator or "
            "branch access to create employees."
        )
    return membership, branch_ids


def _match(index, text, parent=None):
    """One row of a name/code lookup, or None. Case and spacing are forgiven."""
    return index.get((parent, _key(text)))


def _key(word):
    return (word or "").casefold().strip()


def _index(queryset, parent=None):
    """``{(parent_id, name or code): row}``, or ``{name or code: row}``.

    Departments are keyed by their branch and designations by their department,
    because two branches routinely hold departments of the same name — copying
    a branch's structure into another branch is a button on the Departments
    page. Without the parent in the key, "Software" would be ambiguous in every
    company that has more than one branch.

    A word that is ambiguous *within one parent* is dropped instead of guessed:
    guessing is exactly the silent mistake this import must not make. The row
    then reads "No active department called ..." and the reader uses the code.
    """
    index, seen = {}, set()
    for row in queryset:
        prefix = getattr(row, parent) if parent else None
        for word in (row.name, row.code):
            key = (prefix, _key(word))
            if not key[1]:
                continue
            if key in seen:
                index.pop(key, None)
            else:
                seen.add(key)
                index[key] = row
    return index


def _date(text):
    for pattern in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def check(user, company_id, rows, *, default_pay_basis="", default_base_rate=None):
    """Fill in each row's matched ids and its errors. Writes nothing.

    Returns the same rows, each gaining ``branch_id`` / ``department_id`` /
    ``designation_id`` / ``first_name`` / ``last_name`` / a normalised
    ``joining_date``, ``pay_basis`` and ``base_rate``, and an ``errors`` list.
    """
    _membership, branch_ids = import_scope(user, company_id)
    # Resolved once: with 600 rows, asking per row would re-read the grants 600
    # times for the same answer.
    pay_branches = branches_for(user, company_id, "salary.prepare")
    default_rate = None if default_base_rate in (None, "") else Decimal(str(default_base_rate))

    with use_company(company_id):
        branches = _index(Branch.objects.filter(status=ActiveStatus.ACTIVE))
        department_rows = list(Department.objects.filter(status=ActiveStatus.ACTIVE))
        designation_rows = list(Designation.objects.filter(status=ActiveStatus.ACTIVE))
        departments = _index(department_rows, "branch_id")
        designations = _index(designation_rows, "department_id")
        # Only to tell "spelled wrong" apart from "right name, wrong branch".
        department_words = {_key(word) for row in department_rows
                            for word in (row.name, row.code) if word}
        designation_words = {_key(word) for row in designation_rows
                             for word in (row.name, row.code) if word}
        taken = set(
            EmployeeAssignment.objects.filter(
                status=EmployeeAssignment.Status.ACTIVE, effective_to__isnull=True,
            ).values_list("employee_code", flat=True)
        )

    seen_ids = {}
    for row in rows:
        errors = []
        row["errors"] = errors

        # --- Employee ID
        code = row["employee_id"].strip()
        if not code:
            errors.append("Employee ID is missing.")
        elif not code.isdigit():
            errors.append(
                f"Employee ID “{code}” is not digits. The terminals only accept a number."
            )
        elif len(code) > 20:
            errors.append(f"Employee ID “{code}” is too long.")
        elif code in taken:
            errors.append(f"Employee ID {code} already belongs to someone still placed.")
        elif code in seen_ids:
            errors.append(f"Employee ID {code} is also on row {seen_ids[code]} of this file.")
        else:
            seen_ids[code] = row["line"]
        row["employee_id"] = code

        # --- Name
        name = " ".join(row["name"].split())
        if not name:
            errors.append("Name is missing.")
        first, _sep, last = name.rpartition(" ")
        row["first_name"], row["last_name"] = (first or name, last if first else "")
        row["name"] = name

        # --- Where they sit
        branch = _match(branches, row["branch"]) if row["branch"] else None
        if not row["branch"]:
            errors.append("Branch is missing.")
        elif branch is None:
            errors.append(
                f"No active branch called “{row['branch']}”. Add it first, or "
                "correct the spelling."
            )
        elif branch_ids is not ALL_BRANCHES and branch.pk not in branch_ids:
            errors.append(f"You cannot add people to {branch.name}.")
        elif pay_branches is not ALL_BRANCHES and branch.pk not in pay_branches:
            errors.append(
                f"Adding someone to {branch.name} sets their pay, which needs "
                "salary access for that branch."
            )

        department = (
            _match(departments, row["department"], branch.pk)
            if branch is not None and row["department"] else None
        )
        if not row["department"]:
            errors.append("Department is missing.")
        elif branch is None:
            pass  # The branch error above already says what to fix.
        elif department is None and _key(row["department"]) in department_words:
            errors.append(
                f"“{row['department']}” is not a department of {branch.name}."
            )
        elif department is None:
            errors.append(f"No active department called “{row['department']}”.")

        designation = (
            _match(designations, row["designation"], department.pk)
            if department is not None and row["designation"] else None
        )
        if not row["designation"]:
            errors.append("Designation is missing.")
        elif department is None:
            pass  # Likewise: fix the department first.
        elif designation is None and _key(row["designation"]) in designation_words:
            errors.append(
                f"“{row['designation']}” does not belong to {department.name}."
            )
        elif designation is None:
            errors.append(f"No active designation called “{row['designation']}”.")

        row["branch_id"] = branch.pk if branch else None
        row["department_id"] = department.pk if department else None
        row["designation_id"] = designation.pk if designation else None

        # --- Joining date
        if not row["joining_date"]:
            errors.append("Joining date is missing.")
        else:
            day = _date(row["joining_date"])
            if day is None:
                errors.append(
                    f"“{row['joining_date']}” is not a date we can read. Write "
                    "it as 2026-01-31 or 31/01/2026."
                )
            else:
                row["joining_date"] = day.isoformat()

        # --- Pay
        basis = (row["pay_basis"] or default_pay_basis or "").strip().casefold()
        if not basis:
            errors.append(
                "No pay basis on this row, and no default chosen above."
            )
        elif basis not in PAY_BASES:
            errors.append(
                f"“{row['pay_basis']}” is not a pay basis. Use monthly, daily or hourly."
            )
        row["pay_basis"] = basis

        raw_rate = (row["base_rate"] or "").replace(",", "").strip()
        rate = None
        if raw_rate:
            try:
                rate = Decimal(raw_rate)
            except InvalidOperation:
                errors.append(f"“{row['base_rate']}” is not an amount.")
        elif default_rate is not None:
            rate = default_rate
        else:
            errors.append("No base rate on this row, and no default chosen above.")
        if rate is not None and rate <= 0:
            errors.append("Base rate must be more than zero.")
            rate = None
        row["base_rate"] = format(rate, "f") if rate is not None else ""

        # --- Optional contact details
        if row["email"]:
            try:
                validate_email(row["email"])
            except ValidationError:
                errors.append(f"“{row['email']}” is not an email address.")
        if len(row["phone"]) > 32:
            errors.append("Phone number is too long.")

    return rows


def summarise(rows):
    bad = [row for row in rows if row["errors"]]
    return {"total": len(rows), "bad": len(bad), "good": len(rows) - len(bad)}


# --- writing ---------------------------------------------------------------


@transaction.atomic
def commit(*, actor, company_id, rows, default_pay_basis="", default_base_rate=None):
    """Create every row's employee, or none of them.

    The rows are re-checked here even though the preview checked them: they
    reach this call through the reader's own session, and a branch they may no
    longer add to (or a department deactivated in between) must be caught now
    rather than written.
    """
    membership, _branch_ids = import_scope(actor, company_id)
    rows = check(actor, company_id, rows,
                 default_pay_basis=default_pay_basis, default_base_rate=default_base_rate)
    bad = [row for row in rows if row["errors"]]
    if bad:
        raise ValidationError({
            "upload": f"{len(bad)} row{'s' if len(bad) != 1 else ''} can no longer "
                      "be imported. Upload the file again to see why."
        })

    zone = ZoneInfo(getattr(membership.company, "timezone", None) or "UTC")
    created = []
    with use_company(company_id):
        branches = {row.pk: row for row in Branch.objects.all()}
        departments = {row.pk: row for row in Department.objects.all()}
        designations = {row.pk: row for row in Designation.objects.all()}
        for row in rows:
            joined = datetime.date.fromisoformat(row["joining_date"])
            try:
                result = create_employee(
                    company=membership.company,
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    employee_code=row["employee_id"],
                    branch=branches[row["branch_id"]],
                    department=departments[row["department_id"]],
                    designation=designations[row["designation_id"]],
                    effective_from=datetime.datetime.combine(
                        joined, datetime.time.min, tzinfo=zone),
                    joining_date=joined,
                    pay_basis=row["pay_basis"],
                    base_rate=Decimal(row["base_rate"]),
                    created_by=actor,
                )
            except (ValidationError, IntegrityError) as exc:
                # One bad row rolls the whole import back, which is the point:
                # a half-imported company is worse than none.
                raise ValidationError({
                    "upload": f"Row {row['line']} ({row['name']}) could not be "
                              f"created, so nothing was imported: {exc}"
                }) from exc
            employee = result["employee"]
            changed = []
            if row["email"]:
                employee.work_email = row["email"]
                changed.append("work_email")
            if row["phone"]:
                employee.phone = row["phone"]
                changed.append("phone")
            if changed:
                employee.save(update_fields=changed)
            created.append(employee)

        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employees.imported", obj=membership.company,
            after={
                "count": len(created),
                "employee_codes": [row["employee_id"] for row in rows],
                "branch_ids": sorted({row["branch_id"] for row in rows}),
                "employee_ids": [employee.pk for employee in created],
            },
        )
    return created


# --- the downloadable template ---------------------------------------------


def template_csv():
    """The template as CSV bytes: the headings, two example rows, the notes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADINGS)
    for row in EXAMPLE_ROWS:
        writer.writerow(row)
    writer.writerow([])
    writer.writerow(["Delete the two example rows before you upload this file."])
    for note in NOTES:
        writer.writerow([note])
    # utf-8-sig so Excel opens Bangla and other non-ASCII names correctly.
    return buffer.getvalue().encode("utf-8-sig")


def template_xlsx():
    """The template as an .xlsx workbook: a People sheet and a Notes sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "People"
    sheet.append(HEADINGS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in EXAMPLE_ROWS:
        sheet.append(list(row))
    for index, heading in enumerate(HEADINGS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(14, len(heading) + 4)
    sheet.freeze_panes = "A2"

    notes = book.create_sheet("Notes")
    notes.append(["Delete the two example rows on the People sheet before you upload it."])
    for note in NOTES:
        notes.append([note])
    notes.column_dimensions["A"].width = 110
    for row in notes.iter_rows():
        row[0].alignment = row[0].alignment.copy(wrap_text=True)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
