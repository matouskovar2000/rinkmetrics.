"""NHL Team Shots Analyzer 0.30 — static site, NHL API proxy and official NHL.com lineup reader."""
import json
import os
import re
import time
import math
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    'DAL':(32.79,-96.81),'DET':(42.34,-83.05),'EDM':(53.55,-113.50),'FLA':(26.16,-80.35),
    'LAK':(34.04,-118.27),'MIN':(44.94,-93.10),'MTL':(45.50,-73.57),'NSH':(36.16,-86.78),
    'NJD':(40.73,-74.17),'NYI':(40.72,-73.59),'NYR':(40.75,-73.99),'OTT':(45.30,-75.93),
    'PHI':(39.90,-75.17),'PIT':(40.44,-79.99),'SEA':(47.62,-122.35),'SJS':(37.33,-121.90),
    'STL':(38.63,-90.21),'TBL':(27.94,-82.45),'TOR':(43.64,-79.38),'UTA':(40.77,-111.90),
    'VAN':(49.28,-123.11),'VGK':(36.10,-115.18),'WSH':(38.90,-77.02),'WPG':(49.89,-97.14),
    'ARI':(33.43,-111.93),
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
_event_cache = {}
_event_cache_lock = threading.Lock()
_box_summary_cache = {}
_box_summary_cache_lock = threading.Lock()

_backtest_jobs = {}
_backtest_jobs_lock = threading.Lock()
_BACKTEST_JOB_TTL = 4 * 3600

# 0.30 production context for the promoted H2H model. These are the untouched
# 2021/22 lockbox results from Model 12. They are descriptive validation
# metadata only: the live probability itself stays the frozen LOCK 5v5+tied
# model probability. We intentionally do not post-hoc remap the probability
# from the same lockbox that approved the model, because that would require a
# new independent calibration test.
H2H_LOCKBOX_BANDS = (
    {'threshold': .80, 'n': 41,  'accuracy': .805, 'ci95_low': .660, 'ci95_high': .898, 'strength': '80 %+ · velmi vysoká, malý vzorek'},
    {'threshold': .75, 'n': 98,  'accuracy': .786, 'ci95_low': .695, 'ci95_high': .855, 'strength': '75 %+ · vysoká, malý vzorek'},
    {'threshold': .70, 'n': 212, 'accuracy': .783, 'ci95_low': .723, 'ci95_high': .833, 'strength': '70 %+ · velmi silná'},
    {'threshold': .65, 'n': 386, 'accuracy': .756, 'ci95_low': .711, 'ci95_high': .797, 'strength': '65 %+ · silná'},
    {'threshold': .60, 'n': 599, 'accuracy': .716, 'ci95_low': .679, 'ci95_high': .751, 'strength': '60 %+ · ověřená'},
)

def _h2h_validation_band(confidence):
    try:
        p = float(confidence)
    except (TypeError, ValueError):
        return None
    for row in H2H_LOCKBOX_BANDS:
        if p >= row['threshold']:
            return dict(row)
    return {
        'threshold': None, 'n': None, 'accuracy': None,
        'ci95_low': None, 'ci95_high': None,
        'strength': 'pod 60 % · standardní jistota',
    }


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
        'User-Agent': 'Mozilla/5.0 NHLShotsAnalyzer/0.41',
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
    """Cached NHL JSON reader with conservative retries for long historical jobs.

    A 2000-game backtest can make thousands of upstream requests. One transient
    429/5xx/timeout must not kill the entire validation run, so retryable errors
    get a short exponential backoff. Historical responses are cached in memory.
    """
    now = time.time()
    with _nhl_cache_lock:
        cached = _nhl_cache.get(path)
        if cached and now - cached[0] < ttl:
            return cached[1]

    retry_codes = {429, 500, 502, 503, 504}
    last_exc = None
    for attempt in range(4):
        try:
            req = Request(
                'https://api-web.nhle.com/v1' + path,
                headers={'User-Agent': 'NHLShotsAnalyzer/0.41', 'Accept': 'application/json'},
            )
            with urlopen(req, timeout=18) as response:
                data = json.loads(response.read(3_000_000).decode('utf-8'))
            with _nhl_cache_lock:
                _nhl_cache[path] = (time.time(), data)
            return data
        except HTTPError as exc:
            last_exc = exc
            if exc.code not in retry_codes or attempt >= 3:
                raise
        except (URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt >= 3:
                raise
        # A little jitter avoids all prefetch workers retrying in lock-step.
        time.sleep((0.7 * (2 ** attempt)) + ((hash(path) & 7) * 0.03))
    if last_exc:
        raise last_exc
    raise URLError('NHL API request failed')


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


def _game_box_summary(game_id):
    """Return a compact boxscore summary and discard the heavy raw document.

    Large multi-season runs used to keep thousands of full boxscore JSON objects
    in RAM. That was the main reason Render could stall/restart before a
    2000-game run finished. We retain only the values needed by the models.
    """
    with _box_summary_cache_lock:
        cached = _box_summary_cache.get(game_id)
    if cached is not None:
        return cached

    path = f'/gamecenter/{game_id}/boxscore'
    box = _nhl_json(path)
    home = box.get('homeTeam') or {}
    away = box.get('awayTeam') or {}
    pbg = box.get('playerByGameStats') or {}
    player_team = {}
    for side, team in (('homeTeam', home), ('awayTeam', away)):
        team_id = team.get('id')
        if not team_id:
            continue
        groups = pbg.get(side) or {}
        for group in ('forwards', 'defense', 'goalies'):
            for player in groups.get(group) or []:
                pid = player.get('playerId')
                if pid:
                    player_team[pid] = team_id

    result = {
        'home': {
            'id': home.get('id'), 'abbrev': home.get('abbrev'),
            'sog': home.get('sog'), 'score': home.get('score'),
        },
        'away': {
            'id': away.get('id'), 'abbrev': away.get('abbrev'),
            'sog': away.get('sog'), 'score': away.get('score'),
        },
        'player_team': player_team,
    }
    with _box_summary_cache_lock:
        _box_summary_cache[game_id] = result
    # Keep compact summaries only. Raw boxscore documents are much larger.
    with _nhl_cache_lock:
        _nhl_cache.pop(path, None)
    return result


def _game_event_summary(game_id):
    """Parse one historical NHL play-by-play feed into team event features.

    The summary is deliberately descriptive, not an xG model. It uses only
    public pre-existing events from that game: shot attempts, unblocked attempts,
    5v5 / power-play attempt counts, score-tied attempts and a simple slot-attempt
    proxy from rink coordinates. Shootout events are ignored.
    """
    with _event_cache_lock:
        cached = _event_cache.get(game_id)
    if cached is not None:
        return cached

    data = _nhl_json(f'/gamecenter/{game_id}/play-by-play')
    home = data.get('homeTeam') or {}
    away = data.get('awayTeam') or {}
    home_id, away_id = home.get('id'), away.get('id')
    home_abbr, away_abbr = home.get('abbrev'), away.get('abbrev')
    if not home_id or not away_id or not home_abbr or not away_abbr:
        result = {'teams': {}, 'coverage': False}
        with _event_cache_lock:
            _event_cache[game_id] = result
        return result

    def blank():
        return {
            'attempts': 0.0, 'unblocked': 0.0, 'sog_events': 0.0,
            'missed': 0.0, 'blocked': 0.0,
            'fivev5_attempts': 0.0, 'pp_attempts': 0.0,
            'tied_attempts': 0.0, 'slot_attempts': 0.0,
            'slot_unblocked': 0.0,
        }

    by_id = {home_id: blank(), away_id: blank()}
    score = {home_id: 0, away_id: 0}
    regulation_score = {home_id: 0, away_id: 0}

    # Player IDs make blocked-shot attribution more robust. The compact
    # boxscore summary stores only player->team mapping instead of retaining
    # the entire historical boxscore document in RAM.
    try:
        player_team = dict((_game_box_summary(game_id) or {}).get('player_team') or {})
    except Exception:
        player_team = {}

    plays = data.get('plays') or []

    def parse_situation(code, shooter_id):
        code = str(code or '')
        if len(code) != 4 or not code.isdigit():
            return None
        away_goalie, away_skaters, home_skaters, home_goalie = map(int, code)
        if shooter_id == home_id:
            return home_skaters, away_skaters, home_goalie, away_goalie
        if shooter_id == away_id:
            return away_skaters, home_skaters, away_goalie, home_goalie
        return None

    for play in plays:
        period = (play.get('periodDescriptor') or {}).get('number')
        if not isinstance(period, int) or period < 1 or period > 4:
            continue
        typ = play.get('typeDescKey')
        if typ not in ('goal', 'shot-on-goal', 'missed-shot', 'blocked-shot'):
            continue
        details = play.get('details') or {}
        owner = details.get('eventOwnerTeamId')
        if owner not in by_id:
            continue

        # Blocked events can be owned by the blocking team. Prefer the
        # shooting player's roster team; otherwise use the opposite owner.
        if typ == 'blocked-shot':
            shooter = player_team.get(details.get('shootingPlayerId'))
            if shooter not in by_id:
                shooter = away_id if owner == home_id else home_id
        else:
            shooter = owner
        stats = by_id[shooter]
        stats['attempts'] += 1.0
        if typ != 'blocked-shot':
            stats['unblocked'] += 1.0
        if typ in ('goal', 'shot-on-goal'):
            stats['sog_events'] += 1.0
        elif typ == 'missed-shot':
            stats['missed'] += 1.0
        elif typ == 'blocked-shot':
            stats['blocked'] += 1.0

        if score[home_id] == score[away_id]:
            stats['tied_attempts'] += 1.0

        situation = parse_situation(play.get('situationCode'), shooter)
        if situation:
            own_skaters, opp_skaters, own_goalie, opp_goalie = situation
            if own_goalie == 1 and opp_goalie == 1 and own_skaters == 5 and opp_skaters == 5:
                stats['fivev5_attempts'] += 1.0
            if own_goalie == 1 and opp_goalie == 1 and own_skaters > opp_skaters:
                stats['pp_attempts'] += 1.0

        x, y = details.get('xCoord'), details.get('yCoord')
        try:
            # Goal line is about 89 ft from centre. abs(x) makes the proxy
            # independent of which end the attacking team used that period.
            dx = max(0.0, 89.0 - abs(float(x)))
            yy = abs(float(y))
            distance = math.hypot(dx, yy)
            in_slot = distance <= 35.0 and yy <= 22.0
        except (TypeError, ValueError):
            in_slot = False
        if in_slot:
            stats['slot_attempts'] += 1.0
            if typ != 'blocked-shot':
                stats['slot_unblocked'] += 1.0

        if typ == 'goal':
            score[shooter] += 1
            if period <= 3:
                regulation_score[shooter] += 1

    home_stats, away_stats = by_id[home_id], by_id[away_id]
    regulation = ('home' if regulation_score[home_id] > regulation_score[away_id]
                  else 'away' if regulation_score[away_id] > regulation_score[home_id] else 'draw')
    result = {
        'coverage': bool(home_stats['attempts'] and away_stats['attempts']),
        'regulation': regulation,
        'teams': {
            home_abbr: dict(home_stats, **{
                'attempts_allowed': away_stats['attempts'],
                'unblocked_allowed': away_stats['unblocked'],
                'fivev5_attempts_allowed': away_stats['fivev5_attempts'],
                'pp_attempts_allowed': away_stats['pp_attempts'],
                'tied_attempts_allowed': away_stats['tied_attempts'],
                'slot_attempts_allowed': away_stats['slot_attempts'],
                'slot_unblocked_allowed': away_stats['slot_unblocked'],
            }),
            away_abbr: dict(away_stats, **{
                'attempts_allowed': home_stats['attempts'],
                'unblocked_allowed': home_stats['unblocked'],
                'fivev5_attempts_allowed': home_stats['fivev5_attempts'],
                'pp_attempts_allowed': home_stats['pp_attempts'],
                'tied_attempts_allowed': home_stats['tied_attempts'],
                'slot_attempts_allowed': home_stats['slot_attempts'],
                'slot_unblocked_allowed': home_stats['slot_unblocked'],
            }),
        },
    }
    with _event_cache_lock:
        _event_cache[game_id] = result
    # Keep the compact event summary, not hundreds of raw PBP events, to stay
    # within Render memory limits during 600-game validation.
    with _nhl_cache_lock:
        _nhl_cache.pop(f'/gamecenter/{game_id}/play-by-play', None)
    return result


def _box_row(game, team):
    game_id = _game_id(game)
    try:
        box = _game_box_summary(game_id)
    except Exception:
        return None
    home_team = (box.get('home') or {}).get('abbrev')
    away_team = (box.get('away') or {}).get('abbrev')
    home = home_team == team
    own = box.get('home') if home else box.get('away')
    opp = box.get('away') if home else box.get('home')
    if not own or not opp or not home_team or not away_team:
        return None
    vals = [own.get('sog'), opp.get('sog'), own.get('score'), opp.get('score')]
    try:
        shots, allowed, goals, ga = [float(x) for x in vals]
    except (TypeError, ValueError):
        return None
    row = {
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
    try:
        event = (_game_event_summary(game_id).get('teams') or {}).get(team) or {}
    except Exception:
        event = {}
    # Safe fallbacks preserve old models even if an old game has incomplete PBP.
    defaults = {
        'attempts': shots, 'attempts_allowed': allowed,
        'unblocked': shots, 'unblocked_allowed': allowed,
        'fivev5_attempts': shots, 'fivev5_attempts_allowed': allowed,
        'pp_attempts': 0.0, 'pp_attempts_allowed': 0.0,
        'tied_attempts': shots, 'tied_attempts_allowed': allowed,
        'slot_attempts': 0.0, 'slot_attempts_allowed': 0.0,
        'slot_unblocked': 0.0, 'slot_unblocked_allowed': 0.0,
    }
    for key, fallback in defaults.items():
        try:
            row[key] = float(event.get(key, fallback))
        except (TypeError, ValueError):
            row[key] = float(fallback)
    row['event_coverage'] = bool(event)
    return row


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
        # A deep 2000-game run creates thousands of team/date windows. Keep a
        # rolling cache only; compact box/event summaries remain cached and make
        # an evicted window cheap to rebuild.
        while len(_history_cache) > 420:
            _history_cache.pop(next(iter(_history_cache)))
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
    limit_travel = 1.5 if kind == 'shots' else 0.30

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


def _profile_inputs(home, away, game_date):
    """Pregame-only profile features used by the 0.17 candidate models."""
    rh = _history_before(home, game_date, 30)
    ra = _history_before(away, game_date, 30)
    if len(rh) < 10 or len(ra) < 10:
        return None
    base = _predict_game(home, away, game_date)
    if not base:
        return None

    def stats(rows):
        shots = max(1.0, _weighted(rows, 'shots'))
        allowed = max(1.0, _weighted(rows, 'allowed'))
        goals = max(0.0, _weighted(rows, 'goals'))
        ga = max(0.0, _weighted(rows, 'ga'))
        pace = shots + allowed
        share = shots / max(1.0, shots + allowed)
        # Shrink noisy conversion rates toward a conservative 10% prior.
        shoot = _clip(.70 * (goals / shots) + .30 * .10, .055, .155)
        concede = _clip(.70 * (ga / allowed) + .30 * .10, .055, .155)
        goal_share = goals / max(.2, goals + ga)
        recent = rows[:min(10, len(rows))]
        recent_shots = _avg(recent, 'shots')
        recent_allowed = _avg(recent, 'allowed')
        recent_pace = recent_shots + recent_allowed
        totals = [float(r['shots']) + float(r['allowed']) for r in rows]
        mean_total = sum(totals) / len(totals)
        total_sd = math.sqrt(sum((x - mean_total) ** 2 for x in totals) / max(1, len(totals)))
        win_rate = sum(float(r['goals']) > float(r['ga']) for r in rows) / len(rows)

        attempts = max(1.0, _weighted(rows, 'attempts'))
        attempts_allowed = max(1.0, _weighted(rows, 'attempts_allowed'))
        unblocked = max(1.0, _weighted(rows, 'unblocked'))
        unblocked_allowed = max(1.0, _weighted(rows, 'unblocked_allowed'))
        fivev5 = max(1.0, _weighted(rows, 'fivev5_attempts'))
        fivev5_allowed = max(1.0, _weighted(rows, 'fivev5_attempts_allowed'))
        tied = max(1.0, _weighted(rows, 'tied_attempts'))
        tied_allowed = max(1.0, _weighted(rows, 'tied_attempts_allowed'))
        slot = max(0.0, _weighted(rows, 'slot_attempts'))
        slot_allowed = max(0.0, _weighted(rows, 'slot_attempts_allowed'))
        pp = max(0.0, _weighted(rows, 'pp_attempts'))
        pp_allowed = max(0.0, _weighted(rows, 'pp_attempts_allowed'))
        event_share = attempts / max(1.0, attempts + attempts_allowed)
        fivev5_share = fivev5 / max(1.0, fivev5 + fivev5_allowed)
        tied_share = tied / max(1.0, tied + tied_allowed)
        slot_share = slot / max(1.0, slot + slot_allowed)
        slot_rate = slot / max(1.0, attempts)
        slot_rate_allowed = slot_allowed / max(1.0, attempts_allowed)
        sog_per_attempt = shots / max(1.0, attempts)
        sog_allowed_per_attempt = allowed / max(1.0, attempts_allowed)
        recent_attempts = _avg(recent, 'attempts')
        recent_attempts_allowed = _avg(recent, 'attempts_allowed')
        recent_attempt_pace = recent_attempts + recent_attempts_allowed
        event_coverage = sum(bool(r.get('event_coverage')) for r in rows) / len(rows)

        return {'shots': shots, 'allowed': allowed, 'pace': pace, 'share': share,
                'shoot': shoot, 'concede': concede, 'goal_share': goal_share,
                'shot_diff': shots - allowed, 'goal_diff': goals - ga, 'win_rate': win_rate,
                'recent_shots': recent_shots, 'recent_allowed': recent_allowed,
                'recent_pace': recent_pace, 'total_sd': total_sd,
                'attempts': attempts, 'attempts_allowed': attempts_allowed,
                'unblocked': unblocked, 'unblocked_allowed': unblocked_allowed,
                'fivev5_attempts': fivev5, 'fivev5_attempts_allowed': fivev5_allowed,
                'tied_attempts': tied, 'tied_attempts_allowed': tied_allowed,
                'slot_attempts': slot, 'slot_attempts_allowed': slot_allowed,
                'attempt_share': event_share, 'fivev5_share': fivev5_share,
                'tied_share': tied_share, 'slot_share': slot_share,
                'slot_rate': slot_rate, 'slot_rate_allowed': slot_rate_allowed,
                'pp_attempts': pp, 'pp_attempts_allowed': pp_allowed,
                'sog_per_attempt': sog_per_attempt,
                'sog_allowed_per_attempt': sog_allowed_per_attempt,
                'recent_attempts': recent_attempts,
                'recent_attempts_allowed': recent_attempts_allowed,
                'recent_attempt_pace': recent_attempt_pace,
                'event_coverage': event_coverage}

    return base, stats(rh), stats(ra)


def _predict_game_pace(home, away, game_date):
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total = base['home_shots'] + base['away_shots']
    matchup_pace = (h['pace'] + a['pace']) / 2.0
    factor = _clip(matchup_pace / max(1.0, total), .92, 1.08)
    return dict(base, home_shots=_clip(base['home_shots'] * factor, 10.0, 50.0),
                away_shots=_clip(base['away_shots'] * factor, 10.0, 50.0),
                profile={'pace_factor': factor})


def _predict_game_share(home, away, game_date):
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total = base['home_shots'] + base['away_shots']
    base_share = base['home_shots'] / max(1.0, total)
    profile_share = _clip(.5 + .60 * (h['share'] - a['share']), .38, .62)
    share = _clip(.65 * base_share + .35 * profile_share, .36, .64)
    return dict(base, home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                profile={'home_shot_share': share})


def _predict_game_efficiency(home, away, game_date):
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    home_rate = _clip((h['shoot'] + a['concede']) / 2.0, .06, .145)
    away_rate = _clip((a['shoot'] + h['concede']) / 2.0, .06, .145)
    gh_rate = base['home_shots'] * home_rate
    ga_rate = base['away_shots'] * away_rate
    # Keep a majority of Model 1's goal level; conversion is an added signal, not a replacement.
    gh = _clip(.55 * base['home_goals'] + .45 * gh_rate, .2, 8.0)
    ga = _clip(.55 * base['away_goals'] + .45 * ga_rate, .2, 8.0)
    return dict(base, home_goals=gh, away_goals=ga,
                profile={'home_shoot_rate': home_rate, 'away_shoot_rate': away_rate})


def _predict_game_profile(home, away, game_date):
    """Combined 0.17 profile: pace + shot share for shots, conversion for goals."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total0 = base['home_shots'] + base['away_shots']
    pace_factor = _clip(((h['pace'] + a['pace']) / 2.0) / max(1.0, total0), .92, 1.08)
    total = total0 * pace_factor
    base_share = base['home_shots'] / max(1.0, total0)
    profile_share = _clip(.5 + .60 * (h['share'] - a['share']), .38, .62)
    share = _clip(.65 * base_share + .35 * profile_share, .36, .64)
    hs = _clip(total * share, 10.0, 50.0)
    aas = _clip(total * (1.0 - share), 10.0, 50.0)
    home_rate = _clip((h['shoot'] + a['concede']) / 2.0, .06, .145)
    away_rate = _clip((a['shoot'] + h['concede']) / 2.0, .06, .145)
    gh = _clip(.50 * base['home_goals'] + .50 * hs * home_rate, .2, 8.0)
    ga = _clip(.50 * base['away_goals'] + .50 * aas * away_rate, .2, 8.0)
    return dict(base, home_shots=hs, away_shots=aas, home_goals=gh, away_goals=ga,
                profile={'pace_factor': pace_factor, 'home_shot_share': share,
                         'home_shoot_rate': home_rate, 'away_shoot_rate': away_rate})

def _predict_game_h2h_edge(home, away, game_date):
    """0.19 specialist for 'více střel'.

    Keeps Model 1's total-shot level but reallocates it using long-run shot share
    and shot differential. This targets which team owns more of the shot volume,
    rather than trying to improve every market with the same correction.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total = base['home_shots'] + base['away_shots']
    base_share = base['home_shots'] / max(1.0, total)
    diff_edge = h['shot_diff'] - a['shot_diff']
    share_edge = h['share'] - a['share']
    specialist_share = _clip(.5 + .55 * share_edge + .010 * diff_edge, .38, .62)
    share = _clip(.72 * base_share + .28 * specialist_share, .36, .64)
    return dict(base,
                home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                profile={'specialist': 'h2h_edge', 'home_shot_share': share})


def _predict_game_team_matchup(home, away, game_date):
    """0.19 specialist for team-shot lines.

    Explicitly matches each offense to the opponent's shot-suppression profile,
    with a smaller recent-10 matchup component. The blend is deliberately
    conservative so a noisy 10-game run cannot replace the 30-game baseline.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    mid = (h['shots'] + h['allowed'] + a['shots'] + a['allowed']) / 4.0
    h_match = h['shots'] + a['allowed'] - mid
    a_match = a['shots'] + h['allowed'] - mid
    recent_mid = (h['recent_shots'] + h['recent_allowed'] + a['recent_shots'] + a['recent_allowed']) / 4.0
    h_recent = h['recent_shots'] + a['recent_allowed'] - recent_mid
    a_recent = a['recent_shots'] + h['recent_allowed'] - recent_mid
    hs = _clip(.68 * base['home_shots'] + .24 * h_match + .08 * h_recent, 10.0, 50.0)
    aas = _clip(.68 * base['away_shots'] + .24 * a_match + .08 * a_recent, 10.0, 50.0)
    return dict(base, home_shots=hs, away_shots=aas,
                profile={'specialist': 'team_matchup', 'home_matchup': h_match, 'away_matchup': a_match})


def _predict_game_total_specialist(home, away, game_date):
    """0.19 specialist for total shots.

    Uses joint long-term + recent pace for the mean and carries an observed
    pre-game volatility estimate. The volatility is used only by the total-shot
    reference market in backtesting; it does not leak future outcomes.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total0 = base['home_shots'] + base['away_shots']
    long_pace = (h['pace'] + a['pace']) / 2.0
    recent_pace = (h['recent_pace'] + a['recent_pace']) / 2.0
    total = _clip(.62 * total0 + .25 * long_pace + .13 * recent_pace, 35.0, 85.0)
    share = base['home_shots'] / max(1.0, total0)
    sigma = _clip((h['total_sd'] + a['total_sd']) / 2.0, 5.0, 12.0)
    return dict(base,
                home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                total_sigma=sigma,
                profile={'specialist': 'total_pace_volatility', 'total_sigma': sigma, 'total_mean': total})


def _predict_game_winner_strength(home, away, game_date):
    """0.19 specialist for regulation 1/X/2.

    Separates result strength from shot markets using pre-game goal differential,
    shot differential and long-run win rate. It adjusts the goal *difference*
    while mostly preserving Model 1's expected total goals.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    total_goals = base['home_goals'] + base['away_goals']
    base_diff = base['home_goals'] - base['away_goals']
    strength_diff = (.45 * (h['goal_diff'] - a['goal_diff'])
                     + .055 * (h['shot_diff'] - a['shot_diff'])
                     + .90 * (h['win_rate'] - a['win_rate']))
    diff = _clip(.72 * base_diff + .28 * strength_diff, -3.5, 3.5)
    gh = _clip((total_goals + diff) / 2.0, .2, 8.0)
    ga = _clip((total_goals - diff) / 2.0, .2, 8.0)
    return dict(base, home_goals=gh, away_goals=ga,
                profile={'specialist': 'winner_strength', 'strength_diff': strength_diff})


def _event_ready(h, a):
    return min(float(h.get('event_coverage', 0.0)), float(a.get('event_coverage', 0.0))) >= .70


def _predict_game_event_share(home, away, game_date):
    """0.21 event specialist for the 'více střel' market.

    Uses score-tied and 5v5 attempt share so the signal is less distorted by
    teams protecting a lead. The output still keeps most of Model 1's shot
    expectation and changes mainly the split between the two teams.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'event_model': 'attempt_share', 'coverage': 'fallback'})
    total = base['home_shots'] + base['away_shots']
    base_share = base['home_shots'] / max(1.0, total)
    h_control = .35 * h['attempt_share'] + .40 * h['fivev5_share'] + .25 * h['tied_share']
    a_control = .35 * a['attempt_share'] + .40 * a['fivev5_share'] + .25 * a['tied_share']
    event_share = _clip(.5 + .58 * (h_control - a_control), .38, .62)
    share = _clip(.78 * base_share + .22 * event_share, .36, .64)
    return dict(base,
                home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                profile={'event_model': 'attempt_share', 'home_control': h_control,
                         'away_control': a_control, 'home_shot_share': share,
                         'home_attempt_share': h['attempt_share'], 'away_attempt_share': a['attempt_share'],
                         'home_fivev5_share': h['fivev5_share'], 'away_fivev5_share': a['fivev5_share'],
                         'home_tied_share': h['tied_share'], 'away_tied_share': a['tied_share'],
                         'home_event_coverage': h['event_coverage'], 'away_event_coverage': a['event_coverage']})


def _predict_game_event_matchup(home, away, game_date):
    """0.21 team-shot specialist from attempt volume and shot suppression.

    It converts an offense-vs-defense attempt matchup back to expected shots on
    goal using each side's historical SOG-per-attempt rate. Slot-rate is only a
    small modifier, not a claimed xG estimate.
    """
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'event_model': 'attempt_matchup', 'coverage': 'fallback'})
    mid = (h['attempts'] + h['attempts_allowed'] + a['attempts'] + a['attempts_allowed']) / 4.0
    h_attempt = _clip(h['attempts'] + a['attempts_allowed'] - mid, 25.0, 90.0)
    a_attempt = _clip(a['attempts'] + h['attempts_allowed'] - mid, 25.0, 90.0)
    h_conv = _clip((h['sog_per_attempt'] + a['sog_allowed_per_attempt']) / 2.0, .35, .80)
    a_conv = _clip((a['sog_per_attempt'] + h['sog_allowed_per_attempt']) / 2.0, .35, .80)
    local_slot = (h['slot_rate'] + h['slot_rate_allowed'] + a['slot_rate'] + a['slot_rate_allowed']) / 4.0
    h_slot = (h['slot_rate'] + a['slot_rate_allowed']) / 2.0
    a_slot = (a['slot_rate'] + h['slot_rate_allowed']) / 2.0
    h_quality = _clip(1.0 + .45 * (h_slot - local_slot), .94, 1.06)
    a_quality = _clip(1.0 + .45 * (a_slot - local_slot), .94, 1.06)
    h_event = h_attempt * h_conv * h_quality
    a_event = a_attempt * a_conv * a_quality
    hs = _clip(.74 * base['home_shots'] + .26 * h_event, 10.0, 50.0)
    aas = _clip(.74 * base['away_shots'] + .26 * a_event, 10.0, 50.0)
    return dict(base, home_shots=hs, away_shots=aas,
                profile={'event_model': 'attempt_matchup', 'home_attempt_mean': h_attempt,
                         'away_attempt_mean': a_attempt, 'home_sog_per_attempt': h_conv,
                         'away_sog_per_attempt': a_conv})


def _predict_game_event_pace(home, away, game_date):
    """0.21 total-shot specialist from all-attempt pace and recent event pace."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'event_model': 'attempt_pace', 'coverage': 'fallback'})
    total0 = base['home_shots'] + base['away_shots']
    long_attempt_pace = ((h['attempts'] + h['attempts_allowed']) +
                         (a['attempts'] + a['attempts_allowed'])) / 2.0
    recent_attempt_pace = (h['recent_attempt_pace'] + a['recent_attempt_pace']) / 2.0
    attempt_pace = .72 * long_attempt_pace + .28 * recent_attempt_pace
    conversion = _clip((h['sog_per_attempt'] + h['sog_allowed_per_attempt'] +
                        a['sog_per_attempt'] + a['sog_allowed_per_attempt']) / 4.0, .38, .78)
    event_total = _clip(attempt_pace * conversion, 38.0, 82.0)
    total = _clip(.72 * total0 + .28 * event_total, 38.0, 82.0)
    base_share = base['home_shots'] / max(1.0, total0)
    event_share = _clip(.5 + .45 * (h['fivev5_share'] - a['fivev5_share']), .40, .60)
    share = _clip(.88 * base_share + .12 * event_share, .37, .63)
    return dict(base,
                home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                profile={'event_model': 'attempt_pace', 'attempt_pace': attempt_pace,
                         'sog_conversion': conversion, 'total_mean': total})


def _predict_game_event_goals(home, away, game_date):
    """0.21 goal candidate: stable efficiency plus slot/PP pressure proxy."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    eff = _predict_game_efficiency(home, away, game_date)
    if not eff or not _event_ready(h, a):
        return eff or base
    local_slot = (h['slot_rate'] + h['slot_rate_allowed'] + a['slot_rate'] + a['slot_rate_allowed']) / 4.0
    h_slot = (h['slot_rate'] + a['slot_rate_allowed']) / 2.0
    a_slot = (a['slot_rate'] + h['slot_rate_allowed']) / 2.0
    # PP shot-attempt counts are a style/discipline proxy only; they are shrunk heavily.
    local_pp = (h['pp_attempts'] + h['pp_attempts_allowed'] + a['pp_attempts'] + a['pp_attempts_allowed']) / 4.0
    h_pp = (h['pp_attempts'] + a['pp_attempts_allowed']) / 2.0
    a_pp = (a['pp_attempts'] + h['pp_attempts_allowed']) / 2.0
    h_factor = _clip(1.0 + .55 * (h_slot - local_slot) + .012 * (h_pp - local_pp), .92, 1.08)
    a_factor = _clip(1.0 + .55 * (a_slot - local_slot) + .012 * (a_pp - local_pp), .92, 1.08)
    gh = _clip(.78 * eff['home_goals'] + .22 * eff['home_goals'] * h_factor, .2, 8.0)
    ga = _clip(.78 * eff['away_goals'] + .22 * eff['away_goals'] * a_factor, .2, 8.0)
    return dict(eff, home_goals=gh, away_goals=ga,
                profile={'event_model': 'slot_pp_goals', 'home_quality_factor': h_factor,
                         'away_quality_factor': a_factor})


def _predict_game_event_winner(home, away, game_date):
    """0.21 regulation-result candidate from possession and chance-location edge."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'event_model': 'possession_result', 'coverage': 'fallback'})
    total_goals = base['home_goals'] + base['away_goals']
    base_diff = base['home_goals'] - base['away_goals']
    possession_edge = (.45 * (h['fivev5_share'] - a['fivev5_share']) +
                       .25 * (h['tied_share'] - a['tied_share']) +
                       .30 * (h['slot_share'] - a['slot_share']))
    event_diff = _clip(5.5 * possession_edge + .25 * (h['goal_diff'] - a['goal_diff']), -3.0, 3.0)
    diff = _clip(.78 * base_diff + .22 * event_diff, -3.5, 3.5)
    gh = _clip((total_goals + diff) / 2.0, .2, 8.0)
    ga = _clip((total_goals - diff) / 2.0, .2, 8.0)
    return dict(base, home_goals=gh, away_goals=ga,
                profile={'event_model': 'possession_result', 'possession_edge': possession_edge})



def _predict_game_locked_h2h(home, away, game_date):
    """0.21 locked H2H candidate.

    The source is deliberately fixed to the only shot-control profile that was
    stable in 0.20: score-tied + 5v5 attempt share. No per-fold model switching.
    """
    p = _predict_game_event_share(home, away, game_date)
    if p is None:
        return None
    profile = dict(p.get('profile') or {})
    profile.update({'locked_model': 'h2h_5v5_tied', 'version': '0.21'})
    return dict(p, profile=profile)


def _predict_game_locked_goals(home, away, game_date):
    """0.21 locked goal candidate from repeated stable signals.

    Efficiency and the PBP slot/PP model were independently stable in previous
    walk-forward tests. The blend is fixed before the new test and therefore
    cannot chase the newest fold.
    """
    base = _predict_game(home, away, game_date)
    eff = _predict_game_efficiency(home, away, game_date)
    event = _predict_game_event_goals(home, away, game_date)
    if not base:
        return None
    if not eff or not event:
        return dict(base, profile={'locked_model': 'goals_fallback', 'version': '0.21'})
    gh = _clip(.45 * eff['home_goals'] + .55 * event['home_goals'], .2, 8.0)
    ga = _clip(.45 * eff['away_goals'] + .55 * event['away_goals'], .2, 8.0)
    return dict(base, home_goals=gh, away_goals=ga,
                profile={'locked_model': 'goals_eff_slot_pp', 'version': '0.21'})


def _predict_game_team_neutral(home, away, game_date):
    """0.21 experiment for team-shot lines using neutral-state attempts + PP pressure."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'locked_model': 'team_neutral_fallback'})
    # Match offense to opponent suppression using 5v5 + tied attempts. Add a
    # heavily-shrunk PP-attempt term; convert attempts to SOG using historical rates.
    h_neutral = .65 * h['fivev5_attempts'] + .35 * h['tied_attempts']
    a_neutral = .65 * a['fivev5_attempts'] + .35 * a['tied_attempts']
    h_neutral_against = .65 * h['fivev5_attempts_allowed'] + .35 * h['tied_attempts_allowed']
    a_neutral_against = .65 * a['fivev5_attempts_allowed'] + .35 * a['tied_attempts_allowed']
    mid = (h_neutral + a_neutral + h_neutral_against + a_neutral_against) / 4.0
    local_pp = (h['pp_attempts'] + h['pp_attempts_allowed'] + a['pp_attempts'] + a['pp_attempts_allowed']) / 4.0
    h_pp = (h['pp_attempts'] + a['pp_attempts_allowed']) / 2.0
    a_pp = (a['pp_attempts'] + h['pp_attempts_allowed']) / 2.0
    h_att = h_neutral + a_neutral_against - mid + .12 * (h_pp - local_pp)
    a_att = a_neutral + h_neutral_against - mid + .12 * (a_pp - local_pp)
    h_conv = _clip((h['sog_per_attempt'] + a['sog_allowed_per_attempt']) / 2.0, .35, .80)
    a_conv = _clip((a['sog_per_attempt'] + h['sog_allowed_per_attempt']) / 2.0, .35, .80)
    h_event = _clip(h_att * h_conv, 10.0, 50.0)
    a_event = _clip(a_att * a_conv, 10.0, 50.0)
    return dict(base,
                home_shots=_clip(.80 * base['home_shots'] + .20 * h_event, 10.0, 50.0),
                away_shots=_clip(.80 * base['away_shots'] + .20 * a_event, 10.0, 50.0),
                profile={'locked_model': 'team_neutral_pp', 'home_event_shots': h_event, 'away_event_shots': a_event})


def _predict_game_total_neutral(home, away, game_date):
    """0.21 experiment for total shots from neutral-state attempt pace and conversion."""
    data = _profile_inputs(home, away, game_date)
    if not data:
        return None
    base, h, a = data
    if not _event_ready(h, a):
        return dict(base, profile={'locked_model': 'total_neutral_fallback'})
    total0 = base['home_shots'] + base['away_shots']
    neutral_pace_h = h['fivev5_attempts'] + h['fivev5_attempts_allowed']
    neutral_pace_a = a['fivev5_attempts'] + a['fivev5_attempts_allowed']
    tied_pace_h = h['tied_attempts'] + h['tied_attempts_allowed']
    tied_pace_a = a['tied_attempts'] + a['tied_attempts_allowed']
    neutral_attempt_pace = .72 * ((neutral_pace_h + neutral_pace_a) / 2.0) + .28 * ((tied_pace_h + tied_pace_a) / 2.0)
    conv = _clip((h['sog_per_attempt'] + h['sog_allowed_per_attempt'] + a['sog_per_attempt'] + a['sog_allowed_per_attempt']) / 4.0, .38, .78)
    event_total = _clip(neutral_attempt_pace * conv, 38.0, 82.0)
    total = _clip(.82 * total0 + .18 * event_total, 38.0, 82.0)
    share = base['home_shots'] / max(1.0, total0)
    sigma = _clip((h['total_sd'] + a['total_sd']) / 2.0, 5.0, 12.0)
    return dict(base,
                home_shots=_clip(total * share, 10.0, 50.0),
                away_shots=_clip(total * (1.0 - share), 10.0, 50.0),
                total_sigma=sigma,
                profile={'locked_model': 'total_neutral_pace', 'event_total': event_total, 'total_sigma': sigma})


def _predict_game_winner_plus(home, away, game_date):
    """0.21 experiment for 1/X/2 using possession plus the stable goal profile."""
    base = _predict_game(home, away, game_date)
    event = _predict_game_event_winner(home, away, game_date)
    goals = _predict_game_locked_goals(home, away, game_date)
    if not base or not event or not goals:
        return base
    total = _clip(.55 * (goals['home_goals'] + goals['away_goals']) + .45 * (base['home_goals'] + base['away_goals']), .5, 12.0)
    diff = _clip(.58 * (event['home_goals'] - event['away_goals']) + .42 * (goals['home_goals'] - goals['away_goals']), -3.5, 3.5)
    return dict(base,
                home_goals=_clip((total + diff) / 2.0, .2, 8.0),
                away_goals=_clip((total - diff) / 2.0, .2, 8.0),
                profile={'locked_model': 'winner_possession_goals', 'goal_diff': diff})


def _shadow_analysis(home, away, game_date):
    """0.41 live comparison plus production H2H payload.

    LOCK 5v5+tied is now the primary model only for the ``Více střel`` market.
    If historical play-by-play coverage is insufficient, the payload explicitly
    falls back to Model 1. Other live markets remain on Model 1; the goal model
    stays shadow-only.
    """
    p1 = _predict_game(home, away, game_date)
    ph = _predict_game_locked_h2h(home, away, game_date)
    pg = _predict_game_locked_goals(home, away, game_date)
    if not p1 or not ph or not pg:
        return None

    def h2h_probs(pred):
        sh, sa = _poisson(pred['home_shots']), _poisson(pred['away_shots'])
        a, tie, b = _h2h(sh, sa)
        return {'home': a, 'tie': tie, 'away': b}

    def goal_probs(pred, line=5.5):
        dist = _poisson(pred['home_goals'] + pred['away_goals'], 40)
        over = _over(dist, line)
        return {'line': line, 'over': over, 'under': 1.0 - over}

    p1_h2h = h2h_probs(p1)
    lock_h2h = h2h_probs(ph)
    lock_profile = ph.get('profile') or {}
    ready = lock_profile.get('coverage') != 'fallback'
    active_pred = ph if ready else p1
    active_probs = lock_h2h if ready else p1_h2h
    confidence = max(active_probs['home'], active_probs['away'])
    signal = _h2h_validation_band(confidence) if ready else None

    return {
        'home': home, 'away': away, 'date': game_date,
        'production': {
            'home_shots': p1['home_shots'], 'away_shots': p1['away_shots'],
            'home_goals': p1['home_goals'], 'away_goals': p1['away_goals'],
            'h2h': p1_h2h, 'goals_55': goal_probs(p1),
            'factors': p1.get('factors') or {},
        },
        'active_h2h': {
            'source': 'LOCK 5v5+tied' if ready else 'Model 1 fallback',
            'ready': bool(ready), 'fallback': not bool(ready),
            'home_shots': active_pred['home_shots'], 'away_shots': active_pred['away_shots'],
            'h2h': active_probs, 'confidence': confidence, 'signal': signal,
            'profile': lock_profile,
            'validation': {
                'season': '2021/22', 'games': 1302, 'positive_blocks': 7, 'blocks': 8,
                'brier_v1': .226, 'brier_candidate': .225,
                'note': 'Lockbox byl jednorázový nezávislý test; historické kurzy bookmakerů v něm nejsou.'
            },
        },
        'locked_h2h': {
            'home_shots': ph['home_shots'], 'away_shots': ph['away_shots'],
            'h2h': lock_h2h, 'profile': lock_profile,
        },
        'locked_goals': {
            'home_goals': pg['home_goals'], 'away_goals': pg['away_goals'],
            'goals_55': goal_probs(pg), 'profile': pg.get('profile') or {},
        },
        'note': ('Více střel používá v 0.41 LOCK 5v5+tied jako hlavní model, pokud je dost PBP historie; '
                 'jinak se bezpečně vrátí na Model 1. Pravděpodobnost zůstává raw LOCK, aby se po lockboxu '
                 'dodatečně nepřelaďovala. Góly a ostatní trhy zůstávají na Modelu 1 / shadow režimu.'),
    }

def _normal_line_pick_row(expected, actual, line, sigma):
    sigma = max(.5, float(sigma))
    # P(X > line) under a normal approximation with pre-game estimated sigma.
    p_over = .5 * math.erfc((float(line) - float(expected)) / (sigma * math.sqrt(2.0)))
    p_over = _clip(p_over, .01, .99)
    choose_over = p_over >= .5
    conf = p_over if choose_over else 1.0 - p_over
    actual_over = actual > line
    return {'p': conf, 'y': 1 if choose_over == actual_over else 0,
            'side': 'over' if choose_over else 'under',
            'actual': 'over' if actual_over else 'under'}


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
    try:
        summary = _game_event_summary(game_id)
        result = summary.get('regulation')
        if result in ('home', 'away', 'draw'):
            return result
    except Exception:
        pass
    return None


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
    """Load club schedules in parallel and deduplicate by NHL game id.

    ARI is included for pre-Utah seasons. Individual club schedule failures are
    tolerated because franchise codes can be season-specific (for example UTA
    has no 2022/23 schedule while ARI does).
    """
    teams = list(TEAM_NICK) + ['ARI']
    def safe_schedule(team):
        try:
            return _team_schedule(team, season_start)
        except (HTTPError, URLError, TimeoutError):
            return []
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=12) as pool:
        schedules = list(pool.map(safe_schedule, teams))
    unique = {}
    for schedule in schedules:
        for game in schedule:
            gid = _game_id(game)
            if gid and _completed_regular(game):
                unique[gid] = game
    return list(unique.values())


def _even_sample_games(schedule, limit):
    """Pick games across the whole season instead of taking only the latest block."""
    ordered = sorted(
        schedule,
        key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '',
    )
    if limit <= 0:
        return []
    if len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[-1]]
    step = (len(ordered) - 1) / float(limit - 1)
    idx = [round(i * step) for i in range(limit)]
    return [ordered[i] for i in idx]


def _prepare_backtest_targets(team, season_start, game_limit):
    """Prepare a balanced, season-wide historical sample.

    ``season_start`` may be one start year or a sequence of start years. In
    multi-season mode the requested sample is split approximately equally by
    season. Within each season games are sampled evenly across the full regular
    season instead of taking only the newest games. This reduces late-season
    sampling bias and makes 1500–2000 game validation much more representative.
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
        selected.extend(_even_sample_games(schedule, limit))
    unique = {}
    for game in selected:
        gid = _game_id(game)
        if gid:
            unique[gid] = game
    # Keep newest-first order because older calibration helpers rely on that
    # ordering before explicitly reversing to chronological order.
    return sorted(
        unique.values(),
        key=lambda g: g.get('startTimeUTC') or g.get('gameDate') or '',
        reverse=True,
    )[:game_limit]


def _prefetch_backtest_histories(targets, progress=None):
    """Warm compact historical caches with bounded memory and live progress.

    The 0.22 implementation prefetched thousands of *full* boxscores before
    parsing PBP. At 1500–2000 games that could exhaust Render memory or appear
    frozen for a long time. 0.24 stores compact box/event summaries and treats
    an isolated historical API failure as missing event coverage rather than
    aborting the entire job.
    """
    def report(phase, current=None, total=None, percent=None):
        if progress:
            progress(phase, current=current, total=total, percent=percent)

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

    schedules = list(needed_schedule)
    report('Načítám týmové rozpisy', 0, len(schedules), 4)
    if schedules:
        done = 0
        lock = threading.Lock()
        def one_schedule(item):
            try:
                return _team_schedule(item[0], item[1])
            except Exception:
                return None
            finally:
                nonlocal done
                with lock:
                    done += 1
                    if done == len(schedules) or done % 12 == 0:
                        report('Načítám týmové rozpisy', done, len(schedules), 4 + 5 * done / len(schedules))
        with ThreadPoolExecutor(max_workers=min(10, len(schedules))) as pool:
            list(pool.map(one_schedule, schedules))

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

    ids = list(history_ids)
    total = len(ids)
    if not ids:
        return

    def warm_parallel(fn, phase, start_pct, end_pct, workers):
        done = 0
        report(phase, 0, total, start_pct)
        def safe(gid):
            try:
                fn(gid)
                return True
            except Exception:
                return False
        with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
            futures = [pool.submit(safe, gid) for gid in ids]
            for fut in as_completed(futures):
                done += 1
                # Progress updates are intentionally sparse to avoid lock churn.
                if done == total or done % 25 == 0:
                    pct = start_pct + (end_pct - start_pct) * done / total
                    report(phase, done, total, pct)

    # Compact box summaries first: they are also used for blocked-shot ownership.
    warm_parallel(_game_box_summary, 'Načítám boxscore historii', 10, 32, 12)
    # PBP is the expensive part. Lower concurrency is friendlier to NHL API and
    # retries handle occasional 429/5xx responses.
    warm_parallel(_game_event_summary, 'Načítám play-by-play historii', 32, 68, 10)


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
        expected_total = prediction['home_shots'] + prediction['away_shots']
        if prediction.get('total_sigma') is not None:
            return [_normal_line_pick_row(expected_total, hs + aas, 56.5, prediction['total_sigma'])]
        return [_line_pick_row(expected_total, hs + aas, 56.5)]
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


def _collect_source_market(rows, market, source):
    out = []
    for row in rows:
        prediction = row['predictions'][source]
        out.extend(_market_prediction_rows(prediction, row, market))
    return out



def _model7_walk_forward(tuning_rows):
    """0.18 walk-forward stability test for the fixed profile families.

    The older half of the sample is the initial warm-up. The newer half is
    split into chronological forward folds. For every fold, source selection
    uses only games strictly before that fold, then evaluates on the next
    unseen block. Calibration is fitted on the same prior-only training set
    and reported separately so an unstable calibration cannot hide a weak raw
    model.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    labels = {'v1': 'Model 1', 'pace': 'Tempo střel', 'share': 'Shot-share profil',
              'efficiency': 'Efektivita střel / inkasování', 'profile': 'Kombinovaný profil'}
    market_labels = {
        'h2h': 'Více střel', 'team': 'Střely týmu ±', 'total': 'Celkové střely ±',
        'goals': 'Góly ±', 'winner': 'Vítěz 1/X/2',
    }
    candidates = {
        'h2h': ('v1', 'share', 'pace', 'profile'),
        'team': ('v1', 'share', 'pace', 'profile'),
        'total': ('v1', 'pace', 'profile'),
        'goals': ('v1', 'efficiency', 'profile'),
        'winner': ('v1', 'efficiency', 'profile'),
    }
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 180:
        return {'available': False, 'reason': 'Model 7 Walk-forward potřebuje alespoň 180 vyhodnocených zápasů.'}

    fold_count = 5 if n >= 500 else 4 if n >= 300 else 3
    warmup_n = max(90, n // 2)
    remaining = n - warmup_n
    fold_size = max(20, remaining // fold_count)
    min_train_gain = 0.0005
    min_stable_gain = 0.0005
    required_positive = max(2, math.ceil(fold_count * .60))

    stability = {m: {src: [] for src in candidates[m] if src != 'v1'} for m in order}
    selector_rows = {m: {'v1': [], 'raw': [], 'cal': []} for m in order}
    selector_fold_gains = {m: [] for m in order}
    calibration_fold_gains = {m: [] for m in order}
    selector_sources = {m: {} for m in order}
    folds_out = []

    start = warmup_n
    for fold_index in range(fold_count):
        end = n if fold_index == fold_count - 1 else min(n, start + fold_size)
        if end <= start:
            break
        train = ordered[:start]
        test = ordered[start:end]
        fold_market = {}

        for market in order:
            train_v1 = _collect_source_market(train, market, 'v1')
            test_v1 = _collect_source_market(test, market, 'v1')
            b_train_v1 = _market_brier(train_v1)
            b_test_v1 = _market_brier(test_v1)

            # Stability of every fixed profile family on this unseen fold.
            for source in candidates[market]:
                if source == 'v1':
                    continue
                test_src = _collect_source_market(test, market, source)
                b_src = _market_brier(test_src)
                gain = None if b_test_v1 is None or b_src is None else b_test_v1 - b_src
                stability[market][source].append({
                    'fold': fold_index + 1, 'n': len(test_src), 'gain': gain,
                    'brier_v1': b_test_v1, 'brier_source': b_src,
                })

            # Train-only selector: choose a source using only data before this fold.
            train_scores = []
            for source in candidates[market]:
                rows = _collect_source_market(train, market, source)
                train_scores.append((source, _market_brier(rows)))
            valid = [x for x in train_scores if x[1] is not None]
            best_source, best_brier = min(valid, key=lambda x: x[1]) if valid else ('v1', b_train_v1)
            train_gain = None if b_train_v1 is None or best_brier is None else b_train_v1 - best_brier
            selected = best_source if best_source != 'v1' and train_gain is not None and train_gain >= min_train_gain else 'v1'
            selector_sources[market][selected] = selector_sources[market].get(selected, 0) + 1

            raw_test = _collect_source_market(test, market, selected)
            b_raw = _market_brier(raw_test)
            raw_gain = None if b_test_v1 is None or b_raw is None else b_test_v1 - b_raw
            selector_fold_gains[market].append(raw_gain)

            train_selected = _collect_source_market(train, market, selected)
            fit = _fit_affine(train_selected)
            cal_test = _apply_affine(raw_test, fit) if fit else [dict(x) for x in raw_test]
            b_cal = _market_brier(cal_test)
            cal_gain = None if b_raw is None or b_cal is None else b_raw - b_cal
            calibration_fold_gains[market].append(cal_gain)

            selector_rows[market]['v1'].extend(test_v1)
            selector_rows[market]['raw'].extend(raw_test)
            selector_rows[market]['cal'].extend(cal_test)
            fold_market[market] = {
                'selected_source': selected, 'selected_label': labels[selected],
                'train_gain': train_gain, 'raw_gain': raw_gain, 'calibration_gain': cal_gain,
                'n': len(raw_test),
            }

        folds_out.append({
            'fold': fold_index + 1, 'train_n': len(train), 'test_n': len(test),
            'train_from': train[0]['date'], 'train_to': train[-1]['date'],
            'test_from': test[0]['date'], 'test_to': test[-1]['date'],
            'markets': fold_market,
        })
        start = end
        if start >= n:
            break

    actual_folds = len(folds_out)
    required_positive = max(2, math.ceil(actual_folds * .60)) if actual_folds else 0

    stability_summary = {}
    for market in order:
        rows = []
        for source, fold_rows in stability[market].items():
            gains = [x['gain'] for x in fold_rows if x['gain'] is not None]
            mean_gain = sum(gains) / len(gains) if gains else None
            worst_gain = min(gains) if gains else None
            positive = sum(g > 0 for g in gains)
            eligible = bool(gains and len(gains) == actual_folds and positive >= required_positive and
                            mean_gain is not None and mean_gain >= min_stable_gain and
                            (worst_gain is None or worst_gain > -0.010))
            rows.append({
                'source': source, 'label': labels[source], 'folds': len(gains),
                'positive_folds': positive, 'mean_gain': mean_gain, 'worst_gain': worst_gain,
                'eligible': eligible, 'total_n': sum(x['n'] for x in fold_rows),
            })
        rows.sort(key=lambda x: (x['mean_gain'] is None, -(x['mean_gain'] or 0.0)))
        stability_summary[market] = {'label': market_labels[market], 'profiles': rows}

    selector_summary = {}
    for market in order:
        v1_rows = selector_rows[market]['v1']
        raw_rows = selector_rows[market]['raw']
        cal_rows = selector_rows[market]['cal']
        b_v1, b_raw, b_cal = _market_brier(v1_rows), _market_brier(raw_rows), _market_brier(cal_rows)
        raw_gain = None if b_v1 is None or b_raw is None else b_v1 - b_raw
        cal_vs_raw = None if b_raw is None or b_cal is None else b_raw - b_cal
        final_gain = None if b_v1 is None or b_cal is None else b_v1 - b_cal
        raw_fold = [g for g in selector_fold_gains[market] if g is not None]
        cal_fold = [g for g in calibration_fold_gains[market] if g is not None]
        positive_raw = sum(g > 0 for g in raw_fold)
        positive_cal = sum(g > 0 for g in cal_fold)
        eligible_raw = bool(raw_fold and positive_raw >= required_positive and raw_gain is not None and raw_gain >= min_stable_gain)
        eligible_cal = bool(cal_fold and positive_cal >= required_positive and cal_vs_raw is not None and cal_vs_raw >= min_stable_gain)
        selector_summary[market] = {
            'label': market_labels[market], 'n': len(raw_rows),
            'source_counts': selector_sources[market],
            'brier_v1': b_v1, 'brier_raw': b_raw, 'brier_calibrated': b_cal,
            'raw_brier_gain': raw_gain, 'calibration_brier_gain': cal_vs_raw, 'final_brier_gain': final_gain,
            'positive_raw_folds': positive_raw, 'positive_calibration_folds': positive_cal,
            'folds': actual_folds, 'eligible_raw': eligible_raw, 'eligible_calibration': eligible_cal,
        }

    return {
        'available': True, 'fold_count': actual_folds, 'warmup_n': warmup_n,
        'first_test_from': folds_out[0]['test_from'] if folds_out else None,
        'last_test_to': folds_out[-1]['test_to'] if folds_out else None,
        'required_positive_folds': required_positive, 'min_stable_gain': min_stable_gain,
        'selector': selector_summary, 'stability': stability_summary, 'folds': folds_out,
        'note': ('Každý fold používá pouze starší zápasy k výběru zdroje a kalibraci. '
                 'Profil je označen jako stabilní jen při kladném průměrném Brier zisku a zlepšení alespoň v 60 % forward foldů.'),
    }

def _model8_specialists_walk_forward(tuning_rows):
    """0.19 walk-forward test of market-specific specialist models.

    Four markets get genuinely different candidate information:
      h2h    -> shot-share + shot-differential edge,
      team   -> offense vs opponent suppression matchup,
      total  -> joint pace + pre-game volatility,
      winner -> goal/shot differential + long-run win rate.
    Goals keep the already-promising efficiency/profile candidates as a control.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    labels = {
        'v1': 'Model 1', 'share': 'Shot-share profil', 'pace': 'Tempo střel',
        'efficiency': 'Efektivita střel / inkasování', 'profile': 'Kombinovaný profil',
        'h2h_edge': 'Shot edge specialist', 'team_matchup': 'Útok vs. potlačení střel',
        'total_specialist': 'Tempo + variabilita', 'winner_strength': 'Výkonnostní 1/X/2 profil',
    }
    market_labels = {
        'h2h': 'Více střel', 'team': 'Střely týmu ±', 'total': 'Celkové střely ±',
        'goals': 'Góly ± (kontrola)', 'winner': 'Vítěz 1/X/2',
    }
    candidates = {
        'h2h': ('v1', 'h2h_edge', 'share'),
        'team': ('v1', 'team_matchup', 'share'),
        'total': ('v1', 'total_specialist', 'pace'),
        'goals': ('v1', 'efficiency', 'profile'),
        'winner': ('v1', 'winner_strength', 'efficiency'),
    }
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 180:
        return {'available': False, 'reason': 'Model 8 Specialists potřebuje alespoň 180 vyhodnocených zápasů.'}

    fold_count = 5 if n >= 500 else 4 if n >= 300 else 3
    warmup_n = max(90, n // 2)
    remaining = n - warmup_n
    fold_size = max(20, remaining // fold_count)
    min_train_gain = 0.0005
    min_stable_gain = 0.0005

    stability = {m: {src: [] for src in candidates[m] if src != 'v1'} for m in order}
    selector_rows = {m: {'v1': [], 'raw': [], 'cal': []} for m in order}
    selector_fold_gains = {m: [] for m in order}
    calibration_fold_gains = {m: [] for m in order}
    selector_sources = {m: {} for m in order}
    folds_out = []

    start = warmup_n
    for fold_index in range(fold_count):
        end = n if fold_index == fold_count - 1 else min(n, start + fold_size)
        if end <= start:
            break
        train = ordered[:start]
        test = ordered[start:end]
        fold_market = {}
        for market in order:
            train_v1 = _collect_source_market(train, market, 'v1')
            test_v1 = _collect_source_market(test, market, 'v1')
            b_train_v1 = _market_brier(train_v1)
            b_test_v1 = _market_brier(test_v1)

            for source in candidates[market]:
                if source == 'v1':
                    continue
                test_src = _collect_source_market(test, market, source)
                b_src = _market_brier(test_src)
                gain = None if b_test_v1 is None or b_src is None else b_test_v1 - b_src
                stability[market][source].append({
                    'fold': fold_index + 1, 'n': len(test_src), 'gain': gain,
                    'brier_v1': b_test_v1, 'brier_source': b_src,
                })

            train_scores = []
            for source in candidates[market]:
                rows = _collect_source_market(train, market, source)
                train_scores.append((source, _market_brier(rows)))
            valid = [x for x in train_scores if x[1] is not None]
            best_source, best_brier = min(valid, key=lambda x: x[1]) if valid else ('v1', b_train_v1)
            train_gain = None if b_train_v1 is None or best_brier is None else b_train_v1 - best_brier
            selected = best_source if best_source != 'v1' and train_gain is not None and train_gain >= min_train_gain else 'v1'
            selector_sources[market][selected] = selector_sources[market].get(selected, 0) + 1

            raw_test = _collect_source_market(test, market, selected)
            b_raw = _market_brier(raw_test)
            raw_gain = None if b_test_v1 is None or b_raw is None else b_test_v1 - b_raw
            selector_fold_gains[market].append(raw_gain)

            train_selected = _collect_source_market(train, market, selected)
            fit = _fit_affine(train_selected)
            cal_test = _apply_affine(raw_test, fit) if fit else [dict(x) for x in raw_test]
            b_cal = _market_brier(cal_test)
            cal_gain = None if b_raw is None or b_cal is None else b_raw - b_cal
            calibration_fold_gains[market].append(cal_gain)

            selector_rows[market]['v1'].extend(test_v1)
            selector_rows[market]['raw'].extend(raw_test)
            selector_rows[market]['cal'].extend(cal_test)
            fold_market[market] = {
                'selected_source': selected, 'selected_label': labels[selected],
                'train_gain': train_gain, 'raw_gain': raw_gain, 'calibration_gain': cal_gain,
                'n': len(raw_test),
            }

        folds_out.append({
            'fold': fold_index + 1, 'train_n': len(train), 'test_n': len(test),
            'train_from': train[0]['date'], 'train_to': train[-1]['date'],
            'test_from': test[0]['date'], 'test_to': test[-1]['date'],
            'markets': fold_market,
        })
        start = end
        if start >= n:
            break

    actual_folds = len(folds_out)
    required_positive = max(2, math.ceil(actual_folds * .60)) if actual_folds else 0
    stability_summary = {}
    for market in order:
        rows = []
        for source, fold_rows in stability[market].items():
            gains = [x['gain'] for x in fold_rows if x['gain'] is not None]
            mean_gain = sum(gains) / len(gains) if gains else None
            worst_gain = min(gains) if gains else None
            positive = sum(g > 0 for g in gains)
            eligible = bool(gains and len(gains) == actual_folds and positive >= required_positive and
                            mean_gain is not None and mean_gain >= min_stable_gain and
                            (worst_gain is None or worst_gain > -0.010))
            rows.append({
                'source': source, 'label': labels[source], 'folds': len(gains),
                'positive_folds': positive, 'mean_gain': mean_gain, 'worst_gain': worst_gain,
                'eligible': eligible, 'total_n': sum(x['n'] for x in fold_rows),
            })
        rows.sort(key=lambda x: (x['mean_gain'] is None, -(x['mean_gain'] or 0.0)))
        stability_summary[market] = {'label': market_labels[market], 'profiles': rows}

    selector_summary = {}
    for market in order:
        v1_rows, raw_rows, cal_rows = (selector_rows[market][k] for k in ('v1','raw','cal'))
        b_v1, b_raw, b_cal = _market_brier(v1_rows), _market_brier(raw_rows), _market_brier(cal_rows)
        raw_gain = None if b_v1 is None or b_raw is None else b_v1 - b_raw
        cal_vs_raw = None if b_raw is None or b_cal is None else b_raw - b_cal
        final_gain = None if b_v1 is None or b_cal is None else b_v1 - b_cal
        raw_fold = [g for g in selector_fold_gains[market] if g is not None]
        cal_fold = [g for g in calibration_fold_gains[market] if g is not None]
        positive_raw = sum(g > 0 for g in raw_fold)
        positive_cal = sum(g > 0 for g in cal_fold)
        eligible_raw = bool(raw_fold and positive_raw >= required_positive and raw_gain is not None and raw_gain >= min_stable_gain)
        eligible_cal = bool(cal_fold and positive_cal >= required_positive and cal_vs_raw is not None and cal_vs_raw >= min_stable_gain)
        selector_summary[market] = {
            'label': market_labels[market], 'n': len(raw_rows), 'source_counts': selector_sources[market],
            'brier_v1': b_v1, 'brier_raw': b_raw, 'brier_calibrated': b_cal,
            'raw_brier_gain': raw_gain, 'calibration_brier_gain': cal_vs_raw, 'final_brier_gain': final_gain,
            'positive_raw_folds': positive_raw, 'positive_calibration_folds': positive_cal,
            'folds': actual_folds, 'eligible_raw': eligible_raw, 'eligible_calibration': eligible_cal,
        }

    return {
        'available': True, 'fold_count': actual_folds, 'warmup_n': warmup_n,
        'first_test_from': folds_out[0]['test_from'] if folds_out else None,
        'last_test_to': folds_out[-1]['test_to'] if folds_out else None,
        'required_positive_folds': required_positive, 'min_stable_gain': min_stable_gain,
        'selector': selector_summary, 'stability': stability_summary, 'folds': folds_out,
        'note': ('0.19 testuje pro každý trh vlastní specialistu. Góly zůstávají kontrolním trhem s profily z 0.18. '
                 'Stabilní = kladný průměrný Brier zisk a zlepšení alespoň v 60 % forward období.'),
    }



def _locked_validation_plan(n):
    """Chronological plan for fixed 0.22 validation.

    Large samples use a smaller warm-up fraction so the forward evaluation
    spans several seasons. The candidate formulas are already fixed; warm-up
    exists only to preserve a clean chronological evaluation window.
    """
    if n >= 1200:
        return 8, max(250, n // 5)
    if n >= 800:
        return 6, max(220, n // 3)
    if n >= 500:
        return 5, n // 2
    if n >= 300:
        return 4, n // 2
    return 3, max(90, n // 2)


def _dated_source_market(rows, market, source):
    out = []
    for row in rows:
        for pred in _market_prediction_rows(row['predictions'][source], row, market):
            item = dict(pred)
            item['date'] = row.get('date')
            out.append(item)
    return out


def _wilson_interval(successes, n, z=1.959963984540054):
    """95% Wilson score interval for a binomial hit rate."""
    if not n:
        return (None, None)
    p = successes / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    half = (z / den) * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))
    return (_clip(center - half, 0.0, 1.0), _clip(center + half, 0.0, 1.0))


def _confidence_threshold_summary(predictions, threshold, min_season_n=20):
    total = len(predictions)
    chosen = [x for x in predictions if x.get('p') is not None and x['p'] >= threshold]
    n = len(chosen)
    if not n:
        return {
            'threshold': threshold, 'n': 0, 'coverage': 0.0, 'avg_probability': None,
            'accuracy': None, 'brier': None, 'calibration_gap': None,
            'sample_status': 'BEZ DAT', 'seasons_with_sample': 0,
            'season_min_accuracy': None, 'season_max_accuracy': None,
            'ci95_low': None, 'ci95_high': None,
        }
    avg_p = sum(x['p'] for x in chosen) / n
    wins = sum(x['y'] for x in chosen)
    accuracy = wins / n
    ci95_low, ci95_high = _wilson_interval(wins, n)
    brier = sum((x['p'] - x['y']) ** 2 for x in chosen) / n

    by_season = {}
    for x in chosen:
        date = x.get('date')
        if not date:
            continue
        try:
            season = _season_start(date)
        except Exception:
            continue
        by_season.setdefault(season, []).append(x)
    season_rows = []
    for season, rows in sorted(by_season.items()):
        if len(rows) < min_season_n:
            continue
        season_rows.append({
            'season': season,
            'label': f'{season}/{str(season + 1)[2:]}',
            'n': len(rows),
            'accuracy': sum(x['y'] for x in rows) / len(rows),
            'avg_probability': sum(x['p'] for x in rows) / len(rows),
        })
    season_acc = [x['accuracy'] for x in season_rows]
    status = 'OK VZOREK' if n >= 100 else 'MALÝ VZOREK' if n >= 40 else 'VELMI MALÝ'
    return {
        'threshold': threshold, 'n': n, 'coverage': n / total if total else 0.0,
        'avg_probability': avg_p, 'accuracy': accuracy, 'brier': brier,
        'calibration_gap': accuracy - avg_p, 'sample_status': status,
        'seasons_with_sample': len(season_rows),
        'season_min_accuracy': min(season_acc) if season_acc else None,
        'season_max_accuracy': max(season_acc) if season_acc else None,
        'season_details': season_rows,
        'ci95_low': ci95_low, 'ci95_high': ci95_high,
    }


def _model12_lockbox(tuning_rows):
    """0.24 final untouched-season check for already frozen candidates."""
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    lockbox = []
    for row in ordered:
        try:
            if _season_start(row.get('date')) == 2021:
                lockbox.append(row)
        except Exception:
            continue
    if len(lockbox) < 100:
        return {
            'available': False,
            'reason': 'Pro Model 12 spusť samostatně sezónu 2021/22 (LOCKBOX) alespoň se 100 zápasy.',
            'lockbox_games': len(lockbox),
        }

    markets = {
        'h2h': ('Více střel · LOCK 5v5+tied', 'locked_h2h'),
        'goals': ('Góly · LOCK efficiency+slot/PP', 'locked_goals'),
    }
    block_count = 8 if len(lockbox) >= 800 else 6 if len(lockbox) >= 480 else 4
    min_gain = 0.0005
    bounds = []
    for i in range(block_count):
        a = round(i * len(lockbox) / block_count)
        b = round((i + 1) * len(lockbox) / block_count)
        if b > a:
            bounds.append((a, b))
    block_count = len(bounds)
    required_positive = max(2, math.ceil(block_count * .60)) if block_count else 0
    result_markets = {}

    for market, (label, source) in markets.items():
        v1_all, cand_all, fold_gains = [], [], []
        for a, b in bounds:
            chunk = lockbox[a:b]
            v1 = _collect_source_market(chunk, market, 'v1')
            cand = _collect_source_market(chunk, market, source)
            bv1, bc = _market_brier(v1), _market_brier(cand)
            fold_gains.append(None if bv1 is None or bc is None else bv1 - bc)
            v1_all.extend(v1)
            cand_all.extend(cand)
        a_sum = _metric_summary(v1_all, label)
        c_sum = _metric_summary(cand_all, label)
        valid = [g for g in fold_gains if g is not None]
        positive = sum(g > 0 for g in valid)
        mean_gain = sum(valid) / len(valid) if valid else None
        worst_gain = min(valid) if valid else None
        brier_gain = None if a_sum['brier'] is None or c_sum['brier'] is None else a_sum['brier'] - c_sum['brier']
        wins = sum(x['y'] for x in cand_all)
        ci_low, ci_high = _wilson_interval(wins, len(cand_all))
        eligible = bool(valid and len(valid) == block_count and positive >= required_positive and
                        brier_gain is not None and brier_gain >= min_gain and
                        (worst_gain is None or worst_gain > -0.010))
        result_markets[market] = {
            'label': label, 'source': source, 'n': len(cand_all),
            'accuracy_v1': a_sum['accuracy'], 'accuracy_candidate': c_sum['accuracy'],
            'candidate_ci95_low': ci_low, 'candidate_ci95_high': ci_high,
            'brier_v1': a_sum['brier'], 'brier_candidate': c_sum['brier'],
            'brier_gain': brier_gain, 'positive_blocks': positive, 'blocks': block_count,
            'mean_block_gain': mean_gain, 'worst_block_gain': worst_gain,
            'eligible': eligible,
        }

    pairs = []
    for row in lockbox:
        ca = _market_prediction_rows(row['predictions']['locked_h2h'], row, 'h2h')
        va = _market_prediction_rows(row['predictions']['v1'], row, 'h2h')
        if ca and va:
            pairs.append({'candidate': ca[0], 'v1': va[0]})
    confidence = []
    for threshold in (.60, .65, .70, .75, .80):
        chosen = [x for x in pairs if x['candidate']['p'] >= threshold]
        n = len(chosen)
        if not n:
            confidence.append({'threshold': threshold, 'n': 0, 'coverage': 0.0,
                               'avg_probability': None, 'accuracy': None, 'ci95_low': None, 'ci95_high': None,
                               'm1_same_accuracy': None, 'accuracy_delta_vs_m1': None, 'brier': None,
                               'sample_status': 'BEZ DAT'})
            continue
        wins = sum(x['candidate']['y'] for x in chosen)
        accuracy = wins / n
        ci_low, ci_high = _wilson_interval(wins, n)
        m1_accuracy = sum(x['v1']['y'] for x in chosen) / n
        avg_p = sum(x['candidate']['p'] for x in chosen) / n
        brier = sum((x['candidate']['p'] - x['candidate']['y']) ** 2 for x in chosen) / n
        status = 'OK VZOREK' if n >= 100 else 'MALÝ VZOREK' if n >= 40 else 'VELMI MALÝ'
        confidence.append({'threshold': threshold, 'n': n, 'coverage': n / len(pairs) if pairs else 0.0,
                           'avg_probability': avg_p, 'accuracy': accuracy, 'ci95_low': ci_low, 'ci95_high': ci_high,
                           'm1_same_accuracy': m1_accuracy, 'accuracy_delta_vs_m1': accuracy - m1_accuracy,
                           'brier': brier, 'sample_status': status})

    h2h_pass = bool(result_markets.get('h2h', {}).get('eligible'))
    return {
        'available': True, 'season': '2021/22', 'lockbox_games': len(lockbox),
        'from': lockbox[0]['date'], 'to': lockbox[-1]['date'],
        'block_count': block_count, 'required_positive_blocks': required_positive,
        'min_brier_gain': min_gain, 'markets': result_markets,
        'h2h_confidence': confidence,
        'verdict': 'PROŠEL' if h2h_pass else 'NEPROŠEL',
        'note': ('2021/22 je jednorázový lockbox. Kandidáti jsou před testem pevně daní a na této sezoně se nic nepřelaďuje. '
                 '95% interval je Wilsonův interval binomické úspěšnosti.'),
    }


def _model11_confidence(tuning_rows):
    """0.22 confidence-band study on a chronological forward-only window.

    Thresholds are declared in advance (60/65/70/75/80 %). We report every
    threshold, including tiny samples, so a high hit rate cannot be hidden
    behind selective reporting. Candidate and Model 1 use their own probability
    thresholds and are shown side by side.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    sources = {
        'h2h': 'locked_h2h', 'team': 'team_neutral', 'total': 'total_neutral',
        'goals': 'locked_goals', 'winner': 'winner_plus',
    }
    labels = {
        'h2h': 'Více střel', 'team': 'Střely týmu ±', 'total': 'Celkové střely ±',
        'goals': 'Góly ±', 'winner': 'Vítěz 1/X/2',
    }
    candidate_labels = {
        'h2h': 'LOCK 5v5+tied', 'team': 'Neutral+PP',
        'total': 'Neutral pace', 'goals': 'LOCK efficiency+slot/PP',
        'winner': 'Possession+goals',
    }
    thresholds = (.60, .65, .70, .75, .80)
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 180:
        return {'available': False, 'reason': 'Model 11 Confidence potřebuje alespoň 180 vyhodnocených zápasů.'}
    fold_count, warmup_n = _locked_validation_plan(n)
    test = ordered[warmup_n:]
    if not test:
        return {'available': False, 'reason': 'Chybí forward část pro confidence test.'}

    markets = {}
    for market in order:
        candidate = _dated_source_market(test, market, sources[market])
        v1 = _dated_source_market(test, market, 'v1')
        markets[market] = {
            'label': labels[market],
            'candidate_label': candidate_labels[market],
            'source': sources[market],
            'candidate_n': len(candidate),
            'v1_n': len(v1),
            'candidate': [_confidence_threshold_summary(candidate, t) for t in thresholds],
            'v1': [_confidence_threshold_summary(v1, t) for t in thresholds],
        }
    seasons = sorted({_season_start(r['date']) for r in test if r.get('date')})
    return {
        'available': True,
        'warmup_n': warmup_n,
        'forward_games': len(test),
        'fold_count_reference': fold_count,
        'test_from': test[0]['date'],
        'test_to': test[-1]['date'],
        'thresholds': list(thresholds),
        'seasons': seasons,
        'season_labels': [f'{x}/{str(x+1)[2:]}' for x in seasons],
        'markets': markets,
        'note': ('Pásma 60–80 % jsou předem daná a počítají se pouze na chronologické forward části. '
                 'Vysoká úspěšnost s malým N se nepovažuje za důkaz; proto se vždy ukazuje počet případů, pokrytí, Brier a nejhorší sezónní úspěšnost při dostatečném sezónním vzorku.'),
    }


def _model10_locked_walk_forward(tuning_rows):
    """0.21 fixed-source walk-forward test.

    H2H and goals are *locked* to previously stable candidates. The other three
    markets each get one new experiment. No source is re-selected inside a fold.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    sources = {
        'h2h': 'locked_h2h', 'team': 'team_neutral', 'total': 'total_neutral',
        'goals': 'locked_goals', 'winner': 'winner_plus',
    }
    labels = {
        'h2h': 'Více střel · LOCK 5v5+tied',
        'team': 'Střely týmu · neutral+PP experiment',
        'total': 'Celkové střely · neutral pace experiment',
        'goals': 'Góly · LOCK efficiency+slot/PP',
        'winner': '1/X/2 · possession+goals experiment',
    }
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 180:
        return {'available': False, 'reason': 'Model 10 Locked potřebuje alespoň 180 vyhodnocených zápasů.'}
    fold_count, warmup_n = _locked_validation_plan(n)
    remaining = n - warmup_n
    fold_size = max(20, remaining // fold_count)
    min_stable_gain = 0.0005
    gains = {m: [] for m in order}
    all_rows = {m: {'v1': [], 'candidate': []} for m in order}
    folds = []
    start = warmup_n
    for fi in range(fold_count):
        end = n if fi == fold_count - 1 else min(n, start + fold_size)
        if end <= start:
            break
        test = ordered[start:end]
        out = {}
        for market in order:
            a = _collect_source_market(test, market, 'v1')
            b = _collect_source_market(test, market, sources[market])
            ba, bb = _market_brier(a), _market_brier(b)
            gain = None if ba is None or bb is None else ba - bb
            gains[market].append(gain)
            all_rows[market]['v1'].extend(a)
            all_rows[market]['candidate'].extend(b)
            out[market] = {'source': sources[market], 'gain': gain, 'brier_v1': ba, 'brier_candidate': bb, 'n': len(b)}
        folds.append({'fold': fi+1, 'test_from': test[0]['date'], 'test_to': test[-1]['date'], 'markets': out})
        start = end
        if start >= n:
            break
    actual_folds = len(folds)
    required_positive = max(2, math.ceil(actual_folds * .60)) if actual_folds else 0
    summary = {}
    for market in order:
        valid = [x for x in gains[market] if x is not None]
        positive = sum(x > 0 for x in valid)
        mean_gain = sum(valid)/len(valid) if valid else None
        worst_gain = min(valid) if valid else None
        a = _metric_summary(all_rows[market]['v1'], labels[market])
        b = _metric_summary(all_rows[market]['candidate'], labels[market])
        eligible = bool(valid and len(valid) == actual_folds and positive >= required_positive and
                        mean_gain is not None and mean_gain >= min_stable_gain and
                        (worst_gain is None or worst_gain > -0.010))
        summary[market] = {
            'label': labels[market], 'source': sources[market], 'n': b['n'],
            'positive_folds': positive, 'folds': actual_folds,
            'mean_gain': mean_gain, 'worst_gain': worst_gain,
            'brier_v1': a['brier'], 'brier_candidate': b['brier'],
            'accuracy_v1': a['accuracy'], 'accuracy_candidate': b['accuracy'],
            'eligible': eligible,
            'role': 'locked' if market in ('h2h','goals') else 'experiment',
        }
    return {
        'available': True, 'fold_count': actual_folds, 'warmup_n': warmup_n,
        'required_positive_folds': required_positive, 'min_stable_gain': min_stable_gain,
        'first_test_from': folds[0]['test_from'] if folds else None,
        'last_test_to': folds[-1]['test_to'] if folds else None,
        'markets': summary, 'folds': folds,
    }


def _model9_event_walk_forward(tuning_rows):
    """0.21 walk-forward validation of richer NHL play-by-play event features.

    The candidates use shot attempts (SOG + misses + blocks + goals), score-tied
    and 5v5 attempt share, a coordinate-based slot-attempt proxy and PP-attempt
    volume. These are descriptive pre-game features; the slot proxy is not xG.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    labels = {
        'v1': 'Model 1',
        'h2h_edge': '0.19 Shot edge', 'team_matchup': '0.19 Útok vs. potlačení',
        'total_specialist': '0.19 Tempo + variabilita', 'winner_strength': '0.19 Výkonnostní 1/X/2',
        'efficiency': 'Efektivita střel / inkasování', 'profile': 'Kombinovaný gólový profil',
        'event_share': 'PBP 5v5 + tied attempt share',
        'event_matchup': 'PBP attempts: útok vs. obrana',
        'event_pace': 'PBP tempo všech pokusů',
        'event_goals': 'PBP slot + PP gólový profil',
        'event_winner': 'PBP possession 1/X/2',
    }
    market_labels = {
        'h2h': 'Více střel', 'team': 'Střely týmu ±', 'total': 'Celkové střely ±',
        'goals': 'Góly ±', 'winner': 'Vítěz 1/X/2',
    }
    candidates = {
        'h2h': ('v1', 'h2h_edge', 'event_share'),
        'team': ('v1', 'team_matchup', 'event_matchup'),
        'total': ('v1', 'total_specialist', 'event_pace'),
        'goals': ('v1', 'efficiency', 'profile', 'event_goals'),
        'winner': ('v1', 'winner_strength', 'event_winner'),
    }
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 180:
        return {'available': False, 'reason': 'Model 9 Event Data potřebuje alespoň 180 vyhodnocených zápasů.'}

    fold_count = 5 if n >= 500 else 4 if n >= 300 else 3
    warmup_n = max(90, n // 2)
    remaining = n - warmup_n
    fold_size = max(20, remaining // fold_count)
    min_train_gain = 0.0005
    min_stable_gain = 0.0005

    stability = {m: {src: [] for src in candidates[m] if src != 'v1'} for m in order}
    selector_rows = {m: {'v1': [], 'raw': [], 'cal': []} for m in order}
    selector_fold_gains = {m: [] for m in order}
    calibration_fold_gains = {m: [] for m in order}
    selector_sources = {m: {} for m in order}
    folds_out = []

    start = warmup_n
    for fold_index in range(fold_count):
        end = n if fold_index == fold_count - 1 else min(n, start + fold_size)
        if end <= start:
            break
        train = ordered[:start]
        test = ordered[start:end]
        fold_market = {}
        for market in order:
            train_v1 = _collect_source_market(train, market, 'v1')
            test_v1 = _collect_source_market(test, market, 'v1')
            b_train_v1 = _market_brier(train_v1)
            b_test_v1 = _market_brier(test_v1)

            for source in candidates[market]:
                if source == 'v1':
                    continue
                test_src = _collect_source_market(test, market, source)
                b_src = _market_brier(test_src)
                gain = None if b_test_v1 is None or b_src is None else b_test_v1 - b_src
                stability[market][source].append({
                    'fold': fold_index + 1, 'n': len(test_src), 'gain': gain,
                    'brier_v1': b_test_v1, 'brier_source': b_src,
                })

            train_scores = []
            for source in candidates[market]:
                rows = _collect_source_market(train, market, source)
                train_scores.append((source, _market_brier(rows)))
            valid = [x for x in train_scores if x[1] is not None]
            best_source, best_brier = min(valid, key=lambda x: x[1]) if valid else ('v1', b_train_v1)
            train_gain = None if b_train_v1 is None or best_brier is None else b_train_v1 - best_brier
            selected = best_source if best_source != 'v1' and train_gain is not None and train_gain >= min_train_gain else 'v1'
            selector_sources[market][selected] = selector_sources[market].get(selected, 0) + 1

            raw_test = _collect_source_market(test, market, selected)
            b_raw = _market_brier(raw_test)
            raw_gain = None if b_test_v1 is None or b_raw is None else b_test_v1 - b_raw
            selector_fold_gains[market].append(raw_gain)

            train_selected = _collect_source_market(train, market, selected)
            fit = _fit_affine(train_selected)
            cal_test = _apply_affine(raw_test, fit) if fit else [dict(x) for x in raw_test]
            b_cal = _market_brier(cal_test)
            cal_gain = None if b_raw is None or b_cal is None else b_raw - b_cal
            calibration_fold_gains[market].append(cal_gain)

            selector_rows[market]['v1'].extend(test_v1)
            selector_rows[market]['raw'].extend(raw_test)
            selector_rows[market]['cal'].extend(cal_test)
            fold_market[market] = {
                'selected_source': selected, 'selected_label': labels[selected],
                'train_gain': train_gain, 'raw_gain': raw_gain, 'calibration_gain': cal_gain,
                'n': len(raw_test),
            }

        folds_out.append({
            'fold': fold_index + 1, 'train_n': len(train), 'test_n': len(test),
            'train_from': train[0]['date'], 'train_to': train[-1]['date'],
            'test_from': test[0]['date'], 'test_to': test[-1]['date'],
            'markets': fold_market,
        })
        start = end
        if start >= n:
            break

    actual_folds = len(folds_out)
    required_positive = max(2, math.ceil(actual_folds * .60)) if actual_folds else 0
    stability_summary = {}
    for market in order:
        rows = []
        for source, fold_rows in stability[market].items():
            gains = [x['gain'] for x in fold_rows if x['gain'] is not None]
            mean_gain = sum(gains) / len(gains) if gains else None
            worst_gain = min(gains) if gains else None
            positive = sum(g > 0 for g in gains)
            eligible = bool(gains and len(gains) == actual_folds and positive >= required_positive and
                            mean_gain is not None and mean_gain >= min_stable_gain and
                            (worst_gain is None or worst_gain > -0.010))
            rows.append({
                'source': source, 'label': labels[source], 'folds': len(gains),
                'positive_folds': positive, 'mean_gain': mean_gain, 'worst_gain': worst_gain,
                'eligible': eligible, 'total_n': sum(x['n'] for x in fold_rows),
            })
        rows.sort(key=lambda x: (x['mean_gain'] is None, -(x['mean_gain'] or 0.0)))
        stability_summary[market] = {'label': market_labels[market], 'profiles': rows}

    selector_summary = {}
    for market in order:
        v1_rows, raw_rows, cal_rows = (selector_rows[market][k] for k in ('v1', 'raw', 'cal'))
        b_v1, b_raw, b_cal = _market_brier(v1_rows), _market_brier(raw_rows), _market_brier(cal_rows)
        raw_gain = None if b_v1 is None or b_raw is None else b_v1 - b_raw
        cal_vs_raw = None if b_raw is None or b_cal is None else b_raw - b_cal
        final_gain = None if b_v1 is None or b_cal is None else b_v1 - b_cal
        raw_fold = [g for g in selector_fold_gains[market] if g is not None]
        cal_fold = [g for g in calibration_fold_gains[market] if g is not None]
        positive_raw = sum(g > 0 for g in raw_fold)
        positive_cal = sum(g > 0 for g in cal_fold)
        eligible_raw = bool(raw_fold and positive_raw >= required_positive and raw_gain is not None and raw_gain >= min_stable_gain)
        eligible_cal = bool(cal_fold and positive_cal >= required_positive and cal_vs_raw is not None and cal_vs_raw >= min_stable_gain)
        selector_summary[market] = {
            'label': market_labels[market], 'n': len(raw_rows), 'source_counts': selector_sources[market],
            'brier_v1': b_v1, 'brier_raw': b_raw, 'brier_calibrated': b_cal,
            'raw_brier_gain': raw_gain, 'calibration_brier_gain': cal_vs_raw, 'final_brier_gain': final_gain,
            'positive_raw_folds': positive_raw, 'positive_calibration_folds': positive_cal,
            'folds': actual_folds, 'eligible_raw': eligible_raw, 'eligible_calibration': eligible_cal,
        }

    with _event_cache_lock:
        event_values = list(_event_cache.values())
    covered = [x for x in event_values if x.get('coverage')]
    attempt_counts = []
    for item in covered:
        for st in (item.get('teams') or {}).values():
            if st.get('attempts') is not None:
                attempt_counts.append(float(st['attempts']))
    coverage = {
        'games_cached': len(event_values), 'games_with_events': len(covered),
        'coverage_rate': (len(covered) / len(event_values)) if event_values else None,
        'avg_team_attempts': (sum(attempt_counts) / len(attempt_counts)) if attempt_counts else None,
    }

    return {
        'available': True, 'fold_count': actual_folds, 'warmup_n': warmup_n,
        'first_test_from': folds_out[0]['test_from'] if folds_out else None,
        'last_test_to': folds_out[-1]['test_to'] if folds_out else None,
        'required_positive_folds': required_positive, 'min_stable_gain': min_stable_gain,
        'selector': selector_summary, 'stability': stability_summary, 'folds': folds_out,
        'event_coverage': coverage,
        'note': ('0.21 používá NHL play-by-play: SOG, minuté a blokované pokusy, 5v5/tied stav, '
                 'PP pokusy a jednoduchý slot proxy z x/y souřadnic. Slot proxy není oficiální xG. '
                 'STABILNÍ znamená kladný průměrný Brier zisk a zlepšení alespoň v 60 % forward období.'),
    }


def _model6_profiles(tuning_rows, empty_markets, empty_lines):
    """0.17: select genuinely different information by market.

    Train chooses the best profile family, validation decides whether it is
    allowed to replace Model 1 (and whether calibration is allowed), and the
    newest 25% is never touched until final evaluation.
    """
    order = ('h2h', 'team', 'total', 'goals', 'winner')
    labels = {'v1': 'Model 1', 'pace': 'Tempo střel', 'share': 'Shot-share profil',
              'efficiency': 'Efektivita střel / inkasování', 'profile': 'Kombinovaný profil'}
    candidates = {
        'h2h': ('v1', 'share', 'pace', 'profile'),
        'team': ('v1', 'share', 'pace', 'profile'),
        'total': ('v1', 'pace', 'profile'),
        'goals': ('v1', 'efficiency', 'profile'),
        'winner': ('v1', 'efficiency', 'profile'),
    }
    ordered = sorted(tuning_rows, key=lambda x: x['sort_key'])
    n = len(ordered)
    if n < 100:
        return {'available': False, 'reason': 'Model 6 Profiles potřebuje alespoň 100 vyhodnocených zápasů.'}
    train_end = max(50, int(n * .50))
    val_end = max(train_end + 25, int(n * .75))
    val_end = min(val_end, n - 25)
    train, validation, test = ordered[:train_end], ordered[train_end:val_end], ordered[val_end:]
    min_model_gain = 0.0005
    min_cal_gain = 0.0005
    decisions, selected_source, calibration_fit = {}, {}, {}

    for market in order:
        train_scores = []
        for source in candidates[market]:
            rows = _collect_source_market(train, market, source)
            train_scores.append((source, _market_brier(rows)))
        valid_scores = [x for x in train_scores if x[1] is not None]
        best_source, best_train_brier = min(valid_scores, key=lambda x: x[1]) if valid_scores else ('v1', None)
        train_v1 = _collect_source_market(train, market, 'v1')
        val_v1 = _collect_source_market(validation, market, 'v1')
        train_v1_brier = _market_brier(train_v1)
        val_v1_brier = _market_brier(val_v1)
        val_candidate = _collect_source_market(validation, market, best_source)
        val_candidate_brier = _market_brier(val_candidate)
        train_gain = None if train_v1_brier is None or best_train_brier is None else train_v1_brier - best_train_brier
        val_gain = None if val_v1_brier is None or val_candidate_brier is None else val_v1_brier - val_candidate_brier
        use_candidate = bool(best_source != 'v1' and train_gain is not None and train_gain > 0 and
                             val_gain is not None and val_gain >= min_model_gain)
        source = best_source if use_candidate else 'v1'
        selected_source[market] = source
        sel_train = _collect_source_market(train, market, source)
        sel_val = _collect_source_market(validation, market, source)
        fit_train = _fit_affine(sel_train)
        use_cal = False
        cal_val_gain = None
        if fit_train and len(sel_val) >= 8:
            raw = _market_brier(sel_val)
            cal = _market_brier(_apply_affine(sel_val, fit_train))
            if raw is not None and cal is not None:
                cal_val_gain = raw - cal
                use_cal = cal_val_gain >= min_cal_gain
        fit_final = _fit_affine(sel_train + sel_val) if use_cal else None
        calibration_fit[market] = fit_final
        decisions[market] = {
            'label': {'h2h':'Více střel','team':'Střely týmu ±','total':'Celkové střely ±','goals':'Góly ±','winner':'Vítěz 1/X/2'}[market],
            'train_candidates': [{'source': src, 'label': labels[src], 'brier': br} for src, br in train_scores],
            'best_train_source': best_source, 'best_train_label': labels[best_source],
            'selected_source': source, 'selected_label': labels[source],
            'train_brier_v1': train_v1_brier, 'train_brier_candidate': best_train_brier,
            'train_brier_gain': train_gain, 'validation_brier_v1': val_v1_brier,
            'validation_brier_candidate': val_candidate_brier, 'validation_brier_gain': val_gain,
            'calibration_enabled': bool(use_cal), 'calibration_validation_gain': cal_val_gain,
        }

    # Final untouched test.
    labels_market = {
        'h2h': ('Více střel', '3cestný trh; shoda = prohra týmového výběru'),
        'team': ('Střely týmu ±', 'referenční hranice 27,5'),
        'total': ('Celkové střely ±', 'referenční hranice 56,5'),
        'goals': ('Góly ±', 'referenční hranice 5,5'),
        'winner': ('Vítěz 1/X/2', 'výsledek po 60 minutách'),
    }
    final_compare = {}
    final_raw, final_cal = {}, {}
    for market in order:
        v1_rows = _collect_source_market(test, market, 'v1')
        raw_rows = _collect_source_market(test, market, selected_source[market])
        cal_rows = _apply_affine(raw_rows, calibration_fit[market])
        a = _metric_summary(v1_rows, *labels_market[market])
        r = _metric_summary(raw_rows, *labels_market[market])
        f = _metric_summary(cal_rows, *labels_market[market])
        final_raw[market], final_cal[market] = r, f
        final_compare[market] = {
            'label': f['label'], 'n': min(a['n'], f['n']), 'selected_source': selected_source[market],
            'selected_label': labels[selected_source[market]], 'calibration_enabled': bool(calibration_fit[market]),
            'accuracy_v1': a['accuracy'], 'accuracy_select': f['accuracy'],
            'accuracy_delta': None if a['accuracy'] is None or f['accuracy'] is None else f['accuracy'] - a['accuracy'],
            'brier_v1': a['brier'], 'brier_raw_select': r['brier'], 'brier_select': f['brier'],
            'brier_gain': None if a['brier'] is None or f['brier'] is None else a['brier'] - f['brier'],
        }
        decisions[market]['final_test_brier_gain'] = final_compare[market]['brier_gain']

    # Line tables with the selected raw source for each related market.
    v1_lines, sel_lines = empty_lines(), empty_lines()
    dummy_v1, dummy_sel = empty_markets(), empty_markets()
    final_games = []
    for row in test:
        p1 = row['predictions']['v1']
        e1 = _evaluate_model_prediction(p1, row['hs'], row['aas'], row['hg'], row['ag'], row['regulation'], dummy_v1, v1_lines)

        p_team = row['predictions'][selected_source['team']]
        for line in TEAM_SHOT_LINES:
            _record_line(sel_lines['team'][line], p_team['home_shots'], row['hs'], line)
            _record_line(sel_lines['team'][line], p_team['away_shots'], row['aas'], line)
        p_total = row['predictions'][selected_source['total']]
        for line in TOTAL_SHOT_LINES:
            _record_line(sel_lines['total'][line], p_total['home_shots'] + p_total['away_shots'], row['hs'] + row['aas'], line)
        p_goals = row['predictions'][selected_source['goals']]
        for line in GOAL_LINES:
            _record_line(sel_lines['goals'][line], p_goals['home_goals'] + p_goals['away_goals'], row['hg'] + row['ag'], line, 55)

        p_h2h = row['predictions'][selected_source['h2h']]
        hrows = _market_prediction_rows(p_h2h, row, 'h2h')
        sh, sa = _poisson(p_h2h['home_shots']), _poisson(p_h2h['away_shots'])
        ph, _, pa = _h2h(sh, sa)
        pick_home = ph >= pa
        conf = ph if pick_home else pa
        fit = calibration_fit['h2h']
        if fit:
            conf = _clip(fit['intercept'] + fit['slope'] * conf, .01, .99)
        final_games.append({
            'date': row['date'], 'matchup': row['matchup'],
            'actual_shots': f"{int(row['hs'])}:{int(row['aas'])}",
            'expected_shots_v1': f"{p1['home_shots']:.1f}:{p1['away_shots']:.1f}",
            'expected_shots_select': f"{p_h2h['home_shots']:.1f}:{p_h2h['away_shots']:.1f}",
            'h2h_pick_select': row['home'] if pick_home else row['away'],
            'h2h_probability_select': conf,
            'h2h_correct_select': bool(hrows and hrows[0]['y']),
            'h2h_probability_v1': e1['h2h_conf'], 'h2h_correct_v1': bool(e1['h2h_ok']),
        })

    dates = lambda rows: [x['date'] for x in rows]
    return {
        'available': True, 'train_n': len(train), 'validation_n': len(validation), 'test_n': len(test),
        'train_from': min(dates(train)), 'train_to': max(dates(train)),
        'validation_from': min(dates(validation)), 'validation_to': max(dates(validation)),
        'test_from': min(dates(test)), 'test_to': max(dates(test)),
        'min_model_gain': min_model_gain, 'min_calibration_gain': min_cal_gain,
        'decisions': decisions, 'final_compare': final_compare,
        'final_models': {'select_raw': {'markets': final_raw, 'lines': _line_summaries(sel_lines)},
                         'select': {'markets': final_cal, 'lines': _line_summaries(sel_lines)},
                         'v1': {'lines': _line_summaries(v1_lines)}},
        'final_games': list(reversed(final_games)),
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

def run_backtest(team, season_start, game_limit=10, progress=None):
    started = time.time()
    def report(phase, current=None, total=None, percent=None):
        if progress:
            progress(phase, current=current, total=total, percent=percent)

    report('Připravuji vzorek zápasů', 0, game_limit, 1)
    targets = _prepare_backtest_targets(team, season_start, game_limit)
    report('Připravuji historická data', 0, len(targets), 3)
    _prefetch_backtest_histories(targets, progress=progress)

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
        'pace': ('Tempo střel', 'Předzápasové tempo: střely vytvořené + povolené obou týmů', _predict_game_pace),
        'share': ('Shot-share profil', 'Rozdělení očekávaných střel podle dlouhodobého podílu střel pro/proti', _predict_game_share),
        'efficiency': ('Efektivita střel / inkasování', 'Gólový profil ze střel, shooting% a inkasované efektivity se shrinkage', _predict_game_efficiency),
        'profile': ('Kombinovaný profil', 'Tempo + shot share pro střely a efektivita střel pro góly', _predict_game_profile),
        'h2h_edge': ('Shot edge specialist', 'Více střel: shot share + dlouhodobý rozdíl vytvořených/povolených střel', _predict_game_h2h_edge),
        'team_matchup': ('Útok vs. potlačení střel', 'Týmové střely: konkrétní útok proti schopnosti soupeře střely potlačovat', _predict_game_team_matchup),
        'total_specialist': ('Tempo + variabilita', 'Celkové střely: společné tempo, recent tempo a předzápasová variabilita', _predict_game_total_specialist),
        'winner_strength': ('Výkonnostní 1/X/2 profil', '1/X/2: gólový rozdíl + shot edge + dlouhodobá výsledková síla', _predict_game_winner_strength),
        'event_share': ('PBP kontrola pokusů', 'Více střel: score-tied + 5v5 shot-attempt share z play-by-play', _predict_game_event_share),
        'event_matchup': ('PBP útok vs. obrana', 'Týmové střely: pokusy, bloky/minuté střely, shot suppression a SOG/attempt', _predict_game_event_matchup),
        'event_pace': ('PBP tempo pokusů', 'Celkové střely: all-attempt pace + recent pace + převod pokusů na SOG', _predict_game_event_pace),
        'event_goals': ('PBP slot + PP profil', 'Góly: stabilní efektivita doplněná o slot-attempt a PP-attempt tlak', _predict_game_event_goals),
        'event_winner': ('PBP possession 1/X/2', '1/X/2: 5v5/tied attempt share + slot share + gólový rozdíl', _predict_game_event_winner),
        'locked_h2h': ('LOCK 5v5+tied', '0.21: pevně zamknutý stabilní PBP profil pro více střel', _predict_game_locked_h2h),
        'locked_goals': ('LOCK góly', '0.21: pevný blend efektivity + slot/PP pro góly', _predict_game_locked_goals),
        'team_neutral': ('Neutral+PP týmové střely', '0.21 experiment: neutral-state attempts, suppression a PP tlak', _predict_game_team_neutral),
        'total_neutral': ('Neutral pace celkové střely', '0.21 experiment: 5v5/tied attempt pace + převod na SOG', _predict_game_total_neutral),
        'winner_plus': ('Possession+goals 1/X/2', '0.21 experiment: possession profil + stabilnější gólový profil', _predict_game_winner_plus),
    }
    # For 800+ games we switch to a focused deep-validation path. Legacy
    # experimental families (Models 4–9) were useful during discovery but make
    # a 2000-game job several times more expensive without adding evidence for
    # the currently locked candidates. Deep mode computes Model 1, Full and the
    # five pre-declared 0.21 candidates needed by Models 10/11/12.
    deep_mode = game_limit > 600
    if deep_mode:
        keep = ('v1', 'full', 'locked_h2h', 'locked_goals',
                'team_neutral', 'total_neutral', 'winner_plus')
        specs = {key: specs[key] for key in keep}

    stores = {key: {'markets': empty_markets(), 'lines': empty_lines()} for key in specs}
    game_rows, factor_rows, tuning_rows = [], [], []
    skipped = 0

    total_targets = len(targets)
    report('Počítám předzápasové modely', 0, total_targets, 69)
    for target_index, game in enumerate(targets, 1):
        if target_index == total_targets or target_index % 10 == 0:
            report('Počítám předzápasové modely', target_index, total_targets,
                   69 + 23 * target_index / max(1, total_targets))
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

        try:
            box = _game_box_summary(game_id)
            ht, at = box.get('home') or {}, box.get('away') or {}
            hs, aas = float(ht.get('sog')), float(at.get('sog'))
            hg, ag = float(ht.get('score')), float(at.get('score'))
        except (TypeError, ValueError, HTTPError, URLError, TimeoutError):
            skipped += 1
            continue
        except Exception:
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

    report('Vyhodnocuji walk-forward a confidence pásma', None, None, 94)
    if deep_mode:
        legacy_reason = ('Deep režim 800+ záměrně nepočítá staré vývojové Modely 4–9. '
                         'Šetří paměť/CPU a testuje jen předem zamknuté kandidáty přes Model 10/11/12.')
        model4 = {'available': False, 'reason': legacy_reason}
        model5 = {'available': False, 'reason': legacy_reason}
        model6 = {'available': False, 'reason': legacy_reason}
        model7 = {'available': False, 'reason': legacy_reason}
        model8 = {'available': False, 'reason': legacy_reason}
        model9 = {'available': False, 'reason': legacy_reason}
    else:
        model4 = _model4_train_test(tuning_rows, empty_markets, empty_lines)
        model5 = _model5_select(tuning_rows, empty_markets, empty_lines)
        model6 = _model6_profiles(tuning_rows, empty_markets, empty_lines)
        model7 = _model7_walk_forward(tuning_rows)
        model8 = _model8_specialists_walk_forward(tuning_rows)
        model9 = _model9_event_walk_forward(tuning_rows)
    model10 = _model10_locked_walk_forward(tuning_rows)
    model11 = _model11_confidence(tuning_rows)
    model12 = _model12_lockbox(tuning_rows)
    report('Sestavuji výsledky', None, None, 98)

    evaluated_by_season = {}
    for row in tuning_rows:
        try:
            sy = _season_start(row['date'])
        except Exception:
            continue
        key = f'{sy}/{str(sy + 1)[2:]}'
        evaluated_by_season[key] = evaluated_by_season.get(key, 0) + 1

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
        'model4': model4, 'model5': model5, 'model6': model6, 'model7': model7, 'model8': model8, 'model9': model9, 'model10': model10, 'model11': model11, 'model12': model12, 'deep_mode': deep_mode, 'evaluated_by_season': evaluated_by_season, 'games': game_rows,
        'warning': ('0.30 zachovává LOCK 5v5+tied do živé Analýzy pouze pro trh Více střel; pokud chybí dostatečná PBP historie, použije se Model 1 fallback. Model 12 lockbox 2021/22 a 95% Wilsonovy intervaly zůstávají validačním základem. Dlouhé testy dál používají kompaktní box/PBP cache, retry při dočasné chybě NHL API a pro 800+ zápasů soustředěný deep režim Model 1 + zamknuté kandidáty. Model 11 ukazuje předem daná pásma jistoty 60/65/70/75/80 % pouze na chronologické forward části. '
                    'Vysoká úspěšnost s malým vzorkem není považována za důkaz; vždy se sleduje N, pokrytí, Brier a sezónní stabilita. V živé Analýze je produkční pouze LOCK 5v5+tied pro Více střel; gólový LOCK a ostatní kandidáti zůstávají shadow. '
                    'Slot proxy není oficiální xG. Historické kurzy bookmakerů nejsou součástí backtestu.'),
    }


def _cleanup_backtest_jobs():
    now = time.time()
    with _backtest_jobs_lock:
        stale = [jid for jid, job in _backtest_jobs.items()
                 if now - job.get('created_at', now) > _BACKTEST_JOB_TTL]
        for jid in stale:
            _backtest_jobs.pop(jid, None)


def _update_backtest_job_progress(job_id, phase, current=None, total=None, percent=None):
    with _backtest_jobs_lock:
        job = _backtest_jobs.get(job_id)
        if not job or job.get('status') != 'running':
            return
        job['phase'] = phase
        if current is not None:
            job['progress_current'] = int(current)
        if total is not None:
            job['progress_total'] = int(total)
        if percent is not None:
            job['progress_percent'] = round(float(percent), 1)
        job['heartbeat_at'] = time.time()


def _run_backtest_job(job_id, team, start, games):
    def progress(phase, current=None, total=None, percent=None):
        _update_backtest_job_progress(job_id, phase, current, total, percent)
    try:
        result = run_backtest(team, start, games, progress=progress)
        payload = {'status': 'done', 'result': result, 'finished_at': time.time(),
                   'phase': 'Hotovo', 'progress_percent': 100.0}
    except HTTPError as exc:
        payload = {'status': 'error', 'error': f'NHL API při backtestu odpovědělo HTTP {exc.code}', 'finished_at': time.time()}
    except (URLError, TimeoutError):
        payload = {'status': 'error', 'error': 'NHL API není při backtestu dostupné', 'finished_at': time.time()}
    except MemoryError:
        payload = {'status': 'error',
                   'error': 'Serveru došla paměť při backtestu',
                   'detail': 'Zkus verzi 0.30 znovu; deep režim má výrazně menší paměťové nároky.',
                   'finished_at': time.time()}
    except Exception as exc:
        payload = {'status': 'error', 'error': 'Backtest se nepodařilo dokončit',
                   'detail': str(exc)[:180], 'finished_at': time.time()}
    finally:
        # Team/date windows are the largest rebuildable objects. Compact box and
        # event summaries stay cached so a second run remains much faster.
        with _history_cache_lock:
            _history_cache.clear()
    with _backtest_jobs_lock:
        job = _backtest_jobs.get(job_id)
        if job:
            job.update(payload)




class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/forward-v042/report':
            return self.respond(200, forward.report())
        if parsed.path == '/forward-v042/settle':
            return self.handle_forward_settle()
        if parsed.path == '/lineups':
            return self.handle_lineups(parse_qs(parsed.query))
        if parsed.path == '/backtest':
            return self.handle_backtest(parse_qs(parsed.query))
        if parsed.path == '/backtest-start':
            return self.handle_backtest_start(parse_qs(parsed.query))
        if parsed.path == '/backtest-status':
            return self.handle_backtest_status(parse_qs(parsed.query))
        if parsed.path == '/shadow':
            return self.handle_shadow(parse_qs(parsed.query))
        if not parsed.path.startswith('/api/'):
            return super().do_GET()
        path = parsed.path[4:]
        if not ALLOWED.fullmatch(path):
            return self.respond(400, {'error': 'Nepovolená cesta API'})
        try:
            req = Request(
                'https://api-web.nhle.com/v1' + path,
                headers={'User-Agent': 'NHLShotsAnalyzer/0.41', 'Accept': 'application/json'},
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

    def do_POST(self):
        if urlparse(self.path).path != '/forward-v042/capture':
            return self.respond(404, {'error':'Unknown endpoint'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 2048: raise ValueError('Invalid payload length')
            req=json.loads(self.rfile.read(length))
            gid=int(req['game_id'])
            box=_nhl_json(f'/gamecenter/{gid}/boxscore',ttl=60)
            home=(box.get('homeTeam') or {}).get('abbrev')
            away=(box.get('awayTeam') or {}).get('abbrev')
            start=box.get('startTimeUTC')
            if not home or not away or not start: raise ValueError('Missing NHL fixture')
            if box.get('gameState') in ('LIVE','CRIT','OFF','FINAL'): raise ValueError('Not pregame')
            result=_shadow_analysis(home,away,start[:10])
            if not result: raise ValueError('No model output')
            active=result.get('active_h2h') or {}
            if not active.get('ready'): raise ValueError('LOCK unavailable; no fallback captured')
            p=float((active.get('h2h') or {})['home'])
            payload={'game_id':gid,'start_utc':start,'home':home,'away':away,'model':'LOCK 5v5+tied v0.41','probability_home_more_shots':p,'shadow':result}
            return self.respond(200,forward.capture(gid,start,home,away,'LOCK 5v5+tied v0.41',p,payload))
        except (ValueError,KeyError,TypeError) as e:
            return self.respond(422,{'error':str(e)[:160]})
        except Exception as e:
            return self.respond(502,{'error':'Capture unavailable','detail':str(e)[:120]})

    def handle_forward_settle(self):
        completed=0;skipped=0
        for row in forward.pending():
            if forward.utc(row['start_utc']) >= datetime.now(timezone.utc): continue
            try:
                box=_nhl_json(f"/gamecenter/{row['game_id']}/boxscore",ttl=60)
                if box.get('gameState') not in ('OFF','FINAL'): continue
                home=box.get('homeTeam') or {};away=box.get('awayTeam') or {}
                if home.get('abbrev')!=row['home'] or away.get('abbrev')!=row['away']: continue
                hs,aas=home.get('sog'),away.get('sog')
                if not isinstance(hs,int) or not isinstance(aas,int): continue
                forward.settle(row['game_id'],hs,aas);completed+=1
            except Exception: skipped+=1
        return self.respond(200,{'settled':completed,'errors':skipped,'report':forward.report()})

    def _parse_backtest_query(self, query):
        team = (query.get('team', ['FLA'])[0] or 'FLA').upper()
        raw_season = query.get('season', ['MULTI4'])[0]
        try:
            games = int(query.get('games', ['600'])[0])
        except ValueError:
            games = 600
        if team != 'ALL' and team not in TEAM_NICK:
            return None, 'Neplatný tým pro backtest'

        multi = {
            'MULTI4': (2022, 2023, 2024, 2025),
            'MULTI3': (2023, 2024, 2025),
            'MULTI2': (2024, 2025),
            'MULTI': (2024, 2025),
        }
        if raw_season in multi:
            start = multi[raw_season]
            games = max(100, min(2000 if team == 'ALL' else 300, games))
        else:
            if not re.fullmatch(r'20\d{6}', raw_season):
                return None, 'Neplatná sezóna'
            start = int(raw_season[:4])
            if raw_season != _season_id(start):
                return None, 'Sezóna musí mít tvar 20252026'
            games = max(5, min(1400 if team == 'ALL' else 80, games))
        return (team, start, games), None

    def handle_backtest(self, query):
        params, error = self._parse_backtest_query(query)
        if error:
            return self.respond(400, {'error': error})
        team, start, games = params
        try:
            result = run_backtest(team, start, games)
            return self.respond(200, result, cache='no-store')
        except HTTPError as exc:
            return self.respond(502, {'error': f'NHL API při backtestu odpovědělo HTTP {exc.code}'})
        except (URLError, TimeoutError):
            return self.respond(502, {'error': 'NHL API není při backtestu dostupné'})
        except Exception as exc:
            return self.respond(500, {'error': 'Backtest se nepodařilo dokončit', 'detail': str(exc)[:180]})

    def handle_backtest_start(self, query):
        params, error = self._parse_backtest_query(query)
        if error:
            return self.respond(400, {'error': error})
        team, start, games = params
        _cleanup_backtest_jobs()
        key = (team, tuple(start) if isinstance(start, (tuple, list)) else start, games)
        with _backtest_jobs_lock:
            for jid, job in _backtest_jobs.items():
                if job.get('status') == 'running':
                    if job.get('key') == key:
                        return self.respond(202, {'job_id': jid, 'status': 'running', 'reused': True})
                    return self.respond(409, {'error': 'Jiný velký backtest právě běží. Počkej na jeho dokončení.'})
            job_id = secrets.token_hex(8)
            _backtest_jobs[job_id] = {
                'status': 'running', 'created_at': time.time(), 'heartbeat_at': time.time(), 'key': key,
                'team': team, 'games': games, 'phase': 'Čekám na start',
                'progress_percent': 0.0, 'progress_current': 0, 'progress_total': games,
            }
        worker = threading.Thread(target=_run_backtest_job, args=(job_id, team, start, games), daemon=True)
        worker.start()
        return self.respond(202, {'job_id': job_id, 'status': 'running'})

    def handle_backtest_status(self, query):
        job_id = (query.get('id', [''])[0] or '').strip()
        if not job_id:
            return self.respond(400, {'error': 'Chybí ID backtestu'})
        _cleanup_backtest_jobs()
        with _backtest_jobs_lock:
            job = _backtest_jobs.get(job_id)
            if not job:
                return self.respond(404, {'error': 'Backtest už není dostupný nebo neexistuje'})
            status = job.get('status')
            created = job.get('created_at', time.time())
            if status == 'running':
                payload = {
                    'status': 'running',
                    'elapsed_seconds': round(time.time() - created, 1),
                    'games': job.get('games'),
                    'phase': job.get('phase'),
                    'progress_percent': job.get('progress_percent'),
                    'progress_current': job.get('progress_current'),
                    'progress_total': job.get('progress_total'),
                    'heartbeat_age': round(time.time() - job.get('heartbeat_at', created), 1),
                }
            elif status == 'done':
                payload = {'status': 'done', 'result': job.get('result')}
            else:
                payload = {'status': 'error', 'error': job.get('error', 'Backtest selhal'),
                           'detail': job.get('detail')}
        return self.respond(200, payload, cache='no-store')

    def handle_shadow(self, query):
        home = (query.get('home', [''])[0] or '').upper()
        away = (query.get('away', [''])[0] or '').upper()
        game_date = (query.get('date', [''])[0] or '')[:10]
        if home not in TEAM_NICK or away not in TEAM_NICK or home == away:
            return self.respond(400, {'error': 'Neplatné týmy pro shadow model'})
        if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', game_date):
            return self.respond(400, {'error': 'Neplatné datum pro shadow model'})
        try:
            result = _shadow_analysis(home, away, game_date)
            if not result:
                return self.respond(422, {'error': 'Pro shadow model není dost předzápasových dat'})
            return self.respond(200, result, cache='no-store')
        except HTTPError as exc:
            return self.respond(502, {'error': f'NHL API při shadow modelu odpovědělo HTTP {exc.code}'})
        except (URLError, TimeoutError):
            return self.respond(502, {'error': 'NHL API není pro shadow model dostupné'})
        except Exception as exc:
            return self.respond(500, {'error': 'Shadow model se nepodařilo spočítat', 'detail': str(exc)[:180]})

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


# v0.42 forward validation (self-contained).
import types as _forward_types
forward = _forward_types.ModuleType("forward_v042_embedded")
forward.__file__ = __file__
exec(compile('"""Append-only pregame forecasts and forward evaluation; no model fitting."""\nimport hashlib, json, math, os, sqlite3\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\nDB = Path(os.environ.get(\'FORWARD_DB_PATH\', str(Path(__file__).with_name(\'forward_v042.sqlite3\'))))\ndef connect():\n    DB.parent.mkdir(parents=True, exist_ok=True)\n    c=sqlite3.connect(str(DB), timeout=15)\n    c.row_factory=sqlite3.Row\n    c.execute(\'PRAGMA busy_timeout=15000\')\n    c.execute(\'CREATE TABLE IF NOT EXISTS forecasts (game_id INTEGER PRIMARY KEY, start_utc TEXT NOT NULL, created_utc TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL, model TEXT NOT NULL, probability REAL NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL)\')\n    c.execute(\'CREATE TABLE IF NOT EXISTS outcomes (game_id INTEGER PRIMARY KEY REFERENCES forecasts(game_id), observed_utc TEXT NOT NULL, home_sog INTEGER NOT NULL, away_sog INTEGER NOT NULL, result INTEGER, source TEXT NOT NULL)\')\n    c.execute(\'CREATE TRIGGER IF NOT EXISTS immutable_forecasts_update BEFORE UPDATE ON forecasts BEGIN SELECT RAISE(ABORT,"immutable forecast"); END\')\n    c.execute(\'CREATE TRIGGER IF NOT EXISTS immutable_forecasts_delete BEFORE DELETE ON forecasts BEGIN SELECT RAISE(ABORT,"immutable forecast"); END\')\n    c.execute(\'CREATE TRIGGER IF NOT EXISTS immutable_outcomes_update BEFORE UPDATE ON outcomes BEGIN SELECT RAISE(ABORT,"immutable outcome"); END\')\n    c.execute(\'CREATE TRIGGER IF NOT EXISTS immutable_outcomes_delete BEFORE DELETE ON outcomes BEGIN SELECT RAISE(ABORT,"immutable outcome"); END\')\n    return c\n\ndef utc(s):\n    d=datetime.fromisoformat(s.replace(\'Z\',\'+00:00\'))\n    if d.tzinfo is None: raise ValueError(\'UTC offset required\')\n    return d.astimezone(timezone.utc)\n\ndef capture(game_id,start,home,away,model,p,payload):\n    now=datetime.now(timezone.utc)\n    if utc(start)<=now: raise ValueError(\'Game already started\')\n    if not (0<=p<=1 and math.isfinite(p)): raise ValueError(\'Invalid probability\')\n    raw=json.dumps(payload,sort_keys=True,separators=(\',\',\':\'),allow_nan=False)\n    digest=hashlib.sha256(raw.encode()).hexdigest()\n    with connect() as c:\n        c.execute(\'INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?)\',(game_id,start,now.isoformat(),home,away,model,p,raw,digest))\n        row=c.execute(\'SELECT * FROM forecasts WHERE game_id=?\',(game_id,)).fetchone()\n    return {\'game_id\':game_id,\'created_utc\':row[\'created_utc\'],\'model\':row[\'model\'],\'sha256\':row[\'sha256\'],\'already_exists\':row[\'sha256\']!=digest}\n\ndef settle(game_id,home_sog,away_sog):\n    if not all(isinstance(v,int) and v>=0 for v in (home_sog,away_sog)): raise ValueError(\'Invalid SOG\')\n    result=1 if home_sog>away_sog else 0 if home_sog<away_sog else None\n    with connect() as c:\n        c.execute(\'INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?,?)\',(game_id,datetime.now(timezone.utc).isoformat(),home_sog,away_sog,result,\'NHL official boxscore\'))\n\ndef wilson(w,n):\n    if not n:return [None,None]\n    z=1.9599639845;p=w/n;d=1+z*z/n;m=(p+z*z/(2*n))/d;r=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d\n    return [m-r,m+r]\n\ndef report():\n    with connect() as c:\n        rows=c.execute(\'SELECT f.*,o.home_sog,o.away_sog,o.result FROM forecasts f LEFT JOIN outcomes o USING(game_id) ORDER BY f.created_utc\').fetchall()\n    valid=[r for r in rows if r[\'result\'] is not None]\n    def metrics(rs):\n        n=len(rs);hits=sum((r[\'probability\']>=.5)==bool(r[\'result\']) for r in rs)\n        return {\'n\':n,\'brier\':sum((r[\'probability\']-r[\'result\'])**2 for r in rs)/n if n else None,\'hit_rate\':hits/n if n else None,\'hit_rate_ci95\':wilson(hits,n),\'mean_probability\':sum(r[\'probability\'] for r in rs)/n if n else None,\'observed_home_win_rate\':sum(r[\'result\'] for r in rs)/n if n else None}\n    bins=[]\n    for lo,hi in ((0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.000001)):\n        group=[r for r in valid if lo<=r[\'probability\']<hi]\n        bins.append({\'range\':[lo,min(hi,1)],**metrics(group)})\n    confidence=[]\n    for lo,hi in ((.5,.6),(.6,.7),(.7,.8),(.8,1.000001)):\n        group=[r for r in valid if lo<=max(r[\'probability\'],1-r[\'probability\'])<hi]\n        confidence.append({\'range\':[lo,min(hi,1)],**metrics(group)})\n    return {\'total\':len(rows),\'pending\':sum(r[\'home_sog\'] is None for r in rows),\'ties_excluded\':sum(r[\'home_sog\'] is not None and r[\'result\'] is None for r in rows),\'overall\':metrics(valid),\'calibration\':bins,\'confidence_bands\':confidence,\'model\':\'Frozen pregame home-more-shots probability; ties excluded from binary metrics\'}\n\ndef pending():\n    with connect() as c:\n        return [dict(r) for r in c.execute(\'SELECT f.game_id,f.start_utc,f.home,f.away FROM forecasts f LEFT JOIN outcomes o USING(game_id) WHERE o.game_id IS NULL\')]\n', "forward_v042_embedded", "exec"), forward.__dict__)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()

