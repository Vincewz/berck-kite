"""
Fast GitHub Actions gate for YOLO inference.
Only installs requests; torch/ultralytics are installed later when this passes.
"""
import os
import sys

import requests

from kite_conditions import current_conditions, now_paris


def set_output(key, value):
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
    print(f"  output: {key}={value}")


now = now_paris()
print(f"Verification conditions ({now.strftime('%H:%M')} Paris)...")

try:
    conditions = current_conditions(now)
except requests.exceptions.RequestException:
    print("API meteo indisponible - skip")
    set_output("conditions_ok", "false")
    set_output("reason", "API meteo indisponible")
    sys.exit(0)

print(
    f"  Vent : {conditions['wind_kt']:.1f}kt  "
    f"Dir : {conditions['wind_dir']}deg  "
    f"Temp : {conditions['temp_c']}C  "
    f"Pluie : {conditions['rain_mm']}mm"
)
print(
    f"  Lumiere : {conditions['sunrise'].strftime('%H:%M')}"
    f"-{conditions['sunset'].strftime('%H:%M')} +30min"
)

if not conditions["ok"]:
    print(f"Conditions KO : {conditions['reason']}")
    set_output("conditions_ok", "false")
    set_output("reason", conditions["reason"])
else:
    print("Conditions OK - lancement YOLO")
    set_output("conditions_ok", "true")
    set_output("reason", "ok")
