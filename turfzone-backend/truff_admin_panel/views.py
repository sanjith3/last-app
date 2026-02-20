"""
Truff-Admin views — all protected by TruffAdminRequiredMixin.
Zero impact on public API endpoints.
"""

from datetime import timedelta, date
from decimal import Decimal

from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncMonth, TruncDate
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator

from bookings.models import Booking, BookingStatus, Payment, CallRecord, CallStatus
from finance.models import LedgerEntry, LedgerAccount, EntryType, OwnerSettlement
from turfs.models import Turf, TurfStatus
from users.models import TurfOwner

from .mixins import TruffAdminRequiredMixin
from .models import AdminAuditLog, PinChangeRequest, PinChangeRequestStatus, AdminConfig
from .utils import log_admin_action, get_client_ip
from .notifications import send_turf_status_email
from .exports import streaming_csv_response

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

class AdminLoginView(View):
    """Login page for Truff-Admin."""

    def get(self, request):
        if request.user.is_authenticated and (
            request.user.is_staff or request.user.groups.filter(name='Truff Admin').exists()
        ):
            return redirect('/truff-admin/')
        return render(request, 'truff_admin/login.html')

    def post(self, request):
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user and (user.is_staff or user.groups.filter(name='Truff Admin').exists()):
            login(request, user)
            log_admin_action(request, 'admin_login', 'User', user.pk, {'username': username})
            return redirect('/truff-admin/')

        return render(request, 'truff_admin/login.html', {
            'error': 'Invalid credentials or insufficient permissions.'
        })


class AdminLogoutView(View):
    def get(self, request):
        if request.user.is_authenticated:
            log_admin_action(request, 'admin_logout', 'User', request.user.pk)
        logout(request)
        return redirect('/truff-admin/login/')


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

class DashboardView(TruffAdminRequiredMixin, View):
    """Main dashboard with KPI cards and charts."""

    def get(self, request):
        ctx = None
        try:
            ctx = cache.get('truff_dashboard_kpis')
        except Exception:
            pass  # Redis unavailable — skip cache

        if not ctx:
            ctx = self._build_kpis()
            try:
                cache.set('truff_dashboard_kpis', ctx, 60)
            except Exception:
                pass

        # Chart data
        ctx['monthly_revenue'] = self._monthly_revenue()
        ctx['daily_revenue'] = self._daily_revenue()
        ctx['top_turfs'] = self._top_turfs()

        return render(request, 'truff_admin/dashboard.html', ctx)

    def _build_kpis(self):
        today = timezone.localdate()
        try:
            confirmed_bookings = Booking.objects.filter(
                booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED]
            )
            total_rev = confirmed_bookings.aggregate(s=Sum('platform_fee'))['s'] or Decimal('0')
            today_rev = confirmed_bookings.filter(
                booking_date=today
            ).aggregate(s=Sum('platform_fee'))['s'] or Decimal('0')
        except Exception:
            total_rev = Decimal('0')
            today_rev = Decimal('0')
            confirmed_bookings = Booking.objects.none()

        return {
            'total_revenue': total_rev,
            'today_revenue': today_rev,
            'total_transactions': confirmed_bookings.count(),
            'active_turfs': Turf.objects.filter(status=TurfStatus.APPROVED, is_active=True).count(),
            'pending_turfs': Turf.objects.filter(status=TurfStatus.PENDING).count(),
            'suspended_turfs': Turf.objects.filter(status=TurfStatus.SUSPENDED).count(),
            'total_owners': User.objects.filter(role='turf_owner').count(),
            'total_users': User.objects.filter(role='user').count(),
        }

    def _monthly_revenue(self):
        """Group by month — SQLite-safe (no TruncMonth)."""
        try:
            twelve_months_ago = timezone.localdate() - timedelta(days=365)
            from django.db import connection
            is_sqlite = 'sqlite' in connection.vendor

            if is_sqlite:
                qs = (
                    Booking.objects
                    .filter(
                        booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
                        booking_date__gte=twelve_months_ago,
                    )
                    .extra(select={'month_str': "strftime('%%Y-%%m', booking_date)"})
                    .values('month_str')
                    .annotate(total=Sum('platform_fee'))
                    .order_by('month_str')
                )
                return [
                    {'month': r['month_str'], 'total': float(r['total'] or 0)}
                    for r in qs
                ]
            else:
                qs = (
                    Booking.objects
                    .filter(
                        booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
                        booking_date__gte=twelve_months_ago,
                    )
                    .annotate(month=TruncMonth('booking_date'))
                    .values('month')
                    .annotate(total=Sum('platform_fee'))
                    .order_by('month')
                )
                return [
                    {'month': r['month'].strftime('%b %Y'), 'total': float(r['total'] or 0)}
                    for r in qs
                ]
        except Exception:
            return []

    def _daily_revenue(self):
        """Group by day — SQLite-safe (no TruncDate)."""
        try:
            thirty_days_ago = timezone.localdate() - timedelta(days=30)
            from django.db import connection
            is_sqlite = 'sqlite' in connection.vendor

            if is_sqlite:
                qs = (
                    Booking.objects
                    .filter(
                        booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
                        booking_date__gte=thirty_days_ago,
                    )
                    .extra(select={'day_str': "strftime('%%Y-%%m-%%d', booking_date)"})
                    .values('day_str')
                    .annotate(total=Sum('platform_fee'))
                    .order_by('day_str')
                )
                return [
                    {'day': r['day_str'][5:], 'total': float(r['total'] or 0)}
                    for r in qs
                ]
            else:
                qs = (
                    Booking.objects
                    .filter(
                        booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
                        booking_date__gte=thirty_days_ago,
                    )
                    .annotate(day=TruncDate('booking_date'))
                    .values('day')
                    .annotate(total=Sum('platform_fee'))
                    .order_by('day')
                )
                return [
                    {'day': r['day'].strftime('%d %b'), 'total': float(r['total'] or 0)}
                    for r in qs
                ]
        except Exception:
            return []

    def _top_turfs(self):
        try:
            qs = (
                Booking.objects
                .filter(booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED])
                .values('turf__id', 'turf__name')
                .annotate(revenue=Sum('final_price'), bookings=Count('id'))
                .order_by('-revenue')[:10]
            )
            return list(qs)
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════════════
# TURF MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TurfListView(TruffAdminRequiredMixin, View):
    """Turf listing with filters, search, pagination."""

    def get(self, request):
        qs = Turf.objects.select_related('owner').all()

        # Filters
        status = request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        city = request.GET.get('city')
        if city:
            qs = qs.filter(city__icontains=city)
        search = request.GET.get('q')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(owner__username__icontains=search) |
                Q(city__icontains=search)
            )

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        # Distinct cities for filter dropdown
        cities = Turf.objects.values_list('city', flat=True).distinct().order_by('city')

        return render(request, 'truff_admin/turfs_list.html', {
            'page': page,
            'status_choices': TurfStatus.choices,
            'cities': cities,
            'current_status': status or '',
            'current_city': city or '',
            'search_query': search or '',
        })


class TurfDetailView(TruffAdminRequiredMixin, View):
    """View turf details + audit trail."""

    def get(self, request, turf_id):
        turf = get_object_or_404(Turf.objects.select_related('owner', 'approved_by'), pk=turf_id)
        images = turf.images.all()
        audit = AdminAuditLog.objects.filter(
            target_model='Turf', target_id=turf_id
        ).order_by('-created_at')[:20]
        bookings = Booking.objects.filter(turf=turf).select_related('user').order_by('-created_at')[:10]

        # Count future active bookings (for suspend UI)
        future_bookings_count = Booking.objects.filter(
            turf=turf,
            booking_date__gte=timezone.localdate(),
            booking_status=BookingStatus.CONFIRMED,
        ).count()

        return render(request, 'truff_admin/turf_detail.html', {
            'turf': turf,
            'images': images,
            'audit_logs': audit,
            'recent_bookings': bookings,
            'future_bookings_count': future_bookings_count,
        })


class TurfActionView(TruffAdminRequiredMixin, View):
    """Handle turf approve/reject/suspend/reactivate with model methods + notifications."""

    def post(self, request, turf_id):
        turf = get_object_or_404(Turf, pk=turf_id)
        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        admin = request.user
        admin_name = admin.get_full_name() or admin.username

        if action == 'approve':
            # Idempotent: if already approved, just log access
            if turf.status == TurfStatus.APPROVED:
                return redirect(f'/truff-admin/turfs/{turf_id}/')
            turf.approve(admin=admin)
            log_admin_action(request, 'turf_approved', 'Turf', turf_id, {'turf_name': turf.name})
            send_turf_status_email(turf, 'approve', admin_name)

        elif action == 'reject':
            if not reason:
                return JsonResponse({'error': 'Reason is required for rejection'}, status=400)
            turf.reject(reason=reason)
            log_admin_action(request, 'turf_rejected', 'Turf', turf_id, {
                'turf_name': turf.name, 'reason': reason
            })
            send_turf_status_email(turf, 'reject', admin_name, reason=reason)

        elif action == 'suspend':
            if not reason:
                return JsonResponse({'error': 'Reason is required for suspension'}, status=400)
            turf.suspend(reason=reason)
            log_admin_action(request, 'turf_suspended', 'Turf', turf_id, {
                'turf_name': turf.name, 'reason': reason
            })
            send_turf_status_email(turf, 'suspend', admin_name, reason=reason)

            # Optionally cancel future bookings
            cancel_bookings = request.POST.get('cancel_future_bookings') == 'on'
            if cancel_bookings:
                cancelled = Booking.objects.filter(
                    turf=turf,
                    booking_date__gte=timezone.localdate(),
                    booking_status=BookingStatus.CONFIRMED,
                ).update(booking_status=BookingStatus.CANCELLED)
                if cancelled:
                    log_admin_action(request, 'future_bookings_cancelled', 'Turf', turf_id, {
                        'turf_name': turf.name, 'count': cancelled,
                    })

        elif action == 'reactivate':
            turf.reactivate(admin=admin)
            log_admin_action(request, 'turf_reactivated', 'Turf', turf_id, {'turf_name': turf.name})
            send_turf_status_email(turf, 'reactivate', admin_name)

        return redirect(f'/truff-admin/turfs/{turf_id}/')


class TurfPendingListView(TruffAdminRequiredMixin, View):
    """Dedicated pending turfs queue for quick review."""

    def get(self, request):
        qs = Turf.objects.filter(status=TurfStatus.PENDING).select_related('owner')

        # Filters
        city = request.GET.get('city')
        if city:
            qs = qs.filter(city__icontains=city)
        search = request.GET.get('q')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(owner__username__icontains=search) |
                Q(city__icontains=search)
            )

        qs = qs.order_by('created_at')  # oldest first
        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        cities = Turf.objects.filter(status=TurfStatus.PENDING).values_list(
            'city', flat=True
        ).distinct().order_by('city')

        return render(request, 'truff_admin/pending_turfs.html', {
            'page': page,
            'cities': cities,
            'current_city': city or '',
            'search_query': search or '',
            'pending_count': Turf.objects.filter(status=TurfStatus.PENDING).count(),
        })


class TurfBulkActionView(TruffAdminRequiredMixin, View):
    """Bulk approve/reject turfs."""

    def post(self, request):
        import json
        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        turf_ids_raw = request.POST.get('turf_ids', '[]')
        admin = request.user
        admin_name = admin.get_full_name() or admin.username

        try:
            turf_ids = json.loads(turf_ids_raw)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({'error': 'Invalid turf_ids'}, status=400)

        if not turf_ids:
            return JsonResponse({'error': 'No turfs selected'}, status=400)

        if action not in ('approve', 'reject'):
            return JsonResponse({'error': 'Invalid action'}, status=400)

        if action == 'reject' and not reason:
            return JsonResponse({'error': 'Reason required for bulk rejection'}, status=400)

        turfs = Turf.objects.filter(pk__in=turf_ids, status=TurfStatus.PENDING)
        processed = 0

        for turf in turfs:
            if action == 'approve':
                turf.approve(admin=admin)
                log_admin_action(request, 'turf_approved', 'Turf', turf.pk, {
                    'turf_name': turf.name, 'bulk': True,
                })
                send_turf_status_email(turf, 'approve', admin_name)
            elif action == 'reject':
                turf.reject(reason=reason)
                log_admin_action(request, 'turf_rejected', 'Turf', turf.pk, {
                    'turf_name': turf.name, 'reason': reason, 'bulk': True,
                })
                send_turf_status_email(turf, 'reject', admin_name, reason=reason)
            processed += 1

        return JsonResponse({
            'success': True,
            'processed': processed,
            'action': action,
        })


# ═══════════════════════════════════════════════════════════════════════════
# OWNER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class OwnerListView(TruffAdminRequiredMixin, View):
    """Owner listing with aggregated stats."""

    def get(self, request):
        search = request.GET.get('q', '')
        qs = User.objects.filter(role='turf_owner').prefetch_related('turfs')

        if search:
            qs = qs.filter(
                Q(username__icontains=search) |
                Q(first_name__icontains=search) |
                Q(phone_number__icontains=search)
            )

        # Annotate with stats
        qs = qs.annotate(
            turf_count=Count('turfs', distinct=True),
            booking_count=Count('turfs__bookings', distinct=True),
            total_rev=Sum('turfs__bookings__final_price'),
        )

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'truff_admin/owners_list.html', {
            'page': page,
            'search_query': search,
        })


class OwnerDetailView(TruffAdminRequiredMixin, View):
    """Owner drill-down: turfs, bookings, settlements, PIN requests."""

    def get(self, request, owner_id):
        owner = get_object_or_404(User, pk=owner_id, role='turf_owner')
        turfs = Turf.objects.filter(owner=owner)
        bookings = Booking.objects.filter(
            turf__owner=owner
        ).select_related('turf', 'user').order_by('-created_at')[:20]
        settlements = OwnerSettlement.objects.filter(owner=owner).order_by('-created_at')[:10]
        pin_requests = PinChangeRequest.objects.filter(owner=owner).order_by('-created_at')

        # Owner stats
        stats = Booking.objects.filter(
            turf__owner=owner,
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
        ).aggregate(
            total_revenue=Sum('final_price'),
            total_bookings=Count('id'),
            total_platform_fee=Sum('platform_fee'),
            total_owner_payout=Sum('owner_payout'),
        )

        return render(request, 'truff_admin/owner_detail.html', {
            'owner': owner,
            'turfs': turfs,
            'bookings': bookings,
            'settlements': settlements,
            'pin_requests': pin_requests,
            'stats': stats,
        })


# ═══════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT (customers)
# ═══════════════════════════════════════════════════════════════════════════

class UserListView(TruffAdminRequiredMixin, View):
    """List all registered customers with stats."""

    def get(self, request):
        qs = User.objects.filter(role='user')

        # Search
        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(
                Q(username__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone_number__icontains=search)
            )

        # Verified filter
        verified = request.GET.get('verified', '')
        if verified == 'yes':
            qs = qs.filter(is_verified=True)
        elif verified == 'no':
            qs = qs.filter(is_verified=False)

        # Date range
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # Annotate with booking stats
        qs = qs.annotate(
            booking_count=Count('bookings', distinct=True),
            total_spent=Sum(
                'bookings__final_price',
                filter=Q(
                    bookings__booking_status__in=[
                        BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
                    ]
                ),
            ),
        )

        # Sorting
        sort = request.GET.get('sort', '-created_at')
        allowed_sorts = {
            'bookings': 'booking_count', '-bookings': '-booking_count',
            'spent': 'total_spent', '-spent': '-total_spent',
            'joined': 'created_at', '-joined': '-created_at',
            'name': 'first_name', '-name': '-first_name',
        }
        order = allowed_sorts.get(sort, '-created_at')
        qs = qs.order_by(order)

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'truff_admin/users_list.html', {
            'page': page,
            'search_query': search,
            'verified_filter': verified,
            'date_from': date_from,
            'date_to': date_to,
            'current_sort': sort,
            'total_users': User.objects.filter(role='user').count(),
        })


class UserDetailView(TruffAdminRequiredMixin, View):
    """Single user detail with booking/call history."""

    def get(self, request, user_id):
        user_obj = get_object_or_404(User, pk=user_id)

        # Booking history
        bookings = Booking.objects.filter(
            user=user_obj
        ).select_related('turf').order_by('-created_at')[:25]

        # Call history (calls where they were the customer)
        call_records = CallRecord.objects.filter(
            booking__user=user_obj
        ).select_related(
            'booking', 'booking__turf', 'initiated_by'
        ).order_by('-started_at')[:20]

        # Stats
        stats = Booking.objects.filter(
            user=user_obj,
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
        ).aggregate(
            total_spent=Sum('final_price'),
            total_bookings=Count('id'),
        )

        return render(request, 'truff_admin/user_detail.html', {
            'user_obj': user_obj,
            'bookings': bookings,
            'call_records': call_records,
            'stats': stats,
        })


class ExportUsersView(TruffAdminRequiredMixin, View):
    """CSV export of all users."""

    def get(self, request):
        qs = User.objects.filter(role='user').annotate(
            booking_count=Count('bookings', distinct=True),
            total_spent=Sum(
                'bookings__final_price',
                filter=Q(
                    bookings__booking_status__in=[
                        BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
                    ]
                ),
            ),
        ).order_by('-created_at').iterator()

        header = [
            'ID', 'Username', 'Name', 'Email', 'Phone',
            'Verified', 'Bookings', 'Total Spent', 'Joined',
        ]

        def rows():
            for u in qs:
                yield [
                    u.id, u.username,
                    u.get_full_name() or u.username,
                    u.email or '', u.phone_number or '',
                    'Yes' if u.is_verified else 'No',
                    u.booking_count, float(u.total_spent or 0),
                    str(u.created_at),
                ]

        log_admin_action(request, 'export_users', 'User', 0)
        return streaming_csv_response('users_export.csv', header, rows())


# ═══════════════════════════════════════════════════════════════════════════
# BOOKINGS & TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════════════

class BookingListView(TruffAdminRequiredMixin, View):
    """Booking table with search and filters."""

    def get(self, request):
        qs = Booking.objects.select_related('user', 'turf', 'turf__owner').all()

        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(
                Q(id__icontains=search) |
                Q(user__username__icontains=search) |
                Q(turf__name__icontains=search)
            )

        status = request.GET.get('status')
        if status:
            qs = qs.filter(booking_status=status)

        date_from = request.GET.get('date_from')
        if date_from:
            qs = qs.filter(booking_date__gte=date_from)
        date_to = request.GET.get('date_to')
        if date_to:
            qs = qs.filter(booking_date__lte=date_to)

        paginator = Paginator(qs.order_by('-created_at'), 25)
        page = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'truff_admin/bookings_list.html', {
            'page': page,
            'search_query': search,
            'current_status': status or '',
            'date_from': date_from or '',
            'date_to': date_to or '',
            'status_choices': BookingStatus.choices,
        })


class BookingDetailView(TruffAdminRequiredMixin, View):
    """Booking detail with ledger entries."""

    def get(self, request, booking_id):
        booking = get_object_or_404(
            Booking.objects.select_related('user', 'turf', 'turf__owner'),
            pk=booking_id,
        )
        ledger = LedgerEntry.objects.filter(booking=booking).order_by('created_at')

        try:
            payment = booking.payment
        except Payment.DoesNotExist:
            payment = None

        return render(request, 'truff_admin/booking_detail.html', {
            'booking': booking,
            'ledger_entries': ledger,
            'payment': payment,
        })


# ═══════════════════════════════════════════════════════════════════════════
# REVENUE & FINANCE
# ═══════════════════════════════════════════════════════════════════════════

class RevenueView(TruffAdminRequiredMixin, View):
    """Revenue analytics page."""

    def get(self, request):
        confirmed = Booking.objects.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED]
        )

        totals = confirmed.aggregate(
            total_gross=Sum('final_price'),
            total_platform_fee=Sum('platform_fee'),
            total_commission=Sum('commission'),
            total_gst=Sum('gst_amount'),
            total_owner_payout=Sum('owner_payout'),
            booking_count=Count('id'),
        )

        # Monthly breakdown — SQLite-safe
        from django.db import connection
        is_sqlite = 'sqlite' in connection.vendor

        if is_sqlite:
            monthly = list(
                confirmed
                .extra(select={'month': "strftime('%%Y-%%m', booking_date)"})
                .values('month')
                .annotate(
                    gross=Sum('final_price'),
                    platform_fee=Sum('platform_fee'),
                    commission=Sum('commission'),
                    owner_payout=Sum('owner_payout'),
                    count=Count('id'),
                )
                .order_by('-month')[:12]
            )
        else:
            monthly = list(
                confirmed
                .annotate(month=TruncMonth('booking_date'))
                .values('month')
                .annotate(
                    gross=Sum('final_price'),
                    platform_fee=Sum('platform_fee'),
                    commission=Sum('commission'),
                    owner_payout=Sum('owner_payout'),
                    count=Count('id'),
                )
                .order_by('-month')[:12]
            )

        return render(request, 'truff_admin/revenue.html', {
            'totals': totals,
            'monthly': monthly,
        })


# ═══════════════════════════════════════════════════════════════════════════
# PIN CHANGE REQUESTS
# ═══════════════════════════════════════════════════════════════════════════

class PinRequestListView(TruffAdminRequiredMixin, View):
    """List PIN change requests."""

    def get(self, request):
        qs = PinChangeRequest.objects.select_related('owner', 'admin').all()

        status = request.GET.get('status')
        if status:
            qs = qs.filter(status=status)

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'truff_admin/pin_requests.html', {
            'page': page,
            'current_status': status or '',
            'status_choices': PinChangeRequestStatus.choices,
        })

    def post(self, request):
        """Handle approve/reject for a PIN request."""
        req_id = request.POST.get('request_id')
        action = request.POST.get('action')
        notes = request.POST.get('notes', '')

        pin_req = get_object_or_404(PinChangeRequest, pk=req_id)

        if action == 'approve':
            pin_req.status = PinChangeRequestStatus.APPROVED
            pin_req.admin = request.user
            pin_req.notes = notes
            pin_req.resolved_at = timezone.now()
            pin_req.save()
            log_admin_action(request, 'pin_request_approved', 'PinChangeRequest', pin_req.pk, {
                'owner': str(pin_req.owner), 'notes': notes,
            })
        elif action == 'reject':
            pin_req.status = PinChangeRequestStatus.REJECTED
            pin_req.admin = request.user
            pin_req.notes = notes
            pin_req.resolved_at = timezone.now()
            pin_req.save()
            log_admin_action(request, 'pin_request_rejected', 'PinChangeRequest', pin_req.pk, {
                'owner': str(pin_req.owner), 'notes': notes,
            })

        return redirect('/truff-admin/pin-requests/')


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════

class AuditLogView(TruffAdminRequiredMixin, View):
    """Searchable, immutable audit log viewer."""

    def get(self, request):
        qs = AdminAuditLog.objects.all()

        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(
                Q(actor_name__icontains=search) |
                Q(action__icontains=search) |
                Q(target_model__icontains=search)
            )

        action_filter = request.GET.get('action')
        if action_filter:
            qs = qs.filter(action=action_filter)

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        # Distinct actions for dropdown
        actions = AdminAuditLog.objects.values_list('action', flat=True).distinct().order_by('action')

        return render(request, 'truff_admin/audit_log.html', {
            'page': page,
            'search_query': search,
            'current_action': action_filter or '',
            'actions': actions,
        })


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

class SettingsView(TruffAdminRequiredMixin, View):
    """Admin settings: config values + admin user management."""

    def get(self, request):
        configs = AdminConfig.objects.all().order_by('key')

        # Ensure defaults exist
        defaults = {
            'platform_fee_percent': ('5', 'Platform convenience fee percentage'),
            'flat_transaction_fee': ('10', 'Flat fee per transaction (₹)'),
            'export_row_limit': ('10000', 'Max rows in CSV export'),
        }
        for key, (val, desc) in defaults.items():
            AdminConfig.objects.get_or_create(
                key=key,
                defaults={'value': val, 'description': desc},
            )

        configs = AdminConfig.objects.all().order_by('key')
        admin_users = User.objects.filter(
            Q(is_staff=True) | Q(groups__name='Truff Admin')
        ).distinct()

        return render(request, 'truff_admin/settings.html', {
            'configs': configs,
            'admin_users': admin_users,
        })

    def post(self, request):
        """Update config values."""
        for key in request.POST:
            if key.startswith('config_'):
                config_key = key.replace('config_', '')
                new_value = request.POST[key]
                try:
                    config = AdminConfig.objects.get(key=config_key)
                    old_value = config.value
                    config.value = new_value
                    config.updated_by = request.user
                    config.save()
                    log_admin_action(request, 'config_updated', 'AdminConfig', config.pk, {
                        'key': config_key, 'old_value': old_value, 'new_value': new_value,
                    })
                except AdminConfig.DoesNotExist:
                    pass

        return redirect('/truff-admin/settings/')


# ═══════════════════════════════════════════════════════════════════════════
# CSV EXPORTS
# ═══════════════════════════════════════════════════════════════════════════

class ExportBookingsView(TruffAdminRequiredMixin, View):
    def get(self, request):
        qs = Booking.objects.select_related('user', 'turf').order_by('-created_at').iterator()
        header = [
            'ID', 'User', 'Turf', 'Date', 'Start', 'End', 'Status',
            'Final Price', 'Platform Fee', 'Owner Payout', 'Created At',
        ]

        def rows():
            for b in qs:
                yield [
                    b.id, b.user.username, b.turf.name,
                    str(b.booking_date), str(b.start_time), str(b.end_time),
                    b.booking_status, float(b.final_price), float(b.platform_fee),
                    float(b.owner_payout), str(b.created_at),
                ]

        log_admin_action(request, 'export_bookings', 'Booking', 0)
        return streaming_csv_response('bookings_export.csv', header, rows())


class ExportOwnersView(TruffAdminRequiredMixin, View):
    def get(self, request):
        qs = User.objects.filter(role='turf_owner').annotate(
            turf_count=Count('turfs', distinct=True),
            booking_count=Count('turfs__bookings', distinct=True),
            total_rev=Sum('turfs__bookings__final_price'),
        ).iterator()
        header = ['ID', 'Username', 'Phone', 'Name', 'Turfs', 'Bookings', 'Revenue', 'Joined']

        def rows():
            for o in qs:
                yield [
                    o.id, o.username, o.phone_number or '',
                    o.get_full_name() or o.username,
                    o.turf_count, o.booking_count,
                    float(o.total_rev or 0), str(o.created_at),
                ]

        log_admin_action(request, 'export_owners', 'User', 0)
        return streaming_csv_response('owners_export.csv', header, rows())


class ExportTurfsView(TruffAdminRequiredMixin, View):
    def get(self, request):
        qs = Turf.objects.select_related('owner').order_by('-created_at').iterator()
        header = ['ID', 'Name', 'City', 'Owner', 'Status', 'Rating', 'Created']

        def rows():
            for t in qs:
                yield [
                    t.id, t.name, t.city or '', t.owner.username,
                    t.status, float(t.rating), str(t.created_at),
                ]

        log_admin_action(request, 'export_turfs', 'Turf', 0)
        return streaming_csv_response('turfs_export.csv', header, rows())


class ExportRevenueView(TruffAdminRequiredMixin, View):
    def get(self, request):
        from django.db import connection
        is_sqlite = 'sqlite' in connection.vendor

        base = Booking.objects.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED]
        )

        if is_sqlite:
            qs = (
                base
                .extra(select={'month_str': "strftime('%%Y-%%m', booking_date)"})
                .values('month_str')
                .annotate(
                    gross=Sum('final_price'),
                    platform_fee=Sum('platform_fee'),
                    commission=Sum('commission'),
                    owner_payout=Sum('owner_payout'),
                    count=Count('id'),
                )
                .order_by('-month_str')
            )
        else:
            qs = (
                base
                .annotate(month=TruncMonth('booking_date'))
                .values('month')
                .annotate(
                    gross=Sum('final_price'),
                    platform_fee=Sum('platform_fee'),
                    commission=Sum('commission'),
                    owner_payout=Sum('owner_payout'),
                    count=Count('id'),
                )
                .order_by('-month')
            )

        header = ['Month', 'Gross Revenue', 'Platform Fee', 'Commission', 'Owner Payout', 'Bookings']

        def rows():
            for r in qs:
                month_val = r.get('month_str') or r.get('month')
                if hasattr(month_val, 'strftime'):
                    month_val = month_val.strftime('%Y-%m')
                yield [
                    month_val,
                    float(r['gross'] or 0), float(r['platform_fee'] or 0),
                    float(r['commission'] or 0), float(r['owner_payout'] or 0),
                    r['count'],
                ]

        log_admin_action(request, 'export_revenue', 'Booking', 0)
        return streaming_csv_response('revenue_export.csv', header, rows())


# ═══════════════════════════════════════════════════════════════════════════
# CALL MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class CallListView(TruffAdminRequiredMixin, View):
    """Call log listing — admin sees REAL phone numbers."""

    def get(self, request):
        qs = CallRecord.objects.select_related(
            'booking', 'booking__user', 'booking__turf', 'initiated_by'
        ).order_by('-started_at')

        # Filters
        status_filter = request.GET.get('status', '')
        if status_filter:
            qs = qs.filter(status=status_filter)

        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        if date_from:
            qs = qs.filter(started_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(started_at__date__lte=date_to)

        owner_filter = request.GET.get('owner', '')
        if owner_filter:
            qs = qs.filter(initiated_by__username__icontains=owner_filter)

        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(
                Q(booking__id__icontains=search) |
                Q(booking__user__username__icontains=search) |
                Q(booking__user__first_name__icontains=search) |
                Q(initiated_by__username__icontains=search)
            )

        # Stats
        total_calls = qs.count()
        total_duration = sum(c.duration_seconds for c in qs[:100])

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        return render(request, 'truff_admin/call_management.html', {
            'page': page,
            'total_calls': total_calls,
            'total_duration_min': round(total_duration / 60, 1),
            'status_choices': CallStatus.choices,
            'current_status': status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'owner_filter': owner_filter,
            'search_query': search,
        })


class CallDetailView(TruffAdminRequiredMixin, View):
    """Single call record detail — admin sees full info."""

    def get(self, request, call_id):
        call = get_object_or_404(
            CallRecord.objects.select_related(
                'booking', 'booking__user', 'booking__turf', 'initiated_by'
            ),
            pk=call_id,
        )
        return render(request, 'truff_admin/call_detail.html', {
            'call': call,
        })


class CallQueueView(TruffAdminRequiredMixin, View):
    """Pending calls needing admin attention."""

    def get(self, request):
        pending = CallRecord.objects.filter(
            admin_acknowledged=False,
        ).select_related(
            'booking', 'booking__user', 'booking__turf', 'initiated_by'
        ).order_by('-started_at')

        active = CallRecord.objects.filter(
            status=CallStatus.CONNECTED,
        ).select_related(
            'booking', 'booking__user', 'booking__turf', 'initiated_by'
        ).order_by('-started_at')

        return render(request, 'truff_admin/call_queue.html', {
            'pending_calls': pending,
            'active_calls': active,
        })


class PendingCallsJsonView(TruffAdminRequiredMixin, View):
    """
    JSON endpoint polled by admin dashboard JS every 10s.
    Returns unacknowledged calls for badge + toast.
    GET /truff-admin/calls/pending/json/
    """

    def get(self, request):
        pending = CallRecord.objects.filter(
            admin_acknowledged=False,
        ).select_related(
            'booking', 'booking__user', 'booking__turf', 'initiated_by'
        ).order_by('-started_at')[:20]

        calls = []
        for c in pending:
            owner = c.initiated_by
            customer = c.booking.user
            calls.append({
                'id': c.id,
                'booking_id': c.booking.id,
                'turf_name': c.booking.turf.name,
                'owner_name': f'{owner.first_name} {owner.last_name}'.strip() or owner.username,
                'customer_name': f'{customer.first_name} {customer.last_name}'.strip() or customer.username,
                'customer_phone': customer.phone_number or '',
                'status': c.status,
                'started_at': c.started_at.isoformat(),
                'admin_notified': c.admin_notified,
            })

        # Mark as notified (admin has seen them via poll)
        CallRecord.objects.filter(
            admin_acknowledged=False, admin_notified=False,
        ).update(admin_notified=True)

        return JsonResponse({
            'count': len(calls),
            'calls': calls,
        })


@method_decorator(csrf_protect, name='dispatch')
class CallAcknowledgeView(TruffAdminRequiredMixin, View):
    """Admin acknowledges a pending call. Badge count decreases."""

    def post(self, request, call_id):
        call = get_object_or_404(CallRecord, pk=call_id)
        call.admin_acknowledged = True
        call.admin_acknowledged_at = timezone.now()
        call.save(update_fields=['admin_acknowledged', 'admin_acknowledged_at'])

        log_admin_action(request, 'call_acknowledge', 'CallRecord', call_id)
        return JsonResponse({'success': True, 'call_id': call_id})


@method_decorator(csrf_protect, name='dispatch')
class CallConnectView(TruffAdminRequiredMixin, View):
    """Admin connects the call (marks as connected). Telephony hook point."""

    def post(self, request, call_id):
        call = get_object_or_404(CallRecord, pk=call_id)
        call.status = CallStatus.CONNECTED
        call.admin_acknowledged = True
        call.admin_acknowledged_at = timezone.now()

        notes = request.POST.get('notes', '')
        if notes:
            call.admin_notes = notes

        call.save(update_fields=[
            'status', 'admin_acknowledged', 'admin_acknowledged_at', 'admin_notes',
        ])

        log_admin_action(request, 'call_connect', 'CallRecord', call_id)

        # TODO: Telephony hook — call owner, then customer, create conference
        # This is where Twilio/Exotel integration goes.

        return JsonResponse({'success': True, 'call_id': call_id, 'status': 'connected'})
