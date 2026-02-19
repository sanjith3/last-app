"""
Backfill turf coordinates from existing google_maps_share_link values.
Run: python manage.py backfill_turf_coordinates [--dry-run]
"""

import time
from django.core.management.base import BaseCommand
from turfs.models import Turf
from core.utils import extract_coordinates_from_google_maps_share_link


class Command(BaseCommand):
    help = 'Resolve missing lat/lon for turfs that have a google_maps_share_link.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be updated without saving.',
        )
        parser.add_argument(
            '--delay', type=float, default=1.0,
            help='Seconds between resolution attempts (rate limit). Default: 1.0',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        delay = options['delay']

        turfs = Turf.objects.filter(
            google_maps_share_link__isnull=False,
        ).exclude(google_maps_share_link='')

        # Only backfill turfs with missing coords
        turfs = [t for t in turfs if not t.latitude or not t.longitude]

        self.stdout.write(f'Found {len(turfs)} turfs needing coordinate backfill.')

        success = 0
        failed = 0

        for turf in turfs:
            self.stdout.write(f'\n[{turf.id}] {turf.name}: {turf.google_maps_share_link}')

            result = extract_coordinates_from_google_maps_share_link(
                turf.google_maps_share_link
            )

            if result['success']:
                self.stdout.write(self.style.SUCCESS(
                    f'  → lat={result["latitude"]}, lon={result["longitude"]} '
                    f'(debug_id={result["debug_id"]})'
                ))
                if not dry_run:
                    turf.latitude = result['latitude']
                    turf.longitude = result['longitude']
                    turf.save(update_fields=['latitude', 'longitude'])
                success += 1
            else:
                self.stdout.write(self.style.ERROR(
                    f'  ✗ {result["message"]} (debug_id={result["debug_id"]})'
                ))
                failed += 1

            time.sleep(delay)

        self.stdout.write(f'\n{"DRY RUN — " if dry_run else ""}Done: {success} resolved, {failed} failed.')
