#!/usr/bin/env python3
"""Fetch Strava running stats and write to _data/strava.yml."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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


def load_history_from_file(path):
    """Read existing strava.yml and return the history list as a list of dicts."""
    history = []
    if not os.path.exists(path):
        return history
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except OSError:
        return history

    in_history = False
    current_entry = {}
    for line in lines:
        stripped = line.rstrip()
        if stripped == "history:":
            in_history = True
            continue
        if in_history:
            # A new top-level key (not indented) ends the history block
            if stripped and not stripped.startswith(" "):
                break
            # List item start: "  - date: ..."
            if stripped.startswith("  - date:"):
                if current_entry:
                    history.append(current_entry)
                date_val = stripped.split(":", 1)[1].strip().strip('"')
                current_entry = {"date": date_val}
            elif stripped.startswith("    rolling_28d_miles:"):
                val_str = stripped.split(":", 1)[1].strip()
                try:
                    current_entry["rolling_28d_miles"] = float(val_str)
                except ValueError:
                    current_entry["rolling_28d_miles"] = 0.0
    if current_entry:
        history.append(current_entry)
    return history


def write_strava_yml(yearly_miles, monthly_miles, recent_miles, year, month, last_updated):
    path = os.path.join(os.path.dirname(__file__), "..", "_data", "strava.yml")
    path = os.path.normpath(path)

    # Load existing history from the file
    existing_history = load_history_from_file(path)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if today's entry already exists; update or append
    found = False
    for entry in existing_history:
        if entry.get("date") == today:
            entry["rolling_28d_miles"] = recent_miles
            found = True
            break
    if not found:
        existing_history.append({"date": today, "rolling_28d_miles": recent_miles})

    # Cap to most recent 90 entries
    existing_history = existing_history[-90:]

    lines = [
        f"yearly_miles: {yearly_miles}\n",
        f"monthly_miles: {monthly_miles}\n",
        f"recent_miles: {recent_miles}\n",
        f"year: {year}\n",
        f"month: {month}\n",
        f"last_updated: \"{last_updated}\"\n",
        "history:\n",
    ]
    for entry in existing_history:
        lines.append(f"  - date: \"{entry['date']}\"\n")
        lines.append(f"    rolling_28d_miles: {entry['rolling_28d_miles']}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def main():
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    month_name = now_utc.strftime("%B")
    last_updated = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    yearly_miles = 0.0
    # monthly_miles always 0.0 — /athlete/activities requires activity:read scope not available
    monthly_miles = 0.0
    recent_miles = 0.0

    try:
        client_id = get_env("STRAVA_CLIENT_ID")
        client_secret = get_env("STRAVA_CLIENT_SECRET")
        refresh_token = get_env("STRAVA_REFRESH_TOKEN")

        access_token = get_access_token(client_id, client_secret, refresh_token)
        athlete_id = get_athlete_id(access_token)
        yearly_miles, recent_miles = get_yearly_miles(access_token, athlete_id)
    except Exception as e:
        # Sanitize: print error class and limited message, not full repr which may include tokens
        print(f"Error fetching Strava data ({type(e).__name__}). Check credentials and API status.")
        print(f"Details: {e}", file=sys.stderr)

    write_strava_yml(yearly_miles, monthly_miles, recent_miles, year, month_name, last_updated)
    print(f"Wrote _data/strava.yml: {yearly_miles} yearly miles, {monthly_miles} monthly miles, {recent_miles} recent miles")


if __name__ == "__main__":
    main()
