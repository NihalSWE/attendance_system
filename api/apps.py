from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "API"

    def ready(self):
        from api.core import schema  # noqa: F401 - registers the Swagger auth description
