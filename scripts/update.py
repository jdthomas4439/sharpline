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
            if r.status_code in (400, 401, 403, 404, 422): return None
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

def to_rows(games, extra=False):
    out = []
    for g in sorted(games, key=lambda x: (x["date"], x["time"])):
        row = [g["season"], g["date"], g["time"], g["a"], g["h"], g.get("as"), g.get("hs"), g.get("sl"), g.get("tot"),
               g.get("aml"), g.get("hml"), g.get("div", 0), g.get("indoor"), g.get("ar"), g.get("hr"), g.get("neutral", 0),
               g.get("ark"), g.get("hrk")]
        if extra: row += [g.get("dml"), g.get("lg")]
        out.append(row)
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
NFL_WX = {}
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
        if indoor == 0 and not pd.isna(x.get("temp")) and not pd.isna(x.home_score):     # kickoff weather for played outdoor games
            NFL_WX[f"{x.gameday}|{out[-1][4]}|{out[-1][5]}"] = [int(x.temp), None if pd.isna(x.get("wind")) else int(x.wind)]
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
    def tprice(side):                                    # over/under price: hockey and baseball totals move by price
        d = (odds.get("total") or {}).get(side) or {}
        for stage in ("close", "current", "open"):
            v = (d.get(stage) or {}).get("odds")
            if v not in (None, ""):
                v = str(v).strip().upper()
                if v in ("EVEN", "EV"): return 100
                try: return int(float(v.replace("+", "")))
                except Exception: pass
        return None
    out["oo"], out["uo"] = tprice("over"), tprice("under")
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
    # one request per batch of stadiums for the whole week (NFL first), instead of one request per game
    items = sorted(((k, v) for k, v in spots.items() if TODAY_ET <= dt.date.fromisoformat(v[2]) <= lim), key=lambda kv: (not kv[0].startswith("nfl"), kv[1][2]))
    for i0 in range(0, len(items), 40):
        chunk = items[i0:i0 + 40]
        r = get("https://api.open-meteo.com/v1/forecast", {"latitude": ",".join(str(v[0]) for _, v in chunk), "longitude": ",".join(str(v[1]) for _, v in chunk),
              "hourly": "temperature_2m,wind_speed_10m,precipitation_probability", "temperature_unit": "fahrenheit",
              "wind_speed_unit": "mph", "timezone": "America/New_York", "start_date": TODAY_ET.isoformat(), "end_date": lim.isoformat()}, tries=2, timeout=20)
        try: js = r.json() if r is not None else None
        except Exception: js = None
        if js is None:
            log(f"Weather: batch {i0 // 40 + 1} failed"); continue
        if isinstance(js, dict): js = [js]
        for (key, (lat, lon, date, time_)), res in zip(chunk, js):
            H = (res or {}).get("hourly") or {}
            hrs = H.get("time", []); want = f"{date}T{time_[:2]}:00"
            if want not in hrs: continue
            i = hrs.index(want)
            out[key] = {"temp": H["temperature_2m"][i], "wind": H["wind_speed_10m"][i],
                        "rain": (H.get("precipitation_probability") or [None] * (i + 1))[i]}
        time.sleep(1)
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
        row = [t, s.get("sl"), s.get("tot"), s.get("hml"), s.get("k"), s.get("p"), s.get("po")]
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
STRAT_NAMES = {"gap": "Market gap", "steam": "Late line move", "angle": "Situational angles", "dog": "Underdog of the day"}
SPREAD_TH = {"nfl": 1.5, "cfb": 2.0, "nba": 2.0, "ncaab": 2.5}
TOTAL_TH = {"nfl": 2.0, "cfb": 3.0, "nba": 3.0, "ncaab": 3.0, "nhl": 0.5, "mlb": 0.5}
PRICE_TOTALS = {"nhl": 6.5, "mlb": 10.0}

TEST_END = "2026-12-31T23:59+00:00"   # keep tallying picks through year end; move this date to extend
STEAM_WINDOW_MIN = 120      # late line move: only log picks in the last 2 hours before start
STEAM_LOOKBACK_MIN = 180    # ...and only count the move made over the last 3 hours (not since the line opened)

def _dec(a): return 1 + a / 100 if a > 0 else 1 + 100 / -a

def _conflicts(p, pool):
    """True if p bets against a pick already on the same game: the other team (spread/ML) or the other side of the total."""
    for q in pool:
        if q["key"] != p["key"]: continue
        if p["mkt"] == "total" and q["mkt"] == "total" and q["side"] != p["side"]: return True
        if p["mkt"] != "total" and q["mkt"] != "total" and q["side"] != p["side"]: return True
    return False
def _imp(a): return 1 / _dec(a)

def make_and_grade_picks(all_lines, MK, hist, B=None):
    P = load("picks.json", None)
    if not P:
        P = {"start": NOW.isoformat(timespec="minutes"), "end": (NOW + dt.timedelta(days=7)).isoformat(timespec="minutes"), "picks": []}
    if P.get("end", "") < TEST_END: P["end"] = TEST_END        # the test keeps running with the updated rules (first window ended Oct 28)
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
            # Strategy 2, late line move: a big move over the last 3 hours, logged in the last 2 hours before start,
            # one pick per game (spread or total, the bigger move). Moves made earlier in the day are old news by then.
            H = hist.get(key) or []
            if mins <= STEAM_WINDOW_MIN and len(H) >= 2 and not any(p["strat"] == "steam" and p["key"] == key for p in P["picks"] + new):
                ref_t = NOW.timestamp() / 60 - STEAM_LOOKBACK_MIN
                base = lambda ok: next((h for h in reversed(H) if ok(h) and h[0] <= ref_t), None) or next((h for h in H if ok(h)), None)
                cands = []
                fs = base(lambda h: h[1] is not None); ls = next((h for h in reversed(H) if h[1] is not None), None)
                th = SPREAD_TH.get(sp)
                if th and fs and ls and abs(ls[1] - fs[1]) >= th and ("steam", key, "spread") not in have:
                    side = "home" if ls[1] > fs[1] else "away"            # sl rising = money on home
                    line = -ls[1] if side == "home" else ls[1]
                    cands.append((abs(ls[1] - fs[1]) / th, {"strat": "steam", "key": key, "sport": sp, "mkt": "spread", "side": side, "odds": -110, "line": line,
                                "move": round(ls[1] - fs[1], 1), "start": o["start"], "at": NOW.isoformat(timespec="minutes")}))
                K = PRICE_TOTALS.get(sp)          # goals/runs per unit of Over probability: 5.5 at -140 is close to 6.5 at +120
                has = (lambda h: h[2] is not None and len(h) > 6 and h[6] is not None) if K else (lambda h: h[2] is not None)
                eff = (lambda h: h[2] + (h[6] - 0.5) * K) if K else (lambda h: h[2])
                ft = base(has); lt = next((h for h in reversed(H) if has(h)), None)
                tt = 0.4 if K else TOTAL_TH.get(sp)                  # hockey/baseball: about a 6-point shift in the Over's chance
                mv = eff(lt) - eff(ft) if ft and lt else 0
                if tt and ft and lt and abs(mv) >= tt and ("steam", key, "total") not in have:
                    side = "over" if mv > 0 else "under"
                    price = o.get("oo") if side == "over" else o.get("uo")
                    cands.append((abs(mv) / tt, {"strat": "steam", "key": key, "sport": sp, "mkt": "total", "side": side, "odds": price if price is not None else -110, "line": lt[2],
                                "move": round(mv, 1), "start": o["start"], "at": NOW.isoformat(timespec="minutes")}))
                for _, c in sorted(cands, key=lambda t: -t[0]):
                    if not _conflicts(c, P["picks"] + new):
                        new.append(c); break
    # cap: best 8 new market-gap picks per run so one noisy run can't flood the test
    gaps = sorted([p for p in new if p["strat"] == "gap"], key=lambda p: -p["edge"])[:8]
    new = gaps + [p for p in new if p["strat"] != "gap"]
    if B is not None:
        try: new += angle_picks(P, B, have, P["picks"] + new)
        except Exception: log(f"angle picks failed:\n{traceback.format_exc()}")
        try: new += dog_pick(P, B, MK, P["picks"] + new)
        except Exception: log(f"dog pick failed:\n{traceback.format_exc()}")
    # never bet both sides of a game: earlier picks stand, a later pick on the other side is dropped
    kept, blocked = [], 0
    for p in new:
        if _conflicts(p, P["picks"] + kept): blocked += 1; continue
        kept.append(p)
    new = kept
    if blocked: log(f"Test picks: {blocked} dropped for betting against an existing pick on the same game")
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
        if not f: continue
        if p.get("result") is not None:                                # already graded: re-grade only if the final score was corrected
            if p.get("score") == f"{f[0]}-{f[1]}": continue
            log(f"  regrade {p['key']} {p['mkt']}: score {p.get('score')} -> {f[0]}-{f[1]}")
        a, h = f
        if p["mkt"] == "ml":
            m = h - a if p["side"] == "home" else a - h
            res = ("loss" if p["sport"] == "soccer" else "push") if m == 0 else "win" if m > 0 else "loss"   # soccer: a draw loses a win bet
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

ANGLE_SPORTS = {"nfl": "NFLV_ROWS", "cfb": "CFB_ROWS", "nba": "NBA_ROWS", "nhl": "NHL_ROWS", "mlb": "MLB_ROWS", "ncaab": "NCAAB_ROWS", "soccer": "SOCCER_ROWS"}
REAL_LINES = {"nfl", "cfb", "soccer"}

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
                    if sp == "nhl":                                # hockey: records are already in the price, so demand value
                        ph, pa = _imp(g["hml"]), _imp(g["aml"])
                        book = (ph if side == "home" else pa) / (ph + pa)
                        rec = 0.5 + ((p if p >= .5 else 1 - p) - 0.5) * len(su) / (len(su) + 20)   # shrink small samples toward 50%
                        if not (-150 <= odds <= 150) or rec - book < 0.03: continue
                    if -200 <= odds <= 200:                       # skip heavy chalk, where a great record is already priced in
                        out.append(dict(score=abs(p - .5) * len(su) ** .5 * 0.7, mkt="ml", side=side, line=None, odds=odds,
                                        why=f"{ab} {w}–{len(su)-w} straight up, {label}" + ("" if p >= .5 else " (fading them)")))
    return out

def angle_picks(P, B, have, pool=()):
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
        if _conflicts({"key": key, "mkt": c["mkt"], "side": c["side"]}, list(pool) + new): continue
        used.add(key)
        new.append({"strat": "angle", "key": key, "sport": sp, "mkt": c["mkt"], "side": c["side"], "odds": c["odds"], "line": c["line"],
                    "why": c["why"], "rank": round(score, 2), "start": start.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
                    "at": NOW.isoformat(timespec="minutes")})
        if len(new) >= need: break
    return new

# ---------------------------------------------------------------- strategy 4: one underdog of the day
def dog_pick(P, B, MK, pool=()):
    today = TODAY_ET.isoformat()
    if any(p["strat"] == "dog" and p["key"].split("|")[1] == today for p in P["picks"]): return []
    if NOW.astimezone(ET).hour < 10: return []          # wait for the day's lines to settle
    mk = {f"{sp}|{r['date']}|{r['a']}|{r['h']}": r for sp, recs in MK.items() for r in recs}
    best = None
    for sp, rk in ANGLE_SPORTS.items():
        games = _rows_as_dicts(sp, B.get(rk))
        if not games: continue
        dml_idx = {}
        if sp == "soccer":
            for r in B.get(rk) or []:
                if len(r) > 18: dml_idx[(r[1], r[3], r[4])] = r[18]
        for g in games:
            if g["played"] or g["date"] != today or g["hml"] is None or g["aml"] is None: continue
            start = dt.datetime.fromisoformat(f"{g['date']}T{g['time']}:00").replace(tzinfo=ET)
            if (start - NOW).total_seconds() < 1200: continue
            key = f"{sp}|{g['date']}|{g['a']}|{g['h']}"
            imps = [_imp(g["hml"]), _imp(g["aml"])]
            d = dml_idx.get((g["date"], g["a"], g["h"]))
            if d is not None: imps.append(_imp(d))
            tot = sum(imps)
            r = mk.get(key) or {}
            cands = angle_candidates(sp, games, g)
            for side, odds in (("home", g["hml"]), ("away", g["aml"])):
                if odds is None or not (100 <= odds <= 250): continue
                if _conflicts({"key": key, "mkt": "ml", "side": side}, pool): continue
                book_fair = _imp(odds) / tot
                k = (r.get("k") or {}).get("h" if side == "home" else "a") or {}
                kmid = (k["bid"] + k["ask"]) / 2 if k.get("bid") and k.get("ask") else None
                pp = (r.get("p") or {}).get("h" if side == "home" else "a") if (r.get("p") or {}).get("vol", 0) > 0 else None
                market = kmid if kmid is not None else pp
                ev = market * _dec(odds) - 1 if market is not None else None
                support = [c for c in cands if c["side"] == side and c["mkt"] in ("ml", "spread")]
                ang = max((c["score"] for c in support), default=0)
                ev_book = book_fair * _dec(odds) - 1             # value against the no-vig sportsbook price (about zero, minus the vig)
                if not ((ev is not None and ev > 0) or support): continue   # no market edge and no trend: not a pick
                score = (ev if ev is not None else ev_book) * 100 + ang * 3
                why = []
                if ev is not None and ev > 0: why.append(f"{'Kalshi' if kmid is not None else 'Polymarket'} prices them at {market*100:.0f}%, the book’s odds imply {_imp(odds)*100:.0f}% ({ev*100:+.1f}% value)")
                if support: why.append(max(support, key=lambda c: c["score"])["why"])
                if not why: why.append(f"Best price among today’s underdogs; no strong market or trend signal behind it")
                if best is None or score > best[0]:
                    best = (score, {"strat": "dog", "key": key, "sport": sp, "mkt": "ml", "side": side, "odds": odds, "line": None,
                                    "why": "; ".join(why), "rank": round(score, 2),
                                    "start": start.astimezone(dt.timezone.utc).isoformat(timespec="minutes"), "at": NOW.isoformat(timespec="minutes")})
    return [best[1]] if best else []

def rows_final(B, key):
    sp, date, a, h = key.split("|")
    for g in _rows_as_dicts(sp, B.get(ANGLE_SPORTS.get(sp, ""), [])):
        if g["played"] and g["a"] == a and g["h"] == h and g["date"] == date: return [g["as_"], g["hs"]]
    return None


# ---------------------------------------------------------------- college basketball (sportsdataverse ESPN archive)
def build_ncaab():
    cur = season_year(8) + 1                      # file year = season end year
    games, names, conf, confname = [], {}, {}, {}
    for y in (cur - 2, cur - 1, cur):
        d = parquet(f"{SDV}/espn_mens_college_basketball_schedules/mbb_schedule_{y}.parquet")
        if d is None: continue
        d = d[d.season_type.isin([2, 3])]
        for _, r in d.iterrows():
            a, h = r.away_abbreviation, r.home_abbreviation
            if not a or not h: continue
            names[a] = r.away_short_display_name or r.away_location or a
            names[h] = r.home_short_display_name or r.home_location or h
            for ab, cid in ((a, r.away_conference_id), (h, r.home_conference_id)):
                if nz(cid) is not None: conf[ab] = str(int(cid))
            if bool(r.conference_competition) and nz(r.groups_short_name) and nz(r.home_conference_id) is not None:
                confname[str(int(r.home_conference_id))] = r.groups_short_name
            date, time_ = et(r.start_date)
            done = bool(r.status_type_completed)
            rk = lambda v: int(v) if nz(v) is not None and 0 < int(v) <= 25 else None
            games.append(dict(season=int(y), date=date, time=time_, a=a, h=h, **{"as": int(r.away_score) if done else None},
                              hs=int(r.home_score) if done else None, div=1 if r.conference_competition else 0,
                              neutral=1 if r.neutral_site else 0, ark=rk(r.away_current_rank), hrk=rk(r.home_current_rank)))
    conf_named = {ab: confname.get(c, "Conference") for ab, c in conf.items()}
    log(f"NCAAB: {len(games)} games")
    return add_rest(games), names, conf_named

# ---------------------------------------------------------------- soccer (football-data.co.uk: results + Pinnacle closing odds)
SOCCER_LEAGUES = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1"}
LONDON = ZoneInfo("Europe/London")

def _am(decv):
    try:
        d = float(decv)
        if not d or d <= 1.0 or math.isnan(d): return None
        return int(round((d - 1) * 100)) if d >= 2 else -int(round(100 / (d - 1)))
    except Exception: return None

def _first(row, *cols):
    for c in cols:
        if c in row and nz(row[c]) is not None and str(row[c]).strip() != "": return row[c]
    return None

def _soccer_dt(date_s, time_s):
    date_s = str(date_s).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = dt.datetime.strptime(date_s, fmt); break
        except Exception: d = None
    if d is None: return None, None
    hh, mm = (str(time_s).strip() or "15:00").split(":")[:2] if time_s and ":" in str(time_s) else ("15", "00")
    local = d.replace(hour=int(hh), minute=int(mm), tzinfo=LONDON).astimezone(ET)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")

def _soccer_row(r, season, upcoming):
    date, time_ = _soccer_dt(r.get("Date"), r.get("Time"))
    if not date or not r.get("HomeTeam") or not r.get("AwayTeam"): return None
    if upcoming:
        h, d_, a = _first(r, "PSH", "AvgH", "B365H"), _first(r, "PSD", "AvgD", "B365D"), _first(r, "PSA", "AvgA", "B365A")
        ou = _first(r, "P>2.5", "Avg>2.5", "B365>2.5"); ah = _first(r, "AHh")
    else:
        h, d_, a = _first(r, "PSCH", "AvgCH", "B365CH", "PSH"), _first(r, "PSCD", "AvgCD", "B365CD", "PSD"), _first(r, "PSCA", "AvgCA", "B365CA", "PSA")
        ou = _first(r, "PC>2.5", "AvgC>2.5", "B365C>2.5", "P>2.5"); ah = _first(r, "AHCh", "AHh")
    done = not upcoming and nz(r.get("FTHG")) is not None and nz(r.get("FTAG")) is not None
    try: ahv = float(ah) if ah is not None else None
    except Exception: ahv = None
    return dict(season=season, date=date, time=time_, a=str(r["AwayTeam"]).strip(), h=str(r["HomeTeam"]).strip(),
                **{"as": int(float(r["FTAG"])) if done else None}, hs=int(float(r["FTHG"])) if done else None,
                sl=(-ahv if ahv is not None else None), tot=2.5 if ou is not None else None,
                aml=_am(a), hml=_am(h), dml=_am(d_), div=0, lg=r.get("Div"))

def build_soccer():
    y = season_year(7)
    games = []
    for yy in (y - 2, y - 1, y):
        code = f"{yy % 100:02d}{(yy + 1) % 100:02d}"
        for div in SOCCER_LEAGUES:
            rr = get(f"https://www.football-data.co.uk/mmz4281/{code}/{div}.csv", timeout=40)
            if rr is None: continue
            try: df = pd.read_csv(io.StringIO(rr.content.decode("latin-1")))
            except Exception as e: log(f"  soccer {div} {code}: {e}"); continue
            for rec in df.to_dict("records"):
                rec["Div"] = rec.get("Div") or div
                g = _soccer_row(rec, yy, False)
                if g and g["hs"] is not None: games.append(g)
    fx = get("https://www.football-data.co.uk/fixtures.csv", timeout=40)
    n_up = 0
    if fx is not None:
        try:
            df = pd.read_csv(io.StringIO(fx.content.decode("utf-8-sig", errors="replace")))
            for rec in df.to_dict("records"):
                if rec.get("Div") not in SOCCER_LEAGUES: continue
                g = _soccer_row(rec, y, True)
                if g: games.append(g); n_up += 1
        except Exception as e: log(f"  soccer fixtures: {e}")
    try: n_up += soccer_upcoming_espn(games)
    except Exception as e: log(f"  soccer ESPN fixtures: {e}")
    seen, out = set(), []
    for g in games:
        k = (g["date"], g["a"], g["h"])
        if k in seen: continue
        seen.add(k); out.append(g)
    log(f"Soccer: {len(out)} matches ({n_up} upcoming)")
    return add_rest(out)


ESPN_SOCCER = {"E0": "eng.1", "SP1": "esp.1", "I1": "ita.1", "D1": "ger.1", "F1": "fra.1"}
SOCCER_ALIAS = {"manchester united": "Man United", "manchester city": "Man City", "nottingham forest": "Nott'm Forest",
    "wolverhampton wanderers": "Wolves", "tottenham hotspur": "Tottenham", "brighton and hove albion": "Brighton", "brighton hove albion": "Brighton",
    "west ham united": "West Ham", "newcastle united": "Newcastle", "leeds united": "Leeds", "afc bournemouth": "Bournemouth",
    "atletico madrid": "Ath Madrid", "athletic club": "Ath Bilbao", "real sociedad": "Sociedad", "celta vigo": "Celta",
    "rayo vallecano": "Vallecano", "real betis": "Betis", "deportivo alaves": "Alaves", "espanyol": "Espanol",
    "borussia dortmund": "Dortmund", "bayer leverkusen": "Leverkusen", "borussia monchengladbach": "M'gladbach",
    "eintracht frankfurt": "Ein Frankfurt", "fc cologne": "FC Koln", "1 fc koln": "FC Koln", "vfb stuttgart": "Stuttgart",
    "tsg hoffenheim": "Hoffenheim", "sc freiburg": "Freiburg", "fsv mainz 05": "Mainz", "mainz": "Mainz", "werder bremen": "Werder Bremen",
    "paris saintgermain": "Paris SG", "paris saint germain": "Paris SG", "olympique marseille": "Marseille", "olympique lyonnais": "Lyon",
    "as monaco": "Monaco", "stade rennais": "Rennes", "internazionale": "Inter", "inter milan": "Inter", "ac milan": "Milan",
    "as roma": "Roma", "hellas verona": "Verona"}

def _team_match(name, known):
    import difflib
    n = norm(name)
    if n in SOCCER_ALIAS and SOCCER_ALIAS[n] in known: return SOCCER_ALIAS[n]
    by_norm = {norm(k): k for k in known}
    if n in by_norm: return by_norm[n]
    strip = lambda x: " ".join(w for w in x.split() if w not in ("fc", "cf", "afc", "ac", "as", "sc", "club", "de", "the", "calcio"))
    cand = {strip(k): v for k, v in by_norm.items()}
    m = difflib.get_close_matches(strip(n), list(cand), n=1, cutoff=0.72)
    return cand[m[0]] if m else None

def soccer_upcoming_espn(games):
    added = 0
    rng = f"{TODAY_ET.strftime('%Y%m%d')}-{(TODAY_ET + dt.timedelta(days=8)).strftime('%Y%m%d')}"
    have = {(g["date"], g["a"], g["h"]) for g in games}
    for div, code in ESPN_SOCCER.items():
        known = {g["h"] for g in games if g.get("lg") == div} | {g["a"] for g in games if g.get("lg") == div}
        evs = []
        for k in range(-3, 8):                           # one request per day (ESPN rejects ranges); past 3 days give final scores
            js = get_json(f"{ESPN}/soccer/{code}/scoreboard", {"dates": (TODAY_ET + dt.timedelta(days=k)).strftime("%Y%m%d")})
            evs += (js or {}).get("events", []) or []
        for ev in evs:
            try:
                comp = ev["competitions"][0]
                state = ((comp.get("status") or {}).get("type") or {}).get("state")
                if state not in ("pre", "post"): continue
                home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
                away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
                h = _team_match(home["team"].get("displayName") or home["team"].get("name"), known)
                a = _team_match(away["team"].get("displayName") or away["team"].get("name"), known)
                if not h or not a: continue
                date, time_ = et(ev["date"])
                if state == "post":                          # final score, so soccer picks grade the same night
                    try: FINALS[f"soccer|{date}|{a}|{h}"] = [int(away.get("score") or 0), int(home.get("score") or 0)]
                    except Exception: pass
                    continue
                if (date, a, h) in have: continue
                o = (comp.get("odds") or [{}])[0] or {}
                ml = o.get("moneyline") or {}
                pick = lambda side: next((v for v in [((ml.get(side) or {}).get(st) or {}).get("odds") for st in ("close", "current", "open")] if v not in (None, "")), None)
                toint = lambda v: (100 if str(v).upper() in ("EVEN", "EV") else int(float(str(v).replace("+", "")))) if v not in (None, "") else None
                hml = (o.get("homeTeamOdds") or {}).get("moneyLine") or toint(pick("home"))
                aml = (o.get("awayTeamOdds") or {}).get("moneyLine") or toint(pick("away"))
                dml = (o.get("drawOdds") or {}).get("moneyLine") or toint(pick("draw"))
                games.append(dict(season=season_year(7), date=date, time=time_, a=a, h=h, **{"as": None}, hs=None, sl=None,
                                  tot=float(o["overUnder"]) if o.get("overUnder") is not None else None, aml=aml, hml=hml, dml=dml, div=0, lg=div))
                have.add((date, a, h)); added += 1
            except Exception as e:
                log(f"  soccer espn skip: {e}")
    log(f"Soccer ESPN fixtures: {added} added")
    return added

# ---------------------------------------------------------------- golf (ESPN PGA Tour + Kalshi/Polymarket winner prices)
def norm(name):
    import unicodedata
    n = unicodedata.normalize("NFD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", n)).strip()

def _golf_events(js):
    return (js or {}).get("events") or []

def _golf_field(ev):
    comp = (ev.get("competitions") or [{}])[0]
    out = []
    for i, c in enumerate(comp.get("competitors") or []):
        ath = c.get("athlete") or {}
        name = ath.get("displayName") or ath.get("fullName")
        if not name: continue
        st = c.get("status") or {}
        pos = ((st.get("position") or {}).get("displayName")) or None
        score = c.get("score")
        if isinstance(score, dict): score = score.get("displayValue")
        thru = st.get("thru") if st.get("thru") not in (None, 0) else ((st.get("type") or {}).get("shortDetail") or None)
        out.append({"n": name, "pos": pos, "score": score, "thru": thru, "order": c.get("sortOrder") or i + 1,
                    "rounds": len([l for l in (c.get("linescores") or []) if l.get("value") is not None or l.get("displayValue")])})
    out.sort(key=lambda p: p["order"])
    most = max((p["rounds"] for p in out), default=0)
    rank = 0
    for i, p in enumerate(out):
        if i == 0 or p["score"] != out[i - 1]["score"]: rank = i + 1
        if not p["pos"]:
            if most >= 4 and 0 < p["rounds"] <= 2: p["pos"] = "MC"
            else:
                tie = (i > 0 and out[i - 1]["score"] == p["score"]) or (i + 1 < len(out) and out[i + 1]["score"] == p["score"])
                p["pos"] = ("T" if tie else "") + str(rank)
    return out

def _finish(pos):
    if not pos: return None
    p = str(pos).upper().replace("T", "")
    return int(p) if p.isdigit() else ("MC" if "CUT" in p or p == "MC" else p[:3])

def build_golf():
    base = f"{ESPN}/golf/pga/scoreboard"
    js = get_json(base)
    evs = _golf_events(js)
    if not evs: log("Golf: no event"); return None
    ev = evs[0]
    comp = (ev.get("competitions") or [{}])[0]
    start = (ev.get("date") or "")[:10]
    G = {"event": {"name": ev.get("name") or ev.get("shortName"), "start": ev.get("date"), "end": ev.get("endDate"),
                   "state": ((ev.get("status") or {}).get("type") or {}).get("state") or ((comp.get("status") or {}).get("type") or {}).get("state"),
                   "course": ((comp.get("venue") or {}).get("fullName")) or ((ev.get("courses") or [{}])[0].get("name"))},
         "players": _golf_field(ev)}
    names = {norm(p["n"]): p for p in G["players"]}
    try: d0 = dt.date.fromisoformat(start)
    except Exception: d0 = TODAY_ET
    # recent form: the previous 8 weekly events
    form = {}
    for k in range(1, 9):
        day = d0 - dt.timedelta(days=7 * k)
        for pe in _golf_events(get_json(base, {"dates": day.strftime("%Y%m%d")})):
            if pe.get("id") == ev.get("id"): continue
            for p in _golf_field(pe):
                f = _finish(p["pos"])
                if f is not None and norm(p["n"]) in names: form.setdefault(norm(p["n"]), []).append(f)
            break
    # last year at this event (same week a year earlier)
    last = {}
    key_words = {w for w in norm(G["event"]["name"] or "").split() if len(w) > 3 and w not in ("championship", "open", "classic", "invitational", "tournament")}
    for pe in _golf_events(get_json(base, {"dates": (d0 - dt.timedelta(days=364)).strftime("%Y%m%d")})):
        if not key_words & set(norm(pe.get("name") or "").split()): break
        for p in _golf_field(pe):
            if norm(p["n"]) in names: last[norm(p["n"])] = _finish(p["pos"])
        G["event"]["lastYearName"] = pe.get("name"); break
    for p in G["players"]:
        p["form"] = form.get(norm(p["n"]), [])[:6]; p["last"] = last.get(norm(p["n"]))
    # winner markets
    hits = 0
    for tag in ("golf", "pga-tour", "pga"):
        pj = get_json(f"{POLY}/events", {"tag_slug": tag, "active": "true", "closed": "false", "limit": 100})
        if not isinstance(pj, list): continue
        for pev in pj:
            title = (pev.get("title") or "").lower()
            if not any(w in title for w in norm(G["event"]["name"] or "").split()[:2]) or "win" not in title: continue
            for m in pev.get("markets") or []:
                q = m.get("question") or m.get("groupItemTitle") or ""
                who = m.get("groupItemTitle") or re.sub(r"^Will (.*?) win.*$", r"\1", q)
                try: price = float((json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices"))[0])
                except Exception: continue
                p = names.get(norm(who))
                if p and 0 < price < 1: p["p"] = round(price, 4); hits += 1
            break
        if hits: break
    kh = 0
    for series in ("KXPGATOUR", "KXPGA", "KXGOLF"):
        kj = get_json(f"{KALSHI}/events", {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 50})
        for kev in (kj or {}).get("events", []) or []:
            for m in kev.get("markets") or []:
                p = names.get(norm(m.get("yes_sub_title") or ""))
                if not p: continue
                bid, ask = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
                if ask: p["k"] = {"bid": bid, "ask": ask}; kh += 1
        if kh: break
    log(f"Golf: {G['event']['name']} ({len(G['players'])} players, form for {len(form)}, Polymarket {hits}, Kalshi {kh})")
    return G

# ---------------------------------------------------------------- college football: this week's Top 25 from ESPN
def cfb_rankings():
    js = get_json(f"{ESPN}/football/college-football/rankings")
    polls = (js or {}).get("rankings") or []
    pick = next((p for p in polls if "playoff" in (p.get("name") or "").lower()), None) or \
           next((p for p in polls if (p.get("type") == "ap") or "ap" in (p.get("shortName") or "").lower().split()), None) or (polls[0] if polls else None)
    if not pick: log("CFB rankings: none"); return None
    ranks = {}
    for r in pick.get("ranks") or []:
        ab = ((r.get("team") or {}).get("abbreviation") or "").upper()
        if ab and r.get("current"): ranks[ab] = int(r["current"])
    log(f"CFB rankings: {pick.get('name')} ({len(ranks)} teams)")
    return {"poll": pick.get("name"), "headline": pick.get("headline") or pick.get("shortHeadline"), "ranks": ranks}

# ---------------------------------------------------------------- money vs bets (DraftKings + Circa splits via SharpAPI, optional)
SPLIT_LEAGUES = {"nfl": "nfl", "nba": "nba", "nhl": "nhl", "mlb": "mlb", "ncaab": "ncaab", "cfb": "ncaaf"}
def betting_splits():
    key = os.environ.get("SHARPAPI_KEY", "").strip()
    if not key: return None
    rows = []
    for sp, lg in SPLIT_LEAGUES.items():
        off = 0
        while True:
            try:
                r = requests.get("https://api.sharpapi.io/api/v1/splits", params={"league": lg, "sportsbook": "draftkings,circa", "limit": 200, "offset": off},
                                 headers={**UA, "X-API-Key": key}, timeout=30)
            except Exception as e:
                log(f"  splits {sp}: {e}"); break
            if r.status_code != 200: log(f"  splits {sp}: HTTP {r.status_code}"); break
            js = r.json()
            for d in js.get("data") or []:
                keep = lambda m: {k: (d.get(m) or {}).get(k) for k in ("handle_pct", "bets_pct", "line", "away_odds", "home_odds")}
                rows.append({"sp": sp, "away": d.get("away_team"), "home": d.get("home_team"), "book": d.get("sportsbook"),
                             "spread": keep("spread"), "total": keep("total"), "ml": keep("moneyline"), "at": d.get("fetched_at")})
            pg = js.get("pagination") or {}
            if not pg.get("has_more"): break
            off = pg.get("next_offset") or off + 200
    log(f"Splits: {len(rows)} rows")
    return rows

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
    ncb = step("ncaab", build_ncaab)
    nc = ncb[0] if ncb else []
    if nc:
        archive["ncaab"] = merge_lines(nc, nc_lines)
        B["NCAAB_ROWS"], B["NCAAB_NAMES"], B["NCAAB_CONF"] = to_rows(nc), ncb[1], ncb[2]
    else:
        for k in ("NCAAB_ROWS", "NCAAB_NAMES", "NCAAB_CONF"): B[k] = old.get(k)
    soc = step("soccer", build_soccer)
    B["SOCCER_ROWS"] = to_rows(soc, extra=True) if soc else old.get("SOCCER_ROWS")
    B["GOLF"] = step("golf", build_golf) or old.get("GOLF")
    B["SPLITS"] = step("splits", betting_splits) or (old.get("SPLITS") if os.environ.get("SHARPAPI_KEY") else None)

    if nba: B["NBA_ROWS"], B["NBA_NAMES"], B["NBA_DIVS"] = to_rows(nba[0]), nba[1], NBA_DIV
    else:
        for k in ("NBA_ROWS", "NBA_NAMES", "NBA_DIVS"): B[k] = old.get(k)
    B["NHL_ROWS"] = to_rows(nhl) if nhl else old.get("NHL_ROWS")
    if mlb: B["MLB_ROWS"], B["MLB_NAMES"], B["MLB_DIV"] = to_rows(mlb[0]), mlb[1], MLB_DIV
    else:
        for k in ("MLB_ROWS", "MLB_NAMES", "MLB_DIV"): B[k] = old.get(k)
    if cfb: B["CFB_ROWS"], B["CFB_NAMES"], B["CFB_CONF"] = to_rows(cfb[0]), cfb[1], cfb[2]
    rk = step("cfb rankings", cfb_rankings)
    if rk and B.get("CFB_ROWS"):
        B["CFB_RANK"] = rk
        for r in B["CFB_ROWS"]:                           # upcoming games show this week's ranking, not the schedule file's
            if r[5] is None and r[1] >= TODAY_ET.isoformat():
                r[16], r[17] = rk["ranks"].get(r[3]), rk["ranks"].get(r[4])
    else:
        for k in ("CFB_ROWS", "CFB_NAMES", "CFB_CONF"): B[k] = old.get(k)
    B["ARCHIVE"] = archive

    # prediction markets, matched to our schedule
    nfl_games = [dict(date=r[2], time=r[3], a=r[4], h=r[5], **{"as": r[6]}) for r in (B.get("NFLV_ROWS") or [])]
    sched = {"nfl": (nfl_games, NFL_FULL), "nhl": (nhl or [], NHL_FULL),
             "nba": (nba[0] if nba else [], nba[1] if nba else {}), "mlb": (mlb[0] if mlb else [], mlb[1] if mlb else {}),
             "cfb": (cfb[0] if cfb else [], cfb[1] if cfb else {}), "ncaab": (nc, ncb[1] if ncb else {})}
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
                po = None
                if o.get("oo") is not None and o.get("uo") is not None:
                    a_, b_ = _imp(o["oo"]), _imp(o["uo"]); po = round(a_ / (a_ + b_), 3)
                snaps[f"{sp}|{key}"] = {"sl": o.get("sl"), "tot": o.get("tot"), "hml": o.get("hml"), "po": po}
    for sp, recs in MK.items():
        for r in recs:
            st = ((all_lines.get(sp) or {}).get(f"{r['date']}|{r['a']}|{r['h']}") or {}).get("start")
            if r["date"] < TODAY_ET.isoformat(): continue
            if st and dt.datetime.fromisoformat(st.replace("Z", "+00:00")) <= NOW: continue      # game already started: in-play price
            if not st and r["date"] == TODAY_ET.isoformat() and NOW.astimezone(ET).hour >= 19: continue
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
    wx_new = step("weather", lambda: weather(B["NFLV_ROWS"])) or {}
    wx_old = {k: v for k, v in (old.get("WEATHER") or {}).items() if k.split("|")[1] >= TODAY_ET.isoformat()}
    B["WEATHER"] = {**wx_old, **wx_new}                      # keep earlier forecasts when a run's lookups fail
    B["NFL_WX"] = NFL_WX or old.get("NFL_WX") or {}
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
