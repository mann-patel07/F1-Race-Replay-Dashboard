from datetime import datetime, timezone

import pytest

import archive
import forget_f1tv_signin as forget
import update_data


def index(extra_race=False):
    races = {"2021": [{"season": 2021, "round": 1, "name": "A GP", "date": "2021-03-28", "sprint_date": None, "openf1_session_key": None}],
             "2023": [{"season": 2023, "round": 1, "name": "B GP", "date": "2023-03-05", "sprint_date": None, "openf1_session_key": 777}],
             "2012": [{"season": 2012, "round": 1, "name": "C GP", "date": "2012-03-18", "sprint_date": None, "openf1_session_key": None}]}
    return {"seasons": races}


class FakeDeps:
    def __init__(self, f1tv_on=True, signed_in=True, ff_done=False, bundles=()):
        self.calls, self.f1tv_on, self.signed_in = [], f1tv_on, signed_in
        self.bundles = set(bundles)
        done = {"sessions": {archive.key_of(2021, 1, "Race"): {"status": "ok"}, archive.key_of(2023, 1, "Race"): {"status": "ok"}}}
        self.arch = done if ff_done else {"sessions": {}}

    def build_index(self): self.calls.append("index")
    def f1tv_enabled(self): return self.f1tv_on
    def probe(self): self.calls.append("probe"); return {"signed_in": self.signed_in, "detail": "test"}
    def run_archive(self): self.calls.append("archive"); return {"finished": True}
    def openf1_build(self, item): self.calls.append(f"openf1 {item['key']}"); self.bundles.add(str(item["key"]))
    def classic_build(self, s, r): self.calls.append(f"classic {s} {r}"); self.bundles.add(f"jl_{s}_{r:02d}")
    def build_site(self): self.calls.append("site")
    def make_zip(self): self.calls.append("zip"); return "share/x.zip"
    def existing_bundles(self): return self.bundles
    def archive_index(self): return self.arch


@pytest.fixture(autouse=True)
def fake_files(tmp_path, monkeypatch):
    monkeypatch.setattr(update_data, "LOCK", tmp_path / "update.lock")
    monkeypatch.setattr(update_data, "LOG", tmp_path / "update.log")
    monkeypatch.setattr(update_data, "load_index", lambda: index())


def test_step_order_with_everything_missing():
    deps = FakeDeps()
    out = update_data.update(deps, say=lambda t: None)
    assert deps.calls == ["index", "probe", "archive", "openf1 777", "classic 2012 1", "site", "zip"]
    assert out["f1tv"] == "ran" and not (update_data.LOCK.exists())


def test_no_fastf1_call_and_no_probe_when_nothing_is_missing():
    deps = FakeDeps(ff_done=True, bundles={"ff_2021_01_R", "ff_2023_01_R", "jl_2012_01"})
    update_data.update(deps, say=lambda t: None)
    assert "probe" not in deps.calls and "archive" not in deps.calls
    assert deps.calls == ["index", "site", "zip"]


def test_fastf1_is_skipped_when_the_switch_is_off_but_other_steps_run():
    deps = FakeDeps(f1tv_on=False)
    out = update_data.update(deps, say=lambda t: None)
    assert "probe" not in deps.calls and "archive" not in deps.calls
    assert "openf1 777" in deps.calls and "classic 2012 1" in deps.calls and out["f1tv"] == "off"


def test_failed_probe_skips_fastf1_and_goes_on():
    deps = FakeDeps(signed_in=False)
    out = update_data.update(deps, say=lambda t: None)
    assert "probe" in deps.calls and "archive" not in deps.calls and out["f1tv"] == "not signed in"
    assert "classic 2012 1" in deps.calls


def test_site_is_rebuilt_after_every_ten_races(monkeypatch):
    many = {"seasons": {"2012": [{"season": 2012, "round": r, "name": f"R{r}", "date": "2012-03-18", "sprint_date": None,
                                  "openf1_session_key": None} for r in range(1, 26)]}}
    monkeypatch.setattr(update_data, "load_index", lambda: many)
    deps = FakeDeps(f1tv_on=False)
    update_data.update(deps, say=lambda t: None)
    assert deps.calls.count("site") == 3
    assert sum(1 for c in deps.calls if c.startswith("classic")) == 25


def test_missing_sessions_respects_the_calendar_and_the_archive():
    now = datetime(2023, 3, 5, 12, tzinfo=timezone.utc)
    out = update_data.missing_f1tv_sessions(index(), {"sessions": {}}, now)
    assert [(s["year"], s["session"]) for s in out] == [(2021, "Race")]      # 2023 race is less than 24 hours old
    done = {"sessions": {archive.key_of(2021, 1, "Race"): {"status": "no_data"}}}
    assert update_data.missing_f1tv_sessions(index(), done, now) == []


def test_forget_refusals(tmp_path):
    idx = index()
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    marker = tmp_path / "DOWNLOAD_COMPLETE.json"
    done = {"sessions": {archive.key_of(2021, 1, "Race"): {"status": "ok"}, archive.key_of(2023, 1, "Race"): {"status": "no_data"}}}
    reasons = forget.refusal_reasons(idx, {"sessions": {}}, [marker], now)
    assert len(reasons) == 2
    assert len(forget.refusal_reasons(idx, done, [marker], now)) == 1 and "telemetry" in forget.refusal_reasons(idx, done, [marker], now)[0]
    marker.write_text("{}")
    assert len(forget.refusal_reasons(idx, {"sessions": {}}, [marker], now)) == 1
    assert forget.refusal_reasons(idx, done, [marker], now) == []


def test_openf1_step_ignores_races_not_held_yet():
    idx = {"seasons": {"2026": [{"season": 2026, "round": 1, "name": "Past", "date": "2026-03-01", "openf1_session_key": 1},
                                {"season": 2026, "round": 2, "name": "Future", "date": "2026-12-06", "openf1_session_key": 2}]}}
    out = update_data.missing_openf1(idx, set(), today="2026-11-17")
    assert [o["key"] for o in out] == [1]
