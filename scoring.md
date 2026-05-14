# Scoring Methodology

This screener uses a 100-point heuristic score to rank companies by capital
intensity, quality, growth, valuation, leverage, and shareholder discipline.
The score is designed for screening and triage. It is not a valuation model and
should not replace normalized earnings work or business analysis.

## Data Window

The core annual metrics use full fiscal-year data from annual `yfinance`
statements:

- Income statement: `stock.financials`
- Cash flow statement: `stock.cashflow`
- Balance sheet: `stock.balance_sheet`

TTM metrics use the latest four quarters from quarterly statements:

- Quarterly income statement: `stock.quarterly_financials`
- Quarterly cash flow statement: `stock.quarterly_cashflow`

The implementation uses two annual windows:

- 3-year averages use the latest three full fiscal years.
- 3-year CAGR and trend metrics use the latest four annual observations, so the
  calculation spans three full year-to-year intervals.

If fewer than four annual periods are available, the script falls back to the
longest annual window available for that ticker.

## FCF Treatment

Raw free cash flow is calculated as:

```text
FCF = Operating Cash Flow + Capital Expenditure
```

`yfinance` reports capital expenditure as a negative cash flow item, so adding
it to operating cash flow produces the standard FCF figure.

Adjusted FCF subtracts stock-based compensation when the cash flow statement
contains an SBC line:

```text
Adj FCF = FCF - Stock Based Compensation
```

The score uses adjusted FCF metrics. Raw FCF metrics remain in exports for
comparison.

## Score Bands

Use these as rough interpretation bands:

| Score | Interpretation |
|---:|---|
| 80+ | Excellent screen result. Usually worth deeper work. |
| 70-80 | Good. Quality profile is attractive, but check the weak spots. |
| 60-70 | Mixed. Potentially interesting, but needs explanation. |
| 50-60 | Average. Usually not compelling without a special situation. |
| <50 | Weak screen result. Usually fails several quality or valuation checks. |

## Scoring Functions

The code uses two linear scoring helpers.

For metrics where higher is better:

```text
score_higher(value, max_points, full_at, zero_at)
```

- `value <= zero_at` receives 0 points.
- `value >= full_at` receives full points.
- Values between those thresholds are scaled linearly.

For metrics where lower is better:

```text
score_lower(value, max_points, best_at, zero_at)
```

- `value <= best_at` receives full points.
- `value >= zero_at` receives 0 points.
- Values between those thresholds are scaled linearly.

Missing values receive 0 points for that component.

## Score Weights

### Capital Intensity: 25 Points

| Metric | Points | Better | Full Points | Zero Points |
|---|---:|---|---:|---:|
| `Avg_Dep_Factor_3Y` | 18 | Lower | <= 1.05 | >= 2.00 |
| `CapEx_Rev_Ratio` | 7 | Lower | <= 2% | >= 15% |

### Returns and Quality: 27 Points

| Metric | Points | Better | Full Points | Zero Points |
|---|---:|---|---:|---:|
| `ROIC_Latest` | 17 | Higher | >= 25% | <= 0% |
| `ROIC_Trend_3Y` | 5 | Higher | >= +5 pts | <= -5 pts |
| `Operating_Margin` | 3 | Higher | >= 30% | <= 5% |
| `Gross_Margin` | 2 | Higher | >= 60% | <= 20% |

### Cash Conversion: 15 Points

| Metric | Points | Better | Full Points | Zero Points |
|---|---:|---|---:|---:|
| `Adj_FCF_EBITDA_Ratio` | 7 | Higher | >= 75% | <= 0% |
| `Adj_FCF_Margin` | 6 | Higher | >= 20% | <= 0% |
| `SBC_Rev_Ratio` | 2 | Lower | <= 0% | >= 8% |

### Growth and Stability: 15 Points

| Metric | Points | Better | Full Points | Zero Points |
|---|---:|---|---:|---:|
| `Rev_CAGR_3Y` | 4 | Higher | >= 12% | <= -5% |
| `EBIT_CAGR_3Y` | 7 | Higher | >= 15% | <= -10% |
| `EBIT_Margin_Trend_3Y` | 2 | Higher | >= +5 pts | <= -5 pts |
| `Revenue_Stability` | 2 | Lower | <= 3% | >= 20% |

### Valuation, Leverage, and Dilution: 18 Points

| Metric | Points | Better | Full Points | Zero Points |
|---|---:|---|---:|---:|
| `Adj_FCF_Yield` | 7 | Higher | >= 6% | <= 0% |
| `EV_EBIT` | 5 | Lower | <= 10.0x | >= 40.0x |
| `Net_Debt_EBITDA` | 4 | Lower | <= 0.0x | >= 4.0x |
| `Share_Count_CAGR_3Y` | 2 | Lower | <= -3% | >= +5% |

## Metric Definitions

### `Avg_Dep_Factor_3Y`

Average depreciation factor over the latest three fiscal years:

```text
Depreciation Factor = EBITDA / EBIT
```

Lower values usually indicate lower capital intensity. A value near 1.0 means
D&A is small relative to EBIT. Values above 1.7 indicate a heavier depreciation
burden and may point to more capital-intensive economics.

Intensity bands:

| Depreciation Factor | Level |
|---:|---|
| < 1.4 | Low |
| 1.4-1.7 | Moderate |
| > 1.7 | High |

### `Dep_Factor_TTM`

TTM depreciation factor:

```text
TTM EBITDA / TTM EBIT
```

This is calculated from the latest four quarterly periods when available. It is
shown as a current-period comparison against the 3-year average.

### `D&A_EBIT_Ratio`

D&A relative to EBIT:

```text
(EBITDA - EBIT) / EBIT
```

This is a direct capital intensity proxy. Lower is better.

This metric is not used directly in the score because it is mechanically
equivalent to depreciation factor minus one:

```text
D&A / EBIT = (EBITDA - EBIT) / EBIT
D&A / EBIT = EBITDA / EBIT - 1
D&A / EBIT = Depreciation Factor - 1
```

It remains in exports as an explanatory version of the same signal.

### `Rev_CAGR_3Y`

Revenue compound growth across the annual window:

```text
(Latest Revenue / Oldest Revenue) ^ (1 / periods) - 1
```

For a full 3-year CAGR, the script uses four annual observations and `periods`
is 3. If fewer observations are available, it uses the longest available annual
window.

### `EBIT_CAGR_3Y`

EBIT compound growth across the annual window:

```text
(Latest EBIT / Oldest EBIT) ^ (1 / periods) - 1
```

This captures operating profit growth, not just top-line growth.

### `ROIC_Latest`

Latest fiscal year return on invested capital:

```text
ROIC = NOPAT / Invested Capital
NOPAT = EBIT * (1 - tax_rate)
Invested Capital = Equity + Total Debt - Cash
```

The script uses the reported effective tax rate when available and reasonable;
otherwise it falls back to 21%.

### `ROIC_Avg_3Y`

Average ROIC across the latest three fiscal years.

### `ROIC_Trend_3Y`

Change in ROIC from the oldest to latest year in the annual window:

```text
Latest ROIC - Oldest ROIC
```

Positive values mean returns are improving. Negative values mean returns are
fading.

### `Gross_Margin`

Latest fiscal year gross margin:

```text
Gross Profit / Revenue
```

Higher values often indicate stronger unit economics, pricing power, or software
and service mix. It receives a small score weight because it is useful evidence
of pricing power, but it is deliberately kept below ROIC and operating margin
because cross-industry comparisons can be misleading.

### `Operating_Margin`

Latest fiscal year operating margin:

```text
EBIT / Revenue
```

Higher is generally better. It is lightly weighted because some lower-margin
businesses can still produce attractive returns on capital.

### `EBIT_Margin_Trend_3Y`

Change in operating margin from the oldest to latest year in the annual window:

```text
Latest Operating Margin - Oldest Operating Margin
```

Positive values indicate margin expansion. Negative values indicate margin
compression.

### `FCF_EBITDA_Ratio`

Raw FCF conversion:

```text
FCF / EBITDA
```

This is exported for comparison but is no longer used in the score.

### `FCF_Margin`

Raw FCF margin:

```text
FCF / Revenue
```

This is exported for comparison but is no longer used in the score.

### `Adj_FCF_EBITDA_Ratio`

SBC-adjusted FCF conversion:

```text
Adj FCF / EBITDA
```

This is used in the score. Higher values indicate stronger cash conversion after
treating stock-based compensation as an economic cost.

### `Adj_FCF_Margin`

SBC-adjusted FCF margin:

```text
Adj FCF / Revenue
```

This is used in the score. Higher values indicate more owner earnings generated
per dollar of sales.

### `SBC_Rev_Ratio`

Stock-based compensation as a percentage of revenue:

```text
Stock Based Compensation / Revenue
```

Lower is better. High SBC can make cash flow look better than shareholder
economics, especially if dilution is not fully offset by buybacks.

### `SBC_FCF_Ratio`

Stock-based compensation as a percentage of raw FCF:

```text
Stock Based Compensation / FCF
```

This is exported for context. It is not directly used in the score because the
score already uses adjusted FCF and `SBC_Rev_Ratio`.

### `CapEx_Rev_Ratio`

Capital expenditure intensity:

```text
Abs(Capital Expenditure) / Revenue
```

Lower is generally better because less revenue must be reinvested into fixed
assets to maintain and grow the business.

### `Net_Debt_EBITDA`

Leverage ratio:

```text
Net Debt / EBITDA
Net Debt = Total Debt - Cash
```

Lower is better. Negative values mean the company has net cash.

### `Share_Count_CAGR_3Y`

Compound growth rate in average diluted or basic shares:

```text
(Latest Average Shares / Oldest Average Shares) ^ (1 / periods) - 1
```

For a full 3-year CAGR, the script uses four annual observations and `periods`
is 3. Lower is better. Negative values indicate a shrinking share count, usually
from buybacks. Positive values indicate dilution.

### `Revenue_Stability`

Standard deviation of year-to-year revenue growth rates in the annual growth
window. With four annual observations, this captures three year-to-year growth
rates.

Lower values indicate more stable revenue growth. Higher values suggest
cyclicality, volatility, or unusual recent changes.

### `EV_EBITDA`

Enterprise value to current EBITDA:

```text
Enterprise Value / EBITDA
```

The denominator uses TTM EBITDA when available, otherwise latest annual EBITDA.
Lower is better, all else equal. This is exported for context but is not used
directly in the score because `EV_EBIT` is a less redundant valuation signal
once depreciation factor is already scored separately.

### `EV_EBIT`

Enterprise value to current EBIT:

```text
Enterprise Value / EBIT
```

The denominator uses TTM EBIT when available, otherwise latest annual EBIT.
Lower is better, all else equal.

### `FCF_Yield`

Raw FCF yield:

```text
FCF / Market Cap
```

This is exported for comparison but is no longer used in the score.

### `Adj_FCF_Yield`

SBC-adjusted FCF yield:

```text
Adj FCF / Market Cap
```

This is used in the score. Higher values indicate a more attractive owner
earnings yield.

## Important Limitations

- `yfinance` data can vary by ticker, exchange, and statement format.
- International tickers may have missing or differently labeled financial lines.
- The ROIC calculation is a practical approximation, not a full invested-capital
  reconstruction.
- The score compares companies across industries, but some metrics are naturally
  industry-specific.
- For financial companies, depreciation factor, EBITDA, and invested capital can
  be less meaningful.
- Negative or missing EBIT, EBITDA, FCF, or revenue can make several metrics
  unavailable.
