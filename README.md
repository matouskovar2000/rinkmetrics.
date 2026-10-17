# NHL Team Shots Analyzer 0.27

Verze 0.27 je skutečný redesign hlavní obrazovky Analýzy podle schváleného světlého návrhu. Výpočty a produkční logika zůstávají stejné jako v 0.26.

## Nové rozložení
- velká horní karta zápasu s logy NHL týmů, domácí/venkovní rolí a očekávanými střelami;
- samostatná karta Výsledek analýzy;
- dominantní karta Tip sázky s pěti trhy a výrazným zeleno/červeným porovnáním;
- Forma a kvalita dat + Produkční model vedle sebe;
- Odpočinek/cestování/sestavy + Vyhodnocení kurzu vedle sebe;
- graf posledních zápasů přes celou šířku;
- ruční scénář výkonu je schovaný v rozbalovací sekci;
- projektované sestavy jsou rozbalovací, protože jsou detailní a nejsou započítané do modelu.

## Model
Produkční `LOCK 5v5+tied` pro trh Více střel se nemění. Fallback na Model 1, lockbox validace, confidence pásma, Backtest a Moje tipy zůstávají funkčně stejné.
