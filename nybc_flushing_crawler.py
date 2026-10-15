#!/usr/bin/env python3
"""NYBC Flushing Badminton Court Availability Crawler.

Crawls New York Badminton Center (Flushing: 132-70 34th Ave, Flushing NY 11354)
for the category "Reservation by # of players (NYBC)" via Acuity Scheduling's
JSON endpoints, maps every open time slot to the exact available court(s),
and generates JSON, Markdown, and standalone bilingual (ZH/EN) HTML calendars.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import json
import os
import re
from typing import Any
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SCHEDULE_URL = (
    "https://nybcreservation.as.me/schedule/6efeecae"
    "?categories%5B%5D=Reservation+by+%23+of+players+%28NYBC%29"
)
CATEGORY_QUERY = "?categories%5B%5D=Reservation+by+%23+of+players+%28NYBC%29"
API_BASE = "https://nybcreservation.as.me/api/scheduling/v1"
OWNER_KEY = "6efeecae"
FLUSHING_LOCATION = "132-70 34th Ave. Flushing NY 11354"
TARGET_CATEGORY = "Reservation by # of players (NYBC)"
TIMEZONE = "America/New_York"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def http_get_text(url: str, timeout: int = 15) -> str:
  req = urllib.request.Request(
      url,
      headers={
          "User-Agent": USER_AGENT,
          "Accept": "application/json, text/html, */*",
      },
  )
  with urllib.request.urlopen(req, timeout=timeout) as resp:
    return resp.read().decode("utf-8", errors="replace")


def http_get_json(path: str, params: dict[str, Any]) -> Any:
  query = urllib.parse.urlencode(params)
  url = f"{API_BASE}/{path}?{query}"
  return json.loads(http_get_text(url))


def fetch_business_metadata() -> dict[str, Any]:
  """Extracts var BUSINESS = {...} from the live NYBC Acuity schedule page."""
  html = http_get_text(SCHEDULE_URL)
  match = re.search(r"var\s+BUSINESS\s*=\s*(\{.*?\});\s*\n", html)
  if not match:
    raise RuntimeError("Could not locate var BUSINESS metadata on page.")
  return json.loads(match.group(1))


def short_court_name(full_name: str) -> str:
  cleaned = full_name.strip()
  cleaned = re.sub(r"^NYBC\s*-\s*", "", cleaned)
  cleaned = re.sub(
      r"\(\s*training court\s*\)", "(Training)", cleaned, flags=re.I
  )
  cleaned = re.sub(r"\(\s*VIP court\s*\)", "(VIP)", cleaned, flags=re.I)
  return re.sub(r"\s+", " ", cleaned).strip()


def fetch_open_dates(
    appointment_type_id: int, months: list[str]
) -> list[str]:
  """Fetches all open dates (YYYY-MM-DD) across the given YYYY-MM-01 months."""
  open_dates: set[str] = set()
  for month_str in months:
    data = http_get_json(
        "availability/month",
        {
            "owner": OWNER_KEY,
            "appointmentTypeId": str(appointment_type_id),
            "calendarId": "any",
            "month": month_str,
            "timezone": TIMEZONE,
        },
    )
    for date_str, is_open in data.items():
      if is_open:
        open_dates.add(date_str)
  return sorted(open_dates)


def crawl_court_times(
    appointment_type_id: int,
    calendar_id: int,
    start_date: str,
    end_date: str,
) -> dict[str, list[str]]:
  """Crawls all available times for a single court between start_date and end_date."""
  results: dict[str, list[str]] = {}
  cur_date = start_date
  while cur_date <= end_date:
    data = http_get_json(
        "availability/times",
        {
            "owner": OWNER_KEY,
            "appointmentTypeId": str(appointment_type_id),
            "calendarId": str(calendar_id),
            "startDate": cur_date,
            "maxDays": "20",
            "timezone": TIMEZONE,
        },
    )
    if not data:
      break
    returned_dates = sorted(data.keys())
    for d in returned_dates:
      if d <= end_date:
        slots = [slot["time"] for slot in data.get(d, []) if "time" in slot]
        if slots:
          results[d] = slots
    last_date_str = returned_dates[-1]
    if last_date_str >= end_date:
      break
    next_dt = datetime.date.fromisoformat(last_date_str) + datetime.timedelta(
        days=1
    )
    cur_date = next_dt.isoformat()
  return results


def build_direct_booking_url(
    appointment_type_id: int, calendar_id: int | str, iso_time: str
) -> str:
  encoded_time = urllib.parse.quote(iso_time, safe="")
  return (
      f"https://nybcreservation.as.me/schedule/{OWNER_KEY}"
      f"/appointment/{appointment_type_id}"
      f"/calendar/{calendar_id}"
      f"/datetime/{encoded_time}"
      f"{CATEGORY_QUERY}"
  )


def format_12h(hhmm: str) -> str:
  h = int(hhmm[:2])
  m = int(hhmm[3:5])
  ampm = "AM" if h < 12 else "PM"
  h12 = h % 12 or 12
  return f"{h12}:{m:02d} {ampm}"


def crawl_all(player_counts: list[int], max_days: int) -> dict[str, Any]:
  business = fetch_business_metadata()
  flushing_calendars = business.get("calendars", {}).get(FLUSHING_LOCATION, [])
  flushing_calendars_sorted = sorted(
      flushing_calendars, key=lambda c: short_court_name(c["name"])
  )

  all_appt_types = business.get("appointmentTypes", {}).get(TARGET_CATEGORY, [])
  appt_by_players: dict[int, dict[str, Any]] = {}
  for appt in all_appt_types:
    m = re.search(r"(\d+)\s*ppl", appt.get("name", ""), flags=re.I)
    if m:
      appt_by_players[int(m.group(1))] = appt

  today = datetime.date.today()
  months = sorted({
      today.replace(day=1).isoformat(),
      (today.replace(day=28) + datetime.timedelta(days=10))
      .replace(day=1)
      .isoformat(),
      (today.replace(day=28) + datetime.timedelta(days=40))
      .replace(day=1)
      .isoformat(),
  })

  primary_players = player_counts[0]
  primary_appt = appt_by_players[primary_players]
  open_dates = fetch_open_dates(primary_appt["id"], months)
  if not open_dates:
    raise RuntimeError("No open dates returned by NYBC Acuity API.")

  start_date = open_dates[0]
  cutoff_date = (
      datetime.date.fromisoformat(start_date)
      + datetime.timedelta(days=max_days - 1)
  ).isoformat()
  target_dates = [d for d in open_dates if d <= cutoff_date]
  end_date = target_dates[-1]

  tasks = []
  for pcount in player_counts:
    if pcount not in appt_by_players:
      continue
    appt = appt_by_players[pcount]
    for cal in flushing_calendars_sorted:
      tasks.append((pcount, appt, cal))

  raw_court_data: dict[int, dict[int, dict[str, list[str]]]] = {
      p: {} for p in player_counts
  }
  with ThreadPoolExecutor(max_workers=14) as pool:
    future_map = {
        pool.submit(
            crawl_court_times,
            appt["id"],
            cal["id"],
            start_date,
            end_date,
        ): (pcount, cal["id"])
        for pcount, appt, cal in tasks
    }
    for fut in as_completed(future_map):
      pcount, cal_id = future_map[fut]
      raw_court_data[pcount][cal_id] = fut.result()

  courts_meta = [
      {
          "id": c["id"],
          "name": c["name"].strip(),
          "shortName": short_court_name(c["name"]),
          "isVip": "vip" in c["name"].lower(),
          "isTraining": "training" in c["name"].lower(),
      }
      for c in flushing_calendars_sorted
  ]

  by_players_output: dict[str, Any] = {}
  for pcount in player_counts:
    if pcount not in appt_by_players:
      continue
    appt = appt_by_players[pcount]
    aid = appt["id"]
    all_dates_for_p = sorted({
        d
        for cal_id, dmap in raw_court_data[pcount].items()
        for d in dmap.keys()
    })
    days_list = []
    for d in all_dates_for_p:
      dt_obj = datetime.date.fromisoformat(d)
      slot_map: dict[str, list[dict[str, Any]]] = {}
      for cal in courts_meta:
        cid = cal["id"]
        for iso_time in raw_court_data[pcount].get(cid, {}).get(d, []):
          hhmm = iso_time[11:16]
          slot_map.setdefault(hhmm, []).append({
              "courtId": cid,
              "courtName": cal["shortName"],
              "isVip": cal["isVip"],
              "isTraining": cal["isTraining"],
              "isoTime": iso_time,
              "bookingUrl": build_direct_booking_url(aid, cid, iso_time),
          })
      slots_sorted = []
      for hhmm in sorted(slot_map.keys()):
        courts_avail = slot_map[hhmm]
        hour_int = int(hhmm[:2])
        min_int = int(hhmm[3:5])
        start_minutes = hour_int * 60 + min_int
        end_minutes = start_minutes + int(appt["duration"])
        end_hhmm = f"{(end_minutes // 60) % 24:02d}:{end_minutes % 60:02d}"
        label_12h = format_12h(hhmm)
        end_label_12h = format_12h(end_hhmm)
        is_weekend = dt_obj.weekday() >= 5
        is_fri_sat_sun = dt_obj.weekday() in (4, 5, 6)
        is_prime_date_time = (
            is_fri_sat_sun
            and start_minutes >= 16 * 60
            and end_minutes <= 21 * 60
        )
        slots_sorted.append({
            "time24": hhmm,
            "endTime24": end_hhmm,
            "time12": label_12h,
            "endTime12": end_label_12h,
            "courtsCount": len(courts_avail),
            "courts": courts_avail,
            "anyCourtBookingUrl": build_direct_booking_url(
                aid, "any", courts_avail[0]["isoTime"]
            ),
            "isPrimeDateSlot": is_prime_date_time,
        })
      days_list.append({
          "date": d,
          "dayOfWeek": dt_obj.strftime("%A"),
          "shortDay": dt_obj.strftime("%a"),
          "monthDay": dt_obj.strftime("%b %-d"),
          "isWeekend": dt_obj.weekday() >= 5,
          "totalSlots": len(slots_sorted),
          "primeDateSlotsCount": sum(
              1 for s in slots_sorted if s["isPrimeDateSlot"]
          ),
          "slots": slots_sorted,
      })

    by_players_output[str(pcount)] = {
        "players": pcount,
        "appointmentTypeId": aid,
        "name": appt["name"],
        "durationMinutes": appt["duration"],
        "priceUsd": appt["price"],
        "description": appt["description"],
        "categoryUrl": (
            f"https://nybcreservation.as.me/schedule/{OWNER_KEY}"
            f"/appointment/{aid}/calendar/any{CATEGORY_QUERY}"
        ),
        "days": days_list,
    }

  return {
      "crawledAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
      "facility": "New York Badminton Center (Flushing)",
      "location": FLUSHING_LOCATION,
      "category": TARGET_CATEGORY,
      "scheduleUrl": SCHEDULE_URL,
      "timezone": TIMEZONE,
      "courts": courts_meta,
      "allPlayerOptions": [
          {
              "players": p,
              "appointmentTypeId": a["id"],
              "name": a["name"],
              "durationMinutes": a["duration"],
              "priceUsd": a["price"],
          }
          for p, a in sorted(appt_by_players.items())
      ],
      "availabilityByPlayers": by_players_output,
  }


def to_compact_payload(data: dict[str, Any]) -> dict[str, Any]:
  courts = data["courts"]
  cid_to_idx = {c["id"]: i for i, c in enumerate(courts)}
  compact: dict[str, Any] = {
      "crawledAt": data["crawledAt"],
      "location": data["location"],
      "category": data["category"],
      "scheduleUrl": data["scheduleUrl"],
      "ownerKey": OWNER_KEY,
      "courts": courts,
      "plans": {},
  }
  for pkey, pval in data["availabilityByPlayers"].items():
    days_compact = []
    for day in pval["days"]:
      slots_c = []
      for s in day["slots"]:
        c_idxs = [cid_to_idx[c["courtId"]] for c in s["courts"]]
        slots_c.append([s["time24"], c_idxs])
      tz_offset = (
          day["slots"][0]["courts"][0]["isoTime"][-5:]
          if day["slots"]
          else "-0400"
      )
      days_compact.append({
          "d": day["date"],
          "dow": day["shortDay"],
          "md": day["monthDay"],
          "wk": 1 if day["isWeekend"] else 0,
          "tz": tz_offset,
          "s": slots_c,
      })
    compact["plans"][pkey] = {
        "players": pval["players"],
        "aid": pval["appointmentTypeId"],
        "name": pval["name"],
        "dur": pval["durationMinutes"],
        "price": pval["priceUsd"],
        "days": days_compact,
    }
  return compact


def generate_markdown_calendar(data: dict[str, Any]) -> str:
  try:
    dt_utc = datetime.datetime.fromisoformat(
        data["crawledAt"].replace("Z", "+00:00")
    )
    est_str = dt_utc.astimezone(ZoneInfo("America/New_York")).strftime(
        "%Y-%m-%d %H:%M EST"
    )
  except Exception:
    est_str = f"{data['crawledAt'][:16].replace('T', ' ')} EST"
  lines = [
      "# NYBC 法拉盛羽毛球馆空场日历 / Flushing Badminton Availability Calendar",
      "",
      f"- **地址 / Location**: `{data['location']}`",
      f"- **预约类别 / Category**: `{data['category']}`",
      f"- **官方预约链接 / Booking Page**: [NYBC Flushing Reservation]({data['scheduleUrl']})",
      f"- **优先时段定义 / Prime Time**: `周五、周六、周日 16:00 (开始) – 21:00 (结束) / Fri, Sat & Sun 4:00 PM (start) – 9:00 PM (end)`",
      f"- **更新时间 / Last Crawled**: `{est_str}`",
      "",
  ]
  p2 = data["availabilityByPlayers"].get("2")
  if p2:
    lines.extend([
        f"## 2人预约 / 2 Players (`{p2['name']}` — {p2['durationMinutes']} min, \\${p2['priceUsd']})",
        "",
        "| 日期 / Date | 星期 / Day | 优先时段 (周五至周日 16:00–21:00) / Prime Slots | 全部可订时段及场地 / All Available Start Times |",
        "| :--- | :--- | :--- | :--- |",
    ])
    for day in p2["days"]:
      prime_badges = []
      all_badges = []
      for s in day["slots"]:
        court_names = ", ".join(c["courtName"] for c in s["courts"])
        entry = f"[**{s['time12']}**]({s['courts'][0]['bookingUrl']}) ({court_names})"
        all_badges.append(entry)
        if s["isPrimeDateSlot"]:
          prime_badges.append(f"**{s['time12']}–{s['endTime12']}** ({s['courtsCount']} ct)")
      prime_str = ", ".join(prime_badges) if prime_badges else "—"
      all_str = " • ".join(all_badges) if all_badges else "None"
      wknd_marker = " *" if day["shortDay"] in ("Fri", "Sat", "Sun") else ""
      lines.append(
          f"| **{day['monthDay']}** (`{day['date']}`) | {day['shortDay']}{wknd_marker} | {prime_str} | {all_str} |"
      )
    lines.append("")
  return "\n".join(lines)


def generate_html_calendar(data: dict[str, Any], use_jetski_cdn: bool = False) -> str:
  """Generates a clean, editorial HTML calendar (White theme default + ZH/EN i18n)."""
  compact_json = json.dumps(
      to_compact_payload(data), separators=(",", ":")
  ).replace("</", "<\\/")
  tailwind_src = (
      "https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"
      if use_jetski_cdn
      else "https://cdn.tailwindcss.com"
  )
  style_block = (
      ""
      if use_jetski_cdn
      else """
  <style>
    :root {
      --background: #fafaf9;
      --foreground: #111827;
      --card: #ffffff;
      --border: #e5e7eb;
      --muted-foreground: #6b7280;
      --primary: #1b4332;
      --primary-foreground: #ffffff;
      --secondary: #f3f4f6;
      --secondary-foreground: #1f2937;
    }
    html.dark {
      --background: #0c0d0e;
      --foreground: #f3f4f6;
      --card: #141618;
      --border: #26292e;
      --muted-foreground: #9ca3af;
      --primary: #2d6a4f;
      --primary-foreground: #ffffff;
      --secondary: #1f2226;
      --secondary-foreground: #e5e7eb;
    }
  </style>"""
  )
  return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NYBC 法拉盛羽毛球馆 · 空场预约日历 | Court Availability</title>
  <script src="{tailwind_src}"></script>{style_block}
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased transition-colors">
  <div class="max-w-6xl mx-auto px-4 sm:px-6 py-6 md:py-8 space-y-6">
    <!-- Top Masthead -->
    <header class="border-b border-[var(--border)] pb-5 flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
      <div class="space-y-1.5">
        <div class="flex items-center gap-2 text-xs text-[var(--muted-foreground)] tracking-wide">
          <span id="header-category" class="font-medium text-[var(--foreground)]"></span>
          <span>·</span>
          <span id="header-location"></span>
        </div>
        <h1 id="page-title" class="text-xl sm:text-2xl font-semibold tracking-tight text-[var(--foreground)]"></h1>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted-foreground)] tabular-nums">
          <span id="subtitle-meta"></span>
          <span id="refresh-status" class="text-emerald-700 dark:text-emerald-400 font-medium hidden"></span>
          <a id="actions-trigger-link" href="https://github.com/Harrypotterrrr/happy-badminton/actions/workflows/update_calendar.yml" target="_blank" rel="noopener noreferrer" class="underline hover:text-[var(--foreground)] transition hidden"></a>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <a id="homepage-link" href="https://harrypotterrrr.github.io" target="_blank" rel="noopener noreferrer"
           class="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--card)] text-[var(--foreground)] border border-[var(--border)] hover:bg-[var(--secondary)] transition">
        </a>
        <button id="refresh-btn" type="button"
           class="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--card)] text-[var(--foreground)] border border-[var(--border)] hover:bg-[var(--secondary)] transition cursor-pointer inline-flex items-center gap-1.5">
          <span id="refresh-btn-text"></span>
        </button>
        <button id="lang-toggle" type="button"
           class="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--card)] text-[var(--foreground)] border border-[var(--border)] hover:bg-[var(--secondary)] transition cursor-pointer">
        </button>
        <button id="theme-toggle" type="button"
           class="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--card)] text-[var(--foreground)] border border-[var(--border)] hover:bg-[var(--secondary)] transition cursor-pointer">
        </button>
        <a id="main-schedule-link" target="_blank" rel="noopener noreferrer"
           class="px-3.5 py-1.5 rounded-md text-xs font-medium bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition">
        </a>
      </div>
    </header>

    <!-- Compact Filter Toolbar -->
    <section class="bg-[var(--card)] border border-[var(--border)] rounded-lg p-3.5 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3.5">
      <div class="flex flex-col sm:flex-row sm:items-center gap-2">
        <span id="label-step-1" class="text-xs font-medium text-[var(--muted-foreground)] shrink-0"></span>
        <div id="player-tabs" class="inline-flex flex-wrap gap-1"></div>
      </div>

      <div class="flex flex-col sm:flex-row sm:items-center gap-2">
        <span id="label-step-2" class="text-xs font-medium text-[var(--muted-foreground)] shrink-0"></span>
        <div id="time-filter-tabs" class="inline-flex flex-wrap gap-1"></div>
      </div>

      <div class="flex items-center gap-2">
        <label id="label-step-3" for="court-select" class="text-xs font-medium text-[var(--muted-foreground)] shrink-0"></label>
        <select id="court-select" class="px-2.5 py-1.5 rounded-md text-xs bg-[var(--background)] text-[var(--foreground)] border border-[var(--border)] focus:outline-none">
        </select>
      </div>
    </section>

    <!-- Main Schedule Workspace: Calendar Matrix + Selected Day Sheet -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
      <!-- Left: 5-Week Calendar Grid -->
      <section class="lg:col-span-7 bg-[var(--card)] border border-[var(--border)] rounded-lg p-3 sm:p-4 space-y-3">
        <div class="flex items-center justify-between flex-wrap gap-2 pb-2 border-b border-[var(--border)]">
          <div class="flex items-center gap-2">
            <h2 id="calendar-heading" class="text-sm font-semibold text-[var(--foreground)]"></h2>
            <button id="cal-view-toggle" type="button"
               class="px-2 py-0.5 rounded text-[11px] font-medium border border-[var(--border)] bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:opacity-80 transition cursor-pointer">
            </button>
          </div>
          <div class="flex items-center gap-4 text-xs text-[var(--muted-foreground)]">
            <span class="inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-amber-500 inline-block"></span><span id="legend-prime"></span></span>
            <span class="inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-600 inline-block"></span><span id="legend-regular"></span></span>
          </div>
        </div>
        <div id="weekday-header" class="grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-[var(--muted-foreground)] py-1">
        </div>
        <div id="calendar-grid" class="grid grid-cols-7 gap-1"></div>
      </section>

      <!-- Right: Selected Day Time & Court Breakdown -->
      <section id="selected-date-section" class="lg:col-span-5 bg-[var(--card)] border border-[var(--border)] rounded-lg p-4 space-y-3 scroll-mt-4">
        <div class="border-b border-[var(--border)] pb-3 flex items-baseline justify-between gap-2">
          <div>
            <h2 id="selected-date-title" class="text-base font-semibold text-[var(--foreground)]"></h2>
            <p id="selected-date-subtitle" class="text-xs text-[var(--muted-foreground)] mt-0.5 tabular-nums"></p>
          </div>
          <span id="selected-date-badge" class="px-2 py-0.5 text-xs font-medium rounded bg-[var(--secondary)] text-[var(--secondary-foreground)] tabular-nums shrink-0"></span>
        </div>
        <div id="slots-list" class="divide-y divide-[var(--border)] max-h-[540px] overflow-y-auto pr-1"></div>
      </section>
    </div>

    <!-- Prime Slots Directory (Strictly Fri/Sat/Sun 4:00 PM – 9:00 PM, positioned under Calendar) -->
    <section class="bg-[var(--card)] border border-[var(--border)] rounded-lg p-4 space-y-3">
      <div class="flex items-baseline justify-between flex-wrap gap-2 border-b border-[var(--border)] pb-2.5">
        <div>
          <h2 id="prime-heading" class="text-sm font-semibold text-[var(--foreground)]"></h2>
          <p id="prime-desc" class="text-xs text-[var(--muted-foreground)] mt-0.5"></p>
        </div>
        <span id="prime-summary-badge" class="text-xs font-medium text-[var(--muted-foreground)] tabular-nums"></span>
      </div>
      <div id="prime-picks-row" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3"></div>
    </section>

    <!-- Footer -->
    <footer class="pt-2 border-t border-[var(--border)] flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
      <span id="footer-note"></span>
      <div class="flex items-center gap-4">
        <a id="footer-homepage" href="https://harrypotterrrr.github.io" target="_blank" rel="noopener noreferrer" class="hover:text-[var(--foreground)] underline transition"></a>
        <a id="footer-nybc" target="_blank" rel="noopener noreferrer" class="hover:text-[var(--foreground)] underline transition"></a>
      </div>
    </footer>
  </div>

  <script>
    let COMPACT_DATA = {compact_json};
    const CATEGORY_QUERY_PARAM = "?categories%5B%5D=Reservation+by+%23+of+players+%28NYBC%29";
    const RAW_JSON_URLS = [
      "https://raw.githubusercontent.com/Harrypotterrrr/happy-badminton/main/nybc_flushing_availability.json",
      "./nybc_flushing_availability.json"
    ];

    const LIGHT_VARS = {{
      "--background": "#fafaf9",
      "--foreground": "#111827",
      "--card": "#ffffff",
      "--border": "#e5e7eb",
      "--muted-foreground": "#6b7280",
      "--primary": "#1b4332",
      "--primary-foreground": "#ffffff",
      "--secondary": "#f3f4f6",
      "--secondary-foreground": "#1f2937"
    }};

    const DARK_VARS = {{
      "--background": "#0c0d0e",
      "--foreground": "#f3f4f6",
      "--card": "#141618",
      "--border": "#26292e",
      "--muted-foreground": "#9ca3af",
      "--primary": "#2d6a4f",
      "--primary-foreground": "#ffffff",
      "--secondary": "#1f2226",
      "--secondary-foreground": "#e5e7eb"
    }};

    const I18N = {{
      zh: {{
        locale: "zh-CN",
        docTitle: "NYBC 法拉盛羽毛球馆 · 空场预约日历",
        headerCategory: "按人数预约 (NYBC 法拉盛)",
        headerLocation: "132-70 34th Ave, Flushing NY 11354",
        pageTitle: "NYBC 法拉盛羽毛球场地空场表",
        homepageBtn: "个人主页 · Haolin ↗",
        refreshBtn: "刷新数据",
        refreshingBtn: "正在实时抓取...",
        refreshProgressMsg: "正在触发 GitHub 实时抓取 NYBC 最新空场 (~10秒)...",
        refreshSuccessPrefix: "已实时同步最新数据 (",
        refreshTriggerLink: "",
        langToggleBtn: "EN",
        themeLight: "浅色",
        themeDark: "深色",
        openMainBtn: "NYBC 官方预约 ↗",
        step1Label: "人数 / 时长",
        step2Label: "时段筛选",
        step3Label: "场地",
        courtFilterAll: "全部 7 个场地 (1–5号标准 / 6号VIP / 7号训练)",
        courtFilterStandard: "标准场地 (1–5号)",
        courtFilterVip: "6号场地 (VIP)",
        courtFilterTraining: "7号场地 (训练场)",
        primeHeading: "周末及周五优先时段空场汇总 (周五、周六、周日 16:00–21:00)",
        primeDesc: "仅筛选周五、周六、周日 16:00 (开始) 至 21:00 (结束) 的空场。点击日期卡片在上方查看，或点击时间直接跳转 NYBC 对应场地预约。",
        calendarHeading: "未来 5 周空场日历",
        calViewCards: "切换卡片视图",
        calViewGrid: "切换日历网格",
        legendPrime: "优先时段 (周五–周日 16–21点)",
        legendRegular: "常规可订时段",
        weekdays: ["一", "二", "三", "四", "五", "六", "日"],
        filterAll: "全部时段",
        filterPrime: "优先时段 (周五–日 16–21点)",
        filterWindow4to9: "16:00–21:00 (全部日期)",
        filterWeekend: "仅看周五/六/日",
        fridayTag: "周五",
        weekendTag: "周末",
        noPrimeMatch: "当前筛选条件下暂无周五、周六、周日 16:00–21:00 的空场，可尝试切换人数或场地类型。",
        noSlotMatch: "当天没有符合筛选条件的空场时段。",
        primeTimeBadge: "优先时段",
        zeroMatch: "无空场",
        zeroMatchShort: "0场",
        closedLabel: "—",
        footerNote: "每天早晚自动更新 2 次 (9:00 / 21:00 EST) · 点击「刷新数据」自动触发 GitHub 实时抓取并同步",
        courtNames: {{
          "Court 1": "1号场",
          "Court 2": "2号场",
          "Court 3": "3号场",
          "Court 4": "4号场",
          "Court 5": "5号场",
          "Court 6 (VIP)": "6号VIP",
          "Court 07 (Training)": "7号训练"
        }}
      }},
      en: {{
        locale: "en-US",
        docTitle: "NYBC Flushing Badminton — Court Availability",
        headerCategory: "Reservation by # of players (NYBC)",
        headerLocation: "132-70 34th Ave, Flushing NY 11354",
        pageTitle: "NYBC Flushing Court Availability",
        homepageBtn: "Haolin's Homepage ↗",
        refreshBtn: "Refresh",
        refreshingBtn: "Crawling live...",
        refreshProgressMsg: "Crawling live NYBC data via GitHub (~10s)...",
        refreshSuccessPrefix: "Synced live schedule (",
        refreshTriggerLink: "",
        langToggleBtn: "中文",
        themeLight: "Light",
        themeDark: "Dark",
        openMainBtn: "NYBC Booking ↗",
        step1Label: "Players",
        step2Label: "Time",
        step3Label: "Court",
        courtFilterAll: "All 7 Courts (1–5 Standard, 6 VIP, 07 Training)",
        courtFilterStandard: "Standard Courts (1–5)",
        courtFilterVip: "Court 6 (VIP)",
        courtFilterTraining: "Court 07 (Training)",
        primeHeading: "Upcoming Prime Slots (Fri, Sat & Sun · 4:00 PM – 9:00 PM)",
        primeDesc: "Strictly Friday, Saturday & Sunday slots starting >= 4:00 PM and ending <= 9:00 PM. Click a date to inspect above, or click a time chip to book that court directly.",
        calendarHeading: "5-Week Availability",
        calViewCards: "Card View",
        calViewGrid: "Grid View",
        legendPrime: "Prime (Fri–Sun 4–9 PM)",
        legendRegular: "Open Slots",
        weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        filterAll: "All Times",
        filterPrime: "Prime (Fri–Sun 4–9 PM)",
        filterWindow4to9: "4–9 PM (All Days)",
        filterWeekend: "Fri / Sat / Sun",
        fridayTag: "Fri",
        weekendTag: "Wknd",
        noPrimeMatch: "No Fri/Sat/Sun 4:00 PM – 9:00 PM prime slots match the current filter.",
        noSlotMatch: "No open slots on this date match your current filter.",
        primeTimeBadge: "Prime",
        zeroMatch: "0 open",
        zeroMatchShort: "0",
        closedLabel: "—",
        footerNote: "Auto-updated twice daily (9 AM & 9 PM EST) · Click Refresh to trigger live crawl & sync",
        courtNames: {{}}
      }}
    }};

    const planKeys = Object.keys(COMPACT_DATA.plans);
    let state = {{
      lang: "zh",
      isDark: false,
      players: planKeys[0] || "2",
      timeFilter: "all",
      courtFilter: "all",
      selectedDate: null,
      mobileCalCards: false,
      isRefreshing: false
    }};

    function applyTheme() {{
      const root = document.documentElement;
      root.classList.toggle("dark", state.isDark);
      const vars = state.isDark ? DARK_VARS : LIGHT_VARS;
      Object.keys(vars).forEach(k => root.style.setProperty(k, vars[k]));
    }}

    function t() {{
      return I18N[state.lang];
    }}

    function el(tag, className, text) {{
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    }}

    function formatCurrency(usdStr) {{
      const num = parseFloat(usdStr);
      return new Intl.NumberFormat(t().locale, {{
        style: "currency",
        currency: "USD"
      }}).format(num);
    }}

    function formatUpdatedEst(isoStr) {{
      try {{
        const dt = new Date(isoStr);
        if (isNaN(dt.getTime())) return isoStr.slice(0, 16).replace("T", " ") + " EST";
        const parts = new Intl.DateTimeFormat("en-CA", {{
          timeZone: "America/New_York",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false
        }}).formatToParts(dt);
        const get = type => (parts.find(p => p.type === type) || {{ value: "" }}).value;
        const hh = get("hour") === "24" ? "00" : get("hour");
        return get("year") + "-" + get("month") + "-" + get("day") + " " + hh + ":" + get("minute") + " EST";
      }} catch (_e) {{
        return isoStr.slice(0, 16).replace("T", " ") + " EST";
      }}
    }}

    function isMobileView() {{
      return window.innerWidth < 1024 || /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent);
    }}

    function scrollToSelectedDateOnMobile() {{
      if (!isMobileView()) return;
      const sec = document.getElementById("selected-date-section");
      if (sec) {{
        sec.scrollIntoView({{ behavior: "smooth", block: "start" }});
      }}
    }}

    function formatDateShort(isoDate) {{
      const dt = new Date(isoDate + "T12:00:00Z");
      return new Intl.DateTimeFormat(t().locale, {{
        month: "short",
        day: "numeric",
        weekday: "short",
        timeZone: "UTC"
      }}).format(dt);
    }}

    function formatDateFull(isoDate) {{
      const dt = new Date(isoDate + "T12:00:00Z");
      return new Intl.DateTimeFormat(t().locale, {{
        year: "numeric",
        month: "short",
        day: "numeric",
        weekday: "long",
        timeZone: "UTC"
      }}).format(dt);
    }}

    function formatMonthDayCell(dt, isFirstOrStart, compactNumeric) {{
      const dayNum = dt.getUTCDate();
      if (compactNumeric || state.lang === "zh") {{
        if (dayNum === 1 || isFirstOrStart) {{
          return (dt.getUTCMonth() + 1) + "/" + dayNum;
        }}
        return String(dayNum);
      }}
      const monthShort = new Intl.DateTimeFormat("en-US", {{
        month: "short",
        timeZone: "UTC"
      }}).format(dt);
      return (dayNum === 1 || isFirstOrStart) ? (monthShort + " " + dayNum) : String(dayNum);
    }}

    function formatTimeLabel(hhmm) {{
      const h = parseInt(hhmm.slice(0, 2), 10);
      const m = parseInt(hhmm.slice(3, 5), 10);
      const dt = new Date(Date.UTC(2026, 0, 1, h, m));
      return new Intl.DateTimeFormat(t().locale, {{
        hour: "numeric",
        minute: "2-digit",
        hour12: state.lang === "en",
        timeZone: "UTC"
      }}).format(dt);
    }}

    function computeEnd24h(hhmm, durMins) {{
      const h = parseInt(hhmm.slice(0, 2), 10);
      const m = parseInt(hhmm.slice(3, 5), 10);
      const total = h * 60 + m + durMins;
      const eh = Math.floor(total / 60) % 24;
      const em = total % 60;
      return String(eh).padStart(2, "0") + ":" + String(em).padStart(2, "0");
    }}

    function localizeCourtName(shortName) {{
      return t().courtNames[shortName] || shortName;
    }}

    function formatPlayerButtonLabel(pKey, plan) {{
      const priceFmt = formatCurrency(plan.price);
      if (state.lang === "zh") {{
        return pKey + "人 · " + plan.dur + "分 · " + priceFmt;
      }}
      return pKey + "P · " + plan.dur + "m · " + priceFmt;
    }}

    function formatPrimeSummary(totalPrimeSlots, daysCount) {{
      if (state.lang === "zh") {{
        return daysCount + " 个周五/六/日有空场 · 共 " + totalPrimeSlots + " 个优先时段";
      }}
      return totalPrimeSlots + " prime slots across " + daysCount + " Fri/Sat/Sun dates";
    }}

    function formatCourtsCountBadge(count) {{
      if (state.lang === "zh") {{
        return count + "场";
      }}
      return count + "c";
    }}

    function formatSlotsCountLabel(count) {{
      if (state.lang === "zh") {{
        return count + " 场";
      }}
      return count + (count === 1 ? " slot" : " slots");
    }}

    function formatSlotsCountCompact(count) {{
      if (state.lang === "zh") {{
        return count + "场";
      }}
      return count + " open";
    }}

    function formatPrimeCountLabel(count) {{
      if (state.lang === "zh") {{
        return count + " 优先";
      }}
      return count + " prime";
    }}

    function formatPrimeCountCompact(count) {{
      return "★" + count;
    }}

    function formatCourtsOpenLabel(count) {{
      if (state.lang === "zh") {{
        return count + " 场可订";
      }}
      return count + (count === 1 ? " court" : " courts");
    }}

    function formatBookCourtButton(shortName) {{
      const cname = localizeCourtName(shortName);
      return cname + " ↗";
    }}

    function buildBookingUrl(aid, cid, dateStr, hhmm, tz) {{
      const iso = dateStr + "T" + hhmm + ":00" + tz;
      return "https://nybcreservation.as.me/schedule/" + COMPACT_DATA.ownerKey +
        "/appointment/" + aid + "/calendar/" + cid + "/datetime/" + encodeURIComponent(iso) +
        CATEGORY_QUERY_PARAM;
    }}

    function buildCategoryPlanUrl(aid) {{
      return "https://nybcreservation.as.me/schedule/" + COMPACT_DATA.ownerKey +
        "/appointment/" + aid + "/calendar/any" + CATEGORY_QUERY_PARAM;
    }}

    function convertRawJsonToCompact(rawData) {{
      const courts = rawData.courts || [];
      const cidToIdx = {{}};
      courts.forEach((c, idx) => {{ cidToIdx[c.id] = idx; }});
      const compact = {{
        crawledAt: rawData.crawledAt,
        location: rawData.location,
        category: rawData.category,
        scheduleUrl: rawData.scheduleUrl,
        ownerKey: COMPACT_DATA.ownerKey || "6efeecae",
        courts: courts,
        plans: {{}}
      }};
      const byPlayers = rawData.availabilityByPlayers || {{}};
      Object.keys(byPlayers).forEach(pkey => {{
        const pval = byPlayers[pkey];
        const daysCompact = (pval.days || []).map(day => {{
          const slotsC = (day.slots || []).map(s => {{
            const cIdxs = (s.courts || []).map(c => cidToIdx[c.courtId]).filter(i => i !== undefined);
            return [s.time24, cIdxs];
          }});
          const firstIso = day.slots && day.slots[0] && day.slots[0].courts && day.slots[0].courts[0]
            ? day.slots[0].courts[0].isoTime
            : "-0400";
          return {{
            d: day.date,
            dow: day.shortDay,
            md: day.monthDay,
            wk: day.isWeekend ? 1 : 0,
            tz: firstIso.slice(-5),
            s: slotsC
          }};
        }});
        compact.plans[pkey] = {{
          players: pval.players,
          aid: pval.appointmentTypeId,
          name: pval.name,
          dur: pval.durationMinutes,
          price: pval.priceUsd,
          days: daysCompact
        }};
      }});
      return compact;
    }}

    const NTFY_REFRESH_URL = "https://ntfy.sh/nybc-flushing-refresh-7f8a9b2c";

    function getNyHour() {{
      try {{
        const hStr = new Intl.DateTimeFormat("en-US", {{
          timeZone: "America/New_York",
          hour: "2-digit",
          hour12: false
        }}).format(new Date());
        const h = parseInt(hStr, 10);
        return h === 24 ? 0 : h;
      }} catch (_e) {{
        return 12;
      }}
    }}

    function isNyActiveWindow() {{
      const h = getNyHour();
      return h >= 9 && h < 23;
    }}

    async function getCandidateJsonUrls() {{
      const urls = [];
      try {{
        const apiResp = await fetch(
          "https://api.github.com/repos/Harrypotterrrr/happy-badminton/commits/main?_=" + Date.now(),
          {{ cache: "no-store", headers: {{ Accept: "application/vnd.github.v3+json" }} }}
        );
        if (apiResp.ok) {{
          const commitData = await apiResp.json();
          if (commitData && commitData.sha) {{
            urls.push(
              "https://raw.githubusercontent.com/Harrypotterrrr/happy-badminton/" +
                commitData.sha +
                "/nybc_flushing_availability.json"
            );
          }}
        }}
      }} catch (_err) {{
        // Ignore GitHub API error and fall back to standard URLs
      }}
      RAW_JSON_URLS.forEach(u => {{
        const sep = u.includes("?") ? "&" : "?";
        urls.push(u + sep + "_=" + Date.now());
      }});
      return urls;
    }}

    async function pullLatestFromGitHub() {{
      const candidateUrls = await getCandidateJsonUrls();
      let bestRawJson = null;
      let bestTime = new Date(COMPACT_DATA.crawledAt || 0).getTime() || 0;

      for (const fetchUrl of candidateUrls) {{
        try {{
          const resp = await fetch(fetchUrl, {{ cache: "no-store" }});
          if (!resp.ok) continue;
          const rawJson = await resp.json();
          if (rawJson && rawJson.availabilityByPlayers) {{
            const rawTime = new Date(rawJson.crawledAt || 0).getTime() || 0;
            if (rawTime >= bestTime || !bestRawJson) {{
              if (rawTime >= bestTime) {{
                bestRawJson = rawJson;
                bestTime = rawTime;
              }}
              if (fetchUrl.includes("raw.githubusercontent.com/Harrypotterrrr/happy-badminton/") && !fetchUrl.includes("/main/")) {{
                break;
              }}
            }}
          }}
        }} catch (_err) {{
          // Try next fallback URL
        }}
      }}

      if (bestRawJson) {{
        COMPACT_DATA = convertRawJsonToCompact(bestRawJson);
      }}
      return new Date(COMPACT_DATA.crawledAt || 0).getTime() || 0;
    }}

    async function refreshScheduleData(silent = false) {{
      if (state.isRefreshing) return;
      const statusEl = document.getElementById("refresh-status");
      const triggerEl = document.getElementById("actions-trigger-link");
      triggerEl.classList.add("hidden");

      if (silent) {{
        const latestTime = await pullLatestFromGitHub();
        renderAll();
        // If current time is between 9 AM and 11 PM EST and data is >= 12 hours old (twice a day), auto-trigger live crawl
        if (isNyActiveWindow() && Date.now() - latestTime >= 12 * 3600 * 1000) {{
          refreshScheduleData(false);
        }}
        return;
      }}

      state.isRefreshing = true;
      renderStaticText();
      const dict = t();
      statusEl.textContent = dict.refreshProgressMsg;
      statusEl.classList.remove("hidden");

      const beforeTime = new Date(COMPACT_DATA.crawledAt || 0).getTime() || 0;

      // Trigger live NYBC crawl & GitHub update automatically
      try {{
        fetch(NTFY_REFRESH_URL, {{
          method: "POST",
          body: "refresh:" + Date.now(),
          mode: "cors"
        }}).catch(() => {{}});
      }} catch (_e) {{}}

      // Poll GitHub commits for the newly crawled availability JSON (takes ~8-15s)
      for (let attempt = 0; attempt < 10; attempt++) {{
        await new Promise(r => setTimeout(r, 2500));
        const latestTime = await pullLatestFromGitHub();
        if (latestTime > beforeTime || (attempt >= 1 && Date.now() - latestTime < 45000)) {{
          break;
        }}
      }}

      state.isRefreshing = false;
      renderAll();

      const updatedEst = formatUpdatedEst(COMPACT_DATA.crawledAt);
      statusEl.textContent = t().refreshSuccessPrefix + updatedEst + ")";
      statusEl.classList.remove("hidden");
    }}

    function filterCourt(courtObj) {{
      if (state.courtFilter === "all") return true;
      if (state.courtFilter === "vip") return Boolean(courtObj.isVip);
      if (state.courtFilter === "training") return Boolean(courtObj.isTraining);
      if (state.courtFilter === "standard") return !courtObj.isVip && !courtObj.isTraining;
      return true;
    }}

    function getHydratedDays() {{
      const plan = COMPACT_DATA.plans[state.players];
      if (!plan) return [];
      return plan.days.map(d => {{
        const isWeekend = Boolean(d.wk);
        const isFriSatSun = d.dow === "Fri" || d.dow === "Sat" || d.dow === "Sun";
        const filteredSlots = [];
        d.s.forEach(pair => {{
          const hhmm = pair[0];
          const courtIdxs = pair[1];
          const startMins = parseInt(hhmm.slice(0, 2), 10) * 60 + parseInt(hhmm.slice(3, 5), 10);
          const endMins = startMins + plan.dur;
          // Prime time: 4:00 PM (start) ~ 9:00 PM (end) on Friday, Saturday, and Sunday
          const isWindow4to9 = startMins >= 16 * 60 && endMins <= 21 * 60;
          const isPrime = isFriSatSun && isWindow4to9;

          if (state.timeFilter === "prime" && !isPrime) return;
          if (state.timeFilter === "weekend" && !isFriSatSun) return;
          if (state.timeFilter === "window4to9" && !isWindow4to9) return;

          const courts = courtIdxs
            .map(idx => COMPACT_DATA.courts[idx])
            .filter(filterCourt)
            .map(c => ({{
              courtId: c.id,
              courtName: c.shortName,
              isVip: c.isVip,
              isTraining: c.isTraining,
              bookingUrl: buildBookingUrl(plan.aid, c.id, d.d, hhmm, d.tz)
            }}));

          if (courts.length === 0) return;
          const end24 = computeEnd24h(hhmm, plan.dur);
          filteredSlots.push({{
            time24: hhmm,
            endTime24: end24,
            timeFormatted: formatTimeLabel(hhmm),
            endTimeFormatted: formatTimeLabel(end24),
            courtsCount: courts.length,
            courts,
            isPrimeDateSlot: isPrime
          }});
        }});

        return {{
          date: d.d,
          dow: d.dow,
          isWeekend,
          isFriSatSun,
          filteredSlots,
          filteredPrimeCount: filteredSlots.filter(s => s.isPrimeDateSlot).length
        }};
      }});
    }}

    function renderStaticText() {{
      const dict = t();
      document.documentElement.lang = dict.locale;
      document.title = dict.docTitle;
      document.getElementById("header-category").textContent = dict.headerCategory;
      document.getElementById("header-location").textContent = dict.headerLocation;
      document.getElementById("page-title").textContent = dict.pageTitle;
      document.getElementById("homepage-link").textContent = dict.homepageBtn;
      document.getElementById("refresh-btn-text").textContent = state.isRefreshing ? dict.refreshingBtn : dict.refreshBtn;
      document.getElementById("lang-toggle").textContent = dict.langToggleBtn;
      document.getElementById("theme-toggle").textContent = state.isDark ? dict.themeLight : dict.themeDark;

      const plan = COMPACT_DATA.plans[state.players];
      const mainLink = document.getElementById("main-schedule-link");
      mainLink.setAttribute("href", plan ? buildCategoryPlanUrl(plan.aid) : COMPACT_DATA.scheduleUrl);
      mainLink.textContent = dict.openMainBtn;

      const priceFmt = formatCurrency(plan.price);
      const updatedEst = formatUpdatedEst(COMPACT_DATA.crawledAt);
      const sub = document.getElementById("subtitle-meta");
      if (state.lang === "zh") {{
        sub.textContent = "当前方案：" + plan.players + "人预约 (" + plan.dur + "分钟 · " + priceFmt + ") · 更新于 " + updatedEst + " (每2小时自动同步)";
      }} else {{
        sub.textContent = "Active plan: " + plan.players + " Players (" + plan.dur + " min · " + priceFmt + ") · Updated " + updatedEst + " (2h auto-sync)";
      }}

      const triggerEl = document.getElementById("actions-trigger-link");
      if (!triggerEl.classList.contains("hidden")) {{
        triggerEl.textContent = dict.refreshTriggerLink;
      }}

      document.getElementById("label-step-1").textContent = dict.step1Label;
      document.getElementById("label-step-2").textContent = dict.step2Label;
      document.getElementById("label-step-3").textContent = dict.step3Label;

      const courtSelect = document.getElementById("court-select");
      const currentCourtVal = state.courtFilter;
      courtSelect.replaceChildren();
      [
        {{ val: "all", text: dict.courtFilterAll }},
        {{ val: "standard", text: dict.courtFilterStandard }},
        {{ val: "vip", text: dict.courtFilterVip }},
        {{ val: "training", text: dict.courtFilterTraining }}
      ].forEach(item => {{
        const opt = el("option", "", item.text);
        opt.value = item.val;
        if (item.val === currentCourtVal) opt.selected = true;
        courtSelect.appendChild(opt);
      }});

      document.getElementById("prime-heading").textContent = dict.primeHeading;
      document.getElementById("prime-desc").textContent = dict.primeDesc;
      document.getElementById("calendar-heading").textContent = dict.calendarHeading;
      document.getElementById("cal-view-toggle").textContent = state.mobileCalCards ? dict.calViewGrid : dict.calViewCards;
      document.getElementById("legend-prime").textContent = dict.legendPrime;
      document.getElementById("legend-regular").textContent = dict.legendRegular;

      document.getElementById("footer-note").textContent = dict.footerNote;
      document.getElementById("footer-homepage").textContent = dict.homepageBtn;
      const footerNybc = document.getElementById("footer-nybc");
      footerNybc.setAttribute("href", COMPACT_DATA.scheduleUrl);
      footerNybc.textContent = dict.openMainBtn;

      const wkHeader = document.getElementById("weekday-header");
      wkHeader.replaceChildren();
      dict.weekdays.forEach((wName, idx) => {{
        wkHeader.appendChild(el("div", idx >= 4 ? "text-[var(--foreground)] font-semibold" : "", wName));
      }});
    }}

    function renderControls() {{
      const playerTabs = document.getElementById("player-tabs");
      playerTabs.replaceChildren();
      Object.keys(COMPACT_DATA.plans).forEach(pKey => {{
        const plan = COMPACT_DATA.plans[pKey];
        const active = state.players === pKey;
        const btn = el(
          "button",
          active
            ? "px-2.5 py-1 rounded text-xs font-medium bg-[var(--primary)] text-[var(--primary-foreground)] transition cursor-pointer tabular-nums"
            : "px-2.5 py-1 rounded text-xs font-medium bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:opacity-80 transition cursor-pointer tabular-nums",
          formatPlayerButtonLabel(pKey, plan)
        );
        btn.addEventListener("click", () => {{
          state.players = pKey;
          renderAll();
        }});
        playerTabs.appendChild(btn);
      }});

      const dict = t();
      const timeTabs = document.getElementById("time-filter-tabs");
      timeTabs.replaceChildren();
      const timeOptions = [
        {{ id: "all", label: dict.filterAll }},
        {{ id: "prime", label: dict.filterPrime }},
        {{ id: "window4to9", label: dict.filterWindow4to9 }},
        {{ id: "weekend", label: dict.filterWeekend }}
      ];
      timeOptions.forEach(opt => {{
        const active = state.timeFilter === opt.id;
        const btn = el(
          "button",
          active
            ? "px-2.5 py-1 rounded text-xs font-medium bg-[var(--foreground)] text-[var(--background)] transition cursor-pointer"
            : "px-2.5 py-1 rounded text-xs font-medium bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:opacity-80 transition cursor-pointer",
          opt.label
        );
        btn.addEventListener("click", () => {{
          state.timeFilter = opt.id;
          renderAll();
        }});
        timeTabs.appendChild(btn);
      }});
    }}

    function renderPrimePicks(days) {{
      const container = document.getElementById("prime-picks-row");
      container.replaceChildren();
      const badge = document.getElementById("prime-summary-badge");
      const dict = t();

      // Strictly Friday, Saturday, and Sunday dates with 4:00 PM (start) ~ 9:00 PM (end) slots
      const daysWithPrime = days.filter(d => d.isFriSatSun && d.filteredPrimeCount > 0);
      const totalPrimeSlots = daysWithPrime.reduce((acc, d) => acc + d.filteredPrimeCount, 0);
      badge.textContent = formatPrimeSummary(totalPrimeSlots, daysWithPrime.length);

      if (daysWithPrime.length === 0) {{
        container.appendChild(el("p", "text-xs text-[var(--muted-foreground)] col-span-4 py-2", dict.noPrimeMatch));
        return;
      }}

      daysWithPrime.forEach(d => {{
        const isSelected = state.selectedDate === d.date;
        const card = el(
          "div",
          "p-3 rounded-md border " + (isSelected ? "border-[var(--foreground)] bg-[var(--secondary)]/40" : "border-[var(--border)] bg-[var(--background)]") + " hover:border-[var(--foreground)]/50 transition cursor-pointer flex flex-col justify-between gap-2.5"
        );
        card.addEventListener("click", () => {{
          state.selectedDate = d.date;
          renderAll();
          scrollToSelectedDateOnMobile();
        }});

        const topRow = el("div", "flex items-center justify-between gap-1");
        const title = el("span", "text-xs font-semibold text-[var(--foreground)]", formatDateShort(d.date));
        const tag = el(
          "span",
          "px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-500/15 text-amber-800 dark:text-amber-300",
          d.isWeekend ? dict.weekendTag : dict.fridayTag
        );
        topRow.appendChild(title);
        topRow.appendChild(tag);
        card.appendChild(topRow);

        const chipsWrap = el("div", "flex flex-wrap gap-1");
        const primeSlots = d.filteredSlots.filter(s => s.isPrimeDateSlot);
        primeSlots.forEach(s => {{
          const link = el(
            "a",
            "px-2 py-0.5 text-[11px] font-medium tabular-nums rounded border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] hover:border-[var(--primary)] hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)] transition",
            s.timeFormatted + "–" + s.endTimeFormatted + " (" + formatCourtsCountBadge(s.courtsCount) + ")"
          );
          link.setAttribute("href", s.courts[0].bookingUrl);
          link.setAttribute("target", "_blank");
          link.setAttribute("rel", "noopener noreferrer");
          link.addEventListener("click", e => e.stopPropagation());
          chipsWrap.appendChild(link);
        }});
        card.appendChild(chipsWrap);
        container.appendChild(card);
      }});
    }}

    function renderCalendarGrid(days) {{
      const grid = document.getElementById("calendar-grid");
      const wkHeader = document.getElementById("weekday-header");
      grid.replaceChildren();
      if (days.length === 0) return;
      const dict = t();

      // Card View mode (when toggled via #cal-view-toggle)
      if (state.mobileCalCards) {{
        wkHeader.classList.add("hidden");
        grid.className = "grid grid-cols-2 sm:grid-cols-3 gap-2";
        days.forEach(dayData => {{
          const iso = dayData.date;
          const isSelected = state.selectedDate === iso;
          const count = dayData.filteredSlots.length;
          const primeCount = dayData.filteredPrimeCount;

          let cardClasses = "p-2.5 rounded border min-h-[60px] flex flex-col justify-between text-left transition cursor-pointer ";
          if (isSelected) {{
            cardClasses += "border-[var(--foreground)] bg-[var(--secondary)]/70";
          }} else {{
            cardClasses += "border-[var(--border)] bg-[var(--background)] hover:border-[var(--foreground)]/40";
          }}

          const card = el("button", cardClasses);
          card.type = "button";
          card.addEventListener("click", () => {{
            state.selectedDate = iso;
            renderAll();
            scrollToSelectedDateOnMobile();
          }});

          const top = el("div", "flex items-center justify-between gap-1 w-full");
          top.appendChild(
            el(
              "span",
              "text-xs tabular-nums " + (isSelected || dayData.isFriSatSun ? "font-semibold text-[var(--foreground)]" : "font-medium text-[var(--muted-foreground)]"),
              formatDateShort(iso)
            )
          );
          if (primeCount > 0) {{
            top.appendChild(el("span", "w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0"));
          }} else if (count > 0) {{
            top.appendChild(el("span", "w-1.5 h-1.5 rounded-full bg-emerald-600 shrink-0"));
          }}
          card.appendChild(top);

          const bottom = el("div", "flex items-center justify-between gap-1 mt-1.5 tabular-nums");
          bottom.appendChild(
            el(
              "span",
              count > 0 ? "text-[11px] font-medium text-[var(--foreground)]" : "text-[10px] text-[var(--muted-foreground)]",
              count > 0 ? formatSlotsCountLabel(count) : dict.zeroMatch
            )
          );
          if (primeCount > 0) {{
            bottom.appendChild(el("span", "sm:hidden text-[10px] font-medium text-amber-700 dark:text-amber-400", formatPrimeCountCompact(primeCount)));
            bottom.appendChild(el("span", "hidden sm:inline text-[10px] font-medium text-amber-700 dark:text-amber-400", formatPrimeCountLabel(primeCount)));
          }}
          card.appendChild(bottom);
          grid.appendChild(card);
        }});
        return;
      }}

      // Standard 7-Column Grid View mode
      wkHeader.classList.remove("hidden");
      grid.className = "grid grid-cols-7 gap-1";

      const dayMap = {{}};
      days.forEach(d => {{ dayMap[d.date] = d; }});

      const firstDate = new Date(days[0].date + "T12:00:00Z");
      const firstDow = (firstDate.getUTCDay() + 6) % 7;
      const startDt = new Date(firstDate);
      startDt.setUTCDate(startDt.getUTCDate() - firstDow);

      const lastDate = new Date(days[days.length - 1].date + "T12:00:00Z");
      const lastDow = (lastDate.getUTCDay() + 6) % 7;
      const endDt = new Date(lastDate);
      endDt.setUTCDate(endDt.getUTCDate() + (6 - lastDow));

      for (let cur = new Date(startDt); cur <= endDt; cur.setUTCDate(cur.getUTCDate() + 1)) {{
        const iso = cur.toISOString().slice(0, 10);
        const dayData = dayMap[iso];
        const isFirstOrStart = iso === days[0].date;

        if (!dayData) {{
          const emptyCell = el("div", "px-1 py-1.5 sm:p-2 rounded border border-transparent opacity-35 min-h-[60px] sm:min-h-[68px] flex flex-col justify-between");
          emptyCell.appendChild(el("span", "text-[11px] sm:text-xs text-[var(--muted-foreground)] tabular-nums", formatMonthDayCell(cur, false, true)));
          emptyCell.appendChild(el("span", "text-[10px] text-[var(--muted-foreground)]", dict.closedLabel));
          grid.appendChild(emptyCell);
          continue;
        }}

        const isSelected = state.selectedDate === iso;
        const count = dayData.filteredSlots.length;
        const primeCount = dayData.filteredPrimeCount;

        let cellClasses = "px-1 py-1.5 sm:p-2 rounded border min-h-[60px] sm:min-h-[68px] flex flex-col justify-between text-left transition cursor-pointer overflow-hidden ";
        if (isSelected) {{
          cellClasses += "border-[var(--foreground)] bg-[var(--secondary)]/70";
        }} else {{
          cellClasses += "border-[var(--border)] bg-[var(--background)] hover:border-[var(--foreground)]/40";
        }}

        const cell = el("button", cellClasses);
        cell.type = "button";
        cell.addEventListener("click", () => {{
          state.selectedDate = iso;
          renderAll();
          scrollToSelectedDateOnMobile();
        }});

        const top = el("div", "flex items-center justify-between w-full gap-0.5");
        const dateTone = isSelected || dayData.isFriSatSun ? "font-semibold text-[var(--foreground)]" : "font-medium text-[var(--muted-foreground)]";
        top.appendChild(el("span", "sm:hidden text-[11px] tabular-nums leading-none whitespace-nowrap " + dateTone, formatMonthDayCell(cur, isFirstOrStart, true)));
        top.appendChild(el("span", "hidden sm:inline text-xs tabular-nums leading-none whitespace-nowrap " + dateTone, formatMonthDayCell(cur, isFirstOrStart, false)));
        if (primeCount > 0) {{
          top.appendChild(el("span", "w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0"));
        }} else if (count > 0) {{
          top.appendChild(el("span", "w-1.5 h-1.5 rounded-full bg-emerald-600 shrink-0"));
        }}
        cell.appendChild(top);

        const bottom = el("div", "space-y-0.5 mt-1 tabular-nums w-full");
        if (count === 0) {{
          bottom.appendChild(el("div", "sm:hidden text-[10px] text-[var(--muted-foreground)] leading-tight whitespace-nowrap", dict.zeroMatchShort));
          bottom.appendChild(el("div", "hidden sm:block text-[10px] text-[var(--muted-foreground)] leading-tight whitespace-nowrap", dict.zeroMatch));
        }} else {{
          // Mobile: compact "8场" + "★2" (no wrapping); Desktop: "8 场" + "2 优先"
          bottom.appendChild(el("div", "sm:hidden text-[10px] font-medium text-[var(--foreground)] leading-tight whitespace-nowrap", formatSlotsCountCompact(count)));
          bottom.appendChild(el("div", "hidden sm:block text-[11px] font-medium text-[var(--foreground)] leading-tight whitespace-nowrap", formatSlotsCountLabel(count)));
          if (primeCount > 0) {{
            bottom.appendChild(el("div", "sm:hidden text-[10px] font-medium text-amber-700 dark:text-amber-400 leading-tight whitespace-nowrap", formatPrimeCountCompact(primeCount)));
            bottom.appendChild(el("div", "hidden sm:block text-[10px] font-medium text-amber-700 dark:text-amber-400 leading-tight whitespace-nowrap", formatPrimeCountLabel(primeCount)));
          }}
        }}
        cell.appendChild(bottom);
        grid.appendChild(cell);
      }}
    }}

    function renderSelectedDateDetail(days) {{
      const titleEl = document.getElementById("selected-date-title");
      const subEl = document.getElementById("selected-date-subtitle");
      const badgeEl = document.getElementById("selected-date-badge");
      const listEl = document.getElementById("slots-list");
      listEl.replaceChildren();
      const dict = t();

      const plan = COMPACT_DATA.plans[state.players];
      const dayObj = days.find(d => d.date === state.selectedDate) || days[0];
      if (!dayObj) return;
      state.selectedDate = dayObj.date;

      const priceFmt = formatCurrency(plan.price);
      titleEl.textContent = formatDateFull(dayObj.date);
      if (state.lang === "zh") {{
        subEl.textContent = plan.players + "人预约 (NYBC) · " + plan.dur + " 分钟 · " + priceFmt;
      }} else {{
        subEl.textContent = plan.name + " · " + plan.dur + " mins · " + priceFmt;
      }}
      badgeEl.textContent = formatSlotsCountLabel(dayObj.filteredSlots.length);

      if (dayObj.filteredSlots.length === 0) {{
        listEl.appendChild(el("p", "text-xs text-[var(--muted-foreground)] py-8 text-center", dict.noSlotMatch));
        return;
      }}

      dayObj.filteredSlots.forEach(slot => {{
        const row = el("div", "py-3 first:pt-1 last:pb-1 space-y-2");

        const header = el("div", "flex items-center justify-between gap-2");
        const timeWrap = el("div", "flex items-center gap-2 flex-wrap");
        timeWrap.appendChild(el("span", "text-sm font-semibold tabular-nums text-[var(--foreground)]", slot.timeFormatted + " – " + slot.endTimeFormatted));
        if (slot.isPrimeDateSlot) {{
          timeWrap.appendChild(el("span", "px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-500/15 text-amber-800 dark:text-amber-300", dict.primeTimeBadge));
        }}
        header.appendChild(timeWrap);
        header.appendChild(el("span", "text-xs text-[var(--muted-foreground)] tabular-nums", formatCourtsOpenLabel(slot.courtsCount)));
        row.appendChild(header);

        const courtsWrap = el("div", "flex flex-wrap gap-1.5");
        slot.courts.forEach(c => {{
          const cBtn = el(
            "a",
            "px-2.5 py-1 rounded text-xs font-medium border border-[var(--border)] bg-[var(--background)] text-[var(--foreground)] hover:border-[var(--primary)] hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)] transition",
            formatBookCourtButton(c.courtName)
          );
          cBtn.setAttribute("href", c.bookingUrl);
          cBtn.setAttribute("target", "_blank");
          cBtn.setAttribute("rel", "noopener noreferrer");
          courtsWrap.appendChild(cBtn);
        }});
        row.appendChild(courtsWrap);
        listEl.appendChild(row);
      }});
    }}

    function renderAll() {{
      applyTheme();
      renderStaticText();
      renderControls();
      const days = getHydratedDays();
      if (!state.selectedDate && days.length > 0) {{
        const firstPrime = days.find(d => d.filteredPrimeCount > 0);
        state.selectedDate = firstPrime ? firstPrime.date : days[0].date;
      }}
      renderCalendarGrid(days);
      renderSelectedDateDetail(days);
      renderPrimePicks(days);
    }}

    document.getElementById("court-select").addEventListener("change", e => {{
      state.courtFilter = e.target.value;
      renderAll();
    }});

    document.getElementById("cal-view-toggle").addEventListener("click", () => {{
      state.mobileCalCards = !state.mobileCalCards;
      renderAll();
    }});

    document.getElementById("refresh-btn").addEventListener("click", () => {{
      refreshScheduleData(false);
    }});

    document.getElementById("lang-toggle").addEventListener("click", () => {{
      state.lang = state.lang === "zh" ? "en" : "zh";
      renderAll();
    }});

    document.getElementById("theme-toggle").addEventListener("click", () => {{
      state.isDark = !state.isDark;
      renderAll();
    }});

    renderAll();
    refreshScheduleData(true);
    setInterval(() => {{
      if (isNyActiveWindow()) {{
        refreshScheduleData(true);
      }}
    }}, 5 * 60 * 1000);
  </script>
</body>
</html>
"""


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Crawl NYBC Flushing badminton court availability."
  )
  parser.add_argument(
      "--players",
      type=int,
      nargs="+",
      default=[2, 3, 4],
      help="Player counts to crawl (default: 2 3 4). 2 = 45m, 3 = 60m, 4 = 90m.",
  )
  parser.add_argument(
      "--days",
      type=int,
      default=35,
      help="Maximum number of days ahead to crawl (default: 35).",
  )
  parser.add_argument(
      "--out-dir",
      type=str,
      default=".",
      help="Output directory for JSON, Markdown, and HTML files.",
  )
  args = parser.parse_args()

  out_dir = os.path.abspath(args.out_dir)
  os.makedirs(out_dir, exist_ok=True)

  print(
      f"Crawling NYBC Flushing ({FLUSHING_LOCATION}) for players={args.players}..."
  )
  data = crawl_all(args.players, args.days)

  json_path = os.path.join(out_dir, "nybc_flushing_availability.json")
  with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

  md_path = os.path.join(out_dir, "nybc_flushing_calendar.md")
  with open(md_path, "w", encoding="utf-8") as f:
    f.write(generate_markdown_calendar(data))

  html_content = generate_html_calendar(data, use_jetski_cdn=False)
  html_path = os.path.join(out_dir, "nybc_flushing_calendar.html")
  with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

  index_path = os.path.join(out_dir, "index.html")
  with open(index_path, "w", encoding="utf-8") as f:
    f.write(html_content)

  p2 = data["availabilityByPlayers"].get(str(args.players[0]))
  if p2:
    print(
        f"\n✅ Crawled {len(p2['days'])} open dates for {p2['name']} "
        f"({p2['durationMinutes']} min, ${p2['priceUsd']})."
    )
    print(f"   Saved JSON     -> {json_path}")
    print(f"   Saved Markdown -> {md_path}")
    print(f"   Saved HTML     -> {html_path} (& {index_path})\n")


if __name__ == "__main__":
  main()
