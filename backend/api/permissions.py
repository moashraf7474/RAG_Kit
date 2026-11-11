from rest_framework.permissions import BasePermission


class IsOrgAdmin(BasePermission):
    """Allow organization admins and Django superusers only."""

    message = "Organization administrator access is required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or getattr(user, "is_org_admin", False))
        )
