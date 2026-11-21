#!/usr/bin/env python3
"""kolektor-rynku: co minutę zapisuje kursy krypto i akcji do pliku CSV.

Konfiguracja: /etc/kolektor-rynku/config.ini (zarządzana też z panelu www, kolektor_www.py).
  [symbole]  SYMBOL = zrodlo:identyfikator   (zrodlo: coingecko albo yahoo)
  [telegram] token, chat_id, prog_proc
  [www]      listen, port, login, haslo (hash pbkdf2)
Dane:  /srv/dane/rynek/RRRR/MM/ceny-RRRR-MM-DD.csv (jeden plik na dobę UTC)
Stan:  /var/lib/kolektor-rynku/stan.json (ostatnia cena i status każdego symbolu)

Format wiersza (separator ';'):
    ts_utc;symbol;cena;waluta;zrodlo;status
    2026-11-21T10:51:00Z;NVDA;155.2;USD;yahoo;ok
    2026-11-21T10:51:00Z;DASH;;USD;coingecko;blad:HTTPError
Brak ceny = pusta kolumna cena, a powód jest w kolumnie status.

Alerty Telegram (sekcja [telegram]; wiadomości mają prefiks [KOLEKTOR]):
  - symbol przeszedł z 'ok' w błąd albo wrócił z błędu,
  - cena zmieniła się o więcej niż prog_proc % względem poprzedniej poprawnej wartości.
Skrypt nie używa zewnętrznych bibliotek.
"""
import configparser
import csv
import fcntl
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WARSZAWA = ZoneInfo("Europe/Warsaw")
CONFIG = os.environ.get("KOLEKTOR_CONFIG", "/etc/kolektor-rynku/config.ini")
DANE = os.environ.get("KOLEKTOR_DANE", "/srv/dane/rynek")
STAN = os.environ.get("KOLEKTOR_STAN", "/var/lib/kolektor-rynku/stan.json")
HTTP_TIMEOUT = 20
# Yahoo odrzuca domyślny User-Agent Pythona, więc podajemy zwykłą przeglądarkę.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) kolektor-rynku/1.0"
NAGLOWEK = ["ts_utc", "symbol", "cena", "waluta", "zrodlo", "status"]
ZRODLA = ("coingecko", "yahoo")
NAGLOWEK_KONFIGU = """# Kolektor kursów krypto i akcji. Zmiany wchodzą w życie od następnej minuty (bez restartu).
# Zarządzane z panelu www, ale można też edytować ręcznie.
# [symbole]: SYMBOL = zrodlo:identyfikator
#   coingecko:<id monety w CoinGecko, np. dash, zcash>   (cena w USD)
#   yahoo:<symbol Yahoo, np. NVDA, PKN.WA>                (waluta z API)
"""


def http_get(url, dane_post=None):
    req = urllib.request.Request(url, data=dane_post, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


# --- konfiguracja -----------------------------------------------------------

def parsuj_zrodlo(wartosc):
    """'coingecko:dash' -> ('coingecko', 'dash'). Rzuca ValueError przy złym wpisie."""
    zrodlo, sep, identyfikator = wartosc.strip().partition(":")
    zrodlo = zrodlo.strip().lower()
    identyfikator = identyfikator.strip()
    if not sep or zrodlo not in ZRODLA or not identyfikator:
        raise ValueError(f"zły wpis '{wartosc}': oczekiwano 'coingecko:id' albo 'yahoo:symbol'")
    return zrodlo, identyfikator


def wczytaj_pelna(sciezka=CONFIG):
    cp = configparser.ConfigParser(interpolation=None)
    if not cp.read(sciezka, encoding="utf-8"):
        raise FileNotFoundError(f"brak pliku konfiguracji: {sciezka}")

    symbole = {}
    if cp.has_section("symbole"):
        for symbol, wartosc in cp["symbole"].items():
            symbole[symbol.upper()] = parsuj_zrodlo(wartosc)
    else:  # format sprzed wersji 1.1: [krypto] i [akcje]
        if cp.has_section("krypto"):
            for symbol, cg_id in cp["krypto"].items():
                if cg_id.strip():
                    symbole[symbol.upper()] = ("coingecko", cg_id.strip())
        if cp.has_section("akcje"):
            for symbol, wartosc in cp["akcje"].items():
                czesci = wartosc.split()
                if czesci:
                    symbole[symbol.upper()] = ("yahoo", czesci[0])

    tg = cp["telegram"] if cp.has_section("telegram") else {}
    telegram = {
        "token": tg.get("token", "").strip(),
        "chat_id": tg.get("chat_id", "").strip(),
        "prog_proc": float(tg.get("prog_proc", "3.0") or 3.0),
    }
    ww = cp["www"] if cp.has_section("www") else {}
    www = {
        "listen": ww.get("listen", "0.0.0.0").strip(),
        "port": int(ww.get("port", "8088") or 8088),
        "login": ww.get("login", "admin").strip(),
        "haslo": ww.get("haslo", "").strip(),
    }
    return {"symbole": symbole, "telegram": telegram, "www": www}


def zapisz_pelna(sciezka, cfg):
    """Zapisuje cały config atomowo. Plik dostaje 0640, bo zawiera token i hash hasła."""
    linie = [NAGLOWEK_KONFIGU, "[symbole]"]
    for symbol in sorted(cfg["symbole"]):
        zrodlo, identyfikator = cfg["symbole"][symbol]
        linie.append(f"{symbol} = {zrodlo}:{identyfikator}")
    tg, ww = cfg["telegram"], cfg["www"]
    linie += [
        "",
        "[telegram]",
        "# Token i chat_id bota (zostaw token pusty, żeby wyłączyć alerty).",
        f"token = {tg['token']}",
        f"chat_id = {tg['chat_id']}",
        f"prog_proc = {tg['prog_proc']}",
        "",
        "[www]",
        f"listen = {ww['listen']}",
        f"port = {ww['port']}",
        f"login = {ww['login']}",
        f"haslo = {ww['haslo']}",
        "",
    ]
    tmp = sciezka + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(linie))
    os.chmod(tmp, 0o640)
    os.replace(tmp, sciezka)


# --- pobieranie -------------------------------------------------------------

MAX_OPOZNIENIE_S = 900  # kurs starszy niż 15 min (w godzinach obrotu) nie jest traktowany jako aktualny


class Nieaktualne(Exception):
    """Źródło zwróciło cenę, ale jest za stara, więc nie zapisujemy jej jako bieżącej."""


def _teraz():
    return datetime.now(timezone.utc)


def pobierz_coingecko(mapa):
    """Jedno zapytanie dla wszystkich monet. mapa: {SYMBOL: id}. Zwraca {SYMBOL: (cena, waluta, zrodlo, status)}.

    Cena bez świeżego znacznika czasu (last_updated_at) albo starsza niż MAX_OPOZNIENIE_S
    dostaje status blad:nieaktualne i nie trafia do CSV jako cena.
    """
    ids = ",".join(sorted(set(mapa.values())))
    url = "https://api.coingecko.com/api/v3/simple/price?" + urllib.parse.urlencode(
        {"ids": ids, "vs_currencies": "usd", "include_last_updated_at": "true"}
    )
    dane = json.loads(http_get(url))
    teraz_ts = _teraz().timestamp()
    wynik = {}
    for symbol, cg_id in mapa.items():
        try:
            wpis = dane[cg_id]
            cena = float(wpis["usd"])
            aktualizacja = wpis.get("last_updated_at")
            if aktualizacja is not None and teraz_ts - float(aktualizacja) > MAX_OPOZNIENIE_S:
                wynik[symbol] = (None, "USD", "coingecko", "blad:nieaktualne")
            else:
                wynik[symbol] = (cena, "USD", "coingecko", "ok")
        except (KeyError, TypeError, ValueError):
            wynik[symbol] = (None, "USD", "coingecko", "blad:brak_danych")
    return wynik


def _rynek_otwarty(meta, teraz):
    """True, gdy teraz mieści się w regularnej sesji z currentTradingPeriod."""
    sesja = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    try:
        return int(sesja["start"]) <= teraz.timestamp() <= int(sesja["end"])
    except (KeyError, TypeError, ValueError):
        return False


def pobierz_yahoo(yahoo_symbol):
    """Ostatnia cena zamknięcia z Yahoo Finance. Zwraca (cena, waluta, czas_utc).

    Bierzemy ostatni niepusty punkt serii 1-minutowej, a nie regularMarketPrice z meta,
    bo meta potrafi zostać przy starej wartości. Gdy sesja trwa, a punkt jest starszy
    niż MAX_OPOZNIENIE_S, rzucamy Nieaktualne. Poza sesją ostatnie zamknięcie jest poprawne.
    """
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(yahoo_symbol)
        + "?interval=1m&range=5d"
    )
    dane = json.loads(http_get(url))
    wynik = dane["chart"]["result"][0]
    meta = wynik["meta"]
    znaczniki = wynik.get("timestamp") or []
    zamkniecia = wynik["indicators"]["quote"][0]["close"]
    pary = [(t, c) for t, c in zip(znaczniki, zamkniecia) if c is not None]
    if not pary:
        raise ValueError("brak notowań")
    czas_ts, cena = pary[-1]
    czas = datetime.fromtimestamp(czas_ts, timezone.utc)
    teraz = _teraz()
    if _rynek_otwarty(meta, teraz) and teraz - czas > timedelta(seconds=MAX_OPOZNIENIE_S):
        raise Nieaktualne(f"ostatni punkt z {czas:%H:%M} UTC")
    return float(cena), meta["currency"], czas


def zbierz(symbole):
    wiersze = []  # (symbol, cena, waluta, zrodlo, status)

    cg = {s: i for s, (z, i) in symbole.items() if z == "coingecko"}
    if cg:
        try:
            wyniki = pobierz_coingecko(cg)
        except Exception as exc:  # cała partia naraz
            wyniki = {s: (None, "USD", "coingecko", f"blad:{type(exc).__name__}") for s in cg}
            print(f"coingecko: {exc}", file=sys.stderr)
        for symbol in sorted(cg):
            wiersze.append((symbol,) + wyniki[symbol])

    for symbol in sorted(symbole):
        zrodlo, identyfikator = symbole[symbol]
        if zrodlo != "yahoo":
            continue
        try:
            cena, waluta, _czas = pobierz_yahoo(identyfikator)
            status = "ok"
        except Nieaktualne as exc:
            cena, waluta, status = None, "", "blad:nieaktualne"
            print(f"{symbol}: {exc}", file=sys.stderr)
        except Exception as exc:
            cena, waluta, status = None, "", f"blad:{type(exc).__name__}"
            print(f"{symbol}: {exc}", file=sys.stderr)
        wiersze.append((symbol, cena, waluta, "yahoo", status))

    return wiersze


# --- zapis danych i stanu ---------------------------------------------------

def zapisz(teraz, wiersze):
    katalog = os.path.join(DANE, teraz.strftime("%Y"), teraz.strftime("%m"))
    os.makedirs(katalog, exist_ok=True)
    sciezka = os.path.join(katalog, f"ceny-{teraz:%Y-%m-%d}.csv")
    ts = teraz.strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(sciezka, "a", encoding="utf-8", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if f.tell() == 0:
            csv.writer(f, delimiter=";").writerow(NAGLOWEK)
        zapis = csv.writer(f, delimiter=";")
        for symbol, cena, waluta, zrodlo, status in wiersze:
            cena_txt = "" if cena is None else f"{cena:.10g}"
            zapis.writerow([ts, symbol, cena_txt, waluta, zrodlo, status])
        f.flush()
        os.fsync(f.fileno())


def wczytaj_stan():
    try:
        with open(STAN, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def zapisz_stan(stan):
    os.makedirs(os.path.dirname(STAN), exist_ok=True)
    tmp = STAN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stan, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STAN)


def pliki_dni(od, do):
    sciezki = []
    dzien = od.date()
    while dzien <= do.date():
        sciezki.append(os.path.join(DANE, f"{dzien:%Y}", f"{dzien:%m}", f"ceny-{dzien:%Y-%m-%d}.csv"))
        dzien += timedelta(days=1)
    return [p for p in sciezki if os.path.isfile(p)]


def wczytaj_serie(od, do=None):
    """{symbol: [(czas_utc, cena, waluta)]} z wierszy status=ok w przedziale od..do."""
    do = do or datetime.now(timezone.utc)
    serie = {}
    for plik in pliki_dni(od, do):
        with open(plik, encoding="utf-8", newline="") as f:
            for w in csv.reader(f, delimiter=";"):
                if len(w) != 6 or w[0] == "ts_utc" or w[5] != "ok" or not w[2]:
                    continue
                czas = datetime.strptime(w[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if od <= czas <= do:
                    serie.setdefault(w[1], []).append((czas, float(w[2]), w[3]))
    return serie


def raport_30min(teraz, wiersze, telegram):
    """Co 30 minut: cena każdego symbolu i zmiana względem odczytu sprzed ok. 30 minut."""
    serie = wczytaj_serie(teraz - timedelta(minutes=40), teraz)
    linie = []
    for symbol, cena, waluta, _zrodlo, status in sorted(wiersze):
        if status != "ok":
            linie.append(f"{symbol}: brak kursu ({status})")
            continue
        sprzed = [p for p in serie.get(symbol, [])
                  if teraz - timedelta(minutes=35) <= p[0] <= teraz - timedelta(minutes=30)]
        if sprzed:
            stara = sprzed[-1][1]
            zmiana = (cena - stara) / stara * 100 if stara else 0.0
            strzalka = "▲" if zmiana > 0 else ("▼" if zmiana < 0 else "■")
            linie.append(f"{symbol}: {cena:.10g} {waluta}  {strzalka} {zmiana:+.2f}% (30 min)")
        else:
            linie.append(f"{symbol}: {cena:.10g} {waluta}  (brak odczytu sprzed 30 min)")
    godzina = teraz.astimezone(WARSZAWA).strftime("%H:%M")
    wyslij_telegram(telegram, f"Kursy {godzina}:\n" + "\n".join(linie))


def wyslij_telegram(telegram, tekst):
    url = f"https://api.telegram.org/bot{telegram['token']}/sendMessage"
    # Prefiks odróżnia alerty kolektora od raportów z zadań Claude na tym samym czacie.
    dane = urllib.parse.urlencode(
        {"chat_id": telegram["chat_id"], "text": f"[KOLEKTOR] {tekst}"}
    ).encode()
    try:
        http_get(url, dane_post=dane)
    except Exception as exc:  # alert nie może wywrócić zbierania
        print(f"telegram: {exc}", file=sys.stderr)


def sprawdz_alerty(wiersze, stan, telegram):
    """Porównuje z poprzednim stanem, wysyła alerty i aktualizuje stan."""
    alerty_wlaczone = bool(telegram["token"] and telegram["chat_id"])
    for symbol, cena, waluta, zrodlo, status in wiersze:
        poprzedni = stan.get(symbol, {})
        was_ok = poprzedni.get("status") == "ok"
        is_ok = status == "ok"

        if alerty_wlaczone and was_ok != is_ok and "status" in poprzedni:
            if is_ok:
                wyslij_telegram(telegram, f"{symbol}: źródło znów działa, kurs {cena:.10g} {waluta}")
            else:
                wyslij_telegram(telegram, f"{symbol}: brak kursu ({status})")

        if alerty_wlaczone and is_ok and was_ok and poprzedni.get("cena"):
            zmiana = (cena - poprzedni["cena"]) / poprzedni["cena"] * 100
            if abs(zmiana) >= telegram["prog_proc"]:
                wyslij_telegram(
                    telegram,
                    f"{symbol}: {zmiana:+.2f}% w minutę ({poprzedni['cena']:.10g} -> {cena:.10g} {waluta})",
                )

        nowy = {"status": status}
        if is_ok:
            nowy["cena"] = cena
        elif "cena" in poprzedni:
            nowy["cena"] = poprzedni["cena"]  # przy błędzie zachowujemy ostatnią poprawną cenę
        stan[symbol] = nowy
    return stan


def main():
    cfg = wczytaj_pelna(CONFIG)
    teraz = datetime.now(timezone.utc).replace(microsecond=0)
    wiersze = zbierz(cfg["symbole"])
    zapisz(teraz, wiersze)

    stan = sprawdz_alerty(wiersze, wczytaj_stan(), cfg["telegram"])
    zapisz_stan(stan)

    tg = cfg["telegram"]
    if teraz.minute % 30 == 0 and tg["token"] and tg["chat_id"]:
        raport_30min(teraz, wiersze, tg)

    udane = sum(1 for w in wiersze if w[4] == "ok")
    print(f"{teraz:%Y-%m-%dT%H:%M:%SZ} zapisano {len(wiersze)} symboli, ok: {udane}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
