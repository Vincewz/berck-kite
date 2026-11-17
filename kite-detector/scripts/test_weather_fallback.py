"""Régressions météo hors réseau : python -m unittest discover -s kite-detector/scripts."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, mock_open, patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import requests


SCRIPTS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "_weather_fallback_conditions", SCRIPTS / "kite_conditions.py"
)
conditions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(conditions)
PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 17, 12, tzinfo=PARIS)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        # Toute requête oubliée par les mocks échoue avant un accès réseau.
        self.network = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Accès réseau interdit dans ces tests"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)
        self.stdout = io.StringIO()
        self.output = contextlib.redirect_stdout(self.stdout)
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def assert_endpoints(self, calls):
        self.assertEqual(
            [urlsplit(call.args[0]).path for call in calls],
            ["/v1/forecast", "/v1/meteofrance"],
        )
        self.assertEqual(
            urlsplit(calls[0].args[0]).query,
            urlsplit(calls[1].args[0]).query,
        )


class CurrentWeatherFallbackTests(OfflineTestCase):
    def test_request_exception_uses_meteofrance(self):
        payload = {"current": {"wind_speed_10m": 30}}
        with patch.object(
            conditions, "retry_json",
            side_effect=[requests.RequestException("primaire indisponible"), payload],
        ) as fetch:
            self.assertIs(conditions.current_weather_payload(), payload)
        self.assert_endpoints(fetch.call_args_list)
        for call in fetch.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": 20})
        query = parse_qs(urlsplit(fetch.call_args_list[0].args[0]).query)
        self.assertEqual(query["daily"], ["sunrise,sunset"])
        self.assertEqual(query["timezone"], ["Europe/Paris"])

    def test_primary_success_does_not_use_fallback(self):
        payload = {"current": {}}
        with patch.object(conditions, "retry_json", return_value=payload) as fetch:
            self.assertIs(conditions.current_weather_payload(), payload)
        fetch.assert_called_once()
        self.assertEqual(urlsplit(fetch.call_args.args[0]).path, "/v1/forecast")

    def test_both_failures_propagate_final_exception(self):
        final_error = requests.ConnectionError("Météo-France indisponible")
        with patch.object(
            conditions, "retry_json",
            side_effect=[requests.Timeout("primaire"), final_error],
        ) as fetch:
            with self.assertRaises(requests.ConnectionError) as raised:
                conditions.current_weather_payload()
        self.assertIs(raised.exception, final_error)
        self.assert_endpoints(fetch.call_args_list)


class ConditionsGateTests(OfflineTestCase):
    def run_gate(self, **mock_kwargs):
        output_file = mock_open()
        with (
            patch.dict(sys.modules, {"kite_conditions": conditions}),
            patch.object(conditions, "now_paris", return_value=NOW),
            patch.object(conditions, "current_conditions", **mock_kwargs) as current,
            patch.dict(os.environ, {"GITHUB_OUTPUT": "/unused/mock-output"}),
            patch("builtins.open", output_file),
        ):
            try:
                runpy.run_path(str(SCRIPTS / "check_conditions.py"), run_name="__main__")
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        current.assert_called_once_with(NOW)
        writes = "".join(call.args[0] for call in output_file().write.call_args_list)
        return code, writes

    def test_unavailable_weather_exits_one(self):
        code, writes = self.run_gate(side_effect=requests.Timeout("deux sources KO"))
        self.assertEqual(code, 1)
        self.assertIn("conditions_ok=false\n", writes)
        self.assertIn("reason=API meteo indisponible\n", writes)
        self.assertIn("::error::", self.stdout.getvalue())

    def test_unfavorable_weather_exits_zero(self):
        code, writes = self.run_gate(return_value={
            "ok": False,
            "reason": "vent insuffisant",
            "wind_kt": 2.0,
            "wind_dir": 270,
            "temp_c": 10,
            "rain_mm": 0,
            "sunrise": NOW.replace(hour=8),
            "sunset": NOW.replace(hour=17),
        })
        self.assertEqual(code, 0)
        self.assertIn("conditions_ok=false\n", writes)
        self.assertIn("reason=vent insuffisant\n", writes)
        self.assertNotIn("::error::", self.stdout.getvalue())


class ForecastFallbackTests(OfflineTestCase):
    @staticmethod
    def hourly_payload():
        # Trois jours complets : vérifie aussi l'exclusion des jours adjacents.
        times = [
            (NOW.replace(hour=0) + timedelta(days=day, hours=hour)).strftime("%Y-%m-%dT%H:%M")
            for day in (-1, 0, 1)
            for hour in range(24)
        ]
        return {"hourly": {
            "time": times,
            "wind_speed_10m": [30] * len(times),
            "wind_gusts_10m": [40] * len(times),
            "wind_direction_10m": [270] * len(times),
            # Les jours voisins sont distincts pour détecter un mauvais filtrage.
            "temperature_2m": [5] * 24 + [10] * 24 + [15] * 24,
        }}

    def forecast_patches(self, responses):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        fetch = stack.enter_context(patch("requests.Session.get", side_effect=responses))
        write = stack.enter_context(patch.object(Path, "write_text", autospec=True))
        mkdir = stack.enter_context(patch.object(Path, "mkdir", autospec=True))
        stack.enter_context(patch("datetime.datetime", FixedDateTime))
        return fetch, write, mkdir

    def response(self):
        response = Mock()
        response.json.return_value = self.hourly_payload()
        return response

    def test_timeout_falls_back_and_writes_identical_copies(self):
        response = self.response()
        fetch, write, mkdir = self.forecast_patches([
            requests.Timeout("primaire"), response,
        ])
        runpy.run_path(str(SCRIPTS / "forecast_kite.py"), run_name="__main__")
        self.assert_endpoints(fetch.call_args_list)
        for call in fetch.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": (10, 30)})
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(write.call_count, 2)
        first, second = write.call_args_list
        root = SCRIPTS.parent.parent
        self.assertEqual(first.args[0], root / "berck-kite" / "kite_forecast.json")
        self.assertEqual(second.args[0], root / "kite_forecast.json")
        self.assertEqual(first.args[1], second.args[1])
        mkdir.assert_called_once_with(root / "berck-kite", parents=True, exist_ok=True)
        self.assertEqual(json.loads(first.args[1])["date"], "2026-11-17")

    def test_both_failures_propagate_without_writing(self):
        final_error = requests.ConnectionError("Météo-France indisponible")
        fetch, write, mkdir = self.forecast_patches([
            requests.Timeout("primaire"), final_error,
        ])
        with self.assertRaises(requests.ConnectionError) as raised:
            runpy.run_path(str(SCRIPTS / "forecast_kite.py"), run_name="__main__")
        self.assertIs(raised.exception, final_error)
        self.assert_endpoints(fetch.call_args_list)
        write.assert_not_called()
        mkdir.assert_not_called()

    def test_primary_success_keeps_eight_slots_for_correct_day(self):
        fetch, write, _ = self.forecast_patches([self.response()])
        runpy.run_path(str(SCRIPTS / "forecast_kite.py"), run_name="__main__")
        fetch.assert_called_once()
        self.assertEqual(urlsplit(fetch.call_args.args[0]).path, "/v1/forecast")
        forecast = json.loads(write.call_args_list[0].args[1])
        self.assertEqual(forecast["date"], "2026-11-17")
        self.assertEqual(forecast["generated_at"], NOW.isoformat())
        slots = forecast["slots"]
        self.assertEqual(len(slots), 8)
        self.assertEqual([slot["hour"] for slot in slots], [f"{h:02d}:00" for h in range(10, 18)])
        self.assertTrue(all(slot["temp"] == 10 for slot in slots))
        self.assertTrue(all(slot["favorable"] for slot in slots))
        self.assertEqual(forecast["favorable_hours"], [slot["hour"] for slot in slots])
        self.assertTrue(forecast["kite_likely"])
        self.assertEqual(forecast["windows"], [{"start": "10:00", "end": "17:00", "hours": 8}])


if __name__ == "__main__":
    unittest.main()
