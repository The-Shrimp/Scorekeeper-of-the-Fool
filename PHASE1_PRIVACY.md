# Phase 1 privacy and local storage

The controlled local cutover is complete; the scheduled bot uses private storage.
Publication and the Cloudflare backend remain separate review steps. This iteration does not
implement the future single authoritative Cloudflare backend/database, Discord OAuth, sessions or website membership. D1 remains a candidate pending compatibility review; local storage is a temporary privacy bridge and backup safeguard.

Public leaderboard data contains split/year, rank, listed alias (otherwise an
independent stable `Player #`) and total score. Raw identity mappings, attendance,
RSVPs, schedules, venue details, notes and game-participant trails stay private.
The public-number registry is `public-identities.db` in private storage. Back it
up with the source records; losing it can change generic player numbers.

Private storage is selected by ignored `.private-config.json` or the explicit
`SCOREKEEPER_PRIVATE_DATA_DIR` environment variable. Use an operator-chosen
absolute directory outside both repositories. Never commit that configuration,
`.env`, databases, mappings, raw CSVs or source-history bundles. The environment
override explicitly selects private storage; do not set it before cutover.

## Pending existing installation

With `storage_ready: false` and `legacy_runtime: true`, a newly started process
uses the original `data/bot.db`, `data/legacy` and root schedule CSVs. Missing
originals cause startup to fail rather than create an empty replacement. The
selection is frozen for that process; changing configuration cannot switch it
live. This compatibility mode prevents selecting the initial stale private copy.
The existing process keeps its original imported modules and files.

`announcements.py` remains the original local module because RSVP and scheduling
import it during events. The tested privacy replacement is staged at
`pending_cutover/announcements.py`; migration installs it only with the bot stopped.
Until cutover, new announcement privacy protections are not active. Do not publish
the bot iteration until the replacement has been installed and included as
`announcements.py` in the reviewed source snapshot.

## Controlled cutover (requires operator approval)

1. Identify the actual launcher and its Python child; stop the launcher cleanly.
   Disable its restart action for the maintenance window. Check that both bot
   processes exited. Do not stop unrelated Python processes.
2. Run `python migrate_private_storage.py` for read-only process verification.
   The script refuses mutation if any `bot.py` process remains or process lookup
   cannot be verified. On Windows the launcher and child may both be counted.
3. Run `python migrate_private_storage.py --apply` while the launcher stays stopped.
   This takes a fresh consistent SQLite snapshot, checks integrity and table-row
   digests, copies the latest CSV/alias inputs with checksum verification, retains
   previous private copies and source originals, and installs the pending module.
   Only then does it atomically set `storage_ready: true`. It never starts the bot.
4. Restart once using the confirmed existing environment/launcher. Verify
   startup, split score totals, aliases, Council-only legacy commands, ephemeral
   details, member-only announcement permissions and storage paths before allowing
   changes. A short maintenance window is expected; its length is unmeasured.
5. If startup fails before any new writes, stop it and use the preserved originals
   and source backup for rollback. After any new writes, preserve a fresh consistent
   private database and CSV snapshot and reconcile/copy the latest data back before
   selecting original storage. Never overwrite it with the initial stale backup.
   Keep `public-identities.db` to preserve public numbers. Do not change a running
   process's configuration as a storage switch.

The migration does not move/delete the original files, rewrite Git history,
stop/start processes, push code, deploy the site or change Discord permissions.
Review process races and launcher restart behavior during the controlled window.

## Transitional public export

`python public_projection.py --output <website>/data/leaderboard.json` generates
aggregate data only. Preserved live website CSVs in `website-legacy-originals`
take precedence for already published splits. Separate bot legacy CSVs remain
unchanged in `legacy`; they supply splits that the website did not contain.
No conflict merge or identity inference is performed. Reconcile source differences
privately before replacing this transitional export with the shared backend.
Current competitive SQLite scores are not exported by this legacy-only script.
Individual player-history visibility remains undecided.

Run `python -B -m unittest discover -s tests -v` using isolated source copies and
synthetic data. Tests never log in to Discord. Use the existing bot environment.

## Publication and history

Removing tracked CSVs in a new commit does not remove them or mapping literals
from older commits. Public repositories and deployed assets remain exposed until
the separately reviewed publication/history containment steps are completed.
No history cleanup, force push, ref deletion, visibility change or credential
rotation is authorized by this local phase. Preserve verified bundles and working
copies before reviewing an exact rewrite with collaborators.
