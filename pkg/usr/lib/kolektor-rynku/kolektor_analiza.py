#!/usr/bin/env python3
"""Analiza techniczna na zapisanych kursach kolektora.

Wskaźniki liczone na próbkach godzinowych (ostatni kurs w każdej pełnej godzinie):
  SMA24, SMA72  – średnie kroczące z 24 i 72 godzin,
  RSI14         – względna siła (Wilder), 0–100; >70 wykupienie, <30 wyprzedanie.
Wynik jest opisem danych, nie rekomendacją inwestycyjną.
Skrypt nie używa zewnętrznych bibliotek.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolektor_rynku as kr  # noqa: E402

OKNO_DNI = 14  # ile dni wstecz bierzemy do obliczeń (SMA72 potrzebuje 3 dni)


def probki_godzinowe(punkty):
    """punkty: [(czas_utc, cena, waluta)]. Zwraca [(godzina, cena)] posortowane po czasie."""
    godziny = {}
    for czas, cena, _waluta in sorted(punkty, key=lambda p: p[0]):
        godziny[czas.replace(minute=0, second=0, microsecond=0)] = cena  # ostatni kurs w godzinie
    return sorted(godziny.items())


def sma(ceny, n):
    """Średnia z ostatnich n wartości, albo None gdy jest ich za mało."""
    if len(ceny) < n:
        return None
    return sum(ceny[-n:]) / n


def rsi(ceny, n=14):
    """RSI Wildera z n okresów, albo None gdy jest za mało danych."""
    if len(ceny) < n + 1:
        return None
    zmiany = [b - a for a, b in zip(ceny, ceny[1:])]
    zyski = [max(z, 0.0) for z in zmiany]
    straty = [max(-z, 0.0) for z in zmiany]
    sr_zysk = sum(zyski[:n]) / n
    sr_strata = sum(straty[:n]) / n
    for zysk, strata in zip(zyski[n:], straty[n:]):
        sr_zysk = (sr_zysk * (n - 1) + zysk) / n
        sr_strata = (sr_strata * (n - 1) + strata) / n
    if sr_strata == 0:
        return 100.0 if sr_zysk > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + sr_zysk / sr_strata)


def poziomy(ceny, k=6, tolerancja=0.015):
    """Wsparcia i opory z lokalnych dołków i szczytów (k godzin z każdej strony).

    Zwraca (wsparcia, opory): listy (poziom, dotknięcia), najbliższe ostatniej cenie najpierw.
    Poziomy w odległości do `tolerancja` (1,5%) są łączone. Dotknięcia liczą wszystkie
    dołki i szczyty w tej strefie. None, gdy danych jest za mało.
    """
    if len(ceny) < 2 * k + 2:
        return None
    punkty = []
    for i in range(k, len(ceny) - k):
        okno = ceny[i - k:i + k + 1]
        if ceny[i] == max(okno) or ceny[i] == min(okno):
            punkty.append(ceny[i])
    punkty.sort()
    grupy = []
    for p in punkty:
        if grupy and (p - grupy[-1][0]) / grupy[-1][0] <= tolerancja:
            grupy[-1][1].append(p)
        else:
            grupy.append([p, [p]])
    strefy = [(sum(g[1]) / len(g[1]), len(g[1])) for g in grupy]
    ost = ceny[-1]
    wsparcia = sorted((x for x in strefy if x[0] < ost), key=lambda x: -x[0])[:3]
    opory = sorted((x for x in strefy if x[0] > ost), key=lambda x: x[0])[:3]
    return wsparcia, opory


def scenariusze(ost, wsparcia, opory):
    """Opisy: gdzie jest najbliższa obrona i opór oraz co dalej, jeśli cena je przebije."""
    linie = []
    if wsparcia:
        p, n = wsparcia[0]
        linie.append(f"Najbliższe wsparcie (punkt obrony): {p:.4g}, {n} dotknięć, {(p - ost) / ost * 100:+.1f}% od ceny.")
        if len(wsparcia) > 1:
            linie.append(f"Jeśli cena przebije {p:.4g} w dół, następne wsparcie to {wsparcia[1][0]:.4g}.")
        else:
            linie.append("Poniżej najbliższego wsparcia nie ma kolejnego poziomu w danych.")
    else:
        linie.append("Brak wsparcia poniżej ceny w danych.")
    if opory:
        p, n = opory[0]
        linie.append(f"Najbliższy opór: {p:.4g}, {n} dotknięć, {(p - ost) / ost * 100:+.1f}% od ceny.")
        if len(opory) > 1:
            linie.append(f"Jeśli cena przebije {p:.4g} w górę, następny opór to {opory[1][0]:.4g}.")
        else:
            linie.append("Powyżej najbliższego oporu nie ma kolejnego poziomu w danych.")
    else:
        linie.append("Brak oporu powyżej ceny w danych.")
    linie.append("Poziomy wynikają z dotychczasowych dołków i szczytów. To nie gwarancja, że cena odbije się od nich.")
    return linie


def pierwsze_trafienia(ceny, horyzont_h, prog_zysk, prog_straty):
    """Dla każdego punktu wejścia: który próg wystąpił jako pierwszy w ciągu horyzontu.

    prog_zysk > 0 i prog_straty < 0 (w %). Zwraca (% najpierw zysk, % najpierw strata,
    % żaden w horyzoncie, liczba punktów) albo None, gdy danych jest za mało.
    Opisuje przeszłość, nie prognozuje.
    """
    n = zysk = strata = brak = 0
    for i in range(len(ceny) - horyzont_h):
        bazowa = ceny[i]
        wynik = None
        for p in ceny[i + 1:i + horyzont_h + 1]:
            zmiana = (p / bazowa - 1) * 100
            if zmiana >= prog_zysk:
                wynik = "zysk"
                break
            if zmiana <= prog_straty:
                wynik = "strata"
                break
        n += 1
        if wynik == "zysk":
            zysk += 1
        elif wynik == "strata":
            strata += 1
        else:
            brak += 1
    if n == 0:
        return None
    return 100.0 * zysk / n, 100.0 * strata / n, 100.0 * brak / n, n


def srednia_dzienna_zmiana(ceny):
    """Średni bezwzględny ruch ceny w ciągu 24 godzin, w %. None, gdy danych jest za mało."""
    if len(ceny) <= 24:
        return None
    ruchy = [abs(ceny[i + 24] / ceny[i] - 1) * 100 for i in range(len(ceny) - 24)]
    return sum(ruchy) / len(ruchy)


def opis_rsi(wartosc):
    if wartosc is None:
        return "za mało danych"
    if wartosc > 70:
        return "wykupiony (możliwy spadek)"
    if wartosc < 30:
        return "wyprzedany (możliwy wzrost)"
    return "neutralny"


def raport(symbol, punkty):
    """Tekst analizy dla jednego symbolu. punkty: [(czas_utc, cena, waluta)]."""
    probki = probki_godzinowe(punkty)
    if not probki:
        return f"{symbol}: brak danych w ostatnich {OKNO_DNI} dniach"
    ceny = [c for _, c in probki]
    waluta = punkty[-1][2] if punkty else ""
    ostatnia = ceny[-1]
    s24 = sma(ceny, 24)
    s72 = sma(ceny, 72)
    r = rsi(ceny, 14)

    linie = [
        f"{symbol}: {ostatnia:.10g} {waluta}",
        f"min/maks ({len(ceny)} godz.): {min(ceny):.10g} / {max(ceny):.10g}",
    ]
    if s24 is None:
        linie.append("SMA24: za mało danych")
    else:
        polozenie = "powyżej" if ostatnia > s24 else "poniżej"
        linie.append(f"SMA24: {s24:.10g} (cena {polozenie})")
    if s72 is None:
        linie.append("SMA72: za mało danych")
    elif s24 is not None:
        trend = "wzrostowy" if s24 > s72 else "spadkowy"
        linie.append(f"SMA72: {s72:.10g} (SMA24 {'nad' if s24 > s72 else 'pod'} SMA72 – trend {trend})")
    else:
        linie.append(f"SMA72: {s72:.10g}")
    linie.append(f"RSI14: {r:.1f} – {opis_rsi(r)}" if r is not None else "RSI14: za mało danych")
    lp = poziomy(ceny)
    if lp is not None:
        linie.extend(scenariusze(ostatnia, *lp)[:3])
    return "\n".join(linie)


def raport_wszystkie(symbol=None, teraz=None):
    """Analiza dla jednego symbolu albo dla wszystkich z ostatnich OKNO_DNI dni."""
    teraz = teraz or datetime.now(timezone.utc)
    serie = kr.wczytaj_serie(teraz - timedelta(days=OKNO_DNI), teraz)
    if symbol:
        symbol = symbol.upper()
        if symbol not in serie:
            return f"Brak danych dla {symbol} w ostatnich {OKNO_DNI} dniach."
        return raport(symbol, serie[symbol])
    if not serie:
        return "Brak danych do analizy."
    bloki = [raport(s, serie[s]) for s in sorted(serie)]
    return "\n\n".join(bloki) + "\n\nTo opis danych, nie rekomendacja inwestycyjna."
