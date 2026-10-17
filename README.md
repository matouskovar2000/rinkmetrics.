# NHL Team Shots Analyzer 0.25

Verze 0.25 povyšuje `LOCK 5v5+tied` z experimentu do **živé Analýzy pro trh Více střel**. Je to první model v aplikaci, který prošel opakovaným walk-forward testem i odděleným lockboxem 2021/22.

## Produkční Více střel
- hlavní zdroj je `LOCK 5v5+tied`, postavený na 5v5 a score-tied shot-attempt share z NHL play-by-play;
- používá pevnou 30zápasovou předzápasovou historii stejně jako ve validaci;
- pokud není alespoň 70 % potřebné PBP historie, aplikace automaticky použije **Model 1 fallback**;
- ruční scénář střel se může počítat nad LOCK očekáváním, ale je výslovně označen jako mimo historickou validaci.

## Ověřená síla signálu
Živá Analýza u Více střel ukazuje, do kterého confidence pásma aktivní LOCK predikce spadá. Jako kontext se zobrazí skutečná úspěšnost, počet případů a 95% Wilsonův interval z nezávislého lockboxu 2021/22:

- 60 %+ confidence: 71,6 % na 599 případech;
- 65 %+: 75,6 % na 386;
- 70 %+: 78,3 % na 212;
- 75 %+: 78,6 % na 98;
- 80 %+: 80,5 % na 41 případech.

Vyšší pásma mají menší vzorek, proto jsou v aplikaci označena opatrně.

## Pravděpodobnost a EV
Samotná produkční pravděpodobnost zůstává **raw LOCK pravděpodobností**. Po lockboxu ji dodatečně nepřemapováváme podle stejné sezóny, protože taková post-hoc kalibrace by potřebovala nový nezávislý test. Ověřená confidence pásma proto slouží jako transparentní kalibrační kontext, ne jako skryté přelaďování modelu.

Férový kurz a modelové EV pro trh Více střel nyní používají produkční LOCK pravděpodobnost, pokud je LOCK dostupný. Historické bookmakerové kurzy ale v backtestu stále nejsou, takže výsledky neprokazují historické ROI ani ziskovost.

## Ostatní trhy
Týmové střely, celkové střely, góly a 1/X/2 zůstávají v živé Analýze na Modelu 1. Gólový locked profil se dál zobrazuje pouze jako shadow, protože lockbox zatím nesplnil podmínku pro povýšení.

## Validace
Model 12 Lockbox 2021/22 zůstává v Backtestu jako auditní kontrola. V posledním běhu bylo vyhodnoceno 1302 zápasů; `LOCK 5v5+tied` zlepšil Brier přibližně z 0,226 na 0,225 a měl kladný výsledek v 7 z 8 chronologických bloků.
