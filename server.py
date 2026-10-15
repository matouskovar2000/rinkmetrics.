"""NHL Team Shots Analyzer 0.10 — static site, NHL API proxy and official NHL.com lineup reader."""
import json
import os
import re
import time
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
ALLOWED = re.compile(r'^/(?:club-schedule-season/[A-Z]{3}/\d{8}|gamecenter/\d+/(?:boxscore|play-by-play))$')
LINEUP_URL = 'https://www.nhl.com/news/nhl-lineup-projections-2026-27-season'
TEAM_NICK = {
    'ANA':'DUCKS','BOS':'BRUINS','BUF':'SABRES','CGY':'FLAMES','CAR':'HURRICANES','CHI':'BLACKHAWKS',
    'COL':'AVALANCHE','CBJ':'BLUE JACKETS','DAL':'STARS','DET':'RED WINGS','EDM':'OILERS','FLA':'PANTHERS',
    'LAK':'KINGS','MIN':'WILD','MTL':'CANADIENS','NSH':'PREDATORS','NJD':'DEVILS','NYI':'ISLANDERS',
    'NYR':'RANGERS','OTT':'SENATORS','PHI':'FLYERS','PIT':'PENGUINS','SEA':'KRAKEN','SJS':'SHARKS',
    'STL':'BLUES','TBL':'LIGHTNING','TOR':'MAPLE LEAFS','UTA':'MAMMOTH','VAN':'CANUCKS','VGK':'GOLDEN KNIGHTS',
    'WSH':'CAPITALS','WPG':'JETS'
}
_lineup_cache = {'at': 0.0, 'data': None}
_nhl_cache = {}
_nhl_cache_lock = threading.Lock()


class TextExtractor(HTMLParser):
    BLOCK = {'p','div','section','article','main','header','footer','li','h1','h2','h3','h4','br','tr'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript'):
            self.skip += 1
            return
        if not self.skip and tag in self.BLOCK:
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript') and self.skip:
            self.skip -= 1
            return
        if not self.skip and tag in self.BLOCK:
            self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
    def text(self):
        raw = unescape(''.join(self.parts)).replace('\xa0', ' ')
        lines = []
        for line in raw.splitlines():
            line = re.sub(r'\s+', ' ', line).strip()
            if line:
                lines.append(line)
        return '\n'.join(lines)

def _article_text(html_bytes):
    parser = TextExtractor()
    parser.feed(html_bytes.decode('utf-8', 'replace'))
    return parser.text()

def _source_date(text):
    months = 'January|February|March|April|May|June|July|August|September|October|November|December'
    m = re.search(rf'\b({months})\s+(\d{{1,2}}),\s+(20\d{{2}})\b', text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), '%B %d, %Y').date().isoformat()
    except ValueError:
        return None

def _split_csv(value):
    value = value.strip()
    if not value or value.lower() == 'none':
        return []
    return [x.strip(' .') for x in re.split(r',\s*', value) if x.strip(' .')]

def _parse_team(text, code):
    nick = TEAM_NICK[code]
    pat = re.compile(rf'(?im)^\s*{re.escape(nick)}\s+projected lineup\s*$')
    m = pat.search(text)
    if not m:
        return {'found': False, 'forwards': [], 'defense': [], 'goalies': [], 'scratched': [], 'injured': []}
    start = m.end()
    next_header = re.search(r'(?im)^\s*[A-Z][A-Z .\'-]+\s+projected lineup\s*$', text[start:])
    status = re.search(r'(?im)^\s*Status report\s*$', text[start:])
    ends = [x.start() for x in (next_header, status) if x]
    end = start + (min(ends) if ends else len(text[start:]))
    section = text[start:end]
    lines = [x.strip() for x in section.splitlines() if x.strip()]
    forwards, defense, goalies = [], [], []
    scratched, injured = [], []
    mode = 'lineup'
    for line in lines:
        low = line.lower()
        if low.startswith('scratched:'):
            mode = 'scratched'; scratched += _split_csv(line.split(':',1)[1]); continue
        if low.startswith('injured:'):
            mode = 'injured'; injured += _split_csv(line.split(':',1)[1]); continue
        if mode == 'scratched':
            if ':' not in line and '--' not in line and len(line) < 250:
                scratched += _split_csv(line)
            continue
        if mode == 'injured':
            if ':' not in line and '--' not in line and len(line) < 250:
                injured += _split_csv(line)
            continue
        if ' -- ' in line:
            names = [x.strip() for x in line.split(' -- ') if x.strip()]
            if len(names) >= 3:
                forwards.append(names)
            elif len(names) == 2:
                defense.append(names)
            continue
        # Goalies are the standalone player-name lines immediately after D pairs.
        if defense and len(goalies) < 3 and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ'’.\- ]{3,60}", line):
            if line not in ('Projected lineup', 'Status report'):
                goalies.append(line)
    return {
        'found': True,
        'forwards': forwards[:4],
        'defense': defense[:3],
        'goalies': goalies[:2],
        'scratched': scratched,
        'injured': injured,
    }

def fetch_lineups(force=False):
    now = time.time()
    if not force and _lineup_cache['data'] and now - _lineup_cache['at'] < 300:
        return _lineup_cache['data']
    req = Request(LINEUP_URL, headers={
        'User-Agent': 'Mozilla/5.0 NHLShotsAnalyzer/0.10',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.8',
    })
    with urlopen(req, timeout=18) as response:
        html = response.read(5_000_000)
    text = _article_text(html)
    data = {
        'source': 'NHL.com',
        'url': LINEUP_URL,
        'source_date': _source_date(text),
        'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'teams': {code: _parse_team(text, code) for code in TEAM_NICK},
    }
    _lineup_cache.update(at=now, data=data)
    return data


def _nhl_json(path, ttl=21600):
    """Cached JSON reader for NHL API. Historical responses are stable enough for long cache windows."""
    now = time.time()
    with _nhl_cache_lock:
        cached = _nhl_cache.get(path)
        if cached and now - cached[0] < ttl:
            return cached[1]
    req = Request(
        'https://api-web.nhle.com/v1' + path,
        headers={'User-Agent': 'NHLShotsAnalyzer/0.10', 'Accept': 'application/json'},
    )
    with urlopen(req, timeout=18) as response:
        data = json.loads(response.read(3_000_000).decode('utf-8'))
    with _nhl_cache_lock:
        _nhl_cache[path] = (now, data)
    return data


def _games_from_schedule(data):
    games = list(data.get('games') or [])
    for day in data.get('gameWeek') or []:
        games.extend(day.get('games') or [])
    return games


def _season_start(date_value):
    if isinstance(date_value, str):
        d = datetime.strptime(date_value[:10], '%Y-%m-%d')
    else:
        d = date_value
    return d.year - 1 if d.month < 8 else d.year


def _season_id(start_year):
    return f'{start_year}{start_year + 1}'


def _team_schedule(team, start_year):
    return _games_from_schedule(_nhl_json(f'/club-schedule-season/{team}/{_season_id(start_year)}'))


def _completed_regular(game):
    return game.get('gameType') == 2 and game.get('gameState') in ('OFF', 'FINAL') and (game.get('id') or game.get('gameId'))


def _game_id(game):
    return game.get('id') or game.get('gameId')


def _box_row(game, team):
    game_id = _game_id(game)
    box = _nhl_json(f'/gamecenter/{game_id}/boxscore')
    home = (box.get('homeTeam') or {}).get('abbrev') == team
    own = box.get('homeTeam') if home else box.get('awayTeam')
    opp = box.get('awayTeam') if home else box.get('homeTeam')
    if not own or not opp:
        return None
    vals = [own.get('sog'), opp.get('sog'), own.get('score'), opp.get('score')]
    try:
        shots, allowed, goals, ga = [float(x) for x in vals]
    except (TypeError, ValueError):
        return None
    return {
        'date': game.get('gameDate') or (game.get('startTimeUTC') or '')[:10],
        'opp': opp.get('abbrev'),
        'home': home,
        'shots': shots,
        'allowed': allowed,
        'goals': goals,
        'ga': ga,
        'gameId': game_id,
    }


def _history_before(team, game_date, n=30):
    start = _season_start(game_date)
    games = []
    for year in (start, start - 1):
        games.extend(_team_schedule(team, year))
    unique = {}
    for game in games:
        gid = _game_id(game)
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        if gid and _completed_regular(game) and date and date < game_date:
            unique[gid] = game
    selected = sorted(
        unique.values(),
        key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '',
        reverse=True,
    )[:n]
    if not selected:
        return []
    workers = min(8, len(selected))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda g: _box_row(g, team), selected))
    return [r for r in rows if r]


def _avg(rows, key):
    return sum(r[key] for r in rows) / len(rows)


def _weighted(rows, key):
    a = _avg(rows, key)
    b = _avg(rows[:min(10, len(rows))], key)
    c = _avg(rows[:min(5, len(rows))], key)
    return .55 * a + .25 * b + .20 * c


def _venue_mean(rows, key, home):
    subset = [r for r in rows if r['home'] is home]
    return _avg(subset, key) if len(subset) >= 5 else None


def _blend(base, venue):
    return base if venue is None else .8 * base + .2 * venue


def _poisson(lam, max_n=135):
    p = math.exp(-lam)
    arr = [p]
    for i in range(1, max_n + 1):
        p *= lam / i
        arr.append(p)
    return arr


def _h2h(a, b):
    win_a = tie = cumulative_b = 0.0
    for i, p_a in enumerate(a):
        win_a += p_a * cumulative_b
        p_b = b[i] if i < len(b) else 0.0
        tie += p_a * p_b
        cumulative_b += p_b
    return win_a, tie, max(0.0, 1.0 - win_a - tie)


def _over(dist, line):
    return sum(p for i, p in enumerate(dist) if i > line)


def _predict_game(home, away, game_date):
    rh = _history_before(home, game_date, 30)
    ra = _history_before(away, game_date, 30)
    if len(rh) < 10 or len(ra) < 10:
        return None
    eh = (
        _blend(_weighted(rh, 'shots'), _venue_mean(rh, 'shots', True)) +
        _blend(_weighted(ra, 'allowed'), _venue_mean(ra, 'allowed', False))
    ) / 2
    ea = (
        _blend(_weighted(ra, 'shots'), _venue_mean(ra, 'shots', False)) +
        _blend(_weighted(rh, 'allowed'), _venue_mean(rh, 'allowed', True))
    ) / 2
    gh = (
        _blend(_weighted(rh, 'goals'), _venue_mean(rh, 'goals', True)) +
        _blend(_weighted(ra, 'ga'), _venue_mean(ra, 'ga', False))
    ) / 2
    ga = (
        _blend(_weighted(ra, 'goals'), _venue_mean(ra, 'goals', False)) +
        _blend(_weighted(rh, 'ga'), _venue_mean(rh, 'ga', True))
    ) / 2
    return {'home_shots': eh, 'away_shots': ea, 'home_goals': gh, 'away_goals': ga,
            'home_history': len(rh), 'away_history': len(ra)}


def _regulation_result(game_id):
    data = _nhl_json(f'/gamecenter/{game_id}/play-by-play')
    home_id = (data.get('homeTeam') or {}).get('id')
    away_id = (data.get('awayTeam') or {}).get('id')
    plays = data.get('plays') or []
    if not home_id or not away_id:
        return None
    home = away = 0
    for play in plays:
        period = ((play.get('periodDescriptor') or {}).get('number'))
        if play.get('typeDescKey') != 'goal' or not isinstance(period, int) or period < 1 or period > 3:
            continue
        owner = (play.get('details') or {}).get('eventOwnerTeamId')
        if owner == home_id:
            home += 1
        elif owner == away_id:
            away += 1
    return 'home' if home > away else 'away' if away > home else 'draw'


def _add_prediction(bucket, probability, correct):
    bucket.append({'p': max(0.0, min(1.0, probability)), 'y': 1 if correct else 0})


def _calibration(predictions):
    bins = [(0, .4, '0–39 %'), (.4, .5, '40–49 %'), (.5, .6, '50–59 %'),
            (.6, .7, '60–69 %'), (.7, .8, '70–79 %'), (.8, 1.000001, '80–100 %')]
    result = []
    for lo, hi, label in bins:
        rows = [x for x in predictions if lo <= x['p'] < hi]
        if rows:
            result.append({
                'label': label,
                'n': len(rows),
                'avg_probability': sum(x['p'] for x in rows) / len(rows),
                'actual_rate': sum(x['y'] for x in rows) / len(rows),
            })
    return result


def _metric_summary(predictions, label, reference=None):
    if not predictions:
        return {'label': label, 'n': 0, 'accuracy': None, 'avg_probability': None, 'brier': None,
                'reference': reference, 'calibration': []}
    n = len(predictions)
    return {
        'label': label,
        'n': n,
        'accuracy': sum(x['y'] for x in predictions) / n,
        'avg_probability': sum(x['p'] for x in predictions) / n,
        'brier': sum((x['p'] - x['y']) ** 2 for x in predictions) / n,
        'reference': reference,
        'calibration': _calibration(predictions),
    }


def run_backtest(team, season_start, game_limit=10):
    started = time.time()
    schedule = [g for g in _team_schedule(team, season_start) if _completed_regular(g)]
    schedule.sort(key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '', reverse=True)
    targets = schedule[:game_limit]
    markets = {'h2h': [], 'team': [], 'total': [], 'goals': [], 'winner': []}
    game_rows = []
    skipped = 0
    for game in targets:
        home = (game.get('homeTeam') or {}).get('abbrev')
        away = (game.get('awayTeam') or {}).get('abbrev')
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        game_id = _game_id(game)
        if not home or not away or not date or not game_id:
            skipped += 1
            continue
        prediction = _predict_game(home, away, date)
        if not prediction:
            skipped += 1
            continue
        box = _nhl_json(f'/gamecenter/{game_id}/boxscore')
        ht, at = box.get('homeTeam') or {}, box.get('awayTeam') or {}
        try:
            hs, aas = float(ht.get('sog')), float(at.get('sog'))
            hg, ag = float(ht.get('score')), float(at.get('score'))
        except (TypeError, ValueError):
            skipped += 1
            continue
        sh = _poisson(prediction['home_shots'])
        sa = _poisson(prediction['away_shots'])
        ph, pt, pa = _h2h(sh, sa)
        pick_home = ph >= pa
        h2h_conf = ph if pick_home else pa
        h2h_ok = hs > aas if pick_home else aas > hs
        _add_prediction(markets['h2h'], h2h_conf, h2h_ok)

        # Reference team-shots line: 27.5. Each game contributes one home and one away decision.
        for expected, actual in ((prediction['home_shots'], hs), (prediction['away_shots'], aas)):
            p_over = _over(_poisson(expected), 27.5)
            choose_over = p_over >= .5
            conf = p_over if choose_over else 1 - p_over
            ok = actual > 27.5 if choose_over else actual < 27.5
            _add_prediction(markets['team'], conf, ok)

        p_total_over = _over(_poisson(prediction['home_shots'] + prediction['away_shots']), 56.5)
        total_over = p_total_over >= .5
        _add_prediction(markets['total'], p_total_over if total_over else 1 - p_total_over,
                        (hs + aas > 56.5) if total_over else (hs + aas < 56.5))

        p_goals_over = _over(_poisson(prediction['home_goals'] + prediction['away_goals'], 55), 5.5)
        goals_over = p_goals_over >= .5
        _add_prediction(markets['goals'], p_goals_over if goals_over else 1 - p_goals_over,
                        (hg + ag > 5.5) if goals_over else (hg + ag < 5.5))

        gh = _poisson(prediction['home_goals'], 45)
        ga = _poisson(prediction['away_goals'], 45)
        wh, wt, wa = _h2h(gh, ga)
        probs = {'home': wh, 'draw': wt, 'away': wa}
        pick = max(probs, key=probs.get)
        regulation = _regulation_result(game_id)
        if regulation:
            _add_prediction(markets['winner'], probs[pick], pick == regulation)

        game_rows.append({
            'date': date,
            'matchup': f'{away} @ {home}',
            'actual_shots': f'{int(hs)}:{int(aas)}',
            'expected_shots': f"{prediction['home_shots']:.1f}:{prediction['away_shots']:.1f}",
            'h2h_pick': home if pick_home else away,
            'h2h_probability': h2h_conf,
            'h2h_correct': bool(h2h_ok),
        })

    summaries = {
        'h2h': _metric_summary(markets['h2h'], 'Více střel', '3cestný trh; shoda = prohra týmového výběru'),
        'team': _metric_summary(markets['team'], 'Střely týmu ±', 'referenční hranice 27,5'),
        'total': _metric_summary(markets['total'], 'Celkové střely ±', 'referenční hranice 56,5'),
        'goals': _metric_summary(markets['goals'], 'Góly ±', 'referenční hranice 5,5'),
        'winner': _metric_summary(markets['winner'], 'Vítěz 1/X/2', 'výsledek po 60 minutách'),
    }
    return {
        'team': team,
        'season': _season_id(season_start),
        'requested_games': game_limit,
        'evaluated_games': len(game_rows),
        'skipped_games': skipped,
        'elapsed_seconds': round(time.time() - started, 1),
        'model': 'Auto forma 55/25/20 + 20% korekce doma/venku při min. 5 zápasech',
        'markets': summaries,
        'games': game_rows,
        'warning': 'Backtest nepoužívá historické kurzy sázkových kanceláří, proto nepočítá historické EV ani ROI.',
    }

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/lineups':
            return self.handle_lineups(parse_qs(parsed.query))
        if parsed.path == '/backtest':
            return self.handle_backtest(parse_qs(parsed.query))
        if not parsed.path.startswith('/api/'):
            return super().do_GET()
        path = parsed.path[4:]
        if not ALLOWED.fullmatch(path):
            return self.respond(400, {'error': 'Nepovolená cesta API'})
        try:
            req = Request(
                'https://api-web.nhle.com/v1' + path,
                headers={'User-Agent': 'NHLShotsAnalyzer/0.10', 'Accept': 'application/json'},
            )
            with urlopen(req, timeout=18) as response:
                payload = response.read(3_000_000)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'public, max-age=180')
            self.end_headers()
            self.wfile.write(payload)
        except HTTPError as exc:
            self.respond(502, {'error': f'NHL API HTTP {exc.code}'})
        except (URLError, TimeoutError):
            self.respond(502, {'error': 'NHL API není dostupné'})

    def handle_backtest(self, query):
        team = (query.get('team', ['FLA'])[0] or 'FLA').upper()
        raw_season = query.get('season', ['20252026'])[0]
        try:
            games = int(query.get('games', ['10'])[0])
        except ValueError:
            games = 10
        if team not in TEAM_NICK:
            return self.respond(400, {'error': 'Neplatný tým pro backtest'})
        if not re.fullmatch(r'20\d{6}', raw_season):
            return self.respond(400, {'error': 'Neplatná sezóna'})
        start = int(raw_season[:4])
        if raw_season != _season_id(start):
            return self.respond(400, {'error': 'Sezóna musí mít tvar 20252026'})
        games = max(5, min(20, games))
        try:
            result = run_backtest(team, start, games)
            return self.respond(200, result, cache='no-store')
        except HTTPError as exc:
            return self.respond(502, {'error': f'NHL API při backtestu odpovědělo HTTP {exc.code}'})
        except (URLError, TimeoutError):
            return self.respond(502, {'error': 'NHL API není při backtestu dostupné'})
        except Exception as exc:
            return self.respond(500, {'error': 'Backtest se nepodařilo dokončit', 'detail': str(exc)[:180]})

    def handle_lineups(self, query):
        requested = []
        for raw in query.get('teams', []):
            requested.extend(x.strip().upper() for x in raw.split(',') if x.strip())
        requested = list(dict.fromkeys(requested))[:2]
        if not requested or any(x not in TEAM_NICK for x in requested):
            return self.respond(400, {'error': 'Neplatné týmy pro sestavy'})
        try:
            data = fetch_lineups()
            subset = {code: data['teams'].get(code, {'found': False}) for code in requested}
            return self.respond(200, {
                'source': data['source'], 'url': data['url'], 'source_date': data['source_date'],
                'fetched_at': data['fetched_at'], 'teams': subset,
            }, cache='public, max-age=120')
        except HTTPError as exc:
            return self.respond(502, {'error': f'NHL.com odpovědělo HTTP {exc.code}'})
        except (URLError, TimeoutError):
            return self.respond(502, {'error': 'NHL.com sestavy nejsou momentálně dostupné'})
        except Exception:
            return self.respond(502, {'error': 'Sestavy se nepodařilo zpracovat'})

    def respond(self, status, data, cache='no-store'):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
