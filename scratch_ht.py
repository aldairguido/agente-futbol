import urllib.request, re

def obtener_marcador_ht_feed(match_id):
    if not match_id:
        return None, None
    url = f"https://global.flashscore.ninja/2/x/feed/df_sui_1_{match_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-fsign": "SW9D1eZo",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            content = resp.read().decode("utf-8")
            # First block usually has AC1st HalfIG3IH1
            for b in content.split("~"):
                if "1st Half" in b:
                    m = re.search(r"IG[^\d]*(\d+)", b)
                    m2 = re.search(r"IH[^\d]*(\d+)", b)
                    if m and m2:
                        return int(m.group(1)), int(m2.group(1))
    except Exception as e:
        pass
    return None, None

gl_ht, gv_ht = obtener_marcador_ht_feed("vXrY11bl")
print(f"Bragantino vs Cruzeiro (vXrY11bl) -> HT: {gl_ht} - {gv_ht}")
