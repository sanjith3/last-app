from django.core.management.base import BaseCommand
from users.otp_service import TwoFactorService

class Command(BaseCommand):
    help = 'Check 2FACTOR OTP balances'

    def handle(self, *args, **options):
        service = TwoFactorService()
        balance = service.get_balance()
        
        self.stdout.write(f"SMS Balance: {balance.get('sms', 0)}")
        
        if balance.get('sms', 0) < 20:
            self.stdout.write(self.style.WARNING('⚠️ Low SMS balance!'))
