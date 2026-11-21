#!/usr/bin/env python3
"""Uzupełnianie historii kursów w plikach CSV kolektora.

  python3 kolektor_import.py md PLIK      # stary format szeroki: ISO_UTC;DASH;ZEC;NVDA;PKN, '-' = brak
  python3 kolektor_import.py api [DNI]    # CoinGecko market_chart i Yahoo (1h) dla symboli z config.ini

Wpisy trafiają do tych samych plików dziennych co kolektor, z zrodlo=import i status=ok,
więc wykresy i analiza widzą je od razu. Pary (czas, symbol), które już są w pliku, są pomijane.
Tryb api uruchamiaj na serwerze, bo wymaga dostępu do internetu i config.ini.
"""
import csv
import os
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolektor_rynku as kr  # noqa: E402

KOLEJNOSC_MD = ["DASH", "ZEC", "NVDA", "PKN"]
WALUTA_MD = {"PKN": "PLN"}  # reszta w pliku szerokim jest w USD
PAUZA_CG_S = 2.0  # CoinGecko w wersji darmowej ogranicza liczbę zapytań na minutę


def wczytaj_md(sciezka):
    """Zwraca [(czas_utc, symbol, cena, waluta)] z pliku szerokiego. Komentarze '#' i puste linie są pomijane."""
    wiersze = []
    with open(sciezka, encoding="utf-8") as f:
        for linia in f:
            linia = linia.strip()
            if not linia or linia.startswith("#"):
                continue
            czesci = linia.split(";")
            if len(czesci) != len(KOLEJNOSC_MD) + 1:
                continue
            czas = datetime.strptime(czesci[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            for symbol, wartosc in zip(KOLEJNOSC_MD, czesci[1:]):
                wartosc = wartosc.strip()
                if wartosc in ("", "-"):
                    continue
                wiersze.append((czas, symbol, float(wartosc), WALUTA_MD.get(symbol, "USD")))
    return wiersze


def _istniejace_pary(dzien):
    """Pary (ts, symbol) już zapisane w pliku danego dnia."""
    sciezka = os.path.join(kr.DANE, f"{dzien:%Y}", f"{dzien:%m}", f"ceny-{dzien:%Y-%m-%d}.csv")
    if not os.path.isfile(sciezka):
        return set()
    with open(sciezka, encoding="utf-8", newline="") as f:
        return {(w[0], w[1]) for w in csv.reader(f, delimiter=";") if len(w) == 6}


def zapisz_historie(wiersze, zrodlo="import"):
    """wiersze: [(czas_utc, symbol, cena, waluta)]. Zwraca liczbę zapisanych wierszy."""
    po_dniu = defaultdict(list)
    for czas, symbol, cena, waluta in sorted(wiersze, key=lambda w: (w[0], w[1])):
        po_dniu[czas.date()].append((czas, symbol, cena, waluta))

    zapisane = 0
    for dzien, grupa in sorted(po_dniu.items()):
        istniejace = _istniejace_pary(dzien)
        for czas, symbol, cena, waluta in grupa:
            ts = czas.strftime("%Y-%m-%dT%H:%M:%SZ")
            if (ts, symbol) in istniejace:
                continue
            kr.zapisz(czas, [(symbol, cena, waluta, zrodlo, "ok")])
            istniejace.add((ts, symbol))
            zapisane += 1
    return zapisane


def _zakres_yahoo(dni):
    for limit, zakres in ((5, "5d"), (30, "1mo"), (90, "3mo"), (180, "6mo")):
        if dni <= limit:
            return zakres
    return "1y"


def pobierz_historie_api(symbole, dni):
    """CoinGecko: godzinowe ceny z ostatnich dni. Yahoo: świece 1h z zakresu dni."""
    wiersze = []
    for symbol, (zrodlo, identyfikator) in sorted(symbole.items()):
        try:
            if zrodlo == "coingecko":
                url = (
                    "https://api.coingecko.com/api/v3/coins/"
                    + urllib.parse.quote(identyfikator)
                    + "/market_chart?"
                    + urllib.parse.urlencode({"vs_currency": "usd", "days": dni})
                )
                dane = kr.json.loads(kr.http_get(url))
                for ms, cena in dane["prices"]:
                    czas = datetime.fromtimestamp(ms / 1000, timezone.utc).replace(minute=0, second=0, microsecond=0)
                    wiersze.append((czas, symbol, float(cena), "USD"))
                time.sleep(PAUZA_CG_S)
            else:
                url = (
                    "https://query1.finance.yahoo.com/v8/finance/chart/"
                    + urllib.parse.quote(identyfikator)
                    + "?interval=1h&range=" + _zakres_yahoo(dni)
                )
                wynik = kr.json.loads(kr.http_get(url))["chart"]["result"][0]
                waluta = wynik["meta"]["currency"]
                for t, cena in zip(wynik.get("timestamp") or [], wynik["indicators"]["quote"][0]["close"]):
                    if cena is None:
                        continue
                    czas = datetime.fromtimestamp(t, timezone.utc).replace(minute=0, second=0, microsecond=0)
                    wiersze.append((czas, symbol, float(cena), waluta))
        except Exception as exc:  # jeden symbol nie zatrzymuje reszty
            print(f"{symbol}: {exc}", file=sys.stderr)
    return wiersze


def main(argv):
    if len(argv) < 2 or argv[1] not in ("md", "api"):
        print(__doc__)
        return 2
    if argv[1] == "md":
        if len(argv) != 3:
            print("użycie: kolektor_import.py md PLIK")
            return 2
        wiersze = wczytaj_md(argv[2])
    else:
        dni = int(argv[2]) if len(argv) > 2 else 30
        cfg = kr.wczytaj_pelna(kr.CONFIG)
        wiersze = pobierz_historie_api(cfg["symbole"], dni)
    zapisane = zapisz_historie(wiersze)
    print(f"odczytano {len(wiersze)}, zapisano nowych {zapisane}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
