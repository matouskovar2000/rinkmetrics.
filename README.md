# NHL Team Shots Analyzer 0.15

Verze 0.15 přidává **Model 4 – samostatné faktory pro každý trh**. Už nenutíme více střel, týmové střely, celkové střely, góly a 1/X/2 používat stejné váhy.

## Co je nové
- data se dál chronologicky dělí na **60 % train / 40 % holdout**;
- každý z pěti trhů si na train části samostatně vybírá váhu **rychlé formy**, **síly soupeře** a **B2B/odpočinku/cestování**;
- grid search je konzervativní: součet faktorových vah nesmí překročit 1,0 a větší odchylky od Modelu 1 dostávají malou regularizační penalizaci;
- tabulka Model 4 ukazuje pro každý trh jiné zvolené váhy, train Brier zisk a hlavně **holdout Brier zisk**;
- týmové střely, celkové střely a góly mají na holdoutu vlastní tabulky hranic s baseline a Brier Skill;
- kalibrace pravděpodobností se učí samostatně pro každý trh pouze na train části a testuje až na holdoutu;
- starší ablační a Full-model diagnostika zůstává v aplikaci jako kontrola, ale hlavním rozhodovacím testem je Model 4.

## Jak výsledek číst
Nejdůležitější je **holdout Brier zisk**. Kladná hodnota znamená, že tržně specifický Model 4 na novějších, při ladění neviděných zápasech překonal Model 1. Nulová váha u faktoru znamená, že se na train datech nevyplatilo ho pro daný trh přidat.

Vyšší úspěšnost bez lepšího Brier score nestačí. Stejně tak vysoká úspěšnost na extrémní hranici může být jen důsledkem jednoduché baseline (například téměř vždy under).

## Živá Analýza
Živá záložka **Analýza stále používá Model 1**. Model 4 je zatím pouze validační laboratoř. Do živých pravděpodobností ho má smysl zapojit až tehdy, když se jeho přínos opakuje na větších league-wide vzorcích a více obdobích.

## Omezení
Backtest nepoužívá historické bookmakerové kurzy, takže z něj nelze přímo odvozovat historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou automatickou číselnou korekcí živého modelu. Model není finálně validovaný a nejde o sázkové doporučení.
