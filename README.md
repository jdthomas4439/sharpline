# Sharpline

Your own sports betting research app, refreshed every hour for free by GitHub.

- **App:** `index.html`, served by GitHub Pages at `https://YOUR-NAME.github.io/sharpline/`
- **Data job:** `scripts/update.py`, run every hour by GitHub Actions (`.github/workflows/update.yml`)
- **Data file:** `data/bundle.js`, rewritten by the job, read by the app

## What the hourly job does

| Data | Source | Needs a key? |
|---|---|---|
| NFL results + real closing lines | nflverse | No |
| NBA, NHL, MLB results | sportsdataverse | No |
| College football results + real lines | CollegeFootballData via sportsdataverse | No |
| NBA, NHL, MLB, college basketball lines | ESPN, saved before every game (builds your own closing-line archive) | No |
| College basketball results | ESPN | No |
| Injuries (NFL, NBA, NHL, MLB) | ESPN | No |
| Weather for outdoor football | Open-Meteo | No |
| Kalshi + Polymarket game prices | Their public market-data APIs | No |
| Line history for move alerts (ESPN lines, Kalshi, Polymarket, every hour) | Built by the job itself | No |
| Sportsbook odds for NHL + NFL (optional) | The Odds API | Yes, add the `ODDS_API_KEY` secret |

## Setup (about 15 minutes, all in the browser)

1. **Create a free GitHub account** at github.com if you don't have one.
2. **Create a repository:** click **+** (top right) → **New repository**. Name it `sharpline`, set it to **Public** (free GitHub Pages requires a public repo), and click **Create repository**.
3. **Upload the files:** on the new repo page click **uploading an existing file**. Drag in `index.html`, `README.md`, `requirements.txt`, and the `scripts` and `data` folders. Click **Commit changes**.
4. **Add the workflow file** (the uploader skips hidden folders): click **Add file → Create new file**, type the name `.github/workflows/update.yml`, paste the contents of that file from this package, and click **Commit changes**.
5. **Let the job save data:** go to **Settings → Actions → General**, scroll to **Workflow permissions**, choose **Read and write permissions**, and click **Save**.
6. **Turn on the website:** go to **Settings → Pages**. Under **Build and deployment**, set Source to **Deploy from a branch**, Branch to **main** and **/ (root)**, then click **Save**. After a minute or two your app is live at `https://YOUR-NAME.github.io/sharpline/`.
7. **Run the job once by hand:** open the **Actions** tab, click **Update Sharpline data**, then **Run workflow**. It takes about two minutes, and after that it runs every hour on its own.
8. **Optional, live sportsbook odds:** go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ODDS_API_KEY` and paste your key from the-odds-api.com. By default odds refresh every 6 hours to stay inside the free plan (NHL + NFL is about 6 credits per pull, roughly 720 a month at that pace, so the free 500 runs out late in the month; change `ODDS_EVERY_HOURS` in the workflow to 8 or 12 to stay under it, or upgrade the plan).
9. **Add it to your phone:** open the link in Safari, tap **Share → Add to Home Screen**.

## Checking that it works

After each run, `data/last_run.txt` lists what was pulled and any source that failed. A failed source keeps its last good data, so the app never goes blank.

The NBA starts showing real against-the-spread records automatically once the line archive has 300 finished games (a few weeks into the season). College basketball appears as its own tab once the season starts and results come in.

## Markets tab and line-move alerts

The **Markets** tab lines up Kalshi and Polymarket prices next to the sportsbooks' fair price for each game and calls out gaps of 2 points or more (Kalshi's fee is included). The **Edges** tab lists line moves from the hourly snapshots: big spread, total and moneyline moves, key-number crosses (3 and 7 in football), prediction-market swings, and late steam. A number on the Edges tab shows how many moves are new since you last looked.

Alerts need a few hourly runs before they appear, because the job has to see a line twice to know it moved.

## Your bets

The **Bets** tab stores your bets on your phone (in the browser). Bets on games in the data settle on their own, and spread and total bets show closing line value: how your number compared with where the line closed.
