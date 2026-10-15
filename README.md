# NHL Team Shots Analyzer 0.9.1

Verze 0.9 navazuje na dashboard 0.8 a přidává automatické načítání **oficiálních denních projektovaných sestav z NHL.com**.

## Nové ve 0.9
- server načítá stabilní stránku NHL.com „Projected lineups, starting goalies for today“;
- pro vybrané dva týmy zobrazuje 4 útočné formace, 3 obranné páry, brankáře, hráče mimo sestavu a zranění, pokud jsou ve zdroji uvedené;
- zobrazuje datum zdroje a nepoužije dnešní článek pro zápas s jiným datem;
- při nedostupnosti NHL.com analýza dál funguje bez sestav;
- načtená sestava je viditelně označena jako **projektovaná** a **nezapočítaná do numerického modelu**.

## Proč sestavy zatím nemění pravděpodobnost
Automaticky přidat například „-2 střely za absenci hvězdy“ by vytvořilo falešnou přesnost. Dalším krokem má být historický backtest, který změří dopad absence klíčových hráčů, změny role, brankáře, odpočinku a cestování. Teprve potom je vhodné přidat datově podložené korekce.

## Nasazení
Nahraj `index.html`, `server.py` a `README.md` do kořene stejného GitHub repozitáře. Render používá `python server.py`.

## Omezení
NHL.com lineup projections jsou redakční denní projekce, nikoli oficiální potvrzená soupiska pro každý budoucí zápas. Struktura stránky NHL.com se může změnit; při chybě parseru aplikace sestavy raději nezobrazí, než aby si je domýšlela. Základní model zatím není historicky kalibrovaný ani validovaný a nejde o sázkové doporučení.


## Oprava 0.9.1
- automatické vyhodnocení tipů umí spárovat zápas i tehdy, když české datum tiketu je o den jiné než místní NHL `gameDate`;
- uložené tipy lze dohledat podle dvojice týmů v okně ±1 den;
- nové tipy převzaté z analýzy se ukládají s konkrétním NHL game ID a NHL datem.
