"""
Management command: seed sample promo codes for testing.

Usage: python manage.py seed_coupons
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from users.models import PromoCode


class Command(BaseCommand):
    help = 'Create sample promo codes for testing the coupon system'

    def handle(self, *args, **options):
        now = timezone.now()
        year_end = now + timedelta(days=365)

        coupons = [
            {
                'code': 'WELCOME50',
                'discount_type': 'fixed',
                'discount_value': Decimal('50.00'),
                'min_order_value': Decimal('0.00'),
                'valid_from': now,
                'valid_until': year_end,
                'max_uses': 10000,
                'max_discount': None,
            },
            {
                'code': 'FLAT100',
                'discount_type': 'fixed',
                'discount_value': Decimal('100.00'),
                'min_order_value': Decimal('1000.00'),
                'valid_from': now,
                'valid_until': year_end,
                'max_uses': 5000,
                'max_discount': None,
            },
            {
                'code': 'WEEKEND20',
                'discount_type': 'percentage',
                'discount_value': Decimal('20.00'),
                'min_order_value': Decimal('0.00'),
                'valid_from': now,
                'valid_until': year_end,
                'max_uses': 99999,
                'max_discount': Decimal('200.00'),
            },
            {
                'code': 'TURFSAVE',
                'discount_type': 'fixed',
                'discount_value': Decimal('75.00'),
                'min_order_value': Decimal('500.00'),
                'valid_from': now,
                'valid_until': year_end,
                'max_uses': 2000,
                'max_discount': None,
            },
        ]

        for c in coupons:
            obj, created = PromoCode.objects.update_or_create(
                code=c['code'],
                defaults={
                    'discount_type': c['discount_type'],
                    'discount_value': c['discount_value'],
                    'min_order_value': c['min_order_value'],
                    'max_discount': c['max_discount'],
                    'valid_from': c['valid_from'],
                    'valid_until': c['valid_until'],
                    'max_uses': c['max_uses'],
                    'is_active': True,
                },
            )
            status = 'Created' if created else 'Updated'
            self.stdout.write(
                self.style.SUCCESS(f'  {status}: {obj.code} ({obj.discount_type}: {obj.discount_value})')
            )

        self.stdout.write(self.style.SUCCESS('\nSample coupons ready!'))
        self.stdout.write('  WELCOME50  — ₹50 off (no minimum)')
        self.stdout.write('  FLAT100    — ₹100 off on ₹1000+')
        self.stdout.write('  WEEKEND20  — 20% off, max ₹200')
        self.stdout.write('  TURFSAVE   — ₹75 off on ₹500+')
