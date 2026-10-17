# NHL Team Shots Analyzer 0.26

Verze 0.26 je zaměřená na **přehlednost a jednoduchý světlý vzhled**, bez změny ověřené produkční logiky trhu Více střel.

## Nový vzhled
- bílý podklad a světlé karty;
- výrazně méně vizuálního šumu;
- tmavé neutrální ovládací prvky místo barev, které by mohly působit jako hodnocení;
- zelená = lepší / pozitivní výsledek, červená = horší / negativní výsledek;
- u barev je současně šipka nahoru/dolů, takže význam není závislý jen na barvě;
- NHL loga zůstávají přímo u týmů a pravděpodobností;
- na mobilu se důležité části řadí dříve: zápas → souhrn → trh → kurz/EV → produkční model; detailní data následují až potom.

## Analýza
Produkční `LOCK 5v5+tied` pro trh **Více střel** zůstává beze změny. Vzájemné pravděpodobnosti se nyní vizuálně porovnají: vyšší hodnota je zelená, nižší červená a remíza zůstává neutrální.

Stejná logika přehlednosti se používá i u over/under a 1/X/2 zobrazení. Barvy neznamenají sázkové doporučení; pouze rychle ukazují, která modelová pravděpodobnost je vyšší nebo zda se metrika zlepšila/zhoršila.

## Backtest
Výpočty, Model 12 Lockbox, confidence pásma i deep režim jsou stejné jako v 0.25. Tabulky jsou pouze převedené do světlého vzhledu a pozitivní/negativní změny jsou výraznější.
