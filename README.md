# NHL Team Shots Analyzer 0.14

Verze 0.14 přidává **Model 3 Lab**. Cílem není přidat další ručně zvolené koeficienty, ale naučit malé konzervativní váhy na starší části historie a ověřit je na novějších zápasech, které ladění vah nevidělo.

## Co je nové
- League-wide backtest lze spustit až na **300 posledních zápasech**.
- Data se chronologicky rozdělí na **60 % train / 40 % holdout**.
- Model 3 začíná u Modelu 1 a učí tři malé váhy: rychlejší forma, síla soupeře a B2B/odpočinek/cestování.
- Grid search testuje 80 konzervativních kombinací vah pouze na train části a používá malou regularizační penalizaci proti zbytečně velkým korekcím.
- Hlavní tabulka Model 1 vs. Model 3 je počítána **jen na novějším holdoutu**.
- Pro týmové střely, celkové střely a góly se na holdoutu zobrazují i jednotlivé hranice, baseline, Brier score a Brier Skill.
- Pravděpodobnostní kalibrátor se učí pouze na train části a jeho Brier zisk se měří na holdoutu.
- Historie předzápasových dat se nově cachuje, aby větší league-wide běhy neopakovaly stejnou práci pro každou variantu modelu.

## Jak výsledek číst
Nejdůležitější je sekce **Model 3 · naučené váhy a nezávislý holdout**. Kladný Brier zisk znamená, že Model 3 má na neviděných novějších zápasech přesnější pravděpodobnosti než Model 1. Vyšší úspěšnost bez lepšího Brier score sama o sobě nestačí.

Váhy z jednoho běhu se zatím nepřenášejí do živé Analýzy. Model 1 zůstává hlavním modelem, dokud Model 3 neukáže stabilní zlepšení na větším league-wide vzorku a ideálně více časových obdobích.

## Doporučený test
Začni `Celá NHL` + `100` zápasů. Pokud vše funguje, spusť `200` a následně `300`. První velký běh může podle cache a NHL API trvat několik minut.

## Omezení
Backtest stále nepoužívá historické bookmakerové kurzy, takže z něj nelze přímo odvozovat historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí živého modelu. Model není považován za finálně validovaný a nejde o sázkové doporučení.
