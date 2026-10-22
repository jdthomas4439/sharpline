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

SAMPLES = {}
def parse_odds(sport, comp, home_raw, away_raw):
    odds = (comp.get("odds") or [None])[0]
    if not odds: return None
    out = {"prov": (odds.get("provider") or {}).get("name"), "tot": nz(odds.get("overUnder"))}
    hml = (odds.get("homeTeamOdds") or {}).get("moneyLine"); aml = (odds.get("awayTeamOdds") or {}).get("moneyLine")
    if hml is None or aml is None:                       # newer ESPN format: odds.moneyline.home.close.odds = "-148"
        ml = odds.get("moneyline") or {}
        def pick(side):
            d = ml.get(side) or {}
            for stage in ("close", "current", "open"):
                v = (d.get(stage) or {}).get("odds")
                if v not in (None, ""):
                    v = str(v).strip().upper()
                    if v in ("EVEN", "EV"): return 100
                    try: return int(float(v.replace("+", "")))
                    except Exception: pass
            return None
        hml, aml = hml if hml is not None else pick("home"), aml if aml is not None else pick("away")
    out["hml"], out["aml"] = hml, aml
    if not SAMPLES.get(sport): SAMPLES[sport] = odds      # one raw example per sport, saved for checking
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
                        o["ts"] = NOW.isoformat(timespec="minutes"); o["start"] = ev["date"]; lines[key] = o; n += 1
                if state == "post":
                    try: FINALS[f"{sport}|{key}"] = [int(away.get("score") or 0), int(home.get("score") or 0)]
                    except Exception: pass
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
    started, fails = time.time(), 0
    for key, (lat, lon, date, time_) in spots.items():
        if not (TODAY_ET <= dt.date.fromisoformat(date) <= lim): continue
        if fails >= 3 or time.time() - started > 120:        # weather is optional: never let it stall the run
            log(f"Weather: stopped early ({fails} failures)"); break
        r = get("https://api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon,
              "hourly": "temperature_2m,wind_speed_10m,precipitation_probability", "temperature_unit": "fahrenheit",
              "wind_speed_unit": "mph", "timezone": "America/New_York", "start_date": date, "end_date": date}, tries=1, timeout=8)
        try: js = r.json() if r is not None else None
        except Exception: js = None
        if not js or "hourly" not in js:
            fails += 1; continue
        fails = 0
        hrs = js["hourly"].get("time", []); want = f"{date}T{time_[:2]}:00"
        i = hrs.index(want) if want in hrs else 0
        H = js["hourly"]
        out[key] = {"temp": H["temperature_2m"][i], "wind": H["wind_speed_10m"][i],
                    "rain": (H.get("precipitation_probability") or [None])[i]}
        time.sleep(0.3)
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
POLY_TAGS = {"nfl":["nfl"],"nhl":["nhl"],"nba":["nba"],"mlb":["mlb"],"cfb":["college-football","ncaaf","cfb","ncaa-football"],"ncaab":["ncaab","cbb","college-basketball"]}
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
        js = []
        for off in (0, 300, 600, 900):
            page = get_json(f"{POLY}/events", {"tag_slug": tag, "active": "true", "closed": "false", "limit": 300, "offset": off,
                                               "end_date_min": (TODAY_ET - dt.timedelta(days=1)).isoformat()})
            if not isinstance(page, list) or not page: break
            js += page
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
                    if rec["vol"] <= 0: continue   # placeholder market with no trading yet
                    ba, bb = m.get("bestAsk"), m.get("bestBid")          # quoted for the first outcome
                    asks = [float(ba) if ba not in (None, "") else None, (1 - float(bb)) if bb not in (None, "") else None]
                    for i, (o, p) in enumerate(zip(outs, prices)):
                        side = "a" if same_team(o, g["ta"]) else "h" if same_team(o, g["th"]) else None
                        if not side: continue
                        rec[side] = p
                        if asks[i] is not None and 0 < asks[i] < 1: rec[side + "ask"] = round(asks[i], 3)
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


# ---------------------------------------------------------------- paper-trading test: system picks, graded after the game
FINALS = {}
STRAT_NAMES = {"gap": "Market gap", "steam": "Steam follow", "angle": "Situational angles"}
SPREAD_TH = {"nfl": 1.5, "cfb": 2.0, "nba": 2.0, "ncaab": 2.5}
TOTAL_TH = {"nfl": 2.0, "cfb": 3.0, "nba": 3.0, "ncaab": 3.0, "nhl": 0.5, "mlb": 0.5}

def _dec(a): return 1 + a / 100 if a > 0 else 1 + 100 / -a
def _imp(a): return 1 / _dec(a)

def make_and_grade_picks(all_lines, MK, hist, B=None):
    P = load("picks.json", None)
    if not P:
        P = {"start": NOW.isoformat(timespec="minutes"), "end": (NOW + dt.timedelta(days=7)).isoformat(timespec="minutes"), "picks": []}
    have = {(p["strat"], p["key"], p["mkt"]) for p in P["picks"]}
    picked_games = {p["key"] for p in P["picks"]}
    mk = {f"{sp}|{r['date']}|{r['a']}|{r['h']}": r for sp, recs in MK.items() for r in recs}
    new = []
    now_h = NOW.isoformat(timespec="minutes")[:13]
    for sp, lines in all_lines.items():
        for k, o in (lines or {}).items():
            key = f"{sp}|{k}"
            if o.get("ts", "")[:13] != now_h or not o.get("start"): continue          # only lines seen pregame this hour
            mins = (dt.datetime.fromisoformat(o["start"].replace("Z", "+00:00")) - NOW).total_seconds() / 60
            if mins < 20: continue
            # Strategy 1, market gap: prediction market vs the sportsbook moneyline (12 hours or less to start)
            r = mk.get(key)
            if mins <= 720 and r and o.get("hml") is not None and o.get("aml") is not None and ("gap", key, "ml") not in have:
                k_ = r.get("k") or {}
                kh = k_.get("h") or {}
                pm = ((kh.get("bid", 0) + kh.get("ask", 0)) / 2) if kh.get("bid") and kh.get("ask") else ((r.get("p") or {}).get("h"))
                if pm:
                    ph, pa = _imp(o["hml"]), _imp(o["aml"]); fh = ph / (ph + pa)
                    for side, prob, odds in (("home", pm, o["hml"]), ("away", 1 - pm, o["aml"])):
                        book_fair = fh if side == "home" else 1 - fh
                        ev = prob * _dec(odds) - 1
                        if prob - book_fair >= 0.03 and ev >= 0.02:
                            new.append({"strat": "gap", "key": key, "sport": sp, "mkt": "ml", "side": side, "odds": odds, "line": None,
                                        "fair": round(prob, 3), "edge": round(ev, 3), "start": o["start"], "at": NOW.isoformat(timespec="minutes")})
                            break
            # Strategy 2, steam follow: a big line move, taken within 3 hours of start
            H = hist.get(key) or []
            if mins <= 180 and len(H) >= 2:
                fs = next((h for h in H if h[1] is not None), None); ls = next((h for h in reversed(H) if h[1] is not None), None)
                th = SPREAD_TH.get(sp)
                if th and fs and ls and abs(ls[1] - fs[1]) >= th and ("steam", key, "spread") not in have:
                    side = "home" if ls[1] > fs[1] else "away"            # sl rising = money on home
                    line = -ls[1] if side == "home" else ls[1]
                    new.append({"strat": "steam", "key": key, "sport": sp, "mkt": "spread", "side": side, "odds": -110, "line": line,
                                "move": round(ls[1] - fs[1], 1), "start": o["start"], "at": NOW.isoformat(timespec="minutes")})
                ft = next((h for h in H if h[2] is not None), None); lt = next((h for h in reversed(H) if h[2] is not None), None)
                tt = TOTAL_TH.get(sp)
                if tt and ft and lt and abs(lt[2] - ft[2]) >= tt and ("steam", key, "total") not in have:
                    side = "over" if lt[2] > ft[2] else "under"
                    new.append({"strat": "steam", "key": key, "sport": sp, "mkt": "total", "side": side, "odds": -110, "line": lt[2],
                                "move": round(lt[2] - ft[2], 1), "start": o["start"], "at": NOW.isoformat(timespec="minutes")})
    # cap: best 8 new market-gap picks per run so one noisy run can't flood the test
    gaps = sorted([p for p in new if p["strat"] == "gap"], key=lambda p: -p["edge"])[:8]
    new = gaps + [p for p in new if p["strat"] != "gap"]
    if B is not None:
        try: new += angle_picks(P, B, have)
        except Exception: log(f"angle picks failed:\n{traceback.format_exc()}")
    P["picks"].extend(new)
    # grade anything finished
    graded = 0
    for p in P["picks"]:
        sp, k = p["sport"], p["key"].split("|", 1)[1]
        o = (all_lines.get(sp) or {}).get(k) or {}
        if p.get("result") is None and o:                              # last pregame number we saw = closing line
            if p["mkt"] == "ml": p["close"] = o.get("hml") if p["side"] == "home" else o.get("aml")
            elif p["mkt"] == "spread" and o.get("sl") is not None: p["close"] = -o["sl"] if p["side"] == "home" else o["sl"]
            elif p["mkt"] == "total": p["close"] = o.get("tot")
        f = FINALS.get(p["key"]) or (rows_final(B, p["key"]) if B is not None else None)
        if p.get("result") is not None or not f: continue
        a, h = f
        if p["mkt"] == "ml":
            m = h - a if p["side"] == "home" else a - h
            res = "push" if m == 0 else "win" if m > 0 else "loss"
        elif p["mkt"] == "spread":
            m = (h - a if p["side"] == "home" else a - h) + p["line"]
            res = "push" if m == 0 else "win" if m > 0 else "loss"
        else:
            s = a + h
            res = "push" if s == p["line"] else "win" if (s > p["line"]) == (p["side"] == "over") else "loss"
        p["result"], p["score"] = res, f"{a}-{h}"
        p["units"] = round(_dec(p["odds"]) - 1, 3) if res == "win" else -1.0 if res == "loss" else 0.0
        graded += 1
    save("picks.json", P)
    log(f"Test picks: {len(new)} new, {graded} graded, {len(P['picks'])} total")
    return P



# ---------------------------------------------------------------- strategy 3: nightly top-5 situational angles
def _rows_as_dicts(sp, rows):
    out = []
    for r in rows or []:
        if sp == "nfl":
            season, _wk, date, time_, a, h, as_, hs, sl, tot, aml, hml, div, indoor, ar, hr, neu = r[:17]
        else:
            season, date, time_, a, h, as_, hs, sl, tot, aml, hml, div, indoor, ar, hr, neu = r[:16]
        out.append(dict(season=season, date=date, time=time_ or "19:00", a=a, h=h, as_=as_, hs=hs, sl=sl, tot=tot,
                        aml=aml, hml=hml, div=div, played=as_ is not None and hs is not None))
    return out

def _team_log(games, ab):
    asc = []
    for x in sorted((g for g in games if g["played"] and ab in (g["a"], g["h"])), key=lambda g: g["date"]):
        home = x["h"] == ab
        asc.append(dict(season=x["season"], date=x["date"], home=home, opp=x["a"] if home else x["h"],
                        pf=x["hs"] if home else x["as_"], pa=x["as_"] if home else x["hs"],
                        line=None if x["sl"] is None else (-x["sl"] if home else x["sl"]), tot=x["tot"], div=x["div"]))
    for i, x in enumerate(asc):
        p = asc[i - 1] if i else None
        x["prev"] = ("W" if p["pf"] > p["pa"] else "L" if p["pf"] < p["pa"] else "T") if p and p["season"] == x["season"] else None
    return list(reversed(asc))

ANGLE_SPORTS = {"nfl": "NFLV_ROWS", "cfb": "CFB_ROWS", "nba": "NBA_ROWS", "nhl": "NHL_ROWS", "mlb": "MLB_ROWS"}
REAL_LINES = {"nfl", "cfb"}

def angle_candidates(sp, games, g):
    real, cur = sp in REAL_LINES, max(x["season"] for x in games)
    minn = 6 if real else 10
    out = []
    for ab, is_home in ((g["a"], False), (g["h"], True)):
        log_ = _team_log(games, ab)
        if not log_: continue
        opp = g["h"] if ab == g["a"] else g["a"]
        recent = [x for x in log_ if x["season"] >= cur - 2]
        last = log_[0]
        prev = ("W" if last["pf"] > last["pa"] else "L" if last["pf"] < last["pa"] else "T") if last["season"] == cur else None
        line_now = None if g["sl"] is None else (-g["sl"] if is_home else g["sl"])
        splits = [("last 10", log_[:10]), ("this season", [x for x in log_ if x["season"] == cur]),
                  ("at home" if is_home else "on the road", [x for x in recent if x["home"] == is_home]),
                  (f"vs {opp}", [x for x in log_ if x["opp"] == opp])]
        if g["div"]: splits.append(("conference games" if sp == "cfb" else "division games", [x for x in recent if x["div"]]))
        if prev in ("W", "L"): splits.append(("after a win" if prev == "W" else "after a loss", [x for x in recent if x["prev"] == prev]))
        if real and line_now not in (None, 0):
            fav = line_now < 0
            splits.append(("as a favorite" if fav else "as an underdog", [x for x in recent if x["line"] is not None and ((x["line"] < 0) if fav else (x["line"] > 0))]))
        for label, arr in splits:
            if real:
                ats = [x for x in arr if x["line"] is not None and x["pf"] - x["pa"] + x["line"] != 0]
                if len(ats) >= minn and line_now is not None:
                    w = sum(1 for x in ats if x["pf"] - x["pa"] + x["line"] > 0); p = w / len(ats)
                    if p >= .7 or p <= .3:
                        side = ("home" if is_home else "away") if p >= .5 else ("away" if is_home else "home")
                        out.append(dict(score=abs(p - .5) * len(ats) ** .5, mkt="spread", side=side,
                                        line=(-g["sl"] if side == "home" else g["sl"]), odds=-110,
                                        why=f"{ab} {w}–{len(ats)-w} against the spread, {label}" + ("" if p >= .5 else " (fading them)")))
                ous = [x for x in arr if x["tot"] is not None and x["pf"] + x["pa"] != x["tot"]]
                if len(ous) >= minn and g["tot"] is not None:
                    o = sum(1 for x in ous if x["pf"] + x["pa"] > x["tot"]); p = o / len(ous)
                    if p >= .7 or p <= .3:
                        out.append(dict(score=abs(p - .5) * len(ous) ** .5, mkt="total", side="over" if p >= .5 else "under",
                                        line=g["tot"], odds=-110,
                                        why=f"{ab} games {'over' if p >= .5 else 'under'} the closing total {o if p >= .5 else len(ous)-o} of {len(ous)}, {label}"))
            su = [x for x in arr if x["pf"] != x["pa"]]
            if len(su) >= minn and g["hml"] is not None and g["aml"] is not None:
                w = sum(1 for x in su if x["pf"] > x["pa"]); p = w / len(su)
                if p >= .75 or p <= .25:
                    side = ("home" if is_home else "away") if p >= .5 else ("away" if is_home else "home")
                    odds = g["hml"] if side == "home" else g["aml"]
                    if -200 <= odds <= 200:                       # skip heavy chalk, where a great record is already priced in
                        out.append(dict(score=abs(p - .5) * len(su) ** .5 * 0.7, mkt="ml", side=side, line=None, odds=odds,
                                        why=f"{ab} {w}–{len(su)-w} straight up, {label}" + ("" if p >= .5 else " (fading them)")))
    return out

def angle_picks(P, B, have):
    today = TODAY_ET.isoformat()
    already = [p for p in P["picks"] if p["strat"] == "angle" and p["key"].split("|")[1] == today]
    need = 5 - len(already)
    if need <= 0: return []
    taken = {p["key"] for p in already}
    pool = []
    for sp, rk in ANGLE_SPORTS.items():
        games = _rows_as_dicts(sp, B.get(rk))
        if not games: continue
        for g in games:
            if g["played"] or g["date"] != today: continue
            start = dt.datetime.fromisoformat(f"{g['date']}T{g['time']}:00").replace(tzinfo=ET)
            mins = (start - NOW).total_seconds() / 60
            if mins < 20: continue
            key = f"{sp}|{g['date']}|{g['a']}|{g['h']}"
            if key in taken: continue
            for c in angle_candidates(sp, games, g):
                pool.append((c["score"], key, sp, start, c))
    pool.sort(key=lambda t: -t[0])
    new, used = [], set()
    for score, key, sp, start, c in pool:
        if key in used or ("angle", key, c["mkt"]) in have: continue
        used.add(key)
        new.append({"strat": "angle", "key": key, "sport": sp, "mkt": c["mkt"], "side": c["side"], "odds": c["odds"], "line": c["line"],
                    "why": c["why"], "rank": round(score, 2), "start": start.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
                    "at": NOW.isoformat(timespec="minutes")})
        if len(new) >= need: break
    return new

def rows_final(B, key):
    sp, date, a, h = key.split("|")
    for g in _rows_as_dicts(sp, B.get(ANGLE_SPORTS.get(sp, ""), [])):
        if g["played"] and g["a"] == a and g["h"] == h and g["date"] == date: return [g["as_"], g["hs"]]
    return None

# ---------------------------------------------------------------- main
def main():
    os.makedirs(DATA, exist_ok=True)
    FINALS.update(load("finals.json", {}))
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
    B["PICKS"] = step("picks", lambda: make_and_grade_picks(all_lines, MK, B["HISTORY"], B)) or load("picks.json", None)
    if SAMPLES: save("espn_odds_sample.json", SAMPLES)
    cut = (TODAY_ET - dt.timedelta(days=14)).isoformat()
    save("finals.json", {k: v for k, v in FINALS.items() if k.split("|")[1] >= cut})

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
