import requests, os
API_KEY = os.environ.get("ELEVENLABS_API_KEY", "sk_25898bd3b1f6155c71d424ec263fab609b85a29f3b5b5366")

# Try both auth methods
for header_name in ["xi-api-key", "Authorization"]:
    val = API_KEY if header_name == "xi-api-key" else f"Bearer {API_KEY}"
    r = requests.get("https://api.elevenlabs.io/v1/voices", headers={header_name: val})
    print(f"{header_name}: {r.status_code}")
    if r.status_code == 200:
        voices = r.json().get("voices", [])
        print(f"Total: {len(voices)}")
        for v in voices:
            labels = v.get("labels", {})
            lang = labels.get("language", labels.get("accent", ""))
            print(f"  {v['voice_id']} | {v['name']:<22} | {lang}")
        break
    else:
        print(f"  Error: {r.text[:100]}")
