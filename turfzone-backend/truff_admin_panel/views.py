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

            # All-time aggregates — use platform_revenue as single source of truth
            totals = confirmed_bookings.aggregate(
                revenue=Sum('platform_revenue'),
                user_fees=Sum('platform_fee'),
                gst_on_pf=Sum('gst_on_platform_fee'),
                owner_commission=Sum('commission'),
                gst_on_commission=Sum('gst_on_commission'),
            )
            total_platform_revenue = totals['revenue'] or Decimal('0')
            total_user_fees = (totals['user_fees'] or Decimal('0')) + (totals['gst_on_pf'] or Decimal('0'))
            total_owner_commission = (totals['owner_commission'] or Decimal('0')) + (totals['gst_on_commission'] or Decimal('0'))

            # Today aggregates
            today_totals = confirmed_bookings.filter(
                booking_date=today
            ).aggregate(
                revenue=Sum('platform_revenue'),
                user_fees=Sum('platform_fee'),
                gst_on_pf=Sum('gst_on_platform_fee'),
                owner_commission=Sum('commission'),
                gst_on_commission=Sum('gst_on_commission'),
            )
            today_platform_revenue = today_totals['revenue'] or Decimal('0')
            today_user_fees = (today_totals['user_fees'] or Decimal('0')) + (today_totals['gst_on_pf'] or Decimal('0'))
            today_owner_commission = (today_totals['owner_commission'] or Decimal('0')) + (today_totals['gst_on_commission'] or Decimal('0'))

        except Exception:
            total_platform_revenue = Decimal('0')
            today_platform_revenue = Decimal('0')
            total_user_fees = Decimal('0')
            total_owner_commission = Decimal('0')
            today_user_fees = Decimal('0')
            today_owner_commission = Decimal('0')
            confirmed_bookings = Booking.objects.none()

        return {
            'total_revenue': total_platform_revenue,
            'today_revenue': today_platform_revenue,
            'total_user_fees': total_user_fees,
            'total_owner_commission': total_owner_commission,
            'today_user_fees': today_user_fees,
            'today_owner_commission': today_owner_commission,
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
                    .annotate(total=Sum('platform_revenue'))
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
                    .annotate(total=Sum('platform_revenue'))
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
                    .annotate(total=Sum('platform_revenue'))
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
                    .annotate(total=Sum('platform_revenue'))
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
        turf = get_object_or_404(
            Turf.objects.select_related('owner', 'approved_by').prefetch_related('sports', 'amenities'),
            pk=turf_id,
        )
        images = turf.images.all()
        sports = turf.sports.all()
        amenities = turf.amenities.all()
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

        # TurfOwner profile (bank details, GST, agreement) — may not exist for older owners
        turf_owner_profile = getattr(turf.owner, 'turf_owner_profile', None)

        return render(request, 'truff_admin/turf_detail.html', {
            'turf': turf,
            'images': images,
            'sports': sports,
            'amenities': amenities,
            'audit_logs': audit,
            'recent_bookings': bookings,
            'future_bookings_count': future_bookings_count,
            'turf_owner_profile': turf_owner_profile,
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
            total_gst_on_platform_fee=Sum('gst_on_platform_fee'),
            total_commission=Sum('commission'),
            total_gst_on_commission=Sum('gst_on_commission'),
            total_platform_revenue=Sum('platform_revenue'),
            total_owner_payout=Sum('owner_payout'),
        )

        # Compute combined card values (fees incl GST, commission incl GST)
        from decimal import Decimal
        pf = (stats['total_platform_fee'] or Decimal('0'))
        gst_pf = (stats['total_gst_on_platform_fee'] or Decimal('0'))
        comm = (stats['total_commission'] or Decimal('0'))
        gst_comm = (stats['total_gst_on_commission'] or Decimal('0'))
        stats['platform_fees_incl_gst'] = pf + gst_pf
        stats['commission_incl_gst'] = comm + gst_comm

        return render(request, 'truff_admin/owner_detail.html', {
            'owner': owner,
            'turfs': turfs,
            'bookings': bookings,
            'settlements': settlements,
            'pin_requests': pin_requests,
            'stats': stats,
            'msg': request.GET.get('msg', ''),
        })

    def post(self, request, owner_id):
        """Handle admin actions: verify bank, suspend, mark settlement paid."""
        owner = get_object_or_404(User, pk=owner_id, role='turf_owner')
        action = request.POST.get('action')
        msg = ''

        if action == 'verify_bank':
            try:
                profile = owner.turf_owner_profile
                profile.bank_verified = True
                profile.save(update_fields=['bank_verified'])
                log_admin_action(request, 'owner_bank_verified', 'TurfOwner', owner_id, {
                    'username': owner.username,
                })
                msg = 'Bank account verified.'
            except TurfOwner.DoesNotExist:
                msg = 'Owner profile not found.'

        elif action == 'unverify_bank':
            try:
                profile = owner.turf_owner_profile
                profile.bank_verified = False
                profile.save(update_fields=['bank_verified'])
                log_admin_action(request, 'owner_bank_unverified', 'TurfOwner', owner_id, {
                    'username': owner.username,
                })
                msg = 'Bank verification removed.'
            except TurfOwner.DoesNotExist:
                msg = 'Owner profile not found.'

        elif action == 'suspend':
            # Suspend all owner's turfs
            reason = request.POST.get('reason', 'Suspended by admin')
            turfs = Turf.objects.filter(owner=owner, status=TurfStatus.APPROVED)
            for turf in turfs:
                turf.suspend(reason=reason)
            owner.is_active = False
            owner.save(update_fields=['is_active'])
            log_admin_action(request, 'owner_suspended', 'User', owner_id, {
                'username': owner.username, 'reason': reason,
                'turfs_suspended': turfs.count(),
            })
            msg = f'Owner suspended. {turfs.count()} turfs deactivated.'

        elif action == 'reactivate':
            owner.is_active = True
            owner.save(update_fields=['is_active'])
            log_admin_action(request, 'owner_reactivated', 'User', owner_id, {
                'username': owner.username,
            })
            msg = 'Owner reactivated.'

        elif action == 'mark_settlement_paid':
            settlement_id = request.POST.get('settlement_id')
            utr = request.POST.get('utr', '')
            from finance.models import SettlementStatus
            settlement = get_object_or_404(OwnerSettlement, pk=settlement_id, owner=owner)
            settlement.status = SettlementStatus.COMPLETED
            settlement.settled_at = timezone.now()
            if utr:
                settlement.razorpay_transfer_id = utr
            settlement.save()
            log_admin_action(request, 'settlement_marked_paid', 'OwnerSettlement', int(settlement_id), {
                'owner': owner.username, 'amount': str(settlement.net_payout), 'utr': utr,
            })
            msg = f'Settlement #{settlement_id} marked as paid.'

        return redirect(f'/truff-admin/owners/{owner_id}/?msg={msg}')


# ═══════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT (customers)
# ═══════════════════════════════════════════════════════════════════════════

class UserListView(TruffAdminRequiredMixin, View):
    """List all registered customers with stats."""

    def get(self, request):
        from django.utils import timezone as tz
        from datetime import timedelta

        # Base queryset — all non-admin users
        qs = User.objects.exclude(role='admin')

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

        # Role filter
        role_filter = request.GET.get('role', '')
        if role_filter:
            qs = qs.filter(role=role_filter)

        # Active/Blocked status filter
        status_filter = request.GET.get('status', '')
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'blocked':
            qs = qs.filter(is_active=False)

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

        # Summary stats
        all_users = User.objects.exclude(role='admin')
        month_ago = tz.now() - timedelta(days=30)
        summary = {
            'total': all_users.count(),
            'active': all_users.filter(is_active=True).count(),
            'turf_owners': all_users.filter(role='turf_owner').count(),
            'new_this_month': all_users.filter(created_at__gte=month_ago).count(),
        }

        return render(request, 'truff_admin/users_list.html', {
            'page': page,
            'search_query': search,
            'role_filter': role_filter,
            'status_filter': status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'current_sort': sort,
            'total_users': summary['total'],
            'summary': summary,
        })


class UserDetailView(TruffAdminRequiredMixin, View):
    """Single user detail with booking/call history + admin actions."""

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

        # Referral history
        from users.models import Referral
        referrals = Referral.objects.filter(
            Q(referrer=user_obj) | Q(referee=user_obj)
        ).select_related('referrer', 'referee').order_by('-created_at')[:20]

        # Stats
        all_bookings = Booking.objects.filter(user=user_obj)
        confirmed = all_bookings.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED],
        )
        stats = confirmed.aggregate(
            total_spent=Sum('final_price'),
            total_bookings=Count('id'),
        )
        stats['cancelled'] = all_bookings.filter(
            booking_status=BookingStatus.CANCELLED
        ).count()
        stats['total_all'] = all_bookings.count()
        stats['total_calls'] = call_records.count()

        # Success message from POST redirects
        msg = request.GET.get('msg', '')

        return render(request, 'truff_admin/user_detail.html', {
            'user_obj': user_obj,
            'bookings': bookings,
            'call_records': call_records,
            'referrals': referrals,
            'stats': stats,
            'msg': msg,
        })

    def post(self, request, user_id):
        """Handle admin actions: verify, block, unblock, delete."""
        user_obj = get_object_or_404(User, pk=user_id)
        action = request.POST.get('action')
        msg = ''

        if action == 'verify':
            user_obj.is_verified = True
            user_obj.save(update_fields=['is_verified'])
            log_admin_action(request, 'user_verified', 'User', user_id, {
                'username': user_obj.username,
            })
            msg = 'User verified successfully.'

        elif action == 'unverify':
            user_obj.is_verified = False
            user_obj.save(update_fields=['is_verified'])
            log_admin_action(request, 'user_unverified', 'User', user_id, {
                'username': user_obj.username,
            })
            msg = 'User verification removed.'

        elif action == 'block':
            user_obj.is_active = False
            user_obj.save(update_fields=['is_active'])
            log_admin_action(request, 'user_blocked', 'User', user_id, {
                'username': user_obj.username,
            })
            msg = 'User blocked.'

        elif action == 'unblock':
            user_obj.is_active = True
            user_obj.save(update_fields=['is_active'])
            log_admin_action(request, 'user_unblocked', 'User', user_id, {
                'username': user_obj.username,
            })
            msg = 'User unblocked.'

        elif action == 'delete':
            # Soft delete — deactivate and anonymize
            user_obj.is_active = False
            user_obj.email = f'deleted_{user_obj.pk}@deleted.trufspot.com'
            user_obj.phone_number = ''
            user_obj.first_name = 'Deleted'
            user_obj.last_name = 'User'
            user_obj.save()
            log_admin_action(request, 'user_deleted', 'User', user_id, {
                'username': user_obj.username,
            })
            msg = 'User soft-deleted (anonymized).'

        return redirect(f'/truff-admin/users/{user_id}/?msg={msg}')


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


class UserBulkActionView(TruffAdminRequiredMixin, View):
    """Bulk actions on selected users: verify, block, export CSV."""

    def post(self, request):
        import json
        action = request.POST.get('action')
        user_ids_raw = request.POST.get('user_ids', '[]')

        try:
            user_ids = json.loads(user_ids_raw)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({'error': 'Invalid user_ids'}, status=400)

        if not user_ids:
            return JsonResponse({'error': 'No users selected'}, status=400)

        users = User.objects.filter(pk__in=user_ids)
        processed = 0

        if action == 'verify':
            processed = users.update(is_verified=True)
            for u in users:
                log_admin_action(request, 'user_verified', 'User', u.pk, {
                    'username': u.username, 'bulk': True,
                })

        elif action == 'block':
            processed = users.update(is_active=False)
            for u in users:
                log_admin_action(request, 'user_blocked', 'User', u.pk, {
                    'username': u.username, 'bulk': True,
                })

        elif action == 'unblock':
            processed = users.update(is_active=True)
            for u in users:
                log_admin_action(request, 'user_unblocked', 'User', u.pk, {
                    'username': u.username, 'bulk': True,
                })

        elif action == 'export':
            # Export selected users as CSV
            qs = users.annotate(
                booking_count=Count('bookings', distinct=True),
                total_spent=Sum(
                    'bookings__final_price',
                    filter=Q(
                        bookings__booking_status__in=[
                            BookingStatus.CONFIRMED, BookingStatus.COMPLETED,
                        ]
                    ),
                ),
            ).order_by('-created_at')
            header = ['ID', 'Username', 'Name', 'Email', 'Phone', 'Verified', 'Active', 'Bookings', 'Spent', 'Joined']

            def rows():
                for u in qs:
                    yield [
                        u.id, u.username, u.get_full_name() or u.username,
                        u.email or '', u.phone_number or '',
                        'Yes' if u.is_verified else 'No',
                        'Yes' if u.is_active else 'No',
                        u.booking_count, float(u.total_spent or 0),
                        str(u.created_at),
                    ]

            log_admin_action(request, 'export_users_selected', 'User', 0, {'count': len(user_ids)})
            return streaming_csv_response('users_selected_export.csv', header, rows())

        else:
            return JsonResponse({'error': 'Invalid action'}, status=400)

        return JsonResponse({'success': True, 'processed': processed, 'action': action})


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

        # Summary aggregates for filtered results
        summary = qs.aggregate(
            total_revenue=Sum('final_price'),
            total_payout=Sum('owner_payout'),
            total_platform_revenue=Sum('platform_revenue'),
            total_count=Count('id'),
        )

        return render(request, 'truff_admin/bookings_list.html', {
            'page': page,
            'search_query': search,
            'current_status': status or '',
            'date_from': date_from or '',
            'date_to': date_to or '',
            'status_choices': BookingStatus.choices,
            'summary': summary,
        })


class BookingDetailView(TruffAdminRequiredMixin, View):
    """Booking detail with ledger entries + admin actions."""

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

        # Call records for this booking
        calls = CallRecord.objects.filter(booking=booking).order_by('-started_at')

        return render(request, 'truff_admin/booking_detail.html', {
            'booking': booking,
            'ledger_entries': ledger,
            'payment': payment,
            'calls': calls,
            'msg': request.GET.get('msg', ''),
        })

    def post(self, request, booking_id):
        """Handle admin actions: cancel, no-show, refund."""
        booking = get_object_or_404(Booking, pk=booking_id)
        action = request.POST.get('action')
        msg = ''

        if action == 'cancel':
            reason = request.POST.get('reason', 'Cancelled by admin')
            if booking.booking_status not in [BookingStatus.CANCELLED]:
                booking.cancel(reason=reason, cancelled_by_admin=True)
                log_admin_action(request, 'booking_cancelled', 'Booking', booking_id, {
                    'reason': reason, 'user': booking.user.username,
                    'turf': booking.turf.name, 'amount': str(booking.final_price),
                })
                msg = f'Booking #{booking_id} cancelled.'
            else:
                msg = 'Booking is already cancelled.'

        elif action == 'no_show':
            booking.booking_status = BookingStatus.COMPLETED
            booking.cancellation_reason = 'No-show'
            booking.save(update_fields=['booking_status', 'cancellation_reason'])
            log_admin_action(request, 'booking_no_show', 'Booking', booking_id, {
                'user': booking.user.username, 'turf': booking.turf.name,
            })
            msg = f'Booking #{booking_id} marked as no-show.'

        elif action == 'refund':
            # Mark payment as refunded
            try:
                payment = booking.payment
                payment.status = 'refunded'
                payment.save(update_fields=['status'])
            except Payment.DoesNotExist:
                pass
            booking.payment_status = 'refunded'
            booking.save(update_fields=['payment_status'])
            log_admin_action(request, 'booking_refunded', 'Booking', booking_id, {
                'user': booking.user.username, 'amount': str(booking.final_price),
            })
            msg = f'Booking #{booking_id} marked as refunded.'

        return redirect(f'/truff-admin/bookings/{booking_id}/?msg={msg}')


# ═══════════════════════════════════════════════════════════════════════════
# REVENUE & FINANCE
# ═══════════════════════════════════════════════════════════════════════════

class RevenueView(TruffAdminRequiredMixin, View):
    """Revenue analytics page."""

    def get(self, request):
        from decimal import Decimal
        from django.db import connection
        is_sqlite = 'sqlite' in connection.vendor

        confirmed = Booking.objects.filter(
            booking_status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED]
        )
        all_bookings = Booking.objects.all()

        # ── Core totals ──
        totals = confirmed.aggregate(
            total_gross=Sum('final_price'),
            total_platform_fee=Sum('platform_fee'),
            total_gst_on_pf=Sum('gst_on_platform_fee'),
            total_commission=Sum('commission'),
            total_gst_on_commission=Sum('gst_on_commission'),
            total_gst_slots=Sum('gst_amount'),
            total_owner_payout=Sum('owner_payout'),
            total_platform_revenue=Sum('platform_revenue'),
            booking_count=Count('id'),
        )

        # ── Derived KPIs ──
        bc = totals['booking_count'] or 0
        gross = totals['total_gross'] or Decimal('0')
        totals['avg_booking_value'] = (gross / bc).quantize(Decimal('0.01')) if bc else Decimal('0')

        total_all = all_bookings.count()
        cancelled = all_bookings.filter(booking_status=BookingStatus.CANCELLED).count()
        totals['cancellation_rate'] = round(cancelled * 100 / total_all, 1) if total_all else 0
        totals['cancelled_count'] = cancelled

        gst_slots = totals['total_gst_slots'] or Decimal('0')
        gst_pf = totals['total_gst_on_pf'] or Decimal('0')
        gst_comm = totals['total_gst_on_commission'] or Decimal('0')
        totals['total_gst_all'] = gst_slots + gst_pf + gst_comm
        gst_total = totals['total_gst_all'] or Decimal('1')
        totals['gst_slots_pct'] = round(gst_slots * 100 / gst_total, 1) if gst_total else 0
        totals['gst_pf_pct'] = round(gst_pf * 100 / gst_total, 1) if gst_total else 0
        totals['gst_comm_pct'] = round(gst_comm * 100 / gst_total, 1) if gst_total else 0

        # ── Top 5 Turfs by revenue ──
        top_turfs = list(
            confirmed
            .values('turf__id', 'turf__name', 'turf__owner__username')
            .annotate(
                bookings=Count('id'),
                revenue=Sum('final_price'),
                platform_earned=Sum('platform_revenue'),
            )
            .order_by('-revenue')[:5]
        )

        # ── Monthly breakdown — SQLite-safe ──
        if is_sqlite:
            monthly = list(
                confirmed
                .extra(select={'month': "strftime('%%Y-%%m', booking_date)"})
                .values('month')
                .annotate(
                    gross=Sum('final_price'),
                    platform_fee=Sum('platform_fee'),
                    commission=Sum('commission'),
                    gst=Sum('gst_amount'),
                    owner_payout=Sum('owner_payout'),
                    platform_revenue=Sum('platform_revenue'),
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
                    gst=Sum('gst_amount'),
                    owner_payout=Sum('owner_payout'),
                    platform_revenue=Sum('platform_revenue'),
                    count=Count('id'),
                )
                .order_by('-month')[:12]
            )

        return render(request, 'truff_admin/revenue.html', {
            'totals': totals,
            'monthly': monthly,
            'top_turfs': top_turfs,
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
                Q(target_model__icontains=search) |
                Q(ip_address__icontains=search)
            )

        action_filter = request.GET.get('action')
        if action_filter:
            qs = qs.filter(action=action_filter)

        model_filter = request.GET.get('model')
        if model_filter:
            qs = qs.filter(target_model=model_filter)

        date_from = request.GET.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = request.GET.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        paginator = Paginator(qs, 50)
        page = paginator.get_page(request.GET.get('page', 1))

        # Distinct actions and models for dropdowns
        actions = AdminAuditLog.objects.values_list('action', flat=True).distinct().order_by('action')
        models_list = AdminAuditLog.objects.values_list('target_model', flat=True).distinct().order_by('target_model')

        # Summary stats
        all_logs = AdminAuditLog.objects.all()
        today = timezone.localdate()
        summary = {
            'total': all_logs.count(),
            'today': all_logs.filter(created_at__date=today).count(),
            'actors': all_logs.values('actor_name').distinct().count(),
            'filtered': qs.count(),
        }

        return render(request, 'truff_admin/audit_log.html', {
            'page': page,
            'search_query': search,
            'current_action': action_filter or '',
            'current_model': model_filter or '',
            'date_from': date_from or '',
            'date_to': date_to or '',
            'actions': actions,
            'models_list': models_list,
            'summary': summary,
        })


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

class SettingsView(TruffAdminRequiredMixin, View):
    """Admin settings: config values + admin user management."""

    def get(self, request):
        import django
        from django.db import connection

        # Ensure defaults exist
        defaults = {
            'platform_fee_percent': ('5', 'Platform convenience fee percentage'),
            'flat_transaction_fee': ('10', 'Flat fee per transaction (INR)'),
            'export_row_limit': ('10000', 'Max rows in CSV export'),
            'gst_rate': ('18', 'GST rate percentage'),
            'booking_buffer_mins': ('30', 'Minutes before slot to block booking'),
            'max_advance_days': ('30', 'Max days in advance users can book'),
            'min_cancel_hours': ('4', 'Hours before slot for free cancellation'),
        }
        for key, (val, desc) in defaults.items():
            AdminConfig.objects.get_or_create(
                key=key,
                defaults={'value': val, 'description': desc},
            )

        configs = AdminConfig.objects.all().order_by('key')
        admin_users = User.objects.filter(
            Q(is_staff=True) | Q(groups__name='Truff Admin')
        ).distinct().select_related().prefetch_related('groups')

        # Recent settings changes from audit log
        recent_changes = AdminAuditLog.objects.filter(
            action='config_updated',
        ).order_by('-created_at')[:10]

        # System info
        sys_info = {
            'django_version': django.get_version(),
            'python_version': f'{__import__("sys").version_info.major}.{__import__("sys").version_info.minor}.{__import__("sys").version_info.micro}',
            'db_engine': connection.vendor,
            'timezone': str(__import__("django.conf", fromlist=["settings"]).settings.TIME_ZONE),
            'debug': __import__("django.conf", fromlist=["settings"]).settings.DEBUG,
            'total_users': User.objects.count(),
            'total_bookings': Booking.objects.count(),
        }

        return render(request, 'truff_admin/settings.html', {
            'configs': configs,
            'admin_users': admin_users,
            'recent_changes': recent_changes,
            'sys_info': sys_info,
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

# ═══════════════════════════════════════════════════════════════════════════
# TURF EDIT
# ═══════════════════════════════════════════════════════════════════════════

class TurfEditView(TruffAdminRequiredMixin, View):
    """Edit turf details form."""

    def get(self, request, turf_id):
        turf = get_object_or_404(
            Turf.objects.select_related('owner').prefetch_related('sports', 'amenities'),
            pk=turf_id,
        )
        from turfs.models import Sport, Amenity
        all_sports = Sport.objects.all()
        all_amenities = Amenity.objects.all()
        return render(request, 'truff_admin/turf_edit.html', {
            'turf': turf,
            'all_sports': all_sports,
            'all_amenities': all_amenities,
        })

    def post(self, request, turf_id):
        turf = get_object_or_404(Turf, pk=turf_id)
        from turfs.models import Sport, Amenity
        turf.name = request.POST.get('name', turf.name)
        turf.description = request.POST.get('description', turf.description)
        turf.address = request.POST.get('address', turf.address)
        turf.city = request.POST.get('city', turf.city)
        turf.state = request.POST.get('state', turf.state)
        price = request.POST.get('price_per_hour')
        if price:
            try:
                turf.price_per_hour = Decimal(price)
            except Exception:
                pass
        max_players = request.POST.get('max_players')
        if max_players:
            try:
                turf.max_players = int(max_players)
            except Exception:
                pass
        turf.save()
        sport_ids = request.POST.getlist('sports')
        if sport_ids:
            turf.sports.set(Sport.objects.filter(pk__in=sport_ids))
        amenity_ids = request.POST.getlist('amenities')
        turf.amenities.set(Amenity.objects.filter(pk__in=amenity_ids))
        log_admin_action(request, 'turf_edited', 'Turf', turf_id, {'turf_name': turf.name})
        return redirect(f'/truff-admin/turfs/{turf_id}/?msg=Turf+updated+successfully.')


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG EXPORT
# ═══════════════════════════════════════════════════════════════════════════

class ExportAuditLogView(TruffAdminRequiredMixin, View):
    """CSV export of audit log with current filters."""

    def get(self, request):
        import json
        qs = AdminAuditLog.objects.all()
        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(
                Q(actor_name__icontains=search) | Q(action__icontains=search) | Q(target_model__icontains=search)
            )
        if request.GET.get('action'):
            qs = qs.filter(action=request.GET['action'])
        if request.GET.get('model'):
            qs = qs.filter(target_model=request.GET['model'])
        if request.GET.get('date_from'):
            qs = qs.filter(created_at__date__gte=request.GET['date_from'])
        if request.GET.get('date_to'):
            qs = qs.filter(created_at__date__lte=request.GET['date_to'])

        header = ['ID', 'Actor', 'Action', 'Target Model', 'Target ID', 'Details', 'IP', 'Timestamp']

        def rows():
            for log in qs.order_by('-created_at').iterator():
                yield [
                    log.id, log.actor_name, log.action,
                    log.target_model, log.target_id,
                    json.dumps(log.details) if log.details else '',
                    log.ip_address or '', str(log.created_at),
                ]

        log_admin_action(request, 'export_audit_log', 'AdminAuditLog', 0)
        return streaming_csv_response('audit_log_export.csv', header, rows())


# ═══════════════════════════════════════════════════════════════════════════
# PROMO CODE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class PromoCodeListView(TruffAdminRequiredMixin, View):
    """List promo codes with filters."""

    def get(self, request):
        from users.models import PromoCode
        qs = PromoCode.objects.all()
        search = request.GET.get('q', '')
        if search:
            qs = qs.filter(code__icontains=search)
        status_filter = request.GET.get('status', '')
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)
        paginator = Paginator(qs.order_by('-created_at'), 25)
        page = paginator.get_page(request.GET.get('page', 1))
        from users.models import PromoCode as PC
        total = PC.objects.count()
        active = PC.objects.filter(is_active=True).count()
        summary = {
            'total': total,
            'active': active,
            'inactive': total - active,
            'total_uses': 0,  # Usage tracking not yet in model
        }
        return render(request, 'truff_admin/promo_codes.html', {
            'page': page, 'search_query': search,
            'status_filter': status_filter, 'summary': summary,
        })


class PromoCodeCreateView(TruffAdminRequiredMixin, View):
    """Create a new promo code."""

    def get(self, request):
        return render(request, 'truff_admin/promo_code_form.html', {'mode': 'create'})

    def post(self, request):
        from users.models import PromoCode
        code = request.POST.get('code', '').strip().upper()
        if not code:
            return render(request, 'truff_admin/promo_code_form.html', {'mode': 'create', 'error': 'Code is required.'})
        if PromoCode.objects.filter(code=code).exists():
            return render(request, 'truff_admin/promo_code_form.html', {
                'mode': 'create', 'error': f'Code "{code}" already exists.',
            })
        try:
            promo = PromoCode.objects.create(
                code=code,
                discount_type=request.POST.get('discount_type', 'percentage'),
                discount_value=Decimal(request.POST.get('discount_value', '0')),
                min_order_value=Decimal(request.POST.get('min_order_value', '0')),
                valid_from=request.POST.get('valid_from') or timezone.now(),
                valid_until=request.POST.get('valid_until') or (timezone.now() + timedelta(days=365)),
                max_uses=int(request.POST.get('max_uses', '1')),
                is_active=request.POST.get('is_active') == 'on',
            )
            log_admin_action(request, 'promo_code_created', 'PromoCode', promo.pk, {'code': code})
        except Exception as e:
            return render(request, 'truff_admin/promo_code_form.html', {'mode': 'create', 'error': str(e)})
        return redirect('/truff-admin/promo-codes/')


class PromoCodeEditView(TruffAdminRequiredMixin, View):
    """Edit an existing promo code."""

    def get(self, request, code_id):
        from users.models import PromoCode
        promo = get_object_or_404(PromoCode, pk=code_id)
        return render(request, 'truff_admin/promo_code_form.html', {'mode': 'edit', 'promo': promo})

    def post(self, request, code_id):
        from users.models import PromoCode
        promo = get_object_or_404(PromoCode, pk=code_id)
        promo.code = request.POST.get('code', promo.code).strip().upper()
        promo.discount_type = request.POST.get('discount_type', promo.discount_type)
        try:
            promo.discount_value = Decimal(request.POST.get('discount_value', str(promo.discount_value)))
            promo.min_order_amount = Decimal(request.POST.get('min_order_amount', str(promo.min_order_value)))
            promo.max_uses = int(request.POST.get('max_uses', str(promo.max_uses)))
        except Exception:
            pass
        promo.valid_from = request.POST.get('valid_from') or promo.valid_from
        promo.valid_until = request.POST.get('valid_until') or promo.valid_until
        promo.is_active = request.POST.get('is_active') == 'on'
        promo.save()
        log_admin_action(request, 'promo_code_updated', 'PromoCode', promo.pk, {'code': promo.code})
        return redirect('/truff-admin/promo-codes/')


class PromoCodeDeleteView(TruffAdminRequiredMixin, View):
    """Delete a promo code."""

    def post(self, request, code_id):
        from users.models import PromoCode
        promo = get_object_or_404(PromoCode, pk=code_id)
        code = promo.code
        promo.delete()
        log_admin_action(request, 'promo_code_deleted', 'PromoCode', code_id, {'code': code})
        return redirect('/truff-admin/promo-codes/')


# =============================================================================
# SUPPORT CHAT — ADMIN PANEL VIEWS  (appended)
# =============================================================================

class SupportDashboardView(TruffAdminRequiredMixin, View):
    """Support ticket dashboard — list all tickets with filters and stats."""

    def get(self, request):
        from support.models import SupportTicket
        from django.utils import timezone as tz
        from datetime import timedelta

        qs = SupportTicket.objects.select_related('user', 'assigned_to').all()

        # Search
        q = request.GET.get('q', '')
        if q:
            qs = qs.filter(
                Q(ticket_id__icontains=q) |
                Q(user__username__icontains=q) |
                Q(user__first_name__icontains=q) |
                Q(subject__icontains=q)
            )

        # Status filter
        status_filter = request.GET.get('status', '')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Priority filter
        priority_filter = request.GET.get('priority', '')
        if priority_filter:
            qs = qs.filter(priority=priority_filter)

        paginator = Paginator(qs, 25)
        page = paginator.get_page(request.GET.get('page', 1))

        # Stats
        today_start = tz.now().replace(hour=0, minute=0, second=0, microsecond=0)
        summary = {
            'open': SupportTicket.objects.filter(status='open').count(),
            'awaiting': SupportTicket.objects.filter(status='awaiting_reply').count(),
            'resolved_today': SupportTicket.objects.filter(
                status='resolved', resolved_at__gte=today_start
            ).count(),
            'total': SupportTicket.objects.count(),
            'unread': sum(t.unread_for_admin for t in SupportTicket.objects.filter(status__in=['open', 'awaiting_reply'])),
        }

        return render(request, 'truff_admin/support_dashboard.html', {
            'page': page,
            'search_query': q,
            'status_filter': status_filter,
            'priority_filter': priority_filter,
            'summary': summary,
        })


class SupportTicketDetailView(TruffAdminRequiredMixin, View):
    """View and respond to a single support ticket."""

    def get(self, request, ticket_id):
        from support.models import SupportTicket

        ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)

        # JSON poll endpoint — called by frontend JS every 12s
        if request.GET.get('poll'):
            after = request.GET.get('after')
            qs = ticket.messages.all()
            if after:
                try:
                    qs = qs.filter(created_at__gt=after)
                except Exception:
                    pass
            new_count = qs.exclude(sender=request.user).count()
            return JsonResponse({'new_count': new_count})

        # Mark user messages as read when admin views ticket
        ticket.messages.filter(is_read=False).exclude(
            sender=request.user
        ).update(is_read=True)

        return render(request, 'truff_admin/support_ticket.html', {
            'ticket': ticket,
            'messages': ticket.messages.select_related('sender').all(),
        })

    def post(self, request, ticket_id):
        from support.models import SupportTicket, SupportMessage

        ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)
        action = request.POST.get('action', 'reply')

        if action == 'reply':
            text = request.POST.get('message', '').strip()
            if text:
                SupportMessage.objects.create(
                    ticket=ticket,
                    sender=request.user,
                    message=text,
                )
                # Update status → awaiting_reply (waiting for user)
                if ticket.status == 'open':
                    ticket.status = 'awaiting_reply'
                    ticket.save(update_fields=['status', 'updated_at'])
                log_admin_action(
                    request, 'support_reply', 'SupportTicket', ticket.pk,
                    {'ticket_id': ticket_id, 'message_len': len(text)},
                )

        elif action == 'assign':
            ticket.assigned_to = request.user
            ticket.save(update_fields=['assigned_to', 'updated_at'])
            log_admin_action(request, 'support_assigned', 'SupportTicket', ticket.pk, {'ticket_id': ticket_id})

        elif action == 'resolve':
            from django.utils import timezone as tz
            ticket.status = 'resolved'
            ticket.resolved_at = tz.now()
            ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
            log_admin_action(request, 'support_resolved', 'SupportTicket', ticket.pk, {'ticket_id': ticket_id})

        elif action == 'close':
            ticket.status = 'closed'
            ticket.save(update_fields=['status', 'updated_at'])
            log_admin_action(request, 'support_closed', 'SupportTicket', ticket.pk, {'ticket_id': ticket_id})

        elif action == 'reopen':
            ticket.status = 'open'
            ticket.resolved_at = None
            ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
            log_admin_action(request, 'support_reopened', 'SupportTicket', ticket.pk, {'ticket_id': ticket_id})

        return redirect(f'/truff-admin/support/{ticket_id}/')


class SupportUnreadCountView(TruffAdminRequiredMixin, View):
    """JSON endpoint — unread ticket count for sidebar badge polling."""

    def get(self, request):
        from support.models import SupportTicket
        count = sum(
            t.unread_for_admin
            for t in SupportTicket.objects.filter(status__in=['open', 'awaiting_reply'])
        )
        return JsonResponse({'unread': count})


# ═══════════════════════════════════════════════════════════════════════════
# OFFER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

from growth.models import OfferConfig, OfferUsage


def _get_offer_config(offer_type):
    """Helper — get or auto-create an OfferConfig for the given type."""
    names = {
        'first_booking': 'First Booking Discount',
        'referral': 'Referral Program',
        'last_minute': 'Last Minute Deals',
        'streak': 'Streak Rewards',
        'loyalty': 'Loyalty Tiers',
        'captain': 'Captain Rewards',
        'wallet': 'Wallet Cashback',
    }
    return OfferConfig.get_or_create_default(offer_type, names.get(offer_type, offer_type))


def _offer_usage_stats(offer_type):
    """Return (count_today, count_total, cost_total) for an offer type."""
    from django.utils import timezone as tz
    today = tz.localdate()
    qs = OfferUsage.objects.filter(offer_type=offer_type)
    today_count = qs.filter(created_at__date=today).count()
    total_count = qs.count()
    total_cost = qs.aggregate(c=Sum('reward_amount'))['c'] or Decimal('0')
    return today_count, total_count, total_cost


class OffersOverviewView(TruffAdminRequiredMixin, View):
    """Offer management dashboard — stats + ROI table for all offer types."""

    def get(self, request):
        OFFER_TYPES = [
            ('first_booking', 'First Booking',   'first-booking', 5.0),
            ('referral',      'Referral',         'referral',      5.0),
            ('last_minute',   'Last Minute',      'last-minute',   6.0),
            ('streak',        'Streak Rewards',   'streaks',       8.0),
            ('loyalty',       'Loyalty Tiers',    'loyalty',       8.0),
            ('captain',       'Captain',          'captain',       8.0),
            ('wallet',        'Wallet Cashback',  'wallet',        4.0),
        ]

        rows = []
        total_cost = Decimal('0')
        total_revenue = Decimal('0')
        total_uses_today = 0
        active_count = 0

        for ot, label, url_slug, roi_multiplier in OFFER_TYPES:
            cfg = _get_offer_config(ot)
            today_c, all_c, cost = _offer_usage_stats(ot)
            revenue = (cost * Decimal(str(roi_multiplier))).quantize(Decimal('0.01'))
            total_cost += cost
            total_revenue += revenue
            total_uses_today += today_c
            if cfg.is_active:
                active_count += 1
            rows.append({
                'offer_type': ot,
                'url_slug': url_slug,
                'label': label,
                'is_active': cfg.is_active,
                'uses_today': today_c,
                'uses_total': all_c,
                'cost': cost,
                'revenue': revenue,
                'roi': roi_multiplier,
            })

        overall_roi = round(float(total_revenue) / float(total_cost), 1) if total_cost else 0

        return render(request, 'truff_admin/offers_overview.html', {
            'rows': rows,
            'active_count': active_count,
            'total_uses_today': total_uses_today,
            'total_cost': total_cost,
            'total_revenue': total_revenue,
            'overall_roi': overall_roi,
        })


class FirstBookingOfferView(TruffAdminRequiredMixin, View):
    """First Booking Discount — config form + usage stats."""

    def _ctx(self, cfg):
        today_c, all_c, cost = _offer_usage_stats('first_booking')
        revenue = (cost * Decimal('5')).quantize(Decimal('0.01'))
        return {
            'cfg': cfg,
            'uses_today': today_c,
            'uses_total': all_c,
            'total_cost': cost,
            'total_revenue': revenue,
            'roi': '5.0',
        }

    def get(self, request):
        cfg = _get_offer_config('first_booking')
        return render(request, 'truff_admin/offer_first_booking.html', self._ctx(cfg))

    def post(self, request):
        cfg = _get_offer_config('first_booking')
        cfg.is_active = request.POST.get('is_active') == 'on'
        discount_type = request.POST.get('discount_type', 'fixed')
        if discount_type == 'fixed':
            cfg.discount_amount = Decimal(request.POST.get('discount_amount') or '0')
            cfg.discount_percent = None
        else:
            cfg.discount_percent = int(request.POST.get('discount_percent') or '0')
            cfg.discount_amount = None
        cfg.min_order_value = Decimal(request.POST.get('min_order_value') or '0') or None
        cfg.expiry_days = int(request.POST.get('expiry_days') or '7')
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'first_booking'})
        return redirect('/truff-admin/offers/first-booking/?saved=1')


class ReferralProgramView(TruffAdminRequiredMixin, View):
    """Referral Program — config + stats."""

    def _ctx(self, cfg):
        today_c, all_c, cost = _offer_usage_stats('referral')
        qualified = User.objects.filter(qualified_referrals__gt=0).count()
        total_referrals = User.objects.aggregate(t=Sum('total_referrals'))['t'] or 0
        conv = round((qualified / total_referrals) * 100) if total_referrals else 0
        revenue = (cost * Decimal('5')).quantize(Decimal('0.01'))
        rewards = cfg.referral_rewards or {'install': 10, 'booking': 40, 'friend': 50}
        return {
            'cfg': cfg,
            'rewards': rewards,
            'uses_today': today_c,
            'uses_total': all_c,
            'total_cost': cost,
            'total_revenue': revenue,
            'roi': '5.0',
            'total_referrals': total_referrals,
            'qualified': qualified,
            'conversion_rate': conv,
        }

    def get(self, request):
        cfg = _get_offer_config('referral')
        return render(request, 'truff_admin/offer_referral.html', self._ctx(cfg))

    def post(self, request):
        cfg = _get_offer_config('referral')
        cfg.is_active = request.POST.get('is_active') == 'on'
        cfg.referral_rewards = {
            'install': int(request.POST.get('reward_install') or '10'),
            'booking': int(request.POST.get('reward_booking') or '40'),
            'friend': int(request.POST.get('reward_friend') or '50'),
        }
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'referral'})
        return redirect('/truff-admin/offers/referral/?saved=1')


class LastMinuteDealsView(TruffAdminRequiredMixin, View):
    """Last Minute Deals — time window CRUD + stats."""

    def _ctx(self, cfg):
        today_c, all_c, cost = _offer_usage_stats('last_minute')
        windows = cfg.last_minute_windows or []
        return {
            'cfg': cfg,
            'windows': windows,
            'uses_total': all_c,
            'uses_today': today_c,
            'total_cost': cost,
            'avg_discount': round(sum(w[2] for w in windows) / len(windows)) if windows else 0,
        }

    def get(self, request):
        cfg = _get_offer_config('last_minute')
        return render(request, 'truff_admin/offer_last_minute.html', self._ctx(cfg))

    def post(self, request):
        import json
        cfg = _get_offer_config('last_minute')
        sub = request.POST.get('sub_action', 'save')

        if sub == 'save':
            cfg.is_active = request.POST.get('is_active') == 'on'
            windows_raw = request.POST.get('windows_json', '[]')
            try:
                cfg.last_minute_windows = json.loads(windows_raw)
            except (json.JSONDecodeError, TypeError):
                pass
            cfg.updated_by = request.user
            cfg.save()
            from .utils import log_admin_action
            log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'last_minute'})

        return redirect('/truff-admin/offers/last-minute/?saved=1')


class StreakRewardsView(TruffAdminRequiredMixin, View):
    """Streak Rewards — threshold/reward CRUD + active streak counts."""

    def _ctx(self, cfg):
        from growth.models import UserStreak
        thresholds = cfg.streak_thresholds or []
        rewards = cfg.streak_rewards or []
        levels = []
        for i, (t, r) in enumerate(zip(thresholds, rewards)):
            count = UserStreak.objects.filter(current_streak__gte=t).count()
            levels.append({'weeks': t, 'reward': r, 'count': count, 'idx': i})
        _, all_c, cost = _offer_usage_stats('streak')
        return {
            'cfg': cfg,
            'levels': levels,
            'uses_total': all_c,
            'total_cost': cost,
        }

    def get(self, request):
        cfg = _get_offer_config('streak')
        return render(request, 'truff_admin/offer_streaks.html', self._ctx(cfg))

    def post(self, request):
        import json
        cfg = _get_offer_config('streak')
        cfg.is_active = request.POST.get('is_active') == 'on'
        try:
            cfg.streak_thresholds = json.loads(request.POST.get('thresholds_json', '[]'))
            cfg.streak_rewards = json.loads(request.POST.get('rewards_json', '[]'))
        except (json.JSONDecodeError, TypeError):
            pass
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'streak'})
        return redirect('/truff-admin/offers/streaks/?saved=1')


class LoyaltyTiersView(TruffAdminRequiredMixin, View):
    """Loyalty Tiers — tier CRUD + user distribution."""

    def _ctx(self, cfg):
        tiers = cfg.loyalty_tiers or []
        perks = cfg.loyalty_perks or []
        TIER_NAMES = ['Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond']
        levels = []
        for i, (t, p) in enumerate(zip(tiers, perks)):
            name = TIER_NAMES[i] if i < len(TIER_NAMES) else f'Tier {i+1}'
            count = User.objects.filter(total_bookings__gte=t).count()
            levels.append({'name': name, 'threshold': t, 'perk': p, 'count': count, 'idx': i})
        _, all_c, cost = _offer_usage_stats('loyalty')
        return {
            'cfg': cfg,
            'levels': levels,
            'uses_total': all_c,
            'total_cost': cost,
        }

    def get(self, request):
        cfg = _get_offer_config('loyalty')
        return render(request, 'truff_admin/offer_loyalty.html', self._ctx(cfg))

    def post(self, request):
        import json
        cfg = _get_offer_config('loyalty')
        cfg.is_active = request.POST.get('is_active') == 'on'
        try:
            cfg.loyalty_tiers = json.loads(request.POST.get('tiers_json', '[]'))
            cfg.loyalty_perks = json.loads(request.POST.get('perks_json', '[]'))
        except (json.JSONDecodeError, TypeError):
            pass
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'loyalty'})
        return redirect('/truff-admin/offers/loyalty/?saved=1')


class CaptainRewardsView(TruffAdminRequiredMixin, View):
    """Captain Rewards — config + team stats."""

    def _ctx(self, cfg):
        from growth.models import TeamBooking
        rewards = cfg.referral_rewards or {'captain': 10, 'teammate': 20}
        total_teams = TeamBooking.objects.filter(status='joined').count()
        with_members = TeamBooking.objects.filter(status='joined').values('booking').distinct().count()
        avg_team_size = round(total_teams / with_members, 1) if with_members else 0
        new_users = TeamBooking.objects.filter(
            status='joined', member__isnull=False
        ).values('member').distinct().count()
        _, _, cost = _offer_usage_stats('captain')
        cost_per_user = (cost / new_users).quantize(Decimal('0.01')) if new_users else Decimal('0')
        return {
            'cfg': cfg,
            'rewards': rewards,
            'total_team_bookings': with_members,
            'avg_team_size': avg_team_size,
            'new_users_via_teams': new_users,
            'cost_per_new_user': cost_per_user,
        }

    def get(self, request):
        cfg = _get_offer_config('captain')
        return render(request, 'truff_admin/offer_captain.html', self._ctx(cfg))

    def post(self, request):
        cfg = _get_offer_config('captain')
        cfg.is_active = request.POST.get('is_active') == 'on'
        cfg.referral_rewards = {
            'captain': int(request.POST.get('captain_reward') or '10'),
            'teammate': int(request.POST.get('teammate_reward') or '20'),
        }
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'captain'})
        return redirect('/truff-admin/offers/captain/?saved=1')


class WalletCashbackView(TruffAdminRequiredMixin, View):
    """Wallet Cashback — config + wallet stats."""

    def _ctx(self, cfg):
        from growth.models import WalletTransaction
        from django.utils import timezone as tz
        total_balance = User.objects.aggregate(b=Sum('wallet_balance'))['b'] or Decimal('0')
        users_with_wallet = User.objects.filter(wallet_balance__gt=0).count()
        now = tz.now()
        expiring_7d = WalletTransaction.objects.filter(
            type='credit', is_expired=False,
            expires_at__isnull=False,
            expires_at__lte=now + timedelta(days=7),
            expires_at__gt=now,
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        month_start = (now.date()).replace(day=1)
        redeemed = WalletTransaction.objects.filter(
            type='debit', created_at__date__gte=month_start
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        return {
            'cfg': cfg,
            'total_balance': total_balance,
            'users_with_wallet': users_with_wallet,
            'expiring_7d': expiring_7d,
            'redeemed_month': redeemed,
        }

    def get(self, request):
        cfg = _get_offer_config('wallet')
        return render(request, 'truff_admin/offer_wallet.html', self._ctx(cfg))

    def post(self, request):
        cfg = _get_offer_config('wallet')
        cfg.is_active = request.POST.get('is_active') == 'on'
        cfg.discount_percent = int(request.POST.get('cashback_percent') or '5')
        cfg.expiry_days = int(request.POST.get('expiry_days') or '30')
        cfg.min_order_value = Decimal(request.POST.get('min_payout') or '50') or None
        cfg.updated_by = request.user
        cfg.save()
        from .utils import log_admin_action
        log_admin_action(request, 'offer_updated', 'OfferConfig', cfg.pk, {'offer_type': 'wallet'})
        return redirect('/truff-admin/offers/wallet/?saved=1')


class OwnerQRCodesView(TruffAdminRequiredMixin, View):
    """Owner QR Codes — table of all owners with QR stats."""

    def get(self, request):
        owners = TurfOwner.objects.select_related('user').order_by('-qr_bookings')
        agg = owners.aggregate(
            total_scans=Sum('qr_scans'),
            total_installs=Sum('qr_installs'),
            total_rev=Sum('qr_earnings'),
        )
        return render(request, 'truff_admin/offer_qr_codes.html', {
            'owners': owners,
            'total_qr_scans': agg['total_scans'] or 0,
            'total_qr_installs': agg['total_installs'] or 0,
            'total_qr_revenue': agg['total_rev'] or Decimal('0'),
        })


class ExportOfferReportView(TruffAdminRequiredMixin, View):
    """Export OfferUsage as CSV."""

    def get(self, request):
        import csv
        from django.http import StreamingHttpResponse

        def rows():
            yield ['offer_type', 'user', 'reward_amount', 'booking_id', 'ip_address', 'created_at']
            for usage in OfferUsage.objects.select_related('user', 'booking').order_by('-created_at'):
                yield [
                    usage.offer_type,
                    usage.user.username,
                    str(usage.reward_amount),
                    usage.booking_id or '',
                    usage.ip_address or '',
                    usage.created_at.strftime('%Y-%m-%d %H:%M'),
                ]

        def stream():
            buf = []
            writer = csv.writer(__import__('io').StringIO())
            for row in rows():
                obj = __import__('io').StringIO()
                csv.writer(obj).writerow(row)
                yield obj.getvalue()

        response = StreamingHttpResponse(stream(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="offer_report.csv"'
        return response

