# NHL Team Shots Analyzer v0.2

Mobilní webová aplikace s proxy serverem pro NHL Web API.

## Spuštění na počítači

Vyžaduje Python 3.10+. Ve složce aplikace spusťte:

```bash
python server.py
```

Otevřete `http://localhost:8000`. **Neotevírejte `index.html` přímo jako stažený soubor** – potřebuje serverový endpoint `/api`.

## Zpřístupnění na mobilu

Aplikaci nasaďte na hosting, který umí spustit Python server (například Render nebo Railway). Spouštěcí příkaz: `python server.py`; hosting předá port proměnnou `PORT`. Poté otevřete přidělenou HTTPS adresu na mobilu. Při lokálním spuštění ve stejné Wi-Fi síti lze použít IP adresu počítače a port 8000 (pokud to firewall dovolí).

## Omezení

Neproběhl živý test dostupnosti NHL API z hostingu. Model je orientační, nevalidovaný na historických sázkách; nepočítá automaticky domácí/venkovní efekt, změny sestav ani pravidla vyhodnocení remízy. Pokud není dostatek dokončených zápasů základní části, aplikace může použít starší sezóny.
