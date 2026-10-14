# NHL Team Shots Analyzer 0.8

Verze 0.8 mění hlavně **strukturu rozhraní** podle schváleného dashboardového návrhu: nahoře kompaktní výběr zápasu, pod ním dvousloupcový analytický dashboard s kartou zápasu, formou, pravděpodobnostmi, ručním kurzem/EV, grafem a doplňkovými faktory.

Funkce zůstávají: všech pět trhů, 30zápasová automatická forma, domácí/venkovní korekce, konkrétní zápas z rozpisu, odpočinek/back-to-back a orientační cestování, ruční scénář změny střel/gólů a deník Moje tipy s lokálním uložením a automatickým vyhodnocením dostupných trhů z NHL dat.

## Omezení
Sestavy, zranění a potvrzený brankář nejsou automaticky strojově načítány ani zahrnuty do základního modelu. Cestování a odpočinek jsou zobrazené, ale zatím automaticky nemění pravděpodobnosti. Model není historicky validován a není sázkovým doporučením.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render může dál používat `python server.py`.
