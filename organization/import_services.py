"""Bulk employee import from a CSV or Excel file: Employee ID and Name only.

Reads .csv, .xlsx and the old .xls (Excel 97-2003) - by what the file *is*,
not what it is named - because a client's employee list arrives in all three.

The file carries two columns and nothing else - **Employee ID** and **Name**, the
same two things the attendance terminals know a person by. The upload also
says which **branch** they join. Everything else about them (department,
designation, salary, contact details) is filled in afterwards on Edit
employee, one person at a time, as HR gets to them.

Each person is created exactly the way "import from a device" creates one, so
both ways in behave the same:

- placed in the branch's **Unassigned** department and designation
  (``devices.services.mapping.unassigned_placement``, made on first use),
  because a placement needs both;
- **no salary record**, so payroll skips them by name until one is set;
- flagged ``needs_hr_review`` in their metadata.

They count for attendance at once (with no department shift they work the
company shift), and the Employees list shows them under their branch and
Employee ID. Their placement starts at midnight today in the company's timezone,
so when HR sets the real department "from today" on Edit employee it corrects
that row instead of adding a one-day history line.

**Which branch:** the company (anyone who may add people in every branch)
picks it from a dropdown that starts on the company's default branch - not
changing it means the default branch. A branch manager imports into their own
branch only: with one branch the field is locked and a posted value is
ignored; with several, the dropdown lists only theirs. The service checks it
again either way.

**The steps:** download the demo file, upload, look at the preview (nothing is
written; every bad row is named with its line and the reason), then confirm.
Confirm writes every row or none, in one transaction with one audit line, and
re-checks everything first, because the rows come back through the reader's
own session.

Who may import into a branch: ``employees.edit`` there - the same permission
Create employee asks for. No pay is set, so nothing about salary is needed.
"""

import csv
import datetime
import io
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from access_control.branch_access import ALL_BRANCHES, branches_for
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization.models import Branch
from organization.services import require_company_membership

#: The file format: these two headings in the first row, one person per row.
HEADINGS = ["Employee ID", "Name"]

#: Headings accepted for each column, compared with spaces, dashes and case
#: ignored - so "Employee ID", "EMP-ID", "Emp Id", "EMPID" and a bare "ID" all
#: work. Real files from a client call the column "ID" as often as anything
#: else; any other column (a serial number, say) is ignored.
#:
#: Two tiers, and the reason matters: a bare "ID" or "Code" is often something
#: else entirely - a row number, a department code - sitting to the LEFT of the
#: real column. Taking the first heading that matched imported those row
#: numbers as employee IDs, digits and all, so the preview accepted them and
#: ninety people arrived numbered 1 to 90. A specific heading anywhere in the
#: row therefore beats a vague one, wherever each sits.
_HEADING_WORDS = {
    "employee_id": [
        {"empid", "employeeid", "empno", "employeeno", "employeecode"},
        {"id", "idno", "code"},
    ],
    "name": [
        {"name", "employeename", "fullname"},
        {"employee"},
    ],
}

#: One upload. Comfortably above the 400-600 a new company brings, and low
#: enough that a wrong file is refused instead of tying up a worker.
MAX_ROWS = 2000

#: The longest Employee ID the terminals take (the device import uses the same).
MAX_ID_LENGTH = 20

DEMO_ROWS = (
    ("445961", "Sajal Ahmed"),
    ("445962", "Ajay Kumar"),
    ("445963", "Dia Rahman"),
    ("445964", "Md Fazle Rabbi"),
    ("445965", "Nusrat Jahan"),
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


def _heading_key(text):
    """``(column, tier)`` for one heading; tier 0 is a specific name."""
    word = re.sub(r"[^a-z0-9]", "", text.casefold())
    for key, tiers in _HEADING_WORDS.items():
        for tier, words in enumerate(tiers):
            if word in words:
                return key, tier
    return None, None


def _rows_from_csv(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


#: How an old Excel file starts. Excel writes BIFF inside an OLE container
#: (d0cf11e0); a bare BIFF stream - what some exporters and our own test
#: fixture produce - starts with its BOF record instead.
BIFF_SIGNATURES = (b"\xd0\xcf\x11\xe0", b"\x09\x00", b"\x09\x02",
                   b"\x09\x04", b"\x09\x08")


def _looks_like_html(data):
    """True for an HTML table saved with a spreadsheet name."""
    head = data[:4096].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<table", b"<?xml")) or b"<table" in head


def _rows_from_xls(data):
    """The old Excel 97-2003 format (BIFF), which openpyxl cannot read at all."""
    try:
        import xlrd
    except ModuleNotFoundError:  # pragma: no cover - listed in requirements.txt
        raise ValidationError({
            "upload": "Old Excel (.xls) files cannot be read on this server. Save "
                      "the file as .xlsx or CSV and upload that instead."
        })
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        # Also lands here for another OLE document (a .doc renamed .xls) and
        # for a password-protected workbook.
        raise ValidationError({
            "upload": "That file could not be opened as an Excel workbook. If it "
                      "is password-protected, remove the password; otherwise save "
                      "it as .xlsx or CSV and upload that."
        })
    sheet = book.sheet_by_index(0)
    rows = []
    for index in range(sheet.nrows):
        row = []
        for cell in sheet.row(index):
            value = cell.value
            if cell.ctype == xlrd.XL_CELL_DATE:
                value = xlrd.xldate_as_datetime(value, book.datemode)
            elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                value = bool(value)
            elif cell.ctype == xlrd.XL_CELL_ERROR:
                value = ""
            row.append(value)
        rows.append(row)
    return rows


def _rows_from_xlsx(data):
    try:
        from openpyxl import load_workbook
    except ModuleNotFoundError:  # pragma: no cover - listed in requirements.txt
        raise ValidationError({
            "upload": "Excel files cannot be read on this server. Save the file "
                      "as CSV and upload that instead."
        })
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValidationError({
            "upload": "That file could not be opened as an Excel workbook. Save "
                      "it as CSV and upload that instead."
        })
    try:
        return [list(row) for row in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()


def read_file(upload):
    """``list[dict]`` of ``{"line", "employee_id", "name"}``, one per person.

    Reads CSV, .xlsx (openpyxl) and the old .xls (xlrd) - a client's list
    arrives in any of the three.

    Raises ValidationError with a reader-facing message when the file itself is
    wrong: empty, unreadable, missing a heading, or too long.
    """
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    if not data:
        raise ValidationError({"upload": "That file is empty."})
    # What the file *is*, not what it is called: people rename spreadsheets,
    # and "Save as .xls" in some tools writes .xlsx (and the other way round).
    if data.startswith(b"PK\x03\x04"):
        table = _rows_from_xlsx(data)
    elif data.startswith(BIFF_SIGNATURES):
        table = _rows_from_xls(data)
    elif name.endswith((".csv", ".txt", ".xls", ".xlsx", ".xlsm")):
        # Named like a spreadsheet but holding text: several systems "export to
        # Excel" by writing a CSV, or an HTML table, and naming it .xls.
        if _looks_like_html(data):
            raise ValidationError({
                "upload": "That file is a web page saved with a spreadsheet name, "
                          "not a spreadsheet. Open it in Excel and use Save As to "
                          "make a .xlsx or CSV file, then upload that."
            })
        table = _rows_from_csv(data)
    else:
        raise ValidationError({
            "upload": "Upload a .csv file in the demo file's format "
                      "(Excel .xlsx and .xls files also work)."
        })

    table = [row for row in table if any(_text(cell) for cell in row)]
    if not table:
        raise ValidationError({"upload": "That file has no rows in it."})

    heading_row, *body = table
    columns = {}
    headings = [_text(cell) for cell in heading_row]
    for index, heading in enumerate(headings):
        key, tier = _heading_key(heading)
        # A specific heading wins over a vague one wherever each sits; between
        # two equally specific ones the left-hand column wins.
        if key and (key not in columns or tier < columns[key][0]):
            columns[key] = (tier, index)
    columns = {key: index for key, (_tier, index) in columns.items()}
    missing = [heading for key, heading in zip(("employee_id", "name"), HEADINGS)
               if key not in columns]
    if missing:
        raise ValidationError({
            "upload": "The first row must be the headings Employee ID and Name, as in "
                      f"the demo file. Missing: {', '.join(missing)}. "
                      f"Found: {', '.join(h for h in headings if h) or 'nothing'}."
        })
    if not body:
        raise ValidationError({"upload": "That file has the headings but no people."})
    if len(body) > MAX_ROWS:
        raise ValidationError({
            "upload": f"That file has {len(body)} people. Split it into files of "
                      f"{MAX_ROWS} or fewer and import them one after another."
        })

    rows = []
    for offset, cells in enumerate(body):
        row = {"line": offset + 2}
        for key, index in columns.items():
            row[key] = _text(cells[index]) if index < len(cells) else ""
        rows.append(row)
    return rows


# --- who may import, and where ---------------------------------------------


def import_scope(user, company_id):
    """``(membership, branch_ids)``: where ``user`` may import; refuses if nowhere."""
    membership = require_company_membership(user, company_id)
    branch_ids = branches_for(user, company_id, "employees.edit")
    if not branch_ids:
        raise PermissionDenied(
            "Importing employees needs access to add employees in a branch."
        )
    return membership, branch_ids


def import_branches(user, company_id):
    """The active branches ``user`` may import into, default branch first."""
    _membership, branch_ids = import_scope(user, company_id)
    with use_company(company_id):
        queryset = Branch.objects.filter(status=ActiveStatus.ACTIVE)
        if branch_ids is not ALL_BRANCHES:
            queryset = queryset.filter(pk__in=branch_ids)
        return queryset.order_by("-is_default", "name")


def default_branch(user, company_id):
    """Where the file goes when no other branch is chosen.

    The company's default branch for someone who may import everywhere; for a
    branch login, the first of their own branches - a branch manager never
    falls through to a branch that is not theirs.
    """
    _membership, branch_ids = import_scope(user, company_id)
    with use_company(company_id):
        if branch_ids is ALL_BRANCHES:
            found = Branch.objects.filter(
                status=ActiveStatus.ACTIVE, is_default=True).first()
            if found is not None:
                return found
        return import_branches(user, company_id).first()


def check_branch(user, company_id, branch_id=None):
    """The branch the whole file joins, or refused.

    No id means the default (``default_branch``). An id is re-read and
    re-checked whatever the form showed: a branch the actor may not import
    into is refused outright.
    """
    _membership, branch_ids = import_scope(user, company_id)
    with use_company(company_id):
        if not branch_id:
            branch = default_branch(user, company_id)
        else:
            branch = Branch.objects.filter(pk=branch_id, status=ActiveStatus.ACTIVE).first()
    if branch is None:
        raise ValidationError({"branch": "Choose a branch."})
    if branch_ids is not ALL_BRANCHES and branch.pk not in branch_ids:
        raise PermissionDenied("You can only import people into your own branch.")
    return branch


# --- checking the rows -----------------------------------------------------


def check(user, company_id, rows):
    """Fill in each row's ``first_name`` / ``last_name`` and ``errors``. Writes nothing."""
    import_scope(user, company_id)
    with use_company(company_id):
        taken = set(
            EmployeeAssignment.objects.filter(
                status=EmployeeAssignment.Status.ACTIVE, effective_to__isnull=True,
            ).values_list("employee_code", flat=True)
        )

    seen = {}
    for row in rows:
        errors = []
        row["errors"] = errors

        code = str(row.get("employee_id", "")).strip()
        if not code:
            errors.append("Employee ID is missing.")
        elif not code.isdigit():
            errors.append(
                f"Employee ID “{code}” is not a number. The attendance terminals only "
                "accept digits.")
        elif len(code) > MAX_ID_LENGTH:
            errors.append(f"Employee ID “{code}” is too long.")
        elif code in taken:
            errors.append(f"Employee ID {code} already belongs to an employee in this company.")
        elif code in seen:
            errors.append(f"Employee ID {code} is also on row {seen[code]} of this file.")
        else:
            seen[code] = row["line"]
        row["employee_id"] = code

        name = " ".join(str(row.get("name", "")).split())
        if not name:
            errors.append("Name is missing.")
        elif len(name) > 300:
            errors.append("Name is too long.")
        # The whole name is kept exactly as written: the part before the last
        # space is the first name and the last word the last name, so the
        # full name reads the same everywhere. A one-word name stays whole.
        first, _sep, last = name.rpartition(" ")
        row["first_name"], row["last_name"] = (first, last) if first else (name, "")
        row["name"] = name
    return rows


def summarise(rows):
    bad = sum(1 for row in rows if row["errors"])
    return {"total": len(rows), "bad": bad, "good": len(rows) - bad}


# --- writing ---------------------------------------------------------------


@transaction.atomic
def commit(*, actor, company_id, rows, branch_id):
    """Create every row's employee in ``branch_id``, or none of them.

    The branch and the rows are re-checked here: they come back through the
    reader's session, and a branch they may no longer import into (or an
    Employee ID taken in between) must be caught now, not written.
    """
    from devices.services.mapping import unassigned_placement

    membership, _branch_ids = import_scope(actor, company_id)
    branch = check_branch(actor, company_id, branch_id)
    rows = check(actor, company_id, rows)
    bad = [row for row in rows if row["errors"]]
    if bad:
        raise ValidationError(
            f"{len(bad)} row{'s' if len(bad) != 1 else ''} can no longer be "
            "imported. Upload the file again to see why."
        )

    company = membership.company
    zone = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    today = datetime.datetime.now(zone).date()
    starts = datetime.datetime.combine(today, datetime.time.min, tzinfo=zone)
    created = []
    with use_company(company_id):
        department, designation = unassigned_placement(branch, actor)
        for row in rows:
            try:
                employee = Employee(
                    first_name=row["first_name"], last_name=row["last_name"],
                    joining_date=today,
                    employment_status=Employee.EmploymentStatus.ACTIVE,
                    metadata={"imported_from_file": True, "needs_hr_review": True},
                    created_by=actor, updated_by=actor,
                )
                employee.company = company
                employee.full_clean()
                employee.save()
                assignment = EmployeeAssignment(
                    employee=employee, employee_code=row["employee_id"], branch=branch,
                    department=department, designation=designation,
                    effective_from=starts,
                    change_reason="Imported from a file; department, designation "
                                  "and salary to be set on Edit employee.",
                    created_by=actor, updated_by=actor,
                )
                assignment.company = company
                assignment.full_clean()
                assignment.save()
            except (ValidationError, IntegrityError) as exc:
                # One row failing rolls the whole import back - a half-imported
                # file is worse than none.
                reason = " ".join(getattr(exc, "messages", [str(exc)]))
                raise ValidationError(
                    f"Row {row['line']} ({row['name']}) could not be created, so "
                    f"nothing was imported: {reason}"
                ) from exc
            created.append(employee)

        record_company_event(
            actor=actor, membership=membership, company=company,
            action="employees.imported", obj=branch,
            after={
                "count": len(created),
                "branch_id": branch.pk,
                "employee_codes": [row["employee_id"] for row in rows],
                "employee_ids": [employee.pk for employee in created],
            },
        )
    return created


# --- the demo file ---------------------------------------------------------


def demo_csv():
    """The demo file: the two headings and a few made-up people."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    for row in DEMO_ROWS:
        writer.writerow(row)
    # utf-8-sig so Excel opens Bangla and other non-English names correctly.
    return buffer.getvalue().encode("utf-8-sig")


def demo_xlsx():
    """The demo file as an Excel workbook: same headings, same people.

    Employee IDs are written as text, so Excel shows 445961 rather than turning
    a long one into 4.46E+05 - and the parser reads text or numbers alike.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = "Employees"
    sheet.append(HEADINGS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for code, name in DEMO_ROWS:
        sheet.append([code, name])
    for row in sheet.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "@"
    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = 32
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
