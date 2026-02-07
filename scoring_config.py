"""
scoring_config.py
All knobs for scoring are centralized here so you can tune them between splits.

Best practice:
- Put “league policy” constants in one file, not scattered through code.
"""

# Core scoring constants
R_POINTS_PER_MINUTE = 0.05
ALPHA = 0.5

# Display policy
STORE_DECIMALS = 2          # stored points per winner precision
DISPLAY_ROUNDING = "int"    # "int" (required by you)

# Time handling
ROUND_MINUTES_TO = 15
MIN_DURATION_MINUTES = 15
REVIEW_THRESHOLD_MINUTES = 120  # over 2 hours -> REVIEW flag

# Eligibility gates
MIN_ELIGIBLE_HOURS = 7
MIN_ELIGIBLE_NIGHTS = 3

# Shrinkage stabilizer (keep visible & editable)
H0_HOURS = 6

# Leaderboard output size
TOP_N_ELIGIBLE = 5
