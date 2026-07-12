# Layouts

The anonymous entry flow has three layouts:
- `/`: compact 64px public header, centered conversion hero, URL form, and a wide dashboard preview.
- `/onboarding/`: centered confirmation panel capped at 576px.
- `/onboarding/<site-slug>/`: centered install panel capped at 672px.

The dashboard uses a single authenticated application shell:
- Full-width compact header.
- Centered content container capped at 1440px.
- Dashboard title and status metadata row.
- Filter row.
- Five-column KPI grid on wide screens, two columns on tablets, one on phones.
- Full-width chart.
- Two-column breakdown grid that collapses to one column.

No sidebar is required for the internal MVP.

All pages extend `templates/base.html`, which owns the document shell, favicon, and compiled stylesheet only.
