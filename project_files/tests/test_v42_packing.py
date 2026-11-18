"""Version 4.2: packed columns (rle, d16, d8). Every decoded value must stay exactly the same."""
import base64
import json

import numpy as np
import pytest

import site_data as sd
from tests import v4_helpers as H

playwright = pytest.importorskip("playwright.sync_api")
TYPES = ["ms", "i32", "u16", "str", "u8", "bool"]


def plain(kind, values):
    arr = np.array(values, dtype=sd.RAW_DTYPE[kind])
    col = {"type": kind, "b64": base64.b64encode(arr.tobytes()).decode("ascii")}
    if kind == "str":
        col["dict"] = [f"s{i}" for i in range(max(1, int(arr[arr != sd.STR_NULL].max()) + 1 if len(arr) and (arr != sd.STR_NULL).any() else 1))]
    return col


def limits(kind):
    return {"ms": (-2000000000, 2000000000), "i32": (-2000000000, 2000000000), "u16": (0, 65534), "str": (0, 60),
            "u8": (0, 254), "bool": (0, 1)}[kind]


def make_cases(kind, rng):
    lo, hi = limits(kind)
    null = sd.RAW_NULL[kind]
    cases = {}
    cases["one"] = [lo]
    cases["one_null"] = [null]
    cases["empty"] = []
    cases["long_run"] = [min(hi, lo + 7)] * 500
    cases["nulls_edges"] = [null, null, lo + 1, lo + 2, null, hi, null, null, lo + 3, null]
    cases["walk"] = list(np.clip(lo + 1000 + np.cumsum(rng.integers(-3, 4, 400)), lo, hi))
    cases["walk_wide"] = list(np.clip(lo + 50000 + np.cumsum(rng.integers(-300, 301, 400)), lo, hi))
    cases["jumps"] = [int(x) for x in rng.integers(lo, hi + 1, 200)]
    cases["max_steps"] = [lo, min(hi, lo + 127), min(hi, lo + 128), min(hi, lo + 255), lo, min(hi, lo + 32767), min(hi, lo + 32768), lo]
    mixed = list(np.clip(lo + 100 + np.cumsum(rng.integers(-2, 3, 300)), lo, hi))
    for i in rng.integers(0, 300, 20):
        mixed[int(i)] = null
    cases["mixed_nulls"] = mixed
    return {k: [int(v) if v == null else int(min(max(v, lo), hi)) for v in vals] for k, vals in cases.items()}


@pytest.mark.parametrize("kind", TYPES)
def test_round_trip_every_type(kind):
    rng = np.random.default_rng(42)
    for name, values in make_cases(kind, rng).items():
        col = plain(kind, [int(v) for v in values])
        raw = base64.b64decode(col["b64"])
        packed = sd.pack_column(col)
        assert sd.unpack_column(packed) == raw, (kind, name, packed.get("enc"))
        for forced in ("rle", "d16", "d8"):
            if not values:
                continue
            arr = np.frombuffer(raw, dtype=sd.RAW_DTYPE[kind])
            cand = sd._pack_rle(arr) if forced == "rle" else sd._pack_delta(arr, forced, sd.RAW_NULL[kind])
            assert sd.unpack_column({"type": kind, **cand}) == raw, (kind, name, forced)


def test_f64_and_empty_columns_stay_plain():
    col = {"type": "f64", "b64": base64.b64encode(np.arange(100, dtype="<f8").tobytes()).decode()}
    assert sd.pack_column(col) is col
    empty = {"type": "ms", "b64": ""}
    assert sd.pack_column(empty) is empty


def size_of(col):
    return sum(len(col.get(k, "")) for k in ("b64", "runs", "exc"))


def test_choice_is_the_smallest_and_ties_prefer_plain_then_rle_then_d16_then_d8():
    rng = np.random.default_rng(7)
    for kind in TYPES:
        for name, values in make_cases(kind, rng).items():
            col = plain(kind, [int(v) for v in values])
            best = sd.pack_column(col)
            if not values:
                continue
            arr = np.frombuffer(base64.b64decode(col["b64"]), dtype=sd.RAW_DTYPE[kind])
            sizes = {"plain": len(col["b64"]), "rle": size_of(sd._pack_rle(arr)),
                     "d16": size_of(sd._pack_delta(arr, "d16", sd.RAW_NULL[kind])), "d8": size_of(sd._pack_delta(arr, "d8", sd.RAW_NULL[kind]))}
            order = ["plain", "rle", "d16", "d8"]
            want = min(order, key=lambda k: (sizes[k], order.index(k)))
            assert best.get("enc", "plain") == want, (kind, name, sizes)
    # a column where nothing helps stays plain, a constant column picks rle, a slow ramp picks d8
    assert "enc" not in sd.pack_column(plain("u16", [10, 60000, 5, 40000]))
    assert sd.pack_column(plain("u16", [5] * 300))["enc"] == "rle"
    assert sd.pack_column(plain("i32", list(range(1000, 3000))))["enc"] in ("d8", "rle")


def test_every_part_file_decodes_alone():
    import pandas as pd
    n = 40
    df = pd.DataFrame({"t": np.arange(n) * 0.25, "driver_number": [1] * 20 + [44] * 20, "speed": np.arange(n) % 7})
    files = sd.table_files("x", "car_data", df, limit=330)
    assert len(files) >= 2
    got = []
    for _, text in files:
        obj = json.loads(text[text.index(", {") + 2:text.rindex(");")])
        got.append(obj)
        for col in obj["cols"].values():
            sd.unpack_column(col)
    assert sum(o["n"] for o in got) == n


@pytest.fixture(scope="module")
def pw():
    with playwright.sync_playwright() as p:
        yield p


def py_values(col):
    kind = col["type"]
    raw = np.frombuffer(sd.unpack_column(col), dtype=sd.RAW_DTYPE[kind])
    null = sd.RAW_NULL[kind]
    if kind == "ms":
        return [None if v == null else int(v) / 1000 for v in raw]
    if kind == "bool":
        return [None if v == 2 else bool(v) for v in raw]
    if kind == "str":
        return [None if v == null else col["dict"][v] for v in raw]
    return [None if v == null else int(v) for v in raw]


def test_javascript_decodes_packed_and_plain_columns_like_python(pw):
    rng = np.random.default_rng(3)
    items = []
    for kind in TYPES:
        for name, values in make_cases(kind, rng).items():
            col = plain(kind, [int(v) for v in values])
            items.append(col)
            packed = sd.pack_column(col)
            items.append(packed)
            if values:
                arr = np.frombuffer(base64.b64decode(col["b64"]), dtype=sd.RAW_DTYPE[kind])
                items.append({"type": kind, **({"dict": col["dict"]} if "dict" in col else {}), **sd._pack_rle(arr)})
                items.append({"type": kind, **({"dict": col["dict"]} if "dict" in col else {}), **sd._pack_delta(arr, "d16", sd.RAW_NULL[kind])})
                items.append({"type": kind, **({"dict": col["dict"]} if "dict" in col else {}), **sd._pack_delta(arr, "d8", sd.RAW_NULL[kind])})
    browser, page, _, errors = H.page_session(pw)
    page.goto("file://" + str(H.PAGE))
    page.wait_for_function("window.F1DATA")
    got = page.evaluate("""(items) => items.map((c) => Array.from(F1DATA.decodeColumn(c), (x) => (typeof x === 'number' && x !== x ? null : x)))""", items)
    browser.close()
    assert len(got) == len(items) and not errors
    for col, js in zip(items, got):
        assert py_values(col) == js, (col["type"], col.get("enc"))
