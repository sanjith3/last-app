"""
Management command: repair_turf_integrity

Fixes:
  1. Owner role mismatches (user owns turfs but role != turf_owner)
  2. Missing TurfOwner profiles
  3. Missing SlotMaster for approved turfs
  4. Stale total_turfs counts

Usage:
  python manage.py repair_turf_integrity          # apply fixes
  python manage.py repair_turf_integrity --dry-run # preview only
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from turfs.models import Turf, TurfStatus
from users.models import TurfOwner

User = get_user_model()


class Command(BaseCommand):
    help = 'Repair turf/owner integrity: roles, profiles, slots, stats'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview changes without applying them')

    def handle(self, *args, **options):
        dry = options['dry_run']
        tag = '[DRY-RUN] ' if dry else ''
        self.stdout.write(self.style.NOTICE(f'{tag}Starting turf integrity repair...\n'))

        stats = {
            'role_fixed': 0,
            'profile_created': 0,
            'slots_created': 0,
            'turfs_with_slots': 0,
            'total_turfs_recalc': 0,
        }

        # ── 1. Fix owner roles ──────────────────────────────────────────
        owners_with_turfs = User.objects.filter(turfs__isnull=False).distinct()
        for user in owners_with_turfs:
            if user.role != 'turf_owner' and user.role != 'admin':
                self.stdout.write(f'  {tag}FIX role: user {user.pk} ({user.username}) '
                                  f'{user.role} → turf_owner')
                if not dry:
                    user.role = 'turf_owner'
                    user.is_verified = True
                    user.save(update_fields=['role', 'is_verified'])
                stats['role_fixed'] += 1

        # ── 2. Create missing TurfOwner profiles ────────────────────────
        for user in owners_with_turfs:
            if not TurfOwner.objects.filter(user=user).exists():
                self.stdout.write(f'  {tag}CREATE TurfOwner for user {user.pk} ({user.username})')
                if not dry:
                    TurfOwner.objects.create(user=user)
                stats['profile_created'] += 1

        # ── 3. Auto-create slots for approved turfs with none ───────────
        approved = Turf.objects.filter(status=TurfStatus.APPROVED, is_active=True)
        for turf in approved:
            active_slots = turf.slot_masters.filter(is_active=True).count()
            if active_slots == 0:
                self.stdout.write(f'  {tag}CREATE 168 slots for turf {turf.pk} ({turf.name})')
                if not dry:
                    created = turf.auto_create_default_slots()
                    stats['slots_created'] += created
                else:
                    stats['slots_created'] += 168
                stats['turfs_with_slots'] += 1

        # ── 4. Recalculate total_turfs on all TurfOwner profiles ────────
        for profile in TurfOwner.objects.select_related('user').all():
            real_count = Turf.objects.filter(owner=profile.user).count()
            if profile.total_turfs != real_count:
                self.stdout.write(
                    f'  {tag}RECALC total_turfs for {profile.user.username}: '
                    f'{profile.total_turfs} → {real_count}')
                if not dry:
                    profile.total_turfs = real_count
                    profile.save(update_fields=['total_turfs'])
                stats['total_turfs_recalc'] += 1

        # ── Summary ────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{tag}Repair complete:'))
        self.stdout.write(f'  Roles fixed:          {stats["role_fixed"]}')
        self.stdout.write(f'  Profiles created:     {stats["profile_created"]}')
        self.stdout.write(f'  Turfs given slots:    {stats["turfs_with_slots"]}')
        self.stdout.write(f'  Slots created:        {stats["slots_created"]}')
        self.stdout.write(f'  total_turfs recalc:   {stats["total_turfs_recalc"]}')
