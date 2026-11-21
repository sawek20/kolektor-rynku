#!/usr/bin/env python3
"""Panel www kolektora kursów: lista symboli (dodawanie, edycja, usuwanie), Telegram,
konto logowania, podgląd ostatnich odczytów i pobieranie CSV.

Uruchomienie:  python3 -I kolektor_www.py            (usługa kolektor-www)
               python3 -I kolektor_www.py --init     (przy instalacji: ustawia hasło, jeśli brak)
Zmiany symboli i Telegrama są widoczne dla kolektora od następnej minuty.
Zmiana portu/adresu wymaga restartu usługi kolektor-www.
"""
import base64
import csv
import hashlib
import hmac
import html
import http.server
import json
import os
import re
import secrets
import sys
import urllib.parse
from collections import deque
from datetime import datetime, timedelta, timezone
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolektor_analiza as ka  # noqa: E402
import kolektor_ocena as ko  # noqa: E402
import kolektor_rynku as kr  # noqa: E402

CONFIG = kr.CONFIG
DANE = kr.DANE
PBKDF2_ITER = 200_000
MAX_POST = 8192
HASLO_HOSTA = "/root/kolektor-haslo.txt"

RE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.^=-]{0,19}$")
RE_CG_ID = re.compile(r"^[a-z0-9-]{1,80}$")
RE_YAHOO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.^=-]{0,19}$")
RE_DZIEN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_CZYSTY = re.compile(r"^[^\r\n]*$")


# --- hasła ------------------------------------------------------------------

def hasz_hasla(haslo):
    sol = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", haslo.encode("utf-8"), sol, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${sol.hex()}${h.hex()}"


def sprawdz_haslo(haslo, zapisany):
    try:
        alg, iteracje, sol, h = zapisany.split("$")
    except ValueError:
        return False
    if alg != "pbkdf2_sha256":
        return False
    kandydat = hashlib.pbkdf2_hmac("sha256", haslo.encode("utf-8"), bytes.fromhex(sol), int(iteracje))
    return hmac.compare_digest(kandydat.hex(), h)


def init(sciezka=CONFIG):
    """Wywoływane przy instalacji. Zapisuje config w nowym formacie i ustawia hasło, jeśli go nie ma."""
    cfg = kr.wczytaj_pelna(sciezka)
    if cfg["www"]["haslo"]:
        kr.zapisz_pelna(sciezka, cfg)
        print("Panel www: hasło już ustawione, zostaje bez zmian.")
        return 0
    haslo = secrets.token_urlsafe(12)
    cfg["www"]["haslo"] = hasz_hasla(haslo)
    kr.zapisz_pelna(sciezka, cfg)
    with open(HASLO_HOSTA, "w", encoding="utf-8") as f:
        f.write(f"login: {cfg['www']['login']}\nhaslo: {haslo}\n")
    os.chmod(HASLO_HOSTA, 0o600)
    print(f"Panel www: login '{cfg['www']['login']}', hasło w {HASLO_HOSTA} (zmień je w panelu).")
    return 0


# --- odczyty ----------------------------------------------------------------

def ostatnie_odczyty(symbole, ile):
    """Ostatnie `ile` wierszy na symbol z dwóch najnowszych dni. Zwraca {symbol: [(ts, cena, waluta, status)]}."""
    pliki = sorted(glob(os.path.join(DANE, "*", "*", "ceny-*.csv")))[-2:]
    bufory = {s: deque(maxlen=ile) for s in symbole}
    for plik in pliki:
        try:
            with open(plik, encoding="utf-8", newline="") as f:
                for wiersz in csv.reader(f, delimiter=";"):
                    if len(wiersz) != 6 or wiersz[0] == "ts_utc":
                        continue
                    ts, symbol, cena, waluta, _zrodlo, status = wiersz
                    if symbol in bufory:
                        bufory[symbol].append((ts, cena, waluta, status))
        except OSError:
            continue
    return {s: list(b) for s, b in bufory.items()}


def wykres_interaktywny(punkty, szer=900, wys=320, poziomy=None):
    """Wykres z osią czasu i ceny oraz podglądem kursu i czasu po najechaniu myszką.

    punkty: [(czas_utc, cena)] posortowane. Próbki są przerzedzane do ok. 800 punktów.
    """
    if len(punkty) < 2:
        return "<p>Za mało punktów do wykresu.</p>"
    krok = max(1, len(punkty) // 800)
    proby = list(punkty[::krok])
    if proby[-1] != punkty[-1]:
        proby.append(punkty[-1])
    ceny = [c for _, c in proby]
    mn, mx = min(ceny), max(ceny)
    # poziomy wsparcia i oporu tylko w pobliżu zakresu cen, żeby nie ściskać wykresu
    poziomy = [(p, t) for p, t in (poziomy or []) if mn * 0.85 <= p <= mx * 1.15]
    mn = min([mn] + [p for p, _ in poziomy])
    mx = max([mx] + [p for p, _ in poziomy])
    rozp = (mx - mn) or (abs(mx) * 0.01) or 1.0
    lo, hi = mn - rozp * 0.05, mx + rozp * 0.05
    ml, mr, mt, mb = 70, 12, 12, 42
    pw, ph = szer - ml - mr, wys - mt - mb
    n = len(proby)

    def X(i):
        return ml + i * pw / (n - 1)

    def Y(c):
        return mt + (hi - c) / (hi - lo) * ph

    def czas_txt(t):
        return t.astimezone(kr.WARSZAWA).strftime("%d.%m %H:%M")

    dane = [[round(X(i), 1), round(Y(c), 1), c, czas_txt(t)] for i, (t, c) in enumerate(proby)]
    linia = " ".join(f"{d[0]},{d[1]}" for d in dane)
    siatka = ""
    for k in range(5):
        c = lo + (hi - lo) * k / 4
        y = Y(c)
        siatka += f"<line x1='{ml}' y1='{y:.1f}' x2='{szer - mr}' y2='{y:.1f}' stroke='#e5e7eb'/>"
        siatka += f"<text x='{ml - 6}' y='{y + 4:.1f}' text-anchor='end' font-size='11'>{c:.4g}</text>"
    for k in range(6):
        i = round((n - 1) * k / 5)
        siatka += f"<line x1='{X(i):.1f}' y1='{mt}' x2='{X(i):.1f}' y2='{wys - mb}' stroke='#e5e7eb'/>"
        siatka += (f"<text x='{X(i):.1f}' y='{wys - mb + 16}' text-anchor='middle' font-size='11'>"
                   f"{czas_txt(proby[i][0])}</text>")
    for p, typ in poziomy:
        y = Y(p)
        kolor = "var(--accent)" if typ == "wsparcie" else "var(--danger)"
        siatka += (f"<line x1='{ml}' y1='{y:.1f}' x2='{szer - mr}' y2='{y:.1f}' stroke-dasharray='6 4' "
                   f"style='stroke:{kolor}'/>")
        siatka += (f"<text x='{szer - mr - 4}' y='{y - 3:.1f}' text-anchor='end' font-size='11' "
                   f"style='fill:{kolor}'>{typ} {p:.4g}</text>")
    dane_json = json.dumps(dane).replace("</", "<\\/")
    return f"""<div><svg id='wykres' data-zoom='1' viewBox='0 0 {szer} {wys}' width='100%' style='background:#fafafa;border:1px solid #ddd'>
{siatka}
<polyline fill='none' stroke='#2563eb' stroke-width='1.5' points='{linia}'/>
<line id='kursor' x1='0' x2='0' y1='{mt}' y2='{wys - mb}' stroke='#9ca3af' stroke-dasharray='3 3' visibility='hidden'/>
<circle id='kropka' r='4' fill='#2563eb' visibility='hidden'/>
<text id='opis' font-size='12' fill='#111' visibility='hidden'></text>
</svg></div>
<script>
(function(){{
  var dane = {dane_json};
  var svg = document.getElementById('wykres');
  var kursor = document.getElementById('kursor');
  var kropka = document.getElementById('kropka');
  var opis = document.getElementById('opis');
  svg.addEventListener('mousemove', function(e){{
    var r = svg.getBoundingClientRect();
    var px = svg.__x ? svg.__x(e.clientX) : (e.clientX - r.left) * ({szer} / r.width);
    var najblizszy = 0, roznica = Infinity;
    for (var i = 0; i < dane.length; i++) {{
      var d = Math.abs(dane[i][0] - px);
      if (d < roznica) {{ roznica = d; najblizszy = i; }}
    }}
    var p = dane[najblizszy];
    kursor.setAttribute('x1', p[0]); kursor.setAttribute('x2', p[0]); kursor.setAttribute('visibility', 'visible');
    kropka.setAttribute('cx', p[0]); kropka.setAttribute('cy', p[1]); kropka.setAttribute('visibility', 'visible');
    opis.textContent = p[3] + '   ' + p[2].toPrecision(6);
    var lewo = p[0] > {szer} / 2;
    opis.setAttribute('x', lewo ? p[0] - 8 : p[0] + 8);
    opis.setAttribute('text-anchor', lewo ? 'end' : 'start');
    opis.setAttribute('y', p[1] > {wys} / 2 ? p[1] - 10 : p[1] + 20);
    opis.setAttribute('visibility', 'visible');
  }});
  svg.addEventListener('mouseleave', function(){{
    kursor.setAttribute('visibility', 'hidden');
    kropka.setAttribute('visibility', 'hidden');
    opis.setAttribute('visibility', 'hidden');
  }});
}})();
</script>"""


def _seria(symbol, zakres):
    dni = {"1d": 1, "7d": 7, "30d": 30}.get(zakres, 1)
    teraz = datetime.now(timezone.utc)
    return kr.wczytaj_serie(teraz - timedelta(days=dni), teraz).get(symbol, [])


def _poziomy_wykresu(serie):
    tech = ko.wskazniki_techniczne(serie)
    if not tech:
        return []
    return ([(p, "wsparcie") for p, _ in tech["wsparcia"][:3]]
            + [(p, "opor") for p, _ in tech["opory"][:3]])


def _dzienne_podsumowanie(serie):
    """Tabela: jeden wiersz na dzień, najniższy i najwyższy kurs (czas polski)."""
    dni = {}
    for t, c, _ in serie:
        lokalny = t.astimezone(kr.WARSZAWA)
        d = dni.setdefault(lokalny.date(), {"min": (c, lokalny), "maks": (c, lokalny)})
        if c < d["min"][0]:
            d["min"] = (c, lokalny)
        if c > d["maks"][0]:
            d["maks"] = (c, lokalny)
    if not dni:
        return "<p>Brak danych w tym zakresie.</p>"
    wiersze = "".join(
        f"<tr><td><b>{dzien:%d.%m}</b></td>"
        f"<td class='spadek'><b>{d['min'][0]:.4g}</b></td><td>{d['min'][1]:%H:%M}</td>"
        f"<td class='wzrost'><b>{d['maks'][0]:.4g}</b></td><td>{d['maks'][1]:%H:%M}</td></tr>"
        for dzien, d in sorted(dni.items())
    )
    return ("<table class='dni'><tr><th>Dzień</th><th>Najniższy</th><th>o godz.</th>"
            f"<th>Najwyższy</th><th>o godz.</th></tr>{wiersze}</table>")


def _wykres_panel(symbol, zakres):
    serie = _seria(symbol, zakres)
    punkty = [(t, c) for t, c, _ in serie]
    przyciski = " ".join(
        f"<a class='btn{' ' if z == zakres else ''}' href='/symbol?s={urllib.parse.quote(symbol)}&zakres={z}'>{e}</a>"
        for z, e in (("1d", "1 dzień"), ("7d", "7 dni"), ("30d", "30 dni"))
    )
    return (
        f"<p>Zakres: {przyciski}</p>"
        + "<div class='obok'><div>" + wykres_interaktywny(punkty, poziomy=_poziomy_wykresu(serie))
        + "</div>" + _renko_obok(serie) + "</div>"
        + "<p class='uwaga'>Najedź myszką na wykres, żeby zobaczyć kurs i czas (czas polski). "
        "Przerywane linie to wsparcia (zielone) i opory (czerwone).</p>"
    )


def wykres_svg(ceny, szer=640, wys=160):
    if len(ceny) < 2:
        return "<p>Za mało punktów do wykresu.</p>"
    mn, mx = min(ceny), max(ceny)
    rozp = (mx - mn) or 1.0
    kroki = len(ceny) - 1
    punkty = " ".join(
        f"{i * szer / kroki:.1f},{wys - (c - mn) / rozp * (wys - 10) - 5:.1f}" for i, c in enumerate(ceny)
    )
    return (
        f'<svg width="{szer}" height="{wys}" viewBox="0 0 {szer} {wys}" '
        f'style="background:#fafafa;border:1px solid #ddd">'
        f'<polyline fill="none" stroke="#2563eb" stroke-width="1.5" points="{punkty}"/></svg>'
        f"<div>min {mn:.10g}, max {mx:.10g}</div>"
    )


# --- strony -----------------------------------------------------------------

STYL = """
:root{--bg:#f2f5f3;--surface:#ffffff;--sunk:#e7ece9;--line:#d3dbd7;--ink:#18241f;--muted:#5b6a64;--accent:#1f5e55;--accent-ink:#ffffff;--free:#fff1bf;--free-ink:#7a5a00;--band:#e9f0fa;--line-strong:#9fb4d4;--res:#dde8f7;--res-ink:#2b5c9e;--due:#fdf0d8;--due-ink:#9a5f0c;--danger:#a33a3a;--danger-bg:#f6e0e0}
html.ciemny{--bg:#111816;--surface:#18221f;--sunk:#0d1311;--line:#2b3834;--ink:#e4ece8;--muted:#93a39c;--accent:#5fb3a3;--accent-ink:#0d1311;--free:#3a3219;--free-ink:#e8c766;--res:#1d2d45;--res-ink:#8db4ec;--band:#1b2733;--line-strong:#3f5670;--due:#3a2f1a;--due-ink:#e3b25a;--danger:#ec8f8f;--danger-bg:#3f2222;color-scheme:dark}
body{font-family:system-ui,sans-serif;margin:24px;color:var(--ink);background:var(--bg);max-width:960px}
table{border-collapse:collapse;margin:12px 0}
td,th{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:middle}
th{background:var(--sunk)}
a{color:var(--res-ink)}
form.inline{display:inline}
fieldset{margin:16px 0;padding:12px 16px;border:1px solid var(--line)}
legend{font-weight:600}
input[type=text],input[type=password],input[type=number],select{padding:4px;background:var(--surface);color:var(--ink);border:1px solid var(--line-strong);border-radius:4px}
.blad{background:var(--danger-bg);color:var(--danger);padding:8px 12px;margin:8px 0}
.info{background:var(--band);color:var(--res-ink);padding:8px 12px;margin:8px 0}
.status-ok{color:var(--accent)}.status-zly{color:var(--danger)}
.btn{display:inline-block;padding:3px 10px;background:var(--accent);color:var(--accent-ink);border:1px solid var(--accent);text-decoration:none;border-radius:8px;font-size:13px;font-weight:600}
.btn:hover{filter:brightness(1.08)}
.btn.icon{padding:7px 9px;line-height:0}
.uwaga{color:var(--muted);font-size:13px}
.wzrost{color:var(--accent);font-weight:600}.spadek{color:var(--danger);font-weight:600}
.ostrzezenie{color:var(--due-ink);font-weight:600}.neutralny{color:var(--muted)}
.wsparcie td{background:var(--band)}.opor td{background:var(--danger-bg)}
h1{font-size:30px;font-weight:700;margin:0}
#motyw{position:fixed;top:16px;right:20px;z-index:10}
.btn .sun{display:none}html.ciemny .btn .sun{display:inline}html.ciemny .btn .moon{display:none}
.obok>div{margin:0 0 16px}
#wykres{background:var(--sunk)!important}
.btn svg{background:transparent}
svg text{fill:var(--muted)}
svg line[stroke='#e5e7eb']{stroke:var(--line)}
svg polyline{stroke:var(--res-ink)!important}
"""


KLUCZ_MOTYWU = "kolektor-rynku-motyw"

SKRYPT_GLOWA = (
    "<script>try{var s=localStorage.getItem('" + KLUCZ_MOTYWU + "');"
    "if(s==='dark'||(!s&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches))"
    "document.documentElement.classList.add('ciemny');}catch(e){}</script>"
)

SKRYPT_ZOOM = (
    "<script>(function(){document.querySelectorAll('svg[data-zoom]').forEach(function(svg){"
    "var b=svg.viewBox.baseVal;var W=b.width,H=b.height,vx=0,vw=W;"
    "function ustaw(){svg.setAttribute('viewBox',vx+' 0 '+vw+' '+H);}"
    "svg.__x=function(cx){var r=svg.getBoundingClientRect();return vx+(cx-r.left)*(vw/r.width);};"
    "svg.addEventListener('wheel',function(e){e.preventDefault();var r=svg.getBoundingClientRect();"
    "var f=(e.clientX-r.left)/r.width;var srodek=vx+f*vw;"
    "var nw=e.deltaY<0?vw/1.25:vw*1.25;nw=Math.min(W,Math.max(W/40,nw));"
    "vx=Math.max(0,Math.min(W-nw,srodek-f*nw));vw=nw;ustaw();},{passive:false});"
    "svg.addEventListener('dblclick',function(){vx=0;vw=W;ustaw();});"
    "});})();</script>"
)

SKRYPT_MOTYWU = (
    "<script>(function(){var b=document.getElementById('motyw');"
    "var mq=window.matchMedia?matchMedia('(prefers-color-scheme: dark)'):null;"
    "function ciemny(){return document.documentElement.classList.contains('ciemny');}"
    "function ustaw(){var t=ciemny()?'Zmień na jasny wygląd':'Zmień na ciemny wygląd';"
    "b.setAttribute('aria-label',t);b.title=t;}"
    "ustaw();"
    "if(mq&&mq.addEventListener){mq.addEventListener('change',function(){"
    "try{if(localStorage.getItem('" + KLUCZ_MOTYWU + "'))return;}catch(e){}"
    "document.documentElement.classList.toggle('ciemny',mq.matches);ustaw();});}"
    "b.addEventListener('click',function(){var d=!ciemny();"
    "document.documentElement.classList.toggle('ciemny',d);"
    "try{localStorage.setItem('" + KLUCZ_MOTYWU + "',d?'dark':'light');}catch(e){}ustaw();});"
    "})();</script>"
)


def strona(tytul, tresc):
    return (
        "<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
        f"<title>{html.escape(tytul)}</title>{SKRYPT_GLOWA}<style>{STYL}</style></head><body>"
        f"<div class='gora'><h1>{html.escape(tytul)}</h1>"
        "<button type='button' id='motyw' class='btn icon' aria-label='Zmień na ciemny wygląd' title='Ciemny / jasny wygląd'>""<svg class='moon' viewBox='0 0 24 24' width='18' height='18' aria-hidden='true'><path fill='currentColor' d='M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z'/></svg>""<svg class='sun' viewBox='0 0 24 24' width='18' height='18' aria-hidden='true'><circle cx='12' cy='12' r='4.5' fill='currentColor'/>""<g stroke='currentColor' stroke-width='2' stroke-linecap='round'><path d='M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8'/></g></svg>""</button></div>"
        f"{tresc}{SKRYPT_ZOOM}{SKRYPT_MOTYWU}</body></html>"
    )


def komunikaty(blad, info):
    out = ""
    if blad:
        out += f"<div class='blad'>{html.escape(blad)}</div>"
    if info:
        out += f"<div class='info'>{html.escape(info)}</div>"
    return out


def strona_glowna(cfg, blad, info):
    symbole = cfg["symbole"]
    odczyty = ostatnie_odczyty(symbole, 1)
    dzis = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    wiersze = []
    for symbol in sorted(symbole):
        zrodlo, ident = symbole[symbol]
        ost = odczyty.get(symbol) or []
        if ost:
            ts, cena, waluta, status = ost[-1]
            klasa = "status-ok" if status == "ok" else "status-zly"
            ostatnia = f"<td>{html.escape(cena or '-')} {html.escape(waluta)}</td><td class='{klasa}'>{html.escape(status)}</td><td>{html.escape(ts)}</td>"
        else:
            ostatnia = "<td>-</td><td class='status-zly'>brak danych</td><td>-</td>"
        opcje = "".join(
            f"<option value='{z}'{' selected' if z == zrodlo else ''}>{z}</option>" for z in kr.ZRODLA
        )
        wiersze.append(
            "<tr>"
            f"<td><a href='/symbol?s={urllib.parse.quote(symbol)}'>{html.escape(symbol)}</a></td>"
            f"<td><a class='btn' href='/analiza?s={urllib.parse.quote(symbol)}'>Analiza</a></td>"
            "<td><form method='post' class='inline'>"
            f"<input type='hidden' name='symbol' value='{html.escape(symbol)}'>"
            f"<select name='zrodlo'>{opcje}</select> "
            f"<input type='text' name='identyfikator' value='{html.escape(ident)}' size='18'> "
            "<button name='akcja' value='zmien'>Zapisz</button> "
            "<button name='akcja' value='usun' onclick=\"return confirm('Usunąć symbol?')\">Usuń</button>"
            "</form></td>"
            f"{ostatnia}</tr>"
        )
    tabela = (
        "<table><tr><th>Symbol</th><th>Analiza</th><th>Źródło i identyfikator</th><th>Ostatnia cena</th>"
        "<th>Status</th><th>Czas (UTC)</th></tr>" + "".join(wiersze) + "</table>"
        if wiersze
        else "<p>Brak symboli. Dodaj poniżej.</p>"
    )

    opcje_nowe = "".join(f"<option value='{z}'>{z}</option>" for z in kr.ZRODLA)
    tg = cfg["telegram"]
    token_info = "ustawiony (…" + tg["token"][-4:] + ")" if len(tg["token"]) > 4 else "nie ustawiony"
    www = cfg["www"]

    tresc = (
        komunikaty(blad, info)
        + f"<p><a href='/csv?dzien={dzis}'>Pobierz CSV z dzisiaj</a></p>"
        + tabela
        + "<fieldset><legend>Dodaj symbol</legend><form method='post'>"
        "<input type='hidden' name='akcja' value='dodaj'>"
        "Symbol: <input type='text' name='symbol' size='10' placeholder='np. TSLA' required> "
        f"Źródło: <select name='zrodlo'>{opcje_nowe}</select> "
        "Identyfikator: <input type='text' name='identyfikator' size='18' placeholder='coingecko: id, yahoo: symbol' required> "
        "<button>Dodaj</button></form>"
        "<p><small>Krypto: źródło coingecko, identyfikator z adresu strony monety (np. dash, zcash). "
        "Akcje i ETF: źródło yahoo, symbol Yahoo (np. NVDA, PKN.WA dla GPW).</small></p></fieldset>"
        "<fieldset><legend>Telegram (alerty)</legend><form method='post'>"
        "<input type='hidden' name='akcja' value='telegram'>"
        f"Token: <input type='password' name='token' size='40' placeholder='zostaw puste, by nie zmieniać'> "
        f"<small>{html.escape(token_info)}</small><br>"
        f"<label><input type='checkbox' name='wyczysc'> wyczyść token (wyłącz alerty)</label><br>"
        f"Chat ID: <input type='text' name='chat_id' value='{html.escape(tg['chat_id'])}' size='16'> "
        f"Próg zmiany w minutę (%): <input type='text' name='prog_proc' value='{tg['prog_proc']}' size='6'> "
        "<button>Zapisz</button></form></fieldset>"
        "<fieldset><legend>Konto panelu</legend><form method='post'>"
        "<input type='hidden' name='akcja' value='konto'>"
        f"Login: <input type='text' name='login' value='{html.escape(www['login'])}' size='12'> "
        "Nowe hasło (min. 8 znaków, puste = bez zmiany): <input type='password' name='haslo' size='20'> "
        "<button>Zapisz</button></form>"
        f"<p><small>Port {www['port']} na {html.escape(www['listen'])}. Zmiana adresu lub portu wymaga restartu usługi kolektor-www.</small></p>"
        "</fieldset>"
    )
    return strona("Kolektor kursów", tresc)


def strona_symbolu(cfg, symbol, zakres='1d'):
    if symbol not in cfg["symbole"]:
        return strona("Nie ma takiego symbolu", "<p><a href='/'>Wróć</a></p>")
    tresc = (
        "<p><a href='/'>← wszystkie symbole</a> · "
        f"<a class='btn' href='/analiza?s={urllib.parse.quote(symbol)}'>Analiza</a></p>"
        f"<h2>{html.escape(symbol)} ({html.escape(cfg['symbole'][symbol][0])}: {html.escape(cfg['symbole'][symbol][1])})</h2>"
        + _wykres_panel(symbol, zakres)
        + "<h3>Najniższy i najwyższy kurs w każdym dniu</h3>"
        + _dzienne_podsumowanie(_seria(symbol, zakres))
    )
    return strona(f"Symbol {symbol}", tresc)


def _liczba(wartosc, przyrostek="", miejsca=2):
    if wartosc is None:
        return "brak danych"
    return f"{wartosc:,.{miejsca}f}{przyrostek}".replace(",", " ")


HORYZONTY = [(1, "1 h"), (3, "3 h"), (5, "5 h"), (8, "8 h"), (12, "12 h"), (24, "1 dzień"), (72, "3 dni"), (168, "7 dni")]


def _kalkulator(symbol, tech, parametry, punkty):
    """Przelicza wynik zakupu przy zadanym kapitale, dla kilku cen wyjścia, i pokazuje, jak często
    w danych wystąpił próg zysku lub straty. Nie wskazuje, kiedy wyjść."""
    def pole(klucz, domyslna):
        return html.escape(parametry[klucz][0] if parametry.get(klucz) else domyslna, quote=True)

    try:
        horyzont_wybrany = int(parametry.get("horyzont", ["8"])[0])
    except ValueError:
        horyzont_wybrany = 8
    opcje = "".join(
        f"<option value='{h}'{' selected' if h == horyzont_wybrany else ''}>{etykieta}</option>"
        for h, etykieta in HORYZONTY
    )
    formularz = (
        "<form method='get'>"
        f"<input type='hidden' name='s' value='{html.escape(symbol, quote=True)}'>"
        f"Kapitał: <input type='text' name='kapital' size='8' value='{pole('kapital', '')}'> "
        f"Kurs wejścia (puste = ostatni): <input type='text' name='wejscie' size='10' value='{pole('wejscie', '')}'> "
        f"Horyzont: <select name='horyzont'>{opcje}</select> "
        f"Próg zysku %: <input type='text' name='zysk' size='4' value='{pole('zysk', '3')}'> "
        f"Próg straty %: <input type='text' name='strata' size='4' value='{pole('strata', '-3')}'> "
        "<button>Przelicz</button></form>"
        "<p class='uwaga'>Pokazuję wynik i to, jak często w zapisanych danych wystąpił dany próg. "
        "Nie wskazuję, kiedy wyjść. Decyzja należy do Ciebie.</p>"
    )
    if not parametry.get("kapital"):
        return formularz
    try:
        kapital = float(parametry["kapital"][0].replace(",", "."))
        wejscie_txt = parametry.get("wejscie", [""])[0].replace(",", ".").strip()
        wejscie = float(wejscie_txt) if wejscie_txt else (punkty[-1][1] if punkty else None)
        zysk = float(parametry.get("zysk", ["3"])[0].replace(",", "."))
        strata = float(parametry.get("strata", ["-3"])[0].replace(",", "."))
    except (ValueError, IndexError):
        return formularz + "<div class='blad'>Podaj liczby w polach kapitał, kurs i progi.</div>"
    if kapital <= 0 or not wejscie or wejscie <= 0:
        return formularz + "<div class='blad'>Kapitał i kurs wejścia muszą być dodatnie.</div>"
    if zysk <= 0 or strata >= 0:
        return formularz + "<div class='blad'>Próg zysku ma być dodatni, a próg straty ujemny.</div>"

    waluta = punkty[-1][2] if punkty else ""
    ilosc = kapital / wejscie
    wiersze = ""
    for m in (-5, -3, -1, 0, 1, 3, 5):
        cena_wyjscia = wejscie * (1 + m / 100)
        wynik = ilosc * (cena_wyjscia - wejscie)
        klasa = "wzrost" if wynik > 0 else ("spadek" if wynik < 0 else "neutralny")
        wiersze += (
            f"<tr><td>{m:+d}%</td><td>{cena_wyjscia:.4g} {html.escape(waluta)}</td>"
            f"<td class='{klasa}'>{wynik:+,.2f} {html.escape(waluta)}</td></tr>"
        )
    tabela = (
        f"<p>Kapitał {kapital:,.2f} {html.escape(waluta)} przy kursie wejścia {wejscie:.4g} "
        f"kupuje {ilosc:.6g} jednostek.</p>"
        "<table><tr><th>Zmiana ceny</th><th>Cena wyjścia</th><th>Wynik</th></tr>" + wiersze + "</table>"
    ).replace(",", " ")

    teraz_ceny = [c for _, c in ka.probki_godzinowe(punkty)]
    srednia = ka.srednia_dzienna_zmiana(teraz_ceny)
    srednia_txt = f"Średni dzienny ruch w danych: {srednia:.2f}%." if srednia is not None else "Za mało danych na średni dzienny ruch."
    wynik_h = ka.pierwsze_trafienia(teraz_ceny, horyzont_wybrany, zysk, strata)
    if wynik_h is None:
        historia = "<p>Za mało danych, żeby policzyć, który próg wystąpił pierwszy w tym horyzoncie.</p>"
    else:
        z, s_, b, n = wynik_h
        etykieta = dict(HORYZONTY).get(horyzont_wybrany, f"{horyzont_wybrany} h")
        historia = (
            f"<p>W zapisanych danych ({n} punktów wejścia, horyzont {etykieta}): "
            f"najpierw <span class='wzrost'>próg zysku {zysk:+.1f}%</span> w {z:.1f}% przypadków, "
            f"najpierw <span class='spadek'>próg straty {strata:.1f}%</span> w {s_:.1f}%, "
            f"żaden próg w {b:.1f}%.</p>"
            "<p class='uwaga'>Przeszłość nie przesądza o przyszłości. Dane z kilku dni są krótkie.</p>"
        )
    return formularz + tabela + f"<p>{srednia_txt}</p>" + historia


def _wykres_renko(cegly, ile=60):
    pokaz = cegly[-ile:]
    if not pokaz:
        return ""
    lo = min(b[1] for b in pokaz)
    hi = max(b[2] for b in pokaz)
    szer, wys, ml, mr, mt, mb = 900, 300, 70, 12, 12, 12
    pw, ph = szer - ml - mr, wys - mt - mb
    cw = pw / len(pokaz)

    def Y(c):
        return mt + (hi - c) / (hi - lo) * ph if hi > lo else mt + ph / 2

    elementy = []
    for k in range(5):
        c = lo + (hi - lo) * k / 4
        elementy.append(f"<line x1='{ml}' y1='{Y(c):.1f}' x2='{szer - mr}' y2='{Y(c):.1f}' stroke='#e5e7eb'/>")
    krok = max(1, len(pokaz) // 6)
    for i in range(0, len(pokaz) + 1, krok):
        x = ml + i * cw
        elementy.append(f"<line x1='{x:.1f}' y1='{mt}' x2='{x:.1f}' y2='{mt + ph}' stroke='#e5e7eb'/>")
    for i, (kierunek, dolna, gorna) in enumerate(pokaz):
        kolor = "var(--accent)" if kierunek == "gora" else "var(--danger)"
        y = Y(gorna)
        h = max(2.0, Y(dolna) - Y(gorna))
        elementy.append(f"<rect x='{ml + i * cw + 1:.1f}' y='{y:.1f}' width='{cw - 2:.1f}' height='{h:.1f}' "
                        f"style='fill:{kolor}'/>")
    for c in (lo, (lo + hi) / 2, hi):
        elementy.append(f"<text x='{ml - 6}' y='{Y(c) + 4:.1f}' text-anchor='end' font-size='11'>{c:.4g}</text>")
    return (f"<svg data-zoom='1' viewBox='0 0 {szer} {wys}' width='100%' style='border:1px solid var(--line)'>"
            + "".join(elementy) + "</svg>")


def _renko_html(punkty):
    probki = [c for _, c in ka.probki_godzinowe(punkty)]
    rozmiar = ka.rozmiar_cegly(probki)
    if not rozmiar:
        return "<p>Za mało danych z kolektora, żeby zbudować cegły Renko.</p>"
    cegly, poziom = ka.renko(probki, rozmiar)
    zdania = "".join(f"<li>{html.escape(z)}</li>" for z in ka.opis_renko(cegly, poziom, rozmiar))
    return (_wykres_renko(cegly) + f"<ul>{zdania}</ul>"
            + "<p class='uwaga'>Zielone cegły idą w górę, czerwone w dół. Renko pomija czas, "
              "więc każda cegła to tylko zmiana ceny o pełny rozmiar.</p>")


def strona_analizy(cfg, symbol, parametry=None):
    parametry = parametry or {}
    if symbol not in cfg["symbole"]:
        return strona("Nie ma takiego symbolu", "<p><a href='/'>Wróć</a></p>")
    zrodlo, ident = cfg["symbole"][symbol]
    teraz = datetime.now(timezone.utc)
    punkty = kr.wczytaj_serie(teraz - timedelta(days=ka.OKNO_DNI), teraz).get(symbol, [])
    o = ko.ocena(symbol, zrodlo, ident, punkty)
    tech, rynek = o["tech"], o["rynek"]

    if tech:
        rsi_txt = f"{tech['rsi']:.1f} – {ka.opis_rsi(tech['rsi'])}" if tech["rsi"] is not None else "za mało danych"
        if tech["rsi"] is None:
            klasa_rsi = "neutralny"
        elif tech["rsi"] > 70 or tech["rsi"] < 30:
            klasa_rsi = "ostrzezenie"
        else:
            klasa_rsi = "neutralny"
        klasa_cena = "wzrost" if tech["sma24"] is not None and tech["ostatnia"] > tech["sma24"] else (
            "spadek" if tech["sma24"] is not None else "neutralny")
        klasa_trend = "wzrost" if tech["sma24"] is not None and tech["sma72"] is not None and tech["sma24"] > tech["sma72"] else (
            "spadek" if tech["sma24"] is not None and tech["sma72"] is not None else "neutralny")
        wiersze_tech = (
            f"<tr><th>Ostatnia cena</th><td class='{klasa_cena}'>{_liczba(tech['ostatnia'], miejsca=4)}</td></tr>"
            f"<tr><th>Średnia 24 h (SMA24)</th><td>{_liczba(tech['sma24'], miejsca=4)}</td></tr>"
            f"<tr><th>Średnia 72 h (SMA72)</th><td class='{klasa_trend}'>{_liczba(tech['sma72'], miejsca=4)}</td></tr>"
            f"<tr><th>RSI14</th><td class='{klasa_rsi}'>{html.escape(rsi_txt)}</td></tr>"
            f"<tr><th>Min / maks w oknie</th><td>{_liczba(tech['min'], miejsca=4)} / {_liczba(tech['maks'], miejsca=4)}</td></tr>"
            f"<tr><th>Próbki godzinowe</th><td>{tech['probek']}</td></tr>"
        )
        def _wiersze_poziomow(lista, klasa_poziomu):
            if not lista:
                return "<tr><td colspan='2'>brak w danych</td></tr>"
            return "".join(
                f"<tr class='{klasa_poziomu}'><td>{p:.4g}</td><td>{n} dotknięć, {(p - tech['ostatnia']) / tech['ostatnia'] * 100:+.1f}%</td></tr>"
                for p, n in lista
            )
        poziomy_html = (
            "<h3>Poziomy (z dołków i szczytów)</h3>"
            "<table><tr><th>Opór (najbliższe pierwsze)</th><th>Szczegóły</th></tr>"
            + _wiersze_poziomow(tech["opory"], "opor")
            + "</table><table><tr><th>Wsparcie (najbliższe pierwsze)</th><th>Szczegóły</th></tr>"
            + _wiersze_poziomow(tech["wsparcia"], "wsparcie")
            + "</table>"
        )
        tech_html = f"<table>{wiersze_tech}</table>" + poziomy_html
    else:
        tech_html = "<p>Za mało danych z kolektora, żeby policzyć wskaźniki.</p>"

    if o["blad_rynku"]:
        rynek_html = f"<div class='blad'>{html.escape(o['blad_rynku'])}</div>"
        nastroje_html = "<p>Brak danych o nastrojach, bo źródło nie odpowiedziało.</p>"
        wiadomosci_html = "<p>Brak wiadomości.</p>"
    else:
        r = rynek
        wycena = [
            ("Nazwa", html.escape(r.get("nazwa") or "-")),
            ("Cena (źródło)", f"{_liczba(r.get('cena'), miejsca=4)} {html.escape(r.get('waluta') or '')}"),
            ("Kapitalizacja", _liczba(r.get("kapitalizacja"), miejsca=0)),
            ("Ranking wg kapitalizacji", html.escape(str(r["ranking"])) if r.get("ranking") else "brak danych"),
            ("Wolumen 24 h", _liczba(r.get("wolumen_24h"), miejsca=0)),
            ("Historyczne maksimum", _liczba(r.get("maks_historyczny"), miejsca=4)),
            ("Od historycznego maksimum", _liczba(r.get("od_maks_proc"), "%", 1)),
            ("Zmiana 7 dni", _liczba(r.get("zmiana_7d_proc"), "%", 2)),
            ("Zmiana 30 dni", _liczba(r.get("zmiana_30d_proc"), "%", 2)),
            ("Maks / min 52 tygodnie", f"{_liczba(r.get('maks_52t'), miejsca=4)} / {_liczba(r.get('min_52t'), miejsca=4)}"),
        ]
        rynek_html = (
            "<table>" + "".join(f"<tr><th>{etykieta}</th><td>{wartosc}</td></tr>" for etykieta, wartosc in wycena)
            + "</table>"
            + f"<p class='uwaga'>Wycena: {html.escape(r.get('wycena') or '')}. Źródło: {html.escape(r['zrodlo'])}.</p>"
        )
        if r.get("nastroje_wzrost_proc") is not None:
            nastroje_html = (
                f"<p>Głosujących za wzrostem: {r['nastroje_wzrost_proc']:.1f}%, "
                f"za spadkiem: {r['nastroje_spadek_proc']:.1f}%.</p>"
                "<p class='uwaga'>To opinie użytkowników CoinGecko, nie prognoza.</p>"
            )
        else:
            nastroje_html = "<p>W tym źródle nie ma wskaźnika nastrojów dla tego symbolu.</p>"
        bezpieczne = [w for w in r["wiadomosci"] if w["link"].startswith("https://")]
        if bezpieczne:
            wiadomosci_html = "<ul>" + "".join(
                f"<li><a href='{html.escape(w['link'], quote=True)}' target='_blank' rel='noopener noreferrer'>"
                f"{html.escape(w['tytul'])}</a> <span class='uwaga'>({html.escape(w['wydawca'])})</span></li>"
                for w in bezpieczne
            ) + "</ul>"
        else:
            wiadomosci_html = "<p>Brak wiadomości z tego źródła.</p>"

    uwagi_html = "".join(f"<li>{html.escape(u)}</li>" for u in o["uwagi"]) or "<li>Brak uwag.</li>"
    kalkulator_html = _kalkulator(symbol, tech, parametry, punkty)
    tresc = (
        "<p><a href='/'>← wszystkie symbole</a> · "
        f"<a href='/symbol?s={urllib.parse.quote(symbol)}'>wykres i odczyty</a></p>"
        f"<h2>{html.escape(symbol)} ({html.escape(zrodlo)}: {html.escape(ident)})</h2>"
        "<fieldset><legend>Wskaźniki techniczne (z kolektora)</legend>" + tech_html + "</fieldset>"
        "<fieldset><legend>Wykres Renko</legend>" + _renko_html(punkty) + "</fieldset>"
        "<fieldset><legend>Wycena i dane rynkowe</legend>" + rynek_html + "</fieldset>"
        "<fieldset><legend>Nastroje</legend>" + nastroje_html + "</fieldset>"
        "<fieldset><legend>Wiadomości</legend>" + wiadomosci_html + "</fieldset>"
        "<fieldset><legend>Jak często taki ruch się zdarzał</legend>" + kalkulator_html + "</fieldset>"
        "<fieldset><legend>Co z tego wynika</legend><ul>" + uwagi_html + "</ul>"
        "<p class='uwaga'>To opis danych i sygnałów, nie rekomendacja kupna, sprzedaży ani trzymania. "
        "Decyzja należy do Ciebie. Przed ruchem sprawdź dane u źródła i w razie wątpliwości skonsultuj się z doradcą.</p>"
        "</fieldset>"
        f"<p class='uwaga'>Wygenerowano {o['wygenerowano']:%Y-%m-%d %H:%M} UTC. Dane zewnętrzne są buforowane 10 minut.</p>"
    )
    return strona(f"Analiza {symbol}", tresc)


# --- akcje (POST) -----------------------------------------------------------

def jedno(dane, klucz):
    return dane.get(klucz, [""])[0].strip()


def wykonaj_akcje(cfg, dane):
    akcja = jedno(dane, "akcja")
    symbole = cfg["symbole"]

    if akcja in ("dodaj", "zmien"):
        symbol = jedno(dane, "symbol").upper()
        zrodlo = jedno(dane, "zrodlo").lower()
        ident = jedno(dane, "identyfikator")
        if not RE_SYMBOL.match(symbol):
            raise ValueError("Symbol: od 1 do 20 znaków, wielkie litery, cyfry, . ^ = -")
        if zrodlo not in kr.ZRODLA:
            raise ValueError("Źródło musi być coingecko albo yahoo")
        if zrodlo == "coingecko" and not RE_CG_ID.match(ident):
            raise ValueError("Identyfikator CoinGecko: małe litery, cyfry i myślniki (np. dash)")
        if zrodlo == "yahoo" and not RE_YAHOO.match(ident):
            raise ValueError("Identyfikator Yahoo: litery, cyfry, . ^ = - (np. NVDA, PKN.WA)")
        if akcja == "dodaj" and symbol in symbole:
            raise ValueError(f"Symbol {symbol} już jest na liście")
        if akcja == "zmien" and symbol not in symbole:
            raise ValueError(f"Nie ma symbolu {symbol}")
        symbole[symbol] = (zrodlo, ident)
        kr.zapisz_pelna(CONFIG, cfg)
        return f"Zapisano {symbol} ({zrodlo}:{ident}). Kolektor użyje tego od następnej minuty."

    if akcja == "usun":
        symbol = jedno(dane, "symbol").upper()
        if symbol not in symbole:
            raise ValueError(f"Nie ma symbolu {symbol}")
        del symbole[symbol]
        kr.zapisz_pelna(CONFIG, cfg)
        return f"Usunięto {symbol}. Dane z CSV zostają bez zmian."

    if akcja == "telegram":
        tg = cfg["telegram"]
        chat_id = jedno(dane, "chat_id")
        if chat_id and not re.match(r"^-?\d+$", chat_id):
            raise ValueError("Chat ID to liczba (dla grup zaczyna się od minusa)")
        try:
            prog = float(jedno(dane, "prog_proc").replace(",", ".") or tg["prog_proc"])
        except ValueError:
            raise ValueError("Próg zmiany to liczba, np. 3.0")
        if not 0.01 <= prog <= 100:
            raise ValueError("Próg zmiany musi być w zakresie 0.01–100")
        nowy_token = jedno(dane, "token")
        if nowy_token and not RE_CZYSTY.match(nowy_token):
            raise ValueError("Token zawiera niedozwolone znaki")
        if jedno(dane, "wyczysc"):
            tg["token"] = ""
        elif nowy_token:
            tg["token"] = nowy_token
        tg["chat_id"] = chat_id
        tg["prog_proc"] = prog
        kr.zapisz_pelna(CONFIG, cfg)
        return "Zapisano ustawienia Telegrama."

    if akcja == "konto":
        ww = cfg["www"]
        login = jedno(dane, "login")
        nowe = jedno(dane, "haslo")
        if not login or not RE_CZYSTY.match(login):
            raise ValueError("Login nie może być pusty")
        if nowe and len(nowe) < 8:
            raise ValueError("Nowe hasło musi mieć co najmniej 8 znaków")
        ww["login"] = login
        if nowe:
            ww["haslo"] = hasz_hasla(nowe)
        kr.zapisz_pelna(CONFIG, cfg)
        return "Zapisano konto panelu." + (" Hasło zmienione." if nowe else "")

    raise ValueError("Nieznana akcja")


# --- serwer -----------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "kolektor-www"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _wyslij(self, kod, typ, dane, naglowki=None):
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(dane)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (naglowki or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(dane)

    def _html(self, kod, tresc):
        self._wyslij(kod, "text/html; charset=utf-8", tresc.encode("utf-8"))

    def _przekieruj(self, adres):
        self._wyslij(303, "text/plain; charset=utf-8", b"", {"Location": adres})

    def _autoryzacja(self):
        cfg = kr.wczytaj_pelna(CONFIG)
        ww = cfg["www"]
        naglowek = self.headers.get("Authorization", "")
        if naglowek.startswith("Basic ") and ww["haslo"]:
            try:
                login, _, haslo = base64.b64decode(naglowek[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                login, haslo = "", ""
            if hmac.compare_digest(login, ww["login"]) and sprawdz_haslo(haslo, ww["haslo"]):
                return cfg
        self._wyslij(401, "text/plain; charset=utf-8", b"Wymagane logowanie",
                     {"WWW-Authenticate": 'Basic realm="kolektor"'})
        return None

    def do_GET(self):
        cfg = self._autoryzacja()
        if cfg is None:
            return
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            return self._html(200, strona_glowna(cfg, q.get("blad", [""])[0], q.get("info", [""])[0]))
        if url.path == "/symbol":
            return self._html(200, strona_symbolu(cfg, q.get("s", [""])[0].upper(), q.get("zakres", ["1d"])[0]))
        if url.path == "/analiza":
            return self._html(200, strona_analizy(cfg, q.get("s", [""])[0].upper(), q))
        if url.path == "/csv":
            dzien = q.get("dzien", [""])[0]
            if not RE_DZIEN.match(dzien):
                return self._html(400, strona("Zły dzień", "<p>Podaj dzien=RRRR-MM-DD.</p>"))
            plik = os.path.join(DANE, dzien[:4], dzien[5:7], f"ceny-{dzien}.csv")
            if not os.path.isfile(plik):
                return self._html(404, strona("Brak pliku", "<p>Nie ma danych z tego dnia.</p>"))
            with open(plik, "rb") as f:
                dane = f.read()
            return self._wyslij(200, "text/csv; charset=utf-8", dane,
                                {"Content-Disposition": f'attachment; filename="ceny-{dzien}.csv"'})
        self._html(404, strona("Nie znaleziono", "<p><a href='/'>Strona główna</a></p>"))

    def do_POST(self):
        cfg = self._autoryzacja()
        if cfg is None:
            return
        try:
            dlugosc = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            dlugosc = -1
        if dlugosc < 0 or dlugosc > MAX_POST:
            return self._html(413, strona("Za duże", "<p>Żądanie jest za duże.</p>"))
        dane = urllib.parse.parse_qs(self.rfile.read(dlugosc).decode("utf-8", "replace"), keep_blank_values=True)
        try:
            info = wykonaj_akcje(cfg, dane)
            self._przekieruj("/?info=" + urllib.parse.quote(info))
        except ValueError as exc:
            self._przekieruj("/?blad=" + urllib.parse.quote(str(exc)))


def main():
    cfg = kr.wczytaj_pelna(CONFIG)
    ww = cfg["www"]
    if not ww["haslo"]:
        print("Brak hasła w configu. Uruchom najpierw: kolektor_www.py --init", file=sys.stderr)
        return 1
    serwer = http.server.ThreadingHTTPServer((ww["listen"], ww["port"]), Handler)
    print(f"Panel www na {ww['listen']}:{ww['port']}", flush=True)
    try:
        serwer.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        sys.exit(init())
    sys.exit(main())
