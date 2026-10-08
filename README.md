# 🏸 NYBC Flushing Badminton Availability Crawler & Calendar

Automated crawler and interactive availability calendar for **New York Badminton Center (Flushing)** (`132-70 34th Ave. Flushing NY 11354`) under the **Reservation by # of players (NYBC)** category.

- **Interactive HTML Calendar**: [`index.html`](./index.html) / [`nybc_flushing_calendar.html`](./nybc_flushing_calendar.html)
- **Markdown Availability Table**: [`nybc_flushing_calendar.md`](./nybc_flushing_calendar.md)
- **Structured JSON Dataset**: [`nybc_flushing_availability.json`](./nybc_flushing_availability.json)
- **Crawler Script**: [`nybc_flushing_crawler.py`](./nybc_flushing_crawler.py)

## Quick Start

Uses only Python 3 standard library (`urllib`, `concurrent.futures`, `json`):

```bash
python3 nybc_flushing_crawler.py --players 2 3 4 --days 35 --out-dir .
```

### Options
- `--players`: Space-separated list of player counts to crawl (default: `2 3 4`, corresponding to `45 min`, `60 min`, and `90 min` court reservations).
- `--days`: Number of days ahead to crawl (default: `35`).
- `--out-dir`: Output directory for generated `.html`, `.md`, and `.json` files.
