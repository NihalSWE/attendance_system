from django.urls import path

from api.v1.access import views

urlpatterns = [
    path("access/permissions", views.PermissionListView.as_view(), name="access_permissions"),
    path("access/people", views.PeopleView.as_view(), name="access_people"),
    path("access/people/<int:employee_id>", views.PersonAccessView.as_view(),
         name="access_person"),
]
