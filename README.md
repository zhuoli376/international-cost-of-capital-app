# International Cost of Capital App (FINC-470)

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in U.S. dollars and in the currency of any selected country**.

**Live app:** _add your Streamlit URL here_

## What it does

- Pulls the latest data on launch (cached for 12 hours; the **🔄 Refresh data** button re-fetches):

| Input | Source | What is used |
|---|---|---|
| Industry unlevered beta | [Damodaran `betas.xls`](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | "Unlevered beta" column, sheet *Industry Averages* |
| USD BBB spread | [Damodaran `ratings.html`](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | Baa2/BBB, large non-financial firms |
| Corporate tax rates | OECD Corporate Tax Statistics ([publication](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html)); values read from the OECD SDMX API (`DSD_TAX_CIT@DF_CIT`) | Combined (central + sub-central) statutory CIT rate, latest year |
| 10-year government yields | [Trading Economics `/bonds`](https://tradingeconomics.com/bonds) (scraped, no API key) | 10Y yield and quote date per country |

- Shows every source, its "as of" date and the time it was fetched. If a site can't be reached, the app uses a built-in snapshot (Damodaran Jan 2026, OECD 2026, Trading Economics 13 Nov 2026) and **flags it** as a fallback.
- Sidebar: industry, target country, ERP, target debt share, U.S. vs. selected-country tax rate, and manual overrides for the unlevered beta, both tax rates, the USD BBB spread and both 10Y yields. A separate toggle overrides the **foreign-currency BBB spread**; with it off, the app shows the parity-implied spread.
- Output: side-by-side USD vs. foreign-currency table, method and assumptions, an all-industry chart and table, and downloads (CSV and an Excel file with inputs, results, all industries and sources).

**Defaults:** Software (System & Application), United States, U.S. tax rate, ERP 6.5%, debt share 30%.

## Formulas

With debt share *d* = D/V, tax rate *t*, ERP, USD yield *y*, foreign yield *y\**:

```
D/E        = d / (1 − d)
βL         = βU · [1 + (1 − t)·D/E]                 (option: βU·(1 + D/E))
kE(USD)    = y + βL · ERP
kD(USD)    = y + BBB spread
k(FC)      = (1 + k(USD)) · (1 + y*) / (1 + y) − 1   (option: k(USD) + (y* − y))
kD(FC)     = parity conversion of kD(USD)  — or —  y* + manual foreign spread
WACC       = (1 − d)·kE + d·(1 − t)·kD
```

The conversion method and the levering formula can be switched under **Formula settings** in the sidebar to match the formulas used in class.

## Checks (`test_app.py`)

`python -m pytest -q` checks that:
1. equal government yields give equal USD and foreign-currency results (spread override off);
2. zero debt makes WACC equal to the cost of equity;
3. overriding the foreign-currency spread leaves all USD results unchanged;
4. a hand-computed example matches;
5. the four parsers read saved copies of the source pages correctly (needs the `samples/` folder, not required for deployment).

## Run locally / deploy

```bash
pip install -r requirements.txt
streamlit run app.py
```
To deploy: push `app.py`, `requirements.txt` and `README.md` to a public GitHub repo, then on [share.streamlit.io](https://share.streamlit.io) choose **Create app → From GitHub**, select the repo, branch `main` and main file `app.py`.

_Built with help from an AI assistant (Claude); calculations independently verified (see submission note)._
