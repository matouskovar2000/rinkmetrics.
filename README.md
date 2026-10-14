# NHL Team Shots Analyzer 0.6

Tři soubory nahrajte do kořene stejného GitHub repozitáře: `index.html`, `server.py`, `README.md`. Render používá příkaz `python server.py`.

## Nové funkce oproti 0.5
- Výběr skutečného zápasu z rozpisu NHL; automaticky se nastaví domácí tým.
- Odpočinek a back-to-back podle data vybraného zápasu a posledního odehraného utkání každého týmu.
- Vzdálenost mezi místy posledního a příštího zápasu vzdušnou čarou. Není to reálná trasa ani přesný cestovní čas.
- Odkaz na aktuální projektované sestavy NHL.com (informace nejsou automaticky parsované ani zahrnuté do modelu).
- V deníku Moje tipy vyhodnocení trhu 1/X/2 a dvojtipů po 60 minutách z play-by-play prvních tří třetin.
- Automatická kontrola čekajících tipů při otevření záložky Moje tipy. Ruční kontrola a export/import JSON zůstávají.

## Omezení
- Sestavy, absence, brankáři a cestování **zatím neupravují** pravděpodobnosti. Bez spolehlivých historických dat by přidání arbitrárních koeficientů budilo falešnou přesnost.
- U zápasů na neutrální půdě může být vzdálenost nepřesná: mapování vychází z domácích arén týmů.
- Data deníku se ukládají pouze do localStorage v daném prohlížeči. Pravidelně exportujte zálohu.
- Model nebyl historicky kalibrován ani validován; kurzy se zadávají ručně.
