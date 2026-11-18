OPENF1_BASE = "https://api.openf1.org/v1"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPEN_METEO_HIST_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
DEFAULT_YEAR = 2025
DEFAULT_COUNTRY = "Netherlands"
SESSION_NAME = "Race"
REQUEST_SPACING_S = 2.5          # wait between any two HTTP requests
REQUEST_TIMEOUT_S = 60
REQUEST_RETRIES = 4
RETRY_WAIT_S = 20                # doubled after each failed try
CHUNK_MINUTES = 15               # time window per request for car_data and location
N_MINISECTORS = 18
MAP_HOLD_S = 5.0                 # hold a car's last map position if samples are further apart than this
FEED_LENGTH = 30
FORECAST_HOURS = 3
OUT_TIMEOUT_S = 240.0
OUT_MIN_OTHERS = 5
PIT_LOSS_MIN_STOPS = 3
PIT_LOSS_CLEAN_LAPS = 5
UNDERCUT_GAP_S = 2.0
RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
FASTEST_LAP_POINT_LAST_SEASON = 2024
REPLAY_SPEEDS = [1, 5, 20, 60]
POLL_MS = 500
HOST = "127.0.0.1"
PORT = 8000
DRS_ON = [10, 12, 14]
DRS_ELIGIBLE = [8]

# Constants written in the instructions as numbers or words inside the text, kept here so no
# magic numbers or strings sit elsewhere in Python.
DRS_STALE_S = 5.0                # car_data rows older than this give DRS n/a
MINISECTOR_MAX_GAP_S = 3.0       # longer gaps between samples stop the mini-sector calculation
REPLAY_PAD_S = 60.0              # replay starts this long before the race start and ends this long after the last lap
MAX_BUNDLE_FILE_MB = 20
ROWS_PER_SAMPLE_FILE = 400000
DRY_COMPOUNDS = ["SOFT", "MEDIUM", "HARD"]
WET_COMPOUNDS = ["INTERMEDIATE", "WET"]
PACE_MAX_DRIVERS = 4
PACE_LOW_PERCENTILE = 5
PACE_BELOW_S = 1.0
PACE_ABOVE_S = 8.0
STATE_MEDIAN_BUDGET_S = 0.150

# Version 3
IDLE_SHUTDOWN_MIN = 30
FASTF1_CACHE_DIR = "fastf1_cache"
ARCHIVE_DIR = "archive"
ARCHIVE_FIRST_SEASON = 2021
CLASSIC_FIRST_SEASON = 2007
PROBE_YEAR = 2024
PROBE_ROUND = 1
FASTF1_RATE_WAIT_S = 900
FASTF1_RATE_MAX_WAITS = 16
FASTF1_SESSION_TIMEOUT_S = 1800
ARCHIVE_MIN_FREE_GB = 15
ARCHIVE_STOP_FREE_GB = 5
ARCHIVE_SAFETY_FREE_GB = 20
AUTO_ARCHIVE_CONFIRM_SESSIONS = 3
AUTO_ARCHIVE_CONFIRM_TIMEOUT_S = 5400
SETTINGS_FILE = "data/settings.json"
ARCHIVE_PHASES = [["Race"], ["Sprint"]]
REPLAY_SESSION_NAMES = ["Race", "Sprint"]
JOLPICA_SPACING_S = 8.0
JOLPICA_PAGE = 100
BUNDLE_CACHE_SIZE = 2
TRACE_POINTS = 200
POINTS_BY_SEASON = {2007: [10, 8, 6, 5, 4, 3, 2, 1], 2010: [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]}
FASTEST_LAP_POINT_SEASONS = [2019, 2020, 2021, 2022, 2023, 2024]
SPRINT_POINTS_BY_SEASON = {2021: [3, 2, 1], 2022: [8, 7, 6, 5, 4, 3, 2, 1]}

# Version 3 values that the instructions describe in words
PROBE_TIMEOUT_S = 120
RETRY_AFTER_FAILURE_S = 60
JOB_POLL_S = 1.0
HEALTH_WAIT_S = 30
SESSION_CODES = {"Race": "R", "Sprint": "S", "Qualifying": "Q", "Sprint Qualifying": "SQ", "Sprint Shootout": "SS",
                 "Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3"}
PROBE_MIN_INTERVAL_S = 600
PIT_DURATION_MAX_S = 120
DEFAULT_RACE_ID = "of_9920"
IDLE_CHECK_S = 30
LOCAL_HOSTS = ["127.0.0.1", "::1", "localhost"]
RECENT_ERRORS = 5
