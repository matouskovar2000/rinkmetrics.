# NHL Team Shots Analyzer 0.37

Verze 0.31 je vizuální přestavba Analýzy podle schváleného světlého mockupu. Produkční výpočty se nemění.

- kompaktní modrobílý header a navigace;
- výběr týmů s logy přímo v polích;
- velká karta zápasu se dvěma logy a očekávanými střelami/góly;
- Výsledek analýzy se dvěma týmovými kartami, povolenými a očekávanými střelami;
- dominantní zelená karta Tip sázky s LOCK 5v5+tied;
- forma a produkční model vedle sebe;
- odpočinek/sestavy a vyhodnocení kurzu vedle sebe;
- graf posledních zápasů přes celou šířku;
- technické detaily zůstávají sekundární.

LOCK 5v5+tied, Model 12 lockbox, Backtest i ostatní výpočty zůstávají funkčně stejné jako v 0.29.


## 0.31 vzhled
Tato verze mění pouze vizuální proporce Analýzy na mobilu tak, aby odpovídaly schválenému mockupu: větší loga, texty, výsledkové karty a tip; modelová logika se nemění.


## 0.32
Opravena mobilní typografie a velikosti prvků. Verze 0.31 byla na telefonu přepisována příliš agresivním breakpointem a text i karty se zmenšovaly pod cílový mockup. 0.32 zachovává stejné výpočty a pouze mění vzhled.


## 0.36
Kompletní vizuální přestavba stránky Analýza podle schváleného mockupu. Výpočty a produkční LOCK model se nemění. Přestavěn header, výběr zápasu, hero matchup, výsledkové karty, tržní karta, forma/model, cestování/kurz a historie.


## 0.36
Finální fidelity pass podle schváleného mockupu: větší typografie a loga na Android šířce, párový graf střely/povolené střely, kompaktní lineup souhrn v kartě cestování. Modelová logika beze změny.


## 0.36
Finální vizuální kontrola proti schválenému mockupu: header a navigace, ikona značky, větší loga ve výsledku a mřížka grafu. Skrytý technický řádek výběru konkrétního fixture zůstává v DOM pro automatiku, ale neruší hlavní návrh. Modelová logika beze změny.


## 0.36
Vizuální doladění podle schváleného mockupu a skutečného screenshotu z Androidu. Výpočty a produkční LOCK 5v5+tied se nemění.


## 0.37 – advanced analysis
- Vzhled 0.36 zůstává beze změny.
- Očekávané střely jsou nyní po načtení LOCKu sjednocené mezi hero kartou, Výsledkem analýzy a Produkčním modelem.
- Technické detaily LOCKu ukazují rozdíl proti Modelu 1, změnu pravděpodobnosti, vážený shot-attempt control a vysvětlení, co model posunulo.
- Lockbox 95% interval je výslovně označen jako historické pásmo, ne interval dnešní predikce.
- Vyhodnocení kurzu nově ukazuje break-even pravděpodobnost a rozdíl modelu v procentních bodech.
- Žádné váhy ani produkční model nebyly přeladěny.
