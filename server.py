"""NHL Team Shots Analyzer 0.12 — static site, NHL API proxy and official NHL.com lineup reader."""
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
        'User-Agent': 'Mozilla/5.0 NHLShotsAnalyzer/0.12',
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
        headers={'User-Agent': 'NHLShotsAnalyzer/0.12', 'Accept': 'application/json'},
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
    selected = _history_games_before(team, game_date, n)
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
    limit_rest = 0.8 if kind == 'shots' else 0.15
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
        if var > 0.15:
            slope = sum((x-mx)*(y-my) for x, y in travel_rows) / var
            slope *= len(travel_rows) / (len(travel_rows) + 20.0)
            slope = _clip(slope, -limit_slope, limit_slope)
            travel_delta = _clip(slope * (target_km/1000.0 - mx), -limit_travel, limit_travel)

    return {'total': b2b_delta + rest_delta + travel_delta, 'b2b': b2b_delta,
            'rest': rest_delta, 'travel': travel_delta}


def _predict_game_v2(home, away, game_date):
    """Experimental Model 2.
    Adds exponential form, additive opponent-strength normalization, and data-derived B2B/travel adjustments.
    It uses only information available before the target game.
    """
    rh = _history_before(home, game_date, 30)
    ra = _history_before(away, game_date, 30)
    if len(rh) < 10 or len(ra) < 10:
        return None

    # Dynamic local baselines from both teams' pre-game histories.
    h_sh, h_al = _exp_weighted(rh, 'shots'), _exp_weighted(rh, 'allowed')
    a_sh, a_al = _exp_weighted(ra, 'shots'), _exp_weighted(ra, 'allowed')
    shot_base = (h_sh + h_al + a_sh + a_al) / 4.0
    h_g, h_ga = _exp_weighted(rh, 'goals'), _exp_weighted(rh, 'ga')
    a_g, a_ga = _exp_weighted(ra, 'goals'), _exp_weighted(ra, 'ga')
    goal_base = (h_g + h_ga + a_g + a_ga) / 4.0

    # Additive attack + opponent-defense model, normalized around the local baseline.
    eh = h_sh + a_al - shot_base
    ea = a_sh + h_al - shot_base
    gh = h_g + a_ga - goal_base
    ga = a_g + h_ga - goal_base

    # Keep the existing venue information, but only as the same modest 20% blend used by Model 1.
    eh = _blend(eh, _venue_mean(rh, 'shots', True))
    ea = _blend(ea, _venue_mean(ra, 'shots', False))
    gh = _blend(gh, _venue_mean(rh, 'goals', True))
    ga = _blend(ga, _venue_mean(ra, 'goals', False))

    target_venue = home
    ctx_h = _target_context(rh, target_venue, game_date)
    ctx_a = _target_context(ra, target_venue, game_date)
    adj_hs = _context_adjustment(rh, 'shots', ctx_h, 'shots')
    adj_as = _context_adjustment(ra, 'shots', ctx_a, 'shots')
    adj_hg = _context_adjustment(rh, 'goals', ctx_h, 'goals')
    adj_ag = _context_adjustment(ra, 'goals', ctx_a, 'goals')

    eh = _clip(eh + adj_hs['total'], 10.0, 50.0)
    ea = _clip(ea + adj_as['total'], 10.0, 50.0)
    gh = _clip(gh + adj_hg['total'], 0.2, 8.0)
    ga = _clip(ga + adj_ag['total'], 0.2, 8.0)

    return {
        'home_shots': eh, 'away_shots': ea, 'home_goals': gh, 'away_goals': ga,
        'home_history': len(rh), 'away_history': len(ra),
        'factors': {
            'shot_baseline': shot_base,
            'goal_baseline': goal_base,
            'home_attack_vs_base': h_sh - shot_base,
            'away_attack_vs_base': a_sh - shot_base,
            'home_opp_def_vs_base': a_al - shot_base,
            'away_opp_def_vs_base': h_al - shot_base,
            'home_b2b': ctx_h.get('b2b'), 'away_b2b': ctx_a.get('b2b'),
            'home_free_days': ctx_h.get('free_days'), 'away_free_days': ctx_a.get('free_days'),
            'home_travel_km': ctx_h.get('travel_km'), 'away_travel_km': ctx_a.get('travel_km'),
            'home_context_shots': adj_hs['total'], 'away_context_shots': adj_as['total'],
            'home_context_goals': adj_hg['total'], 'away_context_goals': adj_ag['total'],
        }
    }


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
    if team == 'ALL':
        schedule = _league_schedule(season_start)
    else:
        schedule = [g for g in _team_schedule(team, season_start) if _completed_regular(g)]
    schedule.sort(key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '', reverse=True)
    return schedule[:game_limit]


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

    markets1, markets2 = empty_markets(), empty_markets()
    lines1, lines2 = empty_lines(), empty_lines()
    game_rows, factor_rows = [], []
    skipped = 0

    for game in targets:
        home = (game.get('homeTeam') or {}).get('abbrev')
        away = (game.get('awayTeam') or {}).get('abbrev')
        date = game.get('gameDate') or (game.get('startTimeUTC') or '')[:10]
        game_id = _game_id(game)
        if not home or not away or not date or not game_id:
            skipped += 1
            continue

        p1 = _predict_game(home, away, date)
        p2 = _predict_game_v2(home, away, date)
        if not p1 or not p2:
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
        e1 = _evaluate_model_prediction(p1, hs, aas, hg, ag, regulation, markets1, lines1)
        e2 = _evaluate_model_prediction(p2, hs, aas, hg, ag, regulation, markets2, lines2)

        f = p2.get('factors') or {}
        factor_rows.append(f)
        game_rows.append({
            'date': date,
            'matchup': f'{away} @ {home}',
            'actual_shots': f'{int(hs)}:{int(aas)}',
            'expected_shots_v1': f"{p1['home_shots']:.1f}:{p1['away_shots']:.1f}",
            'expected_shots_v2': f"{p2['home_shots']:.1f}:{p2['away_shots']:.1f}",
            'h2h_pick_v1': home if e1['pick_home'] else away,
            'h2h_probability_v1': e1['h2h_conf'],
            'h2h_correct_v1': bool(e1['h2h_ok']),
            'h2h_pick_v2': home if e2['pick_home'] else away,
            'h2h_probability_v2': e2['h2h_conf'],
            'h2h_correct_v2': bool(e2['h2h_ok']),
            'home_b2b': f.get('home_b2b'), 'away_b2b': f.get('away_b2b'),
            'home_travel_km': f.get('home_travel_km'), 'away_travel_km': f.get('away_travel_km'),
        })

    summaries1, summaries2 = _summaries(markets1), _summaries(markets2)
    line_results1, line_results2 = _line_summaries(lines1), _line_summaries(lines2)
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    comparison = {}
    for key in order:
        a, b = summaries1[key], summaries2[key]
        comparison[key] = {
            'label': b['label'], 'n': min(a['n'], b['n']),
            'accuracy_v1': a['accuracy'], 'accuracy_v2': b['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or b['accuracy'] is None else b['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_v2': b['brier'],
            'brier_gain': None if a['brier'] is None or b['brier'] is None else a['brier'] - b['brier'],
            'reference': b['reference'],
        }

    factor_summary = {}
    for key, label in (
        ('home_context_shots', 'Kontext střel domácí'),
        ('away_context_shots', 'Kontext střel hosté'),
        ('home_context_goals', 'Kontext gólů domácí'),
        ('away_context_goals', 'Kontext gólů hosté'),
    ):
        vals = [float(x[key]) for x in factor_rows if x.get(key) is not None]
        factor_summary[key] = {'label': label, 'mean': sum(vals)/len(vals) if vals else None,
                               'mean_abs': sum(abs(v) for v in vals)/len(vals) if vals else None}
    b2b_count = sum(bool(x.get('home_b2b')) + bool(x.get('away_b2b')) for x in factor_rows)
    travel_vals = [float(v) for x in factor_rows for v in (x.get('home_travel_km'), x.get('away_travel_km')) if v is not None]
    factor_summary['b2b_team_games'] = b2b_count
    factor_summary['avg_travel_km'] = sum(travel_vals)/len(travel_vals) if travel_vals else None

    return {
        'team': team,
        'scope': 'league' if team == 'ALL' else 'team',
        'season': _season_id(season_start),
        'requested_games': game_limit,
        'evaluated_games': len(game_rows),
        'skipped_games': skipped,
        'elapsed_seconds': round(time.time() - started, 1),
        'models': {
            'v1': {
                'label': 'Model 1',
                'description': 'Auto forma 55/25/20 + 20% korekce doma/venku při min. 5 zápasech',
                'markets': summaries1, 'lines': line_results1,
            },
            'v2': {
                'label': 'Model 2 (experimentální)',
                'description': 'Exponenciální forma (half-life 8) + aditivní síla soupeře + data-derived B2B/odpočinek/cestování + stejná venue korekce',
                'markets': summaries2, 'lines': line_results2,
            },
        },
        'comparison': comparison,
        'factor_summary': factor_summary,
        'games': game_rows,
        'warning': ('Model 2 je experimentální kandidát. Korekce B2B a cestování se odhadují pouze z předzápasové historie '
                    'a jsou záměrně shrnuté a omezené; pokud v backtestu nezlepší Brier score/kalibraci, nemají se nasadit '
                    'do hlavní analýzy. Backtest nepoužívá historické bookmakerové kurzy, takže nepočítá historické EV ani ROI.'),
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
                headers={'User-Agent': 'NHLShotsAnalyzer/0.12', 'Accept': 'application/json'},
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
        if not re.fullmatch(r'20\d{6}', raw_season):
            return self.respond(400, {'error': 'Neplatná sezóna'})
        start = int(raw_season[:4])
        if raw_season != _season_id(start):
            return self.respond(400, {'error': 'Sezóna musí mít tvar 20252026'})
        games = max(5, min(100 if team == 'ALL' else 40, games))
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
