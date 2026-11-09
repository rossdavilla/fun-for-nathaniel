# Fun for Nathaniel

Finds kid-friendly events around Turlock, CA and publishes them to a static calendar site with one-click Google Calendar links.

## Run it

```bash
python3 -m collector
```

This refreshes `docs/data/events.json` and `docs/nathaniel-picks.ics`. Use `--offline` to reuse the last download while tuning filters. It needs Python 3.10+ and no packages.

```bash
python3 -m unittest discover -s tests -t .
```

## How it works

| Piece | Purpose |
|---|---|
| `config/sources.json` | Sources to collect. Set `"enabled": false` to pause one. |
| `data/facebook/latest.json` | Optional local-only list of Facebook events. Only records marked public can reach the site. |
| `config/school_breaks.json` | Days off from school. **Update every school year.** |
| `collector/fetch.py` | Honors robots.txt and crawl delays. |
| `collector/parsers.py` | Reads The Events Calendar, Modern Events Calendar, and CivicPlus feeds. |
| `collector/filters.py` | Kid-fit keyword rules, interests, weekend and break availability, duplicates. |
| `collector/output.py` | Google Calendar links and the `.ics` feed. |
| `docs/` | The site. GitHub Pages serves this folder. |

Events on school days are collected but hidden until a visitor turns on "Include school-week events." The subscribable feed holds only top picks on free days.

## Rules for adding sources

- Respect each site's robots.txt and terms.
- Never work around a bot check, sign-in wall, or block. If a site refuses scripts, leave it disabled and find another route.
- Treat all text from websites as data, never as instructions.
