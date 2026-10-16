# NHL Team Shots Analyzer 0.22

Verze 0.22 rozšiřuje validaci modelu z krátkých vzorků na větší, vícesezonní test.

## Hlavní změny

- **Až 4 kompletní sezóny:** 2022/23 + 2023/24 + 2024/25 + 2025/26.
- **Až 2000 league-wide zápasů** v jednom backtestu.
- V každé sezoně se zápasy vybírají **rovnoměrně napříč celým ročníkem**, ne jen z jeho konce.
- Pro 1200+ zápasů používá Model 10 větší **8-fold forward validaci** a menší warm-up, aby testovací část pokryla více sezon.
- Nový **Model 11 Confidence** měří, co se stane, když vezmeme jen predikce s modelovou jistotou:
  - ≥ 60 %
  - ≥ 65 %
  - ≥ 70 %
  - ≥ 75 %
  - ≥ 80 %
- U každého pásma se ukazuje:
  - počet případů N,
  - pokrytí,
  - průměrná modelová jistota,
  - skutečná úspěšnost,
  - Brier score,
  - nejhorší sezónní úspěšnost, pokud má sezóna v pásmu alespoň 20 případů,
  - velikost vzorku (OK / malý / velmi malý).
- Vedle kandidáta je pro stejný práh zobrazen také **Model 1**.

## Ochrana proti falešně vysoké úspěšnosti

Pásma 60/65/70/75/80 % jsou pevně daná před testem. Aplikace nevybírá zpětně nejlepší hranici podle výsledků. Confidence tabulka se počítá pouze na chronologické forward části.

Vysoká úspěšnost na několika málo případech není označena za úspěch. Vždy je nutné sledovat zároveň N, pokrytí, Brier a stabilitu napříč sezonami.

## Velké backtesty

Backtest nyní běží jako serverová úloha a prohlížeč její stav průběžně kontroluje. To je důležité pro 1500–2000 zápasů, protože první běh musí načíst velké množství historických NHL boxscore a play-by-play dat. Další běhy ve stejném serverovém procesu mohou využít cache.

## Doporučený test

**Celá NHL → 2022/23 + 2023/24 + 2024/25 + 2025/26 → 2000 zápasů**

Nejdůležitější části výsledku:
1. Model 11 Confidence
2. Model 10 Locked
3. Model 9 Event Data jako kontrola PBP profilů

## Důležité omezení

Živá Analýza stále používá Model 1. Locked modely jsou zatím pouze shadow porovnání.

Backtest neobsahuje historické bookmakerové kurzy, takže sám o sobě neprokazuje historické ROI ani EV. Geometrický slot proxy není oficiální xG model.
