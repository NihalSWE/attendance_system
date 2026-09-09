"""Authentication models for the attendance platform.

The concrete authentication model is ``accounts.User`` (class name exactly
``User``). It is registered via ``AUTH_USER_MODEL = "accounts.User"`` in
config/settings.py before the first migration. Runtime code must resolve it
with ``django.contrib.auth.get_user_model()``; other models must reference it
through ``settings.AUTH_USER_MODEL``. Do not import
``django.contrib.auth.models.User`` anywhere in this project.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models

from common.models import ActorTracked, TenantOwned


class UserManager(DjangoUserManager):
    """User manager keyed on email instead of username.

    AbstractUser's default manager builds accounts from a ``username``. Because
    this project logs in by email (``USERNAME_FIELD = "email"``), the creation
    helpers below key on email so that ``createsuperuser`` and programmatic
    ``create_user`` / ``create_superuser`` calls work correctly.
    """

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required to create a user.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Platform login account.

    Inherits Django's authentication machinery (password, permission flags,
    ``groups``/``user_permissions``) from AbstractUser, but logs in by email.
    A person's employment identity lives in the employees app; a User may be
    linked to zero, one or many company memberships. Accounts are deactivated
    (``is_active=False``), never hard-deleted, so audit/actor history survives.
    """

    # Email is the login identifier (see MODEL_FIELD_DICTIONARY.md §1).
    email = models.EmailField("email address", unique=True)
    # Retained for AbstractUser compatibility; optional and internal. NULLs are
    # distinct in PostgreSQL, so many users may have no username.
    username = models.CharField(max_length=150, null=True, blank=True, unique=True)

    phone = models.CharField(max_length=32, blank=True)
    # Per-user presentation preferences; operational timestamps stay UTC.
    timezone = models.CharField(max_length=64, blank=True, default="UTC")
    language = models.CharField(max_length=16, blank=True, default="en-us")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    # email + password are always prompted; nothing else is required.
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.email


class CompanyMembership(TenantOwned, ActorTracked):
    """Links a User to a Company with a role. A user may belong to several
    companies; an employee may have no user. This is the model that resolves
    "which company is this user acting in" (bootstrapped via all_objects, since
    resolving it happens before a tenant context exists). See dictionary §2.

    Branch/department scopes narrow what this member may see. Empty means
    unrestricted at that level for an eligible role — not "no access".
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        COMPANY_ADMIN = "company_admin", "Company admin"
        HR = "hr", "HR"
        MANAGER = "manager", "Manager"
        PAYROLL_MANAGER = "payroll_manager", "Payroll manager"
        EMPLOYEE = "employee", "Employee"
        AUDITOR = "auditor", "Auditor"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ENDED = "ended", "Ended"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="company_memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.INVITED
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitations_sent",
    )
    # Empty = unrestricted at that level for an eligible role, NOT "no access".
    allowed_branches = models.ManyToManyField(
        "organization.Branch", blank=True, related_name="scoped_memberships"
    )
    allowed_departments = models.ManyToManyField(
        "organization.CompanyDepartment",
        blank=True,
        related_name="scoped_memberships",
    )
    last_access_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_companymembership"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "user"], name="uniq_company_user_membership"
            ),
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(role__in=["owner", "company_admin"]) & ~models.Q(status="ended"),
                name="uniq_current_company_administrator",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(role__in=["owner", "company_admin"]) & ~models.Q(status="ended"),
                name="uniq_current_administrator_company",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.company_id} ({self.role})"
