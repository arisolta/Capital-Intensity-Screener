#!/usr/bin/env python3
"""
Capital Intensity & Quality Stock Screener

Pulls financial statements from yfinance and ranks companies using a
Mauboussin-inspired capital intensity lens:

    Depreciation Factor = EBITDA / EBIT

Lower depreciation factors generally point to lower reinvestment intensity,
but this should be interpreted with business model, accounting, and cyclicality
in mind. Use this as a first-pass screener, not an investment decision engine.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yfinance as yf

try:
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:  # pragma: no cover - optional dependency
    Console = None
    Table = None
    HAS_RICH = False


# Easy-to-edit fallback universe when no CLI tickers or file are supplied.
DEFAULT_TICKERS = ["AAPL", "MSFT", "GOOGL", "UL", "PYPL", "GIS", "CLX", "HENKY"]

CACHE_DIR = Path(".cache") / "capital_screener"
CACHE_TTL_SECONDS = 24 * 60 * 60

TERMINAL_COLUMNS = [
    ("Ticker", "Ticker", True),
    ("Company Name", "Company", True),
    ("Avg_Dep_Factor_3Y", "Avg DF", True),
    ("Dep_Factor_TTM", "TTM DF", False),
    ("Rev_CAGR_3Y", "Rev CAGR", False),
    ("EBIT_CAGR_3Y", "EBIT CAGR", False),
    ("ROIC_Latest", "ROIC", True),
    ("ROIC_Trend_3Y", "ROIC Tr", False),
    ("Adj_FCF_EBITDA_Ratio", "Adj FCF/EBITDA", True),
    ("Adj_FCF_Margin", "Adj FCF Mrg", False),
    ("SBC_Rev_Ratio", "SBC/Rev", False),
    ("CapEx_Rev_Ratio", "CapEx/Rev", False),
    ("Net_Debt_EBITDA", "ND/EBITDA", False),
    ("EV_EBITDA", "EV/EBITDA", False),
    ("EV_EBIT", "EV/EBIT", False),
    ("Adj_FCF_Yield", "Adj FCF Yield", True),
    ("Intensity_Level", "Level", True),
    ("Score", "Score", True),
]

DROP_PRIORITY = [
    "EV_EBIT",
    "Gross_Margin",
    "Operating_Margin",
    "Share_Count_CAGR_3Y",
    "CapEx_Rev_Ratio",
    "SBC_Rev_Ratio",
    "Adj_FCF_Margin",
    "EBIT_CAGR_3Y",
    "ROIC_Trend_3Y",
    "Dep_Factor_TTM",
    "Net_Debt_EBITDA",
    "Rev_CAGR_3Y",
    "EV_EBITDA",
]


@dataclass(frozen=True)
class ScreenerConfig:
    years: int = 3
    rank_by: str = "Score"
    min_roic: float | None = None
    max_dep_factor: float | None = None
    use_cache: bool = True
    benchmark: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank stocks by capital intensity, FCF quality, ROIC, and valuation."
    )
    parser.add_argument("--tickers", nargs="*", help="Ticker symbols, e.g. AAPL MSFT GOOGL")
    parser.add_argument("--file", type=Path, help="Text file with one ticker per line")
    parser.add_argument(
        "--rank-by",
        default="Score",
        help="Column to rank by. Examples: Score, Avg_Dep_Factor_3Y, ROIC_Latest, FCF_EBITDA_Ratio",
    )
    parser.add_argument("--min-roic", type=float, help="Filter out companies below this ROIC percent")
    parser.add_argument(
        "--max-dep-factor",
        type=float,
        help="Filter out companies above this average depreciation factor",
    )
    parser.add_argument(
        "--export",
        type=Path,
        help="Export path. Supported extensions: .csv, .xlsx, .xls, .md, .markdown",
    )
    parser.add_argument(
        "--benchmark",
        help="Optional benchmark ticker to include for comparison, e.g. SPY",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local JSON cache for yfinance responses",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich terminal output even if rich is installed",
    )
    return parser.parse_args()


def load_tickers(cli_tickers: list[str] | None, file_path: Path | None, benchmark: str | None) -> list[str]:
    tickers: list[str] = []
    if cli_tickers:
        tickers.extend(cli_tickers)

    if file_path:
        if not file_path.exists():
            raise FileNotFoundError(f"Ticker file not found: {file_path}")
        file_tickers = [
            line.strip()
            for line in file_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        tickers.extend(file_tickers)

    if not tickers:
        tickers = DEFAULT_TICKERS.copy()

    if benchmark:
        tickers.append(benchmark)

    cleaned: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        normalized = ticker.strip().upper()
        if normalized and normalized not in seen:
            cleaned.append(normalized)
            seen.add(normalized)
    return cleaned


def cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe}.json"


def is_cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def frame_to_jsonable(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    serializable = df.copy()
    serializable.columns = [str(col) for col in serializable.columns]
    serializable.index = [str(idx) for idx in serializable.index]
    return serializable.to_dict(orient="split")


def frame_from_jsonable(payload: dict[str, Any]) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame()
    return pd.DataFrame(
        data=payload.get("data", []),
        index=payload.get("index", []),
        columns=payload.get("columns", []),
    )


def get_financials(ticker: str, use_cache: bool = True) -> dict[str, Any]:
    path = cache_path(ticker)
    if use_cache and is_cache_fresh(path):
        with path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        return {
            "info": cached.get("info", {}),
            "financials": frame_from_jsonable(cached.get("financials", {})),
            "cashflow": frame_from_jsonable(cached.get("cashflow", {})),
            "balance_sheet": frame_from_jsonable(cached.get("balance_sheet", {})),
            "quarterly_financials": frame_from_jsonable(cached.get("quarterly_financials", {})),
            "quarterly_cashflow": frame_from_jsonable(cached.get("quarterly_cashflow", {})),
            "quarterly_balance_sheet": frame_from_jsonable(cached.get("quarterly_balance_sheet", {})),
        }

    stock = yf.Ticker(ticker)
    payload = {
        "info": stock.info or {},
        "financials": stock.financials,
        "cashflow": stock.cashflow,
        "balance_sheet": stock.balance_sheet,
        "quarterly_financials": stock.quarterly_financials,
        "quarterly_cashflow": stock.quarterly_cashflow,
        "quarterly_balance_sheet": stock.quarterly_balance_sheet,
    }

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "info": payload["info"],
                    "financials": frame_to_jsonable(payload["financials"]),
                    "cashflow": frame_to_jsonable(payload["cashflow"]),
                    "balance_sheet": frame_to_jsonable(payload["balance_sheet"]),
                    "quarterly_financials": frame_to_jsonable(payload["quarterly_financials"]),
                    "quarterly_cashflow": frame_to_jsonable(payload["quarterly_cashflow"]),
                    "quarterly_balance_sheet": frame_to_jsonable(payload["quarterly_balance_sheet"]),
                },
                handle,
                default=str,
            )
    return payload


def find_statement_value(
    statement: pd.DataFrame,
    labels: Iterable[str],
    period: str,
    default: float = np.nan,
) -> float:
    if statement is None or statement.empty or period not in statement.columns:
        return default
    lower_index = {str(idx).lower(): idx for idx in statement.index}
    for label in labels:
        actual = lower_index.get(label.lower())
        if actual is not None:
            return to_float(statement.loc[actual, period])
    return default


def to_float(value: Any) -> float:
    if value is None:
        return np.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if math.isfinite(result) else np.nan


def safe_divide(numerator: float, denominator: float) -> float:
    numerator = to_float(numerator)
    denominator = to_float(denominator)
    if np.isnan(numerator) or np.isnan(denominator) or denominator == 0:
        return np.nan
    return numerator / denominator


def safe_positive_divide(numerator: float, denominator: float) -> float:
    denominator = to_float(denominator)
    if np.isnan(denominator) or denominator <= 0:
        return np.nan
    return safe_divide(numerator, denominator)


def nanmean(values: Iterable[float]) -> float:
    valid = [to_float(value) for value in values if not np.isnan(to_float(value))]
    return float(np.mean(valid)) if valid else np.nan


def nanstd(values: Iterable[float]) -> float:
    valid = [to_float(value) for value in values if not np.isnan(to_float(value))]
    return float(np.std(valid, ddof=0)) if valid else np.nan


def cagr(start: float, end: float, periods: int) -> float:
    start = to_float(start)
    end = to_float(end)
    if periods <= 0 or np.isnan(start) or np.isnan(end) or start <= 0 or end <= 0:
        return np.nan
    return (end / start) ** (1 / periods) - 1


def sorted_periods(statement: pd.DataFrame) -> list[str]:
    if statement is None or statement.empty:
        return []
    return sorted([str(col) for col in statement.columns], reverse=True)


def latest_periods(statement: pd.DataFrame, n: int) -> list[str]:
    return sorted_periods(statement)[:n]


def calculate_ttm(financials_q: pd.DataFrame, cashflow_q: pd.DataFrame) -> dict[str, float]:
    periods = latest_periods(financials_q, 4)
    cf_periods = latest_periods(cashflow_q, 4)
    if len(periods) < 4:
        return {}

    def sum_line(statement: pd.DataFrame, labels: Iterable[str], use_periods: list[str]) -> float:
        values = [find_statement_value(statement, labels, period) for period in use_periods]
        valid = [value for value in values if not np.isnan(value)]
        return float(np.sum(valid)) if valid else np.nan

    revenue = sum_line(financials_q, ["Total Revenue", "Operating Revenue"], periods)
    ebit = sum_line(financials_q, ["EBIT", "Operating Income"], periods)
    ebitda = sum_line(financials_q, ["EBITDA"], periods)
    if np.isnan(ebitda) and not np.isnan(ebit):
        depreciation = sum_line(financials_q, ["Reconciled Depreciation", "Depreciation And Amortization"], periods)
        ebitda = ebit + depreciation if not np.isnan(depreciation) else np.nan
    operating_cash_flow = sum_line(cashflow_q, ["Operating Cash Flow", "Total Cash From Operating Activities"], cf_periods)
    capex = sum_line(cashflow_q, ["Capital Expenditure", "Capital Expenditures"], cf_periods)
    sbc = sum_line(cashflow_q, sbc_labels(), cf_periods)
    fcf = operating_cash_flow + capex if not np.isnan(operating_cash_flow) else np.nan
    adj_fcf = fcf - sbc if not np.isnan(fcf) and not np.isnan(sbc) else fcf

    return {
        "revenue": revenue,
        "ebit": ebit,
        "ebitda": ebitda,
        "fcf": fcf,
        "sbc": sbc,
        "adj_fcf": adj_fcf,
        "capex": capex,
    }


def sbc_labels() -> list[str]:
    return [
        "Stock Based Compensation",
        "Stock Based Compensation And Other",
        "Share Based Compensation",
        "Share Based Compensation Expense",
        "Stock-Based Compensation",
    ]


def calculate_roic(period: str, financials: pd.DataFrame, balance_sheet: pd.DataFrame, tax_rate: float) -> float:
    ebit = find_statement_value(financials, ["EBIT", "Operating Income"], period)
    nopat = ebit * (1 - tax_rate) if not np.isnan(ebit) else np.nan
    equity = find_statement_value(balance_sheet, ["Stockholders Equity", "Total Stockholder Equity"], period)
    debt = find_statement_value(
        balance_sheet,
        ["Total Debt", "Long Term Debt And Capital Lease Obligation", "Long Term Debt"],
        period,
    )
    cash = find_statement_value(
        balance_sheet,
        ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
        period,
    )
    invested_capital = np.nansum([equity, debt, -cash])
    if invested_capital <= 0:
        return np.nan
    return safe_divide(nopat, invested_capital)


def calculate_net_debt(period: str, balance_sheet: pd.DataFrame) -> float:
    debt = find_statement_value(
        balance_sheet,
        ["Total Debt", "Long Term Debt And Capital Lease Obligation", "Long Term Debt"],
        period,
    )
    cash = find_statement_value(
        balance_sheet,
        ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
        period,
    )
    if np.isnan(debt) and np.isnan(cash):
        return np.nan
    return np.nan_to_num(debt, nan=0.0) - np.nan_to_num(cash, nan=0.0)


def intensity_level(avg_dep_factor: float) -> str:
    if np.isnan(avg_dep_factor):
        return "Unknown"
    if avg_dep_factor < 1.4:
        return "Low"
    if avg_dep_factor <= 1.7:
        return "Moderate"
    return "High"


def score_higher(value: float, max_points: float, full_at: float, zero_at: float = 0.0) -> float:
    value = to_float(value)
    if np.isnan(value):
        return 0.0
    if full_at == zero_at:
        return max_points if value >= full_at else 0.0
    scaled = (value - zero_at) / (full_at - zero_at)
    return float(np.clip(scaled, 0.0, 1.0) * max_points)


def score_lower(value: float, max_points: float, best_at: float, zero_at: float) -> float:
    value = to_float(value)
    if np.isnan(value):
        return 0.0
    if value <= best_at:
        return max_points
    if value >= zero_at:
        return 0.0
    scaled = (zero_at - value) / (zero_at - best_at)
    return float(np.clip(scaled, 0.0, 1.0) * max_points)


def score_range(value: float, max_points: float, low_full: float, high_zero: float) -> float:
    """Reward values at or below low_full, then fade linearly to zero."""
    return score_lower(value, max_points, low_full, high_zero)


def calculate_score(row: pd.Series) -> float:
    # 100-point heuristic score. Missing values receive zero for that component.
    score = 0.0

    # Capital intensity: low D&A burden and low capex needs are better.
    score += score_lower(row.get("Avg_Dep_Factor_3Y", np.nan), 18, best_at=1.05, zero_at=2.0)
    score += score_lower(row.get("D&A_EBIT_Ratio", np.nan), 3, best_at=0.05, zero_at=1.0)
    score += score_lower(row.get("CapEx_Rev_Ratio", np.nan), 4, best_at=0.02, zero_at=0.15)

    # Business quality and cash conversion.
    score += score_higher(row.get("ROIC_Latest", np.nan), 15, full_at=0.25, zero_at=0.0)
    score += score_higher(row.get("Adj_FCF_EBITDA_Ratio", np.nan), 7, full_at=0.75, zero_at=0.0)
    score += score_higher(row.get("Adj_FCF_Margin", np.nan), 5, full_at=0.20, zero_at=0.0)
    score += score_lower(row.get("SBC_Rev_Ratio", np.nan), 2, best_at=0.0, zero_at=0.08)
    score += score_higher(row.get("Gross_Margin", np.nan), 1.5, full_at=0.60, zero_at=0.20)
    score += score_higher(row.get("Operating_Margin", np.nan), 1.5, full_at=0.30, zero_at=0.05)

    # Growth, stability, and direction of returns/margins.
    score += score_higher(row.get("Rev_CAGR_3Y", np.nan), 6, full_at=0.12, zero_at=-0.05)
    score += score_higher(row.get("EBIT_CAGR_3Y", np.nan), 5, full_at=0.15, zero_at=-0.10)
    score += score_higher(row.get("ROIC_Trend_3Y", np.nan), 4, full_at=0.05, zero_at=-0.05)
    score += score_higher(row.get("EBIT_Margin_Trend_3Y", np.nan), 3, full_at=0.05, zero_at=-0.05)
    score += score_range(row.get("Revenue_Stability", np.nan), 2, low_full=0.03, high_zero=0.20)

    # Valuation, leverage, and shareholder dilution discipline.
    score += score_higher(row.get("Adj_FCF_Yield", np.nan), 7, full_at=0.06, zero_at=0.0)
    score += score_lower(row.get("EV_EBITDA", np.nan), 5, best_at=8.0, zero_at=30.0)
    score += score_lower(row.get("EV_EBIT", np.nan), 3, best_at=10.0, zero_at=40.0)
    score += score_lower(row.get("Net_Debt_EBITDA", np.nan), 5, best_at=0.0, zero_at=4.0)
    score += score_lower(row.get("Share_Count_CAGR_3Y", np.nan), 5, best_at=-0.03, zero_at=0.05)

    return round(score, 1)


def calculate_metrics(ticker: str, data: dict[str, Any], years: int = 3, is_benchmark: bool = False) -> dict[str, Any]:
    info = data["info"]
    financials = data["financials"]
    cashflow = data["cashflow"]
    balance_sheet = data["balance_sheet"]
    quarterly_financials = data["quarterly_financials"]
    quarterly_cashflow = data["quarterly_cashflow"]

    if financials.empty:
        raise ValueError("no annual income statement returned")

    periods = latest_periods(financials, years)
    if len(periods) < 2:
        raise ValueError("not enough annual financial statement history")

    annual_rows: list[dict[str, float]] = []
    tax_rate = to_float(info.get("effectiveTaxRate"))
    if np.isnan(tax_rate) or tax_rate < 0 or tax_rate > 0.5:
        tax_rate = 0.21

    for period in periods:
        revenue = find_statement_value(financials, ["Total Revenue", "Operating Revenue"], period)
        gross_profit = find_statement_value(financials, ["Gross Profit"], period)
        ebit = find_statement_value(financials, ["EBIT", "Operating Income"], period)
        ebitda = find_statement_value(financials, ["EBITDA"], period)
        depreciation = find_statement_value(
            financials,
            ["Reconciled Depreciation", "Depreciation And Amortization", "Depreciation"],
            period,
        )
        if np.isnan(ebitda) and not np.isnan(ebit) and not np.isnan(depreciation):
            ebitda = ebit + depreciation

        operating_cash_flow = find_statement_value(
            cashflow,
            ["Operating Cash Flow", "Total Cash From Operating Activities"],
            period,
        )
        capex = find_statement_value(cashflow, ["Capital Expenditure", "Capital Expenditures"], period)
        sbc = find_statement_value(cashflow, sbc_labels(), period)
        fcf = operating_cash_flow + capex if not np.isnan(operating_cash_flow) else np.nan
        adj_fcf = fcf - sbc if not np.isnan(fcf) and not np.isnan(sbc) else fcf
        average_shares = find_statement_value(
            financials,
            ["Diluted Average Shares", "Basic Average Shares", "Average Dilution Earnings"],
            period,
        )

        annual_rows.append(
            {
                "period": period,
                "revenue": revenue,
                "gross_profit": gross_profit,
                "ebit": ebit,
                "ebitda": ebitda,
                "depreciation": ebitda - ebit if not np.isnan(ebitda) and not np.isnan(ebit) else np.nan,
                "fcf": fcf,
                "sbc": sbc,
                "adj_fcf": adj_fcf,
                "capex": abs(capex) if not np.isnan(capex) else np.nan,
                "average_shares": average_shares,
                "gross_margin": safe_positive_divide(gross_profit, revenue),
                "operating_margin": safe_positive_divide(ebit, revenue),
                "dep_factor": safe_positive_divide(ebitda, ebit),
                "da_to_ebit": safe_positive_divide(ebitda - ebit, ebit),
                "fcf_ebitda": safe_positive_divide(fcf, ebitda),
                "fcf_margin": safe_positive_divide(fcf, revenue),
                "adj_fcf_ebitda": safe_positive_divide(adj_fcf, ebitda),
                "adj_fcf_margin": safe_positive_divide(adj_fcf, revenue),
                "sbc_revenue": safe_positive_divide(sbc, revenue),
                "sbc_fcf": safe_positive_divide(sbc, fcf),
                "capex_revenue": safe_positive_divide(abs(capex), revenue),
                "net_debt": calculate_net_debt(period, balance_sheet),
                "roic": calculate_roic(period, financials, balance_sheet, tax_rate),
            }
        )

    rows_by_period = {row["period"]: row for row in annual_rows}
    latest = annual_rows[0]
    oldest = annual_rows[-1]
    rev_cagr = cagr(oldest["revenue"], latest["revenue"], len(annual_rows) - 1)
    ebit_cagr = cagr(oldest["ebit"], latest["ebit"], len(annual_rows) - 1)
    share_count_cagr = cagr(oldest["average_shares"], latest["average_shares"], len(annual_rows) - 1)
    roic_trend = latest["roic"] - oldest["roic"] if not np.isnan(latest["roic"]) and not np.isnan(oldest["roic"]) else np.nan
    ebit_margin_trend = (
        latest["operating_margin"] - oldest["operating_margin"]
        if not np.isnan(latest["operating_margin"]) and not np.isnan(oldest["operating_margin"])
        else np.nan
    )

    chronological_rows = list(reversed(annual_rows))
    revenue_growth_rates = [
        safe_positive_divide(chronological_rows[idx]["revenue"], chronological_rows[idx - 1]["revenue"]) - 1
        for idx in range(1, len(chronological_rows))
    ]
    revenue_stability = nanstd(revenue_growth_rates)

    ttm = calculate_ttm(quarterly_financials, quarterly_cashflow)
    ttm_ebit = ttm.get("ebit", np.nan)
    ttm_ebitda = ttm.get("ebitda", np.nan)
    ttm_fcf = ttm.get("fcf", np.nan)
    ttm_adj_fcf = ttm.get("adj_fcf", np.nan)
    fallback_fcf = latest["fcf"]
    fallback_adj_fcf = latest["adj_fcf"]

    market_cap = to_float(info.get("marketCap"))
    enterprise_value = to_float(info.get("enterpriseValue"))
    current_ebitda = ttm_ebitda if not np.isnan(ttm_ebitda) else latest["ebitda"]
    current_ebit = ttm_ebit if not np.isnan(ttm_ebit) else latest["ebit"]
    current_fcf = ttm_fcf if not np.isnan(ttm_fcf) else fallback_fcf
    current_adj_fcf = ttm_adj_fcf if not np.isnan(ttm_adj_fcf) else fallback_adj_fcf

    result: dict[str, Any] = {
        "Ticker": ticker,
        "Company Name": info.get("shortName") or info.get("longName") or ticker,
        "Is_Benchmark": is_benchmark,
        "Currency": info.get("financialCurrency") or info.get("currency"),
        "Avg_Dep_Factor_3Y": nanmean([row["dep_factor"] for row in annual_rows]),
        "Dep_Factor_TTM": safe_positive_divide(ttm_ebitda, ttm_ebit),
        "D&A_EBIT_Ratio": nanmean([row["da_to_ebit"] for row in annual_rows]),
        "Rev_CAGR_3Y": rev_cagr,
        "EBIT_CAGR_3Y": ebit_cagr,
        "ROIC_Latest": latest["roic"],
        "ROIC_Avg_3Y": nanmean([row["roic"] for row in annual_rows]),
        "ROIC_Trend_3Y": roic_trend,
        "Gross_Margin": latest["gross_margin"],
        "Operating_Margin": latest["operating_margin"],
        "EBIT_Margin_Trend_3Y": ebit_margin_trend,
        "FCF_EBITDA_Ratio": nanmean([row["fcf_ebitda"] for row in annual_rows]),
        "FCF_Margin": nanmean([row["fcf_margin"] for row in annual_rows]),
        "Adj_FCF_EBITDA_Ratio": nanmean([row["adj_fcf_ebitda"] for row in annual_rows]),
        "Adj_FCF_Margin": nanmean([row["adj_fcf_margin"] for row in annual_rows]),
        "SBC_Rev_Ratio": nanmean([row["sbc_revenue"] for row in annual_rows]),
        "SBC_FCF_Ratio": nanmean([row["sbc_fcf"] for row in annual_rows]),
        "CapEx_Rev_Ratio": nanmean([row["capex_revenue"] for row in annual_rows]),
        "Net_Debt_EBITDA": safe_positive_divide(latest["net_debt"], current_ebitda),
        "Share_Count_CAGR_3Y": share_count_cagr,
        "Revenue_Stability": revenue_stability,
        "EV_EBITDA": safe_positive_divide(enterprise_value, current_ebitda),
        "EV_EBIT": safe_positive_divide(enterprise_value, current_ebit),
        "FCF_Yield": safe_positive_divide(current_fcf, market_cap),
        "Adj_FCF_Yield": safe_positive_divide(current_adj_fcf, market_cap),
    }

    for idx, period in enumerate(periods, start=1):
        result[f"Dep_Factor_Y{idx}"] = rows_by_period[period]["dep_factor"]
        result[f"Fiscal_Y{idx}"] = period[:10]

    result["Intensity_Level"] = intensity_level(result["Avg_Dep_Factor_3Y"])
    result["Score"] = calculate_score(pd.Series(result))
    return result


def screen_tickers(tickers: list[str], config: ScreenerConfig) -> tuple[pd.DataFrame, list[str]]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in tickers:
        try:
            data = get_financials(ticker, use_cache=config.use_cache)
            is_benchmark = ticker == config.benchmark
            results.append(calculate_metrics(ticker, data, years=config.years, is_benchmark=is_benchmark))
        except Exception as exc:  # noqa: BLE001 - CLI should continue across bad tickers
            errors.append(f"{ticker}: {exc}")

    df = pd.DataFrame(results)
    if df.empty:
        return df, errors

    if config.min_roic is not None:
        df = df[df["ROIC_Latest"] >= config.min_roic / 100]
    if config.max_dep_factor is not None:
        df = df[df["Avg_Dep_Factor_3Y"] <= config.max_dep_factor]

    higher_is_better = {
        "ROIC_Latest",
        "ROIC_Avg_3Y",
        "ROIC_Trend_3Y",
        "Gross_Margin",
        "Operating_Margin",
        "EBIT_Margin_Trend_3Y",
        "FCF_EBITDA_Ratio",
        "FCF_Margin",
        "Adj_FCF_EBITDA_Ratio",
        "Adj_FCF_Margin",
        "FCF_Yield",
        "Adj_FCF_Yield",
        "Score",
    }
    ascending = config.rank_by not in higher_is_better
    if config.rank_by in df.columns:
        df = df.sort_values(config.rank_by, ascending=ascending, na_position="last")
    else:
        errors.append(f"Rank column not found: {config.rank_by}; defaulted to Score")
        df = df.sort_values("Score", ascending=False, na_position="last")

    return df.reset_index(drop=True), errors


def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    display = df.copy()
    ratio_columns = [
        "Rev_CAGR_3Y",
        "EBIT_CAGR_3Y",
        "ROIC_Latest",
        "ROIC_Avg_3Y",
        "ROIC_Trend_3Y",
        "Gross_Margin",
        "Operating_Margin",
        "EBIT_Margin_Trend_3Y",
        "FCF_EBITDA_Ratio",
        "FCF_Margin",
        "Adj_FCF_EBITDA_Ratio",
        "Adj_FCF_Margin",
        "SBC_Rev_Ratio",
        "SBC_FCF_Ratio",
        "CapEx_Rev_Ratio",
        "FCF_Yield",
        "Adj_FCF_Yield",
        "D&A_EBIT_Ratio",
        "Share_Count_CAGR_3Y",
        "Revenue_Stability",
    ]
    multiple_columns = ["Avg_Dep_Factor_3Y", "Dep_Factor_TTM", "EV_EBITDA", "EV_EBIT", "Net_Debt_EBITDA", "Score"]

    for col in ratio_columns:
        if col in display.columns:
            display[col] = display[col].map(lambda x: "" if pd.isna(x) else f"{x:.1%}")
    for col in multiple_columns:
        if col in display.columns:
            display[col] = display[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")

    for col in [c for c in display.columns if c.startswith("Dep_Factor_Y")]:
        display[col] = display[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")

    preferred = [
        "Ticker",
        "Company Name",
        "Avg_Dep_Factor_3Y",
        "Dep_Factor_TTM",
        "Rev_CAGR_3Y",
        "EBIT_CAGR_3Y",
        "ROIC_Latest",
        "ROIC_Trend_3Y",
        "Adj_FCF_EBITDA_Ratio",
        "Adj_FCF_Margin",
        "SBC_Rev_Ratio",
        "CapEx_Rev_Ratio",
        "Net_Debt_EBITDA",
        "EV_EBITDA",
        "EV_EBIT",
        "Adj_FCF_Yield",
        "Intensity_Level",
        "Score",
        "FCF_EBITDA_Ratio",
        "FCF_Margin",
        "FCF_Yield",
        "SBC_FCF_Ratio",
        "Gross_Margin",
        "Operating_Margin",
        "EBIT_Margin_Trend_3Y",
        "Share_Count_CAGR_3Y",
        "Revenue_Stability",
    ]
    remaining = [col for col in display.columns if col not in preferred and col != "Is_Benchmark"]
    return display[[col for col in preferred if col in display.columns] + remaining]


def terminal_display_frame(df: pd.DataFrame, terminal_width: int) -> pd.DataFrame:
    display = format_for_display(df)
    selected = [col for col, _, _ in TERMINAL_COLUMNS if col in display.columns]

    for col in DROP_PRIORITY:
        candidate = [candidate_col for candidate_col in selected if candidate_col != col]
        if table_width(display, candidate) <= terminal_width:
            break
        selected = candidate

    terminal = display[selected].rename(
        columns={source: label for source, label, _ in TERMINAL_COLUMNS if source in selected}
    )
    if "Company" in terminal.columns:
        clean_company = terminal["Company"].map(lambda value: " ".join(str(value).split()))
        company_widths = [30, 24, 20, 16, 12]
        for max_company_width in company_widths:
            candidate = terminal.copy()
            candidate["Company"] = clean_company.map(lambda value: truncate(value, max_company_width))
            if rendered_width(candidate) <= terminal_width or max_company_width == company_widths[-1]:
                terminal = candidate
                break
    return terminal


def table_width(df: pd.DataFrame, columns: list[str]) -> int:
    renamed = df[columns].rename(columns={source: label for source, label, _ in TERMINAL_COLUMNS})
    return rendered_width(renamed)


def rendered_width(df: pd.DataFrame) -> int:
    widths = column_widths(df)
    return sum(widths) + (3 * len(widths)) + 1


def column_widths(df: pd.DataFrame) -> list[int]:
    widths: list[int] = []
    for col in df.columns:
        values = [str(value) for value in df[col].fillna("").tolist()]
        widths.append(max(len(str(col)), *(len(value) for value in values)))
    return widths


def truncate(value: str, max_width: int) -> str:
    if len(value) <= max_width:
        return value
    if max_width <= 1:
        return value[:max_width]
    return value[: max_width - 1] + "…"


def render_terminal_table(df: pd.DataFrame) -> str:
    terminal_width = shutil.get_terminal_size((140, 24)).columns
    display = terminal_display_frame(df, terminal_width)
    widths = column_widths(display)
    numeric_columns = set(display.columns) - {"Ticker", "Company", "Level", "Currency"}

    def border(left: str, join: str, right: str) -> str:
        return left + join.join("─" * (width + 2) for width in widths) + right

    def row(values: list[str]) -> str:
        cells = []
        for idx, value in enumerate(values):
            col = display.columns[idx]
            text = str(value)
            cells.append(f" {text.rjust(widths[idx]) if col in numeric_columns else text.ljust(widths[idx])} ")
        return "│" + "│".join(cells) + "│"

    lines = [border("┌", "┬", "┐"), row(list(display.columns)), border("├", "┼", "┤")]
    for _, record in display.iterrows():
        lines.append(row([str(record[col]) for col in display.columns]))
    lines.append(border("└", "┴", "┘"))
    return "\n".join(lines)


def print_table(df: pd.DataFrame, plain: bool = False) -> None:
    display = format_for_display(df)
    if HAS_RICH and not plain:
        console = Console()
        terminal_display = terminal_display_frame(df, console.width)
        table = Table(title="Capital Intensity & Quality Stock Screener", show_lines=False)
        for col in terminal_display.columns:
            justify = "right" if col not in {"Ticker", "Company", "Currency", "Level"} else "left"
            table.add_column(col, justify=justify, no_wrap=col == "Ticker")
        for _, row in terminal_display.iterrows():
            style = None
            if row.get("Level") == "Low":
                style = "green"
            elif row.get("Level") == "Moderate":
                style = "yellow"
            elif row.get("Level") == "High":
                style = "red"
            table.add_row(*[str(row[col]) for col in terminal_display.columns], style=style)
        console.print(table)
    else:
        print(render_terminal_table(df))


def export_results(df: pd.DataFrame, export_path: Path) -> None:
    suffix = export_path.suffix.lower()
    export_path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        df.to_csv(export_path, index=False)
    elif suffix in {".xlsx", ".xls"}:
        try:
            df.to_excel(export_path, index=False)
        except ImportError as exc:
            raise ImportError(
                "Excel export requires an engine such as openpyxl. "
                "Install it with: pip install openpyxl"
            ) from exc
    elif suffix in {".md", ".markdown"}:
        export_path.write_text(to_markdown_table(format_for_display(df)), encoding="utf-8")
    else:
        raise ValueError("Unsupported export format. Use .csv, .xlsx, .xls, .md, or .markdown")


def to_markdown_table(df: pd.DataFrame) -> str:
    """Render a small dependency-free GitHub-flavored Markdown table."""
    text_df = df.fillna("").astype(str)
    headers = list(text_df.columns)
    rows = text_df.values.tolist()
    widths = [
        max(len(header), *(len(row[idx]) for row in rows)) if rows else len(header)
        for idx, header in enumerate(headers)
    ]

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([render_row(headers), separator, *(render_row(row) for row in rows)]) + "\n"


def print_summary(df: pd.DataFrame, errors: list[str]) -> None:
    if df.empty:
        print("No valid companies returned.")
        if errors:
            print("\nSkipped / warnings:")
            for err in errors:
                print(f"  - {err}")
        return

    low = int((df["Intensity_Level"] == "Low").sum())
    moderate = int((df["Intensity_Level"] == "Moderate").sum())
    high = int((df["Intensity_Level"] == "High").sum())
    total = len(df)
    print()
    print(
        f"Summary: {total} stocks screened | "
        f"{low} Low Intensity (<1.4) | {moderate} Moderate (1.4-1.7) | {high} High (>1.7)"
    )
    if errors:
        print("\nSkipped / warnings:")
        for err in errors:
            print(f"  - {err}")


def main() -> int:
    args = parse_args()
    config = ScreenerConfig(
        rank_by=args.rank_by,
        min_roic=args.min_roic,
        max_dep_factor=args.max_dep_factor,
        use_cache=not args.no_cache,
        benchmark=args.benchmark.upper() if args.benchmark else None,
    )

    try:
        tickers = load_tickers(args.tickers, args.file, config.benchmark)
    except Exception as exc:  # noqa: BLE001
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    df, errors = screen_tickers(tickers, config)
    if df.empty:
        print_summary(df, errors)
        return 1

    print_table(df, plain=args.plain)
    print_summary(df, errors)

    if args.export:
        try:
            export_results(df, args.export)
            print(f"\nExported results to {args.export}")
        except Exception as exc:  # noqa: BLE001
            print(f"Export failed: {exc}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
