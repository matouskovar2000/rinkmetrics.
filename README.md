# NHL Team Shots Analyzer 0.10

Verze 0.10 přidává první skutečný **historický backtest modelu**. Cílem je ověřovat pravděpodobnosti na minulých zápasech místo dalšího přidávání neověřených koeficientů.

## Nové ve 0.10
- nová záložka **Backtest**;
- pro vybraný tým a historickou sezonu lze otestovat posledních 5, 10, 15 nebo 20 dokončených zápasů;
- každý historický odhad se počítá pouze z utkání odehraných **před testovaným zápasem**, aby nevznikal únik budoucích dat;
- používá stejnou základní logiku jako hlavní automatická forma: 55 % delší historie, 25 % posledních 10, 20 % posledních 5 a 20% korekci doma/venku při dostatečném vzorku;
- vyhodnocuje pět oblastí: více střel, týmové střely, celkové střely, góly a 1/X/2 po 60 minutách;
- zobrazuje úspěšnost, průměrnou modelovou jistotu, Brier score a kalibrační pásma;
- ukazuje jednotlivé kontrolované zápasy a zda vyšel modelový výběr na více střel.

## Důležité omezení backtestu
Toto je **beta validační slice jednoho týmu**, nikoli ještě league-wide backtest stovek zápasů. Pro týmové střely, celkové střely a góly se používají pevné referenční hranice 27,5 / 56,5 / 5,5, protože nemáme historické bookmakerové line ani kurzy. Proto backtest nepočítá historické EV ani ROI.

Po ověření funkčnosti bude dalším krokem rozšíření na stovky ligových zápasů, uložení/cache historických dat a následné testování dopadu B2B, odpočinku, cestování, brankáře a absencí.

## Další funkce
Zůstává dashboard, všech pět analytických trhů, ruční kurzy a modelové EV, konkrétní rozpis NHL, cestování/odpočinek, projektované sestavy NHL.com a deník **Moje tipy** s automatickým vyhodnocováním.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render používá `python server.py`.

## Upozornění
Model zatím není historicky kalibrovaný ani validovaný na dostatečně velkém vzorku. Backtest 0.10 je nástroj právě k tomu, abychom tuto validaci začali dělat transparentně. Nejde o sázkové doporučení.
