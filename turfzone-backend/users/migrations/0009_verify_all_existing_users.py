"""
Data migration: mark all existing users as is_verified=True.
This is a one-time migration for booking app — no manual admin approval needed.
"""
from django.db import migrations


def set_all_users_verified(apps, schema_editor):
    """Mark every existing CustomUser as verified."""
    CustomUser = apps.get_model('users', 'CustomUser')
    updated = CustomUser.objects.filter(is_verified=False).update(is_verified=True)
    print(f'  Auto-verified {updated} existing users.')


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_auto_verify_users_default'),
    ]

    operations = [
        migrations.RunPython(
            set_all_users_verified,
            reverse_code=migrations.RunPython.noop,  # irreversible data change
        ),
    ]
