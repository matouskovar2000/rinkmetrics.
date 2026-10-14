# NHL Team Shots Analyzer 0.5

Statická mobilní webová aplikace s Python proxy serverem pro veřejné NHL Web API. Spuštění: `python server.py`, otevřít `http://localhost:8000`. Render start command: `python server.py`.

## Nové ve verzi 0.5
- Černý, červený, bílý a modrý design; pravděpodobnosti bíle, záporné EV červeně.
- Záložka **Moje tipy** s místním uložením, exportem/importem JSON a automatickým vyhodnocením dokončených zápasů z NHL API pro střely a celkové góly. Pro výsledek 1X2 po 60 minutách zatím vyhodnocení záměrně čeká na přesná data třetin.
- Orientační vzdálenost mezi arénami a informace o posledním zápase. Bez výběru konkrétního budoucího utkání nelze korektně stanovit skutečný cestovní plán.
- Sestavy, zranění a potvrzení brankáři jsou výslovně označeny jako nedostupné; žádná neověřená data se nevymýšlejí.
- Zachováno všech pět modelových trhů, ruční kurzy a předběžný model formy.

## Důležité limity
- Deník se ukládá **pouze v místním úložišti prohlížeče**. Použij export zálohy před změnou zařízení nebo vymazáním dat.
- Vyhodnocení se provede po kliknutí na „Aktualizovat výsledky NHL“, nikoli na pozadí při zavřené aplikaci.
- Datum zápasu musí odpovídat NHL gameDate a týmy musí být správně přiřazené doma/venku.
- Poissonovy modely nejsou kalibrované ani backtestované. Únava, cestování, sestavy a absence **zatím neupravují pravděpodobnosti**.
- Aplikace není napojena na Tipsport, nesbírá ani neodesílá sázky.
