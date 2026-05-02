import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'turfspotx.settings')
django.setup()

from turfs.models import Turf, TurfStatus
from users.models import CustomUser

def sync_roles():
    print("Starting role sync...")
    approved_turfs = Turf.objects.filter(status=TurfStatus.APPROVED)
    count = 0
    for turf in approved_turfs:
        # Ensure role is updated
        if turf.owner.role == 'user':
            turf.owner.role = 'turf_owner'
            turf.owner.is_verified = True
            turf.owner.save()
            print(f"Updated user {turf.owner.username} to turf_owner")
            count += 1
            
    # Also ensure all users with turfs have 'isPartner' property logic-friendly state
    # Though 'isPartner' is a Flutter thing, we can trust 'turf_owner' role here.
    
    print(f"Sync complete. Updated {count} users.")

if __name__ == "__main__":
    sync_roles()
