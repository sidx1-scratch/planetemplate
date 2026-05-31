"""
request.py — trigger a Paper Plane AI run on GitHub Actions

Required env vars:
  GH_TOKEN   — GitHub token (repo + workflow scopes)
  GH_REPO    — owner/repo  e.g. "sidx1-scratch/paper-plane-ai"

Usage:
  python request.py --web URL --theme flame
  python request.py --youtube ID --theme galaxy --wait
  python request.py --file notes.txt --theme rainbow --wait
  python request.py --web URL --theme skull --model gemma2-9b-it --wait
"""

import os, sys, time, argparse, textwrap, zipfile
from pathlib import Path
import requests

GH_TOKEN  = os.getenv("GH_TOKEN")
GH_REPO   = os.getenv("GH_REPO")
WORKFLOW  = "blueprint.yml"
BRANCH    = os.getenv("GH_BRANCH","main")
API_BASE  = "https://api.github.com"

ALL_THEMES = [
    "default","fighter_jet","military","flame","ocean","jungle","arctic",
    "galaxy","bumblebee","patriot","sakura","lightning","racing",
    "graffiti","skull","rust","rainbow",
]
FREE_MODELS = ["llama3-70b-8192","llama3-8b-8192","gemma2-9b-it","mixtral-8x7b-32768"]

def headers():
    if not GH_TOKEN: sys.exit("ERROR: GH_TOKEN not set.")
    if not GH_REPO:  sys.exit("ERROR: GH_REPO not set.")
    return {"Authorization":f"Bearer {GH_TOKEN}",
            "Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2022-11-28"}

def trigger(inputs):
    r=requests.post(f"{API_BASE}/repos/{GH_REPO}/actions/workflows/{WORKFLOW}/dispatches",
                    headers=headers(),json={"ref":BRANCH,"inputs":inputs})
    if r.status_code==204: print("✅  Workflow triggered.")
    else: print(f"❌  GitHub {r.status_code}:\n{r.text}"); sys.exit(1)

def latest_run():
    r=requests.get(f"{API_BASE}/repos/{GH_REPO}/actions/workflows/{WORKFLOW}/runs",
                   headers=headers(),params={"per_page":1})
    runs=r.json().get("workflow_runs",[])
    return runs[0] if runs else None

def wait_for_run(timeout=600,poll=8):
    print("⏳  Waiting for run to start…",flush=True)
    time.sleep(5)
    run=latest_run()
    if not run: sys.exit("ERROR: No run found.")
    run_id=run["id"]
    print(f"🔗  {run['html_url']}\n🆔  Run ID: {run_id}")
    spin=["|","/ ","—","\\"]
    start=time.time(); i=0
    while True:
        if time.time()-start>timeout: sys.exit(f"Timeout after {timeout}s")
        r=requests.get(f"{API_BASE}/repos/{GH_REPO}/actions/runs/{run_id}",
                       headers=headers()).json()
        status=r.get("status","?"); conc=r.get("conclusion")
        print(f"\r{spin[i%4]}  [{int(time.time()-start):>4}s]  {status:<12}",
              end="",flush=True); i+=1
        if status=="completed": print(); return r
        time.sleep(poll)

def download_artifacts(run_id,out_dir="downloads"):
    arts=requests.get(f"{API_BASE}/repos/{GH_REPO}/actions/runs/{run_id}/artifacts",
                      headers=headers()).json().get("artifacts",[])
    if not arts: print("⚠  No artifacts."); return []
    Path(out_dir).mkdir(exist_ok=True)
    saved=[]
    for a in arts:
        print(f"⬇   {a['name']}…")
        zdata=requests.get(f"{API_BASE}/repos/{GH_REPO}/actions/artifacts/{a['id']}/zip",
                           headers=headers(),allow_redirects=True).content
        zp=Path(out_dir)/f"{a['name']}.zip"
        zp.write_bytes(zdata)
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(Path(out_dir)/a["name"])
        print(f"    → {Path(out_dir)/a['name']}/")
        saved.append(str(zp))
    return saved

def parse_args():
    p=argparse.ArgumentParser(
        description="Trigger Paper Plane AI on GitHub Actions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
          Themes: {', '.join(ALL_THEMES)}

          Examples:
            python request.py --web URL --theme flame
            python request.py --youtube ID --theme galaxy --wait
            python request.py --file notes.txt --theme bumblebee --wait
        """))
    src=p.add_argument_group("input sources")
    src.add_argument("--web",metavar="URL")
    src.add_argument("--youtube",metavar="VIDEO_ID")
    src.add_argument("--file",metavar="PATH",
                     help="Local file — contents sent as workflow input")
    p.add_argument("--theme",default="default",choices=ALL_THEMES)
    p.add_argument("--model",default="llama3-70b-8192",choices=FREE_MODELS)
    p.add_argument("--wait",action="store_true",
                   help="Block until done then download artifacts")
    p.add_argument("--out-dir",default="downloads")
    return p.parse_args()

def main():
    args=parse_args()
    inputs={}
    if args.web:     inputs["web_url"]=args.web
    if args.youtube: inputs["youtube_id"]=args.youtube
    if args.file:
        content=Path(args.file).read_text(encoding="utf-8",errors="replace")
        if len(content)>60_000:
            print(f"⚠  File truncated to 60 000 chars ({len(content)} total)")
            content=content[:60_000]
        inputs["file_content"]=content
    inputs["theme"]=args.theme
    inputs["model"]=args.model

    if not any(k in inputs for k in ("web_url","youtube_id","file_content")):
        sys.exit("ERROR: Provide at least one of --web, --youtube, --file.")

    print("📤  Triggering with:")
    for k,v in inputs.items():
        print(f"    {k}: {v[:80]+'…' if len(str(v))>80 else v}")
    print()
    trigger(inputs)

    if args.wait:
        run=wait_for_run()
        conc=run.get("conclusion","?")
        print(f"{'✅' if conc=='success' else '❌'}  Conclusion: {conc}")
        if conc=="success":
            files=download_artifacts(run["id"],args.out_dir)
            if files: print(f"\n🎉  Artifacts in '{args.out_dir}/'")
        else:
            print(f"    Logs: {run['html_url']}")
    else:
        print(f"💡  Watch: https://github.com/{GH_REPO}/actions/workflows/{WORKFLOW}")

if __name__=="__main__":
    main()
