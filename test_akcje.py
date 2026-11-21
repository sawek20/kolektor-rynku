import importlib.util, json, os, tempfile
tmp = tempfile.mkdtemp()
os.environ["KOLEKTOR_DANE"] = os.path.join(tmp, "rynek"); os.environ["KOLEKTOR_STAN"] = os.path.join(tmp, "s.json")
cfg = os.path.join(tmp, "c.ini"); open(cfg, "w").write("[krypto]\nDASH = dash\n[akcje]\nNVDA = NVDA\nPKN = PKN.WA\n")
os.environ["KOLEKTOR_CONFIG"] = cfg
spec = importlib.util.spec_from_file_location("k", "pkg/usr/lib/kolektor-rynku/kolektor_rynku.py")
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
def fake(url, dane_post=None):
    if "yahoo" in url:
        sym = url.split("/chart/")[1].split("?")[0]
        if sym == "PKN.WA":
            return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 121.28, "currency": "PLN"}}]}})
        return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 155.2, "currency": "USD"}}]}})
    return json.dumps({"dash": {"usd": 32.6}})
k.http_get = fake
print(k.main())
print(open(os.path.join(tmp, "rynek", "2026", "11", os.listdir(os.path.join(tmp,"rynek","2026","11"))[0])).read())
