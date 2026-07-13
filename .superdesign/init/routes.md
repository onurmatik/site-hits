# Routes

- `/`: anonymous onboarding landing; authenticated superusers redirect to `/dashboard/all`.
- `/start/`: POST-only website validation; stores the normalized site in session and routes anonymous users through sign-in.
- `/onboarding/`: superuser-only website confirmation and tracked-site creation.
- `/onboarding/<site-slug>/`: superuser-only tracker installation step.
- `/accounts/login/`: Django login page.
- `/dashboard/all`: aggregate dashboard for all tracked sites.
- `/dashboard/<site-slug>`: dashboard for a single tracked site.
- `/widget/<public-key>/`: public, frameable aggregate widget for the last 60 minutes of one active tracked site.
- `/admin/`: Django administration for tracked sites and events.
