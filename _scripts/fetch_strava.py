#!/usr/bin/env python3
"""Fetch Strava running stats and write to _data/strava.yml."""

import json
import os
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
    meters = data.get("ytd_run_totals", {}).get("distance", 0.0)
    return round(meters / METERS_PER_MILE, 1)


def get_monthly_miles(access_token, after_timestamp):
    total_meters = 0.0
    page = 1
    while True:
        params = urllib.parse.urlencode({
            "after": after_timestamp,
            "per_page": 200,
            "page": page,
        })
        url = f"https://www.strava.com/api/v3/athlete/activities?{params}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req) as resp:
            activities = json.loads(resp.read())
        if not isinstance(activities, list):
            raise ValueError(f"Expected list from activities endpoint, got: {type(activities).__name__}")
        if not activities:
            break
        for activity in activities:
            if activity.get("type") == "Run":
                total_meters += activity.get("distance", 0.0)
        if len(activities) < 200:
            break
        page += 1
    return round(total_meters / METERS_PER_MILE, 1)


def get_weekly_miles(access_token, num_weeks=8):
    """Fetch per-week run miles for the last num_weeks weeks (rolling window)."""
    from datetime import timedelta

    now_utc = datetime.now(timezone.utc)

    # Find the most recent Monday (start of current ISO week)
    days_since_monday = now_utc.weekday()  # Monday=0, Sunday=6
    current_week_start = (now_utc - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Build list of week-start datetimes, oldest first
    week_starts = [
        current_week_start - timedelta(weeks=i)
        for i in range(num_weeks - 1, -1, -1)
    ]

    # Fetch all activities since the oldest week start
    after_timestamp = int(week_starts[0].timestamp())
    all_activities = []
    page = 1
    while True:
        params = urllib.parse.urlencode({
            "after": after_timestamp,
            "per_page": 200,
            "page": page,
        })
        url = f"https://www.strava.com/api/v3/athlete/activities?{params}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req) as resp:
            activities = json.loads(resp.read())
        if not isinstance(activities, list):
            raise ValueError(f"Expected list from activities endpoint, got: {type(activities).__name__}")
        if not activities:
            break
        all_activities.extend(activities)
        if len(activities) < 200:
            break
        page += 1

    # Bucket runs into weeks
    from datetime import timedelta
    weekly = []
    for week_start in week_starts:
        week_end = week_start + timedelta(weeks=1)
        label = week_start.strftime("%-m/%-d")  # e.g. "3/3"
        total_meters = 0.0
        for activity in all_activities:
            if activity.get("type") != "Run":
                continue
            start_date = activity.get("start_date", "")
            try:
                act_dt = datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if week_start <= act_dt < week_end:
                total_meters += activity.get("distance", 0.0)
        weekly.append({
            "label": label,
            "miles": round(total_meters / METERS_PER_MILE, 1),
        })

    return weekly


def write_strava_yml(yearly_miles, monthly_miles, year, month, last_updated, weekly_miles):
    path = os.path.join(os.path.dirname(__file__), "..", "_data", "strava.yml")
    path = os.path.normpath(path)
    lines = [
        f"yearly_miles: {yearly_miles}\n",
        f"monthly_miles: {monthly_miles}\n",
        f"year: {year}\n",
        f"month: {month}\n",
        f"last_updated: \"{last_updated}\"\n",
        "weekly_miles:\n",
    ]
    for week in weekly_miles:
        lines.append(f"  - label: \"{week['label']}\"\n")
        lines.append(f"    miles: {week['miles']}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def main():
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    month_name = now_utc.strftime("%B")
    last_updated = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Unix timestamp for the 1st of the current month at 00:00:00 UTC
    first_of_month = datetime(year, now_utc.month, 1, 0, 0, 0, tzinfo=timezone.utc)
    after_timestamp = int(first_of_month.timestamp())

    yearly_miles = 0.0
    monthly_miles = 0.0
    weekly_miles = []

    try:
        client_id = get_env("STRAVA_CLIENT_ID")
        client_secret = get_env("STRAVA_CLIENT_SECRET")
        refresh_token = get_env("STRAVA_REFRESH_TOKEN")

        access_token = get_access_token(client_id, client_secret, refresh_token)
        athlete_id = get_athlete_id(access_token)
        yearly_miles = get_yearly_miles(access_token, athlete_id)
        monthly_miles = get_monthly_miles(access_token, after_timestamp)
        weekly_miles = get_weekly_miles(access_token)
    except Exception as e:
        # Sanitize: print error class and limited message, not full repr which may include tokens
        print(f"Error fetching Strava data ({type(e).__name__}). Check credentials and API status.")
        import sys
        print(f"Details: {e}", file=sys.stderr)

    write_strava_yml(yearly_miles, monthly_miles, year, month_name, last_updated, weekly_miles)
    print(f"Wrote _data/strava.yml: {yearly_miles} yearly miles, {monthly_miles} monthly miles")


if __name__ == "__main__":
    main()
