from rest_framework import serializers


class PingSerializer(serializers.Serializer):
    status = serializers.CharField(
        help_text='Always "ok" when the API answers.')
    version = serializers.CharField(
        help_text='The API version this address serves, e.g. "v1".')
    server_time = serializers.DateTimeField(
        help_text="The server's clock, in UTC (ISO 8601). Compare it with your "
                  "device's clock: a signed request more than 5 minutes off is "
                  "refused.")
