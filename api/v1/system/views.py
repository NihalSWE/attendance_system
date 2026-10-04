from django.utils import timezone
from rest_framework.response import Response

from api.core.docs import endpoint
from api.core.permissions import Public
from api.core.views import ApiView
from api.v1.system.serializers import PingSerializer


class PingView(ApiView):
    permission_classes = [Public]
    throttle_scope = "public"

    @endpoint(
        id="ping", area="start", title="Ping",
        summary="Is the API up, and what time is it on the server.",
        what_it_does=[
            "Answers at once, without a login.",
            "Gives the server's time, so an app can correct its own clock before "
            "signing requests.",
        ],
        description=(
            "Use it to check that the address is right and the API is running, and to "
            "read the server's clock. Signed requests (phase 1) carry a timestamp that "
            "must be within 5 minutes of the server's time; a phone or PC whose clock "
            "is wrong can compare with `server_time` and adjust. It reads nothing and "
            "changes nothing."
        ),
        roles=["Anyone - no login needed"],
        auth="public",
        response=PingSerializer,
        response_example={"status": "ok", "version": "v1",
                          "server_time": "2026-10-04T04:15:00Z"},
        errors=["rate_limited", "server_error"],
    )
    def get(self, request):
        data = {"status": "ok", "version": "v1", "server_time": timezone.now()}
        return Response(PingSerializer(data).data)
