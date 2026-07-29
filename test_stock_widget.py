import json
import ssl
import tempfile
import unittest
from pathlib import Path

from stock_widget import (
    Quote,
    StockWidget,
    clean_settings,
    load_settings,
    normalize_code,
    parse_quote,
    query_channels,
    save_settings,
    threshold_reached,
    twse_ssl_context,
)


SAMPLE = {
    "c": "2330",
    "n": "台積電",
    "ex": "tse",
    "z": "2280.0000",
    "y": "2350.0000",
    "d": "20260728",
    "t": "13:30:00",
}


class StockWidgetTest(unittest.TestCase):
    def test_codes_and_market_channels(self):
        self.assertEqual(normalize_code(" 大盤 "), "TAIEX")
        self.assertEqual(normalize_code("00631l"), "00631L")
        self.assertEqual(
            query_channels(["TAIEX", "2330"]),
            ["tse_t00.tw", "tse_2330.tw", "otc_2330.tw"],
        )
        with self.assertRaises(ValueError):
            normalize_code("2330;rm")

    def test_quote_change_uses_previous_close(self):
        quote = parse_quote(SAMPLE)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "2330")
        self.assertAlmostEqual(quote.change_pct, -2.9787234)

    def test_missing_current_trade_does_not_become_zero_change(self):
        message = {**SAMPLE, "z": "-", "pz": "-"}
        self.assertIsNone(parse_quote(message))
        quote = parse_quote(message, use_previous_close=True)
        self.assertIsNotNone(quote)
        self.assertFalse(quote.has_current_price)
        self.assertEqual(quote.price, 2350.0)
        self.assertEqual(quote.change_pct, 0.0)

    def test_signed_thresholds_and_disabled_zero(self):
        self.assertTrue(threshold_reached(-3.2, -3))
        self.assertFalse(threshold_reached(-2.9, -3))
        self.assertTrue(threshold_reached(5.1, 5))
        self.assertFalse(threshold_reached(99, 0))
    def test_twse_tls_keeps_certificate_and_hostname_checks(self):
        context = twse_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)


    def test_threshold_notification_fires_once_per_trading_day(self):
        widget = object.__new__(StockWidget)
        widget.settings = {"items": [{"code": "2330", "threshold": -3.0}]}
        widget.quotes = {
            "2330": Quote(
                code="2330",
                name="TSMC",
                exchange="tse",
                price=96.8,
                previous_close=100.0,
                change_pct=-3.2,
                trade_date="20260728",
                trade_time="10:00:00",
            )
        }
        widget.alerted = set()
        notices = []
        widget.show_notification = lambda title, body: notices.append((title, body))

        widget.notify_reached_thresholds()
        widget.notify_reached_thresholds()
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0][0])
        self.assertEqual(notices[0][1], "2330 TSMC  -3.20%")

    def test_settings_are_sanitized_and_saved_atomically(self):
        raw = {
            "refresh_seconds": 2,
            "always_on_top": False,
            "items": [
                {"code": "2330", "threshold": "-4"},
                {"code": "bad code", "threshold": 3},
            ],
        }
        cleaned = clean_settings(raw)
        self.assertEqual(cleaned["refresh_seconds"], 10)
        self.assertEqual([item["code"] for item in cleaned["items"]], ["TAIEX", "2330"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(cleaned, path)
            self.assertEqual(load_settings(path), json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
