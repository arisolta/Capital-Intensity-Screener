# Capital Intensity & Quality Stock Screener

Python screener inspired by Michael Mauboussin-style capital intensity analysis.
It ranks companies using depreciation factor, FCF conversion, ROIC, growth, and
valuation metrics from `yfinance`.

## Install

```bash
pip install -r requirements.txt
```

`rich` improves terminal display and `openpyxl` enables Excel export. The script
still runs without `rich`; CSV and Markdown export do not need Excel packages.

## Usage

```bash
python3 capital_screener.py --tickers AAPL GOOGL MSFT UL PYPL GIS CLX HENKY
python3 capital_screener.py --file my_universe.txt --export results.xlsx
python3 capital_screener.py --tickers AAPL MSFT --rank-by ROIC_Latest --min-roic 12
python3 capital_screener.py --tickers AAPL MSFT ADS.DE UMG.AS --benchmark SPY
```

Supported export formats: `.csv`, `.xlsx`, `.xls`, `.md`, `.markdown`.

The default rank column is `Score` descending. Use `--rank-by` for other columns
such as `Avg_Dep_Factor_3Y`, `ROIC_Latest`, `FCF_EBITDA_Ratio`, or `FCF_Yield`.

## Scoring

`Score` is a 100-point heuristic that blends:

- Capital intensity: average depreciation factor, D&A/EBIT, CapEx/revenue
- Business quality: ROIC, FCF/EBITDA, FCF margin, gross margin, operating margin
- Growth and trend: revenue CAGR, EBIT CAGR, ROIC trend, EBIT margin trend
- Stability: standard deviation of annual revenue growth
- Balance sheet and valuation: net debt/EBITDA, FCF yield, EV/EBITDA, EV/EBIT
- Shareholder discipline: 3-year share count CAGR, rewarding buybacks and penalizing dilution

The score is meant for ranking and triage. It is not a replacement for business
quality assessment, normalized earnings work, or valuation.
