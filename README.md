# NHL Team Shots Analyzer 0.11

Verze 0.11 rozšiřuje historický backtest z jednoho týmu na **league-wide test celé NHL** a přidává porovnání více hranic.

## Nové ve 0.11
- v Backtestu lze zvolit **Celá NHL (league-wide)**;
- league-wide režim deduplikuje zápasy napříč klubovými rozpisy a umí testovat až 100 posledních dokončených zápasů základní části;
- historická data se přednačítají a cachují, aby se stejné boxscore nemuselo stahovat znovu pro každý model;
- týmové střely se porovnávají na hranicích 23,5 / 25,5 / 27,5 / 29,5 / 31,5;
- celkové střely na 51,5 / 53,5 / 55,5 / 56,5 / 57,5 / 59,5 / 61,5;
- góly na 4,5 / 5,5 / 6,5 / 7,5;
- pro každou hranici se ukazuje N, úspěšnost, poměr modelových Over/Under výběrů, skutečný podíl overů a Brier score;
- základní tabulka trhů stále používá referenční hranice 27,5 / 56,5 / 5,5, aby šlo porovnávat výsledky s verzí 0.10.

## Jak backtest číst
Každá predikce používá pouze zápasy odehrané **před** testovaným utkáním. League-wide výsledek je užitečnější než vzorek jednoho týmu, ale stále nejde o historický test proti skutečným kurzům sázkové kanceláře. Proto z něj nelze přímo odvozovat historické EV ani ROI.

Vysoká úspěšnost na konkrétní hranici může být způsobena tím, že hranice byla pro daný vzorek jednoduše nízká nebo vysoká. Proto 0.11 vedle úspěšnosti ukazuje také, kolikrát model volil over/under a jak často byl skutečný výsledek over.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render používá `python server.py`.

## Omezení
První league-wide běh může trvat déle, protože se načítá více historických NHL dat. Následující běhy jsou díky serverové cache rychlejší. Backtest nepoužívá historické bookmakerové kurzy, sestavy ani zranění jako číselné korekce modelu. Model stále není považován za historicky validovaný a nejde o sázkové doporučení.
