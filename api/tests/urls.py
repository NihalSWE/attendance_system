"""The real API routes plus a few test-only endpoints that exercise the core
(errors, strict input, Idempotency-Key, rate limits). Used with
override_settings(ROOT_URLCONF="api.tests.urls")."""

from django.core.exceptions import ValidationError
from django.urls import include, path
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.core.errors import ApiError
from api.core.permissions import HasScope, Public
from api.core.serializers import StrictSerializer
from api.core.views import ApiView

COUNTER = {"made": 0}


class NameSerializer(StrictSerializer):
    name = serializers.CharField(max_length=20, help_text="A name.")


class EchoView(ApiView):
    permission_classes = [Public]
    throttle_scope = "write"

    def post(self, request):
        data = NameSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(data.validated_data, status=201)


class ServiceRefusesView(ApiView):
    permission_classes = [Public]
    throttle_scope = "write"

    def post(self, request):
        raise ValidationError({"email": ["Another company already uses this email."]})


class ConflictView(ApiView):
    permission_classes = [Public]
    throttle_scope = "write"

    def post(self, request):
        raise ApiError("conflict", "Salary up to 31 Aug 2026 is already finalised.")


class BrokenView(ApiView):
    permission_classes = [Public]
    throttle_scope = "read"

    def get(self, request):
        raise RuntimeError("secret internal detail")


class DeniedView(ApiView):
    throttle_scope = "read"        # no permission_classes: the default denies

    def get(self, request):
        return Response({"never": True})


class MakeView(ApiView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "write"

    def post(self, request):
        COUNTER["made"] += 1
        return Response({"made": COUNTER["made"]}, status=201)


class KeyProbeView(ApiView):
    """For the API-key tests: needs a scope and acts for a company."""

    permission_classes = [HasScope]
    required_scopes = ["employees:read"]
    company_required = True
    throttle_scope = "read"

    def get(self, request):
        return Response({"company_id": request.company_id, "user": request.user.email})

    def post(self, request):
        return Response({"got": request.data})


urlpatterns = [
    path("", include("config.urls")),
    path("api-test/echo", EchoView.as_view()),
    path("api-test/refuses", ServiceRefusesView.as_view()),
    path("api-test/conflict", ConflictView.as_view()),
    path("api-test/broken", BrokenView.as_view()),
    path("api-test/denied", DeniedView.as_view()),
    path("api-test/make", MakeView.as_view()),
    path("api-test/key-probe", KeyProbeView.as_view()),
]
