"""Base serializers.

``StrictSerializer`` refuses fields it does not know (``unknown_field``), so a
typo such as ``nmae`` is reported instead of silently ignored. Every field
must carry ``help_text`` - the documentation site shows it, and the
documentation test refuses a field without one.
"""

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                # The code travels with each message (DRF rebuilds the error
                # on the way out), so the handler answers unknown_field.
                raise serializers.ValidationError(
                    {name: ["This field is not accepted here."] for name in unknown},
                    code="unknown_field")
        return super().to_internal_value(data)
