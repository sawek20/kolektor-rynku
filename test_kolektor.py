"""Test kolektora: alerty Telegram, świeżość kursów CoinGecko i Yahoo (sesja otwarta/zamknięta)."""
import importlib.util, json, os, sys, tempfile, urllib.error
from datetime import datetime, timezone

tmp = tempfile.mkdtemp()
os.environ["KOLEKTOR_DANE"] = os.path.join(tmp, "rynek")
os.environ["KOLEKTOR_STAN"] = os.path.join(tmp, "stan.json")
cfg = os.path.join(tmp, "config.ini")
open(cfg, "w").write("""[symbole]
DASH = coingecko:dash
ZEC = coingecko:zcash
NVDA = yahoo:NVDA
PKN = yahoo:PKN.WA
[telegram]
token = TESTTOKEN
chat_id = 123
prog_proc = 3.0
""")
os.environ["KOLEKTOR_CONFIG"] = cfg

spec = importlib.util.spec_from_file_location("k", "pkg/usr/lib/kolektor-rynku/kolektor_rynku.py")
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)

sent = []
k.wyslij_telegram = lambda t, txt: sent.append(txt)

# Stan testu: czas „teraz” w sekundach UTC i dane źródeł.
CZAS = {"ts": datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone.utc)}
state = {
    "ceny": {"dash": 33.0, "zcash": 700.0},
    "cg_aktualizacja_s_wstecz": 60,   # świeżość CoinGecko w sekundach
    "fail": False,
    # Yahoo: ostatni punkt i to, czy sesja NVDA trwa
    "nvda_ostatni_s_wstecz": 60,
    "nvda_cena": 155.20,
    "sesja_otwarta": True,
}

k._teraz = lambda: CZAS["ts"]


def yahoo_json(cena, ostatni_s_wstecz, sesja_otwarta, waluta):
    teraz = int(CZAS["ts"].timestamp())
    start, koniec = (teraz - 3600, teraz + 3600) if sesja_otwarta else (teraz - 90000, teraz - 45000)
    t_ost = teraz - ostatni_s_wstecz
    return json.dumps({"chart": {"result": [{
        "meta": {"currency": waluta,
                 "currentTradingPeriod": {"regular": {"start": start, "end": koniec}}},
        "timestamp": [t_ost - 120, t_ost - 60, t_ost],
        "indicators": {"quote": [{"close": [cena - 1, None, cena]}]},
    }], "error": None}})


def fake_get(url, dane_post=None):
    if "api.coingecko.com" in url:
        if state["fail"]:
            raise urllib.error.HTTPError(url, 429, "rate", None, None)
        ts = int(CZAS["ts"].timestamp()) - state["cg_aktualizacja_s_wstecz"]
        return json.dumps({
            "dash": {"usd": state["ceny"]["dash"], "last_updated_at": ts},
            "zcash": {"usd": state["ceny"]["zcash"], "last_updated_at": ts},
        })
    if "finance.yahoo.com" in url:
        if "NVDA" in url:
            return yahoo_json(state["nvda_cena"], state["nvda_ostatni_s_wstecz"], state["sesja_otwarta"], "USD")
        return yahoo_json(121.28, 60, True, "PLN")
    raise AssertionError(url)


k.http_get = fake_get


def run(minute):
    CZAS["ts"] = datetime(2026, 11, 21, 10, minute, 0, tzinfo=timezone.utc)
    return k.main()


def wiersze_csv():
    sciezka = os.path.join(tmp, "rynek", "2026", "11", "ceny-2026-11-21.csv")
    with open(sciezka, encoding="utf-8") as f:
        return f.read().splitlines()


# 1) alerty: skok ceny, brak źródła, powrót
assert run(0) == 0
state["ceny"]["dash"] = 34.0          # +3.03% -> alert
assert run(1) == 0
state["fail"] = True                  # CoinGecko nie odpowiada -> alert o braku
assert run(2) == 0
state["fail"] = False                 # wraca -> alert
assert run(3) == 0
assert any("DASH: +3.03%" in s for s in sent), sent
assert any("brak kursu" in s for s in sent), sent
assert any("znów działa" in s for s in sent), sent
print("1 alerty OK")

# 2) CoinGecko z przestarzałym kursem nie trafia jako ok
state["cg_aktualizacja_s_wstecz"] = 7200
state["ceny"]["dash"] = 35.0
assert run(4) == 0
ostatnie = wiersze_csv()[-4:]
assert any(";DASH;;USD;coingecko;blad:nieaktualne" in w for w in ostatnie), ostatnie
assert any(";ZEC;;USD;coingecko;blad:nieaktualne" in w for w in ostatnie), ostatnie
state["cg_aktualizacja_s_wstecz"] = 60
print("2 CoinGecko stale -> blad:nieaktualne OK")

# 3) Yahoo, sesja otwarta, ostatni punkt 2 h stary -> nieaktualne
state["nvda_ostatni_s_wstecz"] = 7200
assert run(5) == 0
assert any(";NVDA;;;yahoo;blad:nieaktualne" in w for w in wiersze_csv()[-4:])
state["nvda_ostatni_s_wstecz"] = 60
print("3 Yahoo stale w sesji -> blad:nieaktualne OK")

# 4) Yahoo, sesja zamknięta, ostatni punkt stary -> ostatnie zamknięcie jest poprawne
state["sesja_otwarta"] = False
state["nvda_ostatni_s_wstecz"] = 40000
state["nvda_cena"] = 125.53
assert run(6) == 0
assert any(";NVDA;125.53;USD;yahoo;ok" in w for w in wiersze_csv()[-4:]), wiersze_csv()[-4:]
state["sesja_otwarta"] = True
state["nvda_ostatni_s_wstecz"] = 60
print("4 Yahoo poza sesją -> ostatnie zamknięcie ok OK")

csv_path = os.path.join(tmp, "rynek", "2026", "11", "ceny-2026-11-21.csv")
print("\n".join(wiersze_csv()[-6:]))
print("ALERTY:", *sent, sep="\n  ")
print("OK")
