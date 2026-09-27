# ESPN Fantasy Alerts

Free phone push notifications for your ESPN fantasy football team, sent through [ntfy](https://ntfy.sh). There's no phone number, account or password to set up.

- **Injury updates 24/7**, within ~10 minutes of ESPN changing a designation. These are high priority, so they break through more loudly:
  ```
  ⚠️ Injury: Bijan Robinson (ATL): Questionable → OUT
  ```
- **Your matchup score every hour on game days** (any day one of your rostered players has a game):
  ```
  🏈 Wk 4: Up 6.50 (44.70–38.20)
  Mahesh's Squad 44.70
  Opp FC 38.20
  Up 6.50 · proj 121.3-110.0
  Top: Allen 21.4, Robinson 14.2, Chase 9.1
  ```
- **Roster moves on game days**:
  ```
  🔁 Roster update
  🔁 Ja'Marr Chase moved BE → WR
  ```

## 1. Get your ESPN details
1. Open your team on fantasy.espn.com. The URL has `leagueId=` and `teamId=`. Those are `ESPN_LEAGUE_ID` and `ESPN_TEAM_ID`.
2. **Private league?** (most are): in Chrome, press F12 → Application → Cookies → `https://fantasy.espn.com`. Copy `espn_s2` and `SWID` (keep the `{}` braces on SWID).

## 2. Set up ntfy on your phone (2 minutes)
1. Install **ntfy** from the App Store or Google Play (it's free).
2. Pick a topic name. This is your private channel, so make it long and random, because anyone who knows the name can read it. For example: `ff-mahesh-90618a5a9a07`
3. In the app, tap **+**, enter that topic name, and subscribe. Leave the server as `ntfy.sh`.
4. Optional: in the app's notification settings for that topic, pick a sound or make it bypass Do Not Disturb.

That topic name is the only thing the app needs (`NTFY_TOPIC`).

## 3a. Run it for free on GitHub Actions (nothing to keep running)
1. Push this folder to a **public** GitHub repo. Checking every 10 minutes around the clock is ~4,300 runs a month. Public repos get unlimited free Actions minutes, while private repos get about 2,000 free minutes a month. Your ESPN cookies and ntfy topic are stored as encrypted secrets and never appear in the code or logs, and the script doesn't log scores or player names.
   - Prefer a private repo? Change the cron in `.github/workflows/fantasy-texter.yml` to `"*/30 * * * *"` (~1,450 runs/month). Injury alerts then arrive within 30 minutes instead of 10.
2. Go to repo → Settings → Secrets and variables → Actions. Add these secrets:
   `ESPN_LEAGUE_ID`, `ESPN_TEAM_ID`, `ESPN_S2`, `ESPN_SWID`, `NTFY_TOPIC`
3. Go to the Actions tab → *Fantasy texter* → **Run workflow** with "test" checked. A score notification should appear on your phone within a minute.
4. After that it runs every 10 minutes on its own. Change `TIMEZONE` in the workflow file if you're not on Central time.

## 3b. Or run it on your own computer / Raspberry Pi
```bash
pip install -r requirements.txt
cp .env.example .env        # fill it in
python fantasy_texter.py --test   # sends the score now
python fantasy_texter.py --loop   # leave running
```
To try it without sending anything, set `NOTIFY_METHOD=console`.

## Settings (in `.env` or the workflow)
| Setting | Default | What it does |
|---|---|---|
| `NOTIFY_METHOD` | ntfy | `ntfy`, `email` (Gmail App Password), `twilio` (paid SMS), or `console` |
| `QUIET_HOURS_START` / `END` | 0 / 9 | No hourly score alerts from midnight to 9 AM. Injury updates still come through. |
| `ROSTER_ALERTS_EVERY_DAY` | false | Set to `true` to also get roster/lineup moves on non-game days. (Injury updates are always 24/7.) |
| `CHECK_EVERY_MIN` | 10 | How often `--loop` mode checks |
| `TIMEZONE` | America/Chicago | Used for game-day detection and timestamps |

## Notes
- Injury updates cover ESPN's designation (Active/Questionable/Doubtful/OUT/IR/Suspended), including changes that happen as one week rolls into the next.
- The very first run only records a baseline. Changes are sent from the second run on.
- ntfy.sh topics can be read by anyone who guesses the name, which is why it should be random. ntfy also offers accounts that let you reserve a topic and use an access token (`NTFY_TOKEN`) if you want it locked down.
- This uses ESPN's unofficial API through the `espn_api` package. If ESPN changes something, run `pip install -U espn_api`.
