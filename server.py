"""NHL Team Shots Analyzer 0.16 — static site, NHL API proxy and official NHL.com lineup reader."""
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
ARENA_COORDS = {
    'ANA':(33.84,-117.91),'BOS':(42.37,-71.06),'BUF':(42.88,-78.88),'CGY':(51.04,-114.05),
    'CAR':(35.80,-78.72),'CHI':(41.88,-87.67),'COL':(39.75,-105.01),'CBJ':(39.97,-83.01),
    'DAL':(32.79,-96.81),'DET':(42.34,-83.05),'EDM':(53.55,-113.50),'FLA':(26.16,-80.33),
    'LAK':(34.04,-118.27),'MIN':(44.94,-93.10),'MTL':(45.50,-73.57),'NSH':(36.16,-86.78),
    'NJD':(40.73,-74.17),'NYI':(40.72,-73.59),'NYR':(40.75,-73.99),'OTT':(45.30,-75.93),
    'PHI':(39.90,-75.17),'PIT':(40.44,-79.99),'SEA':(47.62,-122.35),'SJS':(37.33,-121.90),
    'STL':(38.63,-90.20),'TBL':(27.94,-82.45),'TOR':(43.64,-79.38),'UTA':(40.77,-111.90),
    'VAN':(49.28,-123.11),'VGK':(36.10,-115.18),'WSH':(38.90,-77.02),'WPG':(49.89,-97.14),
}

def _distance_km(a, b):
    if a not in ARENA_COORDS or b not in ARENA_COORDS:
        return None
    lat1, lon1 = [math.radians(x) for x in ARENA_COORDS[a]]
    lat2, lon2 = [math.radians(x) for x in ARENA_COORDS[b]]
    q = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return round(12742 * math.asin(min(1.0, math.sqrt(q))))

def _clip(value, lo, hi):
    return max(lo, min(hi, value))

_lineup_cache = {'at': 0.0, 'data': None}
_nhl_cache = {}
_nhl_cache_lock = threading.Lock()
_history_cache = {}
_history_cache_lock = threading.Lock()


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
        'User-Agent': 'Mozilla/5.0 NHLShotsAnalyzer/0.16',
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
        headers={'User-Agent': 'NHLShotsAnalyzer/0.16', 'Accept': 'application/json'},
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
    home_team = (box.get('homeTeam') or {}).get('abbrev')
    away_team = (box.get('awayTeam') or {}).get('abbrev')
    home = home_team == team
    own = box.get('homeTeam') if home else box.get('awayTeam')
    opp = box.get('awayTeam') if home else box.get('homeTeam')
    if not own or not opp or not home_team or not away_team:
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
        'venue': home_team,
        'shots': shots,
        'allowed': allowed,
        'goals': goals,
        'ga': ga,
        'gameId': game_id,
    }


def _history_games_before(team, game_date, n=30):
    """Return the schedule rows used as pre-game history, without fetching boxscores yet."""
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
    return sorted(
        unique.values(),
        key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '',
        reverse=True,
    )[:n]


def _history_before(team, game_date, n=30):
    key = (team, game_date, int(n))
    with _history_cache_lock:
        cached = _history_cache.get(key)
    if cached is not None:
        return cached
    selected = _history_games_before(team, game_date, n)
    if not selected:
        rows = []
    else:
        workers = min(8, len(selected))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = [r for r in pool.map(lambda g: _box_row(g, team), selected) if r]
    with _history_cache_lock:
        _history_cache[key] = rows
    return rows


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



def _exp_weighted(rows, key, half_life=8.0):
    """Exponential recency weighting; rows are newest first."""
    weights = [0.5 ** (i / half_life) for i in range(len(rows))]
    total = sum(weights)
    return sum(w * r[key] for w, r in zip(weights, rows)) / total


def _row_contexts(rows, key):
    """Historical pre-game context inferred only from games already in the team's history."""
    if len(rows) < 2:
        return []
    center = _avg(rows, key)
    out = []
    for i in range(len(rows) - 1):
        current, previous = rows[i], rows[i + 1]
        try:
            gap = (datetime.strptime(current['date'], '%Y-%m-%d') -
                   datetime.strptime(previous['date'], '%Y-%m-%d')).days
        except (ValueError, TypeError, KeyError):
            continue
        if gap <= 0:
            continue
        km = _distance_km(previous.get('venue'), current.get('venue'))
        out.append({
            'residual': current[key] - center,
            'b2b': gap == 1,
            'free_days': max(0, gap - 1),
            'travel1000': None if km is None else km / 1000.0,
        })
    return out


def _target_context(rows, target_venue, game_date):
    if not rows:
        return {'b2b': False, 'free_days': None, 'travel_km': None}
    previous = rows[0]
    try:
        gap = (datetime.strptime(game_date, '%Y-%m-%d') -
               datetime.strptime(previous['date'], '%Y-%m-%d')).days
    except (ValueError, TypeError, KeyError):
        gap = None
    return {
        'b2b': gap == 1,
        'free_days': None if gap is None else max(0, gap - 1),
        'travel_km': _distance_km(previous.get('venue'), target_venue),
    }


def _context_adjustment(rows, key, target, kind):
    """Estimate small rest/travel effects from that team's own pre-game history.
    Coefficients are shrunk and clipped because samples are small; backtest decides whether they help.
    """
    samples = _row_contexts(rows, key)
    if not samples:
        return {'total': 0.0, 'b2b': 0.0, 'rest': 0.0, 'travel': 0.0}

    limit_b2b = 1.5 if kind == 'shots' else 0.30
    limit_rest_slope = 0.45 if kind == 'shots' else 0.08  # per extra free day, capped context
    limit_rest = 0.8 if kind == 'shots' else 0.16
    limit_slope = 0.75 if kind == 'shots' else 0.12  # per 1000 km
    limit_travel = 1.5 if kind == 'shots' else 0.25

    b2b_rows = [x['residual'] for x in samples if x['b2b']]
    rested = [x['residual'] for x in samples if not x['b2b']]
    b2b_delta = 0.0
    if target.get('b2b') and len(b2b_rows) >= 2 and len(rested) >= 6:
        raw = sum(b2b_rows)/len(b2b_rows) - sum(rested)/len(rested)
        shrink = len(b2b_rows) / (len(b2b_rows) + 4.0)
        b2b_delta = _clip(raw * shrink, -limit_b2b, limit_b2b)

    rest_delta = 0.0
    target_free = target.get('free_days')
    rested_rows = [(min(3.0, float(x['free_days'])), x['residual']) for x in samples if not x['b2b']]
    if target_free is not None and target_free >= 1 and len(rested_rows) >= 8:
        xs = [x for x, _ in rested_rows]
        ys = [y for _, y in rested_rows]
        mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
        var = sum((x-mx)**2 for x in xs)
        if var > 0.5:
            slope = sum((x-mx)*(y-my) for x, y in rested_rows) / var
            slope *= len(rested_rows) / (len(rested_rows) + 20.0)
            slope = _clip(slope, -limit_rest_slope, limit_rest_slope)
            rest_delta = _clip(slope * (min(3.0, float(target_free)) - mx), -limit_rest, limit_rest)

    travel_rows = [(x['travel1000'], x['residual']) for x in samples if x['travel1000'] is not None]
    travel_delta = 0.0
    target_km = target.get('travel_km')
    if target_km is not None and len(travel_rows) >= 10:
        xs = [x for x, _ in travel_rows]
        ys = [y for _, y in travel_rows]
        mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
        var = sum((x-mx)**2 for x in xs)
        if var > 0.16:
            slope = sum((x-mx)*(y-my) for x, y in travel_rows) / var
            slope *= len(travel_rows) / (len(travel_rows) + 20.0)
            slope = _clip(slope, -limit_slope, limit_slope)
            travel_delta = _clip(slope * (target_km/1000.0 - mx), -limit_travel, limit_travel)

    return {'total': b2b_delta + rest_delta + travel_delta, 'b2b': b2b_delta,
            'rest': rest_delta, 'travel': travel_delta}


def _predict_variant(home, away, game_date, *, recency=False, opponent=False, context=False):
    """Build a pre-game prediction with independently switchable factors.

    This powers the 0.13 ablation test. Every variant sees exactly the same
    pre-game history; only the requested factor changes.
    """
    rh = _history_before(home, game_date, 30)
    ra = _history_before(away, game_date, 30)
    if len(rh) < 10 or len(ra) < 10:
        return None

    weight = _exp_weighted if recency else _weighted
    h_sh, h_al = weight(rh, 'shots'), weight(rh, 'allowed')
    a_sh, a_al = weight(ra, 'shots'), weight(ra, 'allowed')
    h_g, h_ga = weight(rh, 'goals'), weight(rh, 'ga')
    a_g, a_ga = weight(ra, 'goals'), weight(ra, 'ga')

    # Apply the same modest venue blend before factor-specific combination.
    h_sh_v = _blend(h_sh, _venue_mean(rh, 'shots', True))
    h_al_v = _blend(h_al, _venue_mean(rh, 'allowed', True))
    a_sh_v = _blend(a_sh, _venue_mean(ra, 'shots', False))
    a_al_v = _blend(a_al, _venue_mean(ra, 'allowed', False))
    h_g_v = _blend(h_g, _venue_mean(rh, 'goals', True))
    h_ga_v = _blend(h_ga, _venue_mean(rh, 'ga', True))
    a_g_v = _blend(a_g, _venue_mean(ra, 'goals', False))
    a_ga_v = _blend(a_ga, _venue_mean(ra, 'ga', False))

    if opponent:
        # Stronger opponent-strength normalization: attack + opponent defense
        # relative to the four-way local pre-game baseline.
        shot_base = (h_sh_v + h_al_v + a_sh_v + a_al_v) / 4.0
        goal_base = (h_g_v + h_ga_v + a_g_v + a_ga_v) / 4.0
        eh = h_sh_v + a_al_v - shot_base
        ea = a_sh_v + h_al_v - shot_base
        gh = h_g_v + a_ga_v - goal_base
        ga = a_g_v + h_ga_v - goal_base
    else:
        shot_base = (h_sh_v + h_al_v + a_sh_v + a_al_v) / 4.0
        goal_base = (h_g_v + h_ga_v + a_g_v + a_ga_v) / 4.0
        eh = (h_sh_v + a_al_v) / 2.0
        ea = (a_sh_v + h_al_v) / 2.0
        gh = (h_g_v + a_ga_v) / 2.0
        ga = (a_g_v + h_ga_v) / 2.0

    target_venue = home
    ctx_h = _target_context(rh, target_venue, game_date)
    ctx_a = _target_context(ra, target_venue, game_date)
    zero = {'total': 0.0, 'b2b': 0.0, 'rest': 0.0, 'travel': 0.0}
    if context:
        adj_hs = _context_adjustment(rh, 'shots', ctx_h, 'shots')
        adj_as = _context_adjustment(ra, 'shots', ctx_a, 'shots')
        adj_hg = _context_adjustment(rh, 'goals', ctx_h, 'goals')
        adj_ag = _context_adjustment(ra, 'goals', ctx_a, 'goals')
    else:
        adj_hs = adj_as = adj_hg = adj_ag = zero

    eh = _clip(eh + adj_hs['total'], 10.0, 50.0)
    ea = _clip(ea + adj_as['total'], 10.0, 50.0)
    gh = _clip(gh + adj_hg['total'], 0.2, 8.0)
    ga = _clip(ga + adj_ag['total'], 0.2, 8.0)

    return {
        'home_shots': eh, 'away_shots': ea, 'home_goals': gh, 'away_goals': ga,
        'home_history': len(rh), 'away_history': len(ra),
        'factors': {
            'shot_baseline': shot_base, 'goal_baseline': goal_base,
            'home_b2b': ctx_h.get('b2b'), 'away_b2b': ctx_a.get('b2b'),
            'home_free_days': ctx_h.get('free_days'), 'away_free_days': ctx_a.get('free_days'),
            'home_travel_km': ctx_h.get('travel_km'), 'away_travel_km': ctx_a.get('travel_km'),
            'home_context_shots': adj_hs['total'], 'away_context_shots': adj_as['total'],
            'home_context_goals': adj_hg['total'], 'away_context_goals': adj_ag['total'],
        }
    }


def _predict_game_recency(home, away, game_date):
    return _predict_variant(home, away, game_date, recency=True)


def _predict_game_opponent(home, away, game_date):
    return _predict_variant(home, away, game_date, opponent=True)


def _predict_game_context(home, away, game_date):
    return _predict_variant(home, away, game_date, context=True)


def _predict_game_v2(home, away, game_date):
    return _predict_variant(home, away, game_date, recency=True, opponent=True, context=True)

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
    return _predict_variant(home, away, game_date)

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


def _add_prediction(bucket, probability, correct, side=None, actual=None):
    row = {'p': max(0.0, min(1.0, probability)), 'y': 1 if correct else 0}
    if side is not None:
        row['side'] = side
    if actual is not None:
        row['actual'] = actual
    bucket.append(row)


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
                'reference': reference, 'calibration': [], 'over_picks': None, 'under_picks': None,
                'actual_over_rate': None, 'baseline_accuracy': None, 'baseline_brier': None,
                'brier_skill': None}
    n = len(predictions)
    sides = [x.get('side') for x in predictions if x.get('side') in ('over', 'under')]
    actuals = [x.get('actual') for x in predictions if x.get('actual') in ('over', 'under')]
    brier = sum((x['p'] - x['y']) ** 2 for x in predictions) / n
    actual_over_rate = (sum(x == 'over' for x in actuals) / len(actuals)) if actuals else None
    baseline_accuracy = baseline_brier = brier_skill = None
    if actual_over_rate is not None:
        q = max(actual_over_rate, 1.0 - actual_over_rate)
        baseline_accuracy = q
        baseline_brier = q * (1.0 - q)
        if baseline_brier > 1e-9:
            brier_skill = 1.0 - brier / baseline_brier
    return {
        'label': label,
        'n': n,
        'accuracy': sum(x['y'] for x in predictions) / n,
        'avg_probability': sum(x['p'] for x in predictions) / n,
        'brier': brier,
        'reference': reference,
        'calibration': _calibration(predictions),
        'over_picks': sum(x == 'over' for x in sides) if sides else None,
        'under_picks': sum(x == 'under' for x in sides) if sides else None,
        'actual_over_rate': actual_over_rate,
        'baseline_accuracy': baseline_accuracy,
        'baseline_brier': baseline_brier,
        'brier_skill': brier_skill,
    }


def _chronological_calibration(predictions, train_fraction=0.60):
    """Fit a simple affine probability calibrator on older observations and
    evaluate it only on newer observations.

    Predictions arrive newest-first from the backtest loop, so reversing them
    creates chronological order. This avoids using future target outcomes to
    calibrate earlier games.
    """
    ordered = list(reversed(predictions))
    n = len(ordered)
    if n < 25:
        return {'available': False, 'n': n, 'reason': 'Málo dat pro chronologickou kalibraci'}
    split = int(n * train_fraction)
    split = max(12, min(n - 10, split))
    train, test = ordered[:split], ordered[split:]
    if len(train) < 12 or len(test) < 10:
        return {'available': False, 'n': n, 'reason': 'Málo dat v train/test části'}

    mean_p = sum(x['p'] for x in train) / len(train)
    mean_y = sum(x['y'] for x in train) / len(train)
    best = None
    # Slope-only complexity is deliberately tiny. Intercept is anchored so the
    # training mean probability equals the training hit rate after calibration.
    for i in range(31):
        slope = i / 20.0  # 0.00 ... 1.50
        intercept = mean_y - slope * mean_p
        vals = [_clip(intercept + slope * x['p'], .01, .99) for x in train]
        brier = sum((p - x['y']) ** 2 for p, x in zip(vals, train)) / len(train)
        if best is None or brier < best[0]:
            best = (brier, slope, intercept)

    _, slope, intercept = best
    raw = [x['p'] for x in test]
    calibrated = [_clip(intercept + slope * p, .01, .99) for p in raw]
    raw_brier = sum((p - x['y']) ** 2 for p, x in zip(raw, test)) / len(test)
    cal_brier = sum((p - x['y']) ** 2 for p, x in zip(calibrated, test)) / len(test)
    return {
        'available': True,
        'train_n': len(train), 'test_n': len(test),
        'slope': slope, 'intercept': intercept,
        'raw_brier': raw_brier, 'calibrated_brier': cal_brier,
        'brier_gain': raw_brier - cal_brier,
        'raw_avg_probability': sum(raw) / len(raw),
        'calibrated_avg_probability': sum(calibrated) / len(calibrated),
        'actual_rate': sum(x['y'] for x in test) / len(test),
    }


TEAM_SHOT_LINES = (23.5, 25.5, 27.5, 29.5, 31.5)
TOTAL_SHOT_LINES = (51.5, 53.5, 55.5, 56.5, 57.5, 59.5, 61.5)
GOAL_LINES = (4.5, 5.5, 6.5, 7.5)


def _prefetch(paths, workers=16):
    paths = list(dict.fromkeys(paths))
    if not paths:
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as pool:
        list(pool.map(_nhl_json, paths))


def _league_schedule(season_start):
    """Load each club schedule in parallel and deduplicate games by NHL game id."""
    teams = list(TEAM_NICK)
    with ThreadPoolExecutor(max_workers=12) as pool:
        schedules = list(pool.map(lambda t: _team_schedule(t, season_start), teams))
    unique = {}
    for schedule in schedules:
        for game in schedule:
            gid = _game_id(game)
            if gid and _completed_regular(game):
                unique[gid] = game
    return list(unique.values())


def _prepare_backtest_targets(team, season_start, game_limit):
    """Prepare a balanced historical sample.

    ``season_start`` may be one start year or a sequence of start years. In the
    multi-season mode we take an approximately equal number of games from each
    season before combining them. This prevents the newer season from crowding
    the older one out of the requested sample.
    """
    seasons = list(season_start) if isinstance(season_start, (list, tuple)) else [season_start]
    base = game_limit // len(seasons)
    remainder = game_limit % len(seasons)
    selected = []
    for idx, start in enumerate(seasons):
        limit = base + (1 if idx < remainder else 0)
        if team == 'ALL':
            schedule = _league_schedule(start)
        else:
            schedule = [g for g in _team_schedule(team, start) if _completed_regular(g)]
        schedule.sort(key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '', reverse=True)
        selected.extend(schedule[:limit])
    unique = {}
    for game in selected:
        gid = _game_id(game)
        if gid:
            unique[gid] = game
    return sorted(unique.values(), key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '', reverse=True)[:game_limit]


def _prefetch_backtest_histories(targets):
    """Warm schedule/boxscore/PBP caches so league-wide validation is practical on Render."""
    needed_schedule = set()
    target_ids = []
    for game in targets:
        home = (game.get('homeTeam') or {}).get('abbrev')
        away = (game.get('awayTeam') or {}).get('abbrev')
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        gid = _game_id(game)
        if not home or not away or not date or not gid:
            continue
        target_ids.append(gid)
        start = _season_start(date)
        for t in (home, away):
            needed_schedule.add((t, start))
            needed_schedule.add((t, start - 1))
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda x: _team_schedule(x[0], x[1]), needed_schedule))

    history_ids = set(target_ids)
    for game in targets:
        home = (game.get('homeTeam') or {}).get('abbrev')
        away = (game.get('awayTeam') or {}).get('abbrev')
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        if not home or not away or not date:
            continue
        for t in (home, away):
            for old in _history_games_before(t, date, 30):
                gid = _game_id(old)
                if gid:
                    history_ids.add(gid)
    _prefetch([f'/gamecenter/{gid}/boxscore' for gid in history_ids], workers=18)
    _prefetch([f'/gamecenter/{gid}/play-by-play' for gid in target_ids], workers=18)


def _record_line(bucket, expected, actual, line, max_n=135):
    p_over = _over(_poisson(expected, max_n), line)
    choose_over = p_over >= .5
    side = 'over' if choose_over else 'under'
    actual_side = 'over' if actual > line else 'under'
    conf = p_over if choose_over else 1 - p_over
    _add_prediction(bucket, conf, side == actual_side, side=side, actual=actual_side)


def _evaluate_model_prediction(prediction, hs, aas, hg, ag, regulation, markets, line_buckets):
    sh = _poisson(prediction['home_shots'])
    sa = _poisson(prediction['away_shots'])
    ph, pt, pa = _h2h(sh, sa)
    pick_home = ph >= pa
    h2h_conf = ph if pick_home else pa
    h2h_ok = hs > aas if pick_home else aas > hs
    _add_prediction(markets['h2h'], h2h_conf, h2h_ok)

    for line in TEAM_SHOT_LINES:
        _record_line(line_buckets['team'][line], prediction['home_shots'], hs, line)
        _record_line(line_buckets['team'][line], prediction['away_shots'], aas, line)
    for line in TOTAL_SHOT_LINES:
        _record_line(line_buckets['total'][line], prediction['home_shots'] + prediction['away_shots'], hs + aas, line)
    for line in GOAL_LINES:
        _record_line(line_buckets['goals'][line], prediction['home_goals'] + prediction['away_goals'], hg + ag, line, 55)

    markets['team'].extend(line_buckets['team'][27.5][-2:])
    markets['total'].append(line_buckets['total'][56.5][-1])
    markets['goals'].append(line_buckets['goals'][5.5][-1])

    gh = _poisson(prediction['home_goals'], 45)
    ga = _poisson(prediction['away_goals'], 45)
    wh, wt, wa = _h2h(gh, ga)
    probs = {'home': wh, 'draw': wt, 'away': wa}
    pick = max(probs, key=probs.get)
    if regulation:
        _add_prediction(markets['winner'], probs[pick], pick == regulation)

    return {'pick_home': pick_home, 'h2h_conf': h2h_conf, 'h2h_ok': h2h_ok}


def _summaries(markets):
    return {
        'h2h': _metric_summary(markets['h2h'], 'Více střel', '3cestný trh; shoda = prohra týmového výběru'),
        'team': _metric_summary(markets['team'], 'Střely týmu ±', 'referenční hranice 27,5'),
        'total': _metric_summary(markets['total'], 'Celkové střely ±', 'referenční hranice 56,5'),
        'goals': _metric_summary(markets['goals'], 'Góly ±', 'referenční hranice 5,5'),
        'winner': _metric_summary(markets['winner'], 'Vítěz 1/X/2', 'výsledek po 60 minutách'),
    }


def _line_summaries(line_buckets):
    return {
        key: [dict(_metric_summary(bucket, f'{line:g}', f'hranice {line:g}'), line=line)
              for line, bucket in values.items()]
        for key, values in line_buckets.items()
    }


def _blend_candidate_prediction(predictions, weights):
    """Conservative Model 3: start from Model 1 and add shrunk deltas from
    independently tested factors. The weights are learned only on older games.
    """
    base = predictions['v1']
    rec = predictions['recency']
    opp = predictions['opponent']
    ctx = predictions['context']
    wr, wo, wc = weights['recency'], weights['opponent'], weights['context']

    def mix(key, lo, hi):
        value = (base[key]
                 + wr * (rec[key] - base[key])
                 + wo * (opp[key] - base[key])
                 + wc * (ctx[key] - base[key]))
        return _clip(value, lo, hi)

    return {
        'home_shots': mix('home_shots', 10.0, 50.0),
        'away_shots': mix('away_shots', 10.0, 50.0),
        'home_goals': mix('home_goals', 0.2, 8.0),
        'away_goals': mix('away_goals', 0.2, 8.0),
        'home_history': base.get('home_history'),
        'away_history': base.get('away_history'),
    }


def _line_pick_row(expected, actual, line, max_n=135):
    p_over = _over(_poisson(expected, max_n), line)
    choose_over = p_over >= .5
    conf = p_over if choose_over else 1.0 - p_over
    actual_over = actual > line
    return {'p': conf, 'y': 1 if choose_over == actual_over else 0,
            'side': 'over' if choose_over else 'under',
            'actual': 'over' if actual_over else 'under'}


def _reference_predictions(prediction, row):
    """Reference-market prediction rows used for tuning the global weights.
    These are intentionally the same reference markets used in the dashboard.
    """
    hs, aas = row['hs'], row['aas']
    hg, ag = row['hg'], row['ag']
    regulation = row.get('regulation')
    out = {'h2h': [], 'team': [], 'total': [], 'goals': [], 'winner': []}

    sh = _poisson(prediction['home_shots'])
    sa = _poisson(prediction['away_shots'])
    ph, pt, pa = _h2h(sh, sa)
    pick_home = ph >= pa
    out['h2h'].append({'p': ph if pick_home else pa,
                       'y': 1 if (hs > aas if pick_home else aas > hs) else 0})

    out['team'].append(_line_pick_row(prediction['home_shots'], hs, 27.5))
    out['team'].append(_line_pick_row(prediction['away_shots'], aas, 27.5))
    out['total'].append(_line_pick_row(prediction['home_shots'] + prediction['away_shots'], hs + aas, 56.5))
    out['goals'].append(_line_pick_row(prediction['home_goals'] + prediction['away_goals'], hg + ag, 5.5, 55))

    if regulation:
        gh = _poisson(prediction['home_goals'], 45)
        ga = _poisson(prediction['away_goals'], 45)
        wh, wt, wa = _h2h(gh, ga)
        probs = {'home': wh, 'draw': wt, 'away': wa}
        pick = max(probs, key=probs.get)
        out['winner'].append({'p': probs[pick], 'y': 1 if pick == regulation else 0})
    return out


def _collect_reference(rows, weights=None, use_v1=False):
    out = {'h2h': [], 'team': [], 'total': [], 'goals': [], 'winner': []}
    for row in rows:
        prediction = row['predictions']['v1'] if use_v1 else _blend_candidate_prediction(row['predictions'], weights)
        one = _reference_predictions(prediction, row)
        for market in out:
            out[market].extend(one[market])
    return out


def _market_brier(predictions):
    if not predictions:
        return None
    return sum((x['p'] - x['y']) ** 2 for x in predictions) / len(predictions)


def _weight_objective(rows, weights):
    markets = _collect_reference(rows, weights=weights)
    briers = [_market_brier(markets[k]) for k in ('h2h', 'team', 'total', 'goals', 'winner')]
    briers = [x for x in briers if x is not None]
    if not briers:
        return 999.0, {}
    # Small regularisation keeps the search conservative when two settings are similar.
    penalty = 0.0005 * sum(float(weights[k]) ** 2 for k in ('recency', 'opponent', 'context'))
    return sum(briers) / len(briers) + penalty, {
        k: _market_brier(markets[k]) for k in markets
    }


def _fit_affine(train):
    if len(train) < 12:
        return None
    mean_p = sum(x['p'] for x in train) / len(train)
    mean_y = sum(x['y'] for x in train) / len(train)
    best = None
    for i in range(31):
        slope = i / 20.0
        intercept = mean_y - slope * mean_p
        vals = [_clip(intercept + slope * x['p'], .01, .99) for x in train]
        brier = sum((p - x['y']) ** 2 for p, x in zip(vals, train)) / len(train)
        if best is None or brier < best[0]:
            best = (brier, slope, intercept)
    return {'slope': best[1], 'intercept': best[2], 'train_brier': best[0]}


def _heldout_calibration(train_markets, test_markets):
    result = {}
    for market in ('h2h', 'team', 'total', 'goals', 'winner'):
        train, test = train_markets[market], test_markets[market]
        fit = _fit_affine(train)
        if not fit or len(test) < 8:
            result[market] = {'available': False, 'reason': 'Málo dat v train/test části'}
            continue
        raw = [x['p'] for x in test]
        calibrated = [_clip(fit['intercept'] + fit['slope'] * p, .01, .99) for p in raw]
        raw_brier = sum((p - x['y']) ** 2 for p, x in zip(raw, test)) / len(test)
        cal_brier = sum((p - x['y']) ** 2 for p, x in zip(calibrated, test)) / len(test)
        result[market] = {
            'available': True, 'train_n': len(train), 'test_n': len(test),
            'slope': fit['slope'], 'intercept': fit['intercept'],
            'raw_brier': raw_brier, 'calibrated_brier': cal_brier,
            'brier_gain': raw_brier - cal_brier,
            'raw_avg_probability': sum(raw) / len(raw),
            'calibrated_avg_probability': sum(calibrated) / len(calibrated),
            'actual_rate': sum(x['y'] for x in test) / len(test),
        }
    return result


def _model3_train_test(tuning_rows, empty_markets, empty_lines):
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 50:
        return {'available': False, 'reason': 'Model 3 potřebuje alespoň 50 vyhodnocených zápasů.'}
    split = max(30, int(n * .60))
    split = min(split, n - 20)
    train, test = ordered[:split], ordered[split:]

    best = None
    recency_grid = (0.0, .25, .50, .75, 1.0)
    opponent_grid = (0.0, .10, .20, .30)
    context_grid = (0.0, .10, .20, .30)
    tested = 0
    for wr in recency_grid:
        for wo in opponent_grid:
            for wc in context_grid:
                weights = {'recency': wr, 'opponent': wo, 'context': wc}
                objective, by_market = _weight_objective(train, weights)
                tested += 1
                if best is None or objective < best['objective']:
                    best = {'weights': weights, 'objective': objective, 'train_briers': by_market}

    weights = best['weights']
    train_m3 = _collect_reference(train, weights=weights)
    test_m3 = _collect_reference(test, weights=weights)
    test_v1 = _collect_reference(test, use_v1=True)
    calibration = _heldout_calibration(train_m3, test_m3)

    # Full line tables on the holdout only. This prevents tuned weights from being
    # judged on the same outcomes that selected them.
    v1_markets, m3_markets = empty_markets(), empty_markets()
    v1_lines, m3_lines = empty_lines(), empty_lines()
    rows_out = []
    for row in test:
        p1 = row['predictions']['v1']
        p3 = _blend_candidate_prediction(row['predictions'], weights)
        e1 = _evaluate_model_prediction(p1, row['hs'], row['aas'], row['hg'], row['ag'], row['regulation'], v1_markets, v1_lines)
        e3 = _evaluate_model_prediction(p3, row['hs'], row['aas'], row['hg'], row['ag'], row['regulation'], m3_markets, m3_lines)
        rows_out.append({
            'date': row['date'], 'matchup': row['matchup'],
            'actual_shots': f"{int(row['hs'])}:{int(row['aas'])}",
            'expected_shots_v1': f"{p1['home_shots']:.1f}:{p1['away_shots']:.1f}",
            'expected_shots_m3': f"{p3['home_shots']:.1f}:{p3['away_shots']:.1f}",
            'h2h_pick_v1': row['home'] if e1['pick_home'] else row['away'],
            'h2h_probability_v1': e1['h2h_conf'], 'h2h_correct_v1': bool(e1['h2h_ok']),
            'h2h_pick_m3': row['home'] if e3['pick_home'] else row['away'],
            'h2h_probability_m3': e3['h2h_conf'], 'h2h_correct_m3': bool(e3['h2h_ok']),
        })

    v1_summary, m3_summary = _summaries(v1_markets), _summaries(m3_markets)
    holdout_compare = {}
    for market in ('h2h', 'team', 'total', 'goals', 'winner'):
        a, b = v1_summary[market], m3_summary[market]
        holdout_compare[market] = {
            'label': b['label'], 'n': min(a['n'], b['n']),
            'accuracy_v1': a['accuracy'], 'accuracy_m3': b['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or b['accuracy'] is None else b['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_m3': b['brier'],
            'brier_gain': None if a['brier'] is None or b['brier'] is None else a['brier'] - b['brier'],
        }

    train_dates = [x['date'] for x in train]
    test_dates = [x['date'] for x in test]
    return {
        'available': True, 'grid_tested': tested, 'weights': weights,
        'objective': best['objective'], 'train_briers': best['train_briers'],
        'train_n': len(train), 'test_n': len(test),
        'train_from': min(train_dates), 'train_to': max(train_dates),
        'test_from': min(test_dates), 'test_to': max(test_dates),
        'holdout_compare': holdout_compare,
        'calibration': calibration,
        'holdout_models': {
            'v1': {'markets': v1_summary, 'lines': _line_summaries(v1_lines)},
            'm3': {'markets': m3_summary, 'lines': _line_summaries(m3_lines)},
        },
        'holdout_games': list(reversed(rows_out)),
    }



def _market_prediction_rows(prediction, row, market):
    """Return only the reference prediction(s) needed by one market.

    0.16 tunes each betting market independently, so we intentionally avoid
    forcing one set of factor weights onto unrelated markets.
    """
    hs, aas = row['hs'], row['aas']
    hg, ag = row['hg'], row['ag']
    regulation = row.get('regulation')

    if market == 'h2h':
        sh = _poisson(prediction['home_shots'])
        sa = _poisson(prediction['away_shots'])
        ph, _, pa = _h2h(sh, sa)
        pick_home = ph >= pa
        return [{'p': ph if pick_home else pa,
                 'y': 1 if (hs > aas if pick_home else aas > hs) else 0}]
    if market == 'team':
        return [
            _line_pick_row(prediction['home_shots'], hs, 27.5),
            _line_pick_row(prediction['away_shots'], aas, 27.5),
        ]
    if market == 'total':
        return [_line_pick_row(prediction['home_shots'] + prediction['away_shots'], hs + aas, 56.5)]
    if market == 'goals':
        return [_line_pick_row(prediction['home_goals'] + prediction['away_goals'], hg + ag, 5.5, 55)]
    if market == 'winner' and regulation:
        gh = _poisson(prediction['home_goals'], 45)
        ga = _poisson(prediction['away_goals'], 45)
        wh, wt, wa = _h2h(gh, ga)
        probs = {'home': wh, 'draw': wt, 'away': wa}
        pick = max(probs, key=probs.get)
        return [{'p': probs[pick], 'y': 1 if pick == regulation else 0}]
    return []


def _collect_one_market(rows, market, weights=None, use_v1=False):
    out = []
    for row in rows:
        prediction = row['predictions']['v1'] if use_v1 else _blend_candidate_prediction(row['predictions'], weights)
        out.extend(_market_prediction_rows(prediction, row, market))
    return out


def _market_weight_objective(rows, market, weights):
    predictions = _collect_one_market(rows, market, weights=weights)
    brier = _market_brier(predictions)
    if brier is None:
        return 999.0, None
    # Conservative shrinkage: larger deviations from Model 1 need to earn their place.
    penalty = 0.0007 * sum(float(weights[k]) ** 2 for k in ('recency', 'opponent', 'context'))
    return brier + penalty, brier


def _fill_market_specific_model(row, weights_by_market, markets, line_buckets):
    """Evaluate one holdout game using a different factor mix for each market."""
    hs, aas, hg, ag = row['hs'], row['aas'], row['hg'], row['ag']

    p_h2h = _blend_candidate_prediction(row['predictions'], weights_by_market['h2h'])
    markets['h2h'].extend(_market_prediction_rows(p_h2h, row, 'h2h'))
    sh = _poisson(p_h2h['home_shots'])
    sa = _poisson(p_h2h['away_shots'])
    ph, _, pa = _h2h(sh, sa)
    pick_home = ph >= pa
    h2h_conf = ph if pick_home else pa
    h2h_ok = hs > aas if pick_home else aas > hs

    p_team = _blend_candidate_prediction(row['predictions'], weights_by_market['team'])
    for line in TEAM_SHOT_LINES:
        _record_line(line_buckets['team'][line], p_team['home_shots'], hs, line)
        _record_line(line_buckets['team'][line], p_team['away_shots'], aas, line)
    markets['team'].extend(line_buckets['team'][27.5][-2:])

    p_total = _blend_candidate_prediction(row['predictions'], weights_by_market['total'])
    total_expected = p_total['home_shots'] + p_total['away_shots']
    for line in TOTAL_SHOT_LINES:
        _record_line(line_buckets['total'][line], total_expected, hs + aas, line)
    markets['total'].append(line_buckets['total'][56.5][-1])

    p_goals = _blend_candidate_prediction(row['predictions'], weights_by_market['goals'])
    goal_expected = p_goals['home_goals'] + p_goals['away_goals']
    for line in GOAL_LINES:
        _record_line(line_buckets['goals'][line], goal_expected, hg + ag, line, 55)
    markets['goals'].append(line_buckets['goals'][5.5][-1])

    p_winner = _blend_candidate_prediction(row['predictions'], weights_by_market['winner'])
    markets['winner'].extend(_market_prediction_rows(p_winner, row, 'winner'))

    return {
        'pick_home': pick_home,
        'h2h_conf': h2h_conf,
        'h2h_ok': h2h_ok,
        'h2h_prediction': p_h2h,
    }


def _model4_train_test(tuning_rows, empty_markets, empty_lines):
    """Market-specific Model 4.

    Each market selects its own recency/opponent/context weights on the older
    60% of games. The selected weights are then frozen and evaluated only on
    the newer 40% holdout.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 60:
        return {'available': False, 'reason': 'Model 4 potřebuje alespoň 60 vyhodnocených zápasů.'}
    split = max(36, int(n * .60))
    split = min(split, n - 24)
    train, test = ordered[:split], ordered[split:]

    recency_grid = (0.0, .25, .50, .75, 1.0)
    opponent_grid = (0.0, .10, .20, .30, .40)
    context_grid = (0.0, .10, .20, .30)
    weights_by_market = {}
    train_details = {}
    total_tested = 0

    for market in order:
        baseline_rows = _collect_one_market(train, market, use_v1=True)
        baseline_brier = _market_brier(baseline_rows)
        best = None
        tested = 0
        for wr in recency_grid:
            for wo in opponent_grid:
                for wc in context_grid:
                    if wr + wo + wc > 1.0 + 1e-9:
                        continue
                    weights = {'recency': wr, 'opponent': wo, 'context': wc}
                    objective, raw_brier = _market_weight_objective(train, market, weights)
                    tested += 1
                    if best is None or objective < best['objective']:
                        best = {'weights': weights, 'objective': objective, 'brier': raw_brier}
        total_tested += tested
        weights_by_market[market] = best['weights']
        train_details[market] = {
            'weights': best['weights'], 'grid_tested': tested,
            'train_brier_v1': baseline_brier, 'train_brier_m4': best['brier'],
            'train_brier_gain': None if baseline_brier is None or best['brier'] is None else baseline_brier - best['brier'],
            'objective': best['objective'],
        }

    train_m4 = {m: _collect_one_market(train, m, weights=weights_by_market[m]) for m in order}
    test_m4 = {m: _collect_one_market(test, m, weights=weights_by_market[m]) for m in order}
    calibration = _heldout_calibration(train_m4, test_m4)

    v1_markets, m4_markets = empty_markets(), empty_markets()
    v1_lines, m4_lines = empty_lines(), empty_lines()
    rows_out = []
    for row in test:
        p1 = row['predictions']['v1']
        e1 = _evaluate_model_prediction(
            p1, row['hs'], row['aas'], row['hg'], row['ag'], row['regulation'], v1_markets, v1_lines
        )
        e4 = _fill_market_specific_model(row, weights_by_market, m4_markets, m4_lines)
        p4 = e4['h2h_prediction']
        rows_out.append({
            'date': row['date'], 'matchup': row['matchup'],
            'actual_shots': f"{int(row['hs'])}:{int(row['aas'])}",
            'expected_shots_v1': f"{p1['home_shots']:.1f}:{p1['away_shots']:.1f}",
            'expected_shots_m4_h2h': f"{p4['home_shots']:.1f}:{p4['away_shots']:.1f}",
            'h2h_pick_v1': row['home'] if e1['pick_home'] else row['away'],
            'h2h_probability_v1': e1['h2h_conf'], 'h2h_correct_v1': bool(e1['h2h_ok']),
            'h2h_pick_m4': row['home'] if e4['pick_home'] else row['away'],
            'h2h_probability_m4': e4['h2h_conf'], 'h2h_correct_m4': bool(e4['h2h_ok']),
        })

    v1_summary, m4_summary = _summaries(v1_markets), _summaries(m4_markets)
    holdout_compare = {}
    for market in order:
        a, b = v1_summary[market], m4_summary[market]
        holdout_compare[market] = {
            'label': b['label'], 'n': min(a['n'], b['n']),
            'accuracy_v1': a['accuracy'], 'accuracy_m4': b['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or b['accuracy'] is None else b['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_m4': b['brier'],
            'brier_gain': None if a['brier'] is None or b['brier'] is None else a['brier'] - b['brier'],
        }
        train_details[market].update({
            'label': b['label'],
            'holdout_brier_v1': a['brier'], 'holdout_brier_m4': b['brier'],
            'holdout_brier_gain': None if a['brier'] is None or b['brier'] is None else a['brier'] - b['brier'],
        })

    train_dates = [x['date'] for x in train]
    test_dates = [x['date'] for x in test]
    return {
        'available': True,
        'train_n': len(train), 'test_n': len(test),
        'train_from': min(train_dates), 'train_to': max(train_dates),
        'test_from': min(test_dates), 'test_to': max(test_dates),
        'weights_by_market': weights_by_market,
        'market_details': train_details,
        'grid_tested_total': total_tested,
        'holdout_compare': holdout_compare,
        'calibration': calibration,
        'holdout_models': {
            'v1': {'markets': v1_summary, 'lines': _line_summaries(v1_lines)},
            'm4': {'markets': m4_summary, 'lines': _line_summaries(m4_lines)},
        },
        'holdout_games': list(reversed(rows_out)),
    }



def _apply_affine(rows, fit):
    if not fit:
        return [dict(x) for x in rows]
    out = []
    for row in rows:
        copy = dict(row)
        copy['p'] = _clip(fit['intercept'] + fit['slope'] * row['p'], .01, .99)
        out.append(copy)
    return out


def _model5_select(tuning_rows, empty_markets, empty_lines):
    """0.16 selective model with a true untouched final test.

    Older 50%: tune market-specific factor weights.
    Middle 25%: decide whether Model 4 is allowed to replace Model 1 and whether
    calibration is allowed for that market.
    Newest 25%: final test that is never used for choosing weights, model source,
    or calibration.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 100:
        return {'available': False, 'reason': 'Model 5 Select potřebuje alespoň 100 vyhodnocených zápasů.'}

    train_end = max(50, int(n * .50))
    val_end = max(train_end + 25, int(n * .75))
    val_end = min(val_end, n - 25)
    train, validation, test = ordered[:train_end], ordered[train_end:val_end], ordered[val_end:]

    recency_grid = (0.0, .25, .50, .75, 1.0)
    opponent_grid = (0.0, .10, .20, .30, .40)
    context_grid = (0.0, .10, .20, .30)
    min_model_gain = 0.0005
    min_cal_gain = 0.0005

    decisions = {}
    weights_by_market = {}
    selected_source = {}
    calibration_fit_final = {}
    total_tested = 0

    for market in order:
        train_v1 = _collect_one_market(train, market, use_v1=True)
        val_v1 = _collect_one_market(validation, market, use_v1=True)
        train_brier_v1 = _market_brier(train_v1)
        val_brier_v1 = _market_brier(val_v1)

        best = None
        tested = 0
        for wr in recency_grid:
            for wo in opponent_grid:
                for wc in context_grid:
                    if wr + wo + wc > 1.0 + 1e-9:
                        continue
                    weights = {'recency': wr, 'opponent': wo, 'context': wc}
                    objective, raw_brier = _market_weight_objective(train, market, weights)
                    tested += 1
                    if best is None or objective < best['objective']:
                        best = {'weights': weights, 'objective': objective, 'brier': raw_brier}
        total_tested += tested

        candidate_weights = best['weights']
        train_m4 = _collect_one_market(train, market, weights=candidate_weights)
        val_m4 = _collect_one_market(validation, market, weights=candidate_weights)
        val_brier_m4 = _market_brier(val_m4)
        validation_gain = None if val_brier_v1 is None or val_brier_m4 is None else val_brier_v1 - val_brier_m4
        train_gain = None if train_brier_v1 is None or best['brier'] is None else train_brier_v1 - best['brier']

        use_m4 = bool(validation_gain is not None and validation_gain >= min_model_gain and
                      train_gain is not None and train_gain > 0)
        if use_m4:
            source = 'm4'
            chosen_weights = candidate_weights
            sel_train, sel_val = train_m4, val_m4
        else:
            source = 'v1'
            chosen_weights = {'recency': 0.0, 'opponent': 0.0, 'context': 0.0}
            sel_train, sel_val = train_v1, val_v1

        # Calibration is itself selected only on the middle validation block.
        fit_train = _fit_affine(sel_train)
        cal_validation_gain = None
        use_calibration = False
        if fit_train and len(sel_val) >= 8:
            raw_val_brier = _market_brier(sel_val)
            cal_val_rows = _apply_affine(sel_val, fit_train)
            cal_val_brier = _market_brier(cal_val_rows)
            if raw_val_brier is not None and cal_val_brier is not None:
                cal_validation_gain = raw_val_brier - cal_val_brier
                use_calibration = cal_validation_gain >= min_cal_gain

        # Once calibration is approved on validation, refit it on train+validation.
        if use_calibration:
            fit_final = _fit_affine(sel_train + sel_val)
        else:
            fit_final = None

        selected_source[market] = source
        weights_by_market[market] = chosen_weights
        calibration_fit_final[market] = fit_final
        decisions[market] = {
            'weights': chosen_weights,
            'candidate_weights': candidate_weights,
            'grid_tested': tested,
            'train_brier_v1': train_brier_v1,
            'train_brier_candidate': best['brier'],
            'train_brier_gain': train_gain,
            'validation_brier_v1': val_brier_v1,
            'validation_brier_candidate': val_brier_m4,
            'validation_brier_gain': validation_gain,
            'selected_source': source,
            'calibration_enabled': bool(use_calibration),
            'calibration_validation_gain': cal_validation_gain,
            'calibration_slope': None if not fit_final else fit_final['slope'],
            'calibration_intercept': None if not fit_final else fit_final['intercept'],
        }

    # Final untouched test: Model 1 baseline, selected raw model, and selected+calibrated.
    v1_test_markets = {m: _collect_one_market(test, m, use_v1=True) for m in order}
    sel_test_raw = {}
    sel_test_final = {}
    for market in order:
        if selected_source[market] == 'm4':
            raw_rows = _collect_one_market(test, market, weights=weights_by_market[market])
        else:
            raw_rows = _collect_one_market(test, market, use_v1=True)
        sel_test_raw[market] = raw_rows
        sel_test_final[market] = _apply_affine(raw_rows, calibration_fit_final[market])

    def summary_map(store):
        labels = {
            'h2h': ('Více střel', '3cestný trh; shoda = prohra týmového výběru'),
            'team': ('Střely týmu ±', 'referenční hranice 27,5'),
            'total': ('Celkové střely ±', 'referenční hranice 56,5'),
            'goals': ('Góly ±', 'referenční hranice 5,5'),
            'winner': ('Vítěz 1/X/2', 'výsledek po 60 minutách'),
        }
        return {m: _metric_summary(store[m], labels[m][0], labels[m][1]) for m in order}

    v1_summary = summary_map(v1_test_markets)
    raw_summary = summary_map(sel_test_raw)
    final_summary = summary_map(sel_test_final)

    final_compare = {}
    for market in order:
        a, r, f = v1_summary[market], raw_summary[market], final_summary[market]
        decisions[market].update({
            'label': f['label'],
            'final_test_brier_v1': a['brier'],
            'final_test_brier_raw': r['brier'],
            'final_test_brier': f['brier'],
            'final_test_brier_gain': None if a['brier'] is None or f['brier'] is None else a['brier'] - f['brier'],
            'final_test_accuracy_v1': a['accuracy'],
            'final_test_accuracy': f['accuracy'],
        })
        final_compare[market] = {
            'label': f['label'], 'n': min(a['n'], f['n']),
            'selected_source': selected_source[market],
            'calibration_enabled': bool(calibration_fit_final[market]),
            'accuracy_v1': a['accuracy'], 'accuracy_select': f['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or f['accuracy'] is None else f['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_raw_select': r['brier'], 'brier_select': f['brier'],
            'brier_gain': None if a['brier'] is None or f['brier'] is None else a['brier'] - f['brier'],
        }

    # Line tests use the selected raw market model. Calibration is not borrowed across lines.
    v1_lines, sel_lines = empty_lines(), empty_lines()
    dummy_v1, dummy_sel = empty_markets(), empty_markets()
    rows_out = []
    for row in test:
        p1 = row['predictions']['v1']
        e1 = _evaluate_model_prediction(p1, row['hs'], row['aas'], row['hg'], row['ag'], row['regulation'], dummy_v1, v1_lines)
        e_sel = _fill_market_specific_model(row, weights_by_market, dummy_sel, sel_lines)
        conf = e_sel['h2h_conf']
        fit = calibration_fit_final['h2h']
        if fit:
            conf = _clip(fit['intercept'] + fit['slope'] * conf, .01, .99)
        psel = e_sel['h2h_prediction']
        rows_out.append({
            'date': row['date'], 'matchup': row['matchup'],
            'actual_shots': f"{int(row['hs'])}:{int(row['aas'])}",
            'expected_shots_v1': f"{p1['home_shots']:.1f}:{p1['away_shots']:.1f}",
            'expected_shots_select_h2h': f"{psel['home_shots']:.1f}:{psel['away_shots']:.1f}",
            'h2h_pick_v1': row['home'] if e1['pick_home'] else row['away'],
            'h2h_probability_v1': e1['h2h_conf'], 'h2h_correct_v1': bool(e1['h2h_ok']),
            'h2h_pick_select': row['home'] if e_sel['pick_home'] else row['away'],
            'h2h_probability_select': conf, 'h2h_correct_select': bool(e_sel['h2h_ok']),
        })

    dates = lambda rows: [x['date'] for x in rows]
    return {
        'available': True,
        'train_n': len(train), 'validation_n': len(validation), 'test_n': len(test),
        'train_from': min(dates(train)), 'train_to': max(dates(train)),
        'validation_from': min(dates(validation)), 'validation_to': max(dates(validation)),
        'test_from': min(dates(test)), 'test_to': max(dates(test)),
        'min_model_gain': min_model_gain, 'min_calibration_gain': min_cal_gain,
        'decisions': decisions,
        'grid_tested_total': total_tested,
        'final_compare': final_compare,
        'final_models': {
            'v1': {'markets': v1_summary, 'lines': _line_summaries(v1_lines)},
            'select': {'markets': final_summary, 'raw_markets': raw_summary, 'lines': _line_summaries(sel_lines)},
        },
        'final_games': list(reversed(rows_out)),
    }

def run_backtest(team, season_start, game_limit=10):
    started = time.time()
    targets = _prepare_backtest_targets(team, season_start, game_limit)
    _prefetch_backtest_histories(targets)

    def empty_markets():
        return {'h2h': [], 'team': [], 'total': [], 'goals': [], 'winner': []}

    def empty_lines():
        return {
            'team': {line: [] for line in TEAM_SHOT_LINES},
            'total': {line: [] for line in TOTAL_SHOT_LINES},
            'goals': {line: [] for line in GOAL_LINES},
        }

    specs = {
        'v1': ('Model 1', 'Původní auto forma 55/25/20 + 20% doma/venku', _predict_game),
        'recency': ('Jen rychlejší forma', 'Pouze exponenciální recency (half-life 8); ostatní logika Modelu 1', _predict_game_recency),
        'opponent': ('Jen síla soupeře', 'Pouze silnější attack-vs-defense normalizace; původní váhy formy', _predict_game_opponent),
        'context': ('Jen B2B / odpočinek / cestování', 'Model 1 + data-derived kontext bez změny formy a soupeře', _predict_game_context),
        'full': ('Full kandidát', 'Rychlejší forma + síla soupeře + B2B/odpočinek/cestování', _predict_game_v2),
    }
    stores = {key: {'markets': empty_markets(), 'lines': empty_lines()} for key in specs}
    game_rows, factor_rows, tuning_rows = [], [], []
    skipped = 0

    for game in targets:
        home = (game.get('homeTeam') or {}).get('abbrev')
        away = (game.get('awayTeam') or {}).get('abbrev')
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        start_time = game.get('startTimeUTC') or (date + 'T00:00:00Z' if date else '')
        game_id = _game_id(game)
        if not home or not away or not date or not game_id:
            skipped += 1
            continue

        predictions = {key: predictor(home, away, date) for key, (_, _, predictor) in specs.items()}
        if any(p is None for p in predictions.values()):
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

        regulation = _regulation_result(game_id)
        evals = {}
        for key, prediction in predictions.items():
            st = stores[key]
            evals[key] = _evaluate_model_prediction(
                prediction, hs, aas, hg, ag, regulation, st['markets'], st['lines']
            )

        tuning_rows.append({
            'date': date, 'sort_key': start_time, 'home': home, 'away': away,
            'matchup': f'{away} @ {home}', 'hs': hs, 'aas': aas, 'hg': hg, 'ag': ag,
            'regulation': regulation, 'predictions': predictions,
        })
        f = predictions['full'].get('factors') or {}
        factor_rows.append(f)
        game_rows.append({
            'date': date, 'matchup': f'{away} @ {home}',
            'actual_shots': f'{int(hs)}:{int(aas)}',
            'expected_shots_v1': f"{predictions['v1']['home_shots']:.1f}:{predictions['v1']['away_shots']:.1f}",
            'expected_shots_v2': f"{predictions['full']['home_shots']:.1f}:{predictions['full']['away_shots']:.1f}",
            'h2h_pick_v1': home if evals['v1']['pick_home'] else away,
            'h2h_probability_v1': evals['v1']['h2h_conf'], 'h2h_correct_v1': bool(evals['v1']['h2h_ok']),
            'h2h_pick_v2': home if evals['full']['pick_home'] else away,
            'h2h_probability_v2': evals['full']['h2h_conf'], 'h2h_correct_v2': bool(evals['full']['h2h_ok']),
            'home_b2b': f.get('home_b2b'), 'away_b2b': f.get('away_b2b'),
            'home_travel_km': f.get('home_travel_km'), 'away_travel_km': f.get('away_travel_km'),
        })

    models = {}
    for key, (label, description, _) in specs.items():
        models[key] = {
            'label': label, 'description': description,
            'markets': _summaries(stores[key]['markets']),
            'lines': _line_summaries(stores[key]['lines']),
        }

    order = ('h2h', 'team', 'total', 'goals', 'winner')
    ablation = {}
    for market in order:
        base = models['v1']['markets'][market]
        rows = []
        for key in specs:
            m = models[key]['markets'][market]
            rows.append({
                'key': key, 'label': models[key]['label'], 'n': m['n'],
                'accuracy': m['accuracy'], 'brier': m['brier'],
                'accuracy_delta': None if base['accuracy'] is None or m['accuracy'] is None else m['accuracy'] - base['accuracy'],
                'brier_gain': None if base['brier'] is None or m['brier'] is None else base['brier'] - m['brier'],
            })
        valid = [r for r in rows if r['brier'] is not None]
        best = min(valid, key=lambda r: r['brier']) if valid else None
        ablation[market] = {
            'label': base['label'], 'rows': rows,
            'best_model': best['key'] if best else None,
            'best_label': best['label'] if best else None,
            'best_brier_gain': best['brier_gain'] if best else None,
        }

    comparison = {}
    for market in order:
        a, b = models['v1']['markets'][market], models['full']['markets'][market]
        comparison[market] = {
            'label': b['label'], 'n': min(a['n'], b['n']),
            'accuracy_v1': a['accuracy'], 'accuracy_v2': b['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or b['accuracy'] is None else b['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_v2': b['brier'],
            'brier_gain': None if a['brier'] is None or b['brier'] is None else a['brier'] - b['brier'],
            'reference': b['reference'],
        }

    calibration_oos = {
        market: _chronological_calibration(stores['full']['markets'][market])
        for market in order
    }

    factor_summary = {}
    for key, label in (
        ('home_context_shots', 'Kontext střel domácí'), ('away_context_shots', 'Kontext střel hosté'),
        ('home_context_goals', 'Kontext gólů domácí'), ('away_context_goals', 'Kontext gólů hosté'),
    ):
        vals = [float(x[key]) for x in factor_rows if x.get(key) is not None]
        factor_summary[key] = {'label': label, 'mean': sum(vals)/len(vals) if vals else None,
                               'mean_abs': sum(abs(v) for v in vals)/len(vals) if vals else None}
    factor_summary['b2b_team_games'] = sum(bool(x.get('home_b2b')) + bool(x.get('away_b2b')) for x in factor_rows)
    travel_vals = [float(v) for x in factor_rows for v in (x.get('home_travel_km'), x.get('away_travel_km')) if v is not None]
    factor_summary['avg_travel_km'] = sum(travel_vals)/len(travel_vals) if travel_vals else None

    model4 = _model4_train_test(tuning_rows, empty_markets, empty_lines)
    model5 = _model5_select(tuning_rows, empty_markets, empty_lines)

    return {
        'team': team, 'scope': 'league' if team == 'ALL' else 'team',
        'season': '+'.join(_season_id(x) for x in season_start) if isinstance(season_start, (list, tuple)) else _season_id(season_start),
        'season_label': ' + '.join(f'{x}/{str(x+1)[2:]}' for x in season_start) if isinstance(season_start, (list, tuple)) else f'{season_start}/{str(season_start+1)[2:]}',
        'season_components': [_season_id(x) for x in season_start] if isinstance(season_start, (list, tuple)) else [_season_id(season_start)],
        'requested_games': game_limit,
        'evaluated_games': len(game_rows), 'skipped_games': skipped,
        'elapsed_seconds': round(time.time() - started, 1),
        'models': models, 'comparison': comparison, 'ablation': ablation,
        'calibration_oos': calibration_oos, 'factor_summary': factor_summary,
        'model4': model4, 'model5': model5, 'games': game_rows,
        'warning': ('0.16 přidává Model 5 Select: starších 50 % ladí váhy, prostředních 25 % rozhoduje, zda nový faktor a kalibrace vůbec smějí projít, a nejnovějších 25 % zůstává nedotčený finální test. '
                    'Multi-season režim bere vyvážený vzorek z 2024/25 a 2025/26, takže starší sezóna nemůže být vytlačena novější. Živá Analýza '
                    'stále používá Model 1, dokud výběrový model neprokáže stabilní zlepšení na finálním testu a napříč sezonami. Historické kurzy bookmakerů nejsou součástí backtestu.'),
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
                headers={'User-Agent': 'NHLShotsAnalyzer/0.16', 'Accept': 'application/json'},
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
        if team != 'ALL' and team not in TEAM_NICK:
            return self.respond(400, {'error': 'Neplatný tým pro backtest'})
        if raw_season == 'MULTI':
            start = (2024, 2025)
            games = max(100, min(600 if team == 'ALL' else 120, games))
        else:
            if not re.fullmatch(r'20\d{6}', raw_season):
                return self.respond(400, {'error': 'Neplatná sezóna'})
            start = int(raw_season[:4])
            if raw_season != _season_id(start):
                return self.respond(400, {'error': 'Sezóna musí mít tvar 20252026'})
            games = max(5, min(300 if team == 'ALL' else 60, games))
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
