# Antwerp studio watcher

Checks listing pages every 3 hours, keeps only matches up to your budget, pushes new ones to your phone
and keeps one dashboard page up to date. A weekly summary push goes out on Mondays.

## Set up (about 15 minutes)

1. **Phone:** install the free ntfy app (iOS or Android) and subscribe to a topic with a long random name,
   for example `antwerp-studio-k7q2x9m4`. Anyone who knows the name can read it, so keep it private.
2. **GitHub:** create a free account and a new **public** repository (needed for free GitHub Pages).
   Upload everything in this folder, including the hidden `.github` folder.
3. **Secret:** repository Settings > Secrets and variables > Actions > New repository secret.
   Name `NTFY_TOPIC`, value your topic name.
4. **Dashboard:** Settings > Pages > Deploy from a branch > `main` and folder `/docs`.
   Put the resulting address in `dashboard_url` in `config.json`.
5. **First run:** Actions tab > studio-watcher > Run workflow. The first run only records what is already
   listed (no pushes). After that, anything new triggers a push.

## Settings (config.json)

- `budget`: maximum price. Listings that show a higher price are dropped; listings with no visible price are kept.
- `keywords`: a link must contain one of these words (set `require_keywords` to false for a source that
  only lists relevant properties).
- `sources`: add one entry per page to watch. Use `include_pattern` (a regex on the link address) to keep
  only real listing links, for example `"include_pattern": "/aanbod/"`.
- `manual_links`: pages that can't be checked automatically, such as Facebook groups. They appear on the
  dashboard so everything is in one place.

## Good to know

- After the first run, open the dashboard and see what each source picked up. If it shows menu links,
  add an `include_pattern`.
- Sites that load listings with JavaScript, or that block bots, will show up under "Sources that failed"
  or return nothing. Those need a manual link instead.
- If the pushes stop, check the Actions tab for errors.
