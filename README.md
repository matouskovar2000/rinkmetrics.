# NHL Team Shots Analyzer 0.29

Verze 0.29 dokončuje mobilní redesign Analýzy podle schváleného světlého mockupu. Výpočty a produkční `LOCK 5v5+tied` se nemění.

## Vzhled
- výrazně větší loga, názvy týmů a hlavní čísla;
- karta Výsledek analýzy obsahuje loga a jednodušší vizuální porovnání;
- karta Tip sázky je dominantní zelený blok se silnou hierarchií;
- Produkční model je zjednodušen na tři bloky: aktivní model, očekávané střely a síla signálu; technické detaily jsou rozbalovací;
- větší mezery, čitelnější typografie a méně drobného textu;
- zelená značí lepší/pozitivní výsledek a červená horší/negativní, vždy spolu se symbolem nebo textem.

## Model
Produkční logika z 0.28 zůstává beze změny: `LOCK 5v5+tied` je hlavní model pouze pro Více střel a při nedostatečné PBP historii se použije Model 1 fallback. Ostatní trhy se nemění.
