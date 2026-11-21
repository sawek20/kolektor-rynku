#!/usr/bin/env python3
"""Bot Telegram kolektora kursów. Odbiera komendy long pollingiem (getUpdates).

Komendy (tylko z czatu ustawionego w [telegram] chat_id):
  wykres N   -> wykres cen z ostatnich N dni (1-365), np. 'wykres 7', 'wykres 30'
  kursy      -> ostatnie znane ceny
  analiza    -> SMA24/SMA72 i RSI14 dla wszystkich symboli; 'analiza DASH' dla jednego
  pomoc      -> lista komend
Wymaga python3-matplotlib. Uruchamiany jako usługa kolektor-bot.
"""
import json
import math
import os
import secrets
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolektor_analiza as ka  # noqa: E402
import kolektor_rynku as kr  # noqa: E402

WARSZAWA = kr.WARSZAWA
OFFSET = os.environ.get("KOLEKTOR_OFFSET", "/var/lib/kolektor-rynku/bot_offset.txt")
MAX_DNI = 365
POLL_TIMEOUT = 50
POMOC = (
    "Komendy:\n"
    "wykres 7 – wykres z ostatnich 7 dni\n"
    "wykres 30 – wykres z ostatnich 30 dni (max 365)\n"
    "kursy – ostatnie znane ceny\n"
    "analiza – SMA24, SMA72 i RSI14 dla wszystkich symboli\n"
    "analiza DASH – to samo dla jednego symbolu\n"
    "pomoc – ta lista"
)


# --- Telegram API -----------------------------------------------------------

def api(token, metoda, parametry=None, timeout=POLL_TIMEOUT + 15):
    url = f"https://api.telegram.org/bot{token}/{metoda}"
    dane = urllib.parse.urlencode(parametry or {}).encode()
    req = urllib.request.Request(url, data=dane, headers={"User-Agent": kr.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        odp = json.loads(resp.read().decode("utf-8"))
    if not odp.get("ok"):
        raise RuntimeError(f"{metoda}: {odp.get('description', 'błąd')}")
    return odp


def wyslij_tekst(token, chat, tekst):
    api(token, "sendMessage", {"chat_id": chat, "text": tekst})


def wyslij_wykres(token, chat, sciezka, podpis):
    """sendPhoto wymaga multipart/form-data, więc składamy body ręcznie."""
    granica = secrets.token_hex(16)
    czesci = []

    def pole(nazwa, wartosc):
        czesci.append(
            f"--{granica}\r\nContent-Disposition: form-data; name=\"{nazwa}\"\r\n\r\n{wartosc}\r\n"
            .encode("utf-8")
        )

    pole("chat_id", chat)
    pole("caption", podpis[:1000])
    with open(sciezka, "rb") as f:
        png = f.read()
    czesci.append(
        f"--{granica}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"wykres.png\"\r\n"
        "Content-Type: image/png\r\n\r\n".encode("utf-8")
        + png
        + b"\r\n"
    )
    czesci.append(f"--{granica}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(czesci),
        headers={
            "Content-Type": f"multipart/form-data; boundary={granica}",
            "User-Agent": kr.USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        odp = json.loads(resp.read().decode("utf-8"))
    if not odp.get("ok"):
        raise RuntimeError(f"sendPhoto: {odp.get('description', 'błąd')}")


# --- dane i wykresy ---------------------------------------------------------

def zrob_wykres(dni, sciezka):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    teraz = datetime.now(timezone.utc)
    serie = kr.wczytaj_serie(teraz - timedelta(days=dni), teraz)
    symbole = sorted(serie)
    if not symbole:
        return False

    n = len(symbole)
    kolumny = 2 if n > 1 else 1
    wiersze = math.ceil(n / kolumny)
    fig, osie = plt.subplots(wiersze, kolumny, figsize=(11, 3.4 * wiersze), dpi=110, squeeze=False)
    for ax, symbol in zip(osie.flat, symbole):
        punkty = serie[symbol]
        xs = [t.astimezone(WARSZAWA) for t, _, _ in punkty]
        ys = [c for _, c, _ in punkty]
        waluta = punkty[-1][2]
        ax.plot(xs, ys, color="#2563eb", linewidth=1.1)
        for i_, kolor in ((ys.index(min(ys)), "#dc2626"), (ys.index(max(ys)), "#16a34a")):
            ax.scatter([xs[i_]], [ys[i_]], color=kolor, s=18, zorder=3)
            ax.annotate(f"{ys[i_]:.4g}\n{xs[i_]:%d.%m %H:%M}", (xs[i_], ys[i_]), textcoords="offset points",
                        xytext=(4, 6), fontsize=7, color=kolor)
        ax.set_ylabel(waluta, fontsize=8)
        zmiana = (ys[-1] - ys[0]) / ys[0] * 100 if ys[0] else 0.0
        strzalka = "▲" if zmiana > 0 else ("▼" if zmiana < 0 else "■")
        ax.set_title(f"{symbol}: {ys[-1]:,.4g} {waluta}  {strzalka} {zmiana:+.1f}%", fontsize=10)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=WARSZAWA))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M", tz=WARSZAWA))
        ax.tick_params(axis="x", labelrotation=25)
    for ax in osie.flat[n:]:
        ax.set_visible(False)
    fig.suptitle(f"Kursy – ostatnie {dni} dni (czas PL)", fontsize=11)
    fig.tight_layout()
    fig.savefig(sciezka)
    plt.close(fig)
    return True


def tekst_kursow():
    teraz = datetime.now(timezone.utc)
    serie = kr.wczytaj_serie(teraz - timedelta(hours=1), teraz)
    linie = []
    for symbol in sorted(serie):
        czas, cena, waluta = serie[symbol][-1]
        linie.append(f"{symbol}: {cena:.10g} {waluta} ({czas.astimezone(WARSZAWA):%H:%M})")
    return "\n".join(linie) or "Brak świeżych kursów z ostatniej godziny."


def obsluz(token, chat, tekst):
    polecenie = " ".join((tekst or "").lower().split()).lstrip("/")
    czesci = polecenie.split()
    if czesci and czesci[0] == "wykres":
        if len(czesci) == 2 and czesci[1].isdigit() and 1 <= int(czesci[1]) <= MAX_DNI:
            dni = int(czesci[1])
            wyslij_tekst(token, chat, f"Robię wykres z {dni} dni…")
            with tempfile.TemporaryDirectory() as katalog:
                sciezka = os.path.join(katalog, "wykres.png")
                if zrob_wykres(dni, sciezka):
                    wyslij_wykres(token, chat, sciezka, f"Wykres {dni} dni")
                else:
                    wyslij_tekst(token, chat, "Brak danych z tego okresu.")
        else:
            wyslij_tekst(token, chat, f"Podaj liczbę dni od 1 do {MAX_DNI}, np. 'wykres 7'.")
    elif polecenie == "kursy":
        wyslij_tekst(token, chat, tekst_kursow())
    elif czesci and czesci[0] == "analiza" and len(czesci) <= 2:
        symbol = czesci[1] if len(czesci) == 2 else None
        wyslij_tekst(token, chat, ka.raport_wszystkie(symbol))
    elif polecenie in ("pomoc", "help", "start"):
        wyslij_tekst(token, chat, POMOC)


# --- pętla ------------------------------------------------------------------

def wczytaj_offset():
    try:
        with open(OFFSET, encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def zapisz_offset(wartosc):
    os.makedirs(os.path.dirname(OFFSET), exist_ok=True)
    tmp = OFFSET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(wartosc))
    os.replace(tmp, OFFSET)


def main():
    offset = wczytaj_offset()
    while True:
        tg = kr.wczytaj_pelna()["telegram"]
        if not tg["token"] or not tg["chat_id"]:
            time.sleep(60)
            continue
        try:
            odp = api(tg["token"], "getUpdates", {"offset": offset, "timeout": POLL_TIMEOUT})
        except Exception as exc:
            print(f"getUpdates: {exc}", file=sys.stderr)
            time.sleep(15)
            continue
        for aktualizacja in odp.get("result", []):
            offset = aktualizacja["update_id"] + 1
            wiadomosc = aktualizacja.get("message") or {}
            chat = str((wiadomosc.get("chat") or {}).get("id", ""))
            if chat == str(tg["chat_id"]):
                try:
                    obsluz(tg["token"], chat, wiadomosc.get("text", ""))
                except Exception as exc:
                    print(f"komenda: {exc}", file=sys.stderr)
            zapisz_offset(offset)


if __name__ == "__main__":
    sys.exit(main())
