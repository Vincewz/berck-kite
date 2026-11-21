"""Tests météo hors réseau : python -m unittest discover -s scripts -p 'test_podcast_weather.py'."""

import contextlib
import importlib.util
import io
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, call, patch
from zoneinfo import ZoneInfo

import requests


# Même import isolé que test_podcast_fallback : aucune clé ni création de dossier.
_SPEC = importlib.util.spec_from_file_location(
    "podcast_weather_under_test", Path(__file__).with_name("generate_daily_podcast.py")
)
podcast = importlib.util.module_from_spec(_SPEC)
with patch.dict("os.environ", {"GEMINI_API_KEY": "", "PODCAST_OFFLINE": "1"}), patch.object(
    Path, "mkdir"
), patch.object(
    requests.sessions.Session, "request", side_effect=AssertionError("Réseau interdit")
):
    _SPEC.loader.exec_module(podcast)


PRIMARY = "https://api.open-meteo.com/v1/forecast?fixture=1"
FALLBACK = "https://api.open-meteo.com/v1/meteofrance?fixture=1"
TIMEOUT = (10, 30)


def response(payload):
    result = Mock()
    result.json.return_value = payload
    return result


def http_error(status):
    result = response({})
    result.status_code = status
    result.raise_for_status.side_effect = requests.HTTPError(
        f"HTTP {status} (fixture)", response=result
    )
    return result


def weather_fixture(now, rain_probability=0):
    """Réponse synthétique Open-Meteo, également valable sur /meteofrance."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    times = [(midnight + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
    return {
        "current": {
            "wind_speed_10m": 18.52, "wind_gusts_10m": 27.78,
            "wind_direction_10m": 270, "temperature_2m": 12, "weather_code": 0,
        },
        "hourly": {
            "time": times, "wind_speed_10m": [18.52] * 48,
            "wind_gusts_10m": [27.78] * 48, "wind_direction_10m": [270] * 48,
            "temperature_2m": [12] * 48, "weather_code": [0] * 48,
            "precipitation_probability": [rain_probability] * 48,
        },
        "daily": {
            "weather_code": [0, 3], "wind_speed_10m_max": [18.52, 37.04],
            "wind_gusts_10m_max": [27.78, 46.3], "temperature_2m_max": [12, 15],
            "temperature_2m_min": [5, 7],
            "sunrise": [f"{now:%Y-%m-%d}T08:00"],
            "sunset": [f"{now:%Y-%m-%d}T17:00"],
        },
    }


class PodcastWeatherTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        # Le moindre appel oublié échoue avant toute connexion réelle.
        self.enterContext(patch.object(
            requests.sessions.Session, "request", side_effect=AssertionError("Réseau interdit")
        ))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Réseau interdit")))
        self.get = self.enterContext(patch.object(podcast.requests, "get"))
        self.sleep = self.enterContext(patch.object(podcast.time, "sleep"))
        self.now = datetime(2026, 11, 17, 6, tzinfo=ZoneInfo("Europe/Paris"))

    def assert_get_urls(self, urls):
        self.assertEqual(self.get.call_args_list, [call(url, timeout=TIMEOUT) for url in urls])

    def test_success_checks_http_status_and_timeout(self):
        result = response({"weather": "fixture"})
        self.get.return_value = result
        self.assertEqual(podcast.fetch_json(PRIMARY), {"weather": "fixture"})
        self.assert_get_urls([PRIMARY])
        result.raise_for_status.assert_called_once_with()
        result.json.assert_called_once_with()
        self.assertEqual(result.method_calls, [call.raise_for_status(), call.json()])
        self.sleep.assert_not_called()

    def test_timeout_then_success(self):
        result = response({"ok": True})
        self.get.side_effect = [requests.Timeout("fixture timeout"), result]
        self.assertEqual(podcast.fetch_json(PRIMARY, FALLBACK), {"ok": True})
        self.assert_get_urls([PRIMARY] * 2)
        result.raise_for_status.assert_called_once_with()
        self.sleep.assert_called_once_with(1)

    def test_three_primary_failures_then_fallback_success(self):
        result = response({"source": "meteofrance"})
        self.get.side_effect = [requests.ConnectionError("fixture TLS")] * 3 + [result]
        self.assertEqual(podcast.fetch_json(PRIMARY, FALLBACK), {"source": "meteofrance"})
        self.assert_get_urls([PRIMARY] * 3 + [FALLBACK])
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])
        result.raise_for_status.assert_called_once_with()

    def test_all_failures_propagate_last_exception_after_six_calls(self):
        errors = [requests.Timeout(f"fixture timeout {i}") for i in range(6)]
        self.get.side_effect = errors
        with self.assertRaises(requests.Timeout) as caught:
            podcast.fetch_json(PRIMARY, FALLBACK)
        self.assertIs(caught.exception, errors[-1])
        self.assert_get_urls([PRIMARY] * 3 + [FALLBACK] * 3)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2), call(1), call(2)])

    def test_http_504_then_success(self):
        failed = http_error(504)
        succeeded = response({"ok": True})
        self.get.side_effect = [failed, succeeded]
        self.assertEqual(podcast.fetch_json(PRIMARY, FALLBACK), {"ok": True})
        self.assert_get_urls([PRIMARY] * 2)
        failed.raise_for_status.assert_called_once_with()
        failed.json.assert_not_called()
        succeeded.raise_for_status.assert_called_once_with()
        self.sleep.assert_called_once_with(1)

    def test_http_400_propagates_without_retry_or_fallback(self):
        failed = http_error(400)
        self.get.return_value = failed
        with self.assertRaises(requests.HTTPError) as caught:
            podcast.fetch_json(PRIMARY, FALLBACK)
        self.assertIs(caught.exception, failed.raise_for_status.side_effect)
        self.assert_get_urls([PRIMARY])
        failed.raise_for_status.assert_called_once_with()
        failed.json.assert_not_called()
        self.sleep.assert_not_called()

    def test_invalid_json_then_success(self):
        invalid = response(None)
        invalid.json.side_effect = requests.exceptions.JSONDecodeError("fixture invalid", "x", 0)
        succeeded = response({"ok": True})
        self.get.side_effect = [invalid, succeeded]
        self.assertEqual(podcast.fetch_json(PRIMARY), {"ok": True})
        self.assert_get_urls([PRIMARY] * 2)
        invalid.raise_for_status.assert_called_once_with()
        succeeded.raise_for_status.assert_called_once_with()
        self.sleep.assert_called_once_with(1)

    def test_invalid_payload_then_success(self):
        for payload in ([], {"error": True, "reason": "fixture"}):
            with self.subTest(payload=payload):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.side_effect = [response(payload), response({"ok": True})]
                self.assertEqual(podcast.fetch_json(PRIMARY), {"ok": True})
                self.assert_get_urls([PRIMARY] * 2)
                self.sleep.assert_called_once_with(1)

    def test_fetch_all_reaches_meteofrance_after_primary_failures(self):
        weather = response(weather_fixture(self.now))
        marine = response({"current": {}, "hourly": {"wave_height": [1] * 48}})
        self.get.side_effect = [requests.Timeout("fixture")] * 3 + [weather, marine]
        data = podcast.fetch_all(self.now)
        urls = [entry.args[0] for entry in self.get.call_args_list]
        self.assertEqual(len(urls), 5)
        self.assertIn("/v1/forecast?", urls[0])
        self.assertEqual(urls[:3], [urls[0]] * 3)
        self.assertEqual(urls[3], urls[0].replace("/v1/forecast", "/v1/meteofrance"))
        self.assertIn("marine-api.open-meteo.com/v1/marine?", urls[4])
        self.assert_get_urls(urls)
        self.assertTrue(all("timezone=Europe/Paris" in url for url in urls))
        self.assertEqual(data["now"]["kt"], 10)
        self.assertEqual(data["now"]["sea_temp"], None)
        self.assertEqual(data["peak"]["wave_h"], 1)
        self.assertEqual(data["tomorrow"]["kt_max"], 20)
        self.assertEqual(len(data["kite_window"]), 9)
        weather.raise_for_status.assert_called_once_with()
        marine.raise_for_status.assert_called_once_with()
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_fetch_all_marine_three_failures_propagate_without_fallback(self):
        errors = [requests.ConnectionError(f"fixture marine {i}") for i in range(3)]
        self.get.side_effect = [response(weather_fixture(self.now))] + errors
        with self.assertRaises(requests.ConnectionError) as caught:
            podcast.fetch_all(self.now)
        self.assertIs(caught.exception, errors[-1])
        urls = [entry.args[0] for entry in self.get.call_args_list]
        self.assertEqual(len(urls), 4)
        self.assertIn("/v1/forecast?", urls[0])
        self.assertIn("marine-api.open-meteo.com/v1/marine?", urls[1])
        self.assertEqual(urls[1:], [urls[1]] * 3)
        self.assert_get_urls(urls)
        self.assertFalse(any("/v1/meteofrance" in url for url in urls))
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_fetch_all_meteofrance_accepts_unknown_precipitation_probability(self):
        # Une probabilité absente reste inconnue : ne pas inventer 0 % de pluie.
        self.get.side_effect = [requests.Timeout("fixture primaire")] * 3 + [
            response(weather_fixture(self.now, rain_probability=None)),
            response({"current": {}, "hourly": {"wave_height": [1] * 48}}),
        ]
        data = podcast.fetch_all(self.now)
        self.assertIsNone(data["hours"][0]["rain_pct"])


if __name__ == "__main__":
    unittest.main()
