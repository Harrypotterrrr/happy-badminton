#!/usr/bin/env python3
"""NYBC Flushing Badminton Court Availability Crawler.

Crawls New York Badminton Center (Flushing: 132-70 34th Ave, Flushing NY 11354)
for the category "Reservation by # of players (NYBC)" via Acuity Scheduling's
JSON endpoints, maps every open time slot to the exact available court(s),
and generates JSON, Markdown, and standalone interactive HTML calendars.
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

SCHEDULE_URL = (
    "https://nybcreservation.as.me/schedule/6efeecae"
    "?categories%5B%5D=Reservation+by+%23+of+players+%28NYBC%29"
)
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
        end_minutes = hour_int * 60 + min_int + int(appt["duration"])
        end_hhmm = f"{(end_minutes // 60) % 24:02d}:{end_minutes % 60:02d}"
        label_12h = format_12h(hhmm)
        end_label_12h = format_12h(end_hhmm)
        is_weekend = dt_obj.weekday() >= 5
        is_prime_date_time = (
            (10 <= hour_int <= 21) if is_weekend else (17 <= hour_int <= 21)
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
            f"/appointment/{aid}/calendar/any"
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
  lines = [
      "# 🏸 NYBC Flushing Badminton Availability Calendar (You & Your Crush)",
      "",
      f"- **Location**: `{data['location']}`",
      f"- **Category**: `{data['category']}`",
      f"- **Booking Page**: [NYBC Flushing Reservation]({data['scheduleUrl']})",
      f"- **Last Crawled**: `{data['crawledAt'][:19].replace('T', ' ')} UTC`",
      "",
  ]
  p2 = data["availabilityByPlayers"].get("2")
  if p2:
    lines.extend([
        f"## 💑 2 Players (`{p2['name']}` — {p2['durationMinutes']} min, \\${p2['priceUsd']})",
        "",
        "| Date | Day | Prime Date Slots (Eve / Wknd) | All Available Start Times (with Open Courts) |",
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
          prime_badges.append(f"**{s['time12']}** ({s['courtsCount']} ct)")
      prime_str = ", ".join(prime_badges) if prime_badges else "—"
      all_str = " • ".join(all_badges) if all_badges else "None"
      wknd_marker = " 🌟" if day["isWeekend"] else ""
      lines.append(
          f"| **{day['monthDay']}** (`{day['date']}`) | {day['shortDay']}{wknd_marker} | {prime_str} | {all_str} |"
      )
    lines.append("")
  return "\n".join(lines)


def generate_html_calendar(data: dict[str, Any]) -> str:
  """Generates a standalone interactive HTML calendar using safe DOM APIs."""
  compact_json = json.dumps(
      to_compact_payload(data), separators=(",", ":")
  ).replace("</", "<\\/")
  return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NYBC Flushing Badminton Availability — You & Your Crush</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {{
      --background: #f8fafc;
      --foreground: #0f172a;
      --card: #ffffff;
      --border: #e2e8f0;
      --muted-foreground: #64748b;
      --primary: #db2777;
      --primary-foreground: #ffffff;
      --secondary: #f1f5f9;
      --secondary-foreground: #1e293b;
    }}
    html.dark {{
      --background: #0b0f19;
      --foreground: #f1f5f9;
      --card: #111827;
      --border: #1e293b;
      --muted-foreground: #94a3b8;
      --primary: #ec4899;
      --primary-foreground: #ffffff;
      --secondary: #1e293b;
      --secondary-foreground: #e2e8f0;
    }}
  </style>
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased p-4 md:p-6 transition-colors">
  <div class="max-w-6xl mx-auto space-y-5">
    <!-- Header Banner -->
    <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl p-5 shadow-sm flex flex-col md:flex-row md:items-center md:justify-between gap-4">
      <div class="space-y-1">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-pink-500/15 text-pink-500 border border-pink-500/30">🏸 Badminton Date Planner</span>
          <span class="px-2.5 py-0.5 text-xs font-medium rounded-full bg-[var(--secondary)] text-[var(--secondary-foreground)]">📍 132-70 34th Ave, Flushing NY 11354</span>
        </div>
        <h1 class="text-xl md:text-2xl font-bold text-[var(--foreground)]">NYBC Flushing Court Availability Calendar</h1>
        <p class="text-sm text-[var(--muted-foreground)]" id="subtitle-meta"></p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button id="theme-toggle" class="px-3 py-2 rounded-xl text-xs font-semibold bg-[var(--secondary)] text-[var(--secondary-foreground)] border border-[var(--border)] hover:opacity-80 transition">
          🌗 Toggle Theme
        </button>
        <a id="main-schedule-link" target="_blank" rel="noopener noreferrer"
           class="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition shadow-sm">
          Open NYBC Booking Page ↗
        </a>
      </div>
    </div>

    <!-- Controls Bar -->
    <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl p-4 shadow-sm grid grid-cols-1 md:grid-cols-3 gap-4">
      <div>
        <label class="block text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2">1. Reservation Duration (# of Players)</label>
        <div id="player-tabs" class="flex flex-wrap gap-1.5"></div>
      </div>
      <div>
        <label class="block text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2">2. Time Slot Filter</label>
        <div id="time-filter-tabs" class="flex flex-wrap gap-1.5"></div>
      </div>
      <div>
        <label class="block text-xs font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2">3. Court Filter</label>
        <select id="court-select" class="w-full px-3 py-2 rounded-xl text-sm bg-[var(--background)] text-[var(--foreground)] border border-[var(--border)] focus:outline-none">
          <option value="all">All 7 Flushing Courts (Courts 1–5, 6 VIP, 07 Training)</option>
          <option value="standard">Standard Courts Only (Courts 1–5)</option>
          <option value="vip">Court 6 (VIP Court) Only</option>
          <option value="training">Court 07 (Training Court) Only</option>
        </select>
      </div>
    </div>

    <!-- Top Date Picks for You & Your Crush -->
    <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl p-4 shadow-sm space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 class="text-base font-semibold text-[var(--foreground)]">✨ Best Upcoming Date Slots (Weekday Evenings 5–10 PM & Weekend Daytime/Evenings)</h2>
          <p class="text-xs text-[var(--muted-foreground)]">Click any date card to inspect all courts below, or click a pink time badge to book that court directly.</p>
        </div>
        <span id="prime-summary-badge" class="text-xs font-medium px-2.5 py-1 rounded-lg bg-[var(--secondary)] text-[var(--secondary-foreground)]"></span>
      </div>
      <div id="prime-picks-row" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3"></div>
    </div>

    <!-- Main Split View: Calendar Grid + Selected Date Detail -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
      <!-- Left: 34-Day Calendar Grid -->
      <div class="lg:col-span-7 bg-[var(--card)] border border-[var(--border)] rounded-2xl p-4 shadow-sm space-y-3">
        <div class="flex items-center justify-between flex-wrap gap-2">
          <h2 class="text-base font-semibold text-[var(--foreground)]">📅 5-Week Availability Calendar</h2>
          <div class="flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
            <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-full bg-pink-500 inline-block"></span> Prime Date Slot</span>
            <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span> Daytime/Late Open</span>
          </div>
        </div>
        <div class="grid grid-cols-7 gap-1.5 text-center text-xs font-semibold text-[var(--muted-foreground)] pb-1 border-b border-[var(--border)]">
          <div>Mon</div><div>Tue</div><div>Wed</div><div>Thu</div><div>Fri</div><div class="text-pink-500">Sat</div><div class="text-pink-500">Sun</div>
        </div>
        <div id="calendar-grid" class="grid grid-cols-7 gap-1.5"></div>
      </div>

      <!-- Right: Selected Day Court & Time Breakdown -->
      <div class="lg:col-span-5 bg-[var(--card)] border border-[var(--border)] rounded-2xl p-4 shadow-sm space-y-4">
        <div class="border-b border-[var(--border)] pb-3 flex items-center justify-between gap-2">
          <div>
            <h2 id="selected-date-title" class="text-lg font-bold text-[var(--foreground)]">Select a Date</h2>
            <p id="selected-date-subtitle" class="text-xs text-[var(--muted-foreground)]"></p>
          </div>
          <span id="selected-date-badge" class="px-2.5 py-1 text-xs font-semibold rounded-full bg-[var(--secondary)] text-[var(--secondary-foreground)]"></span>
        </div>
        <div id="slots-list" class="space-y-2.5 max-h-[540px] overflow-y-auto pr-1"></div>
      </div>
    </div>
  </div>

  <script>
    const COMPACT_DATA = {compact_json};

    const planKeys = Object.keys(COMPACT_DATA.plans);
    let state = {{
      players: planKeys[0] || "2",
      timeFilter: "all",
      courtFilter: "all",
      selectedDate: null
    }};

    function el(tag, className, text) {{
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    }}

    function format12h(hhmm) {{
      const h = parseInt(hhmm.slice(0, 2), 10);
      const m = hhmm.slice(3, 5);
      const ampm = h < 12 ? "AM" : "PM";
      const h12 = h % 12 || 12;
      return h12 + ":" + m + " " + ampm;
    }}

    function computeEnd12h(hhmm, durMins) {{
      const h = parseInt(hhmm.slice(0, 2), 10);
      const m = parseInt(hhmm.slice(3, 5), 10);
      const total = h * 60 + m + durMins;
      const eh = Math.floor(total / 60) % 24;
      const em = total % 60;
      const end24 = String(eh).padStart(2, "0") + ":" + String(em).padStart(2, "0");
      return format12h(end24);
    }}

    function buildBookingUrl(aid, cid, dateStr, hhmm, tz) {{
      const iso = dateStr + "T" + hhmm + ":00" + tz;
      return "https://nybcreservation.as.me/schedule/" + COMPACT_DATA.ownerKey +
        "/appointment/" + aid + "/calendar/" + cid + "/datetime/" + encodeURIComponent(iso);
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
        const filteredSlots = [];
        d.s.forEach(pair => {{
          const hhmm = pair[0];
          const courtIdxs = pair[1];
          const hour = parseInt(hhmm.slice(0, 2), 10);
          const isPrime = isWeekend ? (hour >= 10 && hour <= 21) : (hour >= 17 && hour <= 21);

          if (state.timeFilter === "prime" && !isPrime) return;
          if (state.timeFilter === "weekend" && !isWeekend) return;
          if (state.timeFilter === "evening" && (hour < 17 || hour > 22)) return;

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
          filteredSlots.push({{
            time24: hhmm,
            time12: format12h(hhmm),
            endTime12: computeEnd12h(hhmm, plan.dur),
            courtsCount: courts.length,
            courts,
            isPrimeDateSlot: isPrime
          }});
        }});

        return {{
          date: d.d,
          shortDay: d.dow,
          monthDay: d.md,
          isWeekend,
          filteredSlots,
          filteredPrimeCount: filteredSlots.filter(s => s.isPrimeDateSlot).length
        }};
      }});
    }}

    function initHeader() {{
      const mainLink = document.getElementById("main-schedule-link");
      mainLink.setAttribute("href", COMPACT_DATA.scheduleUrl);
      const sub = document.getElementById("subtitle-meta");
      const plan = COMPACT_DATA.plans[state.players];
      sub.textContent = "Category: " + COMPACT_DATA.category + " • Active Option: " + plan.name + " (" + plan.dur + " min, $" + plan.price + ") • Crawled " + COMPACT_DATA.crawledAt.slice(0, 16).replace("T", " ") + " UTC";
    }}

    function renderControls() {{
      const playerTabs = document.getElementById("player-tabs");
      playerTabs.replaceChildren();
      const playerLabels = {{
        "2": "💑 2 Players (45m · $45.76)",
        "3": "🏸 3 Players (60m · $68.44)",
        "4": "🔥 4 Players (90m · $91.52)"
      }};
      Object.keys(COMPACT_DATA.plans).forEach(pKey => {{
        const plan = COMPACT_DATA.plans[pKey];
        const active = state.players === pKey;
        const btn = el(
          "button",
          active
            ? "px-3 py-1.5 rounded-xl text-xs font-semibold bg-[var(--primary)] text-[var(--primary-foreground)] shadow-sm transition"
            : "px-3 py-1.5 rounded-xl text-xs font-medium bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:opacity-80 transition",
          playerLabels[pKey] || (pKey + " Players (" + plan.dur + "m · $" + plan.price + ")")
        );
        btn.addEventListener("click", () => {{
          state.players = pKey;
          renderAll();
        }});
        playerTabs.appendChild(btn);
      }});

      const timeTabs = document.getElementById("time-filter-tabs");
      timeTabs.replaceChildren();
      const timeOptions = [
        {{ id: "all", label: "All Times" }},
        {{ id: "prime", label: "💖 Prime Date Slots" }},
        {{ id: "evening", label: "🌙 Evenings (5–10 PM)" }},
        {{ id: "weekend", label: "🌟 Weekends Only" }}
      ];
      timeOptions.forEach(opt => {{
        const active = state.timeFilter === opt.id;
        const btn = el(
          "button",
          active
            ? "px-3 py-1.5 rounded-xl text-xs font-semibold bg-pink-500 text-white shadow-sm transition"
            : "px-3 py-1.5 rounded-xl text-xs font-medium bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:opacity-80 transition",
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

      const daysWithPrime = days.filter(d => d.filteredPrimeCount > 0);
      const totalPrimeSlots = daysWithPrime.reduce((acc, d) => acc + d.filteredPrimeCount, 0);
      badge.textContent = totalPrimeSlots + " prime slots across " + daysWithPrime.length + " dates";

      const topDays = daysWithPrime.slice(0, 8);
      if (topDays.length === 0) {{
        container.appendChild(el("p", "text-xs text-[var(--muted-foreground)] col-span-4", "No prime slots match the current filter. Try switching to All Times or All Courts."));
        return;
      }}

      topDays.forEach(d => {{
        const isSelected = state.selectedDate === d.date;
        const card = el(
          "div",
          "p-3 rounded-xl border " + (isSelected ? "border-pink-500 ring-1 ring-pink-500/30" : "border-[var(--border)]") + " bg-[var(--background)] hover:border-pink-500/50 transition cursor-pointer flex flex-col justify-between gap-2"
        );
        card.addEventListener("click", () => {{
          state.selectedDate = d.date;
          renderAll();
        }});

        const topRow = el("div", "flex items-center justify-between gap-1");
        const title = el("span", "text-sm font-bold text-[var(--foreground)]", d.shortDay + ", " + d.monthDay);
        const tag = el(
          "span",
          d.isWeekend
            ? "px-2 py-0.5 text-[10px] font-semibold rounded-full bg-pink-500/15 text-pink-500"
            : "px-2 py-0.5 text-[10px] font-semibold rounded-full bg-[var(--secondary)] text-[var(--secondary-foreground)]",
          d.isWeekend ? "Weekend Date" : "Weekday Eve"
        );
        topRow.appendChild(title);
        topRow.appendChild(tag);
        card.appendChild(topRow);

        const chipsWrap = el("div", "flex flex-wrap gap-1");
        const primeSlots = d.filteredSlots.filter(s => s.isPrimeDateSlot).slice(0, 4);
        primeSlots.forEach(s => {{
          const link = el(
            "a",
            "px-2 py-0.5 text-[11px] font-semibold rounded-md bg-pink-500/15 text-pink-500 hover:bg-pink-500 hover:text-white transition",
            s.time12 + " (" + s.courtsCount + "c)"
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
      grid.replaceChildren();
      if (days.length === 0) return;

      const dayMap = {{}};
      days.forEach(d => {{ dayMap[d.date] = d; }});

      const firstDate = new Date(days[0].date + "T12:00:00Z");
      const firstDow = (firstDate.getUTCDay() + 6) % 7; // Monday = 0
      const startDt = new Date(firstDate);
      startDt.setUTCDate(startDt.getUTCDate() - firstDow);

      const lastDate = new Date(days[days.length - 1].date + "T12:00:00Z");
      const lastDow = (lastDate.getUTCDay() + 6) % 7;
      const endDt = new Date(lastDate);
      endDt.setUTCDate(endDt.getUTCDate() + (6 - lastDow));

      for (let cur = new Date(startDt); cur <= endDt; cur.setUTCDate(cur.getUTCDate() + 1)) {{
        const iso = cur.toISOString().slice(0, 10);
        const dayData = dayMap[iso];
        const dayNum = cur.getUTCDate();
        const monthShort = cur.toLocaleString("en-US", {{ month: "short", timeZone: "UTC" }});

        if (!dayData) {{
          const emptyCell = el("div", "p-2 rounded-xl border border-[var(--border)] opacity-35 min-h-[74px] flex flex-col justify-between bg-[var(--background)]");
          emptyCell.appendChild(el("span", "text-xs text-[var(--muted-foreground)]", (dayNum === 1 ? monthShort + " " : "") + dayNum));
          emptyCell.appendChild(el("span", "text-[10px] text-[var(--muted-foreground)]", "—"));
          grid.appendChild(emptyCell);
          continue;
        }}

        const isSelected = state.selectedDate === iso;
        const count = dayData.filteredSlots.length;
        const primeCount = dayData.filteredPrimeCount;

        let borderClass = "border-[var(--border)]";
        if (isSelected) borderClass = "border-2 border-pink-500 ring-2 ring-pink-500/20";
        else if (primeCount > 0) borderClass = "border-pink-500/40";

        const cell = el(
          "button",
          "p-2 rounded-xl border " + borderClass + " min-h-[74px] flex flex-col justify-between text-left transition hover:opacity-90 bg-[var(--background)]"
        );
        cell.addEventListener("click", () => {{
          state.selectedDate = iso;
          renderAll();
        }});

        const top = el("div", "flex items-center justify-between w-full");
        const dateLabel = el(
          "span",
          "text-xs font-bold " + (dayData.isWeekend ? "text-pink-500" : "text-[var(--foreground)]"),
          (dayNum === 1 || iso === days[0].date ? monthShort + " " : "") + dayNum
        );
        top.appendChild(dateLabel);
        if (primeCount > 0) {{
          top.appendChild(el("span", "w-2 h-2 rounded-full bg-pink-500"));
        }} else if (count > 0) {{
          top.appendChild(el("span", "w-2 h-2 rounded-full bg-emerald-500"));
        }}
        cell.appendChild(top);

        const bottom = el("div", "space-y-0.5 mt-1");
        if (count === 0) {{
          bottom.appendChild(el("div", "text-[10px] text-[var(--muted-foreground)]", "0 match"));
        }} else {{
          bottom.appendChild(el("div", "text-[11px] font-semibold text-[var(--foreground)]", count + (count === 1 ? " slot" : " slots")));
          if (primeCount > 0) {{
            bottom.appendChild(el("div", "text-[10px] font-medium text-pink-500", primeCount + " prime"));
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

      const plan = COMPACT_DATA.plans[state.players];
      const dayObj = days.find(d => d.date === state.selectedDate) || days[0];
      if (!dayObj) return;
      state.selectedDate = dayObj.date;

      titleEl.textContent = dayObj.shortDay + ", " + dayObj.monthDay + " (" + dayObj.date + ")";
      subEl.textContent = plan.name + " • " + plan.dur + " mins • $" + plan.price + " total";
      badgeEl.textContent = dayObj.filteredSlots.length + " available times";

      if (dayObj.filteredSlots.length === 0) {{
        listEl.appendChild(el("p", "text-sm text-[var(--muted-foreground)] py-6 text-center", "No time slots on this date match your current filter."));
        return;
      }}

      dayObj.filteredSlots.forEach(slot => {{
        const row = el(
          "div",
          "p-3 rounded-xl border " + (slot.isPrimeDateSlot ? "border-pink-500/40 bg-pink-500/5" : "border-[var(--border)] bg-[var(--background)]") + " space-y-2"
        );

        const header = el("div", "flex items-center justify-between gap-2");
        const timeWrap = el("div", "flex items-center gap-2 flex-wrap");
        timeWrap.appendChild(el("span", "text-sm font-bold text-[var(--foreground)]", slot.time12 + " – " + slot.endTime12));
        if (slot.isPrimeDateSlot) {{
          timeWrap.appendChild(el("span", "px-2 py-0.5 text-[10px] font-semibold rounded-full bg-pink-500 text-white", "💖 Prime Date Time"));
        }}
        header.appendChild(timeWrap);
        header.appendChild(el("span", "text-xs font-medium text-[var(--muted-foreground)]", slot.courtsCount + (slot.courtsCount === 1 ? " court open" : " courts open")));
        row.appendChild(header);

        const courtsWrap = el("div", "flex flex-wrap gap-1.5");
        slot.courts.forEach(c => {{
          const cBtn = el(
            "a",
            "px-2.5 py-1 rounded-lg text-xs font-semibold border border-[var(--border)] bg-[var(--card)] hover:border-pink-500 hover:text-pink-500 transition flex items-center gap-1",
            "Book " + c.courtName + " ↗"
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
      initHeader();
      renderControls();
      const days = getHydratedDays();
      if (!state.selectedDate && days.length > 0) {{
        const firstPrime = days.find(d => d.filteredPrimeCount > 0);
        state.selectedDate = firstPrime ? firstPrime.date : days[0].date;
      }}
      renderPrimePicks(days);
      renderCalendarGrid(days);
      renderSelectedDateDetail(days);
    }}

    document.getElementById("court-select").addEventListener("change", e => {{
      state.courtFilter = e.target.value;
      renderAll();
    }});

    document.getElementById("theme-toggle").addEventListener("click", () => {{
      document.documentElement.classList.toggle("dark");
    }});

    renderAll();
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

  html_content = generate_html_calendar(data)
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
