# NHL Team Shots Analyzer 0.16

Verze 0.16 přidává **Model 5 Select** a přísnější validaci. Cílem je zabránit tomu, aby se nový faktor nebo kalibrace dostaly do modelu jen proto, že náhodou vyšly dobře na stejném holdoutu, podle kterého jsme je vybrali.

## Co je nové
- Chronologické dělení dat na **50 % train / 25 % validation / 25 % finální test**.
- Train část ladí samostatné váhy rychlé formy, síly soupeře a B2B/odpočinku/cestování pro každý z pěti trhů.
- Validation část rozhodne, zda daný trh smí použít Model 4, nebo se má vrátit k Modelu 1.
- Kalibrace se také povolí jen tehdy, když na validation části zlepší Brier score alespoň o minimální práh.
- Nejnovějších 25 % zápasů zůstává **zcela nedotčený finální test**. Ten se nepoužívá pro výběr vah, modelu ani kalibrace.
- Nový režim **2024/25 + 2025/26** bere vyvážený počet zápasů z obou sezon. Při 600 zápasech jde přibližně o 300 + 300, takže novější sezóna nevytlačí starší.
- League-wide multi-season backtest lze spustit až na **600 zápasech**.
- Výsledky ukazují pro každý trh vybraný model, povolenou/zakázanou kalibraci a skutečný Brier zisk na finálním testu.

## Jak Model 5 číst
Nejdůležitější je sekce **Model 5 Select · nedotčený finální test**. Kladný Brier zisk znamená, že výběrový model měl na datech, která nikdy neviděl při ladění ani výběru, přesnější pravděpodobnosti než Model 1.

Pokud validation nepotvrdí přínos Modelu 4, trh automaticky zůstane na Modelu 1. Stejně tak se nepoužije kalibrace, pokud na validation části nepomůže. To je záměrně konzervativní.

## Doporučený test
Zvol `Celá NHL`, období `2024/25 + 2025/26` a nejdříve `300` zápasů. Pokud vše proběhne, spusť `600`. U 600 zápasů je train/validation/final přibližně 300/150/150.

## Omezení
Živá záložka Analýza zatím stále používá Model 1. Model 5 je validační laboratoř; do živého výpočtu se nepřenese, dokud neukáže stabilní přínos na finálním testu a napříč časovými obdobími. Backtest nepoužívá historické bookmakerové kurzy, takže nepočítá skutečné historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí modelu. Nejde o sázkové doporučení.
