"""The upload form for the bulk employee import: the file and the branch.

The file carries Employee ID and Name; this form says which branch everyone in it
joins. The choices are narrowed for convenience - ``import_services.check_branch``
is what enforces them, because a crafted POST reaches it too.
"""

from django import forms

from common.forms import StyledFormMixin
from organization.models import Branch


class EmployeeImportForm(StyledFormMixin, forms.Form):
    # Not required: an empty branch means the default branch (the service
    # resolves it), so clearing the dropdown is never an error.
    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(), label="Branch", required=False)
    upload = forms.FileField(
        label="Employee file",
        help_text="A .csv file with the headings Employee ID and Name, like the demo file.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx,text/csv"}),
    )

    def __init__(self, *args, branches, default_branch, locked=False, **kwargs):
        """``branches``: where the actor may import. ``locked``: a branch
        manager with a single branch - the field shows it and cannot change."""
        super().__init__(*args, **kwargs)
        field = self.fields["branch"]
        field.queryset = branches
        field.empty_label = None          # always a branch; it starts on the default
        field.initial = default_branch.pk if default_branch else None
        self.locked = locked
        if locked:
            # A disabled field ignores whatever is posted and keeps its initial
            # value, so the branch cannot be swapped in the request.
            field.disabled = True
            field.help_text = "You add people to your own branch."
        elif default_branch is not None:
            field.help_text = (
                f"Everyone in the file joins this branch. Left empty, they join the "
                f"company's default branch, {default_branch.name}."
            )
