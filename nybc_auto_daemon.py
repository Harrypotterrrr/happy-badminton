#!/usr/bin/env python3
"""Background auto-updater for NYBC Flushing Badminton Calendar.

1. Automatically crawls NYBC and pushes to GitHub every 2 hours between
   9:00 AM and 11:00 PM America/New_York time (no auto-updates between 11 PM and 9 AM).
2. Listens in real time to https://ntfy.sh/nybc-flushing-refresh-7f8a9b2c/json
   so clicking the '刷新数据' (Refresh Data) button on the public webpage
   immediately triggers a live crawl and updates GitHub within ~10 seconds.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

REPO_DIR = pathlib.Path(__file__).resolve().parent
ENV_FILE = pathlib.Path.home() / ".config" / "nybc-badminton" / "github.env"
LOG_FILE = pathlib.Path.home() / ".config" / "nybc-badminton" / "daemon.log"
NTFY_TRIGGER_TOPIC = "nybc-flushing-refresh-7f8a9b2c"
NY_TZ = ZoneInfo("America/New_York")

_crawl_lock = threading.Lock()
_last_crawl_epoch = 0.0


def log(msg: str) -> None:
  now_ny = datetime.datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
  line = f"[{now_ny}] {msg}\n"
  sys.stdout.write(line)
  sys.stdout.flush()
  try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
      f.write(line)
  except Exception:
    pass


def load_env() -> dict[str, str]:
  env: dict[str, str] = {}
  if ENV_FILE.exists():
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
      line = raw_line.strip()
      if not line or line.startswith("#") or "=" not in line:
        continue
      k, v = line.split("=", 1)
      env[k.strip()] = v.strip().strip("'").strip('"')
  return env


def get_last_crawled_epoch() -> float:
  json_path = REPO_DIR / "nybc_flushing_availability.json"
  if not json_path.exists():
    return 0.0
  try:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    crawled_at = data.get("crawledAt", "")
    if crawled_at:
      dt = datetime.datetime.fromisoformat(crawled_at)
      return dt.timestamp()
  except Exception:
    pass
  return 0.0


def trigger_github_workflow(gh_user: str, repo_name: str, gh_token: str) -> bool:
  url = (
      f"https://api.github.com/repos/{gh_user}/{repo_name}"
      "/actions/workflows/update_calendar.yml/dispatches"
  )
  req = urllib.request.Request(
      url,
      data=json.dumps({"ref": "main"}).encode("utf-8"),
      headers={
          "Accept": "application/vnd.github+json",
          "Authorization": f"Bearer {gh_token}",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "nybc-auto-daemon",
          "Content-Type": "application/json",
      },
      method="POST",
  )
  try:
    with urllib.request.urlopen(req, timeout=10) as resp:
      return resp.status in (200, 201, 204)
  except Exception as e:
    log(f"Note: workflow_dispatch returned: {e}")
    return False


def run_crawl_and_push(reason: str) -> bool:
  global _last_crawl_epoch
  if not _crawl_lock.acquire(blocking=False):
    log(f"Crawl already in progress; skipping duplicate trigger ({reason}).")
    return False

  try:
    now = time.time()
    if now - _last_crawl_epoch < 15.0:
      log(f"Skipped trigger ({reason}): crawled {now - _last_crawl_epoch:.1f}s ago.")
      return False

    env_cfg = load_env()
    gh_user = env_cfg.get("GH_USER", "Harrypotterrrr")
    repo_name = env_cfg.get("REPO_NAME", "happy-badminton")
    gh_token = env_cfg.get("GH_TOKEN", "")

    log(f"Starting live NYBC crawl & GitHub update (reason={reason})...")
    if gh_token:
      trigger_github_workflow(gh_user, repo_name, gh_token)

    cmd = [
        sys.executable,
        str(REPO_DIR / "nybc_flushing_crawler.py"),
        "--players",
        "2",
        "3",
        "4",
        "--days",
        "35",
        "--out-dir",
        str(REPO_DIR),
    ]
    res = subprocess.run(cmd, cwd=str(REPO_DIR), capture_output=True, text=True)
    if res.returncode != 0:
      log(f"Crawler failed (exit {res.returncode}): {res.stderr}")
      return False

    _last_crawl_epoch = time.time()

    if not gh_token:
      log("Crawled locally, but GH_TOKEN is not yet configured in ~/.config/nybc-badminton/github.env.")
      return False

    askpass = REPO_DIR / ".git_askpass_tmp.sh"
    try:
      askpass.write_text(
          "#!/usr/bin/env bash\n"
          'case "$1" in\n'
          '  *Username*) printf "%s\\n" "$GH_PUSH_USER" ;;\n'
          '  *Password*) printf "%s\\n" "$GH_PUSH_TOKEN" ;;\n'
          "esac\n",
          encoding="utf-8",
      )
      askpass.chmod(0o700)

      git_env = os.environ.copy()
      git_env["GH_PUSH_USER"] = gh_user
      git_env["GH_PUSH_TOKEN"] = gh_token
      git_env["GIT_ASKPASS"] = str(askpass)
      git_env["GIT_TERMINAL_PROMPT"] = "0"

      subprocess.run(
          [
              "git",
              "add",
              "index.html",
              "nybc_flushing_calendar.html",
              "nybc_flushing_calendar.md",
              "nybc_flushing_availability.json",
          ],
          cwd=str(REPO_DIR),
          env=git_env,
          check=False,
      )
      diff_res = subprocess.run(
          ["git", "diff", "--staged", "--quiet"],
          cwd=str(REPO_DIR),
          env=git_env,
      )
      if diff_res.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"chore: update NYBC Flushing court availability ({reason})"],
            cwd=str(REPO_DIR),
            env=git_env,
            check=False,
        )
      subprocess.run(
          ["git", "pull", "--rebase", "-X", "theirs", "origin", "main"],
          cwd=str(REPO_DIR),
          env=git_env,
          check=False,
      )
      push_res = subprocess.run(
          ["git", "push", "origin", "main"],
          cwd=str(REPO_DIR),
          env=git_env,
          capture_output=True,
          text=True,
      )
      if push_res.returncode == 0:
        log(f"Successfully pushed fresh NYBC availability to GitHub ({reason}).")
        return True
      else:
        log(f"Git push returned {push_res.returncode}: {push_res.stderr}")
        return False
    finally:
      try:
        askpass.unlink(missing_ok=True)
      except Exception:
        pass
  finally:
    _crawl_lock.release()


def listen_ntfy_loop() -> None:
  """Streams real-time refresh events from ntfy.sh when users click Refresh on the web."""
  stream_url = f"https://ntfy.sh/{NTFY_TRIGGER_TOPIC}/json"
  log(f"Listening for web Refresh clicks on {stream_url} ...")
  while True:
    try:
      req = urllib.request.Request(
          stream_url,
          headers={"User-Agent": "nybc-auto-daemon/1.0"},
      )
      with urllib.request.urlopen(req, timeout=90) as resp:
        for raw_line in resp:
          line = raw_line.decode("utf-8", errors="replace").strip()
          if not line:
            continue
          try:
            evt = json.loads(line)
          except Exception:
            continue
          if evt.get("event") == "message":
            msg = str(evt.get("message", "")).strip()
            log(f"Received web refresh trigger: {msg!r}")
            threading.Thread(
                target=run_crawl_and_push,
                args=("web-refresh-button",),
                daemon=True,
            ).start()
    except Exception as e:
      time.sleep(3)


def schedule_2h_loop() -> None:
  """Runs every 2 hours between 9:00 AM and 11:00 PM America/New_York time."""
  log("Started 2-hour scheduler (active 09:00–23:00 America/New_York, paused 23:00–09:00).")
  last_scheduled_slot: tuple[str, int] | None = None

  while True:
    try:
      now_ny = datetime.datetime.now(NY_TZ)
      hour = now_ny.hour
      minute = now_ny.minute
      today_str = now_ny.strftime("%Y-%m-%d")

      # Active window: 9:00 AM to 11:00 PM EST (hours 9, 11, 13, 15, 17, 19, 21, 23)
      is_active_window = (9 <= hour < 23) or (hour == 23 and minute <= 5)

      if is_active_window:
        # 1) Exact 2-hour schedule slot (9, 11, 13, 15, 17, 19, 21, 23)
        if hour in (9, 11, 13, 15, 17, 19, 21, 23) and minute <= 4:
          slot_key = (today_str, hour)
          if last_scheduled_slot != slot_key:
            last_scheduled_slot = slot_key
            run_crawl_and_push(f"scheduled-{hour:02d}h-EST")

        # 2) Catch-up check: if during 9 AM - 11 PM EST and data is >= 2 hours old
        elif 9 <= hour < 23:
          last_epoch = max(_last_crawl_epoch, get_last_crawled_epoch())
          age_seconds = time.time() - last_epoch
          if age_seconds >= 2 * 3600:
            run_crawl_and_push("auto-2h-stale-check")
    except Exception as e:
      log(f"Scheduler error: {e}")

    time.sleep(30)


def main() -> None:
  t = threading.Thread(target=listen_ntfy_loop, daemon=True)
  t.start()
  schedule_2h_loop()


if __name__ == "__main__":
  main()
