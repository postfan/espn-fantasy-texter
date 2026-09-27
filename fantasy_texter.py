#!/usr/bin/env python3
"""
ESPN Fantasy Football alerts (phone push via ntfy by default).

24/7, it notifies you whenever one of your players' injury designation changes
(Active / Questionable / Doubtful / OUT / IR ...).

On NFL game days (any day one of YOUR players has a game), it also:
  * sends your matchup score once an hour, and
  * sends roster moves (adds/drops, moved between starter/bench/IR).

Run it one of two ways:
  python fantasy_texter.py --loop     # keeps running, checks every CHECK_EVERY_MIN minutes
  python fantasy_texter.py            # one check and exit (for cron / GitHub Actions)
  python fantasy_texter.py --test     # send a test alert with the current score right now

All settings come from environment variables (or a .env file next to this script).
"""

import argparse
import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- config
def load_dotenv(path: Path) -> None:
    """Tiny .env loader so there's no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_dotenv(HERE / ".env")


def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"Missing required setting: {name} (put it in .env)")
    return val


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


LEAGUE_ID = int(env("ESPN_LEAGUE_ID", required=True))
TEAM_ID = int(env("ESPN_TEAM_ID", required=True))
YEAR = int(env("ESPN_YEAR", str(datetime.now().year)))
ESPN_S2 = env("ESPN_S2")  # only needed for private leagues
SWID = env("ESPN_SWID")   # only needed for private leagues

TZ = ZoneInfo(env("TIMEZONE", "America/Chicago"))
QUIET_START = int(env("QUIET_HOURS_START", "0"))   # no hourly score texts from this hour...
QUIET_END = int(env("QUIET_HOURS_END", "9"))       # ...until this hour (local time)
CHECK_EVERY_MIN = int(env("CHECK_EVERY_MIN", "10"))
ROSTER_ALERTS_EVERY_DAY = env_bool("ROSTER_ALERTS_EVERY_DAY", False)
STATE_FILE = Path(env("STATE_FILE", str(HERE / "state.json")))

# ntfy (free phone push, default) | email | twilio (SMS, paid) | sms_gateway | console (print only)
NOTIFY_METHOD = env("NOTIFY_METHOD", env("SMS_PROVIDER", "ntfy")).lower()


# --------------------------------------------------------------------------- sending
def _send_email(to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"Fantasy Texter <{env('SMTP_USER', required=True)}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(env("SMTP_USER"), env("SMTP_PASSWORD", required=True))
        s.send_message(msg)


def _send_ntfy(title: str, body: str, priority: int, tags: list[str]) -> None:
    """Publish to ntfy (https://ntfy.sh). JSON publishing keeps emoji/UTF-8 intact."""
    payload = {
        "topic": env("NTFY_TOPIC", required=True),
        "title": title,
        "message": body,
        "priority": priority,  # 1=min .. 3=default .. 5=max
        "tags": tags,
    }
    req = urllib.request.Request(
        env("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = env("NTFY_TOKEN")  # only if you protect your topic with an ntfy account
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def send_text(body: str, subject: str = "Fantasy update",
              priority: int = 3, tags: list[str] | None = None) -> None:
    body = body.strip()
    if NOTIFY_METHOD == "console":
        print(f"---- [p{priority}] {subject} ----\n{body}\n--------------")
    elif NOTIFY_METHOD == "ntfy":
        _send_ntfy(subject, body, priority, tags or [])
    elif NOTIFY_METHOD == "email":
        # Defaults to emailing yourself (the Gmail account that sends it)
        _send_email(env("EMAIL_TO") or env("SMTP_USER", required=True), subject, body)
    elif NOTIFY_METHOD == "sms_gateway":
        _send_email(env("SMS_GATEWAY_ADDRESS", required=True), "", body)
    elif NOTIFY_METHOD == "twilio":
        from twilio.rest import Client

        client = Client(env("TWILIO_ACCOUNT_SID", required=True),
                        env("TWILIO_AUTH_TOKEN", required=True))
        client.messages.create(body=body,
                               from_=env("TWILIO_FROM_NUMBER", required=True),
                               to=env("MY_PHONE_NUMBER", required=True))
    else:
        sys.exit(f"Unknown NOTIFY_METHOD: {NOTIFY_METHOD}")


# --------------------------------------------------------------------------- ESPN
def get_league():
    from espn_api.football import League

    kwargs = dict(league_id=LEAGUE_ID, year=YEAR)
    if ESPN_S2 and SWID:
        kwargs.update(espn_s2=ESPN_S2, swid=SWID)
    return League(**kwargs)


def team_name(team) -> str:
    return getattr(team, "team_name", None) or getattr(team, "team_abbrev", "BYE")


def get_my_matchup(league):
    """Return a dict describing my current matchup and my lineup."""
    for box in league.box_scores():
        home_id = getattr(box.home_team, "team_id", None)
        away_id = getattr(box.away_team, "team_id", None)
        if TEAM_ID not in (home_id, away_id):
            continue
        mine_home = home_id == TEAM_ID
        return {
            "week": league.current_week,
            "my_name": team_name(box.home_team if mine_home else box.away_team),
            "opp_name": team_name(box.away_team if mine_home else box.home_team),
            "my_score": box.home_score if mine_home else box.away_score,
            "opp_score": box.away_score if mine_home else box.home_score,
            "my_proj": box.home_projected if mine_home else box.away_projected,
            "opp_proj": box.away_projected if mine_home else box.home_projected,
            "lineup": box.home_lineup if mine_home else box.away_lineup,
        }
    raise RuntimeError(f"Team {TEAM_ID} not found in this week's matchups. "
                       "Check ESPN_TEAM_ID (the number after teamId= in your team URL).")


def player_snapshot(lineup) -> dict:
    """{player_id: {name, team, status, slot, game_date}} for every rostered player."""
    snap = {}
    for p in lineup:
        game_date = getattr(p, "game_date", None)
        snap[str(p.playerId)] = {
            "name": p.name,
            "team": getattr(p, "proTeam", ""),
            "status": (getattr(p, "injuryStatus", None) or "ACTIVE").upper(),
            "slot": getattr(p, "slot_position", ""),
            # espn_api returns a naive datetime in the machine's local time
            "game_date": game_date.astimezone(TZ).isoformat() if game_date else None,
            "points": round(getattr(p, "points", 0) or 0, 2),
        }
    return snap


# --------------------------------------------------------------------------- logic
STATUS_LABELS = {
    "ACTIVE": "Active", "NORMAL": "Active", "QUESTIONABLE": "Questionable",
    "DOUBTFUL": "Doubtful", "OUT": "OUT", "INJURY_RESERVE": "IR",
    "SUSPENSION": "Suspended", "DAY_TO_DAY": "Day-to-day", "PROBABLE": "Probable",
}
BENCH_SLOTS = {"BE", "IR"}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status.title())


def is_game_day(snap: dict, now: datetime) -> bool:
    today = now.date()
    for p in snap.values():
        if p["game_date"] and datetime.fromisoformat(p["game_date"]).astimezone(TZ).date() == today:
            return True
    return False


def score_text(m: dict, snap: dict, now: datetime) -> str:
    diff = m["my_score"] - m["opp_score"]
    lead = "Up" if diff > 0 else "Down" if diff < 0 else "Tied"
    starters = [p for p in snap.values() if p["slot"] not in BENCH_SLOTS]
    top = sorted(starters, key=lambda p: p["points"], reverse=True)[:3]
    tops = ", ".join(f"{p['name'].split()[-1]} {p['points']:g}" for p in top if p["points"])
    lines = [
        f"🏈 Wk {m['week']} · {now.strftime('%-I:%M %p')}",
        f"{m['my_name']} {m['my_score']:.2f}",
        f"{m['opp_name']} {m['opp_score']:.2f}",
        f"{lead} {abs(diff):.2f}" + (f" · proj {m['my_proj']:.1f}-{m['opp_proj']:.1f}"
                                      if m.get("my_proj") is not None else ""),
    ]
    if tops:
        lines.append(f"Top: {tops}")
    return "\n".join(lines)


def injury_changes(old: dict, new: dict) -> list[str]:
    """Injury designation changes for players on the roster both times."""
    changes = []
    for pid, p in new.items():
        prev = old.get(pid)
        if prev and label(prev["status"]) != label(p["status"]):
            changes.append(f"⚠️ {p['name']} ({p['team']}): "
                           f"{label(prev['status'])} → {label(p['status'])}")
    return changes


def roster_changes(old: dict, new: dict) -> list[str]:
    """Players added/dropped and lineup-slot moves."""
    changes = []
    for pid, p in new.items():
        prev = old.get(pid)
        if prev is None:
            changes.append(f"➕ {p['name']} added to your roster ({label(p['status'])})")
        elif prev["slot"] != p["slot"] and p["slot"]:
            changes.append(f"🔁 {p['name']} moved {prev['slot']} → {p['slot']}")
    for pid, p in old.items():
        if pid not in new:
            changes.append(f"➖ {p['name']} left your roster")
    return changes


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check_once(force_score: bool = False) -> None:
    now = datetime.now(TZ)
    league = get_league()
    m = get_my_matchup(league)
    snap = player_snapshot(m["lineup"])
    state = load_state()
    game_day = is_game_day(snap, now)

    # 1a) Injury updates: every check, 24/7, including across week rollovers
    old = state.get("players")
    if old is not None:
        changes = injury_changes(old, snap)
        if changes:
            subj = (changes[0].replace("⚠️ ", "⚠️ Injury: ") if len(changes) == 1
                    else f"⚠️ {len(changes)} injury updates")
            send_text("\n".join(changes), subj, priority=4)

    # 1b) Roster/lineup moves: game days only, within the same week
    #     (lineup slots reset when a new week starts, so don't compare across weeks)
    if old is not None and state.get("week") == m["week"] and (game_day or ROSTER_ALERTS_EVERY_DAY):
        changes = roster_changes(old, snap)
        if changes:
            send_text("\n".join(changes), "🔁 Roster update")

    # 2) Hourly score
    hour_key = now.strftime("%Y-%m-%d %H")
    if QUIET_START == QUIET_END:
        quiet = False  # quiet hours disabled
    elif QUIET_START < QUIET_END:
        quiet = QUIET_START <= now.hour < QUIET_END
    else:  # wraps midnight, e.g. 23 -> 8
        quiet = now.hour >= QUIET_START or now.hour < QUIET_END
    if force_score or (game_day and not quiet and state.get("last_score_hour") != hour_key):
        diff = m["my_score"] - m["opp_score"]
        lead = "Up" if diff > 0 else "Down" if diff < 0 else "Tied"
        send_text(score_text(m, snap, now),
                  f"🏈 Wk {m['week']}: {lead} {abs(diff):.2f} "
                  f"({m['my_score']:.2f}–{m['opp_score']:.2f})")
        state["last_score_hour"] = hour_key

    state.update(week=m["week"], players=snap, last_check=now.isoformat())
    save_state(state)
    # Keep the log free of scores/names: GitHub Actions logs are public on a public repo
    print(f"[{now:%Y-%m-%d %H:%M}] week {m['week']} game_day={game_day} ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", action="store_true", help="run forever")
    ap.add_argument("--test", action="store_true", help="send the current score right now")
    args = ap.parse_args()

    if args.test:
        check_once(force_score=True)
        return
    if not args.loop:
        check_once()
        return
    while True:
        try:
            check_once()
        except Exception as e:  # keep the loop alive through ESPN hiccups
            print(f"check failed: {e!r}", file=sys.stderr)
        time.sleep(CHECK_EVERY_MIN * 60)


if __name__ == "__main__":
    main()
