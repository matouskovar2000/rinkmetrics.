# NHL Team Shots Analyzer 0.17

Verze 0.17 přidává **Model 6 Profiles**. Místo dalšího otáčení stejných vah testuje nové předzápasové informace a nechává každý trh vybrat jiný typ profilu.

## Nové profilové signály
- **Tempo střel**: kolik střel tým ve svých zápasech vytváří a povoluje dohromady; používá se hlavně jako kandidát pro celkové a týmové střely.
- **Shot-share profil**: podíl střel pro / proti; kandidát hlavně pro „více střel“ a týmové střely.
- **Efektivita střel / inkasování**: předzápasová shooting% a gólová efektivita soupeře se shrinkage k 10% prioru; kandidát pro góly a 1/X/2.
- **Kombinovaný profil**: tempo + shot share pro očekávané střely a efektivita pro očekávané góly.

## Ochrana proti přeučení
Model 6 používá stejné bezpečné chronologické dělení jako 0.16:
- starších **50 % train** vybírá nejlepší profil pro každý trh;
- prostředních **25 % validation** rozhoduje, zda profil smí nahradit Model 1 a zda smí být použita kalibrace;
- nejnovějších **25 % final test** je nedotčených a používá se až pro konečné hodnocení.

Nový profil musí na validation zlepšit Brier score alespoň o 0,0005. Kalibrace má stejný minimální požadavek. Pokud podmínku nesplní, trh automaticky zůstane na Modelu 1.

## Doporučený test
Použij `Celá NHL` + `2024/25 + 2025/26` + nejdřív **300 zápasů**. Pokud vše funguje, spusť **600 zápasů**, aby měl finální test 150 zápasů.

## Důležité
Živá záložka Analýza dál používá Model 1. Model 6 je zatím výzkumná vrstva v Backtestu. Historické bookmakerové kurzy nejsou součástí testu, takže z výsledků nelze přímo odvozovat historické EV ani ROI. Sestavy, zranění a potvrzený brankář zatím nejsou číselnou korekcí živého modelu.
