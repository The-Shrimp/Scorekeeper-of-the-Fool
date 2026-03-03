# Scorekeeper-of-the-Fool — Discord Game Night Bot

A Discord bot for tracking competitive game night scores, scheduling game nights, and managing player stats across seasonal splits. Built with `discord.py`, `SQLite`, and `Polars`.

---

## Features

- **Competitive Scoring System** — Log games, track points via a pool-based formula with Bayesian shrinkage-adjusted rankings
- **Seasonal Splits** — Scores are organized into Split 1 (Jan–Jun) and Split 2 (Jul–Dec) per year
- **Scheduling & RSVP** — Schedule game nights and track attendance via ✅ / ❔ / ❌ emoji reactions (mutual exclusion enforced)
- **Player Aliases** — Each player can set a display alias used in announcements and stats
- **Safe Delete** — `/undo` moves games to a review table rather than permanently deleting; `/recover` restores them
- **Audit Log** — Every write operation (game inserts, deletes, recoveries, alias changes) is persisted to an audit log
- **Game Name Normalization** — Canonical game names registered via `/addgame`; free-text variants mapped to them at log time
- **Expanded Stats** — Per-player win rate, most-played game, points per night; split-level summaries via `/splitstats`
- **Legacy Scoring** — Archival CSV-based scoring system from earlier seasons, preserved in `data/legacy/`

---

## Prerequisites

- Python 3.8+
- Discord bot token (with all intents enabled)
- Discord server with:
  - Role: **"Game Night Council"** (for admin commands)
  - Role: **"Game Night"** (mentioned in announcements)
  - Channel: **"game-night-announcement-board"**
- Required Python packages:
  ```
  discord.py
  python-dotenv
  polars
  ```

---

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/The-Shrimp/Scorekeeper-of-the-Fool.git
   cd Scorekeeper-of-the-Fool
   ```

2. **Create a `.env` file** in the project root:
   ```env
   DISCORD_BOT_TOKEN=your_token_here
   SECRET=your_secret
   APP_ID=your_app_id
   PUBLIC_KEY=your_public_key
   SERVER_ID=your_guild_id
   ```

3. **Install dependencies**
   ```bash
   pip install discord.py python-dotenv polars
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

   The SQLite database (`data/bot.db`) and all required tables are created automatically on first run.

---

## Commands

### Competitive Scoring (2026+ System)

| Command | Access | Description |
|---|---|---|
| `/loggame game:<name> minutes:<int> players:<mentions> winners:<mentions> [date] [notes]` | Council | Log a game; name is normalized against canonical game list |
| `/leaderboard` | Anyone | Display the current split leaderboard with eligibility and adjusted totals |
| `/mystats` | Anyone | View your own stats: points, hours, nights, win rate, most played game, pts/night |
| `/stats <player>` | Anyone | View another player's stats for the current split |
| `/splitstats` | Anyone | Aggregate split summary: total games, players, hours, most played game, busiest night |
| `/undo <game_id>` | Council | Soft-delete a game by ID (moved to review table, recoverable) |
| `/undo_last` | Council | Soft-delete the most recent game in the current split |
| `/recover <game_id>` | Council | Restore a game from the review table back to the leaderboard |

### Game Name Normalization

| Command | Access | Description |
|---|---|---|
| `/addgame <name>` | Council | Register a canonical game name for normalization |
| `/renamegame <old_name> <new_name>` | Council | Rename a canonical game and update all historical records |

### Scheduling & RSVP

| Command | Access | Description |
|---|---|---|
| `/schedulegamenight [date] [time] [attire] [location] [notes]` | Council | Post a game night announcement with ✅ / ❔ / ❌ RSVP reactions |

Reactions are mutually exclusive — adding one removes any conflicting reaction the user previously set. Attendance is synced to the schedule CSV both live and on bot startup via reconciliation.

### Aliases

| Command | Access | Description |
|---|---|---|
| `/setalias <alias>` | Anyone | Set your display name for game night records (stored in SQLite) |

### Legacy Scoring (Archival)

| Command | Access | Description |
|---|---|---|
| `/updatescore <name> <amount> <game> [date] [notes]` | Anyone | Append a score entry to the legacy CSV |
| `/scoreboardleaders` | Anyone | Display the top and runner-up players from legacy data |
| `/scoreboard <year> <split>` | Anyone | Show all legacy scores for a given year and split |
| `/legacystats <name> [year] [split]` | Anyone | View legacy stats for a player |

All legacy commands respond ephemerally. Legacy data lives in `data/legacy/`.

### Informational

| Command | Access | Description |
|---|---|---|
| `/introductions` | Anyone | Display the bot introduction message |

---

## File Map

```
Scorekeeper-of-the-Fool/
│
├── bot.py                        # Entry point — creates bot, registers all modules, syncs slash commands
├── config.py                     # Loads DISCORD_BOT_TOKEN from .env
├── constants.py                  # Shared constants: emoji (✅ ❔ ❌), channel names, role names
│
├── db.py                         # SQLite access layer — game_instances, aliases, review, audit log, normalization
│
├── aliases.py                    # /setalias command; alias resolution for player name lookups
├── rsvp.py                       # Reaction event handlers (add/remove); startup reconciliation
├── schedule.py                   # /schedulegamenight; schedule CSV creation and attendance tracking
│
├── competitive_scoring.py        # /loggame, /leaderboard, /mystats, /stats, /splitstats,
│                                 # /undo, /undo_last, /recover, /addgame, /renamegame
├── scoring_engine.py             # Pure scoring logic — compute_points(), aggregate_split(),
│                                 # compute_leaderboard(), compute_player_detail(), compute_split_summary()
├── scoring_config.py             # All tunable scoring parameters (rates, thresholds, shrinkage)
│
├── scoring.py                    # Legacy CSV-based scoring commands (all ephemeral)
├── split_ids.py                  # Split ID encoding: YYYY-S1 / YYYY-S2
├── utils_split.py                # Split membership logic (Split1: Jan–Jun, Split2: Jul–Dec)
├── utils_time.py                 # Time parsing helpers (upcoming Saturday, parse_time_input)
│
├── pit.py                        # Stub — Pit board game scoring (incomplete)
├── main_old.py                   # Deprecated monolithic version (kept for reference)
├── introduction.txt              # Text for /introductions command
│
├── data/
│   ├── bot.db                    # SQLite database (auto-created, gitignored)
│   └── legacy/
│       ├── 2024_Split1.csv       # Legacy scoring data
│       ├── 2024_Split2.csv       # Legacy scoring data
│       └── 2025_Split2.csv       # Legacy scoring data
│
└── 2026_Split1_gamenights.csv    # Game night schedule — 2026 Split 1
```

### Logic Flow

**Bot Startup**
```
bot.py
  └── config.py                           (load .env secrets)
  └── aliases.register()                  → db.init_db()  (creates all tables)
  └── scoring.register()
  └── schedule.register()
  └── competitive_scoring.register()
  └── rsvp.register()
  └── bot.tree.sync()                     (sync slash commands to Discord)
  └── rsvp.reconcile_active_invitation()  (replay missed reactions)
```

**Logging a Game**
```
/loggame → competitive_scoring.loggame()
  └── Parse player/winner mentions from strings
  └── scoring_engine.validate_game_instance()
  └── scoring_engine.round_minutes_to_nearest_15()
  └── db.normalize_game_name()            (lookup canonical name; unchanged if not mapped)
  └── scoring_engine.compute_points()     → pool & points_per_winner
  └── db.insert_game_instance()
  └── db.write_audit("INSERT_GAME")
  └── Reply with game summary + ID (+ normalization note if name was remapped)
```

**Leaderboard Generation**
```
/leaderboard → competitive_scoring.leaderboard()
  └── db.fetch_game_instances_for_split(split_id)
  └── scoring_engine.aggregate_split()       → per-player totals
  └── scoring_engine.compute_leaderboard()   → shrinkage ranking
  └── Display ranked table
```

**Soft Delete / Recovery**
```
/undo <id> → competitive_scoring.undo()
  └── db.move_game_to_review(game_id, actor_id)
      └── Copies row to game_instances_review (with deleted_at_utc + deleted_by)
      └── Deletes from game_instances
  └── db.write_audit("SOFT_DELETE_GAME")

/recover <id> → competitive_scoring.recover()
  └── db.restore_game_from_review(game_id)
      └── Copies row back to game_instances
      └── Deletes from game_instances_review
  └── db.write_audit("RECOVER_GAME")
```

**Scheduling a Game Night**
```
/schedulegamenight → schedule.py
  └── ensure_schedule_file_for_date()    (create schedule CSV if needed)
  └── update_schedule_entry()            (mark as Scheduled)
  └── Post message in #game-night-announcement-board with ✅ / ❔ / ❌

Player reacts → rsvp.on_raw_reaction_add()
  └── Removes any competing reactions from that user
  └── schedule.update_schedule_attendance_for_member()
      └── Updates Attendees / Possible Attendees / Unavailable columns in CSV

Player removes reaction → rsvp.on_raw_reaction_remove()
  └── Checks remaining reactions for that user
  └── schedule.update_schedule_attendance_for_member() with updated status (or "none")
```

---

## Database Schema

### `game_instances`
Stores every logged game for the competitive scoring system.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Unique game ID |
| `timestamp_utc` | TEXT | When the game was logged |
| `local_date` | TEXT | Date game was played (ISO format) |
| `split_id` | TEXT | e.g. `2026-S1` |
| `game_name` | TEXT | Canonical game name |
| `duration_min` | INTEGER | Duration (rounded to nearest 15 min) |
| `players_json` | TEXT | JSON array of player Discord IDs |
| `winners_json` | TEXT | JSON array of winner Discord IDs |
| `pool_points` | REAL | Total points in the pool |
| `points_per_winner` | REAL | Points each winner receives |
| `review_flag` | INTEGER | 1 if duration > 120 min |
| `notes` | TEXT | Admin notes |
| `logged_by` | TEXT | Discord ID of the logger |
| `channel_id` | TEXT | Channel where logged |
| `message_id` | TEXT | Discord message ID |

### `game_instances_review`
Soft-deleted games — same schema as `game_instances` plus:

| Column | Type | Description |
|---|---|---|
| `deleted_at_utc` | TEXT | When the game was soft-deleted |
| `deleted_by` | TEXT | Discord ID of who deleted it |

### `player_aliases`
Maps Discord IDs to display names.

| Column | Type | Description |
|---|---|---|
| `discord_id` | TEXT PK | Discord user ID |
| `alias` | TEXT | Display name |
| `updated_at_utc` | TEXT | Last update timestamp |

### `audit_log`
Persistent record of every write operation.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp_utc` | TEXT | When the action occurred |
| `actor_discord_id` | TEXT | Who performed the action |
| `action` | TEXT | e.g. `INSERT_GAME`, `SOFT_DELETE_GAME`, `RECOVER_GAME`, `SET_ALIAS`, `RENAME_GAME` |
| `target_id` | TEXT | game_id, discord_id, etc. |
| `payload_json` | TEXT | JSON snapshot of the affected record |

### `canonical_games`
Registry of approved game names for normalization.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `canonical_name` | TEXT UNIQUE | The official game name |
| `added_at_utc` | TEXT | When added |
| `added_by` | TEXT | Discord ID of adder |

### `game_name_aliases`
Maps free-text variants to canonical game names.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `raw_name` | TEXT UNIQUE | As-typed variant (case-insensitive matched) |
| `canonical_id` | INTEGER FK | References `canonical_games.id` |
| `mapped_at_utc` | TEXT | When mapped |

---

## Scoring Formula

```
pool = R × t × (p − 1) × (p / w) ^ α

  R = 0.05 pts/min  (base rate)
  t = duration in minutes (rounded to nearest 15)
  p = number of players
  w = number of winners
  α = 0.5  (competition exponent)

points_per_winner = pool / w
```

### Leaderboard Ranking

1. Aggregate per-player: `total_points`, `total_hours`, `nights_attended`
2. Eligibility: requires **≥ 7 hours** AND **≥ 3 nights** in the split
3. Shrinkage adjustment (Bayesian):
   ```
   E_adj = (E0 × H0 + E × H) / (H0 + H)

     E0 = average raw efficiency of eligible players
     H0 = 6 hrs  (shrinkage stabilizer)
     E  = player's raw efficiency (pts/hr)
     H  = player's total hours

   adjusted_total = E_adj × H
   ```
4. Sort by `adjusted_total` DESC; tiebreakers: `total_points`, `nights`, `hours`

All constants are configurable in `scoring_config.py`.

---

## In Progress

No features are currently pending. All previously planned work has been implemented:

- ✅ `/undo` and `/undo_last` — soft-delete to `game_instances_review`; `/recover` to restore
- ✅ Audit log — all mutations written to `audit_log` table in `bot.db`
- ✅ RSVP ❌ reaction — mutual exclusion enforced; "Unavailable" column tracked in schedule CSV
- ✅ Expanded stats — win rate, most played game, points/night in `/mystats` and `/stats`; `/splitstats` command added
- ✅ Game name normalization — `/addgame`, `/renamegame`; normalization applied at log time in `/loggame`
