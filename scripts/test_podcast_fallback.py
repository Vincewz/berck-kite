"""Tests hors réseau : python -m unittest discover -s scripts -p 'test_podcast_fallback.py'."""

import base64
import copy
import contextlib
import io
import importlib.util
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests


# Ne jamais lire une vraie clé ni créer de répertoire pendant l'import.
_SPEC = importlib.util.spec_from_file_location(
    "podcast_under_test", Path(__file__).with_name("generate_daily_podcast.py")
)
podcast = importlib.util.module_from_spec(_SPEC)
with patch.dict("os.environ", {"GEMINI_API_KEY": "", "PODCAST_OFFLINE": "1"}), patch.object(Path, "mkdir"):
    _SPEC.loader.exec_module(podcast)


def bulletin_data():
    peak = {
        "h": "15:00", "kt": 18, "gkt": 25, "dir": "Ouest", "deg": 270,
        "weather": "ensoleillé", "temp": 17,
    }
    return {
        "now": {
            "offshore": None, "wave_h": 1.2, "wave_p": 7,
            "wave_dir": "Ouest", "sea_temp": 15,
        },
        "peak": peak,
        "kite_window": [peak.copy()],
        "tomorrow": {
            "weather": "couvert", "temp_min": 10, "temp_max": 16,
            "dir": "Nord-Ouest", "kt_max": 20, "gkt_max": 28,
        },
        "tides": [
            {"type": "PM", "timeStr": "12:10", "h": 8.1},
            {"type": "BM", "timeStr": "18:30", "h": 1.7},
        ],
        "sunrise": "08:00", "sunset": "18:00",
    }


class PodcastFallbackTests(unittest.TestCase):
    def setUp(self):
        # Protection globale : même un appel non mocké échoue avant toute connexion.
        self.addCleanup(patch.stopall)
        # Les erreurs simulées ne doivent pas créer de fausses annotations CI.
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        patch.object(
            requests.sessions.Session, "request",
            side_effect=AssertionError("Tout accès réseau est interdit dans ces tests"),
        ).start()
        self.post = patch.object(podcast.requests, "post").start()
        self.get = patch.object(podcast.requests, "get").start()
        self.run = patch.object(
            podcast.subprocess, "run",
            side_effect=AssertionError("Aucun programme externe ne doit être exécuté"),
        ).start()
        patch.object(podcast, "GEMINI_KEY", "fake-unit-test-key").start()
        patch.object(podcast, "OFFLINE", False).start()
        self.data = bulletin_data()
        self.date = "mardi 17 novembre"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / "voice.wav"

    def http_error(self, status=402):
        response = Mock(status_code=status, text="Payment required (fixture)")
        response.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status}", response=response
        )
        return response

    def enable_local_audio(self):
        def synthesize(command, **kwargs):
            # Une WAV synthétique suffit : aucun binaire ni équipement audio requis.
            with wave.open(command[command.index("-w") + 1], "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(b"\x00\x00" * 100)
            return Mock(returncode=0)
        self.run.side_effect = synthesize

    def test_fallback_script_contains_only_available_facts(self):
        original = copy.deepcopy(self.data)
        text = podcast.fallback_script(self.data, self.date)
        for fact in (
            self.date, "15:00", "18 nœuds", "25 nœuds", "Ouest", "17 degrés",
            "1.2 mètres", "7 secondes", "15 degrés", "12:10", "8.1 mètres",
            "18:30", "1.7 mètres", "Demain", "10 à 16 degrés", "Nord-Ouest",
            "20 nœuds", "28 nœuds",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, text)
        self.assertTrue(text.endswith("Bonne journée les Berckois."))
        self.assertNotIn("kitesurfs", text)
        self.assertNotIn("vent de terre", text)
        self.assertEqual(self.data, original)
        self.post.assert_not_called()

    def test_fallback_script_missing_optional_data(self):
        self.data.update(peak=None, tides=[], kite_window=[])
        self.data["now"]["sea_temp"] = None
        text = podcast.fallback_script(self.data, self.date)
        self.assertIn("prévisions horaires de la journée sont indisponibles", text)
        self.assertNotIn("température de l'eau", text)
        self.assertNotIn("prochaine pleine mer", text)
        self.assertNotIn("prochaine basse mer", text)

    def test_fallback_script_mentions_kites_and_offshore_when_present(self):
        for current_offshore, slot_deg in (("vent de terre", 270), (None, 120)):
            with self.subTest(current_offshore=current_offshore, slot_deg=slot_deg):
                self.data["now"]["offshore"] = current_offshore
                self.data["kite_window"][0]["deg"] = slot_deg
                text = podcast.fallback_script(self.data, self.date, {"kites_detected": 2})
                self.assertIn("Quelques kitesurfs à l'eau hier.", text)
                self.assertIn("Un vent de terre est prévu", text)

    def test_missing_key_or_offline_skips_all_gemini_calls(self):
        for key, offline in (("", False), ("fake-unit-test-key", True), ("", True)):
            with self.subTest(key_present=bool(key), offline=offline), patch.object(
                podcast, "GEMINI_KEY", key
            ), patch.object(podcast, "OFFLINE", offline):
                self.enable_local_audio()
                script, use_gemini = podcast.make_script(self.data, self.date)
                self.assertFalse(use_gemini)
                self.assertEqual(script, podcast.fallback_script(self.data, self.date))
                self.assertEqual(
                    podcast.make_voice(script, self.wav, use_gemini),
                    "eSpeak NG français (secours)",
                )
                self.post.assert_not_called()

    def test_http_402_script_failure_does_not_attempt_gemini_tts(self):
        self.post.return_value = self.http_error()
        self.enable_local_audio()
        script, use_gemini = podcast.make_script(self.data, self.date)
        self.assertFalse(use_gemini)
        self.assertEqual(script, podcast.fallback_script(self.data, self.date))
        self.assertEqual(podcast.make_voice(script, self.wav, use_gemini), "eSpeak NG français (secours)")
        self.post.assert_called_once()
        self.assertIn("flash-lite:generateContent", self.post.call_args.args[0])
        self.run.assert_called_once_with(
            ["espeak-ng", "-v", "fr-fr", "-s", "155", "-w", str(self.wav), "--stdin"],
            input=script, text=True, check=True, capture_output=True,
        )

    def test_script_network_failure_does_not_attempt_second_paid_call(self):
        self.post.side_effect = requests.ConnectionError("fixture: offline")
        self.enable_local_audio()
        script, use_gemini = podcast.make_script(self.data, self.date)
        self.assertFalse(use_gemini)
        podcast.make_voice(script, self.wav, use_gemini)
        self.post.assert_called_once()

    def test_gemini_script_and_tts_success(self):
        script_response = Mock()
        script_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "**Bulletin Gemini.**"}]}}]
        }
        pcm = b"\x01\x00" * 100
        voice_response = Mock()
        voice_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"inlineData": {
                "data": base64.b64encode(pcm).decode("ascii")
            }}]}}]
        }
        self.post.side_effect = [script_response, voice_response]
        script, use_gemini = podcast.make_script(self.data, self.date)
        self.assertEqual(script, "Bulletin Gemini.")
        self.assertTrue(use_gemini)
        self.assertEqual(podcast.make_voice(script, self.wav, use_gemini), "Kore")
        self.assertEqual(self.post.call_count, 2)
        self.assertIn("flash-tts-preview:generateContent", self.post.call_args.args[0])
        self.run.assert_not_called()
        with wave.open(str(self.wav), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), 24000)
            self.assertEqual(audio.readframes(100), pcm)

    def test_gemini_tts_http_402_uses_local_voice(self):
        self.post.return_value = self.http_error()
        self.enable_local_audio()
        self.assertEqual(podcast.make_voice("Texte", self.wav, True), "eSpeak NG français (secours)")
        self.post.assert_called_once()
        self.run.assert_called_once()

    def test_gemini_tts_connection_error_uses_local_voice(self):
        self.post.side_effect = requests.Timeout("fixture timeout")
        self.enable_local_audio()
        self.assertEqual(podcast.make_voice("Texte", self.wav, True), "eSpeak NG français (secours)")
        self.post.assert_called_once()
        self.run.assert_called_once()

    def test_local_tts_rejects_missing_or_header_only_audio(self):
        self.run.side_effect = None
        for header_only in (False, True):
            with self.subTest(header_only=header_only):
                if header_only:
                    self.wav.write_bytes(b"\x00" * 44)
                with self.assertRaisesRegex(RuntimeError, "pas produit d'audio"):
                    podcast.make_voice("Texte", self.wav, False)
        self.post.assert_not_called()

    def test_late_weather_window_never_includes_tomorrow(self):
        now = datetime(2026, 11, 17, 23, 45, tzinfo=ZoneInfo("Europe/Paris"))
        midnight = now.replace(hour=0, minute=0)
        times = [(midnight + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
        # Les vents de demain sont volontairement extrêmes pour détecter une fuite.
        weather = {
            "current": {
                "wind_speed_10m": 18.52, "wind_gusts_10m": 27.78,
                "wind_direction_10m": 270, "temperature_2m": 12, "weather_code": 0,
            },
            "hourly": {
                "time": times, "wind_speed_10m": [18.52] * 24 + [185.2] * 24,
                "wind_gusts_10m": [27.78] * 48, "wind_direction_10m": [270] * 48,
                "temperature_2m": [12] * 48, "weather_code": [0] * 48,
                "precipitation_probability": [0] * 48,
            },
            "daily": {
                "weather_code": [0, 3], "wind_speed_10m_max": [18.52, 185.2],
                "wind_gusts_10m_max": [27.78, 200], "temperature_2m_max": [12, 15],
                "temperature_2m_min": [5, 7],
                "sunrise": ["2026-11-17T08:00"], "sunset": ["2026-11-17T17:00"],
            },
        }
        marine = {"current": {}, "hourly": {"wave_height": [1] * 24 + [9] * 24}}
        self.get.side_effect = [Mock(json=Mock(return_value=weather)), Mock(json=Mock(return_value=marine))]
        data = podcast.fetch_all(now)
        self.assertEqual([slot["h"] for slot in data["kite_window"]], [f"{h:02}:00" for h in range(10, 19)])
        self.assertTrue(all(slot["kt"] == 10 and slot["wave_h"] == 1 for slot in data["kite_window"]))
        self.assertEqual(data["peak"]["kt"], 10)
        self.assertTrue(all(slot["kt"] == 10 for slot in data["hours"]))
        self.assertEqual(data["tomorrow"]["kt_max"], 100)
        self.assertEqual(self.get.call_count, 2)
        for call in self.get.call_args_list:
            self.assertIn("timezone=Europe/Paris", call.args[0])

    def test_tide_hours_use_paris_winter_and_summer_offsets(self):
        paris = ZoneInfo("Europe/Paris")
        for month, offset_hours in ((1, 1), (7, 2)):
            with self.subTest(month=month):
                utc_now = datetime(2026, month, 15, 0, tzinfo=timezone.utc)
                local_now = utc_now.astimezone(paris)
                self.assertEqual(local_now.utcoffset(), timedelta(hours=offset_hours))
                utc_tides = podcast.next_tides(utc_now)
                local_tides = podcast.next_tides(local_now)
                self.assertEqual(len(local_tides), 4)
                for utc_tide, local_tide in zip(utc_tides, local_tides):
                    utc_clock = datetime.strptime(utc_tide["timeStr"], "%H:%M")
                    expected = (utc_clock + timedelta(hours=offset_hours)).strftime("%H:%M")
                    self.assertEqual(local_tide["timeStr"], expected)
                    self.assertEqual(local_tide["type"], utc_tide["type"])
                    self.assertEqual(local_tide["h"], utc_tide["h"])


if __name__ == "__main__":
    unittest.main()
