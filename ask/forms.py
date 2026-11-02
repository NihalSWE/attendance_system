"""Ask's settings form. Convenience only: ``ask.views.save_settings`` checks everything again."""

from django import forms

from ai_assistant.models import AiSettings
from common.forms import StyledFormMixin, date_widget


class AskSettingsForm(StyledFormMixin, forms.ModelForm):
    api_key = forms.CharField(label="Anthropic API key", required=False, widget=forms.PasswordInput,
                              help_text="Shared with the AI order assistant. Write-only: a saved key is never "
                                        "shown. Leave empty to keep it.")

    class Meta:
        model = AiSettings
        fields = ("ask_consent_received", "ask_consent_date", "ask_enabled", "ask_model", "daily_cost_cap")
        widgets = {"ask_consent_date": date_widget()}

    def _post_clean(self):
        pass
