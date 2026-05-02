"""
Backfill financial fields for old bookings that have platform_fee=0.
Run: python backfill_financials.py
"""
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'turfspotx.settings'
django.setup()

from bookings.models import Booking
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Sum

Q = Decimal('0.01')
PF = Decimal('10.00')
GST = Decimal('0.18')
CR = Decimal('0.05')

old = Booking.objects.filter(platform_fee=Decimal('0.00'))
print("Bookings to backfill:", old.count())

updated = 0
for b in old:
    tp = b.total_price or Decimal('0')
    disc = b.discount or Decimal('0')
    sub = tp - disc  # subtotal after discount
    ga = b.gst_amount or Decimal('0')

    b.platform_fee = PF
    b.gst_on_platform_fee = (PF * GST).quantize(Q, ROUND_HALF_UP)

    comm = (sub * CR).quantize(Q, ROUND_HALF_UP)
    b.commission = comm
    b.commission_percent = Decimal('5.00')
    gst_c = (comm * GST).quantize(Q, ROUND_HALF_UP)
    b.gst_on_commission = gst_c

    b.owner_payout = (sub + ga - comm - gst_c).quantize(Q, ROUND_HALF_UP)
    b.platform_revenue = (PF + b.gst_on_platform_fee + comm + gst_c).quantize(Q, ROUND_HALF_UP)
    b.final_price = (sub + ga + PF + b.gst_on_platform_fee).quantize(Q, ROUND_HALF_UP)

    b.save()
    updated += 1
    print(
        "  id=%d sub=%s pf=%s gst_pf=%s comm=%s gst_c=%s pay=%s rev=%s final=%s"
        % (b.id, sub, b.platform_fee, b.gst_on_platform_fee,
           comm, gst_c, b.owner_payout, b.platform_revenue, b.final_price)
    )

print("Backfilled %d bookings" % updated)

# Verify totals
t = Booking.objects.aggregate(
    pf=Sum('platform_fee'),
    gst_pf=Sum('gst_on_platform_fee'),
    comm=Sum('commission'),
    gst_c=Sum('gst_on_commission'),
    rev=Sum('platform_revenue'),
    pay=Sum('owner_payout'),
)
print("--- TOTALS ---")
for k, v in t.items():
    print("  %s = %s" % (k, v))
