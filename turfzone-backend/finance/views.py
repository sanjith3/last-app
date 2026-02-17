"""
Finance API views — admin-only dashboards and reports.

All endpoints require IsPlatformAdmin (role == 'admin').
No float() anywhere — all Decimal.
"""

import csv
from datetime import date
from decimal import Decimal

from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncDate, TruncMonth
from django.http import HttpResponse
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response

from core.permissions import IsPlatformAdmin
from bookings.models import Booking, BookingStatus
from finance.models import LedgerEntry, LedgerAccount, OwnerSettlement


class FinanceDashboardView(APIView):
    """
    GET /api/finance/dashboard/

    Returns platform-wide financial summary:
    - GMV (gross merchandise value)
    - Total revenue (slot revenue after discounts)
    - GST collected
    - Commission earned
    - Pending owner payouts
    - Booking counts
    """
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        confirmed = Booking.objects.filter(booking_status=BookingStatus.CONFIRMED)
        completed = Booking.objects.filter(booking_status=BookingStatus.COMPLETED)
        all_valid = confirmed | completed

        # Aggregate financials
        totals = all_valid.aggregate(
            gmv=Sum('total_price') or Decimal('0.00'),
            total_discount=Sum('discount') or Decimal('0.00'),
            total_revenue=Sum('final_price') or Decimal('0.00'),
            total_gst=Sum('gst_amount') or Decimal('0.00'),
            total_commission=Sum('commission') or Decimal('0.00'),
            total_platform_fee=Sum('platform_fee') or Decimal('0.00'),
            total_owner_payout=Sum('owner_payout') or Decimal('0.00'),
        )

        # Pending payouts
        pending_payouts = all_valid.filter(
            payout_status='pending'
        ).aggregate(
            amount=Sum('owner_payout') or Decimal('0.00'),
            count=Count('id'),
        )

        # Booking counts
        booking_counts = {
            'confirmed': confirmed.count(),
            'completed': completed.count(),
            'cancelled': Booking.objects.filter(
                booking_status=BookingStatus.CANCELLED
            ).count(),
            'pending': Booking.objects.filter(
                booking_status=BookingStatus.PENDING
            ).count(),
            'total': Booking.objects.count(),
        }

        return Response({
            'success': True,
            'financials': {
                'gmv': str(totals['gmv'] or Decimal('0.00')),
                'total_discount': str(totals['total_discount'] or Decimal('0.00')),
                'revenue': str(totals['total_revenue'] or Decimal('0.00')),
                'gst_collected': str(totals['total_gst'] or Decimal('0.00')),
                'commission_earned': str(totals['total_commission'] or Decimal('0.00')),
                'platform_fee': str(totals['total_platform_fee'] or Decimal('0.00')),
                'total_owner_payout': str(totals['total_owner_payout'] or Decimal('0.00')),
            },
            'pending_payouts': {
                'amount': str(pending_payouts['amount'] or Decimal('0.00')),
                'count': pending_payouts['count'],
            },
            'bookings': booking_counts,
        })


class GSTReportView(APIView):
    """
    GET /api/finance/gst-report/?start_date=2026-01-01&end_date=2026-02-28&format=csv

    Returns GST breakdown by date. Supports CSV export.
    """
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        start = request.query_params.get('start_date')
        end = request.query_params.get('end_date')
        export_csv = request.query_params.get('format') == 'csv'

        # Default: last 30 days
        if not end:
            end_date = date.today()
        else:
            end_date = date.fromisoformat(end)

        if not start:
            start_date = end_date.replace(day=1)  # First day of current month
        else:
            start_date = date.fromisoformat(start)

        # Query confirmed/completed bookings in date range
        bookings = Booking.objects.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
            booking_date__gte=start_date,
            booking_date__lte=end_date,
        ).order_by('booking_date')

        # Daily GST breakdown
        daily = bookings.annotate(
            day=TruncDate('booking_date')
        ).values('day').annotate(
            booking_count=Count('id'),
            subtotal=Sum('final_price'),
            gst=Sum('gst_amount'),
            gst_on_platform_fee=Sum('gst_on_platform_fee'),
        ).order_by('day')

        rows = []
        total_gst = Decimal('0.00')
        total_gst_pf = Decimal('0.00')
        for entry in daily:
            gst_val = entry['gst'] or Decimal('0.00')
            gst_pf_val = entry['gst_on_platform_fee'] or Decimal('0.00')
            total_gst += gst_val
            total_gst_pf += gst_pf_val
            rows.append({
                'date': str(entry['day']),
                'bookings': entry['booking_count'],
                'subtotal': str(entry['subtotal'] or Decimal('0.00')),
                'gst_on_slots': str(gst_val),
                'gst_on_platform_fee': str(gst_pf_val),
                'total_gst': str(gst_val + gst_pf_val),
            })

        if export_csv:
            return self._export_csv(rows, start_date, end_date)

        return Response({
            'success': True,
            'period': {'start': str(start_date), 'end': str(end_date)},
            'daily': rows,
            'totals': {
                'gst_on_slots': str(total_gst),
                'gst_on_platform_fee': str(total_gst_pf),
                'total_gst_liability': str(total_gst + total_gst_pf),
            },
        })

    def _export_csv(self, rows, start_date, end_date):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="gst_report_{start_date}_{end_date}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(['Date', 'Bookings', 'Subtotal', 'GST on Slots', 'GST on Platform Fee', 'Total GST'])
        for row in rows:
            writer.writerow([
                row['date'], row['bookings'], row['subtotal'],
                row['gst_on_slots'], row['gst_on_platform_fee'], row['total_gst'],
            ])
        return response


class CommissionReportView(APIView):
    """
    GET /api/finance/commission-report/?start_date=2026-01-01&end_date=2026-02-28&format=csv

    Returns per-owner commission breakdown. Supports CSV export.
    """
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        start = request.query_params.get('start_date')
        end = request.query_params.get('end_date')
        export_csv = request.query_params.get('format') == 'csv'

        if not end:
            end_date = date.today()
        else:
            end_date = date.fromisoformat(end)

        if not start:
            start_date = end_date.replace(day=1)
        else:
            start_date = date.fromisoformat(start)

        # Per-owner aggregation
        bookings = Booking.objects.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
            booking_date__gte=start_date,
            booking_date__lte=end_date,
        )

        per_owner = bookings.values(
            'turf__owner__id',
            'turf__owner__username',
            'turf__owner__email',
        ).annotate(
            booking_count=Count('id'),
            gross_revenue=Sum('total_price'),
            total_discount=Sum('discount'),
            net_revenue=Sum('final_price'),
            commission=Sum('commission'),
            gst=Sum('gst_amount'),
            owner_payout=Sum('owner_payout'),
        ).order_by('-net_revenue')

        rows = []
        for entry in per_owner:
            rows.append({
                'owner_id': entry['turf__owner__id'],
                'owner': entry['turf__owner__username'] or entry['turf__owner__email'],
                'bookings': entry['booking_count'],
                'gross_revenue': str(entry['gross_revenue'] or Decimal('0.00')),
                'discount': str(entry['total_discount'] or Decimal('0.00')),
                'net_revenue': str(entry['net_revenue'] or Decimal('0.00')),
                'commission': str(entry['commission'] or Decimal('0.00')),
                'owner_payout': str(entry['owner_payout'] or Decimal('0.00')),
            })

        totals = bookings.aggregate(
            total_commission=Sum('commission') or Decimal('0.00'),
            total_payout=Sum('owner_payout') or Decimal('0.00'),
        )

        if export_csv:
            return self._export_csv(rows, start_date, end_date)

        return Response({
            'success': True,
            'period': {'start': str(start_date), 'end': str(end_date)},
            'owners': rows,
            'totals': {
                'total_commission': str(totals['total_commission'] or Decimal('0.00')),
                'total_owner_payout': str(totals['total_payout'] or Decimal('0.00')),
            },
        })

    def _export_csv(self, rows, start_date, end_date):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="commission_report_{start_date}_{end_date}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            'Owner ID', 'Owner', 'Bookings',
            'Gross Revenue', 'Discount', 'Net Revenue',
            'Commission', 'Owner Payout',
        ])
        for row in rows:
            writer.writerow([
                row['owner_id'], row['owner'], row['bookings'],
                row['gross_revenue'], row['discount'], row['net_revenue'],
                row['commission'], row['owner_payout'],
            ])
        return response
