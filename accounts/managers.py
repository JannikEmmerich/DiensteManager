from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    """User manager that uses the email address as the login name."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        return (email or "").strip().lower()

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        from allauth.account.models import EmailAddress

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "dispatcher")
        if extra_fields["is_staff"] is not True or extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser needs is_staff=True and is_superuser=True.")
        user = self._create_user(email, password, **extra_fields)
        # `createsuperuser` has no email confirmation. Without a verified address,
        # allauth would block the first sign-in and ask for a confirmation first.
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
        return user
