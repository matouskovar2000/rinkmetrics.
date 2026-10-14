# NHL Team Shots Analyzer 0.7

Nový výrazně přepracovaný vzhled (černá, červená, bílá, ledově modrá), velká karta porovnání týmů, transparentní scénářová analýza pro střely a góly, všech pět trhů, výběr skutečného zápasu NHL, odpočinek a orientační cestování, deník Moje tipy a lokální export/import JSON.

## Instalace
Nahraj soubory `index.html`, `server.py` a `README.md` do kořene existujícího GitHub repozitáře. Render používá `python server.py`.

## Jak funguje scénář
Čtyři ruční změny očekávaných střel/gólů se promítnou do pravděpodobností všech pěti trhů, modelových férových kurzů i EV. Jsou to **předpoklady uživatele**, nikoli ověřené odhady vlivu konkrétních zranění. Základní model zůstává beze změny, dokud uživatel hodnoty neupraví. Reset vrací původní model.

## Omezení
Model není historicky validován. Neobsahuje automatické spolehlivé sestavy, zranění ani brankáře; cestování a odpočinek se ukazují, ale samy automaticky neupravují model. Deník tipů je uložen pouze v prohlížeči a vyžaduje zálohování. NHL API může být dočasně nedostupné. Nejde o sázková doporučení.
