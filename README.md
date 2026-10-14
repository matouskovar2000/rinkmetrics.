# NHL Team Shots Analyzer 0.3

Mobilní webová aplikace pro ruční porovnání dvou týmů NHL a pět trhů:
- více střel na branku (dvoucestný trh s vrácením vkladu při shodě),
- počet střel vybraného týmu nad/pod hranicí,
- celkový počet střel nad/pod hranicí,
- celkový počet gólů nad/pod hranicí,
- vítěz po 60 minutách a dvojtip 1X / X2 / 12.

## Spuštění

Soubory `index.html` a `server.py` musí být ve stejném adresáři.

```bash
python server.py
```

Pak otevřete http://localhost:8000. Na Renderu nastavte Python, Build Command `echo "No dependencies"`, Start Command `python server.py`, Root Directory prázdné a Free instance.

## Aktualizace stávajícího Render nasazení

Nahrajte soubory `index.html`, `server.py`, `README.md` přímo do kořene GitHub repozitáře `rinkmetrics` (ne celý ZIP). Render s automatickým deployem nasadí změny po commitu. `server.py` je kompatibilní s proměnnou `PORT` na Renderu.

## Důležité hranice modelu

Používá veřejné NHL API přes vlastní omezený proxy server. Model je zjednodušený Poissonův odhad z průměrů střel/gólů a povolených střel/gólů, **není backtestovaný**. Přepínač domácího týmu slouží k označení; model zatím neupravuje pravděpodobnosti podle domácí/venkovní formy. Zobrazené výpočty pro góly se opírají o konečné skóre z boxscore (může zahrnovat prodloužení či rozhodující nájezd), takže model trhu po 60 minutách je jen orientační a pro přesné vyhodnocení potřebuje samostatná data po třetinách. Vítěz včetně prodloužení není implementován. Před skutečným sázením je nutné model ověřit na historických datech a ověřit pravidla vyhodnocení konkrétní sázkové kanceláře.
