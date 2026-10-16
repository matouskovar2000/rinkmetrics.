# NHL Team Shots Analyzer 0.21

Verze 0.21 přidává **Model 10 Locked**. Po výsledcích verze 0.20 už u trhů, kde jsme našli stabilní signál, nenecháváme model v každém časovém bloku přepínat mezi více kandidáty. Kandidát se zamkne před testem a pak se opakovaně ověřuje v navazujících walk-forward obdobích.

## Zamknuté kandidáty
- **Více střel:** pevný profil založený na 5v5 a score-tied shot-attempt share z NHL play-by-play. Tento směr byl v 0.20 stabilní ve 4 z 5 forward období.
- **Góly ±:** pevný blend historické efektivity střel/inkasování a PBP slot + power-play profilu. Tyto gólové signály byly opakovaně stabilní v předchozích walk-forward testech.

## Nové experimenty pro zbývající trhy
- **Střely týmu ±:** neutral-state 5v5/tied attempts, soupeřovo potlačení pokusů, SOG-per-attempt a malý PP tlak.
- **Celkové střely ±:** neutral-state attempt pace, tied pace, převod pokusů na střely na branku a předzápasová variabilita.
- **Vítěz 1/X/2:** possession profil kombinovaný se stabilnějším gólovým profilem.

Každý z těchto tří trhů má v Modelu 10 jen jeden nový experiment. Tím omezujeme riziko přeučení z výběru mezi příliš mnoha kandidáty.

## Shadow model v živé Analýze
Živá stránka stále používá **Model 1** pro hlavní pravděpodobnosti, férové kurzy a EV. Nově ale zobrazí kartu **Experimentální model 0.21 · shadow**, kde vedle Modelu 1 ukáže:
- zamknutou pravděpodobnost trhu „více střel“ včetně remízy,
- očekávané střely Modelu 1 vs. locked kandidáta,
- očekávané góly Modelu 1 vs. locked gólového kandidáta,
- orientační pravděpodobnost over 5,5 gólů obou modelů.

Shadow výsledek nic automaticky nepřepisuje a nepoužívá se k výpočtu EV.

## Validace
Model 10 používá stejný walk-forward princip: u 600 zápasů starší polovina tvoří počáteční historii a novější polovina se rozdělí do 5 po sobě jdoucích forward foldů. Kandidát je označen **STABILNÍ**, pokud má kladný průměrný Brier zisk, zlepší alespoň 60 % forward období a nemá extrémně špatný nejhorší fold.

## Doporučený test
`Celá NHL` → `2024/25 + 2025/26` → `600 zápasů`.

## Omezení
Backtest nepoužívá historické bookmakerové kurzy, takže neprokazuje historické EV ani ROI. Slot je pouze geometrický proxy z veřejných souřadnic NHL play-by-play, nikoli oficiální xG. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí modelu. Výsledky jsou analytická validace, ne sázkové doporučení.
