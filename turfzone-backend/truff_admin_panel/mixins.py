"""
Access control mixins for Truff-Admin views.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect


class TruffAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Require user to be authenticated AND be either:
    - is_staff=True, OR
    - member of 'Truff Admin' group
    """
    login_url = '/truff-admin/login/'
    raise_exception = False

    def test_func(self):
        user = self.request.user
        if not user.is_authenticated:
            return False
        return user.is_staff or user.groups.filter(name='Truff Admin').exists()

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect(self.login_url)
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden(
            '<h1>403 Forbidden</h1><p>You do not have permission to access Truff-Admin.</p>'
        )
