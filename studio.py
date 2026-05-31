"""
Paper Plane AI Studio
=====================

Desktop launcher for Paper Plane AI.

Features
- Configure GitHub repo/token/branch.
- Launch Paper Plane AI via GitHub Actions.
- Monitor active and past runs.
- Download artifacts.
- Run local CAD preview via main.py.

Requirements
- Python 3
- requests
- pillow
"""

import os
import sys
import json
import time
import zipfile
import threading
import subprocess
import webbrowser
import base64
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    import requests as http
except ImportError:
    sys.exit("Install requests: pip install requests")

try:
    from PIL import Image, ImageTk, ImageDraw
    PILOK = True
except ImportError:
    PILOK = False


APPNAME = "Paper Plane AI Studio"
CONFIGPATH = Path.home() / ".paperplanestudio.json"
WORKFLOW = "blueprint.yml"
APIBASE = "https://api.github.com"
POLLINTERVALMS = 7000

ALLTHEMES = [
    "default",
    "fighter_jet",
    "military",
    "flame",
    "ocean",
    "jungle",
    "arctic",
    "galaxy",
    "bumblebee",
    "patriot",
    "sakura",
    "lightning",
    "racing",
    "graffiti",
    "skull",
    "rust",
    "rainbow",
]

THEMEEMOJI = {
    "default": "✈",
    "fighter_jet": "🛩",
    "military": "🪖",
    "flame": "🔥",
    "ocean": "🌊",
    "jungle": "🌿",
    "arctic": "❄",
    "galaxy": "🌌",
    "bumblebee": "🐝",
    "patriot": "🇺🇸",
    "sakura": "🌸",
    "lightning": "⚡",
    "racing": "🏎",
    "graffiti": "🎨",
    "skull": "💀",
    "rust": "🦾",
    "rainbow": "🌈",
}

THEMEACCENT = {
    "default": "E94560",
    "fighter_jet": "FF6B35",
    "military": "8BC34A",
    "flame": "FF6B00",
    "ocean": "00B4D8",
    "jungle": "6DBF67",
    "arctic": "4FC3F7",
    "galaxy": "A855F7",
    "bumblebee": "FFD700",
    "patriot": "B21F35",
    "sakura": "FF85A1",
    "lightning": "FFE600",
    "racing": "CC0000",
    "graffiti": "FF3366",
    "skull": "AAAAAA",
    "rust": "D2691E",
    "rainbow": "FF00AA",
}

FREE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
]

TITLE = "Paper Plane AI Studio"

C = {
    "bg": "#0F1117",
    "panel": "#1A1D27",
    "card": "#22263A",
    "border": "#2E3250",
    "accent": "#5865F2",
    "accent2": "#EB459E",
    "success": "#57F287",
    "warning": "#FEE75C",
    "danger": "#ED4245",
    "text": "#DCDDDE",
    "muted": "#72767D",
    "white": "#FFFFFF",
    "inputbg": "#2F3349",
}


def loadconfig():
    try:
        return json.loads(CONFIGPATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "token": "",
            "repo": "",
            "branch": "main",
            "outdir": str(Path.home() / "Downloads"),
        }


def saveconfig(cfg):
    CONFIGPATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def normalize_theme(theme):
    if not theme:
        return "default"
    theme = theme.strip()
    theme = theme.replace("fighterjet", "fighter_jet")
    theme = theme.replace(" ", "_")
    return theme if theme in ALLTHEMES else "default"


def fmttime(iso):
    try:
        dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return iso[:16] if iso else "?"


def statuscolour(status, conclusion):
    if status == "completed":
        return {
            "success": C["success"],
            "failure": C["danger"],
            "cancelled": C["warning"],
        }.get(conclusion or "", C["muted"])
    return {
        "queued": C["warning"],
        "in_progress": C["accent"],
    }.get(status, C["muted"])


def statusicon(status, conclusion):
    if status == "completed":
        return {
            "success": "✅",
            "failure": "❌",
            "cancelled": "⚠",
        }.get(conclusion or "", "•")
    return {
        "queued": "⏳",
        "in_progress": "▶",
    }.get(status, "•")


class GitHubAPI:
    def __init__(self, token, repo, branch="main"):
        self.token = token
        self.repo = repo
        self.branch = branch

    def h(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def trigger(self, inputs: dict):
        url = f"{APIBASE}/repos/{self.repo}/actions/workflows/{WORKFLOW}/dispatches"
        r = http.post(url, headers=self.h(), json={"ref": self.branch, "inputs": inputs}, timeout=20)
        if r.status_code == 204:
            return True, "Workflow triggered successfully."
        return False, f"GitHub {r.status_code}: {r.text[:200]}"

    def listruns(self, perpage=15):
        r = http.get(
            f"{APIBASE}/repos/{self.repo}/actions/workflows/{WORKFLOW}/runs",
            headers=self.h(),
            params={"per_page": perpage},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("workflow_runs", [])

    def getrun(self, runid):
        r = http.get(f"{APIBASE}/repos/{self.repo}/actions/runs/{runid}", headers=self.h(), timeout=20)
        r.raise_for_status()
        return r.json()

    def getartifacts(self, runid):
        r = http.get(f"{APIBASE}/repos/{self.repo}/actions/runs/{runid}/artifacts", headers=self.h(), timeout=20)
        r.raise_for_status()
        return r.json().get("artifacts", [])

    def downloadartifact(self, artid, outdir: Path, name: str, progresscb=None):
        url = f"{APIBASE}/repos/{self.repo}/actions/artifacts/{artid}/zip"
        resp = http.get(url, headers=self.h(), allow_redirects=True, stream=True, timeout=60)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        done = 0
        zpath = outdir / f"{name}.zip"

        with open(zpath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progresscb and total:
                        progresscb(done, total)

        extractdir = outdir / name
        extractdir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(extractdir)
        return extractdir

    def validate(self):
        try:
            r = http.get(f"{APIBASE}/user", headers=self.h(), timeout=15)
            if r.status_code == 200:
                return True, r.json().get("login", "?")
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)


swatchcache = {}


def makeswatch(theme: str, size=22):
    if not PILOK:
        return None
    if theme in swatchcache:
        return swatchcache[theme]
    accent = THEMEACCENT.get(theme, "888888")
    r = int(accent[0:2], 16)
    g = int(accent[2:4], 16)
    b = int(accent[4:6], 16)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([1, 1, size - 2, size - 2], fill=(r, g, b, 255))
    tkimg = ImageTk.PhotoImage(img)
    swatchcache[theme] = tkimg
    return tkimg


class PaperPlaneStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APPNAME)
        self.geometry("1100x780")
        self.minsize(900, 640)
        self.configure(bg=C["bg"])

        self.cfg = loadconfig()
        self.api = None
        self.activerunid = None
        self.polljob = None
        self.monstarttime = None

        self.buildstyles()
        self.buildui()
        self.tryrestoreapi()

    def buildstyles(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure(".", background=C["bg"], foreground=C["text"], fieldbackground=C["inputbg"], borderwidth=0, relief="flat")
        s.configure("TNotebook", background=C["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=C["panel"], foreground=C["muted"], padding=(18, 8), font=("Helvetica", 11, "bold"))
        s.map("TNotebook.Tab", background=[("selected", C["card"])], foreground=[("selected", C["white"])])
        s.configure("TFrame", background=C["bg"])
        s.configure("Card.TFrame", background=C["card"])
        s.configure("TLabel", background=C["bg"], foreground=C["text"])
        s.configure("Muted.TLabel", background=C["bg"], foreground=C["muted"], font=("Helvetica", 10))
        s.configure("Heading.TLabel", background=C["bg"], foreground=C["white"], font=("Helvetica", 13, "bold"))
        s.configure("Title.TLabel", background=C["bg"], foreground=C["white"], font=("Helvetica", 22, "bold"))
        s.configure("TEntry", fieldbackground=C["inputbg"], foreground=C["white"], insertcolor=C["white"], borderwidth=1, relief="flat")
        s.configure("TCombobox", fieldbackground=C["inputbg"], foreground=C["white"], selectbackground=C["accent"], arrowcolor=C["text"])
        s.map("TCombobox", fieldbackground=[("readonly", C["inputbg"])], foreground=[("readonly", C["white"])])
        s.configure("TCheckbutton", background=C["bg"], foreground=C["text"])
        s.configure("TScrollbar", background=C["panel"], troughcolor=C["bg"], arrowcolor=C["muted"])
        s.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["card"], borderwidth=0)
        s.configure("Treeview", background=C["card"], foreground=C["text"], fieldbackground=C["card"], rowheight=32, font=("Helvetica", 11))
        s.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], font=("Helvetica", 10, "bold"))
        s.map("Treeview", background=[("selected", C["accent"])])

    def setstatus(self, msg):
        self.sbar.config(text=msg)

    def loglaunch(self, msg, colour=None):
        self.launchlog.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.launchlog.insert("end", line)
        if colour:
            idx = self.launchlog.index("end-1c").split(".")[0]
            tag = f"c{colour.replace('#', '')}"
            self.launchlog.tag_config(tag, foreground=colour)
            self.launchlog.tag_add(tag, f"{idx}.0", f"{idx}.end")
        self.launchlog.see("end")
        self.launchlog.configure(state="disabled")

    def logmonitor(self, msg, colour=None):
        self.monlog.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.monlog.insert("end", line)
        if colour:
            idx = self.monlog.index("end-1c").split(".")[0]
            tag = f"m{colour.replace('#', '')}"
            self.monlog.tag_config(tag, foreground=colour)
            self.monlog.tag_add(tag, f"{idx}.0", f"{idx}.end")
        self.monlog.see("end")
        self.monlog.configure(state="disabled")

    def buildui(self):
        hdr = tk.Frame(self, bg=C["panel"], height=58)
        hdr.pack(fill="x", side="top")
        tk.Label(hdr, text="Paper Plane AI Studio", bg=C["panel"], fg=C["white"], font=("Helvetica", 17, "bold")).pack(side="left", padx=20, pady=14)
        self.statusdot = tk.Label(hdr, text="not connected", bg=C["panel"], fg=C["muted"], font=("Helvetica", 10))
        self.statusdot.pack(side="right", padx=20)
        tk.Frame(self, bg=C["accent"], height=3).pack(fill="x", side="top")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        self.tablaunch = ttk.Frame(self.nb)
        self.tabmonitor = ttk.Frame(self.nb)
        self.tabhistory = ttk.Frame(self.nb)
        self.tabsettings = ttk.Frame(self.nb)

        self.nb.add(self.tablaunch, text="Launch")
        self.nb.add(self.tabmonitor, text="Monitor")
        self.nb.add(self.tabhistory, text="History")
        self.nb.add(self.tabsettings, text="Settings")

        self.buildlaunchtab()
        self.buildmonitortab()
        self.buildhistorytab()
        self.buildsettingstab()

        self.sbar = tk.Label(self, text="Ready", bg=C["panel"], fg=C["muted"], font=("Helvetica", 10), anchor="w", padx=12)
        self.sbar.pack(fill="x", side="bottom", ipady=4)

    def buildlaunchtab(self):
        root = self.tablaunch
        left = tk.Frame(root, bg=C["bg"])
        right = tk.Frame(root, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=20, pady=20)
        right.pack(side="right", fill="y", padx=0, pady=20, ipadx=4)

        ttk.Label(left, text="INPUT SOURCE", style="Muted.TLabel").pack(anchor="w")
        self.srcmode = tk.StringVar(value="web")
        srcbar = tk.Frame(left, bg=C["bg"])
        srcbar.pack(fill="x", pady=(4, 12))
        for mode, label in [("web", "Web URL"), ("youtube", "YouTube ID"), ("file", "File")]:
            b = tk.Radiobutton(srcbar, text=label, variable=self.srcmode, value=mode, bg=C["bg"], fg=C["text"], selectcolor=C["card"], activebackground=C["bg"], activeforeground=C["white"], font=("Helvetica", 11), command=self.onsrcmode)
            b.pack(side="left", padx=(0, 16))

        self.srcentry = ttk.Entry(left, font=("Helvetica", 12), width=55)
        self.srcentry.pack(fill="x", ipady=6)
        self.srchint = ttk.Label(left, text="Enter a full URL", style="Muted.TLabel")
        self.srchint.pack(anchor="w", pady=(2, 0))

        self.browsebtn = tk.Button(left, text="Browse", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=4, cursor="hand2", command=self.browsefile)

        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", pady=14)

        ttk.Label(left, text="GROQ MODEL", style="Muted.TLabel").pack(anchor="w")
        self.modelvar = tk.StringVar(value=FREE_MODELS[0])
        modelbox = ttk.Combobox(left, textvariable=self.modelvar, values=FREE_MODELS, state="readonly", font=("Helvetica", 12), width=36)
        modelbox.pack(anchor="w", pady=(4, 0), ipady=4)
        ttk.Label(left, text="llama-3.3 is best quality; 8b is fastest", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", pady=14)

        ttk.Label(left, text="CUSTOM IMAGE (optional)", style="Muted.TLabel").pack(anchor="w")
        imgrow = tk.Frame(left, bg=C["bg"])
        imgrow.pack(fill="x", pady=(4, 0))
        self.imgentry = ttk.Entry(imgrow, font=("Helvetica", 12))
        self.imgentry.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(imgrow, text="Browse", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=4, cursor="hand2", command=self.browseimage).pack(side="left", padx=8)
        ttk.Label(left, text="Your image is mapped directly onto the plane body skin.", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", pady=16)

        btnrow = tk.Frame(left, bg=C["bg"])
        btnrow.pack(fill="x")
        self.launchbtn = tk.Button(btnrow, text="Launch on GitHub Actions", bg=C["accent"], fg=C["white"], font=("Helvetica", 13, "bold"), relief="flat", padx=20, pady=10, cursor="hand2", command=self.dolaunch)
        self.launchbtn.pack(side="left")
        self.cadbtn = tk.Button(btnrow, text="CAD Preview Only", bg=C["card"], fg=C["text"], font=("Helvetica", 11), relief="flat", padx=14, pady=10, cursor="hand2", command=self.docadpreview)
        self.cadbtn.pack(side="left", padx=(10, 0))

        tk.Frame(left, bg=C["border"], height=1).pack(fill="x", pady=14)
        self.launchlog = scrolledtext.ScrolledText(left, height=7, bg=C["card"], fg=C["text"], font=("Courier", 11), relief="flat", insertbackground=C["white"], state="disabled")
        self.launchlog.pack(fill="x")

        ttk.Label(right, text="THEME", style="Muted.TLabel").pack(anchor="w")
        self.themevar = tk.StringVar(value="default")
        themescrollframe = tk.Frame(right, bg=C["bg"])
        themescrollframe.pack(fill="both", expand=True, pady=(4, 0))
        canvas2 = tk.Canvas(themescrollframe, bg=C["bg"], highlightthickness=0, width=230)
        vsb = ttk.Scrollbar(themescrollframe, orient="vertical", command=canvas2.yview)
        canvas2.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas2.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas2, bg=C["bg"])
        canvas2.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas2.configure(scrollregion=canvas2.bbox("all")))
        self.themeradios = []
        for theme in ALLTHEMES:
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", pady=2)
            swatch = makeswatch(theme, 20)
            if swatch:
                tk.Label(row, image=swatch, bg=C["bg"]).pack(side="left", padx=(0, 6))
            rb = tk.Radiobutton(row, text=f"{THEMEEMOJI.get(theme, '✈')} {theme.replace('_', ' ')}", variable=self.themevar, value=theme, bg=C["bg"], fg=C["text"], selectcolor=C["card"], activebackground=C["bg"], activeforeground=C["white"], font=("Helvetica", 11), command=self.onthemechange)
            rb.pack(side="left")
            self.themeradios.append(rb)

        self.themeaccentbar = tk.Frame(right, height=6, bg=C["accent"])
        self.themeaccentbar.pack(fill="x", pady=(8, 0))
        self.themelabel = ttk.Label(right, text="default", style="Muted.TLabel")
        self.themelabel.pack(anchor="w")

    def onsrcmode(self):
        mode = self.srcmode.get()
        hints = {
            "web": "Enter a full URL (https://...)",
            "youtube": "Enter YouTube video ID, not the full URL.",
            "file": "Enter local file path or browse.",
        }
        self.srchint.config(text=hints.get(mode, ""))
        if mode == "file":
            self.browsebtn.pack(anchor="w", pady=6, before=self.launchlog)
        else:
            self.browsebtn.pack_forget()

    def onthemechange(self):
        theme = normalize_theme(self.themevar.get())
        self.themevar.set(theme)
        self.themeaccentbar.config(bg="#" + THEMEACCENT.get(theme, "5865F2"))
        self.themelabel.config(text=f"{THEMEEMOJI.get(theme, '✈')} {theme.replace('_', ' ')}")

    def browsefile(self):
        p = filedialog.askopenfilename(title="Select text file", filetypes=[("Text files", "*.txt *.md *.rst"), ("All files", "*.*")])
        if p:
            self.srcentry.delete(0, "end")
            self.srcentry.insert(0, p)

    def browseimage(self):
        p = filedialog.askopenfilename(title="Select image", filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if p:
            self.imgentry.delete(0, "end")
            self.imgentry.insert(0, p)

    def getapi(self, quiet=False):
        if self.api:
            return self.api
        token = self.cfg.get("token", "").strip()
        repo = self.cfg.get("repo", "").strip()
        branch = self.cfg.get("branch", "main").strip() or "main"
        if token and repo:
            self.api = GitHubAPI(token, repo, branch)
            return self.api
        if not quiet:
            messagebox.showwarning("Not connected", "Go to Settings, enter your GitHub token and repo, then click Save + Connect.")
        return None

    def getapiquiet(self):
        return self.getapi(quiet=True)

    def setstatus(self, msg):
        self.sbar.config(text=msg)

    def tryrestoreapi(self):
        t = self.cfg.get("token", "").strip()
        r = self.cfg.get("repo", "").strip()
        b = self.cfg.get("branch", "main").strip() or "main"
        if t and r:
            self.api = GitHubAPI(t, r, b)
            self.statusdot.config(text=f"connected: {r}", fg=C["success"])
        else:
            self.statusdot.config(text="not connected", fg=C["muted"])

    def dolaunch(self):
        api = self.getapi()
        if not api:
            return

        mode = self.srcmode.get()
        src = self.srcentry.get().strip()
        if not src:
            messagebox.showwarning("Missing input", "Please enter a source URL, video ID, or file path.")
            return

        inputs = {
            "theme": normalize_theme(self.themevar.get()),
            "model": self.modelvar.get().strip() or FREE_MODELS[0],
        }

        if mode == "web":
            inputs["weburl"] = src
        elif mode == "youtube":
            inputs["youtubeid"] = src
        elif mode == "file":
            p = Path(src)
            if not p.exists():
                messagebox.showerror("File not found", f"Cannot read: {src}")
                return
            content = p.read_text(encoding="utf-8", errors="replace")
            if len(content) > 60000:
                self.loglaunch(f"File truncated to 60,000 chars ({len(content)} total).", C["warning"])
                content = content[:60000]
            inputs["filecontent"] = content

        imgpath = self.imgentry.get().strip()
        if imgpath:
            try:
                imgdata = Path(imgpath).read_bytes()
                inputs["imageb64"] = base64.b64encode(imgdata).decode("ascii")
                inputs["imagename"] = Path(imgpath).name
                self.loglaunch(f"Attached image: {Path(imgpath).name}")
            except Exception as e:
                self.loglaunch(f"Could not read image: {e}", C["warning"])

        self.loglaunch(f"Triggering workflow on {api.repo}...", C["muted"])
        self.launchbtn.config(state="disabled", text="Launching...")

        def go():
            try:
                ok, msg = api.trigger(inputs)
                if ok:
                    self.loglaunch(msg, C["success"])
                    self.after(1200, self.switchtomonitor)
                else:
                    self.loglaunch(msg, C["danger"])
            finally:
                self.after(0, lambda: self.launchbtn.config(state="normal", text="Launch on GitHub Actions"))

        threading.Thread(target=go, daemon=True).start()

    def docadpreview(self):
        theme = normalize_theme(self.themevar.get())
        img = self.imgentry.get().strip()
        main = Path("main.py")
        if not main.exists():
            messagebox.showinfo("main.py not found", "Put main.py in the same directory as studio.py to use local CAD preview.")
            return

        cmd = [sys.executable, str(main), "--cad-preview", f"--theme={theme}", "--out-png=cadpreview.png"]
        if img:
            cmd.append(f"--image={img}")

        self.loglaunch(f"Running CAD preview for theme {theme}...", C["muted"])
        self.launchbtn.config(state="disabled")

        def go():
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    self.loglaunch("CAD preview saved to cadpreview.png", C["success"])
                    self.after(0, self.showcadwindow)
                else:
                    self.loglaunch(result.stderr[-500:] or "CAD preview failed.", C["danger"])
            except Exception as e:
                self.loglaunch(str(e), C["danger"])
            finally:
                self.after(0, lambda: self.launchbtn.config(state="normal"))

        threading.Thread(target=go, daemon=True).start()

    def showcadwindow(self):
        if not PILOK:
            messagebox.showinfo("PIL not installed", "Install Pillow to view the CAD preview.")
            return
        p = Path("cadpreview.png")
        if not p.exists():
            return

        win = tk.Toplevel(self)
        win.title("CAD Preview")
        win.configure(bg=C["bg"])

        img = Image.open(p)
        sw = self.winfo_screenwidth() - 80
        sh = self.winfo_screenheight() - 80
        img.thumbnail((sw, sh), Image.LANCZOS)
        tkimg = ImageTk.PhotoImage(img)
        win.img = tkimg
        tk.Label(win, image=tkimg, bg=C["bg"]).pack(padx=10, pady=10)
        tk.Button(win, text="Open full-size", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=6, command=lambda: webbrowser.open(p.resolve().as_uri())).pack(pady=8)

    def switchtomonitor(self):
        self.nb.select(1)
        self.startmonitorpolling()

    def buildmonitortab(self):
        root = self.tabmonitor
        top = tk.Frame(root, bg=C["bg"])
        top.pack(fill="x", padx=20, pady=(18, 8))
        ttk.Label(top, text="ACTIVE RUN", style="Heading.TLabel").pack(side="left")
        tk.Button(top, text="Refresh", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=4, cursor="hand2", command=self.refreshmonitor).pack(side="right")
        tk.Button(top, text="Open in GitHub", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=4, cursor="hand2", command=self.openruninbrowser).pack(side="right", padx=(0, 8))

        infocard = tk.Frame(root, bg=C["card"], padx=16, pady=14)
        infocard.pack(fill="x", padx=20, pady=(0, 12))
        self.monstatusicon = tk.Label(infocard, text="•", font=("Helvetica", 28), bg=C["card"], fg=C["muted"])
        self.monstatusicon.pack(side="left", padx=(0, 14))
        inforight = tk.Frame(infocard, bg=C["card"])
        inforight.pack(side="left", fill="both", expand=True)
        self.montitle = tk.Label(inforight, text="No active run", bg=C["card"], fg=C["white"], font=("Helvetica", 13, "bold"), anchor="w")
        self.montitle.pack(fill="x")
        self.monmeta = tk.Label(inforight, text="Trigger a run from the Launch tab", bg=C["card"], fg=C["muted"], font=("Helvetica", 10), anchor="w")
        self.monmeta.pack(fill="x")

        self.monprogress = ttk.Progressbar(root, mode="indeterminate", style="Horizontal.TProgressbar")
        self.monprogress.pack(fill="x", padx=20, pady=(0, 12))

        self.monelapsed = tk.Label(root, text="", bg=C["bg"], fg=C["muted"], font=("Courier", 11))
        self.monelapsed.pack(anchor="w", padx=24)

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=10)

        ttk.Label(root, text="ARTIFACTS", style="Muted.TLabel").pack(anchor="w", padx=24)
        self.artframe = tk.Frame(root, bg=C["bg"])
        self.artframe.pack(fill="x", padx=20, pady=(6, 0))
        self.noartlabel = tk.Label(self.artframe, text="Artifacts will appear here when the run completes.", bg=C["bg"], fg=C["muted"], font=("Helvetica", 10))
        self.noartlabel.pack(anchor="w")

        self.dlbar = ttk.Progressbar(root, mode="determinate", style="Horizontal.TProgressbar")

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=10)

        ttk.Label(root, text="STATUS LOG", style="Muted.TLabel").pack(anchor="w", padx=24)
        self.monlog = scrolledtext.ScrolledText(root, height=8, bg=C["card"], fg=C["text"], font=("Courier", 11), relief="flat", insertbackground=C["white"], state="disabled")
        self.monlog.pack(fill="both", expand=True, padx=20, pady=(4, 16))

    def startmonitorpolling(self):
        if self.polljob:
            self.after_cancel(self.polljob)
        self.monstarttime = time.time()
        self.refreshmonitor()

    def refreshmonitor(self):
        api = self.getapiquiet()
        if not api:
            return

        def go():
            try:
                runs = api.listruns(perpage=1)
                if not runs:
                    self.after(0, lambda: self.montitle.config(text="No runs found"))
                    return
                run = runs[0]
                self.after(0, lambda r=run: self.updatemonitorui(r, api))
            except Exception as e:
                self.after(0, lambda: self.logmonitor(f"Poll error: {e}", C["danger"]))
            finally:
                self.polljob = self.after(POLLINTERVALMS, self.refreshmonitor)

        threading.Thread(target=go, daemon=True).start()

    def updatemonitorui(self, run, api):
        runid = run.get("id", "?")
        status = run.get("status", "?")
        conclusion = run.get("conclusion")
        name = run.get("name", "Workflow")
        created = run.get("created_at")
        updated = run.get("updated_at")
        col = statuscolour(status, conclusion)
        icon = statusicon(status, conclusion)

        self.activerunid = runid
        self.monstatusicon.config(text=icon, fg=col)
        self.montitle.config(text=f"Run {runid} — {status.upper()}")
        self.monmeta.config(text=f"{name} • started {fmttime(created)} • updated {fmttime(updated)}")
        if self.monstarttime:
            elapsed = int(time.time() - self.monstarttime)
            self.monelapsed.config(text=f"Elapsed {elapsed}s")

        if status == "in_progress":
            self.monprogress.start(12)
        else:
            self.monprogress.stop()
            self.monprogress["value"] = 100 if status == "completed" else 0

        self.renderartifacts(api, runid)
        self.logmonitor(f"Status: {status} {conclusion or ''}".strip(), col)

    def renderartifacts(self, api, runid):
        for w in self.artframe.winfo_children():
            w.destroy()

        def go():
            try:
                arts = api.getartifacts(runid)
                self.after(0, lambda a=arts: self.populateartifacts(a, api))
            except Exception as e:
                self.after(0, lambda: self.logmonitor(f"Artifact fetch error: {e}", C["danger"]))

        threading.Thread(target=go, daemon=True).start()

    def populateartifacts(self, arts, api):
        for w in self.artframe.winfo_children():
            w.destroy()

        if not arts:
            tk.Label(self.artframe, text="No artifacts found for this run.", bg=C["bg"], fg=C["muted"], font=("Helvetica", 10)).pack(anchor="w")
            return

        for art in arts:
            row = tk.Frame(self.artframe, bg=C["card"], padx=12, pady=8)
            row.pack(fill="x", pady=3)
            name = art.get("name", "artifact")
            size = art.get("size_in_bytes", 0) / 1024 / 1024
            icon = "📦"
            low = name.lower()
            if "pdf" in low:
                icon = "📄"
            elif "png" in low or "preview" in low:
                icon = "🖼"
            tk.Label(row, text=f"{icon} {name}", bg=C["card"], fg=C["white"], font=("Helvetica", 11, "bold")).pack(side="left")
            tk.Label(row, text=f"{size:.1f} MB", bg=C["card"], fg=C["muted"], font=("Helvetica", 9)).pack(side="left", padx=(10, 0))

            def dl(a=art):
                self.downloadartifact(a, api)

            tk.Button(row, text="Download", bg=C["accent"], fg=C["white"], relief="flat", padx=10, pady=3, cursor="hand2", command=dl).pack(side="right")

        self.logmonitor(f"Found {len(arts)} artifacts ready to download.", C["success"])

    def downloadartifact(self, art, api):
        outdir = Path(self.cfg.get("outdir", str(Path.home() / "Downloads")))
        outdir.mkdir(parents=True, exist_ok=True)
        self.dlbar.pack(fill="x", padx=20, pady=(0, 6))
        self.dlbar["value"] = 0

        artid = art["id"]
        artname = art.get("name", f"artifact-{artid}")

        def go():
            try:
                extractdir = api.downloadartifact(
                    artid,
                    outdir,
                    artname,
                    progresscb=lambda d, t: self.after(0, lambda v=int(d * 100 / max(1, t)): self.dlbar.config(value=v)),
                )
                self.after(0, lambda: self.onartifactdownloaded(extractdir, artname))
            except Exception as e:
                self.after(0, lambda: self.logmonitor(f"Download failed: {e}", C["danger"]))
            finally:
                self.after(0, lambda: self.dlbar.pack_forget())

        threading.Thread(target=go, daemon=True).start()

    def onartifactdownloaded(self, extractdir: Path, name: str):
        self.logmonitor(f"Saved to {extractdir}", C["success"])
        for suffix, label in [(".pdf", "Open PDF"), (".png", "View PNG")]:
            files = list(extractdir.glob(f"*{suffix}"))
            if files:
                f = files[0]
                tk.Button(self.artframe, text=f"{label}: {f.name}", bg=C["success"], fg=C["bg"], relief="flat", padx=12, pady=5, cursor="hand2", command=lambda fp=f: self.openorpreview(fp)).pack(anchor="w", pady=2, padx=20)
                break

    def openorpreview(self, filepath: Path):
        if PILOK and filepath.suffix.lower() == ".png":
            self.previewimagewindow(filepath)
        else:
            webbrowser.open(filepath.resolve().as_uri())

    def previewimagewindow(self, filepath: Path):
        win = tk.Toplevel(self)
        win.title(filepath.name)
        win.configure(bg=C["bg"])
        img = Image.open(filepath)
        sw = self.winfo_screenwidth() - 80
        sh = self.winfo_screenheight() - 80
        img.thumbnail((sw, sh), Image.LANCZOS)
        tkimg = ImageTk.PhotoImage(img)
        win.img = tkimg
        tk.Label(win, image=tkimg, bg=C["bg"]).pack(padx=10, pady=10)
        tk.Button(win, text="Open externally", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=6, command=lambda: webbrowser.open(filepath.resolve().as_uri())).pack(pady=8)

    def openruninbrowser(self):
        if self.activerunid:
            api = self.getapiquiet()
            if api:
                webbrowser.open(f"https://github.com/{api.repo}/actions/runs/{self.activerunid}")

    def buildhistorytab(self):
        root = self.tabhistory
        top = tk.Frame(root, bg=C["bg"])
        top.pack(fill="x", padx=20, pady=(18, 8))
        ttk.Label(top, text="RUN HISTORY", style="Heading.TLabel").pack(side="left")
        tk.Button(top, text="Refresh", bg=C["card"], fg=C["text"], relief="flat", padx=10, pady=4, cursor="hand2", command=self.refreshhistory).pack(side="right")

        brow = tk.Frame(root, bg=C["bg"])
        brow.pack(fill="x", padx=20, pady=(0, 12))
        tk.Button(brow, text="Open in GitHub", bg=C["card"], fg=C["text"], relief="flat", padx=12, pady=5, cursor="hand2", command=self.openselectedrun).pack(side="left")
        tk.Button(brow, text="Monitor this run", bg=C["accent"], fg=C["white"], relief="flat", padx=12, pady=5, cursor="hand2", command=self.monitorselectedrun).pack(side="left", padx=10)
        tk.Button(brow, text="Download artifacts", bg=C["card"], fg=C["text"], relief="flat", padx=12, pady=5, cursor="hand2", command=self.downloadselectedrun).pack(side="left", padx=10)

        cols = ("id", "status", "conclusion", "theme", "created", "duration")
        self.histtree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse")
        for col, label, w in [
            ("id", "Run ID", 90),
            ("status", "Status", 100),
            ("conclusion", "Result", 90),
            ("theme", "Theme", 110),
            ("created", "Started", 130),
            ("duration", "Duration", 90),
        ]:
            self.histtree.heading(col, text=label)
            self.histtree.column(col, width=w, anchor="center")
        self.histtree.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.histtree.bind("<Double-1>", self.onhistoryselect)

    def refreshhistory(self):
        api = self.getapiquiet()
        if not api:
            return
        self.setstatus("Loading history...")

        def go():
            try:
                runs = api.listruns(perpage=20)
                self.after(0, lambda rr=runs: self.populatehistory(rr))
            except Exception as e:
                self.after(0, lambda: self.setstatus(f"History error: {e}"))

        threading.Thread(target=go, daemon=True).start()

    def populatehistory(self, runs):
        for row in self.histtree.get_children():
            self.histtree.delete(row)
        for run in runs:
            status = run.get("status", "?")
            conclusion = run.get("conclusion") or ""
            created = fmttime(run.get("created_at"))
            updated = run.get("updated_at")
            dur = "?"
            try:
                if run.get("created_at") and run.get("updated_at"):
                    sdt = datetime.strptime(run["created_at"][:19], "%Y-%m-%dT%H:%M:%S")
                    udt = datetime.strptime(run["updated_at"][:19], "%Y-%m-%dT%H:%M:%S")
                    dur = f"{int((udt - sdt).total_seconds() // 60)}m"
            except Exception:
                pass

            field = f"{run.get('display_title','')} {run.get('name','')}".lower()
            theme = next((t for t in ALLTHEMES if t in field), "")
            rid = run.get("id", "")
            self.histtree.insert("", "end", iid=str(rid), values=(rid, status, conclusion, theme, created, dur))

        self.setstatus(f"Loaded {len(runs)} runs.")

    def selectedrunid(self):
        sel = self.histtree.selection()
        return int(sel[0]) if sel else None

    def onhistoryselect(self, event=None):
        self.monitorselectedrun()

    def openselectedrun(self):
        rid = self.selectedrunid()
        api = self.getapiquiet()
        if rid and api:
            webbrowser.open(f"https://github.com/{api.repo}/actions/runs/{rid}")

    def monitorselectedrun(self):
        rid = self.selectedrunid()
        if not rid:
            return
        self.activerunid = rid
        self.nb.select(1)
        self.startmonitorpolling()

    def downloadselectedrun(self):
        rid = self.selectedrunid()
        api = self.getapiquiet()
        if not rid or not api:
            return

        def go():
            try:
                arts = api.getartifacts(rid)
                if not arts:
                    self.after(0, lambda: self.logmonitor("No artifacts for this run.", C["warning"]))
                    return
                self.after(0, lambda a=arts: self.renderartifactssimple(a, rid, api))
            except Exception as e:
                self.after(0, lambda: self.logmonitor(f"Error: {e}", C["danger"]))

        threading.Thread(target=go, daemon=True).start()

    def renderartifactssimple(self, arts, rid, api):
        self.populateartifacts(arts, api)

    def buildsettingstab(self):
        root = self.tabsettings
        padx = dict(padx=28, pady=8)

        ttk.Label(root, text="GitHub Connection", style="Heading.TLabel").pack(anchor="w", **padx)

        ttk.Label(root, text="Repository owner/repo", style="Muted.TLabel").pack(anchor="w", padx=28, pady=(10, 2))
        self.repovar = tk.StringVar(value=self.cfg.get("repo", ""))
        ttk.Entry(root, textvariable=self.repovar, font=("Helvetica", 12), width=40).pack(anchor="w", padx=28, ipady=6)

        ttk.Label(root, text="Branch", style="Muted.TLabel").pack(anchor="w", padx=28, pady=(10, 2))
        self.branchvar = tk.StringVar(value=self.cfg.get("branch", "main"))
        ttk.Entry(root, textvariable=self.branchvar, font=("Helvetica", 12), width=22).pack(anchor="w", padx=28, ipady=6)

        ttk.Label(root, text="Personal Access Token (needs repo + workflow scopes)", style="Muted.TLabel").pack(anchor="w", padx=28, pady=(10, 2))
        tokrow = tk.Frame(root, bg=C["bg"])
        tokrow.pack(fill="x", padx=28, pady=(4, 4))
        self.tokenvar = tk.StringVar(value=self.cfg.get("token", ""))
        self.tokentry = ttk.Entry(tokrow, textvariable=self.tokenvar, show="•", font=("Helvetica", 12), width=52)
        self.tokentry.pack(side="left", ipady=6, fill="x", expand=True)
        tk.Button(tokrow, text="Show", bg=C["card"], fg=C["text"], relief="flat", padx=8, pady=4, cursor="hand2", command=self.toggletokenvis).pack(side="left", padx=(8, 0))
        tk.Button(tokrow, text="Get token", bg=C["card"], fg=C["text"], relief="flat", padx=8, pady=4, cursor="hand2", command=lambda: webbrowser.open("https://github.com/settings/tokens")).pack(side="left", padx=(6, 0))

        ttk.Label(root, text="Download folder", style="Muted.TLabel").pack(anchor="w", padx=28, pady=(10, 2))
        outrow = tk.Frame(root, bg=C["bg"])
        outrow.pack(fill="x", padx=28)
        self.outdirvar = tk.StringVar(value=self.cfg.get("outdir", str(Path.home() / "Downloads")))
        ttk.Entry(outrow, textvariable=self.outdirvar, font=("Helvetica", 12), width=44).pack(side="left", ipady=6, fill="x", expand=True)
        tk.Button(outrow, text="Browse", bg=C["card"], fg=C["text"], relief="flat", padx=8, pady=4, cursor="hand2", command=self.browseoutdir).pack(side="left", padx=(8, 0))

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=28, pady=16)
        btnrow = tk.Frame(root, bg=C["bg"])
        btnrow.pack(anchor="w", padx=28)
        tk.Button(btnrow, text="Save + Connect", bg=C["accent"], fg=C["white"], font=("Helvetica", 12, "bold"), relief="flat", padx=16, pady=8, cursor="hand2", command=self.savesettings).pack(side="left")
        tk.Button(btnrow, text="Test Connection", bg=C["card"], fg=C["text"], font=("Helvetica", 11), relief="flat", padx=14, pady=8, cursor="hand2", command=self.testconnection).pack(side="left", padx=10)

        self.settingsstatus = ttk.Label(root, text="", style="Muted.TLabel")
        self.settingsstatus.pack(anchor="w", padx=28, pady=(8, 0))

    def toggletokenvis(self):
        cur = self.tokentry.cget("show")
        self.tokentry.config(show="" if cur else "•")

    def browseoutdir(self):
        d = filedialog.askdirectory(title="Select download folder")
        if d:
            self.outdirvar.set(d)

    def savesettings(self):
        self.cfg["token"] = self.tokenvar.get().strip()
        self.cfg["repo"] = self.repovar.get().strip()
        self.cfg["branch"] = self.branchvar.get().strip() or "main"
        self.cfg["outdir"] = self.outdirvar.get().strip()
        saveconfig(self.cfg)
        self.api = None
        self.tryrestoreapi()
        self.settingsstatus.config(text="Settings saved.", foreground=C["success"])

    def testconnection(self):
        token = self.tokenvar.get().strip()
        repo = self.repovar.get().strip()
        if not token or not repo:
            self.settingsstatus.config(text="Enter token and repo first.", foreground=C["warning"])
            return
        api = GitHubAPI(token, repo, self.branchvar.get().strip() or "main")

        def go():
            ok, msg = api.validate()
            colour = C["success"] if ok else C["danger"]
            text = f"Connected as {msg}" if ok else f"Failed: {msg}"
            self.after(0, lambda: self.settingsstatus.config(text=text, foreground=colour))
            if ok:
                self.after(0, lambda: self.statusdot.config(text=f"connected: {repo}", fg=C["success"]))

        threading.Thread(target=go, daemon=True).start()


def main():
    app = PaperPlaneStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
