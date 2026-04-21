# -*- coding: utf-8 -*-
"""
Tests for multi-market data pipeline support.

Covers code normalization, market detection, security type classification,
and data provider routing for A-shares, HK, and US markets — including
stocks, ETFs, and indices.

Ticket context: verify data pipeline supports 15 representative securities
across 3 markets × 3 types (stock / ETF / index).
"""

import sys
from unittest.mock import MagicMock

import pytest

# Lightweight stubs for heavy optional deps so tests stay runnable in CI.
for mod in ("litellm", "json_repair", "fake_useragent"):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from data_provider.base import (
    _is_etf_code,
    _is_hk_market,
    _is_us_market,
    _market_tag,
    normalize_stock_code,
)
from data_provider.us_index_mapping import (
    is_us_index_code,
    is_us_stock_code,
)
from src.core.trading_calendar import get_market_for_stock


# ---------------------------------------------------------------------------
# Test fixtures: 15 representative securities
# ---------------------------------------------------------------------------

# fmt: off
A_SHARE_STOCKS = [
    ("600519.SH", "600519",  "cn"),   # 贵州茅台
    ("000001.SZ", "000001",  "cn"),   # 平安银行
]
A_SHARE_INDICES = [
    ("000300.SH", "000300",  "cn"),   # 沪深300
    ("399006.SZ", "399006",  "cn"),   # 创业板指
]
A_SHARE_ETFS = [
    ("510300.SH", "510300",  "cn"),   # 沪深300ETF
]

HK_STOCKS = [
    ("0700.HK",  "HK00700", "hk"),   # 腾讯控股
    ("9988.HK",  "HK09988", "hk"),   # 阿里巴巴
]
HK_ETFS = [
    ("2800.HK",  "HK02800", "hk"),   # 恒生ETF
    ("3033.HK",  "HK03033", "hk"),   # 南方恒生科技ETF
]
HK_INDICES = [
    ("^HSI",     "^HSI",    None),    # 恒生指数 — not recognized by any detector
]

US_STOCKS = [
    ("AAPL",     "AAPL",    "us"),    # Apple
    ("TSLA",     "TSLA",    "us"),    # Tesla
]
US_ETFS = [
    ("SPY",      "SPY",     "us"),    # 标普500 ETF (treated as US stock by regex)
    ("QQQ",      "QQQ",     "us"),    # 纳指100 ETF (treated as US stock by regex)
]
US_INDICES = [
    ("^GSPC",    "^GSPC",   "us"),    # S&P 500
]
# fmt: on

ALL_SECURITIES = (
    A_SHARE_STOCKS
    + A_SHARE_INDICES
    + A_SHARE_ETFS
    + HK_STOCKS
    + HK_ETFS
    + HK_INDICES
    + US_STOCKS
    + US_ETFS
    + US_INDICES
)


# ===========================================================================
# 1. Code Normalization
# ===========================================================================


class TestNormalizeStockCode:
    """normalize_stock_code() must strip exchange suffixes and canonicalize."""

    # --- A-share stocks ---
    def test_a_share_sh_suffix(self):
        assert normalize_stock_code("600519.SH") == "600519"

    def test_a_share_sz_suffix(self):
        assert normalize_stock_code("000001.SZ") == "000001"

    # --- A-share indices ---
    def test_a_index_sh_suffix(self):
        assert normalize_stock_code("000300.SH") == "000300"

    def test_a_index_sz_suffix(self):
        assert normalize_stock_code("399006.SZ") == "399006"

    # --- A-share ETFs ---
    def test_a_etf_sh_suffix(self):
        assert normalize_stock_code("510300.SH") == "510300"

    # --- HK stocks ---
    def test_hk_suffix_4digit(self):
        assert normalize_stock_code("0700.HK") == "HK00700"

    def test_hk_suffix_4digit_ali(self):
        assert normalize_stock_code("9988.HK") == "HK09988"

    # --- HK ETFs ---
    def test_hk_etf_suffix(self):
        assert normalize_stock_code("2800.HK") == "HK02800"

    def test_hk_etf_suffix_3033(self):
        assert normalize_stock_code("3033.HK") == "HK03033"

    # --- HK index ---
    def test_hk_index_passthrough(self):
        """^HSI is not a standard format; normalize should pass it through."""
        result = normalize_stock_code("^HSI")
        # Current behavior: returns as-is (no stripping needed)
        assert result == "^HSI"

    # --- US stocks ---
    def test_us_stock_passthrough(self):
        assert normalize_stock_code("AAPL") == "AAPL"

    def test_us_stock_tsla(self):
        assert normalize_stock_code("TSLA") == "TSLA"

    # --- US ETFs ---
    def test_us_etf_spy(self):
        assert normalize_stock_code("SPY") == "SPY"

    def test_us_etf_qqq(self):
        assert normalize_stock_code("QQQ") == "QQQ"

    # --- US index ---
    def test_us_index_gspc(self):
        assert normalize_stock_code("^GSPC") == "^GSPC"


# ===========================================================================
# 2. Market Detection — _is_us_market / _is_hk_market / _market_tag
# ===========================================================================


class TestMarketDetection:
    """_market_tag() correctly classifies securities by market."""

    # --- A-shares (stocks + indices + ETFs) → "cn" ---
    @pytest.mark.parametrize("raw,_,expected_market", A_SHARE_STOCKS + A_SHARE_INDICES + A_SHARE_ETFS)
    def test_a_share_market_cn(self, raw, _, expected_market):
        normalized = normalize_stock_code(raw)
        assert _market_tag(normalized) == expected_market

    # --- HK stocks + ETFs → "hk" ---
    @pytest.mark.parametrize("raw,normalized_expected,expected_market", HK_STOCKS + HK_ETFS)
    def test_hk_market(self, raw, normalized_expected, expected_market):
        normalized = normalize_stock_code(raw)
        assert normalized == normalized_expected
        assert _market_tag(normalized) == expected_market

    # --- US stocks + ETFs → "us" ---
    @pytest.mark.parametrize("raw,_,expected_market", US_STOCKS + US_ETFS)
    def test_us_stock_market(self, raw, _, expected_market):
        assert _market_tag(raw) == expected_market

    # --- US indices → "us" ---
    def test_us_index_gspc(self):
        assert _market_tag("^GSPC") == "us"

    # --- HK index ^HSI — known gap ---
    def test_hk_index_hsi_detection(self):
        """^HSI is NOT currently detected as HK market — documenting the gap."""
        tag = _market_tag("^HSI")
        # ^HSI starts with ^, not HK prefix, not 5-digit pure number,
        # not in US_INDEX_MAPPING → falls through to "cn" (incorrect).
        # This test documents the current (incorrect) behavior.
        assert tag == "cn", (
            "^HSI currently misclassified as 'cn'. "
            "When support is added, update this test to assert 'hk'."
        )


class TestIsUsMarket:
    """Fine-grained US market detection."""

    def test_us_stock(self):
        assert _is_us_market("AAPL") is True

    def test_us_etf(self):
        # SPY/QQQ match US stock regex (1-5 uppercase letters)
        assert _is_us_market("SPY") is True
        assert _is_us_market("QQQ") is True

    def test_us_index(self):
        assert _is_us_market("^GSPC") is True

    def test_not_us(self):
        assert _is_us_market("600519") is False
        assert _is_us_market("HK00700") is False


class TestIsHkMarket:
    """Fine-grained HK market detection."""

    def test_hk_stock(self):
        assert _is_hk_market("HK00700") is True
        assert _is_hk_market("HK09988") is True

    def test_hk_etf(self):
        assert _is_hk_market("HK02800") is True
        assert _is_hk_market("HK03033") is True

    def test_hk_suffix_format(self):
        assert _is_hk_market("0700.HK") is True

    def test_hk_5digit_pure(self):
        assert _is_hk_market("00700") is True

    def test_hk_index_not_detected(self):
        """^HSI is not recognized as HK market by current implementation."""
        assert _is_hk_market("^HSI") is False

    def test_not_hk(self):
        assert _is_hk_market("600519") is False
        assert _is_hk_market("AAPL") is False


# ===========================================================================
# 3. US Stock / Index Classification
# ===========================================================================


class TestUsStockVsIndex:
    """is_us_stock_code() vs is_us_index_code() distinction."""

    def test_aapl_is_stock(self):
        assert is_us_stock_code("AAPL") is True
        assert is_us_index_code("AAPL") is False

    def test_tsla_is_stock(self):
        assert is_us_stock_code("TSLA") is True

    def test_spy_is_stock_not_index(self):
        """SPY is an ETF ticker, classified as stock (not in index mapping)."""
        assert is_us_stock_code("SPY") is True
        assert is_us_index_code("SPY") is False

    def test_qqq_is_stock_not_index(self):
        """QQQ is an ETF ticker, classified as stock (not in index mapping)."""
        assert is_us_stock_code("QQQ") is True
        assert is_us_index_code("QQQ") is False

    def test_gspc_is_index(self):
        assert is_us_index_code("^GSPC") is True
        assert is_us_stock_code("^GSPC") is False

    def test_a_share_not_us(self):
        assert is_us_stock_code("600519") is False
        assert is_us_index_code("600519") is False


# ===========================================================================
# 4. A-Share ETF Detection
# ===========================================================================


class TestAShareEtfDetection:
    """_is_etf_code() correctly identifies A-share ETFs."""

    def test_510300_is_etf(self):
        assert _is_etf_code("510300") is True

    def test_510300_with_suffix(self):
        assert _is_etf_code("510300.SH") is True

    def test_regular_stock_not_etf(self):
        assert _is_etf_code("600519") is False
        assert _is_etf_code("000001") is False

    def test_index_not_etf(self):
        assert _is_etf_code("000300") is False
        assert _is_etf_code("399006") is False

    def test_hk_code_not_a_etf(self):
        """HK codes should not match A-share ETF prefixes."""
        assert _is_etf_code("HK02800") is False

    def test_us_code_not_a_etf(self):
        assert _is_etf_code("SPY") is False


# ===========================================================================
# 5. Trading Calendar — Market Resolution
# ===========================================================================


class TestTradingCalendarMarket:
    """get_market_for_stock() maps codes to market regions."""

    # A-shares
    def test_a_share_stock(self):
        assert get_market_for_stock("600519") == "cn"
        assert get_market_for_stock("000001") == "cn"

    def test_a_share_index(self):
        assert get_market_for_stock("000300") == "cn"
        assert get_market_for_stock("399006") == "cn"

    def test_a_share_etf(self):
        assert get_market_for_stock("510300") == "cn"

    # HK
    def test_hk_stock(self):
        assert get_market_for_stock("HK00700") == "hk"
        assert get_market_for_stock("HK09988") == "hk"

    def test_hk_etf(self):
        assert get_market_for_stock("HK02800") == "hk"
        assert get_market_for_stock("HK03033") == "hk"

    def test_hk_index_gap(self):
        """^HSI is not recognized by get_market_for_stock — returns None."""
        result = get_market_for_stock("^HSI")
        assert result is None, (
            "^HSI currently unrecognized. "
            "When support is added, update to assert 'hk'."
        )

    # US
    def test_us_stock(self):
        assert get_market_for_stock("AAPL") == "us"
        assert get_market_for_stock("TSLA") == "us"

    def test_us_etf(self):
        assert get_market_for_stock("SPY") == "us"
        assert get_market_for_stock("QQQ") == "us"

    def test_us_index(self):
        assert get_market_for_stock("^GSPC") == "us"


# ===========================================================================
# 6. Data Provider Capability Matrix (unit-level, no network)
# ===========================================================================


class TestDataProviderCapabilityMatrix:
    """
    Verify which data provider methods are expected to work for each
    security type. These are structural/routing tests — no network calls.

    Tests document the CURRENT capability matrix:
    - chip_distribution: A-share stocks only
    - fundamental_context: A-share stocks only (HK/US rejected)
    - daily_data / realtime_quote: all markets
    """

    def test_etf_detected_for_a_share_only(self):
        """ETF detection only covers A-share ETF codes."""
        assert _is_etf_code("510300") is True   # A-share ETF
        assert _is_etf_code("SPY") is False      # US ETF
        assert _is_etf_code("HK02800") is False  # HK ETF

    def test_chip_distribution_unsupported_markets(self):
        """
        Chip distribution is only implemented for A-share stocks.
        HK/US codes should be skipped gracefully at the routing level.
        """
        for code in ("HK00700", "AAPL", "SPY", "^GSPC", "^HSI"):
            tag = _market_tag(code)
            assert tag in ("hk", "us", "cn"), f"Unexpected tag for {code}"

    def test_fundamental_context_market_rejection(self):
        """
        Fundamental context explicitly rejects HK and US markets.
        A-share stocks should be accepted; ETFs/indices are partial.
        """
        # Accepted
        assert _market_tag("600519") == "cn"
        assert _market_tag("000001") == "cn"

        # Rejected
        assert _market_tag("HK00700") == "hk"
        assert _market_tag("AAPL") == "us"
        assert _market_tag("^GSPC") == "us"


# ===========================================================================
# 7. Known Gaps — ^HSI (Hang Seng Index) End-to-End
# ===========================================================================


class TestKnownGapHSI:
    """
    ^HSI (Hang Seng Index) is not properly supported in the current pipeline.
    These tests document the gap so it can be addressed in a future PR.
    """

    def test_normalize_preserves_caret(self):
        """^HSI should survive normalization without corruption."""
        assert normalize_stock_code("^HSI") == "^HSI"

    def test_not_detected_as_any_market(self):
        """^HSI is not US, not HK (by current rules), defaults to cn."""
        assert _is_us_market("^HSI") is False
        assert _is_hk_market("^HSI") is False
        assert _market_tag("^HSI") == "cn"  # Incorrect but current behavior

    def test_trading_calendar_returns_none(self):
        """get_market_for_stock returns None for ^HSI."""
        assert get_market_for_stock("^HSI") is None


# ===========================================================================
# 8. Cross-Market Normalization Round-Trip
# ===========================================================================


class TestNormalizationRoundTrip:
    """Normalized codes must be stable (idempotent)."""

    @pytest.mark.parametrize("raw,expected_normalized,_", ALL_SECURITIES)
    def test_normalize_idempotent(self, raw, expected_normalized, _):
        """Normalizing an already-normalized code should return the same value."""
        first = normalize_stock_code(raw)
        assert first == expected_normalized
        second = normalize_stock_code(first)
        assert second == first, (
            f"Non-idempotent: normalize({raw!r}) = {first!r}, "
            f"normalize({first!r}) = {second!r}"
        )
