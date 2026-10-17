# NHL Team Shots Analyzer 0.23

Verze 0.23 je oprava dlouhého multi-season backtestu. Cíl se nemění: tvrdě ověřit Model 10 Locked a Model 11 Confidence na 4 sezonách a až 2000 zápasech, ale tak, aby test na Renderu skutečně doběhl.

## Co je opravené
- **Deep režim pro 800+ zápasů:** počítá pouze Model 1, Full a pět předem deklarovaných kandidátů potřebných pro Model 10/11. Staré vývojové Modely 4–9 se u velkého testu záměrně nepřepočítávají.
- **Kompaktní boxscore cache:** server už nedrží tisíce kompletních historických boxscore dokumentů v RAM. Ukládá jen SOG, skóre a nezbytné mapování hráč→tým.
- **Kompaktní PBP cache:** po zpracování se syrový play-by-play zahodí a zůstane jen agregovaný event profil.
- **Omezená history cache:** tisíce team/date oken už nemohou nekontrolovaně růst v paměti.
- **Retry NHL API:** dočasné 429/5xx/timeout chyby dostanou několik pokusů s backoffem místo pádu celého 2000zápasového testu.
- **Izolované chyby historických PBP:** chybějící starý event feed už nezastaví celý test; model použije bezpečný fallback.
- **Viditelný průběh:** stav backtestu ukazuje fázi, procenta a počet zpracovaných položek/zápasů.

## Doporučený tvrdý test
`Celá NHL` → `2022/23 + 2023/24 + 2024/25 + 2025/26` → `2000 zápasů`.

U tohoto běhu je rozhodující **Model 10 Locked** a **Model 11 Confidence**. Confidence pásma 60/65/70/75/80 % jsou předem daná a počítají se pouze na chronologické forward části. U každého pásma sleduj N, pokrytí, Brier, skutečnou úspěšnost a nejhorší sezonu při dostatečném vzorku.

## Důležité
Živá Analýza stále používá Model 1. Locked kandidáti jsou pouze shadow/validační modely a nemění hlavní EV. Historické bookmakerové kurzy nejsou součástí backtestu, takže výsledek neprokazuje historickou ziskovost.
