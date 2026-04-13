#!/usr/bin/env python3
"""Fetch Strava running stats and write to _data/strava.yml."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

METERS_PER_MILE = 1609.344


def get_env(name):
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def get_access_token(client_id, client_secret, refresh_token):
    url = "https://www.strava.com/oauth/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def get_athlete_id(access_token):
    url = "https://www.strava.com/api/v3/athlete"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["id"]


def get_yearly_miles(access_token, athlete_id):
    url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    yearly_meters = data.get("ytd_run_totals", {}).get("distance", 0.0)
    recent_meters = data.get("recent_run_totals", {}).get("distance", 0.0)
    return round(yearly_meters / METERS_PER_MILE, 1), round(recent_meters / METERS_PER_MILE, 1)


def load_yml_lists(path):
    """Read existing strava.yml and return (daily_snapshots, weekly_history) as lists of dicts."""
    daily_snapshots = []
    weekly_history = []

    if not os.path.exists(path):
        return daily_snapshots, weekly_history
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except OSError:
        return daily_snapshots, weekly_history

    mode = None  # None, "daily", or "weekly"
    current_entry = {}

    for line in lines:
        stripped = line.rstrip()

        if stripped == "daily_snapshots:":
            if current_entry:
                if mode == "daily" and "date" in current_entry and "ytd_miles" in current_entry:
                    daily_snapshots.append(current_entry)
                elif mode == "weekly" and "week_start" in current_entry and "miles" in current_entry:
                    weekly_history.append(current_entry)
                current_entry = {}
            mode = "daily"
            continue

        if stripped == "weekly_history:":
            if current_entry:
                if mode == "daily" and "date" in current_entry and "ytd_miles" in current_entry:
                    daily_snapshots.append(current_entry)
                elif mode == "weekly" and "week_start" in current_entry and "miles" in current_entry:
                    weekly_history.append(current_entry)
                current_entry = {}
            mode = "weekly"
            continue

        if mode is None:
            continue

        # A non-indented non-empty line ends the current block
        if stripped and not stripped.startswith(" "):
            if current_entry:
                if mode == "daily" and "date" in current_entry and "ytd_miles" in current_entry:
                    daily_snapshots.append(current_entry)
                elif mode == "weekly" and "week_start" in current_entry and "miles" in current_entry:
                    weekly_history.append(current_entry)
                current_entry = {}
            mode = None
            continue

        if mode == "daily":
            if stripped.startswith("  - date:"):
                if current_entry and "date" in current_entry and "ytd_miles" in current_entry:
                    daily_snapshots.append(current_entry)
                date_val = stripped.split(":", 1)[1].strip().strip('"')
                current_entry = {"date": date_val}
            elif stripped.startswith("    ytd_miles:"):
                val_str = stripped.split(":", 1)[1].strip()
                try:
                    current_entry["ytd_miles"] = float(val_str)
                except ValueError:
                    current_entry["ytd_miles"] = 0.0

        elif mode == "weekly":
            if stripped.startswith("  - week_start:"):
                if current_entry and "week_start" in current_entry and "miles" in current_entry:
                    weekly_history.append(current_entry)
                date_val = stripped.split(":", 1)[1].strip().strip('"')
                current_entry = {"week_start": date_val}
            elif stripped.startswith("    miles:"):
                val_str = stripped.split(":", 1)[1].strip()
                try:
                    current_entry["miles"] = float(val_str)
                except ValueError:
                    current_entry["miles"] = 0.0

    # Flush any trailing entry
    if current_entry:
        if mode == "daily" and "date" in current_entry and "ytd_miles" in current_entry:
            daily_snapshots.append(current_entry)
        elif mode == "weekly" and "week_start" in current_entry and "miles" in current_entry:
            weekly_history.append(current_entry)

    return daily_snapshots, weekly_history


def compute_weekly_history(daily_snapshots, num_weeks=13):
    """Derive per-week miles from daily YTD snapshots."""
    snapshot_by_date = {
        entry["date"]: entry["ytd_miles"]
        for entry in daily_snapshots
        if "date" in entry and "ytd_miles" in entry
    }

    today = datetime.now(timezone.utc).date()

    days_since_monday = today.weekday()  # Monday=0
    current_week_monday = today - timedelta(days=days_since_monday)

    mondays = [current_week_monday - timedelta(weeks=i) for i in range(num_weeks - 1, -1, -1)]

    weekly = []
    for monday in mondays:
        week_end = monday + timedelta(days=6)  # Sunday
        end_date = min(week_end, today)  # current week may be incomplete

        # Find end_ytd: look up end_date, scan backwards up to 6 days if missing
        end_ytd = None
        for delta in range(7):
            candidate = (end_date - timedelta(days=delta)).strftime("%Y-%m-%d")
            if candidate in snapshot_by_date:
                # Don't go before monday
                if (end_date - timedelta(days=delta)) >= monday:
                    end_ytd = snapshot_by_date[candidate]
                    break
        if end_ytd is None:
            continue

        # Find start_ytd: the YTD for the day before monday
        day_before_monday = monday - timedelta(days=1)
        start_ytd = None
        for delta in range(7):
            candidate = (day_before_monday - timedelta(days=delta)).strftime("%Y-%m-%d")
            if candidate in snapshot_by_date:
                start_ytd = snapshot_by_date[candidate]
                break
        if start_ytd is None:
            # No snapshot before this week — treat as start of history (or new year boundary)
            start_ytd = 0.0

        week_miles = round(max(end_ytd - start_ytd, 0.0), 1)
        weekly.append({"week_start": monday.strftime("%Y-%m-%d"), "miles": week_miles})

    # Backfill any missing leading weeks with 0.0 so we always return num_weeks entries
    today = datetime.now(timezone.utc).date()
    days_since_monday = today.weekday()
    current_week_monday = today - timedelta(days=days_since_monday)
    all_mondays = [current_week_monday - timedelta(weeks=i) for i in range(num_weeks - 1, -1, -1)]

    computed_starts = {entry["week_start"] for entry in weekly}
    full_weekly = []
    for monday in all_mondays:
        key = monday.strftime("%Y-%m-%d")
        if key in computed_starts:
            full_weekly.append(next(e for e in weekly if e["week_start"] == key))
        else:
            full_weekly.append({"week_start": key, "miles": 0.0})

    return full_weekly


def write_strava_yml(yearly_miles, recent_miles, year, month, last_updated, daily_snapshots, weekly_history):
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_data", "strava.yml"))

    lines = [
        f"yearly_miles: {yearly_miles}\n",
        f"recent_miles: {recent_miles}\n",
        f"year: {year}\n",
        f"month: {month}\n",
        f"last_updated: \"{last_updated}\"\n",
        "daily_snapshots:\n",
    ]
    for entry in daily_snapshots:
        lines.append(f"  - date: \"{entry['date']}\"\n")
        lines.append(f"    ytd_miles: {entry['ytd_miles']}\n")
    lines.append("weekly_history:\n")
    for entry in weekly_history:
        lines.append(f"  - week_start: \"{entry['week_start']}\"\n")
        lines.append(f"    miles: {entry['miles']}\n")

    with open(path, "w") as f:
        f.writelines(lines)


def main():
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    month_name = now_utc.strftime("%B")
    last_updated = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str = now_utc.strftime("%Y-%m-%d")

    yearly_miles = 0.0
    recent_miles = 0.0

    # Load existing data from file
    yml_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_data", "strava.yml"))
    daily_snapshots, _ = load_yml_lists(yml_path)

    api_success = False
    try:
        client_id = get_env("STRAVA_CLIENT_ID")
        client_secret = get_env("STRAVA_CLIENT_SECRET")
        refresh_token = get_env("STRAVA_REFRESH_TOKEN")

        access_token = get_access_token(client_id, client_secret, refresh_token)
        athlete_id = get_athlete_id(access_token)
        yearly_miles, recent_miles = get_yearly_miles(access_token, athlete_id)
        api_success = True
    except Exception as e:
        print(f"Error fetching Strava data ({type(e).__name__}). Check credentials and API status.")
        print(f"Details: {e}", file=sys.stderr)

    # Upsert today's daily snapshot only if API succeeded
    if api_success:
        found = False
        for entry in daily_snapshots:
            if entry["date"] == today_str:
                entry["ytd_miles"] = yearly_miles
                found = True
                break
        if not found:
            daily_snapshots.append({"date": today_str, "ytd_miles": yearly_miles})
    # Cap to 90 most recent
    daily_snapshots = daily_snapshots[-90:]

    # Compute weekly history from snapshots
    weekly_history = compute_weekly_history(daily_snapshots)

    write_strava_yml(yearly_miles, recent_miles, year, month_name, last_updated, daily_snapshots, weekly_history)
    print(f"Wrote _data/strava.yml: {yearly_miles} yearly miles, {recent_miles} recent miles, {len(weekly_history)} weekly buckets")


if __name__ == "__main__":
    main()
