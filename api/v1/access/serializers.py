from rest_framework import serializers

from access_control.branch_access import CODES
from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer


class PermissionSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="The permission, e.g. employees.view.")
    label = serializers.CharField(help_text="What it allows, in the panel's words.")


class AccessLoginSerializer(serializers.Serializer):
    role = serializers.CharField(help_text="employee, manager, hr, company_admin, owner, …")
    label = serializers.CharField(help_text="The role in the panel's words.")
    disabled = serializers.BooleanField(help_text="The login is switched off.")


class HeldSerializer(serializers.Serializer):
    permission = serializers.CharField(help_text="The permission's code.")
    label = serializers.CharField(help_text="What it allows.")
    branches = RefSerializer(many=True, help_text="The branches where it was given.")


class PersonRowSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="The person.")
    employee_code = serializers.CharField(allow_null=True, help_text="Their employee ID now.")
    branch = RefSerializer(help_text="The branch they are placed in now.")
    login = AccessLoginSerializer(allow_null=True, help_text="Their login, or null when they have none.")
    role_note = serializers.CharField(
        help_text="What their role gives by itself (it cannot be removed here); may be empty.")
    access = HeldSerializer(many=True, help_text="The access given to them by hand.")


class CellSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField(help_text="The branch.")
    granted = serializers.BooleanField(help_text="They have this permission in this branch.")
    editable = serializers.BooleanField(
        help_text="You may change it (you hold the permission there and may give access).")


class GridRowSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="The permission, e.g. employees.view.")
    label = serializers.CharField(help_text="What it allows.")
    branches = CellSerializer(many=True, help_text="One cell per branch, as on the panel.")


class PersonAccessSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="The person.")
    login_label = serializers.CharField(help_text="Their login's role; empty without a login.")
    role_note = serializers.CharField(help_text="What their role gives by itself; may be empty.")
    is_self = serializers.BooleanField(help_text="It is you - you cannot change your own access.")
    everything = serializers.BooleanField(
        help_text="An owner or company administrator: they have every permission already.")
    branches = RefSerializer(many=True, help_text="The branches (the grid's columns).")
    permissions = GridRowSerializer(many=True, help_text="The grid: one row per permission.")


class GrantSerializer(StrictSerializer):
    permission = serializers.ChoiceField(choices=[(c, c) for c in CODES],
                                         help_text="The permission's code.")
    branch_id = serializers.IntegerField(help_text="The branch.")


class AccessChangeSerializer(StrictSerializer):
    grants = GrantSerializer(
        many=True, allow_empty=True,
        help_text="Every permission the person should have, by branch - the whole grid you "
                  "may change. A cell you may change that is missing here is removed.")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                   help_text="Why - kept in the audit log.")


class ChangeSerializer(serializers.Serializer):
    permission = serializers.CharField(help_text="The permission's code.")
    branch_id = serializers.IntegerField(help_text="The branch.")


class AccessChangedSerializer(PersonAccessSerializer):
    added = ChangeSerializer(many=True, help_text="What was given now.")
    removed = ChangeSerializer(many=True, help_text="What was taken away now.")
