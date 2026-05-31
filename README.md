# ✈ Paper Plane AI — v2

Turn **any text, webpage, or YouTube video** into a fully illustrated, multi-page paper airplane blueprint PDF.  
Runs 100% free on GitHub Actions (or locally) — no server, no paid APIs.

---

## What's new in v2

| Feature | v1 | v2 |
|---|---|---|
| Output | Text dump on white PNG | Multi-page illustrated PDF |
| Blueprint format | Raw text | Structured JSON → rendered cards |
| Fold lines | None | Dashed (valley) / dotted (mountain) |
| Step cards | None | Numbered cards with fold type badge |
| Tips & warnings | None | Dedicated tips/warnings page |
| Launch diagram | None | ✔ |
| CLI | Env vars only | `argparse` with `--web`, `--youtube`, `--file` |
| Model choice | Hard-coded | `--model` flag (4 free Groq models) |
| Error handling | Silent `except: pass` | Logged, retried, meaningful exit codes |
| Font | Pillow default (tiny) | DejaVu / Liberation (auto-detected) |

---

## Setup

### 1. Get a free Groq API key
Sign up at [console.groq.com](https://console.groq.com) — it's free.

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set your API key
```bash
export GROQ_API_KEY=your_key_here
```

---

## Usage

```bash
# from a webpage
python main.py --web https://en.wikipedia.org/wiki/Paper_plane

# from a YouTube video (pass the video ID, not the full URL)
python main.py --youtube dQw4w9WgXcQ

# from a local text file
python main.py --file my_notes.txt

# combine multiple sources
python main.py --web https://example.com --file extra_notes.txt

# pick a different free model
python main.py --web URL --model gemma2-9b-it

# custom output paths
python main.py --web URL --out-pdf blueprint.pdf --out-png cover.png
```

### Available free models (`--model`)
| Model | Speed | Quality |
|---|---|---|
| `llama3-70b-8192` | medium | ⭐⭐⭐⭐ (default) |
| `llama3-8b-8192`  | fast   | ⭐⭐⭐ |
| `gemma2-9b-it`    | fast   | ⭐⭐⭐ |
| `mixtral-8x7b-32768` | medium | ⭐⭐⭐⭐ |

---

## Output

- **`output.pdf`** — full multi-page blueprint (cover + step pages + tips page)
- **`preview.png`** — cover page as PNG (great for GitHub Actions artifacts)

---

## GitHub Actions

Add `GROQ_API_KEY` to your repo **Secrets**, then trigger the workflow manually
and grab the PDF from the **Artifacts** section.

```yaml
# .github/workflows/blueprint.yml
name: Generate Blueprint
on:
  workflow_dispatch:
    inputs:
      web_url:
        description: 'Webpage URL (optional)'
      youtube_id:
        description: 'YouTube video ID (optional)'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python main.py --web "${{ github.event.inputs.web_url }}" --youtube "${{ github.event.inputs.youtube_id }}"
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: blueprint
          path: |
            output.pdf
            preview.png
```

---

## How it works

```
Input (URL / YouTube / file)
        │
        ▼
  Text extraction
        │
        ▼
  Groq LLM (free tier)
  → structured JSON blueprint
  { name, difficulty, steps[], tips[], ... }
        │
        ▼
  Pillow renderer
  → Cover page (specs + schematic)
  → Step pages (fold cards + diagrams)
  → Tips page (tips + warnings + launch guide)
        │
        ▼
  ReportLab → multi-page PDF
```

---

## Remote requests via `request.py`

`request.py` lets you trigger a blueprint run on GitHub Actions from your local
machine (or any CI) without touching the GitHub UI.

### One-time setup

1. Create a GitHub token at [github.com/settings/tokens](https://github.com/settings/tokens)
   with **repo** + **workflow** scopes.
2. Set env vars:
   ```bash
   export GH_TOKEN=ghp_your_token_here
   export GH_REPO=sidx1-scratch/paper-plane-ai
   ```
3. Add `GROQ_API_KEY` to your repo **Secrets** (Settings → Secrets → Actions).

### Usage

```bash
# fire and forget — check Actions UI yourself
python request.py --web https://en.wikipedia.org/wiki/Paper_plane

# fire and WAIT — blocks until done, then auto-downloads artifacts
python request.py --youtube dQw4w9WgXcQ --wait

# pass a local file as input
python request.py --file my_notes.txt --wait

# pick a model
python request.py --web URL --model gemma2-9b-it --wait

# combine sources
python request.py --web URL --youtube ID --wait
```

With `--wait`, the script polls GitHub every 8 seconds, shows a live spinner,
and downloads the PDF + PNG into `downloads/` when the run finishes.

### Full flow

```
You (local)                     GitHub Actions
──────────────────────────────────────────────────────
python request.py --web URL
  │
  ├─ POST /repos/.../dispatches ──────────────────► workflow_dispatch fires
  │                                                        │
  │  (--wait)                                              ▼
  │                                               pip install requirements
  ├─ poll GET /runs (every 8s)                            │
  │  spinner: | / — \                             python main.py --web URL
  │                                                        │
  │                                               output.pdf + preview.png
  │                                                        │
  │                                               upload-artifact
  │                                                        │
  └─ run complete                        ◄────────────────┘
       │
       ├─ download blueprint-pdf.zip → downloads/blueprint-pdf/output.pdf
       └─ download blueprint-preview.zip → downloads/blueprint-preview/preview.png
```
