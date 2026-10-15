# NHL Team Shots Analyzer 0.12

Verze 0.12 přidává **experimentální Model 2** a porovnává ho v historickém backtestu proti původnímu Modelu 1.

## Model 2
Model 2 používá pouze informace dostupné před zápasem a přidává:
- exponenciální vážení formy (half-life 8 zápasů), takže novější utkání mají plynule větší váhu;
- aditivní normalizaci síly soupeře: vlastní útok + obrana soupeře vůči dynamickému předzápasovému baseline;
- kontext back-to-back, počtu volných dní a cestování odvozený z předchozí historie daného týmu;
- korekce B2B/cestování jsou shrinkované a omezené, aby několik extrémních zápasů nerozbilo predikci;
- stejnou střídmou doma/venku korekci jako Model 1.

## Nové v Backtestu
- přímá tabulka **Model 1 vs. Model 2** pro všech pět trhů;
- rozdíl úspěšnosti a rozdíl Brier score;
- u O/U hranic jednoduchý **baseline** („vždy častější strana“);
- **Brier Skill Score** Modelu 2 proti tomuto baseline;
- zobrazení průměrného cestování, počtu B2B tým-zápasů a velikosti kontextových korekcí;
- tabulka kontrolovaných zápasů ukazuje očekávané střely Modelu 1 i Modelu 2 vedle skutečnosti.

## Důležité
Model 2 je zatím pouze kandidát v Backtestu. Živá záložka Analýza dál používá Model 1. Model 2 se nemá nasadit do hlavního výpočtu, dokud na větším league-wide vzorku nezlepší především Brier score a kalibraci.

Baseline u hranic je klimatologický baseline daného testovacího vzorku, nikoli historická sázková strategie. Backtest stále nepoužívá historické kurzy bookmakerů, takže z něj nelze přímo počítat historické EV nebo ROI.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render používá `python server.py`.
