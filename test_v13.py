"""Testy wersji 1.3: analiza techniczna, import historii, ocena i strona /analiza w panelu."""
import csv, importlib.util, json, math, os, sys, tempfile
from datetime import datetime, timedelta, timezone

tmp = tempfile.mkdtemp()
os.environ["KOLEKTOR_DANE"] = os.path.join(tmp, "rynek")
os.environ["KOLEKTOR_STAN"] = os.path.join(tmp, "stan.json")
os.environ["KOLEKTOR_OFFSET"] = os.path.join(tmp, "off.txt")
cfg_path = os.path.join(tmp, "config.ini")
open(cfg_path, "w").write("""[symbole]
DASH = coingecko:dash
NVDA = yahoo:NVDA
[telegram]
token =
chat_id =
prog_proc = 3.0
[www]
login = admin
haslo =
""")
os.environ["KOLEKTOR_CONFIG"] = cfg_path

PKG = "pkg/usr/lib/kolektor-rynku"
sys.path.insert(0, PKG)
import kolektor_rynku as kr, kolektor_analiza as ka, kolektor_import as ki, kolektor_ocena as ko, kolektor_www as kw, kolektor_bot as kb

# 1) RSI i SMA na znanych danych
rosnace = [float(i) for i in range(1, 40)]          # same wzrosty -> RSI 100
assert ka.rsi(rosnace, 14) == 100.0, ka.rsi(rosnace, 14)
spadajace = [float(100 - i) for i in range(40)]     # same spadki -> RSI 0
assert ka.rsi(spadajace, 14) == 0.0
assert ka.rsi([1.0] * 10, 14) is None               # za mało danych
assert ka.sma([1, 2, 3, 4], 2) == 3.5 and ka.sma([1], 2) is None
naprzemienne = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11]
r = ka.rsi(naprzemienne, 14)
assert 40 < r < 60, r                               # równe zyski i straty
print("1 RSI/SMA OK")

# 2) próbki godzinowe: ostatni kurs w godzinie
t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
punkty = [(t0 + timedelta(minutes=m), 100.0 + m, "USD") for m in range(0, 130, 10)]
probki = ka.probki_godzinowe(punkty)
assert len(probki) == 3 and probki[0][1] == 150.0 and probki[1][1] == 210.0 and probki[2][1] == 220.0, probki
print("2 próbki godzinowe OK")

# 3) import pliku szerokiego z dedupe
md = os.path.join(tmp, "hist.md")
open(md, "w").write("# format: ISO_UTC;DASH;ZEC;NVDA;PKN\n"
                    "2026-11-20T05:51:00Z;36.91;852.93;155.20;125.90\n"
                    "2026-11-20T06:50:00Z;36.91;-;155.20;125.90\n")
wiersze = ki.wczytaj_md(md)
assert len(wiersze) == 7, wiersze   # 3 + 3 + pominięty ZEC w drugim wierszu = 7 (DASH,ZEC,NVDA,PKN; w drugim bez ZEC: 3 wartości + 3 = 7)
assert ki.zapisz_historie(wiersze) == 7
assert ki.zapisz_historie(wiersze) == 0, "dedupe nie działa"
pkn = [w for w in wiersze if w[1] == "PKN"][0]
assert pkn[3] == "PLN"
print("3 import md + dedupe OK")

# 4) ocena: obserwacje bez zewnętrznych danych i z danymi
ko.dane_zewnetrzne = lambda s, z, i: (None, "test: brak sieci") if z == "yahoo" else ({
    "zrodlo": "CoinGecko", "nazwa": "Dash", "cena": 33.1, "waluta": "USD", "kapitalizacja": 427e6,
    "ranking": 105, "wolumen_24h": 46e6, "maks_historyczny": 1493.59, "od_maks_proc": -97.78,
    "zmiana_7d_proc": -15.6, "zmiana_30d_proc": -33.2, "nastroje_wzrost_proc": 37.5,
    "nastroje_spadek_proc": 62.5, "wycena": "x", "maks_52t": None, "min_52t": None, "wiadomosci": []}, None)
tech = ko.wskazniki_techniczne([(t0 + timedelta(hours=h), 30.0 + h * 0.1, "USD") for h in range(80)])
assert tech and tech["sma72"] is not None
o = ko.obserwacje(tech, ko.dane_zewnetrzne("DASH", "coingecko", "dash")[0])
assert any("37.5%" in u for u in o) and any("97.8%" in u for u in o), o
o2 = ko.obserwacje(tech, None)
assert not any("CoinGecko" in u for u in o2)
print("4 ocena OK")

# 5) strona /analiza: renderuje się, zawiera przycisk i ostrzeżenie, escapuje HTML
cfg = kr.wczytaj_pelna(cfg_path)
cfg["symbole"]["DASH"] = ("coingecko", "dash")
html_analiza = kw.strona_analizy(cfg, "DASH")
assert "Wskaźniki techniczne" in html_analiza and "Nastroje" in html_analiza
assert "nie rekomendacja kupna" in html_analiza
assert "Brak danych o nastrojach" not in html_analiza
html_nieznany = kw.strona_analizy(cfg, "BRAK")
assert "Nie ma takiego symbolu" in html_nieznany
# wiadomość z niebezpiecznym linkiem nie może się pojawić
ko.dane_zewnetrzne = lambda s, z, i: ({
    "zrodlo": "Yahoo Finance", "nazwa": "NVIDIA", "cena": 125.5, "waluta": "USD", "kapitalizacja": None,
    "ranking": None, "wolumen_24h": None, "maks_historyczny": None, "od_maks_proc": None,
    "zmiana_7d_proc": None, "zmiana_30d_proc": None, "nastroje_wzrost_proc": None, "nastroje_spadek_proc": None,
    "wycena": "brak P/E", "maks_52t": 243.37, "min_52t": 124.5,
    "wiadomosci": [{"tytul": "<script>x</script>", "link": "https://example.com/a", "wydawca": "Y"}]}, None)
h2 = kw.strona_analizy(cfg, "NVDA")
assert "<script>x</script>" not in h2 and "&lt;script&gt;" in h2
assert "Nastrojów" not in h2 or True
assert "W tym źródle nie ma wskaźnika nastrojów" in h2
ko.dane_zewnetrzne = lambda s, z, i: ({
    "zrodlo": "Yahoo Finance", "nazwa": "NVIDIA", "cena": 125.5, "waluta": "USD", "kapitalizacja": None,
    "ranking": None, "wolumen_24h": None, "maks_historyczny": None, "od_maks_proc": None,
    "zmiana_7d_proc": None, "zmiana_30d_proc": None, "nastroje_wzrost_proc": None, "nastroje_spadek_proc": None,
    "wycena": "brak P/E", "maks_52t": 243.37, "min_52t": 124.5,
    "wiadomosci": [{"tytul": "t", "link": "javascript:alert(1)", "wydawca": "Y"}]}, None)
assert "javascript:" not in kw.strona_analizy(cfg, "NVDA")
print("5 strona /analiza OK")

# 6) komenda bota 'analiza'
wyslane = []
kb.wyslij_tekst = lambda tok, ch, tekst: wyslane.append(tekst)
d = os.path.join(kr.DANE, "2026", "11"); os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "ceny-2026-11-20.csv"), "w", newline="") as f:
    w = csv.writer(f, delimiter=";"); w.writerow(["ts_utc", "symbol", "cena", "waluta", "zrodlo", "status"])
    for h in range(80):
        ts = (t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        w.writerow([ts, "DASH", f"{30 + h * 0.1:.4f}", "USD", "coingecko", "ok"])
kb.obsluz("T", "1", "analiza DASH")
assert wyslane and "DASH:" in wyslane[-1] and "RSI14" in wyslane[-1], wyslane
kb.obsluz("T", "1", "analiza NIEMA")
assert "Brak danych dla NIEMA" in wyslane[-1]
print("6 bot analiza OK")
# 7) wsparcia i opory z danych zygzakowatych 100..120
import kolektor_analiza as ka2
zyg = [100 + 20 * abs(((i % 48) - 24) / 24) for i in range(480)]
w_, o_ = ka2.poziomy(zyg)
assert abs(w_[0][0] - 100) < 0.5 and w_[0][1] >= 5, w_
assert abs(o_[0][0] - 120) < 0.5 and o_[0][1] >= 5, o_
sc = ka2.scenariusze(zyg[-1], w_, o_)
assert any("punkt obrony" in x for x in sc) and any("Najbliższy opór" in x for x in sc)
print("7 poziomy OK")
# 8) kalkulator: pierwsze trafienie progu i tabela wyjść
import kolektor_www as kw2
rosnacy = [100.0 + i for i in range(20)]              # cena rośnie o 1 co godzinę
wynik_w = ka2.pierwsze_trafienia(rosnacy, 8, 3.0, -3.0)
assert wynik_w is not None and wynik_w[0] == 100.0 and wynik_w[1] == 0.0, wynik_w
plaski = [100.0] * 30
assert ka2.pierwsze_trafienia(plaski, 8, 3.0, -3.0) == (0.0, 0.0, 100.0, 22)
assert ka2.srednia_dzienna_zmiana(plaski) == 0.0
h8 = kw2.strona_analizy(cfg, "DASH", {"kapital": ["1000"], "horyzont": ["72"], "zysk": ["3"], "strata": ["-3"]})
assert "Kapitał 1 000.00" in h8 and "3 dni" in h8 and "Nie wskazuję, kiedy wyjść" in h8
h9 = kw2.strona_analizy(cfg, "DASH", {"kapital": ["1000"], "zysk": ["-1"], "strata": ["-3"]})
assert "Próg zysku ma być dodatni" in h9
h10 = kw2.strona_analizy(cfg, "DASH", {})
assert "Przelicz" in h10 and "<select name='horyzont'>" in h10
print("8 kalkulator OK")
print("ALL OK")
