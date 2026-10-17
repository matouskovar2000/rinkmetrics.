# NHL Team Shots Analyzer 0.24

Verze 0.24 přidává poslední nezávislou kontrolu před případným povýšením locked modelu pro trh **Více střel**. Kandidát `LOCK 5v5+tied` byl zamknutý už před tímto testem; sezóna **2021/22** se proto používá jako jednorázový lockbox a na jejích výsledcích se nic nepřelaďuje.

## Model 12 Lockbox
- nový samostatný test pro **2021/22**;
- používá všechny dostupné league-wide zápasy sezóny, u kterých existuje dost předzápasové historie;
- porovnává Model 1 proti předem zamknutým kandidátům pro **Více střel** a **Góly**;
- sezónu rozděluje do 4–8 chronologických bloků pouze kvůli stabilitě, nikoli kvůli trénování;
- `PROŠEL` vyžaduje kladný celkový Brier zisk alespoň `0.0005`, zlepšení nejméně v 60 % bloků a žádný extrémně špatný blok.

## 95% intervaly spolehlivosti
Model 11 Confidence i nový lockbox ukazují **95% Wilsonův interval** skutečné úspěšnosti. Díky tomu je vidět rozdíl mezi např. 80 % na stovkách predikcí a 80 % na několika desítkách případů.

U lockbox confidence tabulky se navíc Model 1 počítá na **stejných zápasech**, které vybral locked kandidát daným prahem jistoty. Tím se neporovnávají dva rozdílné vzorky.

## Doporučený test 0.24
`Celá NHL` → `2021/22 · LOCKBOX` → `1400 zápasů`.

Server použije všechny dostupné regular-season zápasy sezóny; skutečný počet vyhodnocených zápasů může být nižší kvůli chybějící předzápasové historii, zejména u začátku sezóny a nového týmu Seattle.

## Výkon
Deep režim, kompaktní box/PBP cache, retry NHL API a průběhové procento z verze 0.23 zůstávají zachované, takže i lockbox přes celou sezonu běží stejnou úspornou cestou.

## Důležité
Živá Analýza stále používá Model 1. Locked kandidáti jsou pouze shadow/validační modely a nemění hlavní EV. Historické bookmakerové kurzy nejsou součástí backtestu, takže ani úspěšný lockbox sám o sobě neprokazuje historickou sázkovou ziskovost.
