#!/usr/bin/env python3
"""Ocena symbolu dla panelu www: wskaźniki techniczne z zapisanych kursów oraz dane
z CoinGecko (wycena, nastroje, zmiany) albo Yahoo (zakres 52 tygodni, nagłówki).

To opis danych i sygnałów, nie rekomendacja kupna, sprzedaży ani trzymania.
Dane zewnętrzne są buforowane na CACHE_S sekund, a błąd jednego źródła nie zatrzymuje strony.
Skrypt nie używa zewnętrznych bibliotek.
"""
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolektor_analiza as ka  # noqa: E402
import kolektor_rynku as kr  # noqa: E402

CACHE_S = 600
_bufor = {}


def _z_bufora(klucz, pobierz):
    teraz = time.time()
    if klucz in _bufor and teraz - _bufor[klucz][0] < CACHE_S:
        return _bufor[klucz][1]
    wynik = pobierz()
    _bufor[klucz] = (teraz, wynik)
    return wynik


def _json(url):
    return json.loads(kr.http_get(url))


def dane_coingecko(cg_id):
    adres = "https://api.coingecko.com/api/v3/coins/" + urllib.parse.quote(cg_id) + "?" + urllib.parse.urlencode(
        {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"}
    )
    d = _json(adres)
    md = d.get("market_data") or {}

    def usd(pole):
        return (md.get(pole) or {}).get("usd")

    return {
        "zrodlo": "CoinGecko",
        "nazwa": d.get("name"),
        "cena": usd("current_price"),
        "waluta": "USD",
        "kapitalizacja": usd("market_cap"),
        "ranking": d.get("market_cap_rank"),
        "wolumen_24h": usd("total_volume"),
        "maks_historyczny": usd("ath"),
        "od_maks_proc": (md.get("ath_change_percentage") or {}).get("usd"),
        "zmiana_7d_proc": md.get("price_change_percentage_7d"),
        "zmiana_30d_proc": md.get("price_change_percentage_30d"),
        "nastroje_wzrost_proc": d.get("sentiment_votes_up_percentage"),
        "nastroje_spadek_proc": d.get("sentiment_votes_down_percentage"),
        "wycena": "kapitalizacja i ranking z CoinGecko",
        "maks_52t": None,
        "min_52t": None,
        "wiadomosci": [],
    }


def dane_yahoo(symbol):
    meta = _json(
        "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?interval=1d&range=5d"
    )["chart"]["result"][0]["meta"]
    wiadomosci = []
    try:
        szukaj = _json(
            "https://query2.finance.yahoo.com/v1/finance/search?"
            + urllib.parse.urlencode({"q": symbol, "quotesCount": 0, "newsCount": 5})
        )
        for n in (szukaj.get("news") or [])[:5]:
            link = n.get("link") or ""
            if link.startswith("https://"):
                wiadomosci.append({"tytul": n.get("title") or "", "link": link, "wydawca": n.get("publisher") or ""})
    except Exception as exc:  # wiadomości są dodatkiem, nie blokują oceny
        print(f"yahoo wiadomości {symbol}: {exc}", file=sys.stderr)
    return {
        "zrodlo": "Yahoo Finance",
        "nazwa": meta.get("longName") or meta.get("shortName"),
        "cena": meta.get("regularMarketPrice"),
        "waluta": meta.get("currency"),
        "kapitalizacja": None,
        "ranking": None,
        "wolumen_24h": meta.get("regularMarketVolume"),
        "maks_historyczny": None,
        "od_maks_proc": None,
        "zmiana_7d_proc": None,
        "zmiana_30d_proc": None,
        "nastroje_wzrost_proc": None,
        "nastroje_spadek_proc": None,
        "wycena": "wskaźniki wyceny (P/E, zysk) nie są dostępne w tym źródle",
        "maks_52t": meta.get("fiftyTwoWeekHigh"),
        "min_52t": meta.get("fiftyTwoWeekLow"),
        "wiadomosci": wiadomosci,
    }


def dane_zewnetrzne(symbol, zrodlo, ident):
    """Zwraca (dane, None) albo (None, komunikat błędu). Wynik jest buforowany."""
    def pobierz():
        try:
            if zrodlo == "coingecko":
                return dane_coingecko(ident), None
            return dane_yahoo(ident), None
        except Exception as exc:
            return None, f"Nie udało się pobrać danych ze źródła: {exc}"

    return _z_bufora((symbol, zrodlo, ident), pobierz)


def wskazniki_techniczne(punkty):
    """Wartości z kursów: ostatnia cena, SMA24, SMA72, RSI14 i zakres. None gdy brak danych."""
    probki = ka.probki_godzinowe(punkty)
    ceny = [c for _, c in probki]
    if not ceny:
        return None
    lp = ka.poziomy(ceny)
    return {
        "ostatnia": ceny[-1],
        "wsparcia": lp[0] if lp else [],
        "opory": lp[1] if lp else [],
        "sma24": ka.sma(ceny, 24),
        "sma72": ka.sma(ceny, 72),
        "rsi": ka.rsi(ceny, 14),
        "min": min(ceny),
        "maks": max(ceny),
        "probek": len(ceny),
    }


def obserwacje(tech, rynek):
    """Lista zdań opisujących dane, bez wniosku o kupnie czy sprzedaży."""
    uwagi = []
    if tech and tech.get("wsparcia") is not None and (tech["wsparcia"] or tech["opory"]):
        uwagi.extend(ka.scenariusze(tech["ostatnia"], tech["wsparcia"], tech["opory"]))
    if tech:
        uwagi.append(f"RSI14 wynosi {tech['rsi']:.1f}, czyli {ka.opis_rsi(tech['rsi'])}." if tech["rsi"] is not None
                     else "RSI14: za mało danych, żeby go policzyć.")
        if tech["sma24"] is not None:
            poz = "powyżej" if tech["ostatnia"] > tech["sma24"] else "poniżej"
            uwagi.append(f"Cena jest {poz} średniej 24-godzinnej ({tech['sma24']:.10g}).")
        if tech["sma24"] is not None and tech["sma72"] is not None:
            kier = "powyżej" if tech["sma24"] > tech["sma72"] else "poniżej"
            uwagi.append(f"Średnia 24 h jest {kier} średniej 72 h, co odpowiada trendowi "
                         f"{'wzrostowemu' if kier == 'powyżej' else 'spadkowemu'} w tym krótkim okresie.")
    if rynek:
        if rynek.get("od_maks_proc") is not None:
            uwagi.append(f"Cena jest {abs(rynek['od_maks_proc']):.1f}% "
                         f"{'poniżej' if rynek['od_maks_proc'] < 0 else 'powyżej'} historycznego maksimum.")
        for etykieta, pole in (("7 dni", "zmiana_7d_proc"), ("30 dni", "zmiana_30d_proc")):
            if rynek.get(pole) is not None:
                uwagi.append(f"Zmiana w ciągu {etykieta}: {rynek[pole]:+.1f}%.")
        if rynek.get("nastroje_wzrost_proc") is not None:
            uwagi.append(f"W głosowaniu CoinGecko {rynek['nastroje_wzrost_proc']:.1f}% użytkowników jest "
                         f"za wzrostem. To opinia użytkowników, nie prognoza.")
        if rynek.get("maks_52t") and rynek.get("min_52t") and tech:
            zakres = rynek["maks_52t"] - rynek["min_52t"]
            if zakres > 0:
                pozycja = (tech["ostatnia"] - rynek["min_52t"]) / zakres * 100
                uwagi.append(f"Cena jest na poziomie {pozycja:.0f}% zakresu 52-tygodniowego "
                             f"(min {rynek['min_52t']:.10g}, maks {rynek['maks_52t']:.10g}).")
        if rynek.get("nastroje_wzrost_proc") is None and rynek["zrodlo"] == "Yahoo Finance":
            uwagi.append("Nastrojów rynku dla akcji nie ma w tym źródle, więc nie podaję ich.")
    return uwagi


def ocena(symbol, zrodlo, ident, punkty):
    """Pełna ocena do strony. Zwraca słownik gotowy do wyświetlenia."""
    tech = wskazniki_techniczne(punkty)
    rynek, blad = dane_zewnetrzne(symbol, zrodlo, ident)
    return {
        "symbol": symbol,
        "tech": tech,
        "rynek": rynek,
        "blad_rynku": blad,
        "uwagi": obserwacje(tech, rynek),
        "wygenerowano": datetime.now(timezone.utc),
    }
