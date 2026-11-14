"""Checks required by the assignment + parser tests.  Run:  python -m pytest -q"""
import math
import pathlib

import pytest

import app

S = pathlib.Path(__file__).resolve().parent.parent / "samples"
BASE = dict(beta_u=1.2254, tax=0.255285, erp=0.065, d=0.30, spread_usd=0.0111)


def close(a, b, tol=1e-12):
    return math.isclose(a, b, abs_tol=tol)


@pytest.mark.parametrize("method", ["exact", "additive"])
def test_equal_yields_equal_results(method):
    r = app.compute(**BASE, y_us=0.0514, y_fc=0.0514, method=method)
    for k in ["ke", "kd", "wacc"]:
        assert close(r[f"{k}_usd"], r[f"{k}_fc"])


@pytest.mark.parametrize("method", ["exact", "additive"])
def test_zero_debt_wacc_equals_equity_cost(method):
    r = app.compute(**{**BASE, "d": 0.0}, y_us=0.0514, y_fc=0.1534, method=method)
    assert close(r["wacc_usd"], r["ke_usd"]) and close(r["wacc_fc"], r["ke_fc"])


def test_fc_spread_override_leaves_usd_unchanged():
    a = app.compute(**BASE, y_us=0.0514, y_fc=0.0286)
    b = app.compute(**BASE, y_us=0.0514, y_fc=0.0286, fc_spread_override=0.03)
    for k in ["ke_usd", "kd_usd", "wacc_usd", "beta_L"]:
        assert close(a[k], b[k])
    assert not close(a["wacc_fc"], b["wacc_fc"])


def test_hand_example():
    # Software, US tax 25.5285%, ERP 6.5%, D/V 30%, US 10Y 5.145%, Japan 10Y 2.8616%, BBB 1.11%
    r = app.compute(1.225392, 0.255285, 0.065, 0.30, 0.0111, 0.05145, 0.028616)
    de = 0.3 / 0.7
    bl = 1.225392 * (1 + (1 - 0.255285) * de)
    ke = 0.05145 + bl * 0.065
    kd = 0.05145 + 0.0111
    assert close(r["ke_usd"], ke)
    assert close(r["wacc_usd"], 0.7 * ke + 0.3 * (1 - 0.255285) * kd)
    assert close(r["ke_fc"], (1 + ke) * 1.028616 / 1.05145 - 1)


def test_parse_betas_sample():
    df, date = app.parse_betas_xls((S / "betas.xls").read_bytes())
    v = df.loc[df.industry == "Software (System & Application)", "unlevered_beta"].iloc[0]
    assert abs(v - 1.2254) < 1e-3 and date == "2026-01-05"


def test_parse_ratings_sample():
    v, asof = app.parse_ratings_html((S / "ratings.html").read_text())
    assert abs(v - 0.0111) < 1e-9 and asof == "January 2026"


def test_parse_te_sample():
    d = app.parse_te_html((S / "te_bonds.html").read_text())
    assert abs(d["United States"][0] - 0.05145) < 1e-9 and "Japan" in d and "Euro Area" in d


def test_parse_oecd_csv():
    hdr = ("STRUCTURE,STRUCTURE_ID,STRUCTURE_NAME,ACTION,REF_AREA,Reference area,FREQ,Frequency of observation,"
           "MEASURE,Measure,TARGETING,Targeting,UNIT_MEASURE,Unit of measure,SECTOR,Institutional sector,"
           "DIV_TREATMENT,Dividend treatment,RATE_STRUCTURE,Rate structure,TAX_BASE,Tax base,TIME_PERIOD,"
           "Time period,OBS_VALUE,Observation value,OBS_STATUS,Observation status,UNIT_MULT,Unit multiplier,"
           "DECIMALS,Decimals")
    row = ("DATAFLOW,X,Y,I,{c},{n},A,Annual,{m},M,{t},T,PT,P,S13,G,_Z,N,_Z,N,_Z,N,{y},,{v},,A,N,0,U,2,T")
    lines = [hdr,
             row.format(c="USA", n="United States", m="CIT_C", t="ST", y=2025, v=25.569714),
             row.format(c="USA", n="United States", m="CIT_C", t="ST", y=2026, v=25.5285),
             row.format(c="USA", n="United States", m="CIT_C", t="TA", y=2026, v=25.303796),
             row.format(c="USA", n="United States", m="CIT", t="ST", y=2026, v=21),
             row.format(c="TUR", n="Türkiye", m="CIT_C", t="ST", y=2025, v=25)]
    rates, _ = app.parse_oecd_csv("\n".join(lines))
    assert rates["United States"] == (0.255285, 2026) and rates["Turkey"][0] == 0.25
