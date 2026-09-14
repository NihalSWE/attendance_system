from django.contrib.auth.decorators import login_required
from django.urls import path

from base_template import me_views

app_name = "me"

# The only pages an Employee or Branch manager login may open
# (common.middleware.SelfServiceGate lets this namespace through).
urlpatterns = [
    path("", me_views.my_account, name="home"),
    path("password/", login_required(me_views.MyPasswordView.as_view()), name="password"),
]
