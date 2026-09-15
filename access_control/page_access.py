"""Which company pages a branch manager or a person given access may open (A12 part 3).

Owner, company admin and HR use the company pages as before; nothing here
changes them. An Employee or Branch-manager login is kept on its own pages by
``common.middleware.SelfServiceGate`` — except the pages listed here, which it
opens to anyone holding the listed permission in at least one branch.

**A page is listed only once it limits what it shows to the viewer's
branches.** Opening a page that still shows the whole company would hand a
branch manager every other branch's people, leave or salary. Parts 4–6 of A12
add each area here as it becomes branch-scoped; the sidebar and the My account
page follow this list, so a page appears the moment it is added.
"""

from access_control.branch_access import CODES, can

# view name -> the branch permission it needs.
BRANCH_PAGES = {
    # A12 part 2: who has which access (itself limited to the viewer's branches).
    "organization:access": "access.grant",
    "organization:access_person": "access.grant",
    # A12 part 4: the Employees area. The list shows only your branches' people
    # (pay only where you may see salaries); Create employee offers only your
    # branches; Edit employee opens details, placement and logins for someone
    # in your branch, and pay where you may prepare salary — own shifts and
    # making a branch manager stay with the company. The employee page and End
    # employment (Nihal's N6) are not listed: part 7's note covers them.
    "employee_list": "employees.view",
    "organization:employee_create": "employees.edit",
    "organization:employee_edit": "employees.edit",
    "organization:employee_branch_departments": "employees.edit",
    "organization:employee_department_designations": "employees.edit",
}


def may_open(user, company_id, view_name):
    """True when ``view_name`` is a branch page and ``user`` holds its permission somewhere."""
    code = BRANCH_PAGES.get(view_name)
    return bool(code) and can(user, company_id, code)


def held_codes(user, company_id):
    """The branch permissions ``user`` holds in at least one branch."""
    return {code for code in CODES if can(user, company_id, code)}


def opener(user, company_id):
    """A cached ``may_open`` for one request (the sidebar asks per menu entry)."""
    held = held_codes(user, company_id)

    def allowed(view_name):
        code = BRANCH_PAGES.get(view_name)
        return bool(code) and code in held

    return allowed
