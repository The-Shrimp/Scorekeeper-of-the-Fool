# Scorekeeper-of-the-Fool

Discord bot for tracking competitive game-night scoring, scheduling events, and managing player stats across seasonal splits. Built with `discord.py`, `SQLite`, and `Polars`.

## Features

- Competitive scoring with split-based leaderboards and per-player stats
- Scheduling and RSVP tracking for game nights
- Player aliases for cleaner display names
- Soft-delete review flow with `/undo`, `/undo_last`, and `/recover`
- Immutable correction flow with `/editgame`
- Game-name normalization with canonical names and alias mapping
- Derived attendance recomputation separated from RSVP intent
- Council quick-reference help via `/scoring_help` and `/audit_help`
- Legacy CSV scoring commands kept for archival use

## Setup

1. Clone the repository.
2. Create a `.env` file in the project root:

```env
DISCORD_BOT_TOKEN=your_token_here
SECRET=your_secret
APP_ID=your_app_id
PUBLIC_KEY=your_public_key
SERVER_ID=your_guild_id
```

3. Install dependencies:

```bash
pip install discord.py python-dotenv polars
```

4. Run the bot:

```bash
python bot.py
```

The SQLite database at `data/bot.db` is created automatically on first run.

## Commands

### Competitive Scoring

| Command | Access | Description |
|---|---|---|
| `/loggame game:<name> minutes:<int> players:<roster> winners:<roster> [date] [notes]` | Council | Log a game. Rosters accept `@mentions` or comma-separated aliases/display names. |
| `/editgame <game_id> [game] [minutes] [players] [winners] [date] [notes]` | Council | Create an immutable corrected replacement for an existing game. |
| `/leaderboard` | Anyone | Show the current split leaderboard. |
| `/mystats` | Anyone | Show your current split stats. |
| `/stats <player>` | Anyone | Show another player's current split stats. |
| `/splitstats` | Anyone | Show aggregate split stats. |
| `/undo <game_id>` | Council | Move an active game to review. |
| `/undo_last` | Council | Move the most recent active game in the current split to review. |
| `/recover <game_id>` | Council | Restore a reviewed game to active scoring. |
| `/reviewqueue [limit]` | Council | List recently soft-deleted games in review. |
| `/reviewgame <game_id>` | Council | Inspect a soft-deleted game in detail. |
| `/gameinfo <game_id>` | Council | Inspect a game record and its correction lineage. |
| `/auditgame <game_id> [limit]` | Council | Show recent audit entries related to a game id. |

### Game Name Normalization

| Command | Access | Description |
|---|---|---|
| `/addgame <name>` | Council | Register a canonical game name. |
| `/mapgamealias <raw_name> <canonical_name>` | Council | Map a typed variant to a canonical game name. |
| `/renamegame <old_name> <new_name>` | Council | Rename a canonical game and update active historical records. |

### Scheduling and RSVP

| Command | Access | Description |
|---|---|---|
| `/schedulegamenight [date] [time] [attire] [location] [notes]` | Council | Post the weekly game-night invite and enable RSVP reactions. |

### Aliases

| Command | Access | Description |
|---|---|---|
| `/setalias <alias>` | Anyone | Set your display alias for scorekeeping and RSVPs. |

### Council Help

| Command | Access | Description |
|---|---|---|
| `/scoring_help` | Council | Short ephemeral scoring workflow reference. |
| `/audit_help` | Council | Short ephemeral audit/review workflow reference. |

### Informational

| Command | Access | Description |
|---|---|---|
| `/introductions` | Anyone | Show the introduction message. |

### Legacy Scoring

| Command | Access | Description |
|---|---|---|
| `/updatescore <name> <amount> <game> [date] [notes]` | Anyone | Append a score entry to the legacy CSV. |
| `/scoreboardleaders` | Anyone | Show leaders from legacy data. |
| `/scoreboard <year> <split>` | Anyone | Show all legacy scores for a given year and split. |
| `/legacystats <name> [year] [split]` | Anyone | Show legacy player stats. |

## Current Scoring Workflow

### Logging a game

1. Council runs `/loggame`.
2. The bot resolves players and winners from mentions or aliases.
3. Minutes are rounded to the nearest 15.
4. Points are computed and the game is written to `game_instances`.
5. Attendance is recomputed for that date.

### Correcting a game

1. Council runs `/editgame <game_id> ...`.
2. The bot loads the original row.
3. A replacement row is inserted with `supersedes_game_id = original`.
4. The original row is marked with `superseded_by_game_id = replacement`.
5. Leaderboards and stats use only non-superseded rows.

### Removing and restoring a game

1. Council runs `/undo` or `/undo_last`.
2. The row moves from `game_instances` to `game_instances_review`.
3. Attendance is recomputed for that date.
4. Council can inspect the removed row with `/reviewgame`.
5. Council can restore it with `/recover`.

## Database Notes

### `game_instances`

Stores active and historical game rows.

Important columns:

- `players_json`
- `winners_json`
- `pool_points`
- `points_per_winner`
- `supersedes_game_id`
- `superseded_by_game_id`

Only rows where `superseded_by_game_id IS NULL` are counted in active scoring.

### `game_instances_review`

Stores soft-deleted rows waiting in review.

Important extra columns:

- `deleted_at_utc`
- `deleted_by`

### `derived_attendance`

Stores attendance derived from active logged games. This is separate from RSVP intent.

Important columns:

- `date_iso`
- `discord_id`
- `source`
- `source_ref`

### `rsvps`

Stores user RSVP intent (`yes`, `maybe`, `unavailable`, `none`) independently of derived attendance.

## Files

- `bot.py`: entry point and small bot-facing commands
- `competitive_scoring.py`: competitive scoring commands
- `db.py`: SQLite schema and data access
- `schedule.py`: scheduling and CSV updates
- `rsvp.py`: reaction handlers and reconciliation
- `aliases.py`: alias management and roster resolution
- `discord_command_guide.txt`: Discord-ready pasteable command guide

## Current State

Implemented recently:

- `/editgame` immutable correction workflow
- `/mapgamealias`, `/reviewqueue`, `/reviewgame`, and `/gameinfo`
- Derived attendance recomputation
- Council quick-help commands

Recommended next work:

- Add audit-inspection commands such as `/auditgame`
- Add automated regression tests for scoring, review, and correction flows
- Decide whether to further demote or hide legacy scoring commands
