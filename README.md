# NHL Team Shots Analyzer 0.19

Verze 0.19 přidává **Model 8 Specialists**. Po výsledcích walk-forward validace už se nesnažíme vylepšit všechny trhy jedním univerzálním faktorem. Čtyři problematické trhy dostávají vlastní předzápasový model a každý se ověřuje v několika navazujících časových blocích.

## Nové specialisty
- **Více střel:** `Shot edge specialist` kombinuje shot share a dlouhodobý rozdíl vytvořených/povolených střel. Celkový objem střel drží blízko Modelu 1 a mění hlavně rozdělení mezi týmy.
- **Střely týmu ±:** `Útok vs. potlačení střel` modeluje konkrétní útok proti tomu, kolik střel soupeř běžně povoluje. Menší část bere z posledních 10 zápasů, ale většina zůstává na 30zápasovém základu.
- **Celkové střely ±:** `Tempo + variabilita` kombinuje dlouhodobé i recent tempo obou týmů a přidává předzápasový odhad variability celkového počtu střel.
- **Vítěz 1/X/2:** `Výkonnostní 1/X/2 profil` používá gólový rozdíl, shot edge a dlouhodobou výsledkovou sílu k úpravě očekávaného rozdílu gólů.
- **Góly ±:** zůstávají jako kontrolní trh s `Efektivitou střel / inkasování` a `Kombinovaným profilem`, protože právě tam Model 7 našel první stabilní signál.

## Validace
Model 8 používá stejný walk-forward princip jako 0.18. U 600 zápasů se starší polovina použije jako počáteční historie a novější polovina se rozdělí do 5 po sobě jdoucích forward foldů. V každém foldu se zdroj modelu i kalibrace učí pouze ze starších zápasů.

Specialista je označen jako **STABILNÍ**, jen pokud má kladný průměrný Brier zisk, zlepší alespoň 60 % forward foldů a nemá extrémně špatný nejhorší fold. Živá Analýza stále používá Model 1; žádný experimentální specialista se do ní nepřenáší automaticky.

## Doporučený test
`Celá NHL` → `2024/25 + 2025/26` → `600 zápasů`.

## Omezení
Backtest nepoužívá historické bookmakerové kurzy, takže neprokazuje historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí modelu. Výsledky jsou analytická validace modelu, ne sázkové doporučení.
