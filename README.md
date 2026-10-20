# NHL Team Shots Analyzer 0.41

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


## 0.38 – pokročilá interpretace bez přeladění modelu
- Vzhled 0.36/0.37 zůstává zachován; mění se hlavně informace uvnitř existujících detailů.
- Produkční panel nově odděluje dnešní modelovou pravděpodobnost, separaci týmů a historickou jistotu validačního pásma. Historický 95% interval není vydáván za interval dnešní predikce.
- Technické detaily zobrazují všechny shot attempts, 5v5 share, tied share, vážený LOCK control a PBP coverage pro oba týmy. Váhy LOCKu zůstávají 35/40/25 a nebyly přeladěny.
- Forma a odpočinek/cestování jsou viditelné jako diagnostický kontext. Produkční LOCK je kvůli tomu nově nepřeváží ani nepřelaďuje.
- Sestavy ukazují stav PROJECTED, čas načtení a výslovně uvádějí, že potvrzená game lineup není z aktuálního NHL.com zdroje k dispozici.
- Horní označení verze bylo opraveno na v0.38.


## 0.41 – funkční dotažení
- Vzhled 0.38 zůstává beze změny.
- Vyhodnocení kurzu po změně trhu automaticky předvybere hlavní modelový výběr: u 1/X/2 nejsilnější z 1/X/2, u Více střel favorizovaný tým a u line marketů pravděpodobnější stranu.
- Produkční model ukazuje „Proč model“ přímo bez rozkliknutí technických detailů.
- Karty odpočinku/cestování zobrazují stav sestavy PROJECTED / NENALEZENO / JINÉ DATUM / NEDOSTUPNÉ a čas aktualizace zdroje.
- LOCK 5v5+tied, jeho váhy a všechny výpočty zůstávají beze změny.


## 0.41 – stabilita a shoda modelů
- Vzhled 0.39 zůstává zachovaný.
- Produkční panel nově ukazuje citlivost favorizovaného týmu při posunu ±0,75 očekávané střely.
- Zobrazuje, zda Model 1 a LOCK 5v5+tied volí stejný tým, a velikost posunu pravděpodobnosti.
- Zobrazuje minimální PBP coverage obou týmů jako samostatnou kontrolu kvality dat.
- Citlivostní rozsah není confidence interval ani nová kalibrace; váhy LOCKu se nemění.


## 0.41 – přesnější vysvětlení LOCKu
- Vzhled zůstává stejný jako 0.40.
- Nejasné slovní hodnocení „Separace týmů“ bylo nahrazeno přesným „Náskokem modelu“ v procentních bodech.
- PBP faktory nově ukazují vážený příspěvek všech pokusů, 5v5 a pokusů při shodném skóre do rozdílu LOCK controlu.
- „Proč model“ zvýrazňuje nejsilnější vážený PBP faktor.
- Technické detaily ukazují i změnu rozdílu očekávaných střel Model 1 → LOCK.
- Produkční váhy, lockbox validace ani vzhled nebyly změněny.

## 0.42 — forward validation (development)
No production model weight or CSS changes. Automatic pregame snapshot only when fixture has an NHL ID, start time is future, and LOCK is ready. Server independently recomputes shadow model, stores SQLite immutable forecast; repeated capture cannot overwrite. `GET /forward-v042/settle` fetches official finished NHL boxscores and appends outcomes; `GET /forward-v042/report` returns Brier, calibration, hit rate, Wilson intervals and confidence bands. Tied SOG games are reported separately and excluded from binary home-more-shots metrics. SQLite file must live on persistent storage in production; set `FORWARD_DB_PATH` to persistent volume. Settling currently requires calling the endpoint (e.g. scheduler); no unattended background scheduler is installed. This is an initial integration, not independently forward-validated yet.
