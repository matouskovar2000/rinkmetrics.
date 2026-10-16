# NHL Team Shots Analyzer 0.20

Verze 0.20 přidává **Model 9 Event Data**. Po předchozích walk-forward testech už nepřidáváme další kombinace stejných boxscore průměrů, ale zpracováváme detailní historické NHL play-by-play události. Každý nový faktor je stále používán pouze z utkání, která byla odehrána před analyzovaným zápasem.

## Nová play-by-play data
- všechny střelecké pokusy: gól, střela na branku, minutá střela a blokovaný pokus;
- 5v5 shot-attempt share;
- shot-attempt share při vyrovnaném skóre, aby model méně reagoval na score effects;
- power-play shot-attempt volume;
- jednoduchý `slot attempt` proxy z x/y souřadnic střelecké události;
- poměr střel na branku ku všem pokusům;
- dlouhodobé a recent tempo všech pokusů.

`Slot attempt` je pouze geometrická heuristika podle vzdálenosti a středu kluziště. Není to oficiální NHL xG ani plnohodnotný expected-goals model.

## Model 9 podle trhu
- **Více střel:** 5v5 + score-tied attempt share proti původnímu shot-edge specialistovi.
- **Střely týmu ±:** offense-vs-defense matchup v pokusech, shot suppression a historický převod pokusů na SOG.
- **Celkové střely ±:** dlouhodobé a recent tempo všech pokusů převedené zpět na očekávané SOG.
- **Góly ±:** dosud stabilnější efektivita střel doplněná o slot a PP-attempt tlak.
- **Vítěz 1/X/2:** possession profil z 5v5/tied attempt share, slot share a gólového rozdílu.

## Walk-forward validace
Model 9 používá stejný přísný postup jako 0.18–0.19. U 600 zápasů se starší polovina používá jako počáteční historie a novější polovina se rozdělí do 5 navazujících forward bloků. Každý blok vidí pouze minulost.

Profil je označen jako **STABILNÍ**, pokud:
- zlepší alespoň 60 % forward období;
- má kladný průměrný Brier zisk alespoň 0.0005;
- nemá extrémně špatný nejhorší fold.

Kalibrace se hodnotí zvlášť a nesmí zakrýt slabý raw model.

## Výkon a cache
Backtest 0.20 musí načíst play-by-play i pro historická utkání použitá v předzápasových 30zápasových oknech. PBP se proto přednačítá paralelně, okamžitě se převádí na kompaktní event summary a celý raw dokument se nedrží dlouhodobě v paměti.

## Doporučený test
`Celá NHL` → `2024/25 + 2025/26` → `600 zápasů`.

## Omezení
Živá záložka **Analýza stále používá Model 1**. Model 9 se do ní nepřenáší automaticky. Historické bookmakerové kurzy nejsou součástí backtestu, takže výsledky neprokazují historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí modelu.
