"""
International Cost of Capital App (FINC-470)

Compares a project's cost of equity, cost of debt and WACC in U.S. dollars
and in the currency of a selected country.

Data sources (fetched live, cached for 12 hours, "Refresh data" re-fetches):
  * Unlevered industry betas ........ Damodaran, betas.xls ("Unlevered beta")
  * USD BBB default spread .......... Damodaran, ratings.html (Baa2/BBB, large non-financial firms)
  * Statutory corporate tax rates ... OECD Corporate Tax Statistics (combined statutory CIT rate)
  * 10-year government bond yields .. Trading Economics, /bonds

Core formulas (all rates in decimals):
  D/E          = d / (1 - d)                         d = target debt share D/V
  beta_L       = beta_U * [1 + (1 - t) * D/E]        (Hamada; option: no-tax version)
  k_E (USD)    = y_US + beta_L * ERP
  k_D (USD)    = y_US + s_BBB
  Conversion to foreign currency (interest-rate parity on 10y yields):
    exact:      (1 + k_FC) = (1 + k_USD) * (1 + y_FC) / (1 + y_US)
    additive:   k_FC = k_USD + (y_FC - y_US)
  k_D (FC)     = conversion of k_D(USD)            (auto)   or  y_FC + manual spread   (override)
  WACC         = (1 - d) * k_E + d * (1 - t) * k_D
"""

from __future__ import annotations

import io
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests

# --------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
BETAS_HTML_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_PUB_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/"
    "statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
OECD_SDMX_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
TE_URL = "https://tradingeconomics.com/bonds"
TE_PARTIAL_URL = "https://tradingeconomics.com/bonds?partial=markets"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 0.065
DEFAULT_DEBT_SHARE = 0.30

# OECD jurisdiction labels -> Trading Economics country names
NAME_ALIASES = {
    "türkiye": "Turkey",
    "turkiye": "Turkey",
    "china (people’s republic of)": "China",
    "china (people's republic of)": "China",
    "hong kong (china)": "Hong Kong",
    "hong kong, china": "Hong Kong",
    "macau (china)": "Macau",
    "korea": "South Korea",
    "republic of korea": "South Korea",
    "czechia": "Czech Republic",
    "slovak republic": "Slovakia",
    "viet nam": "Vietnam",
    "russian federation": "Russia",
    "chinese taipei": "Taiwan",
    "united states of america": "United States",
    "euro area": "Euro Area",
}


def canon(name: str) -> str:
    n = re.sub(r"\s+", " ", str(name)).strip()
    return NAME_ALIASES.get(n.lower(), n)


# --------------------------------------------------------------------------------------
# Offline fallback snapshots (used ONLY if a live fetch fails; flagged in the UI)
# --------------------------------------------------------------------------------------
FALLBACK_BETAS = {"date": "Damodaran file dated 2026-01-05 (snapshot)", "data": {'Advertising': 0.9301, 'Aerospace/Defense': 0.8467, 'Air Transport': 0.7041, 'Apparel': 0.758, 'Auto & Truck': 1.269, 'Auto Parts': 1.0217, 'Bank (Money Center)': 0.3411, 'Banks (Regional)': 0.2865, 'Beverage (Alcoholic)': 0.6132, 'Beverage (Soft)': 0.5557, 'Broadcasting': 0.2864, 'Brokerage & Investment Banking': 0.5808, 'Building Materials': 0.9302, 'Business & Consumer Services': 0.7736, 'Cable TV': 0.3524, 'Chemical (Basic)': 0.58, 'Chemical (Diversified)': 0.3666, 'Chemical (Specialty)': 0.7922, 'Coal & Related Energy': 1.0165, 'Computer Services': 0.9155, 'Computers/Peripherals': 1.3051, 'Construction Supplies': 1.0161, 'Diversified': 0.7889, 'Drugs (Biotechnology)': 1.0341, 'Drugs (Pharmaceutical)': 0.8862, 'Education': 0.6602, 'Electrical Equipment': 1.1478, 'Electronics (Consumer & Office)': 0.8299, 'Electronics (General)': 0.8974, 'Engineering/Construction': 1.0949, 'Entertainment': 0.7372, 'Environmental & Waste Services': 0.8145, 'Farming/Agriculture': 0.813, 'Financial Svcs. (Non-bank & Insurance)': 0.3189, 'Food Processing': 0.4574, 'Food Wholesalers': 0.6409, 'Furn/Home Furnishings': 0.6248, 'Green & Renewable Energy': 0.4633, 'Healthcare Products': 0.8291, 'Healthcare Support Services': 0.6889, 'Heathcare Information and Technology': 0.9913, 'Homebuilding': 0.785, 'Hospitals/Healthcare Facilities': 0.5517, 'Hotel/Gaming': 0.8323, 'Household Products': 0.7178, 'Information Services': 0.7372, 'Insurance (General)': 0.5636, 'Insurance (Life)': 0.4272, 'Insurance (Prop/Cas.)': 0.4357, 'Investments & Asset Management': 0.5297, 'Machinery': 0.868, 'Metals & Mining': 0.9636, 'Office Equipment & Services': 0.9806, 'Oil/Gas (Integrated)': 0.2712, 'Oil/Gas (Production and Exploration)': 0.5628, 'Oil/Gas Distribution': 0.4651, 'Oilfield Svcs/Equip.': 0.743, 'Packaging & Container': 0.7231, 'Paper/Forest Products': 0.7216, 'Power': 0.31, 'Precious Metals': 0.7932, 'Publishing & Newspapers': 0.4773, 'R.E.I.T.': 0.3929, 'Real Estate (Development)': 0.4783, 'Real Estate (General/Diversified)': 0.5779, 'Real Estate (Operations & Services)': 0.8149, 'Recreation': 0.6951, 'Reinsurance': 0.4384, 'Restaurant/Dining': 0.7674, 'Retail (Automotive)': 0.6984, 'Retail (Building Supply)': 1.307, 'Retail (Distributors)': 0.7827, 'Retail (General)': 0.76, 'Retail (Grocery and Food)': 0.8048, 'Retail (REITs)': 0.4362, 'Retail (Special Lines)': 0.9488, 'Rubber& Tires': 0.1435, 'Semiconductor': 1.4893, 'Semiconductor Equip': 1.3481, 'Shipbuilding & Marine': 0.644, 'Shoe': 0.9337, 'Software (Entertainment)': 1.0128, 'Software (Internet)': 1.5461, 'Software (System & Application)': 1.2254, 'Steel': 0.9035, 'Telecom (Wireless)': 0.3873, 'Telecom. Equipment': 0.8635, 'Telecom. Services': 0.3654, 'Tobacco': 0.6776, 'Transportation': 0.6753, 'Transportation (Railroads)': 0.8069, 'Trucking': 0.8504, 'Utility (General)': 0.1486, 'Utility (Water)': 0.2812, 'Total Market': 0.7217, 'Total Market (without financials)': 0.8775}}
FALLBACK_SPREAD = {"date": "Data used is as of January 2026 (snapshot)", "value": 0.0111}
FALLBACK_TAX = {"date": "OECD CTS, 2026 rates (snapshot)", "data": {'Australia': 30, 'Austria': 23, 'Belgium': 25, 'Brazil': 34, 'Canada': 26, 'Chile': 27, 'China': 25, 'Colombia': 35, 'Croatia': 18, 'Czech Republic': 21, 'Denmark': 22, 'Estonia': 22, 'Finland': 20, 'France': 36.13, 'Germany': 30.133026, 'Greece': 22, 'Hong Kong': 16.5, 'Hungary': 9, 'Iceland': 20, 'India': 25.17, 'Indonesia': 22, 'Ireland': 12.5, 'Israel': 23, 'Italy': 27.81, 'Japan': 29.74, 'Kenya': 30, 'South Korea': 27.5, 'Latvia': 20, 'Lithuania': 17, 'Luxembourg': 23.87, 'Malaysia': 24, 'Mexico': 30, 'Netherlands': 25.8, 'New Zealand': 28, 'Nigeria': 30, 'Norway': 22, 'Pakistan': 29, 'Peru': 29.5, 'Philippines': 25, 'Poland': 19, 'Portugal': 29.5, 'Romania': 16, 'Saudi Arabia': 20, 'Singapore': 17, 'Slovakia': 24, 'Slovenia': 22, 'South Africa': 27, 'Spain': 25, 'Sweden': 20.6, 'Switzerland': 19.471871, 'Thailand': 20, 'Turkey': 25, 'United Arab Emirates': 9, 'United Kingdom': 25, 'United States': 25.5285, 'Vietnam': 20, 'Zambia': 30, 'Argentina': 35, 'Egypt': 22.5, 'Malta': 35}}
FALLBACK_YIELDS = {"date": "Trading Economics close Nov/13/2026 (snapshot)", "data": {'United States': [5.145, 'Nov/13'], 'United Kingdom': [5.3377, 'Nov/13'], 'Japan': [2.8616, 'Nov/13'], 'Australia': [5.143, 'Nov/13'], 'Germany': [3.3762, 'Nov/13'], 'Brazil': [15.335, 'Nov/13'], 'Russia': [16.74, 'Nov/13'], 'India': [7.312, 'Nov/13'], 'Canada': [3.856, 'Nov/13'], 'Italy': [4.437, 'Nov/13'], 'France': [4.921, 'Nov/13'], 'South Africa': [8.675, 'Nov/13'], 'China': [1.6181, 'Nov/13'], 'Switzerland': [0.5626, 'Nov/13'], 'Mexico': [9.308, 'Nov/13'], 'Netherlands': [3.4768, 'Nov/13'], 'New Zealand': [4.7462, 'Nov/13'], 'Portugal': [3.8186, 'Nov/13'], 'South Korea': [4.004, 'Nov/13'], 'Spain': [3.938, 'Nov/13'], 'Greece': [4.2636, 'Nov/13'], 'Turkey': [36.44, 'Nov/13'], 'Finland': [3.7675, 'Nov/13'], 'Lithuania': [4.327, 'Nov/13'], 'Norway': [4.3842, 'Nov/13'], 'Poland': [6.335, 'Nov/13'], 'Romania': [7.12, 'Nov/13'], 'Slovakia': [4.305, 'Nov/13'], 'Slovenia': [3.914, 'Nov/13'], 'Sweden': [3.098, 'Nov/13'], 'Hungary': [5.5249, 'Nov/13'], 'Iceland': [6.81, 'Nov/13'], 'Ireland': [3.534, 'Nov/13'], 'Austria': [3.6096, 'Nov/13'], 'Belgium': [4.3545, 'Nov/13'], 'Croatia': [3.8665, 'Nov/12'], 'Czech Republic': [5.2222, 'Nov/13'], 'Denmark': [3.1693, 'Nov/13'], 'Euro Area': [3.9, 'Nov/12'], 'Peru': [6.26, 'Nov/12'], 'Chile': [6.01, 'Nov/13'], 'Colombia': [12.698, 'Nov/13'], 'Singapore': [2.3296, 'Nov/13'], 'Taiwan': [1.86, 'Nov/13'], 'Thailand': [1.995, 'Nov/13'], 'Vietnam': [4.5726, 'Nov/13'], 'Hong Kong': [3.994, 'Nov/13'], 'Indonesia': [7.607, 'Nov/13'], 'Israel': [4.183, 'Nov/13'], 'Malaysia': [3.8628, 'Nov/13'], 'Pakistan': [12.604, 'Nov/13'], 'Philippines': [7.767, 'Nov/13'], 'Zambia': [14.74, 'Nov/13'], 'Kenya': [12.5, 'Nov/13'], 'Nigeria': [16.597, 'Nov/12']}}


# --------------------------------------------------------------------------------------
# Parsers (pure functions; unit-testable against saved HTML / XLS)
# --------------------------------------------------------------------------------------
def parse_betas_xls(content: bytes) -> tuple[pd.DataFrame, str]:
    """Return DataFrame[industry, unlevered_beta, levered_beta, de_ratio, n_firms] and the file date."""
    raw = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    sheet = raw.get("Industry Averages")
    if sheet is None:  # pick the sheet that contains 'Industry Name'
        sheet = next(s for s in raw.values() if (s.astype(str) == "Industry Name").any().any())
    first = sheet[0].astype(str).str.strip()
    hdr_row = first[first == "Industry Name"].index[0]
    date = ""
    for i in range(hdr_row):
        if "date" in str(sheet.iat[i, 0]).lower():
            val = sheet.iat[i, 1]
            date = val.strftime("%Y-%m-%d") if hasattr(val, "strftime") else str(val)
            break
    cols = [str(c).strip() for c in sheet.iloc[hdr_row]]
    body = sheet.iloc[hdr_row + 1 :].copy()
    body.columns = cols
    body = body[body["Industry Name"].notna()]
    ucol = next(c for c in cols if c.lower() == "unlevered beta")
    out = pd.DataFrame(
        {
            "industry": body["Industry Name"].astype(str).str.strip(),
            "unlevered_beta": pd.to_numeric(body[ucol], errors="coerce"),
            "levered_beta_damodaran": pd.to_numeric(body.get("Beta"), errors="coerce"),
            "de_ratio_damodaran": pd.to_numeric(body.get("D/E Ratio"), errors="coerce"),
            "n_firms": pd.to_numeric(body.get("Number of firms"), errors="coerce"),
        }
    ).dropna(subset=["unlevered_beta"])
    return out.reset_index(drop=True), date


def parse_ratings_html(html: str) -> tuple[float, str]:
    """Return (Baa2/BBB spread for large non-financial firms, as decimal; 'as of' text)."""
    m = re.search(r"Data used is as of\s*([A-Za-z]+\s+\d{4})", re.sub(r"<[^>]+>|&nbsp;", " ", html))
    asof = m.group(1) if m else ""
    for tb in pd.read_html(io.StringIO(html)):
        for _, row in tb.iterrows():
            cells = [str(x).strip() for x in row.tolist()]
            if "Baa2/BBB" in cells:
                j = cells.index("Baa2/BBB")  # first occurrence = non-financial block
                val = cells[j + 1].replace("%", "").strip()
                return float(val) / 100.0, asof
    raise ValueError("Baa2/BBB row not found")


def parse_oecd_csv(text: str) -> tuple[dict, str]:
    """Combined statutory CIT rate (MEASURE=CIT_C, TARGETING=ST), latest year per jurisdiction."""
    df = pd.read_csv(io.StringIO(text), low_memory=False)
    sel = df[(df["MEASURE"] == "CIT_C") & (df["TARGETING"] == "ST")]
    sel = sel.dropna(subset=["OBS_VALUE"]).sort_values("TIME_PERIOD")
    latest = sel.groupby("REF_AREA").tail(1)
    rates = {canon(r["Reference area"]): (float(r["OBS_VALUE"]) / 100.0, int(r["TIME_PERIOD"]))
             for _, r in latest.iterrows()}
    yr = int(latest["TIME_PERIOD"].max())
    return rates, f"OECD Corporate Tax Statistics database, latest year {yr}"


def parse_te_html(html: str) -> dict:
    """Return {country: (yield_decimal, date_text)} from Trading Economics bond tables."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    for tr in soup.select("tr[data-symbol]"):
        a = tr.find("a", href=re.compile(r"/government-bond-yield"))
        p = tr.find("td", id="p")
        if not (a and p):
            continue
        name = canon(a.get_text(strip=True))
        try:
            y = float(p.get_text(strip=True).replace(",", "")) / 100.0
        except ValueError:
            continue
        d = tr.find("td", id="date")
        out.setdefault(name, (y, d.get_text(strip=True) if d else ""))
    if "United States" not in out:
        raise ValueError("U.S. 10Y not found in Trading Economics page")
    return out


# --------------------------------------------------------------------------------------
# Fetchers (each returns a dict with data + provenance; never raises)
# --------------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fetch_betas() -> dict:
    try:
        r = requests.get(BETAS_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df, date = parse_betas_xls(r.content)
        return {"df": df, "asof": f"File dated {date}" if date else r.headers.get("Last-Modified", ""),
                "live": True, "fetched": _now(), "source": BETAS_URL}
    except Exception as e:  # noqa: BLE001
        df = pd.DataFrame({"industry": list(FALLBACK_BETAS["data"]),
                           "unlevered_beta": list(FALLBACK_BETAS["data"].values())})
        return {"df": df, "asof": FALLBACK_BETAS["date"], "live": False, "error": str(e),
                "fetched": _now(), "source": BETAS_URL}


def fetch_spread() -> dict:
    try:
        r = requests.get(RATINGS_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        v, asof = parse_ratings_html(r.text)
        return {"value": v, "asof": f"Data used is as of {asof}" if asof else "", "live": True,
                "fetched": _now(), "source": RATINGS_URL}
    except Exception as e:  # noqa: BLE001
        return {"value": FALLBACK_SPREAD["value"], "asof": FALLBACK_SPREAD["date"], "live": False,
                "error": str(e), "fetched": _now(), "source": RATINGS_URL}


def fetch_tax() -> dict:
    try:
        r = requests.get(OECD_SDMX_URL, headers={**HEADERS, "Accept": "text/csv"}, timeout=60)
        r.raise_for_status()
        rates, asof = parse_oecd_csv(r.text)
        if "United States" not in rates:
            raise ValueError("United States missing")
        return {"data": rates, "asof": asof, "live": True, "fetched": _now(), "source": OECD_SDMX_URL}
    except Exception as e:  # noqa: BLE001
        data = {k: (v / 100.0, None) for k, v in FALLBACK_TAX["data"].items()}
        return {"data": data, "asof": FALLBACK_TAX["date"], "live": False, "error": str(e),
                "fetched": _now(), "source": OECD_SDMX_URL}


def fetch_yields() -> dict:
    err = ""
    sess = requests.Session()
    sess.headers.update(HEADERS)
    for url in [TE_PARTIAL_URL, TE_PARTIAL_URL, TE_PARTIAL_URL, TE_URL]:
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 202:  # TE sometimes answers 202 first; retry
                time.sleep(1.5)
                continue
            r.raise_for_status()
            data = parse_te_html(r.text)
            dates = sorted({d for _, d in data.values() if d})
            return {"data": data, "asof": "Quote dates: " + ", ".join(dates[-3:]), "live": True,
                    "fetched": _now(), "source": TE_URL}
        except Exception as e:  # noqa: BLE001
            err = str(e)
    data = {k: (v[0] / 100.0, v[1]) for k, v in FALLBACK_YIELDS["data"].items()}
    return {"data": data, "asof": FALLBACK_YIELDS["date"], "live": False,
            "error": err or "HTTP 202 (bot check)", "fetched": _now(), "source": TE_URL}


# --------------------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------------------
def convert(r_usd: float, y_us: float, y_fc: float, method: str = "exact") -> float:
    if method == "exact":
        return (1 + r_usd) * (1 + y_fc) / (1 + y_us) - 1
    return r_usd + (y_fc - y_us)


def compute(beta_u: float, tax: float, erp: float, d: float, spread_usd: float,
            y_us: float, y_fc: float, fc_spread_override: float | None = None,
            method: str = "exact", lever: str = "hamada_tax") -> dict:
    de = d / (1 - d) if d < 1 else float("inf")
    beta_l = beta_u * (1 + ((1 - tax) if lever == "hamada_tax" else 1.0) * de)
    ke_usd = y_us + beta_l * erp
    kd_usd = y_us + spread_usd
    ke_fc = convert(ke_usd, y_us, y_fc, method)
    kd_fc_auto = convert(kd_usd, y_us, y_fc, method)
    spread_fc_auto = kd_fc_auto - y_fc
    if fc_spread_override is None:
        kd_fc, spread_fc = kd_fc_auto, spread_fc_auto
    else:
        spread_fc = fc_spread_override
        kd_fc = y_fc + spread_fc
    wacc_usd = (1 - d) * ke_usd + d * (1 - tax) * kd_usd
    wacc_fc = (1 - d) * ke_fc + d * (1 - tax) * kd_fc
    return {
        "D/E": de, "beta_L": beta_l,
        "rf_usd": y_us, "rf_fc": y_fc,
        "spread_usd": spread_usd, "spread_fc": spread_fc, "spread_fc_auto": spread_fc_auto,
        "ke_usd": ke_usd, "ke_fc": ke_fc,
        "kd_usd": kd_usd, "kd_fc": kd_fc,
        "kd_at_usd": kd_usd * (1 - tax), "kd_at_fc": kd_fc * (1 - tax),
        "wacc_usd": wacc_usd, "wacc_fc": wacc_fc,
    }


# --------------------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------------------
def main() -> None:
    import altair as alt
    import streamlit as st

    st.set_page_config(page_title="International Cost of Capital", layout="wide")

    @st.cache_data(ttl=12 * 3600, show_spinner=False)
    def load_all() -> dict:
        return {"betas": fetch_betas(), "spread": fetch_spread(), "tax": fetch_tax(), "yields": fetch_yields()}

    st.title("International Cost of Capital")
    st.caption("Cost of equity, cost of debt and WACC for a project in U.S. dollars vs. a selected country's currency.")

    top = st.columns([1, 4])
    if top[0].button("🔄 Refresh data", help="Clear the cache and re-fetch all four sources"):
        load_all.clear()
    with st.spinner("Fetching Damodaran, OECD and Trading Economics data…"):
        data = load_all()

    betas_df: pd.DataFrame = data["betas"]["df"]
    yields: dict = data["yields"]["data"]
    taxes: dict = data["tax"]["data"]

    # ---------------- Sidebar inputs ----------------
    sb = st.sidebar
    sb.header("Project inputs")
    industries = betas_df["industry"].tolist()
    industry = sb.selectbox("Industry", industries,
                            index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
    countries = sorted(yields.keys())
    country = sb.selectbox("Target country (foreign currency)", countries,
                           index=countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0)
    erp = sb.number_input("Equity risk premium (%)", 0.0, 20.0, DEFAULT_ERP * 100, 0.1, format="%.2f") / 100
    d = sb.slider("Target debt share D/(D+E) (%)", 0, 90, int(DEFAULT_DEBT_SHARE * 100), 1) / 100
    tax_choice = sb.radio("Project tax rate", ["U.S.", "Selected country"], horizontal=True,
                          help="The same tax rate is applied to both currencies.")

    # fetched values
    bu_f = float(betas_df.loc[betas_df["industry"] == industry, "unlevered_beta"].iloc[0])
    t_us_f = taxes.get("United States", (0.0, None))[0]
    t_fc_f = taxes.get(country, (None, None))[0]
    s_f = data["spread"]["value"]
    y_us_f = yields["United States"][0]
    y_fc_f = yields[country][0]

    sb.header("Manual overrides")
    def ov(label, fetched, key, fmt="%.4f", scale=1.0, step=0.01, force=False):
        on = sb.checkbox(f"Override {label}", value=force, key=f"on_{key}",
                         help=None if not force else "No fetched value available — please enter one.")
        if on:
            default = (fetched if fetched is not None else 0.0) * scale
            return sb.number_input(label, value=float(default), step=step, format=fmt, key=f"v_{key}") / scale, True
        return fetched, False

    bu, bu_ov = ov("unlevered beta", bu_f, "bu", "%.4f", 1, 0.01)
    t_us, t_us_ov = ov("U.S. tax rate (%)", t_us_f, "tus", "%.3f", 100, 0.1)
    t_fc, t_fc_ov = ov(f"{country} tax rate (%)", t_fc_f, "tfc", "%.3f", 100, 0.1, force=t_fc_f is None)
    s_usd, s_ov = ov("USD BBB spread (%)", s_f, "spr", "%.3f", 100, 0.01)
    y_us, yus_ov = ov("U.S. 10Y yield (%)", y_us_f, "yus", "%.4f", 100, 0.01)
    y_fc, yfc_ov = ov(f"{country} 10Y yield (%)", y_fc_f, "yfc", "%.4f", 100, 0.01)
    if country == "United States" and not yfc_ov:
        y_fc = y_us  # keep the two legs identical when the target is the U.S.
    if country == "United States" and not t_fc_ov:
        t_fc = t_us

    fc_ov_on = sb.toggle(f"Override {country}-currency BBB spread", value=False,
                         help="Off: spread implied by converting the USD cost of debt at interest-rate parity.")
    fc_spread = None
    if fc_ov_on:
        fc_spread = sb.number_input(f"{country}-currency BBB spread (%)", value=float(s_usd * 100),
                                    step=0.05, format="%.3f") / 100

    with sb.expander("Formula settings"):
        method = st.radio("Currency conversion", ["exact", "additive"], key="method",
                          format_func=lambda m: "Exact parity: (1+k)(1+y*)/(1+y)−1" if m == "exact"
                          else "Additive: k + (y* − y)")
        lever = st.radio("Levering", ["hamada_tax", "hamada_notax"], key="lever",
                         format_func=lambda m: "βL = βU[1+(1−t)D/E]" if m == "hamada_tax" else "βL = βU(1+D/E)")

    tax = t_us if tax_choice == "U.S." else t_fc
    if tax is None:
        st.error(f"No OECD tax rate for {country}. Turn on the tax override in the sidebar.")
        st.stop()

    res = compute(bu, tax, erp, d, s_usd, y_us, y_fc, fc_spread, method, lever)

    # ---------------- Results ----------------
    cur = country if country != "United States" else "United States (same as USD)"
    st.subheader(f"{industry} — USD vs. {cur} currency")
    k = st.columns(4)
    k[0].metric("Cost of equity (USD)", f"{res['ke_usd']:.2%}")
    k[1].metric(f"Cost of equity ({country})", f"{res['ke_fc']:.2%}", f"{res['ke_fc']-res['ke_usd']:+.2%}")
    k[2].metric("WACC (USD)", f"{res['wacc_usd']:.2%}")
    k[3].metric(f"WACC ({country})", f"{res['wacc_fc']:.2%}", f"{res['wacc_fc']-res['wacc_usd']:+.2%}")

    rows = [
        ("Risk-free rate: 10Y government yield", res["rf_usd"], res["rf_fc"]),
        ("Unlevered beta βU", bu, bu),
        ("Debt/Equity D/E", res["D/E"], res["D/E"]),
        ("Levered beta βL", res["beta_L"], res["beta_L"]),
        ("Equity risk premium (USD)", erp, erp),
        ("Cost of equity k_E", res["ke_usd"], res["ke_fc"]),
        ("BBB default spread", res["spread_usd"], res["spread_fc"]),
        ("Pre-tax cost of debt k_D", res["kd_usd"], res["kd_fc"]),
        ("Project tax rate t", tax, tax),
        ("After-tax cost of debt k_D(1−t)", res["kd_at_usd"], res["kd_at_fc"]),
        ("Equity weight E/V", 1 - d, 1 - d),
        ("Debt weight D/V", d, d),
        ("WACC", res["wacc_usd"], res["wacc_fc"]),
    ]
    tbl = pd.DataFrame(rows, columns=["Item", "USD", country])
    pct_rows = {0, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    disp = tbl.copy()
    for c in ["USD", country]:
        disp[c] = [f"{v:.2%}" if i in pct_rows else f"{v:.4f}" for i, v in enumerate(tbl[c])]
    st.dataframe(disp, hide_index=True, width="stretch")
    if fc_spread is None:
        st.caption(f"{country}-currency BBB spread is auto-calculated: {res['spread_fc_auto']:.3%} "
                   f"(USD cost of debt converted at parity, minus the {country} 10Y yield).")
    else:
        st.caption(f"{country}-currency BBB spread overridden to {fc_spread:.3%} "
                   f"(auto value would be {res['spread_fc_auto']:.3%}). USD results are unaffected.")

    # ---------------- Data sources ----------------
    with st.expander("📚 Data sources, dates and values used", expanded=True):
        def status(x): return "✅ live" if x["live"] else f"⚠️ fallback snapshot ({x.get('error','')[:80]})"
        src = pd.DataFrame([
            ["Unlevered beta — " + industry, f"{bu_f:.4f}", "manual" if bu_ov else status(data['betas']),
             data["betas"]["asof"], data["betas"]["fetched"], BETAS_URL],
            ["USD BBB spread (Baa2/BBB, large non-financial)", f"{s_f:.2%}", "manual" if s_ov else status(data['spread']),
             data["spread"]["asof"], data["spread"]["fetched"], RATINGS_URL],
            ["Corporate tax — United States", f"{t_us_f:.2%}", "manual" if t_us_ov else status(data['tax']),
             data["tax"]["asof"], data["tax"]["fetched"], OECD_SDMX_URL],
            [f"Corporate tax — {country}", "n/a" if t_fc_f is None else f"{t_fc_f:.2%}",
             "manual" if t_fc_ov else status(data['tax']), data["tax"]["asof"], data["tax"]["fetched"], OECD_SDMX_URL],
            ["10Y yield — United States", f"{y_us_f:.3%}", "manual" if yus_ov else status(data['yields']),
             f"{yields['United States'][1]} · " + data["yields"]["asof"], data["yields"]["fetched"], TE_URL],
            [f"10Y yield — {country}", f"{y_fc_f:.3%}", "manual" if yfc_ov else status(data['yields']),
             f"{yields[country][1]}", data["yields"]["fetched"], TE_URL],
        ], columns=["Input", "Fetched value", "Status", "Source date", "Fetched at", "URL"])
        st.dataframe(src, hide_index=True, width="stretch",
                     column_config={"URL": st.column_config.LinkColumn("URL")})
        st.caption(f"Tax rates are OECD combined (central + sub-central) statutory CIT rates, pulled from the "
                   f"machine-readable OECD Corporate Tax Statistics database behind the publication "
                   f"[Corporate Tax Statistics — statutory rates]({OECD_PUB_URL}).")

    # ---------------- Assumptions ----------------
    with st.expander("🧮 Method and main assumptions"):
        st.markdown(f"""
1. **USD build-up (CAPM).** U.S. 10Y Treasury yield is the USD risk-free rate. The industry's Damodaran
   unlevered beta is relevered at the target debt share: βL = βU·[1 + (1−t)·D/E] (switchable).
   k_E(USD) = y_US + βL·ERP, k_D(USD) = y_US + BBB spread.
2. **Foreign-currency rates by interest-rate parity.** The 10Y yield gap is taken as the expected currency
   depreciation / inflation differential, so every USD rate is converted with
   (1+k_FC) = (1+k_USD)·(1+y_FC)/(1+y_US) {'(selected)' if method=='exact' else ''}
   or the additive approximation k_FC = k_USD + (y_FC − y_US) {'(selected)' if method=='additive' else ''}.
   The project's real risk is the same in both currencies; only the currency unit changes.
3. **Same project parameters in both currencies:** one tax rate (U.S. or selected country), one debt share,
   one ERP, one beta. Foreign-currency debt spread defaults to the parity-implied value; you can override it
   with a market spread, which changes only the foreign-currency results.
4. **Government yields are treated as default-free.** For countries with sovereign credit risk the local
   10Y yield also contains a default premium, so the converted foreign-currency rates may overstate the
   pure inflation differential. No separate country risk premium is added.
5. **WACC** = E/V·k_E + D/V·(1−t)·k_D, with constant target weights.
""")

    # ---------------- All-industry comparison ----------------
    st.subheader("All-industry comparison")
    allrows = []
    for _, r in betas_df.iterrows():
        x = compute(float(r["unlevered_beta"]), tax, erp, d, s_usd, y_us, y_fc, fc_spread, method, lever)
        allrows.append({"Industry": r["industry"], "Unlevered beta": r["unlevered_beta"], "Levered beta": x["beta_L"],
                        "Cost of equity USD": x["ke_usd"], f"Cost of equity {country}": x["ke_fc"],
                        "WACC USD": x["wacc_usd"], f"WACC {country}": x["wacc_fc"]})
    all_df = pd.DataFrame(allrows).sort_values("WACC USD", ascending=False)
    view = st.radio("Chart", ["WACC USD", f"WACC {country}", "Cost of equity USD", f"Cost of equity {country}"],
                    horizontal=True)
    chart_df = all_df.assign(Selected=all_df["Industry"].eq(industry))
    chart = (alt.Chart(chart_df).mark_bar().encode(
        x=alt.X(f"{view}:Q", axis=alt.Axis(format="%"), title=view),
        y=alt.Y("Industry:N", sort="-x", title=None),
        color=alt.condition("datum.Selected", alt.value("#d62728"), alt.value("#4c78a8")),
        tooltip=["Industry", alt.Tooltip("Unlevered beta:Q", format=".3f"),
                 alt.Tooltip(f"{view}:Q", format=".2%")],
    ).properties(height=max(400, 14 * len(chart_df))))
    st.altair_chart(chart, width="stretch")
    fmt = {c: "{:.2%}" for c in all_df.columns if c.startswith(("Cost", "WACC"))}
    fmt.update({"Unlevered beta": "{:.3f}", "Levered beta": "{:.3f}"})
    st.dataframe(all_df.style.format(fmt), hide_index=True, width="stretch", height=400)

    # ---------------- Downloads ----------------
    st.subheader("Download results")
    inputs = pd.DataFrame([
        ["Industry", industry], ["Target country", country], ["Unlevered beta", bu],
        ["Equity risk premium", erp], ["Debt share D/V", d], ["Tax rate used", tax],
        ["Tax rate source", tax_choice], ["USD BBB spread", s_usd], ["US 10Y yield", y_us],
        [f"{country} 10Y yield", y_fc], ["Foreign spread override", "off" if fc_spread is None else fc_spread],
        ["Conversion method", method], ["Levering formula", lever], ["Generated", _now()],
    ], columns=["Parameter", "Value"])
    c1, c2, c3 = st.columns(3)
    c1.download_button("Summary (CSV)", tbl.to_csv(index=False).encode(), "cost_of_capital_summary.csv", "text/csv")
    c2.download_button("All industries (CSV)", all_df.to_csv(index=False).encode(), "all_industries.csv", "text/csv")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs.to_excel(xw, sheet_name="Inputs", index=False)
        tbl.to_excel(xw, sheet_name="Results", index=False)
        all_df.to_excel(xw, sheet_name="All industries", index=False)
        src.to_excel(xw, sheet_name="Sources", index=False)
    c3.download_button("Everything (Excel)", buf.getvalue(), "international_cost_of_capital.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    main()
