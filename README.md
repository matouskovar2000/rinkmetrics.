# NHL Team Shots Analyzer 0.4

Mobilní webová aplikace pro ruční porovnání dvou týmů NHL a pět analytických trhů: více střel, střely týmu O/U, celkové střely O/U, góly O/U, výsledek po 60 minutách a dvojtip.

## Novinky
- Výběr posledních 5/10/20/30 zápasů, celé aktuální základní části nebo automatické formy.
- Automatická forma: 55 % váhy širšího vzorku (až 30 zápasů), 25 % posledních 10 a 20 % posledních 5; váhy jsou předběžné, nebyly optimalizovány backtestem.
- Korekce domácí/venkovní formy: pokud je alespoň 5 zápasů daného typu, 20 % váhy týmového průměru tvoří průměr pro dané prostředí.
- Tabulka formy za 5/10/20/30 zápasů, střely povolené, rozdíl posledních pěti proti předchozím pěti.
- Historická četnost překročení hranice u týmových střel a součtu střel či gólů.
- Oddělení popisné historie od modelových pravděpodobností.

## Spuštění
`python server.py` (Python 3, bez externích závislostí), lokálně http://localhost:8000. Na Renderu: Build `echo "No dependencies"`, Start `python server.py`.

## Omezení
Není proveden historický backtest ani kalibrace pravděpodobností. Nejde o ověřený prediktivní model. Zatím bez síly soupeřů, potvrzených brankářů a xG. Pro celé období se používá aktuální sezóna; pro 5/10/20/30 zápasů může historie na začátku sezóny zasahovat do předchozích sezón. Data z NHL Web API mohou být dočasně nedostupná. Výsledky nejsou doporučení k sázení.
