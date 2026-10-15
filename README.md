# NHL Team Shots Analyzer 0.13

Verze 0.13 přidává **ablation test** a chronologickou kalibraci. Cílem není přidávat další faktory naslepo, ale přesně změřit, která změna proti Modelu 1 skutečně pomáhá.

## Nové ve 0.13
- backtest počítá pět variant nad stejnými předzápasovými daty: **Model 1**, jen rychlejší forma, jen silnější síla soupeře, jen B2B/odpočinek/cestování a Full kandidát;
- u každého trhu se ukazuje úspěšnost, Brier score a Brier zisk proti Modelu 1;
- nejlepší varianta daného trhu je zvýrazněná;
- hranice střel/gólů dál porovnávají Model 1, Full kandidáta a jednoduchou baseline;
- přidána jednoduchá **chronologická out-of-sample kalibrace**: starších 60 % predikcí slouží k nastavení affine kalibrátoru, novějších 40 % je čistý test;
- kalibrace mění jen pravděpodobnostní jistotu, nikoli samotný výběr;
- živá záložka Analýza dál používá Model 1.

## Proč ablace
Výsledek 0.12 ukázal, že Full Model 2 nebyl stabilně lepší. Ablace oddělí vliv recency, síly soupeře a kontextu cestování/odpočinku. Faktor, který nezlepší Brier score v league-wide testu, se nemá automaticky přidávat do hlavního modelu.

## Kalibrace
Kalibrace v 0.13 není trénovaná na stejných výsledcích, na kterých je následně hodnocena. Backtestové predikce se chronologicky rozdělí: starší část nastaví jednoduchý kalibrátor a novější část změří raw vs. kalibrovaný Brier. Je to pořád validační krok, ne finální produkční kalibrátor.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render používá `python server.py`.

## Omezení
Backtest stále nepoužívá historické bookmakerové kurzy, proto z něj nelze přímo odvozovat historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou číselnou korekcí hlavního modelu.
