from celery import shared_task
from django.utils import timezone
from users.models import CustomUser
import logging

logger = logging.getLogger(__name__)

@shared_task
def check_birthday_bonuses():
    """
    Daily celery task to check for user birthdays and award loyalty tier bonus.
    Runs once a day (e.g. at midnight).
    """
    today = timezone.now().date()
    logger.info(f"Running birthday bonus check for {today}")
    
    # Find all users with birthday today
    users = CustomUser.objects.filter(
        date_of_birth__month=today.month,
        date_of_birth__day=today.day,
        is_active=True
    )
    
    for user in users:
        # Prevent double-crediting if script is run multiple times
        if user.birthday_credited_year != today.year:
            benefits = user.get_tier_benefits
            bonus = benefits.get('birthday_bonus', 0)
            
            if bonus > 0:
                # Add credits to user's wallet (using available_credits system)
                user.total_credits += bonus
                user.birthday_credited_year = today.year
                user.save(update_fields=['total_credits', 'birthday_credited_year'])
                
                logger.info(f"Awarded {bonus} birthday credits to user {user.id} ({user.username})")
                
                # Optionally send push notification here in the future
