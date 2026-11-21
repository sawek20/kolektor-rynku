import importlib.util, os, sys, json, math, tempfile
from datetime import datetime, timedelta, timezone
tmp = tempfile.mkdtemp()
os.environ["KOLEKTOR_DANE"] = os.path.join(tmp, "rynek"); os.environ["KOLEKTOR_STAN"] = os.path.join(tmp, "s.json")
os.environ["KOLEKTOR_OFFSET"] = os.path.join(tmp, "off.txt")
cfg = os.path.join(tmp, "c.ini")
open(cfg, "w").write("[symbole]\nDASH = coingecko:dash\nNVDA = yahoo:NVDA\nPKN = yahoo:PKN.WA\n[telegram]\ntoken = T:1\nchat_id = 42\nprog_proc = 3.0\n[www]\nhaslo = x\n")
os.environ["KOLEKTOR_CONFIG"] = cfg
sys.path.insert(0, "pkg/usr/lib/kolektor-rynku")
import kolektor_rynku as kr, kolektor_bot as kb
kr.DANE = os.environ["KOLEKTOR_DANE"]
kb.OFFSET = os.environ["KOLEKTOR_OFFSET"]

# 1) 40 dni syntetycznych danych co minutę dla 3 symboli
teraz = datetime.now(timezone.utc).replace(second=0, microsecond=0)
start = teraz - timedelta(days=40)
t = start
rows_by_day = {}
while t <= teraz:
    day = t.date()
    k = (t - start).total_seconds() / 60
    rows_by_day.setdefault(day, []).append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), "DASH", f"{32+math.sin(k/500):.6f}", "USD", "coingecko", "ok"])
    rows_by_day[day].append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), "NVDA", f"{155+k*0.001:.4f}", "USD", "yahoo", "ok"])
    rows_by_day[day].append([t.strftime("%Y-%m-%dT%H:%M:%SZ"), "PKN", "" if k % 97 == 0 else f"{121+math.cos(k/300):.4f}", "PLN", "yahoo", "ok" if k % 97 else "blad:HTTPError"])
    t += timedelta(minutes=1)
import csv
for day, rows in rows_by_day.items():
    d = os.path.join(kr.DANE, f"{day:%Y}", f"{day:%m}"); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"ceny-{day:%Y-%m-%d}.csv"), "w", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(["ts_utc","symbol","cena","waluta","zrodlo","status"]); w.writerows(rows)
print("1 dane syntetyczne:", len(rows_by_day), "dni")

# 2) wykresy 7 i 30 dni
png7 = os.path.join(tmp, "w7.png"); assert kb.zrob_wykres(7, png7)
png30 = os.path.join(tmp, "w30.png"); assert kb.zrob_wykres(30, png30)
s7, s30 = os.path.getsize(png7), os.path.getsize(png30)
assert s7 > 5000 and s30 > 5000, (s7, s30)
print(f"2 wykres 7 dni: {s7} B, wykres 30 dni: {s30} B")
assert kb.zrob_wykres(1, os.path.join(tmp, "x.png")) is True  # ostatni dzień też ma dane

# 3) raport co 30 min: zmiana względem odczytu sprzed 30 min
sent = []
kr.http_get = lambda url, dane_post=None: sent.append(urllib_parse(dane_post)) or ""
import urllib.parse
def urllib_parse(d): return urllib.parse.parse_qs(d.decode())["text"][0]
wiersze = [("DASH", 33.0, "USD", "coingecko", "ok"), ("NVDA", 160.0, "USD", "yahoo", "ok"), ("PKN", None, "PLN", "yahoo", "blad:HTTPError")]
tg = {"token": "T:1", "chat_id": "42", "prog_proc": 3.0}
kr.raport_30min(teraz, wiersze, tg)
print("3 raport:\n" + sent[-1])
assert "DASH: 33 USD" in sent[-1] and "(30 min)" in sent[-1] and "brak kursu" in sent[-1]

# 4) komendy bota
odpowiedzi = []
def fake_api(token, metoda, parametry=None, timeout=None):
    odpowiedzi.append((metoda, parametry)); return {"ok": True, "result": []}
kb.api = fake_api
wyslane_zdj = []
kb.wyslij_wykres = lambda tok, ch, sc, pod: wyslane_zdj.append((sc, pod, os.path.getsize(sc)))
kb.obsluz("T:1", "42", "wykres 7")
kb.obsluz("T:1", "42", "/wykres 30")
kb.obsluz("T:1", "42", "wykres 400")
kb.obsluz("T:1", "42", "kursy")
kb.obsluz("T:1", "42", "pomoc")
print("4 komendy:", [p[1]["text"][:40] for p in odpowiedzi if "text" in (p[1] or {})])
assert [z[1] for z in wyslane_zdj] == ["Wykres 7 dni", "Wykres 30 dni"]
assert any("od 1 do 365" in (p[1] or {}).get("text","") for p in odpowiedzi)

# 5) multipart: poprawne body i boundary
import importlib; kb = importlib.reload(kb); kb.OFFSET = os.environ["KOLEKTOR_OFFSET"]
captured = {}
class Resp:
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def read(self): return b'{"ok": true}'
def fake_open(req, timeout=None):
    captured["body"] = req.data; captured["ct"] = req.headers["Content-type"]; return Resp()
kb.urllib.request.urlopen = fake_open
kb.wyslij_wykres("T:1", "42", png7, "Wykres 7 dni")
bnd = captured["ct"].split("boundary=")[1]
b = captured["body"]
assert b.count(("--" + bnd).encode()) == 4 and b.endswith(("--" + bnd + "--\r\n").encode())
assert b'name="chat_id"\r\n\r\n42' in b and b'name="photo"; filename="wykres.png"' in b
assert open(png7,"rb").read() in b
print("5 multipart OK")

# 6) offset zapisany
kb.zapisz_offset(777); assert kb.wczytaj_offset() == 777
print("6 offset OK")
print("ALL OK")
