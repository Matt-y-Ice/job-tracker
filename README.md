# Job Tracker

Finds remote job listings, ranks them against your resume, and gives you an Excel sheet and a
simple dashboard to track your applications.

- **Private.** The ranking runs on your own computer with a free local AI model (Ollama).
  Your resume never leaves your computer. No accounts, no API keys, nothing to pay for.
- **Ranked for you.** Each job gets a 0–100 score with plain reasons: why it fits, which skills
  you're missing, and any red flags (travel, on-call, clearance, and so on).
- **Your tracking is safe.** Every run updates the workbook without erasing anything you typed:
  statuses, dates, and notes always survive.
- **Tailored applications.** One click writes a resume and cover letter for a specific job, using
  only facts from your resume, as Word files and PDFs.

---

## 1. What you need

| | |
|---|---|
| **Windows 10 or 11** | Double-click `setup.bat`. When installing Python, **tick "Add python.exe to PATH"**. |
| **Mac** | Double-click `Job Tracker.app` or `setup.command`. Python 3.10 or newer from [python.org/downloads](https://www.python.org/downloads/) or Homebrew (`brew install python@3.12`). The system Python on older macOS is too old. Ollama is the Mac app from [ollama.com/download](https://ollama.com/download). |
| **Python 3.10 or newer** | Free from [python.org/downloads](https://www.python.org/downloads/). When installing, **tick "Add python.exe to PATH"**. |
| **Ollama** | Free from [ollama.com/download](https://ollama.com/download). It runs the AI model. Install it and keep the defaults. |
| **About 12 GB of free disk space** | Mostly for the AI model. |
| **A graphics card (recommended)** | An NVIDIA card or a recent AMD Radeon (RX 6800 or newer) with 10 GB+ of memory makes ranking about 10× faster. It still works without one, just slowly. |

## 2. Install (one time)

1. Unzip the download anywhere, for example to your Desktop.
2. Install Python and Ollama (links above).
3. Open the folder and **double-click `setup.bat`** (Windows) or **`setup.command`** (Mac). You can also double-click `Job Tracker.app` and choose **Set up**. It will:
   - set up a private Python environment for this project,
   - tell you whether the AI will run on your **GPU** (fast) or **CPU** (slow),
   - download the AI models (about 10 GB, which can take a while).

If setup says Ollama is missing, install it and run `setup.bat` (Windows) or `setup.command` (Mac) again. Running it again is
always safe.

The first time you open `Job Tracker.app` or a `.command` file from a downloaded zip, macOS may
refuse it. Right-click the file and choose **Open**, then Open again. A git clone does not need
that. Do not turn off Gatekeeper.

## 3. Add your resume

Put **one** resume file in the `resume` folder. PDF, Word (.docx), and plain text (.txt) all work.

- Only one file at a time. If there are two, it will ask you to remove one.
- A scanned PDF (a picture of a page) won't work. Export your resume from Word or Google Docs
  instead.
- Old `.doc` files: open them in Word and save as `.docx`.

## 4. Run it

**Double-click `run.bat`.** On a Mac, double-click `run.command`, or open `Job Tracker.app` and choose **Find and rank jobs**.

The first time, it asks a few questions in the window. Press Enter to accept the suggestion in
[brackets]:

| Question | What it does |
|---|---|
| Minimum salary | Jobs that list a maximum below this get a score of 0 (they are kept, not deleted). |
| Work arrangement | Remote only, remote or hybrid, or anything. |
| Locations / time zones | For example "United States". |
| Job titles | What you're looking for. These are also added to the search. |
| Must-have skills | For example "Python, SQL". Jobs using them score higher. |
| Deal breakers | For example "AI/ML development, required travel". Jobs where this is the main work or a firm requirement get a score of 0. Just mentioning it isn't enough. |
| Seniority | Entry, mid, senior… Jobs above or below your levels lose some points. |
| Company size | No effect yet beyond guiding the AI's judgment. |
| Days back | How far back to search (25 days by default). |

Next time it shows your answers and asks **"Use these again? (Y/n)"**. Just press Enter.

You'll then see something like this:

```
[1/5] Reading resume... found resume.pdf (42 skills detected)
[2/5] Loading preferences... using saved answers
[3/5] Scraping jobs... RemoteOK 34, Remotive 21, WWR 18, ... (12 duplicates removed)
      331 remote US, 51 unsure (kept separately), 359 not remote US (kept with reasons)
      372 new jobs to review (about 29 min), 0 cached, ...
[4/5] Ranking... 372 new jobs, 0 cached | model qwen2.5:14b on GPU
[5/5] Building Excel... 77 top matches, 254 low matches -> output/Job_Rankings.xlsx
Done in 31 min 4 s.
```

**How long it takes.** The first run reviews every job: about 5 seconds per job with a good
graphics card (roughly 30 minutes), and much longer on a CPU. Later runs only review **new**
jobs, so they usually take a few minutes. You can stop at any time with **Ctrl+C**; everything
reviewed so far is saved, and the next run continues from there.

## 5. Your results: `output/Job_Rankings.xlsx`

| Tab | What's on it |
|---|---|
| **Top Matches** | Jobs scoring 50 or higher, best first. |
| **Low Matches** | Jobs below 50, including any that hit a deal breaker (the reason is in *Red Flags*). |
| **Unsure Location** | Jobs where it couldn't confirm "remote and open to the US". They are still scored, so check these by hand. *Location Check* says why. |
| **Summary** | Counts by status, applications sent, interviews, follow-ups due, and the top companies. It updates as you edit. |
| **Excluded** | Jobs that are onsite, hybrid, or limited to other countries, with the reason. |
| **Scoring Method** | How the score is made. |

**Tracking columns** you can fill in: Status, Date Applied, Applied Via, Contact / Recruiter,
Last Contact, Next Step, Follow-Up Date, Notes, Interview Scheduled, Rejected, Hired.
Everything you type there is kept on every run, even when a job moves between tabs. You can also
add your own columns or rows; they are kept too.

**Close the workbook in Excel before running.** Windows locks files that are open in Excel. If
it's open, the run tells you so and changes nothing. Close it, then run `run.bat --skip-scrape`
to finish quickly.

Every save first makes a backup in `output/backups` (the 10 most recent are kept).

## 6. The dashboard

**Double-click `dashboard.bat`.** On a Mac, double-click `dashboard.command`. It opens <http://localhost:5000> in your browser. Keep the
black window open while you use it. If macOS says port 5000 is in use, AirPlay Receiver is the usual cause; the Mac launcher picks another port (5050–5059) unless you passed `--port`. Windows stays on port 5000.

- Search, filter by status, score, or source, and sort by any column.
- Change **Status**, **Date Applied**, and **Notes** right in the table. Each change is saved
  to the Excel file straight away. Choosing "Applied" fills in today's date for you.
- Click **▶** on a row to see why it matched, missing skills, and red flags.
- **Show Low Matches** / **Show Unsure Location** switch those tabs on or off.
- **Generate Documents** writes a resume and cover letter tailored to that job (see section 7).
- **Refresh jobs** runs the whole search and ranking in the background and shows its progress.
- If the workbook is open in Excel, a yellow banner says *"Close the workbook in Excel to save
  changes"*. Your changes wait and save automatically once you close it.
- If you edit the workbook in Excel, the dashboard notices within a few seconds and reloads.

The dashboard only works on your own computer; nobody else can open it.

## 7. Tailored resume and cover letter

In the dashboard, click **Generate Documents** on any job. A larger AI model (`qwen2.5:32b`)
reads the posting and your resume and writes both documents, which takes about a minute (the
first one also loads the model). When it's done, the row shows download links:

- **Resume: DOCX · PDF** and **Cover letter: DOCX · PDF**. Use the Word file if you want to edit
  it, and the PDF to submit. On Windows, PDFs are made by Microsoft Word; without Word you get the Word files
  only, and the row says why. On a Mac, PDFs are made by Microsoft Word for Mac, or by LibreOffice if Word is not installed. Without either, the Word files are still written.
- Files are also saved in `output/documents/<Company>_<Job title>_<id>/`.

**It never makes things up.** The AI may choose what to include, reorder it, and use the
posting's wording where your resume backs it up. Then every claim is checked against your
original resume, and anything that doesn't match is fixed or removed:

- skills, employers, schools, and projects that aren't on your resume are removed,
- job titles and dates are put back to your real ones,
- lines with numbers that aren't in your resume (like an invented "40% faster") are removed,
  and bullets that lost your real numbers get your original wording back,
- stock phrases like "proven track record" or "I am confident" are removed,
- your name, contact details, education, publications, and certifications are copied
  word for word, never rewritten.

**Your GitHub work counts too** if you build a skill catalog (next section): the documents can
then use your GitHub projects and the skills your code shows, under the same fact checks.

Click **▶** on the row to see the list of corrections ("fact-check notes"). **Always read the
documents before you send them.** They are a strong first draft, not a finished application.

**Regenerate** writes new versions and replaces the old files, so if you edited a Word file,
save a copy under a different name first.

### Skill catalog from GitHub (optional)

Your resume can't list everything. The skill catalog collects your GitHub projects so ranking
and documents know about them. Open a command window in the project folder and run:

```
.venv\Scripts\python -m src.catalog github YOUR_GITHUB_NAME
```

- If you're signed in with the GitHub CLI (`gh auth login`), it also reads your private and
  organization repositories. Otherwise it reads public ones without any key.
- It only lists repositories **you committed to**, and records how many of the commits were
  yours. Projects where you made a smaller share are described as "contributed", not "built".
- Skills come from your code (languages and dependency files) in repos where you wrote a real
  share (10%+ or 10+ commits), not from words in a README.
- The local AI writes a short summary of each project from its README, fact-checked the same way
  as the documents.

The result is `config/skill_catalog.json` (private). Open it in Notepad to review it:
- set `"include": false` on any project or skill you don't want used (for example private work),
- fix any summary, and add `"locked": true` so re-collecting never changes it.

Run the command again any time; it keeps your edits and only re-summarizes repos that changed.
The next ranking run re-scores jobs with your catalog included.

## 8. Choosing an AI model

The model is set in `config/models.json` (`"model": "qwen2.5:14b"`). Bigger models judge
better but need more graphics memory and are slower.

| Model | Graphics memory | Notes |
|---|---|---|
| `qwen2.5:7b` | about 6 GB | Fastest. Good choice for a CPU or a smaller graphics card. |
| `qwen2.5:14b` | about 10 GB | **Default.** Good balance of quality and speed. |
| `qwen2.5:32b` | about 21 GB | Better judgment, needs a strong GPU (24 GB cards). |
| `llama3.1:70b` | about 43 GB | Best quality, only for very large GPUs or multi-GPU machines. |

To switch: change the name in `config/models.json`, then run `setup.bat` (it downloads the new
model). The next run re-scores every job with the new model. To try one just once:
`run.bat --model qwen2.5:7b`.

The **documents** model (section 7) is set separately, under `"documents"` in the same file. It
defaults to `qwen2.5:32b` (about 20 GB download, about 21 GB of graphics memory) because larger
models write noticeably better. If your graphics card is smaller, `setup.bat` skips it and
documents are written with the ranking model instead. You can also set `"model": "qwen2.5:14b"`
there yourself.

## 9. Changing what it searches

**Search words and sources: `config/sources.json`.** Open it in Notepad.

- `"keywords"`: titles to look for. A job is kept if its title contains all the words of any
  keyword (for example "backend engineer"). Your target titles from the questions are added
  automatically.
- `"enabled": false` turns a source off.
- **Adding a company:** many companies post on Greenhouse, Lever, or Ashby. Look at the address of
  one of their job postings:
  - `job-boards.greenhouse.io/`**`stripe`**`/jobs/…` → add `"stripe": "Stripe"` under `greenhouse`
  - `jobs.lever.co/`**`acme`**`/…` → under `lever`
  - `jobs.ashbyhq.com/`**`notion`**`/…` → under `ashby`
- **Optional extra sources** (Adzuna, USAJOBS, Jooble, Findwork) need a free key. Copy
  `config/api_keys.example.json` to `config/api_keys.json` and fill in the keys you sign up for.
  Each source turns on by itself once its key is there. Nothing else ever needs a key.

**How far back to search:** run `run.bat --reset-preferences` and change the last answer.

**How the score is weighted:** `config/preferences.json` → `score_weights` (AI review 0.5,
resume similarity 0.25, keywords 0.15, salary 0.1). Changing these is instant; nothing is
re-reviewed.

The job sites used are ones that offer public feeds for this kind of use (RemoteOK, Remotive, We
Work Remotely, Arbeitnow, Himalayas, Jobicy, The Muse, JobYap, Parlel, and company boards on
Greenhouse, Lever, and Ashby). LinkedIn, Indeed, and Glassdoor are not searched: they block
automated tools and their terms forbid it. Every job links back to the site it came from.

## 10. Options for `run.bat` / `run.py`

To use options, open a command window in the folder (type `cmd` in the Explorer address bar)
and run, for example, `run.bat --skip-scrape`.

| Option | What it does |
|---|---|
| `--skip-scrape` | Don't search; re-rank the jobs from the last search and rebuild Excel. |
| `--rescore-all` | Ignore saved scores and review every job again (slow). |
| `--reset-preferences` | Answer the preference questions again. |
| `--model NAME` | Use a different Ollama model for this run. |

`python run.py` works the same way if you prefer it.

## 11. Common problems

| You see | What to do |
|---|---|
| *Python 3.10 or newer was not found* | Install Python from python.org and tick "Add python.exe to PATH". Then run `setup.bat` again. |
| *Ollama is not installed* | Install it from ollama.com/download, then run `setup.bat` again. |
| *Ollama is not running* | Open **Ollama** from the Start menu, then run again. |
| *The model '…' is not downloaded* | Run `setup.bat` (or `ollama pull <name>` in a command window). |
| *No resume found* / *Found 2 resumes* | Keep exactly one PDF, DOCX, or TXT in the `resume` folder. |
| *…has almost no readable text* | Your PDF is a scanned image. Export a text-based PDF or a DOCX. |
| *…is open in Excel. Close the workbook…* | Close the workbook in Excel and run `run.bat --skip-scrape`. |
| *Could not reach any job site* | Check your internet connection and try again. |
| *Lever: FAILED (…)* | One site had a problem. The others still ran; it usually works next time. |
| *Port 5000 is already in use* | The dashboard is probably already open; go to http://localhost:5000. On a Mac, AirPlay Receiver often owns 5000. Double-click `dashboard.command` and it picks another port unless you passed `--port`. |
| *macOS says the app is from an unidentified developer* | Right-click `Job Tracker.app` or the `.command` file and choose Open. A git clone does not need this. |
| *Python 3.10 or newer was not found* (Mac) | `/usr/bin/python3` on older macOS is too old. Install Python 3.10+ from python.org or `brew install python@3.12`, then double-click `setup.command` again. |
| Ranking is very slow | The model is running on the CPU. Switch to `qwen2.5:7b` (section 8), or let it run once overnight; later runs are much faster. |
| *AI model will run on: GPU+CPU* | The model is bigger than your graphics memory. Use a smaller model for speed. |
| *No PDF: Microsoft Word is not installed* | The Word (.docx) files are ready. Open them in Word, Google Docs, or LibreOffice and save as PDF. |
| *Written with qwen2.5:14b because qwen2.5:32b is not downloaded* | Documents still work. For better writing, run `setup.bat` or `ollama pull qwen2.5:32b`. |
| *This posting's full text was not downloaded* | Some sites only give a summary. Open the posting and write your documents from there. |
| Generate Documents is slow | The first one loads the large model (up to a minute). Ranking and documents share the graphics card, so they're slower while a refresh is running. |
| Something else | Look at `logs/run.log` for details. Running again often helps. |

## 12. Where things are

| Folder / file | What's in it | Private? |
|---|---|---|
| `resume/` | Your resume | **Yes** |
| `config/preferences.json` | Your answers to the questions | **Yes** |
| `config/api_keys.json` | Optional keys (only if you created it) | **Yes** |
| `config/skill_catalog.json` | Your GitHub projects and skills (only if you built it) | **Yes** |
| `config/sources.json`, `config/models.json` | Search and model settings | No |
| `data/` | Search results, scores, and the cache that makes repeat runs fast | **Yes** |
| `output/` | The Excel workbook, its backups, and generated documents (`output/documents/`) | **Yes** |
| `logs/run.log` | Details of each run, for troubleshooting | **Yes** |

**Sharing this project:** `.gitignore` already keeps the private items above (and `.venv`) out
of Git. To strip a copy completely before zipping or pushing it, run **`scrub.bat`** in that
copy. It deletes your resume, settings, skill catalog, job data, workbook, generated documents,
logs, `.venv`, and Python caches, leaving a fresh download. It asks you to type `SCRUB` first,
only touches its own folder, and cannot be undone, so **run it on a copy** if you want to keep
your data. `scrub.bat --with-models` also removes the downloaded Ollama models. On a Mac, use
`scrub.command` the same way (`scrub.command --with-models`).

`samples/` has a made-up resume and fake jobs for trying things out.

---

### For developers

- Tests: `.venv\Scripts\python -m unittest discover tests` (they run offline with a fake Ollama).
- Add a job source: create one file in `src/scrapers/` with a class that subclasses `Source`,
  sets `key` and `label`, and yields `make_job(...)` from `fetch()`. It's picked up
  automatically. The runner handles keyword and date filtering, de-duplication, and the
  remote-US check.
- Pipeline: `src/pipeline.py` (used by `run.py` and the dashboard's Refresh button). Ranking:
  `src/ranker.py`. Workbook: `src/excel_builder.py`. Dashboard: `src/dashboard/`. Documents:
  `src/documents/` (`generator.py` writes and fact-checks, `writer.py` makes the files,
  `python -m src.documents <Job ID>` runs it from the command line).
