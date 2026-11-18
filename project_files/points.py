"""Points tables by season and the result status rule."""
import config


def _lookup(table, season):
    keys = [k for k in table if k <= season]
    return table[max(keys)] if keys else []


def points_table(season, session_name="Race"):
    """Points for positions 1, 2, 3... in a Race or a Sprint of that season."""
    if session_name == "Sprint":
        return list(_lookup(config.SPRINT_POINTS_BY_SEASON, season))
    return list(_lookup(config.POINTS_BY_SEASON, season))


def points_for_position(season, session_name, position):
    table = points_table(season, session_name)
    if position is None or position < 1 or position > len(table):
        return 0
    return table[int(position) - 1]


def fastest_lap_point(season, session_name="Race"):
    """True when the fastest lap earns a point for a driver in the top 10 in this session."""
    return session_name == "Race" and season in config.FASTEST_LAP_POINT_SEASONS


def classify_status(text):
    """Return (dnf, dns, dsq) from a FastF1 results Status text."""
    s = (text or "").strip()
    if s == "Disqualified":
        return False, False, True
    if s in ("Did not start", "Withdrew"):
        return False, True, False
    if s == "Finished" or s.startswith("+") or s == "Lapped":
        return False, False, False
    return True, False, False
