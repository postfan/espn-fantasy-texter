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


def _send_ntfy_image(title: str, body: str, priority: int, png: bytes) -> None:
    """Publish with a PNG attachment. Text goes in URL params so emoji survive."""
    from urllib.parse import urlencode

    params = urlencode({"title": title, "message": body, "priority": priority,
                        "filename": "matchup.png"})
    url = f"{env('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')}/{env('NTFY_TOPIC', required=True)}?{params}"
    req = urllib.request.Request(url, data=png, method="PUT")
    token = env("NTFY_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def send_text(body: str, subject: str = "Fantasy update",
              priority: int = 3, tags: list[str] | None = None,
              image: bytes | None = None, full_text: str | None = None) -> None:
    """image: optional PNG (used by ntfy). full_text: text fallback used when no image is sent."""
    body = body.strip()
    if NOTIFY_METHOD == "console":
        if image:
            out = STATE_FILE.parent / "matchup.png"
            out.write_bytes(image)
            print(f"---- [p{priority}] {subject} ----\n{body}\n[image saved to {out}]\n--------------")
        else:
            print(f"---- [p{priority}] {subject} ----\n{(full_text or body)}\n--------------")
        return
    if NOTIFY_METHOD == "ntfy" and image:
        try:
            _send_ntfy_image(subject, body, priority, image)
            return
        except Exception as e:  # attachment failed -> fall back to plain text
            print(f"image upload failed, sending text instead: {e!r}", file=sys.stderr)
    body = (full_text or body).strip()
    if NOTIFY_METHOD == "ntfy":
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
            "opp_lineup": (box.away_lineup if mine_home else box.home_lineup) or [],
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
            "pos": getattr(p, "position", ""),
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

# Lineup-slot order as ESPN Fantasycast shows it (FLEX sits between TE and D/ST)
SLOT_ORDER = ["QB", "TQB", "RB", "RB/WR", "WR", "WR/TE", "TE", "RB/WR/TE", "OP",
              "DT", "DE", "LB", "DL", "CB", "S", "DB", "DP", "D/ST", "K", "P", "HC",
              "BE", "IR"]


def slot_rank(slot: str) -> int:
    return SLOT_ORDER.index(slot) if slot in SLOT_ORDER else len(SLOT_ORDER) - 2


def fantasycast_order(players) -> list:
    # stable sort keeps ESPN's own order within a slot (e.g. RB1 before RB2)
    return sorted(players, key=lambda p: slot_rank(p["slot"]))


def who(p: dict) -> str:
    """'Josh Allen (BUF - QB)'"""
    tags = " - ".join(x for x in (p.get("team"), p.get("pos")) if x)
    return f"{p['name']} ({tags})" if tags else p["name"]


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status.title())


def is_game_day(snap: dict, now: datetime) -> bool:
    today = now.date()
    for p in snap.values():
        if p["game_date"] and datetime.fromisoformat(p["game_date"]).astimezone(TZ).date() == today:
            return True
    return False


SLOT_LABELS = {"RB/WR/TE": "FLEX", "BE": "BN", "WR/TE": "W/T", "RB/WR": "R/W"}


def short_who(p: dict | None, slot: str) -> str:
    """'Josh Allen (BUF)'; adds position when the slot doesn't already say it (FLEX, bench)."""
    if p is None:
        return "—"
    tags = [p.get("team") or ""]
    if p.get("pos") and p.get("pos") != slot:
        tags.append(p["pos"])
    tags = " - ".join(t for t in tags if t)
    return f"{p['name']} ({tags})" if tags else p["name"]


def side_by_side(mine: dict, theirs: dict) -> list[str]:
    """One row per lineup slot, Fantasycast style:  my player  pts · SLOT · pts  their player"""
    by_slot = {}
    for side, snap in (("me", mine), ("opp", theirs)):
        for pl in fantasycast_order(snap.values()):
            by_slot.setdefault(pl["slot"], {"me": [], "opp": []})[side].append(pl)
    rows, bench_started = [], False
    for slot in sorted(by_slot, key=slot_rank):
        if slot in BENCH_SLOTS and not bench_started:
            rows.append("")
            bench_started = True
        a, b = by_slot[slot]["me"], by_slot[slot]["opp"]
        for i in range(max(len(a), len(b))):
            pa = a[i] if i < len(a) else None
            pb = b[i] if i < len(b) else None
            left = f"{short_who(pa, slot)} {pa['points']:g}" if pa else "—"
            right = f"{pb['points']:g} {short_who(pb, slot)}" if pb else "—"
            rows.append(f"{left} · {SLOT_LABELS.get(slot, slot)} · {right}")
    return rows


# --------------------------------------------------------------------------- matchup image
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for path in (BOLD_PATHS if bold else FONT_PATHS):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render_matchup_png(m: dict, mine: dict, theirs: dict, now: datetime) -> bytes:
    """Fantasycast-style table: my player | pts | SLOT | pts | their player, in fixed columns."""
    import io
    from PIL import Image, ImageDraw

    BG, ROW_ALT, LINE = (18, 20, 24), (28, 31, 37), (48, 52, 60)
    TEXT, DIM, WIN, LOSE = (235, 237, 240), (140, 146, 158), (92, 214, 128), (240, 110, 110)
    W, PAD, ROW_H = 1080, 28, 66
    name_f, sub_f, pts_f, slot_f = _font(28, True), _font(21), _font(30, True), _font(22, True)
    head_f, big_f = _font(30, True), _font(56, True)

    # rows by slot, same pairing as the text version
    by_slot = {}
    for side, snap in (("me", mine), ("opp", theirs)):
        for pl in fantasycast_order(snap.values()):
            by_slot.setdefault(pl["slot"], {"me": [], "opp": []})[side].append(pl)
    rows = []
    for slot in sorted(by_slot, key=slot_rank):
        a, b = by_slot[slot]["me"], by_slot[slot]["opp"]
        for i in range(max(len(a), len(b))):
            rows.append((slot, a[i] if i < len(a) else None, b[i] if i < len(b) else None))
    n_bench_gap = 1 if any(r[0] in BENCH_SLOTS for r in rows) else 0

    header_h = 210
    H = header_h + ROW_H * (len(rows) + n_bench_gap) + PAD
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def fit(text, font, max_w):
        if d.textlength(text, font=font) <= max_w:
            return text
        while text and d.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text + "…"

    # header
    my_s, op_s = m["my_score"], m["opp_score"]
    my_c = WIN if my_s > op_s else LOSE if my_s < op_s else TEXT
    op_c = WIN if op_s > my_s else LOSE if op_s < my_s else TEXT
    d.text((W / 2, 22), f"Week {m['week']}  ·  {now.strftime('%a %-I:%M %p')}", font=sub_f, fill=DIM, anchor="mt")
    d.text((PAD, 60), fit(m["my_name"], head_f, W / 2 - PAD - 20), font=head_f, fill=TEXT)
    d.text((W - PAD, 60), fit(m["opp_name"], head_f, W / 2 - PAD - 20), font=head_f, fill=TEXT, anchor="ra")
    d.text((PAD, 100), f"{my_s:.2f}", font=big_f, fill=my_c)
    d.text((W - PAD, 100), f"{op_s:.2f}", font=big_f, fill=op_c, anchor="ra")
    if m.get("my_proj") is not None:
        d.text((PAD, 168), f"Proj {m['my_proj']:.1f}", font=sub_f, fill=DIM)
        d.text((W - PAD, 168), f"Proj {m['opp_proj']:.1f}", font=sub_f, fill=DIM, anchor="ra")
    d.line((0, header_h - 6, W, header_h - 6), fill=LINE, width=2)

    # column geometry (fixed x positions = perfect alignment)
    CX = W / 2
    slot_w = 110
    pts_w = 90
    name_w = CX - slot_w / 2 - pts_w - PAD - 12

    y = header_h
    bench_drawn = False
    for idx, (slot, pa, pb) in enumerate(rows):
        if slot in BENCH_SLOTS and not bench_drawn:
            d.text((CX, y + ROW_H / 2), "BENCH", font=slot_f, fill=DIM, anchor="mm")
            y += ROW_H
            bench_drawn = True
        if idx % 2 == 0:
            d.rectangle((0, y, W, y + ROW_H), fill=ROW_ALT)
        mid = y + ROW_H / 2
        d.text((CX, mid), SLOT_LABELS.get(slot, slot), font=slot_f, fill=DIM, anchor="mm")
        dim_row = slot in BENCH_SLOTS
        for side, pl in (("L", pa), ("R", pb)):
            if pl is None:
                continue
            tag = " - ".join(t for t in (pl.get("team"), pl.get("pos")) if t)
            status = label(pl["status"]) if label(pl["status"]) not in ("Active",) else ""
            sub = tag + (f"  ·  {status}" if status else "")
            pts = f"{pl['points']:.2f}".rstrip("0").rstrip(".") if pl["points"] else "0"
            name_c = DIM if dim_row else TEXT
            sub_c = LOSE if status in ("OUT", "IR", "Doubtful", "Suspended") else DIM
            if side == "L":
                d.text((PAD, mid - 3), fit(pl["name"], name_f, name_w), font=name_f, fill=name_c, anchor="ls")
                d.text((PAD, mid + 3), sub, font=sub_f, fill=sub_c, anchor="lt")
                d.text((CX - slot_w / 2 - 8, mid), pts, font=pts_f, fill=name_c, anchor="rm")
            else:
                d.text((W - PAD, mid - 3), fit(pl["name"], name_f, name_w), font=name_f, fill=name_c, anchor="rs")
                d.text((W - PAD, mid + 3), sub, font=sub_f, fill=sub_c, anchor="rt")
                d.text((CX + slot_w / 2 + 8, mid), pts, font=pts_f, fill=name_c, anchor="lm")
        y += ROW_H

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def score_text(m: dict, snap: dict, now: datetime) -> str:
    diff = m["my_score"] - m["opp_score"]
    lead = "Up" if diff > 0 else "Down" if diff < 0 else "Tied"
    has_proj = m.get("my_proj") is not None
    lines = [
        f"🏈 Wk {m['week']} · {now.strftime('%-I:%M %p')} · {lead} {abs(diff):.2f}",
        f"{m['my_name']} {m['my_score']:.2f} · {m['opp_score']:.2f} {m['opp_name']}",
    ]
    if has_proj:
        lines.append(f"Proj {m['my_proj']:.1f} · {m['opp_proj']:.1f}")
    lines.append("")
    lines += side_by_side(snap, player_snapshot(m["opp_lineup"]))
    return "\n".join(lines)


def injury_changes(old: dict, new: dict) -> list[str]:
    """Injury designation changes for players on the roster both times."""
    changes = []
    for pid, p in new.items():
        prev = old.get(pid)
        if prev and label(prev["status"]) != label(p["status"]):
            changes.append(f"⚠️ {who(p)}: "
                           f"{label(prev['status'])} → {label(p['status'])}")
    return changes


def roster_changes(old: dict, new: dict) -> list[str]:
    """Players added/dropped and lineup-slot moves."""
    changes = []
    for pid, p in new.items():
        prev = old.get(pid)
        if prev is None:
            changes.append(f"➕ {who(p)} added to your roster ({label(p['status'])})")
        elif prev["slot"] != p["slot"] and p["slot"]:
            changes.append(f"🔁 {who(p)} moved {prev['slot']} → {p['slot']}")
    for pid, p in old.items():
        if pid not in new:
            changes.append(f"➖ {who(p)} left your roster")
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
        opp_snap = player_snapshot(m["opp_lineup"])
        short = (f"{m['my_name']} {m['my_score']:.2f} · {m['opp_score']:.2f} {m['opp_name']}"
                 + (f"\nProj {m['my_proj']:.1f} · {m['opp_proj']:.1f}" if m.get("my_proj") is not None else ""))
        try:
            png = render_matchup_png(m, snap, opp_snap, now)
        except Exception as e:
            print(f"couldn't draw matchup image: {e!r}", file=sys.stderr)
            png = None
        send_text(short,
                  f"🏈 Wk {m['week']}: {lead} {abs(diff):.2f} "
                  f"({m['my_score']:.2f}–{m['opp_score']:.2f})",
                  image=png, full_text=score_text(m, snap, now))
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
