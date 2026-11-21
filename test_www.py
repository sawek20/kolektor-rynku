import base64, importlib.util, os, sys, tempfile, threading, urllib.request, urllib.parse, urllib.error, stat, json
tmp = tempfile.mkdtemp()
os.environ["KOLEKTOR_DANE"] = os.path.join(tmp, "rynek")
os.environ["KOLEKTOR_STAN"] = os.path.join(tmp, "stan.json")
cfg_path = os.path.join(tmp, "config.ini")
# stary format (sprzed 1.1) z tokenem
open(cfg_path, "w").write("[krypto]\nDASH = dash\nZEC = zcash\n[akcje]\nNVDA = nvda.us USD\nPKN = PKN.WA PLN\n[telegram]\ntoken = 123:ABCDEFGH\nchat_id = 42\nprog_proc = 3.0\n")
os.environ["KOLEKTOR_CONFIG"] = cfg_path
sys.path.insert(0, "pkg/usr/lib/kolektor-rynku")
import kolektor_rynku as kr
import kolektor_www as kw
kw.CONFIG = cfg_path; kw.DANE = os.environ["KOLEKTOR_DANE"]

# 1) migracja starego formatu
cfg = kr.wczytaj_pelna(cfg_path)
assert cfg["symbole"] == {"DASH": ("coingecko","dash"), "ZEC": ("coingecko","zcash"), "NVDA": ("yahoo","nvda.us"), "PKN": ("yahoo","PKN.WA")}, cfg["symbole"]
print("1 migracja OK:", cfg["symbole"])

# 2) init: ustawia hasło, zapisuje format nowy, plik 0640
kw.HASLO_HOSTA = os.path.join(tmp, "haslo.txt")
assert kw.init(cfg_path) == 0
cfg = kr.wczytaj_pelna(cfg_path)
haslo = [l.split(": ",1)[1] for l in open(kw.HASLO_HOSTA).read().splitlines() if l.startswith("haslo")][0]
assert kw.sprawdz_haslo(haslo, cfg["www"]["haslo"]) and not kw.sprawdz_haslo("zle", cfg["www"]["haslo"])
assert stat.S_IMODE(os.stat(cfg_path).st_mode) == 0o640
assert "[symbole]" in open(cfg_path).read()
print("2 init OK, plik", oct(stat.S_IMODE(os.stat(cfg_path).st_mode)))

# 3) kolektor czyta nowy config i zbiera (atrapy sieci)
def fake(url, dane_post=None):
    if "coingecko" in url: return json.dumps({"dash": {"usd": 32.6}, "zcash": {"usd": 770.1}})
    if "yahoo" in url:
        sym = url.split("/chart/")[1].split("?")[0]
        return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 155.2 if sym=="NVDA" else 121.28, "currency": "USD" if sym=="NVDA" else "PLN"}}]}})
    raise AssertionError(url)
kr.http_get = fake; kw.kr.http_get = fake
kr.main()
print("3 zbieranie OK")

# 4) serwer HTTP + auth + dodanie/usuniecie symbolu + wykres + CSV
srv = kw.http.server.ThreadingHTTPServer(("127.0.0.1", 0), kw.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
auth = "Basic " + base64.b64encode(f"admin:{haslo}".encode()).decode()

def req(path, data=None, login=None):
    login = auth if login is None else login
    h = {"Authorization": login} if login else {}
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(base + path, data=body, headers=h)
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NoRedir)
    try:
        with op.open(r) as resp: return resp.status, resp.read().decode(), resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Location")

st, _, _ = req("/", login=""); assert st == 401, st
st, body, _ = req("/"); assert st == 200 and "DASH" in body and "NVDA" in body, st
print("4a auth OK, strona główna 200")

st, _, loc = req("/", {"akcja":"dodaj","symbol":"tsla","zrodlo":"yahoo","identyfikator":"TSLA"})
assert st == 303 and "info=" in loc, (st, loc)
assert kr.wczytaj_pelna(cfg_path)["symbole"]["TSLA"] == ("yahoo","TSLA")
st, _, loc = req("/", {"akcja":"dodaj","symbol":"BAD SYM","zrodlo":"yahoo","identyfikator":"X"})
assert "blad=" in loc, loc
st, _, loc = req("/", {"akcja":"dodaj","symbol":"XRP","zrodlo":"coingecko","identyfikator":"Ripple Bad"})
assert "blad=" in loc
st, _, loc = req("/", {"akcja":"zmien","symbol":"DASH","zrodlo":"coingecko","identyfikator":"dash"})
assert "info=" in loc
st, _, loc = req("/", {"akcja":"usun","symbol":"TSLA"})
assert "info=" in loc and "TSLA" not in kr.wczytaj_pelna(cfg_path)["symbole"]
print("4b dodaj/zmień/usuń OK, walidacja OK")

st, _, loc = req("/", {"akcja":"telegram","chat_id":"42","prog_proc":"2,5","token":""})
tg = kr.wczytaj_pelna(cfg_path)["telegram"]
assert tg["token"] == "123:ABCDEFGH" and tg["prog_proc"] == 2.5, tg
st, _, loc = req("/", {"akcja":"telegram","chat_id":"abc"}); assert "blad=" in loc
st, _, loc = req("/", {"akcja":"telegram","wyczysc":"on","chat_id":"42","prog_proc":"3"})
assert kr.wczytaj_pelna(cfg_path)["telegram"]["token"] == ""
print("4c Telegram OK (token zostaje, wyczyść działa)")

st, _, loc = req("/", {"akcja":"konto","login":"admin","haslo":"krotkie"}); assert "blad=" in loc
st, _, loc = req("/", {"akcja":"konto","login":"admin","haslo":"nowehaslo123"}); assert "info=" in loc
assert kw.sprawdz_haslo("nowehaslo123", kr.wczytaj_pelna(cfg_path)["www"]["haslo"])
auth = "Basic " + base64.b64encode(b"admin:nowehaslo123").decode()
st, _, _ = req("/"); assert st == 200
print("4d konto/hasło OK")

st, body, _ = req("/symbol?s=DASH"); assert st == 200 and "<svg" in body or "Za mało" in body
st, body, _ = req("/csv?dzien=2026-11-21"); print("   csv status:", st)
st, _, _ = req("/csv?dzien=../../etc"); assert st == 400
srv.shutdown()
print("ALL OK")
