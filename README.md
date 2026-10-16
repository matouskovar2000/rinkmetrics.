# NHL Team Shots Analyzer 0.18

Verze 0.18 přidává **Model 7 Walk-forward**, který testuje stabilitu modelu v čase. Cílem není najít jednu šťastnou validation periodu, ale ověřit, zda nový profil pomáhá opakovaně v několika po sobě jdoucích obdobích.

## Co je nové
- Multi-season league-wide backtest až na **600 zápasech**.
- U 600 zápasů se starší polovina použije jako počáteční historie a novější polovina se rozdělí do **5 chronologických forward foldů**.
- V každém foldu se zdroj modelu vybírá pouze podle zápasů, které už byly v té chvíli v minulosti.
- Testují se samostatně profily z 0.17: **tempo střel**, **shot share**, **efektivita střel/inkasování** a **kombinovaný profil**.
- U každého trhu se ukazuje, v kolika forward obdobích profil zlepšil Brier score, jeho průměrný zisk a nejhorší období.
- Profil je označen jako **STABILNÍ** pouze tehdy, když má kladný průměrný Brier zisk a pomáhá alespoň v 60 % forward foldů.
- Kalibrace se v každém foldu učí pouze na starší historii a její přínos se reportuje odděleně.

## Jak výsledek číst
Nejdůležitější jsou dvě nové sekce:

1. **Model 7 Walk-forward · stabilita v čase** – ukazuje reálný výsledek train-only selektoru v navazujících časových blocích.
2. **Model 7 Walk-forward · které profily jsou stabilní** – ukazuje každý profil samostatně a kolikrát skutečně pomohl v budoucím období.

Kladný Brier zisk znamená lepší pravděpodobnosti než Model 1. Samotná vyšší úspěšnost nestačí.

## Doporučený test
`Celá NHL` → `2024/25 + 2025/26` → `600 zápasů`.

## Omezení
Živá Analýza stále používá Model 1. Model 7 je validační laboratoř a nic automaticky nepřepíná do živého modelu. Historické bookmakerové kurzy nejsou součástí backtestu, takže z výsledků nelze přímo počítat historické EV nebo ROI. Sestavy, zranění a potvrzení brankáře zatím nejsou číselnou korekcí modelu.
