# S&P 500 Earnings Season Tracker

A self-updating dashboard built from FactSet's weekly **Earnings Insight** report:

- companies reported, % beating EPS estimates, positive next-quarter guidance (current season)
- % beating EPS by quarter since Q2'21 against FactSet's 5-year average
- y/y EPS growth by quarter and on a trailing-twelve-month basis
- % of companies issuing positive quarterly EPS guidance since Q2'21
- the season's week-by-week path and a full quarterly ledger with a link to every source PDF

A GitHub Action fetches each new edition, parses it, updates `data/tracker_data.json`, rebuilds `docs/index.html` and commits. GitHub Pages serves the page.

## Repository layout

| Path | What it does |
|---|---|
| `.github/workflows/update-tracker.yml` | Weekly schedule (Sat morning Melbourne) plus a manual "Run workflow" button |
| `scripts/update_from_factset.py` | Finds the newest PDF, extracts the figures, merges them into the data file |
| `rebuild.py` | Builds the page from the data file (standard library only) |
| `data/tracker_data.json` | All history and the current snapshot; the only file the updater edits |
| `docs/index.html` | The published page |

## One-time setup (about 5 minutes)

1. **Create a repository** on GitHub, e.g. `earnings-tracker`. Make it **public** if you want free GitHub Pages hosting (private repos need a paid plan for Pages).

2. **Add the files.** Either:
   - **Git (recommended):**
     ```bash
     unzip earnings-tracker-repo.zip && cd earnings-tracker-repo
     git init -b main && git add . && git commit -m "Initial tracker"
     git remote add origin https://github.com/<you>/earnings-tracker.git
     git push -u origin main
     ```
   - **Browser upload:** on the repo page, *Add file → Upload files* and drag in the folder contents. The `.github` folder is hidden in Finder/Explorer, so then use *Add file → Create new file*, name it `.github/workflows/update-tracker.yml`, and paste in the workflow file's contents.

3. **Let the Action push commits:** *Settings → Actions → General → Workflow permissions →* **Read and write permissions** → Save.

4. **Turn on Pages:** *Settings → Pages → Build and deployment → Source:* **Deploy from a branch**, *Branch:* `main`, folder `/docs` → Save. After a minute the page is live at `https://<you>.github.io/earnings-tracker/`.

5. **Run it once by hand:** *Actions* tab → enable workflows if asked → **Update earnings season tracker** → **Run workflow**. A green tick with "No new edition" is the expected result until FactSet publishes the next report.

## What happens each week

- **Friday 22:23 UTC** (Saturday 09:23 in Melbourne during daylight saving) the Action runs; retries on Saturday and Monday catch late or holiday-shifted editions. If the edition is already in the data file it exits without changes.
- **During a season** the live quarter's beat rate, 5-yr average, blended growth and the context figures update, and a row is added to the week-by-week table.
- **When a season closes** the quarter keeps its last (late-season) reading in the history. The next quarter starts once at least 15 companies have reported, so the first row isn't based on two or three early reporters.
- **Guidance** is tracked for whichever quarter FactSet is counting. A quarter's pre-season count freezes when FactSet moves on to the next quarter.
- **If FactSet changes the report's wording** so a required figure can't be read, the run fails and GitHub emails you. The data file is left untouched.

### Manual runs

*Actions → Update earnings season tracker → Run workflow*, optionally with an edition code (`103026`, or `103026A` when FactSet issues a lettered re-post). Codes are the `MMDDYY` in the PDF file name.

### Running locally

```bash
sudo apt-get install poppler-utils        # macOS: brew install poppler
python scripts/update_from_factset.py --data data/tracker_data.json             # latest edition
python scripts/update_from_factset.py --data data/tracker_data.json --dry-run   # parse only
python rebuild.py docs                                                          # rebuild page
```

## Method notes

- Every completed quarter uses a late-season reading (94%+ of companies reported). FactSet's later citations can differ by a point or a few tenths.
- **TTM growth** weights FactSet's quarterly y/y rates for the last four quarters by the year-ago quarters' bottom-up EPS. Those levels come from FactSet's "Bottom-Up EPS Estimates" chart pages, which are images, so the updater can't read new ones. When a needed level isn't in `eps_levels`, it is imputed as the year-ago level × (1 + that quarter's growth), and the page lists the imputed quarters. To replace an imputed value with FactSet's printed one, add it to `eps_levels` in the data file.
- Guidance "positive/negative" is FactSet's classification against the consensus mean the day before the guidance. FactSet does not publish a "% raising guidance" series.
- Scheduled workflows pause after 60 days without repository activity. The weekly commits keep it active; if it ever pauses, re-enable it on the Actions tab.

Source: FactSet Research Systems, *Earnings Insight* (public weekly PDF). Not investment advice.
