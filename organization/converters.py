"""Employee page addresses carry the employee's random id (2026-10-18).

``/organization/employees/268/`` let anyone allowed in count the company's
people and try the next number. The address now holds ``Employee.public_id``
(a UUID); the views still receive the employee's own id as ``pk``, so none of
them changed. A plain number no longer matches: "not found".

Who may see the employee is still decided by the view's service (company,
branch, role), exactly as before; the random id only stops the guessing.
"""

import uuid

UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class EmployeeConverter:
    regex = UUID_PATTERN

    def to_python(self, value):
        from employees.models import Employee

        pk = Employee.all_objects.filter(public_id=value).values_list("pk", flat=True).first()
        if pk is None:
            raise ValueError("No such employee.")      # the address does not match: 404
        return pk

    def to_url(self, value):
        """An Employee, its public id, or its own id (looked up: links built
        before 2026-10-18 pass ``employee.pk``; busy lists pass the public id)."""
        from employees.models import Employee

        if isinstance(value, Employee):
            return str(value.public_id)
        if isinstance(value, uuid.UUID):
            return str(value)
        text = str(value)
        if not text.isdigit():
            return text
        public_id = Employee.all_objects.filter(pk=int(text)).values_list(
            "public_id", flat=True).first()
        if public_id is None:
            raise ValueError("No such employee.")
        return str(public_id)
