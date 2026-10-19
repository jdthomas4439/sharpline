"""
Sharpline data updater. Runs on GitHub Actions (free) every hour and writes data/bundle.js,
which the app loads. Everything here is keyless except the optional Odds API pull.

Sources
  NFL results + real closing lines ...... nflverse (GitHub)
  NBA / NHL / MLB results ............... sportsdataverse (GitHub)
  College football results + lines ...... CollegeFootballData via sportsdataverse (GitHub)
  NBA / NHL / MLB / college hoops lines . ESPN scoreboard, saved before each game (builds your own archive)
  College basketball results ............ ESPN scoreboard
  Injuries .............................. ESPN injuries feed
  Weather ............................... Open-Meteo forecast (outdoor football)
  Live sportsbook odds (optional) ....... The Odds API, only if the ODDS_API_KEY secret is set
  Kalshi + Polymarket prices ............ their public market-data APIs (no account needed)
  Line history for move alerts .......... hourly snapshots of all of the above
"""
import re
import os, json, math, time, io, datetime as dt, traceback
from zoneinfo import ZoneInfo
import requests
import pandas as pd

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SDV = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
NFLVERSE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (Sharpline personal research)"}
NOW = dt.datetime.now(dt.timezone.utc)
TODAY_ET = NOW.astimezone(ET).date()
LOG = []

def log(msg):
    print(msg, flush=True); LOG.append(msg)

def get(url, params=None, tries=3, timeout=40):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200: return r
            log(f"  {r.status_code} from {url}")
            if r.status_code in (401, 403, 404, 422): return None
        except Exception as e:
            log(f"  error {e} on {url}")
        time.sleep(2 * (i + 1))
    return None

def get_json(url, params=None):
    r = get(url, params)
    try: return r.json() if r is not None else None
    except Exception: return None

def parquet(url):
    r = get(url, timeout=90)
    if r is None: return None
    try: return pd.read_parquet(io.BytesIO(r.content))
    except Exception as e:
        log(f"  could not read parquet {url}: {e}"); return None

def nz(x):
    if x is None: return None
    try:
        if isinstance(x, float) and math.isnan(x): return None
    except Exception: pass
    return x

def et(ts):
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    t = t.tz_convert(ET); return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")

def load(name, default):
    p = os.path.join(DATA, name)
    try:
        with open(p) as f: return json.load(f)
    except Exception: return default

def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f: json.dump(obj, f, separators=(",", ":"))

def add_rest(games):
    last = {}
    for g in sorted(games, key=lambda x: (x["date"], x["time"])):
        for side, ab in (("ar", g["a"]), ("hr", g["h"])):
            k = (g["season"], ab); d = dt.date.fromisoformat(g["date"])
            g[side] = (d - last[k]).days if k in last else None
            last[k] = d
    return games

def to_rows(games):
    out = []
    for g in sorted(games, key=lambda x: (x["date"], x["time"])):
        out.append([g["season"], g["date"], g["time"], g["a"], g["h"], g.get("as"), g.get("hs"), g.get("sl"), g.get("tot"),
                    g.get("aml"), g.get("hml"), g.get("div", 0), g.get("indoor"), g.get("ar"), g.get("hr"), g.get("neutral", 0),
                    g.get("ark"), g.get("hrk")])
    return out

# ---------------------------------------------------------------- team maps
NBA_MAP = {"GS":"GSW","NY":"NYK","NO":"NOP","SA":"SAS","UTAH":"UTA","WSH":"WAS"}
NBA_DIV = {'BOS':'A','BKN':'A','NYK':'A','PHI':'A','TOR':'A','CHI':'C','CLE':'C','DET':'C','IND':'C','MIL':'C','ATL':'SE','CHA':'SE','MIA':'SE','ORL':'SE','WAS':'SE','DEN':'NW','MIN':'NW','OKC':'NW','POR':'NW','UTA':'NW','GSW':'P','LAC':'P','LAL':'P','PHX':'P','SAC':'P','DAL':'SW','HOU':'SW','MEM':'SW','NOP':'SW','SAS':'SW'}
NHL_DIV = {'BOS':'A','BUF':'A','DET':'A','FLA':'A','MTL':'A','OTT':'A','TBL':'A','TOR':'A','CAR':'M','CBJ':'M','NJD':'M','NYI':'M','NYR':'M','PHI':'M','PIT':'M','WSH':'M','CHI':'C','COL':'C','DAL':'C','MIN':'C','NSH':'C','STL':'C','UTA':'C','WPG':'C','ANA':'P','CGY':'P','EDM':'P','LAK':'P','SJS':'P','SEA':'P','VAN':'P','VGK':'P'}
NHL_ESPN = {"LA":"LAK","NJ":"NJD","SJ":"SJS","TB":"TBL","UTAH":"UTA","UTA":"UTA"}
MLB_NAME = {'Arizona Diamondbacks':'ARI','Athletics':'ATH','Oakland Athletics':'ATH','Atlanta Braves':'ATL','Baltimore Orioles':'BAL','Boston Red Sox':'BOS','Chicago Cubs':'CHC','Chicago White Sox':'CWS','Cincinnati Reds':'CIN','Cleveland Guardians':'CLE','Colorado Rockies':'COL','Detroit Tigers':'DET','Houston Astros':'HOU','Kansas City Royals':'KC','Los Angeles Angels':'LAA','Los Angeles Dodgers':'LAD','Miami Marlins':'MIA','Milwaukee Brewers':'MIL','Minnesota Twins':'MIN','New York Mets':'NYM','New York Yankees':'NYY','Philadelphia Phillies':'PHI','Pittsburgh Pirates':'PIT','San Diego Padres':'SD','San Francisco Giants':'SF','Seattle Mariners':'SEA','St. Louis Cardinals':'STL','Tampa Bay Rays':'TB','Texas Rangers':'TEX','Toronto Blue Jays':'TOR','Washington Nationals':'WSH'}
MLB_DIV = {'BAL':'ALE','BOS':'ALE','NYY':'ALE','TB':'ALE','TOR':'ALE','CWS':'ALC','CLE':'ALC','DET':'ALC','KC':'ALC','MIN':'ALC','ATH':'ALW','HOU':'ALW','LAA':'ALW','SEA':'ALW','TEX':'ALW','ATL':'NLE','MIA':'NLE','NYM':'NLE','PHI':'NLE','WSH':'NLE','CHC':'NLC','CIN':'NLC','MIL':'NLC','PIT':'NLC','STL':'NLC','ARI':'NLW','COL':'NLW','LAD':'NLW','SD':'NLW','SF':'NLW'}
MLB_ESPN = {"CHW":"CWS","OAK":"ATH","WSN":"WSH","AZ":"ARI"}
NFL_MAP = {"LA":"LAR","WAS":"WSH"}

def espn_ab(sport, ab):
    if sport == "nba": return NBA_MAP.get(ab, ab)
    if sport == "nhl": return NHL_ESPN.get(ab, ab)
    if sport == "mlb": return MLB_ESPN.get(ab, ab)
    if sport == "nfl": return NFL_MAP.get(ab, ab)
    return ab

def season_year(month_start):
    d = TODAY_ET
    return d.year if d.month >= month_start else d.year - 1

# ---------------------------------------------------------------- NFL (nflverse)
def build_nfl():
    r = get(NFLVERSE, timeout=90)
    if r is None: return None
    df = pd.read_csv(io.StringIO(r.text))
    first = season_year(3) - 5
    df = df[(df.season >= first) & (df.game_type == "REG")]
    out = []
    for _, x in df.iterrows():
        roof = x.get("roof"); indoor = 1 if roof in ("dome", "closed") else 0 if roof in ("outdoors", "open") else None
        f = lambda v: None if pd.isna(v) else (float(v) if isinstance(v, float) and not float(v).is_integer() else int(v))
        out.append([int(x.season), int(x.week), x.gameday, x.gametime if isinstance(x.gametime, str) else "13:00",
                    NFL_MAP.get(x.away_team, x.away_team), NFL_MAP.get(x.home_team, x.home_team),
                    f(x.away_score), f(x.home_score), f(x.spread_line), f(x.total_line), f(x.away_moneyline), f(x.home_moneyline),
                    int(x.div_game or 0), indoor, f(x.away_rest), f(x.home_rest), 1 if x.location == "Neutral" else 0])
    log(f"NFL: {len(out)} games")
    return out

# ---------------------------------------------------------------- NBA / NHL / MLB / CFB (sportsdataverse)
def build_nba():
    cur = season_year(8) + 1  # file year = season end year
    games, names = [], {}
    for y in (cur - 2, cur - 1, cur):
        d = parquet(f"{SDV}/espn_nba_schedules/nba_schedule_{y}.parquet")
        if d is None: continue
        d = d[d.season_type == 2]
        for _, r in d.iterrows():
            a, h = NBA_MAP.get(r.away_abbreviation, r.away_abbreviation), NBA_MAP.get(r.home_abbreviation, r.home_abbreviation)
            if a not in NBA_DIV or h not in NBA_DIV: continue
            names[a], names[h] = r.away_display_name, r.home_display_name
            date, time_ = et(r.start_date); done = bool(r.status_type_completed)
            games.append(dict(season=int(r.season), date=date, time=time_, a=a, h=h, **{"as": int(r.away_score) if done else None},
                              hs=int(r.home_score) if done else None, div=1 if NBA_DIV[a] == NBA_DIV[h] else 0, neutral=1 if r.neutral_site else 0))
    log(f"NBA: {len(games)} games"); return add_rest(games), names

def build_nhl():
    cur = season_year(8) + 1
    games = []
    for y in (cur - 2, cur - 1, cur):
        d = parquet(f"{SDV}/nhl_schedules/nhl_schedule_{y}.parquet")
        if d is None: continue
        d = d[d.game_type == "R"]
        for _, r in d.iterrows():
            a, h = r.away_team_abbr, r.home_team_abbr
            if a not in NHL_DIV or h not in NHL_DIV: continue
            date, time_ = et(r.game_time); done = r.game_state in ("OFF", "FINAL")
            games.append(dict(season=int(r.season), date=date, time=time_, a=a, h=h, **{"as": int(r.away_score) if done else None},
                              hs=int(r.home_score) if done else None, div=1 if NHL_DIV[a] == NHL_DIV[h] else 0))
    log(f"NHL: {len(games)} games"); return add_rest(games)

def build_mlb():
    cur = season_year(3)
    games, names = [], {}
    for y in (cur - 2, cur - 1, cur):
        d = parquet(f"{SDV}/mlb_schedules/mlb_schedule_{y}.parquet")
        if d is None: continue
        d = d[d.game_type.isin(["R", "F", "D", "L", "W"])]
        for _, r in d.iterrows():
            a, h = MLB_NAME.get(r.away_team_name), MLB_NAME.get(r.home_team_name)
            if not a or not h: continue
            done = r.abstract_state == "Final" and nz(r.home_score) is not None
            if not done and r.detailed_state in ("Postponed", "Cancelled"): continue
            names[a], names[h] = r.away_team_name, r.home_team_name
            date, time_ = et(r.game_datetime)
            games.append(dict(season=int(y), date=date, time=time_, a=a, h=h, **{"as": int(r.away_score) if done else None},
                              hs=int(r.home_score) if done else None, div=1 if MLB_DIV[a] == MLB_DIV[h] else 0))
    log(f"MLB: {len(games)} games"); return add_rest(games), names

CFB_COORDS = {}
def build_cfb():
    cur = season_year(7)
    games, names, confs = [], {}, {}
    for y in range(cur - 3, cur + 1):
        m = parquet(f"{SDV}/cfb_matchup_line/cfb_matchup_line_{y}.parquet")
        s = parquet(f"{SDV}/cfb_schedules/cfb_schedules_{y}.parquet")
        if m is None: continue
        tm, tbd, rk = {}, {}, {}
        if s is not None:
            tm = dict(zip(s.game_id, s.start_date)); tbd = dict(zip(s.game_id, s.start_time_tbd))
            rk = {g: (nz(a), nz(h)) for g, a, h in zip(s.game_id, s.away_rank, s.home_rank)}
        for _, r in m.iterrows():
            a, h = r.away_abbreviation, r.home_abbreviation
            if not a or not h: continue
            st = tm.get(r.game_id)
            if st is None: date, time_ = str(r.start_date)[:10], "15:30"
            else:
                date, time_ = et(st)
                if tbd.get(r.game_id): time_ = "12:00"
            names[a], names[h] = r.away_team, r.home_team; confs[a], confs[h] = r.away_conference, r.home_conference
            done = nz(r.home_points) is not None and nz(r.away_points) is not None
            sp, tot = nz(r.spread), nz(r.over_under)
            ar, hr = rk.get(r.game_id, (None, None))
            g = dict(season=int(y), date=date, time=time_, a=a, h=h, **{"as": int(r.away_points) if done else None},
                     hs=int(r.home_points) if done else None, sl=-float(sp) if sp is not None else None,
                     tot=float(tot) if tot is not None else None, div=1 if r.conference_game else 0,
                     indoor=(1 if r.home_dome else 0) if not r.neutral_site else None, neutral=1 if r.neutral_site else 0,
                     ark=int(ar) if ar else None, hrk=int(hr) if hr else None)
            games.append(g)
            if not done and not r.neutral_site and not r.home_dome and nz(r.home_latitude) is not None:
                CFB_COORDS[f"cfb|{date}|{a}|{h}"] = (float(r.home_latitude), float(r.home_longitude), date, time_)
    log(f"CFB: {len(games)} games"); return add_rest(games), names, confs

# ---------------------------------------------------------------- ESPN line + results archive
ESPN_PATH = {"nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb", "ncaab": "basketball/mens-college-basketball",
             "nfl": "football/nfl", "cfb": "football/college-football"}
ESPN_GROUPS = {"ncaab": 50, "cfb": 80}

def parse_odds(sport, comp, home_raw, away_raw):
    odds = (comp.get("odds") or [None])[0]
    if not odds: return None
    out = {"prov": (odds.get("provider") or {}).get("name"), "tot": nz(odds.get("overUnder"))}
    hml = (odds.get("homeTeamOdds") or {}).get("moneyLine"); aml = (odds.get("awayTeamOdds") or {}).get("moneyLine")
    out["hml"], out["aml"] = hml, aml
    if sport in ("nba", "ncaab", "nfl", "cfb"):
        det = (odds.get("details") or "").strip()
        try:
            if det.upper() in ("EVEN", "PK", "PICK", "PICK'EM"): out["sl"] = 0.0
            elif det:
                fav, val = det.split()[0], float(det.split()[-1])
                home_line = val if fav == home_raw else -val if fav == away_raw else None
                if home_line is not None: out["sl"] = -home_line   # sl > 0 means home favored
        except Exception: pass
    return out

def espn_archive(sport):
    lines = load(f"lines_{sport}.json", {})
    results = load("results_ncaab.json", {}) if sport == "ncaab" else None
    names = load("names_ncaab.json", {}) if sport == "ncaab" else None
    n = 0
    for off in (-1, 0, 1):
        day = TODAY_ET + dt.timedelta(days=off)
        params = {"dates": day.strftime("%Y%m%d"), "limit": 500}
        if sport in ESPN_GROUPS: params["groups"] = ESPN_GROUPS[sport]
        js = get_json(f"{ESPN}/{ESPN_PATH[sport]}/scoreboard", params)
        if not js: continue
        for ev in js.get("events", []):
            try:
                comp = ev["competitions"][0]
                home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
                away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
                hr_, ar_ = home["team"]["abbreviation"], away["team"]["abbreviation"]
                h, a = espn_ab(sport, hr_), espn_ab(sport, ar_)
                date, time_ = et(ev["date"])
                key = f"{date}|{a}|{h}"
                state = ((comp.get("status") or {}).get("type") or {}).get("state")
                if state == "pre":
                    o = parse_odds(sport, comp, hr_, ar_)
                    if o and (o.get("tot") is not None or o.get("sl") is not None or o.get("hml") is not None):
                        o["ts"] = NOW.isoformat(timespec="minutes"); lines[key] = o; n += 1
                if sport == "ncaab":
                    names[a] = away["team"].get("shortDisplayName") or away["team"].get("displayName") or a
                    names[h] = home["team"].get("shortDisplayName") or home["team"].get("displayName") or h
                    done = state == "post"
                    yr = int(date[:4]) + (1 if int(date[5:7]) >= 8 else 0)
                    results[key] = dict(season=yr, date=date, time=time_, a=a, h=h, neutral=1 if comp.get("neutralSite") else 0,
                                        **{"as": int(away.get("score") or 0) if done else None}, hs=int(home.get("score") or 0) if done else None)
            except Exception as e:
                log(f"  skip event in {sport}: {e}")
    save(f"lines_{sport}.json", lines)
    if sport == "ncaab": save("results_ncaab.json", results); save("names_ncaab.json", names)
    log(f"ESPN {sport}: saved {n} pregame lines (archive {len(lines)})")
    return lines

def merge_lines(games, lines):
    hit = 0
    for g in games:
        o = lines.get(f"{g['date']}|{g['a']}|{g['h']}")
        if not o: continue
        if o.get("sl") is not None: g["sl"] = o["sl"]
        if o.get("tot") is not None: g["tot"] = float(o["tot"])
        if o.get("hml") is not None: g["hml"], g["aml"] = o["hml"], o.get("aml")
        if g.get("as") is not None: hit += 1
    return hit

# ---------------------------------------------------------------- injuries
INJ_PATH = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
def injuries(sport, name_to_ab):
    js = get_json(f"{ESPN}/{INJ_PATH[sport]}/injuries")
    out = {}
    if not js: return out
    for team in js.get("injuries", []) or []:
        tab = ((team.get("team") or {}).get("abbreviation")) or name_to_ab.get(team.get("displayName"))
        for it in team.get("injuries", []) or []:
            ath = it.get("athlete") or {}
            ab = espn_ab(sport, ((ath.get("team") or {}).get("abbreviation")) or tab or "")
            if not ab: continue
            out.setdefault(ab, []).append({"n": ath.get("displayName") or ath.get("fullName") or "?",
                                           "p": (ath.get("position") or {}).get("abbreviation") or "",
                                           "s": it.get("status") or (it.get("type") or {}).get("description") or "",
                                           "d": (it.get("shortComment") or "")[:140]})
    log(f"Injuries {sport}: {sum(len(v) for v in out.values())} players")
    return out

# ---------------------------------------------------------------- weather (Open-Meteo, outdoor football)
NFL_OUTDOOR = {"BAL":(39.278,-76.623),"BUF":(42.774,-78.787),"CAR":(35.226,-80.853),"CHI":(41.862,-87.617),"CIN":(39.095,-84.516),
  "CLE":(41.506,-81.699),"DEN":(39.744,-105.020),"GB":(44.501,-88.062),"JAX":(30.324,-81.637),"KC":(39.049,-94.484),"MIA":(25.958,-80.239),
  "NE":(42.091,-71.264),"NYG":(40.813,-74.074),"NYJ":(40.813,-74.074),"PHI":(39.901,-75.168),"PIT":(40.447,-80.016),"SF":(37.403,-121.970),
  "SEA":(47.595,-122.332),"TB":(27.976,-82.503),"TEN":(36.166,-86.771),"WSH":(38.908,-76.864)}
def weather(nfl_rows):
    spots = dict(CFB_COORDS)
    lim = TODAY_ET + dt.timedelta(days=7)
    for r in nfl_rows or []:
        if r[6] is not None or r[16]: continue
        d = dt.date.fromisoformat(r[2])
        if not (TODAY_ET <= d <= lim) or r[5] not in NFL_OUTDOOR: continue
        lat, lon = NFL_OUTDOOR[r[5]]; spots[f"nfl|{r[2]}|{r[4]}|{r[5]}"] = (lat, lon, r[2], r[3])
    out = {}
    for key, (lat, lon, date, time_) in spots.items():
        if not (TODAY_ET <= dt.date.fromisoformat(date) <= lim): continue
        js = get_json("https://api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon,
              "hourly": "temperature_2m,wind_speed_10m,precipitation_probability", "temperature_unit": "fahrenheit",
              "wind_speed_unit": "mph", "timezone": "America/New_York", "start_date": date, "end_date": date})
        if not js or "hourly" not in js: continue
        hrs = js["hourly"].get("time", []); want = f"{date}T{time_[:2]}:00"
        i = hrs.index(want) if want in hrs else 0
        H = js["hourly"]
        out[key] = {"temp": H["temperature_2m"][i], "wind": H["wind_speed_10m"][i],
                    "rain": (H.get("precipitation_probability") or [None])[i]}
        time.sleep(0.2)
    log(f"Weather: {len(out)} outdoor games")
    return out

# ---------------------------------------------------------------- optional live odds (The Odds API)
def odds():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key: return None, None
    meta = load("odds_meta.json", {})
    every = float(os.environ.get("ODDS_EVERY_HOURS", "6"))
    last = meta.get("last")
    if last and (NOW - dt.datetime.fromisoformat(last)).total_seconds() < every * 3600 - 300:
        return load("odds.json", None), last
    out = {}
    for sport, k in (("nhl", "icehockey_nhl"), ("nfl", "americanfootball_nfl")):
        js = get_json(f"https://api.the-odds-api.com/v4/sports/{k}/odds", {"apiKey": key, "regions": "us",
              "markets": "h2h,spreads,totals", "oddsFormat": "american", "bookmakers": "draftkings,fanduel,betmgm,williamhill_us,pinnacle"})
        if isinstance(js, list): out[sport] = js
    if out:
        save("odds.json", out); meta["last"] = NOW.isoformat(); save("odds_meta.json", meta)
        log(f"Odds: {', '.join(f'{k} {len(v)}' for k, v in out.items())}")
        return out, meta["last"]
    return load("odds.json", None), last


# ---------------------------------------------------------------- Kalshi + Polymarket
NFL_FULL = {"ARI":"Arizona Cardinals","ATL":"Atlanta Falcons","BAL":"Baltimore Ravens","BUF":"Buffalo Bills","CAR":"Carolina Panthers","CHI":"Chicago Bears","CIN":"Cincinnati Bengals","CLE":"Cleveland Browns","DAL":"Dallas Cowboys","DEN":"Denver Broncos","DET":"Detroit Lions","GB":"Green Bay Packers","HOU":"Houston Texans","IND":"Indianapolis Colts","JAX":"Jacksonville Jaguars","KC":"Kansas City Chiefs","LV":"Las Vegas Raiders","LAC":"Los Angeles Chargers","LAR":"Los Angeles Rams","MIA":"Miami Dolphins","MIN":"Minnesota Vikings","NE":"New England Patriots","NO":"New Orleans Saints","NYG":"New York Giants","NYJ":"New York Jets","PHI":"Philadelphia Eagles","PIT":"Pittsburgh Steelers","SF":"San Francisco 49ers","SEA":"Seattle Seahawks","TB":"Tampa Bay Buccaneers","TEN":"Tennessee Titans","WSH":"Washington Commanders"}
NHL_FULL = {"ANA":"Anaheim Ducks","BOS":"Boston Bruins","BUF":"Buffalo Sabres","CGY":"Calgary Flames","CAR":"Carolina Hurricanes","CHI":"Chicago Blackhawks","COL":"Colorado Avalanche","CBJ":"Columbus Blue Jackets","DAL":"Dallas Stars","DET":"Detroit Red Wings","EDM":"Edmonton Oilers","FLA":"Florida Panthers","LAK":"Los Angeles Kings","MIN":"Minnesota Wild","MTL":"Montreal Canadiens","NSH":"Nashville Predators","NJD":"New Jersey Devils","NYI":"New York Islanders","NYR":"New York Rangers","OTT":"Ottawa Senators","PHI":"Philadelphia Flyers","PIT":"Pittsburgh Penguins","SJS":"San Jose Sharks","SEA":"Seattle Kraken","STL":"St. Louis Blues","TBL":"Tampa Bay Lightning","TOR":"Toronto Maple Leafs","UTA":"Utah Mammoth","VAN":"Vancouver Canucks","VGK":"Vegas Golden Knights","WSH":"Washington Capitals","WPG":"Winnipeg Jets"}
TWO = ("Maple Leafs","Red Wings","Blue Jackets","Golden Knights","Red Sox","White Sox","Blue Jays","Trail Blazers")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = {"nfl":"KXNFLGAME","nhl":"KXNHLGAME","nba":"KXNBAGAME","mlb":"KXMLBGAME","cfb":"KXNCAAFGAME","ncaab":"KXNCAAMBGAME"}
POLY = "https://gamma-api.polymarket.com"
POLY_TAGS = {"nfl":["nfl"],"nhl":["nhl"],"nba":["nba"],"mlb":["mlb"],"cfb":["cfb","ncaaf","college-football"],"ncaab":["ncaab","cbb","college-basketball"]}
MONTHS = {m:i for i,m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"],1)}

def team_tokens(ab, full):
    full = full or ab
    toks = {ab.lower(), full.lower()}
    nick = next((t for t in TWO if full.endswith(t)), full.split()[-1])
    city = full[: len(full) - len(nick)].strip()
    for t in (nick, city): 
        if t: toks.add(t.lower())
    return toks

def same_team(x, toks):
    x = (x or "").strip().lower()
    if not x: return False
    if x in toks: return True
    return any(len(t) >= 4 and (t in x or (len(x) >= 4 and x in t)) for t in toks)

def candidates(sport, games, names):
    lo, hi = TODAY_ET - dt.timedelta(days=1), TODAY_ET + dt.timedelta(days=8)
    out = []
    for g in games:
        d = dt.date.fromisoformat(g["date"])
        if lo <= d <= hi and g.get("as") is None:
            out.append(dict(date=g["date"], time=g.get("time"), a=g["a"], h=g["h"],
                            ta=team_tokens(g["a"], names.get(g["a"])), th=team_tokens(g["h"], names.get(g["h"]))))
    return out

def pick_game(cands, date, strs):
    best = None
    for c in cands:
        if date and abs((dt.date.fromisoformat(c["date"]) - dt.date.fromisoformat(date)).days) > 1: continue
        ma = any(same_team(x, c["ta"]) for x in strs); mh = any(same_team(x, c["th"]) for x in strs)
        if ma and mh:
            score = 0 if c["date"] == date else 1
            if best is None or score < best[0]: best = (score, c)
    return best[1] if best else None

def kalshi(sport, cands):
    out = {}
    if sport not in KALSHI_SERIES or not cands: return out
    cursor, pages = None, 0
    while pages < 5:
        params = {"series_ticker": KALSHI_SERIES[sport], "status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor: params["cursor"] = cursor
        js = get_json(f"{KALSHI}/events", params); pages += 1
        if not js: break
        for ev in js.get("events", []) or []:
            mk = ev.get("markets") or []
            m_ = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev.get("event_ticker", ""))
            date = f"20{m_.group(1)}-{MONTHS.get(m_.group(2),1):02d}-{m_.group(3)}" if m_ else None
            strs = []
            for m in mk: strs += [m.get("ticker", "").rsplit("-", 1)[-1], m.get("yes_sub_title") or ""]
            g = pick_game(cands, date, strs)
            if not g: continue
            rec = {"vol": 0}
            for m in mk:
                code, sub = m.get("ticker", "").rsplit("-", 1)[-1], m.get("yes_sub_title") or ""
                side = "a" if (same_team(code, g["ta"]) or same_team(sub, g["ta"])) else "h" if (same_team(code, g["th"]) or same_team(sub, g["th"])) else None
                if not side: continue
                f = lambda k: float(m.get(k) or 0)
                bid, ask, last = f("yes_bid_dollars"), f("yes_ask_dollars"), f("last_price_dollars")
                if not (bid or ask or last):  # older cent fields
                    bid, ask, last = (m.get("yes_bid") or 0)/100, (m.get("yes_ask") or 0)/100, (m.get("last_price") or 0)/100
                rec[side] = {"bid": bid, "ask": ask, "last": last, "t": m.get("ticker")}
                rec["vol"] += int(float(m.get("volume_fp") or m.get("volume") or 0))
            if "a" in rec or "h" in rec:
                out[f"{g['date']}|{g['a']}|{g['h']}"] = rec
        cursor = js.get("cursor")
        if not cursor: break
    log(f"Kalshi {sport}: {len(out)} games matched")
    return out

def polymarket(sport, cands):
    out = {}
    if not cands: return out
    for tag in POLY_TAGS.get(sport, []):
        js = get_json(f"{POLY}/events", {"tag_slug": tag, "active": "true", "closed": "false", "limit": 300})
        if not isinstance(js, list): continue
        for ev in js:
            for m in ev.get("markets") or []:
                try:
                    outs = m.get("outcomes"); prices = m.get("outcomePrices")
                    outs = json.loads(outs) if isinstance(outs, str) else outs
                    prices = [float(p) for p in (json.loads(prices) if isinstance(prices, str) else prices or [])]
                    if not outs or len(outs) != 2 or len(prices) != 2: continue
                    if {o.lower() for o in outs} & {"yes", "no", "over", "under"}: continue
                    q = (m.get("question") or "") + " " + (m.get("sportsMarketType") or "")
                    if re.search(r"spread|o/u|total|1st|first half|half|quarter|period|inning|\(-|\(\+", q, re.I): continue
                    when = m.get("gameStartTime") or ev.get("startDate") or m.get("endDate")
                    date = et(when)[0] if when else None
                    g = pick_game(cands, date, outs)
                    if not g: continue
                    rec = {"vol": int(float(m.get("volume") or 0)), "slug": ev.get("slug")}
                    for o, p in zip(outs, prices):
                        if same_team(o, g["ta"]): rec["a"] = p
                        elif same_team(o, g["th"]): rec["h"] = p
                    if "a" in rec and "h" in rec: out[f"{g['date']}|{g['a']}|{g['h']}"] = rec
                except Exception as e:
                    log(f"  poly skip: {e}")
        if out: break
    log(f"Polymarket {sport}: {len(out)} games matched")
    return out

# ---------------------------------------------------------------- line history for move alerts
def update_history(snaps):
    """snaps: {key: {sl, tot, hml, k, p}} for upcoming games. Keeps the first snapshot (opener) plus the last 71."""
    hist = load("history.json", {})
    t = int(NOW.timestamp() // 60)
    for key, s in snaps.items():
        row = [t, s.get("sl"), s.get("tot"), s.get("hml"), s.get("k"), s.get("p")]
        if all(v is None for v in row[1:]): continue
        arr = hist.setdefault(key, [])
        if arr and arr[-1][1:] == row[1:]: continue        # unchanged since last snapshot
        arr.append(row)
        if len(arr) > 72: hist[key] = [arr[0]] + arr[-71:]
    cutoff = (TODAY_ET - dt.timedelta(days=2)).isoformat()
    hist = {k: v for k, v in hist.items() if k.split("|")[1] >= cutoff}
    save("history.json", hist)
    log(f"History: {len(hist)} games tracked")
    return hist

# ---------------------------------------------------------------- main
def main():
    os.makedirs(DATA, exist_ok=True)
    old = {}
    try:
        with open(os.path.join(DATA, "bundle.js")) as f:
            txt = f.read(); old = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception: pass
    B = {}
    def step(name, fn):
        try: return fn()
        except Exception:
            log(f"{name} failed:\n{traceback.format_exc()}"); return None

    nfl = step("nfl", build_nfl);                B["NFLV_ROWS"] = nfl or old.get("NFLV_ROWS")
    nba = step("nba", build_nba)
    nhl = step("nhl", build_nhl)
    mlb = step("mlb", build_mlb)
    cfb = step("cfb", build_cfb)

    archive = {}
    for sp, res in (("nba", nba and nba[0]), ("nhl", nhl), ("mlb", mlb and mlb[0])):
        lines = step(f"espn {sp}", lambda sp=sp: espn_archive(sp)) or load(f"lines_{sp}.json", {})
        if res: archive[sp] = merge_lines(res, lines)
    all_lines = {}
    for sp in ("nba", "nhl", "mlb"):
        all_lines[sp] = load(f"lines_{sp}.json", {})
    for sp in ("nfl", "cfb"):
        all_lines[sp] = step(f"espn {sp}", lambda sp=sp: espn_archive(sp)) or load(f"lines_{sp}.json", {})
    nc_lines = step("espn ncaab", lambda: espn_archive("ncaab")) or {}
    all_lines["ncaab"] = nc_lines
    nc = list(load("results_ncaab.json", {}).values())
    if nc:
        add_rest(nc); archive["ncaab"] = merge_lines(nc, nc_lines)
        B["NCAAB_ROWS"] = to_rows(nc); B["NCAAB_NAMES"] = load("names_ncaab.json", {})

    if nba: B["NBA_ROWS"], B["NBA_NAMES"], B["NBA_DIVS"] = to_rows(nba[0]), nba[1], NBA_DIV
    else:
        for k in ("NBA_ROWS", "NBA_NAMES", "NBA_DIVS"): B[k] = old.get(k)
    B["NHL_ROWS"] = to_rows(nhl) if nhl else old.get("NHL_ROWS")
    if mlb: B["MLB_ROWS"], B["MLB_NAMES"], B["MLB_DIV"] = to_rows(mlb[0]), mlb[1], MLB_DIV
    else:
        for k in ("MLB_ROWS", "MLB_NAMES", "MLB_DIV"): B[k] = old.get(k)
    if cfb: B["CFB_ROWS"], B["CFB_NAMES"], B["CFB_CONF"] = to_rows(cfb[0]), cfb[1], cfb[2]
    else:
        for k in ("CFB_ROWS", "CFB_NAMES", "CFB_CONF"): B[k] = old.get(k)
    B["ARCHIVE"] = archive

    # prediction markets, matched to our schedule
    nfl_games = [dict(date=r[2], time=r[3], a=r[4], h=r[5], **{"as": r[6]}) for r in (B.get("NFLV_ROWS") or [])]
    sched = {"nfl": (nfl_games, NFL_FULL), "nhl": (nhl or [], NHL_FULL),
             "nba": (nba[0] if nba else [], nba[1] if nba else {}), "mlb": (mlb[0] if mlb else [], mlb[1] if mlb else {}),
             "cfb": (cfb[0] if cfb else [], cfb[1] if cfb else {}), "ncaab": (nc, load("names_ncaab.json", {}))}
    MK = {}
    for sp, (games, names) in sched.items():
        c = candidates(sp, games, names)
        k = step(f"kalshi {sp}", lambda sp=sp, c=c: kalshi(sp, c)) or {}
        p = step(f"polymarket {sp}", lambda sp=sp, c=c: polymarket(sp, c)) or {}
        recs = []
        for key in set(k) | set(p):
            d, a, h = key.split("|")
            recs.append({"date": d, "a": a, "h": h, "k": k.get(key), "p": p.get(key)})
        if recs: MK[sp] = sorted(recs, key=lambda r: r["date"])
    B["MARKETS"] = MK
    B["MARKETS_TIME"] = NOW.isoformat(timespec="minutes")

    # hourly snapshot of lines + market prices for move alerts
    def mid(x):
        if not x: return None
        return round((x["bid"] + x["ask"]) / 2, 3) if x.get("bid") and x.get("ask") else (x.get("last") or None)
    snaps = {}
    for sp, lines in all_lines.items():
        for key, o in (lines or {}).items():
            if o.get("ts", "")[:13] == NOW.isoformat(timespec="minutes")[:13] and key.split("|")[0] >= TODAY_ET.isoformat():
                snaps[f"{sp}|{key}"] = {"sl": o.get("sl"), "tot": o.get("tot"), "hml": o.get("hml")}
    for sp, recs in MK.items():
        for r in recs:
            s = snaps.setdefault(f"{sp}|{r['date']}|{r['a']}|{r['h']}", {})
            s["k"] = mid((r.get("k") or {}).get("h")); s["p"] = (r.get("p") or {}).get("h")
    B["HISTORY"] = step("history", lambda: update_history(snaps)) or load("history.json", {})

    nfl_names = {}
    B["INJURIES"] = {sp: step(f"injuries {sp}", lambda sp=sp: injuries(sp, nfl_names)) or (old.get("INJURIES") or {}).get(sp, {})
                     for sp in ("nfl", "nba", "nhl", "mlb")}
    B["WEATHER"] = step("weather", lambda: weather(B["NFLV_ROWS"])) or old.get("WEATHER") or {}
    o = step("odds", odds)
    if o and o[0]: B["ODDS"], B["ODDS_TIME"] = o
    B["UPDATED"] = NOW.astimezone(ET).strftime("%b %-d, %Y %-I:%M %p ET")
    B = {k: v for k, v in B.items() if v is not None}
    with open(os.path.join(DATA, "bundle.js"), "w") as f:
        f.write("window.SLB=" + json.dumps(B, separators=(",", ":")) + ";\n")
    with open(os.path.join(DATA, "last_run.txt"), "w") as f: f.write("\n".join(LOG) + "\n")
    log(f"Wrote bundle.js ({os.path.getsize(os.path.join(DATA, 'bundle.js'))//1024} KB)")

if __name__ == "__main__":
    main()
