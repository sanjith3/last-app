from django.apps import AppConfig


class TurfsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'turfs'

    def ready(self):
        """Connect cache-invalidation signals when the app is loaded."""
        import turfs.signals  # noqa: F401
