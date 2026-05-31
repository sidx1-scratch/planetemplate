"""
Paper Plane AI Studio
=====================
GUI tool to configure, trigger, monitor and download Paper Plane AI runs
on GitHub Actions — combines request.py + fetch.py into one desktop app.

Requirements (all already in requirements.txt):
  pip install requests pillow

Run:
  python studio.py

First-time setup:
  Fill in GH Token + Repo in the Settings tab, click Save.
  They persist in ~/.paperplane_studio.json
"""

import os, sys, json, time, zipfile, threading, subprocess, webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime

try:
    import requests as http
except ImportError:
    sys.exit("pip install requests")

try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME   = "Paper Plane AI Studio"
CONFIG_PATH= Path.home() / ".paperplane_studio.json"
WORKFLOW   = "blueprint.yml"
API_BASE   = "https://api.github.com"
POLL_INTERVAL_MS = 7000   # ms between status polls

ALL_THEMES = [
    "default","fighter_jet","military","flame","ocean","jungle","arctic",
    "galaxy","bumblebee","patriot","sakura","lightning","racing",
    "graffiti","skull","rust","rainbow",
]
THEME_EMOJI = {
    "default":"✈","fighter_jet":"🛩","military":"🪖","flame":"🔥","ocean":"🌊",
    "jungle":"🌿","arctic":"❄","galaxy":"🌌","bumblebee":"🐝","patriot":"🇺🇸",
    "sakura":"🌸","lightning":"⚡","racing":"🏎","graffiti":"🎨",
    "skull":"💀","rust":"🦾","rainbow":"🌈",
}
THEME_ACCENT = {
    "default":"#E94560","fighter_jet":"#FF6B35","military":"#8BC34A",
    "flame":"#FF6B00","ocean":"#00B4D8","jungle":"#6DBF67","arctic":"#4FC3F7",
    "galaxy":"#A855F7","bumblebee":"#FFD700","patriot":"#B21F35",
    "sakura":"#FF85A1","lightning":"#FFE600","racing":"#CC0000",
    "graffiti":"#FF3366","skull":"#AAAAAA","rust":"#D2691E","rainbow":"#FF00AA",
}
FREE_MODELS = ["llama3-70b-8192","llama3-8b-8192","gemma2-9b-it","mixtral-8x7b-32768"]

# dark colour palette for the UI
C = dict(
    bg      ="#0F1117",
    panel   ="#1A1D27",
    card    ="#22263A",
    border  ="#2E3250",
    accent  ="#5865F2",
    accent2 ="#EB459E",
    success ="#57F287",
    warning ="#FEE75C",
    danger  ="#ED4245",
    text    ="#DCDDDE",
    muted   ="#72767D",
    white   ="#FFFFFF",
    input_bg="#2F3349",
)

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except:
        return {"token":"","repo":"","branch":"main","out_dir":str(Path.home()/"Downloads")}

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg,indent=2))

# ─────────────────────────────────────────────────────────────────────────────
#  GITHUB API
# ─────────────────────────────────────────────────────────────────────────────

class GitHubAPI:
    def __init__(self, token, repo, branch="main"):
        self.token  = token
        self.repo   = repo
        self.branch = branch

    def _h(self):
        return {"Authorization":f"Bearer {self.token}",
                "Accept":"application/vnd.github+json",
                "X-GitHub-Api-Version":"2022-11-28"}

    def trigger(self, inputs: dict) -> tuple[bool, str]:
        url = f"{API_BASE}/repos/{self.repo}/actions/workflows/{WORKFLOW}/dispatches"
        r   = http.post(url, headers=self._h(), json={"ref":self.branch,"inputs":inputs}, timeout=15)
        if r.status_code == 204:
            return True, "Workflow triggered successfully"
        return False, f"GitHub {r.status_code}: {r.text[:200]}"

    def list_runs(self, per_page=15) -> list:
        r = http.get(f"{API_BASE}/repos/{self.repo}/actions/workflows/{WORKFLOW}/runs",
                     headers=self._h(), params={"per_page":per_page}, timeout=15)
        return r.json().get("workflow_runs", [])

    def get_run(self, run_id) -> dict:
        r = http.get(f"{API_BASE}/repos/{self.repo}/actions/runs/{run_id}",
                     headers=self._h(), timeout=15)
        return r.json()

    def get_run_logs_url(self, run_id) -> str:
        return f"https://github.com/{self.repo}/actions/runs/{run_id}"

    def get_artifacts(self, run_id) -> list:
        r = http.get(f"{API_BASE}/repos/{self.repo}/actions/runs/{run_id}/artifacts",
                     headers=self._h(), timeout=15)
        return r.json().get("artifacts", [])

    def download_artifact(self, art_id, out_dir: Path, name: str,
                          progress_cb=None) -> Path:
        url  = f"{API_BASE}/repos/{self.repo}/actions/artifacts/{art_id}/zip"
        resp = http.get(url, headers=self._h(), allow_redirects=True, stream=True, timeout=60)
        total = int(resp.headers.get("content-length", 0))
        done  = 0
        zpath = out_dir / f"{name}.zip"
        with open(zpath,"wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk); done+=len(chunk)
                if progress_cb and total:
                    progress_cb(done/total)
        extract_dir = out_dir / name
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(extract_dir)
        return extract_dir

    def validate_token(self) -> tuple[bool,str]:
        try:
            r = http.get(f"{API_BASE}/user", headers=self._h(), timeout=10)
            if r.status_code == 200:
                return True, r.json().get("login","?")
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

# ─────────────────────────────────────────────────────────────────────────────
#  THEME SWATCH (tiny coloured circle drawn with PIL or fallback canvas)
# ─────────────────────────────────────────────────────────────────────────────

_swatch_cache: dict[str, tk.PhotoImage] = {}

def make_swatch(theme: str, size=22) -> tk.PhotoImage | None:
    if not PIL_OK: return None
    if theme in _swatch_cache: return _swatch_cache[theme]
    accent = THEME_ACCENT.get(theme,"#888888")
    img = Image.new("RGBA",(size,size),(0,0,0,0))
    d   = ImageDraw.Draw(img)
    r,g,b = int(accent[1:3],16),int(accent[3:5],16),int(accent[5:7],16)
    d.ellipse([1,1,size-2,size-2],fill=(r,g,b,255))
    tk_img = ImageTk.PhotoImage(img)
    _swatch_cache[theme] = tk_img
    return tk_img

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def status_colour(status, conclusion):
    if status == "completed":
        return {"success":C["success"],"failure":C["danger"],
                "cancelled":C["warning"]}.get(conclusion or "", C["muted"])
    return {"queued":C["warning"],"in_progress":C["accent"]}.get(status,C["muted"])

def status_icon(status, conclusion):
    if status == "completed":
        return {"success":"✅","failure":"❌","cancelled":"⚠"}.get(conclusion or "","⬜")
    return {"queued":"⏳","in_progress":"🔄"}.get(status,"⬜")

def fmt_time(iso: str) -> str:
    try:
        dt = datetime.strptime(iso[:19],"%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%b %d  %H:%M")
    except:
        return iso[:16] if iso else "—"

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class PaperPlaneStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x780")
        self.minsize(900,640)
        self.configure(bg=C["bg"])

        self.cfg   = load_config()
        self._api  = None          # GitHubAPI instance, built on demand
        self._active_run_id = None # run being monitored
        self._poll_job      = None # after() job id

        self._build_styles()
        self._build_ui()
        self._try_restore_api()

    # ── styles ────────────────────────────────────────────────────────────────
    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure(".",background=C["bg"],foreground=C["text"],
                    fieldbackground=C["input_bg"],borderwidth=0,relief="flat")
        s.configure("TNotebook",background=C["bg"],borderwidth=0)
        s.configure("TNotebook.Tab",background=C["panel"],foreground=C["muted"],
                    padding=[18,8],font=("Helvetica",11,"bold"))
        s.map("TNotebook.Tab",
              background=[("selected",C["card"])],
              foreground=[("selected",C["white"])])
        s.configure("TFrame",background=C["bg"])
        s.configure("Card.TFrame",background=C["card"])
        s.configure("TLabel",background=C["bg"],foreground=C["text"])
        s.configure("Muted.TLabel",background=C["bg"],foreground=C["muted"],font=("Helvetica",10))
        s.configure("Heading.TLabel",background=C["bg"],foreground=C["white"],
                    font=("Helvetica",13,"bold"))
        s.configure("Title.TLabel",background=C["bg"],foreground=C["white"],
                    font=("Helvetica",22,"bold"))
        s.configure("TEntry",fieldbackground=C["input_bg"],foreground=C["white"],
                    insertcolor=C["white"],borderwidth=1,relief="flat")
        s.configure("TCombobox",fieldbackground=C["input_bg"],foreground=C["white"],
                    selectbackground=C["accent"],arrowcolor=C["text"])
        s.map("TCombobox",fieldbackground=[("readonly",C["input_bg"])],
              foreground=[("readonly",C["white"])])
        s.configure("TCheckbutton",background=C["bg"],foreground=C["text"])
        s.configure("TScrollbar",background=C["panel"],troughcolor=C["bg"],
                    arrowcolor=C["muted"])
        s.configure("Horizontal.TProgressbar",background=C["accent"],
                    troughcolor=C["card"],borderwidth=0)

    # ── root layout ───────────────────────────────────────────────────────────
    def _build_ui(self):
        # header
        hdr = tk.Frame(self,bg=C["panel"],height=58)
        hdr.pack(fill="x",side="top")
        tk.Label(hdr,text="✈  Paper Plane AI Studio",bg=C["panel"],fg=C["white"],
                 font=("Helvetica",17,"bold")).pack(side="left",padx=20,pady=14)
        self._status_dot = tk.Label(hdr,text="⬤  not connected",bg=C["panel"],
                                    fg=C["muted"],font=("Helvetica",10))
        self._status_dot.pack(side="right",padx=20)

        # separator
        tk.Frame(self,bg=C["accent"],height=3).pack(fill="x")

        # notebook
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both",expand=True,padx=0,pady=0)

        self._tab_launch  = ttk.Frame(self._nb)
        self._tab_monitor = ttk.Frame(self._nb)
        self._tab_history = ttk.Frame(self._nb)
        self._tab_settings= ttk.Frame(self._nb)

        self._nb.add(self._tab_launch,  text="🚀  Launch")
        self._nb.add(self._tab_monitor, text="📡  Monitor")
        self._nb.add(self._tab_history, text="📋  History")
        self._nb.add(self._tab_settings,text="⚙  Settings")

        self._build_launch_tab()
        self._build_monitor_tab()
        self._build_history_tab()
        self._build_settings_tab()

        # status bar
        self._sbar = tk.Label(self,text="Ready",bg=C["panel"],fg=C["muted"],
                              font=("Helvetica",10),anchor="w",padx=12)
        self._sbar.pack(fill="x",side="bottom",ipady=4)

    # ─────────────────────────────────────────────────────────────────────────
    #  LAUNCH TAB
    # ─────────────────────────────────────────────────────────────────────────
    def _build_launch_tab(self):
        root = self._tab_launch
        root.configure(style="TFrame")

        # split: left = form, right = theme picker
        left  = tk.Frame(root,bg=C["bg"])
        right = tk.Frame(root,bg=C["bg"])
        left.pack(side="left",fill="both",expand=True,padx=20,pady=20)
        right.pack(side="right",fill="y",padx=(0,20),pady=20,ipadx=4)

        # ── LEFT: input sources ──────────────────────────────────────────
        ttk.Label(left,text="INPUT SOURCE",style="Muted.TLabel").pack(anchor="w")

        self._src_mode = tk.StringVar(value="web")
        src_bar = tk.Frame(left,bg=C["bg"])
        src_bar.pack(fill="x",pady=(4,12))
        for mode,label in [("web","🌐 Web URL"),("youtube","▶ YouTube ID"),("file","📄 File")]:
            b=tk.Radiobutton(src_bar,text=label,variable=self._src_mode,value=mode,
                             bg=C["bg"],fg=C["text"],selectcolor=C["card"],
                             activebackground=C["bg"],activeforeground=C["white"],
                             font=("Helvetica",11),command=self._on_src_mode)
            b.pack(side="left",padx=(0,16))

        self._src_entry = ttk.Entry(left,font=("Helvetica",12),width=55)
        self._src_entry.pack(fill="x",ipady=6)
        self._src_hint  = ttk.Label(left,text="Enter a full URL (https://…)",style="Muted.TLabel")
        self._src_hint.pack(anchor="w",pady=(2,0))

        # file browse button (hidden unless file mode)
        self._browse_btn = tk.Button(left,text="Browse…",bg=C["card"],fg=C["text"],
                                     relief="flat",padx=10,pady=4,cursor="hand2",
                                     command=self._browse_file)

        # ── model ────────────────────────────────────────────────────────
        tk.Frame(left,bg=C["border"],height=1).pack(fill="x",pady=14)
        ttk.Label(left,text="GROQ MODEL",style="Muted.TLabel").pack(anchor="w")
        self._model_var = tk.StringVar(value=FREE_MODELS[0])
        model_box = ttk.Combobox(left,textvariable=self._model_var,
                                  values=FREE_MODELS,state="readonly",
                                  font=("Helvetica",12),width=36)
        model_box.pack(anchor="w",pady=(4,0),ipady=4)
        ttk.Label(left,text="llama3-70b is the best quality; 8b is fastest",
                  style="Muted.TLabel").pack(anchor="w",pady=(2,0))

        # ── image override ───────────────────────────────────────────────
        tk.Frame(left,bg=C["border"],height=1).pack(fill="x",pady=14)
        ttk.Label(left,text="CUSTOM IMAGE (optional)",style="Muted.TLabel").pack(anchor="w")
        img_row = tk.Frame(left,bg=C["bg"])
        img_row.pack(fill="x",pady=(4,0))
        self._img_entry = ttk.Entry(img_row,font=("Helvetica",12))
        self._img_entry.pack(side="left",fill="x",expand=True,ipady=6)
        tk.Button(img_row,text="Browse…",bg=C["card"],fg=C["text"],relief="flat",
                  padx=10,pady=4,cursor="hand2",
                  command=self._browse_image).pack(side="left",padx=(8,0))
        ttk.Label(left,text="Your image is mapped directly onto the plane body skin",
                  style="Muted.TLabel").pack(anchor="w",pady=(2,0))

        # ── launch button ────────────────────────────────────────────────
        tk.Frame(left,bg=C["border"],height=1).pack(fill="x",pady=16)
        btn_row = tk.Frame(left,bg=C["bg"])
        btn_row.pack(fill="x")

        self._launch_btn = tk.Button(btn_row,text="🚀  Launch on GitHub Actions",
                                     bg=C["accent"],fg=C["white"],
                                     font=("Helvetica",13,"bold"),
                                     relief="flat",padx=20,pady=10,cursor="hand2",
                                     command=self._do_launch)
        self._launch_btn.pack(side="left")

        self._cad_btn = tk.Button(btn_row,text="👁  CAD Preview Only",
                                  bg=C["card"],fg=C["text"],
                                  font=("Helvetica",11),
                                  relief="flat",padx=14,pady=10,cursor="hand2",
                                  command=self._do_cad_preview)
        self._cad_btn.pack(side="left",padx=(10,0))

        # ── launch log ───────────────────────────────────────────────────
        tk.Frame(left,bg=C["border"],height=1).pack(fill="x",pady=14)
        self._launch_log = scrolledtext.ScrolledText(
            left,height=7,bg=C["card"],fg=C["text"],
            font=("Courier",11),relief="flat",insertbackground=C["white"],
            state="disabled")
        self._launch_log.pack(fill="x")

        # ── RIGHT: theme picker ──────────────────────────────────────────
        ttk.Label(right,text="THEME",style="Muted.TLabel").pack(anchor="w")

        self._theme_var = tk.StringVar(value="default")

        theme_scroll_frame = tk.Frame(right,bg=C["bg"])
        theme_scroll_frame.pack(fill="both",expand=True,pady=(4,0))

        canvas2 = tk.Canvas(theme_scroll_frame,bg=C["bg"],
                            highlightthickness=0,width=230)
        vsb = ttk.Scrollbar(theme_scroll_frame,orient="vertical",
                            command=canvas2.yview)
        canvas2.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right",fill="y")
        canvas2.pack(side="left",fill="both",expand=True)

        inner = tk.Frame(canvas2,bg=C["bg"])
        canvas2.create_window((0,0),window=inner,anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas2.configure(scrollregion=canvas2.bbox("all")))

        self._theme_radios = []
        for theme in ALL_THEMES:
            emoji  = THEME_EMOJI.get(theme,"✈")
            accent = THEME_ACCENT.get(theme,"#888888")
            row    = tk.Frame(inner,bg=C["bg"])
            row.pack(fill="x",pady=2)
            swatch = make_swatch(theme,20)
            if swatch:
                tk.Label(row,image=swatch,bg=C["bg"]).pack(side="left",padx=(0,6))
            rb = tk.Radiobutton(row,text=f"{emoji} {theme.replace('_',' ')}",
                                variable=self._theme_var,value=theme,
                                bg=C["bg"],fg=C["text"],selectcolor=C["card"],
                                activebackground=C["bg"],activeforeground=C["white"],
                                font=("Helvetica",11),command=self._on_theme_change)
            rb.pack(side="left")
            self._theme_radios.append(rb)

        # ── selected theme accent bar ──────────────────────────────────
        self._theme_accent_bar = tk.Frame(right,height=6,bg=C["accent"])
        self._theme_accent_bar.pack(fill="x",pady=(8,0))
        self._theme_label = ttk.Label(right,text="✈  default",style="Muted.TLabel")
        self._theme_label.pack(anchor="w")

    def _on_src_mode(self):
        mode  = self._src_mode.get()
        hints = {"web":"Enter a full URL (https://…)",
                 "youtube":"Enter YouTube video ID (not the full URL)",
                 "file":"Enter local file path or browse →"}
        self._src_hint.config(text=hints[mode])
        if mode=="file":
            self._browse_btn.pack(anchor="w",pady=(6,0))
        else:
            self._browse_btn.pack_forget()

    def _on_theme_change(self):
        theme = self._theme_var.get()
        self._theme_accent_bar.config(bg=THEME_ACCENT.get(theme,C["accent"]))
        self._theme_label.config(text=f"{THEME_EMOJI.get(theme,'✈')}  {theme.replace('_',' ')}")

    def _browse_file(self):
        p = filedialog.askopenfilename(title="Select text file",
                                       filetypes=[("Text/Markdown","*.txt *.md *.rst"),
                                                  ("All files","*.*")])
        if p:
            self._src_entry.delete(0,"end")
            self._src_entry.insert(0,p)

    def _browse_image(self):
        p = filedialog.askopenfilename(title="Select image",
                                       filetypes=[("Images","*.png *.jpg *.jpeg *.webp *.bmp"),
                                                  ("All files","*.*")])
        if p:
            self._img_entry.delete(0,"end")
            self._img_entry.insert(0,p)

    def _log_launch(self, msg: str, colour=None):
        self._launch_log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._launch_log.insert("end", f"[{ts}]  {msg}\n")
        if colour:
            line_idx = int(self._launch_log.index("end-1c").split(".")[0])
            tag = f"col_{colour.replace('#','')}"
            self._launch_log.tag_config(tag,foreground=colour)
            self._launch_log.tag_add(tag,f"{line_idx-1}.0",f"{line_idx-1}.end")
        self._launch_log.see("end")
        self._launch_log.configure(state="disabled")

    def _do_launch(self):
        api = self._get_api()
        if not api: return

        mode = self._src_mode.get()
        src  = self._src_entry.get().strip()
        if not src:
            messagebox.showwarning("Missing input","Please enter a source URL, video ID, or file path.")
            return

        inputs = {"theme": self._theme_var.get(), "model": self._model_var.get()}

        if mode == "web":
            inputs["web_url"] = src
        elif mode == "youtube":
            inputs["youtube_id"] = src
        elif mode == "file":
            p = Path(src)
            if not p.exists():
                messagebox.showerror("File not found", f"Cannot read: {src}")
                return
            content = p.read_text(encoding="utf-8",errors="replace")
            if len(content) > 60_000:
                self._log_launch(f"⚠  File truncated to 60 000 chars ({len(content)} total)",C["warning"])
                content = content[:60_000]
            inputs["file_content"] = content

        # optional custom image — base64 encode it
        img_path = self._img_entry.get().strip()
        if img_path:
            import base64
            try:
                img_data = Path(img_path).read_bytes()
                inputs["image_b64"] = base64.b64encode(img_data).decode()
                inputs["image_name"] = Path(img_path).name
                self._log_launch(f"📎  Image attached: {Path(img_path).name}")
            except Exception as e:
                self._log_launch(f"⚠  Could not read image: {e}",C["warning"])

        self._log_launch(f"🚀  Triggering [{inputs['theme']}] on {api.repo}…")
        self._launch_btn.config(state="disabled",text="Launching…")

        def _go():
            ok, msg = api.trigger(inputs)
            if ok:
                self._log_launch(f"✅  {msg}",C["success"])
                self._log_launch("⏳  Switching to Monitor tab…")
                self.after(1500, self._switch_to_monitor)
            else:
                self._log_launch(f"❌  {msg}",C["danger"])
            self.after(0, lambda: self._launch_btn.config(state="normal",
                                                           text="🚀  Launch on GitHub Actions"))

        threading.Thread(target=_go,daemon=True).start()

    def _do_cad_preview(self):
        """Run main.py --cad-preview locally if main.py is present."""
        theme = self._theme_var.get()
        img   = self._img_entry.get().strip()
        main  = Path("main.py")
        if not main.exists():
            messagebox.showinfo("main.py not found",
                "Put main.py in the same directory as studio.py to use local CAD preview.")
            return
        cmd = [sys.executable,"main.py","--cad-preview",f"--theme={theme}","--out-png=cad_preview.png"]
        if img: cmd.append(f"--image={img}")
        self._log_launch(f"👁  Running CAD preview: {theme}…")
        self._launch_btn.config(state="disabled")

        def _go():
            try:
                result = subprocess.run(cmd,capture_output=True,text=True,timeout=60)
                if result.returncode==0:
                    self._log_launch("✅  CAD preview saved → cad_preview.png",C["success"])
                    self.after(0, self._show_cad_window)
                else:
                    self._log_launch(f"❌  {result.stderr[-300:]}",C["danger"])
            except Exception as e:
                self._log_launch(f"❌  {e}",C["danger"])
            self.after(0, lambda: self._launch_btn.config(state="normal"))

        threading.Thread(target=_go,daemon=True).start()

    def _show_cad_window(self):
        if not PIL_OK:
            messagebox.showinfo("PIL not installed","Install Pillow to view the CAD preview.")
            return
        p = Path("cad_preview.png")
        if not p.exists(): return
        win = tk.Toplevel(self)
        win.title("CAD Preview")
        win.configure(bg=C["bg"])
        img = Image.open(p)
        # fit to screen
        sw,sh = self.winfo_screenwidth()-80, self.winfo_screenheight()-80
        img.thumbnail((sw,sh),Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(img)
        win._img = tk_img   # keep reference
        label   = tk.Label(win,image=tk_img,bg=C["bg"])
        label.pack()
        tk.Button(win,text="Open full-size",bg=C["card"],fg=C["text"],relief="flat",
                  padx=10,pady=6,command=lambda: webbrowser.open(p.resolve().as_uri())
                  ).pack(pady=8)

    def _switch_to_monitor(self):
        self._nb.select(1)   # Monitor tab
        self._start_monitor_polling()

    # ─────────────────────────────────────────────────────────────────────────
    #  MONITOR TAB
    # ─────────────────────────────────────────────────────────────────────────
    def _build_monitor_tab(self):
        root = self._tab_monitor

        # top bar
        top = tk.Frame(root,bg=C["bg"])
        top.pack(fill="x",padx=20,pady=(18,8))
        ttk.Label(top,text="ACTIVE RUN",style="Heading.TLabel").pack(side="left")
        tk.Button(top,text="↻ Refresh",bg=C["card"],fg=C["text"],relief="flat",
                  padx=10,pady=4,cursor="hand2",
                  command=self._refresh_monitor).pack(side="right")
        tk.Button(top,text="🌐 Open in GitHub",bg=C["card"],fg=C["text"],relief="flat",
                  padx=10,pady=4,cursor="hand2",
                  command=self._open_run_in_browser).pack(side="right",padx=(0,8))

        # run info card
        info_card = tk.Frame(root,bg=C["card"],padx=16,pady=14)
        info_card.pack(fill="x",padx=20,pady=(0,12))

        self._mon_status_icon = tk.Label(info_card,text="⬜",font=("Helvetica",28),
                                         bg=C["card"],fg=C["muted"])
        self._mon_status_icon.pack(side="left",padx=(0,14))

        info_right = tk.Frame(info_card,bg=C["card"])
        info_right.pack(side="left",fill="both",expand=True)

        self._mon_title = tk.Label(info_right,text="No active run",bg=C["card"],
                                   fg=C["white"],font=("Helvetica",13,"bold"),anchor="w")
        self._mon_title.pack(fill="x")
        self._mon_meta  = tk.Label(info_right,text="Trigger a run from the Launch tab",
                                   bg=C["card"],fg=C["muted"],font=("Helvetica",10),anchor="w")
        self._mon_meta.pack(fill="x")

        # progress bar
        self._mon_progress = ttk.Progressbar(root,mode="indeterminate",
                                              style="Horizontal.TProgressbar")
        self._mon_progress.pack(fill="x",padx=20,pady=(0,12))

        # elapsed timer
        self._mon_elapsed = tk.Label(root,text="",bg=C["bg"],fg=C["muted"],
                                     font=("Courier",11))
        self._mon_elapsed.pack(anchor="w",padx=24)

        # divider
        tk.Frame(root,bg=C["border"],height=1).pack(fill="x",padx=20,pady=10)

        # artifacts panel
        ttk.Label(root,text="ARTIFACTS",style="Muted.TLabel").pack(anchor="w",padx=24)
        self._art_frame = tk.Frame(root,bg=C["bg"])
        self._art_frame.pack(fill="x",padx=20,pady=(6,0))

        self._no_art_label = tk.Label(self._art_frame,
                                      text="Artifacts will appear here when the run completes.",
                                      bg=C["bg"],fg=C["muted"],font=("Helvetica",10))
        self._no_art_label.pack(anchor="w")

        # download progress bar
        self._dl_bar = ttk.Progressbar(root,mode="determinate",
                                        style="Horizontal.TProgressbar")

        # log output
        tk.Frame(root,bg=C["border"],height=1).pack(fill="x",padx=20,pady=10)
        ttk.Label(root,text="STATUS LOG",style="Muted.TLabel").pack(anchor="w",padx=24)
        self._mon_log = scrolledtext.ScrolledText(
            root,height=8,bg=C["card"],fg=C["text"],
            font=("Courier",11),relief="flat",insertbackground=C["white"],
            state="disabled")
        self._mon_log.pack(fill="both",expand=True,padx=20,pady=(4,16))

        self._mon_start_time = None

    def _log_monitor(self, msg, colour=None):
        self._mon_log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._mon_log.insert("end",f"[{ts}]  {msg}\n")
        if colour:
            idx = int(self._mon_log.index("end-1c").split(".")[0])
            tag = f"mc_{colour.replace('#','')}"
            self._mon_log.tag_config(tag,foreground=colour)
            self._mon_log.tag_add(tag,f"{idx-1}.0",f"{idx-1}.end")
        self._mon_log.see("end")
        self._mon_log.configure(state="disabled")

    def _start_monitor_polling(self):
        if self._poll_job:
            self.after_cancel(self._poll_job)
        self._mon_start_time = time.time()
        self._refresh_monitor()

    def _refresh_monitor(self):
        api = self._get_api(quiet=True)
        if not api: return

        def _go():
            try:
                runs = api.list_runs(per_page=1)
                if not runs:
                    self.after(0,lambda: self._mon_title.config(text="No runs found"))
                    return
                run = runs[0]
                self.after(0, lambda r=run: self._update_monitor_ui(r, api))
            except Exception as e:
                self.after(0,lambda: self._log_monitor(f"Poll error: {e}",C["danger"]))

        threading.Thread(target=_go,daemon=True).start()

    def _update_monitor_ui(self, run: dict, api: GitHubAPI):
        run_id    = run["id"]
        status    = run.get("status","?")
        conclusion= run.get("conclusion")
        name      = run.get("name","Workflow")
        created   = run.get("created_at","")
        col       = status_colour(status,conclusion)
        icon      = status_icon(status,conclusion)

        self._active_run_id = run_id
        self._mon_status_icon.config(text=icon,fg=col)
        self._mon_title.config(text=f"Run #{run_id}  —  {status.upper()}")
        self._mon_meta.config(
            text=f"Started: {fmt_time(created)}   |   Theme: {run.get('inputs',{}).get('theme','?') if hasattr(run,'get') else '?'}")

        # progress bar
        if status=="in_progress":
            self._mon_progress.start(12)
        else:
            self._mon_progress.stop()
            self._mon_progress["value"]=100 if status=="completed" else 0

        # elapsed
        if self._mon_start_time:
            elapsed=int(time.time()-self._mon_start_time)
            self._mon_elapsed.config(text=f"Elapsed: {elapsed}s")

        self._log_monitor(f"{icon} Status: {status}" + (f" / {conclusion}" if conclusion else ""),col)

        if status=="completed":
            if conclusion=="success":
                self._log_monitor("✅  Run succeeded — loading artifacts…",C["success"])
                self._load_artifacts(run_id,api)
            else:
                self._log_monitor(f"❌  Run {conclusion}",C["danger"])
            # stop polling
            if self._poll_job:
                self.after_cancel(self._poll_job)
                self._poll_job=None
        else:
            # schedule next poll
            self._poll_job=self.after(POLL_INTERVAL_MS,self._refresh_monitor)

    def _load_artifacts(self, run_id, api):
        def _go():
            try:
                arts = api.get_artifacts(run_id)
                self.after(0,lambda a=arts: self._render_artifacts(a,run_id,api))
            except Exception as e:
                self.after(0,lambda: self._log_monitor(f"Artifact fetch error: {e}",C["danger"]))
        threading.Thread(target=_go,daemon=True).start()

    def _render_artifacts(self, arts, run_id, api):
        # clear
        for w in self._art_frame.winfo_children(): w.destroy()
        if not arts:
            tk.Label(self._art_frame,text="No artifacts found.",
                     bg=C["bg"],fg=C["muted"],font=("Helvetica",10)).pack(anchor="w")
            return
        for art in arts:
            row=tk.Frame(self._art_frame,bg=C["card"],padx=12,pady=8)
            row.pack(fill="x",pady=3)
            icon="📄" if "pdf" in art["name"].lower() else ("🖼" if "preview" in art["name"].lower() else "📦")
            size_mb=art.get("size_in_bytes",0)/(1024*1024)
            tk.Label(row,text=f"{icon}  {art['name']}",bg=C["card"],fg=C["white"],
                     font=("Helvetica",11,"bold")).pack(side="left")
            tk.Label(row,text=f"  {size_mb:.1f} MB  •  expires {art.get('expires_at','?')[:10]}",
                     bg=C["card"],fg=C["muted"],font=("Helvetica",9)).pack(side="left")
            tk.Button(row,text="⬇ Download",bg=C["accent"],fg=C["white"],
                      relief="flat",padx=10,pady=3,cursor="hand2",
                      command=lambda a=art: self._download_artifact(a,api)).pack(side="right")
        self._log_monitor(f"Found {len(arts)} artifact(s) ready to download",C["success"])

    def _download_artifact(self, art, api):
        out_dir = Path(self.cfg.get("out_dir", Path.home()/"Downloads"))
        out_dir.mkdir(parents=True,exist_ok=True)
        self._dl_bar.pack(fill="x",padx=20,pady=(0,6))
        self._dl_bar["value"]=0

        def _go():
            try:
                self.after(0,lambda: self._log_monitor(f"⬇  Downloading {art['name']}…"))
                extract_dir = api.download_artifact(
                    art["id"], out_dir, art["name"],
                    progress_cb=lambda p: self.after(0,lambda v=p: self._dl_bar.config(value=v*100)))
                self.after(0,lambda d=extract_dir: self._on_artifact_downloaded(d,art["name"]))
            except Exception as e:
                self.after(0,lambda: self._log_monitor(f"❌  Download failed: {e}",C["danger"]))
            self.after(0,lambda: self._dl_bar.pack_forget())

        threading.Thread(target=_go,daemon=True).start()

    def _on_artifact_downloaded(self, extract_dir: Path, name: str):
        self._log_monitor(f"✅  Saved → {extract_dir}",C["success"])
        # look for PDF / PNG and offer preview
        for suffix,label in [(".pdf","📄 Open PDF"),(".png","🖼 View PNG")]:
            files=list(extract_dir.glob(f"*{suffix}"))
            if files:
                f=files[0]
                tk.Button(self._art_frame,text=f"{label}: {f.name}",
                          bg=C["success"],fg=C["bg"],relief="flat",
                          padx=12,pady=5,cursor="hand2",
                          command=lambda fp=f: self._open_or_preview(fp)
                          ).pack(anchor="w",pady=2,padx=20)

    def _open_or_preview(self, filepath: Path):
        if PIL_OK and filepath.suffix.lower()==".png":
            self._preview_image_window(filepath)
        else:
            webbrowser.open(filepath.resolve().as_uri())

    def _preview_image_window(self, filepath: Path):
        win=tk.Toplevel(self)
        win.title(filepath.name)
        win.configure(bg=C["bg"])
        img=Image.open(filepath)
        sw,sh=self.winfo_screenwidth()-80,self.winfo_screenheight()-80
        img.thumbnail((sw,sh),Image.LANCZOS)
        tk_img=ImageTk.PhotoImage(img)
        win._img=tk_img
        tk.Label(win,image=tk_img,bg=C["bg"]).pack()
        tk.Button(win,text="Open externally",bg=C["card"],fg=C["text"],
                  relief="flat",padx=10,pady=6,
                  command=lambda: webbrowser.open(filepath.resolve().as_uri())
                  ).pack(pady=8)

    def _open_run_in_browser(self):
        if self._active_run_id:
            api=self._get_api(quiet=True)
            if api:
                webbrowser.open(f"https://github.com/{api.repo}/actions/runs/{self._active_run_id}")

    # ─────────────────────────────────────────────────────────────────────────
    #  HISTORY TAB
    # ─────────────────────────────────────────────────────────────────────────
    def _build_history_tab(self):
        root = self._tab_history

        top=tk.Frame(root,bg=C["bg"])
        top.pack(fill="x",padx=20,pady=(18,8))
        ttk.Label(top,text="RUN HISTORY",style="Heading.TLabel").pack(side="left")
        tk.Button(top,text="↻ Refresh",bg=C["card"],fg=C["text"],relief="flat",
                  padx=10,pady=4,cursor="hand2",
                  command=self._refresh_history).pack(side="right")

        # treeview
        cols=("id","status","conclusion","theme","created","duration")
        self._hist_tree=ttk.Treeview(root,columns=cols,show="headings",
                                      selectmode="browse")
        for col,label,w in [("id","Run ID",90),("status","Status",100),
                              ("conclusion","Result",90),("theme","Theme",110),
                              ("created","Started",130),("duration","Duration",90)]:
            self._hist_tree.heading(col,text=label)
            self._hist_tree.column(col,width=w,anchor="center" if col!="created" else "w")

        # style tree
        style=ttk.Style()
        style.configure("Treeview",background=C["card"],foreground=C["text"],
                        fieldbackground=C["card"],rowheight=32,font=("Helvetica",11))
        style.configure("Treeview.Heading",background=C["panel"],foreground=C["muted"],
                        font=("Helvetica",10,"bold"))
        style.map("Treeview",background=[("selected",C["accent"])])

        vsb=ttk.Scrollbar(root,orient="vertical",command=self._hist_tree.yview)
        self._hist_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right",fill="y",padx=(0,20),pady=(0,16))
        self._hist_tree.pack(fill="both",expand=True,padx=(20,0),pady=(0,16))

        self._hist_tree.bind("<Double-1>",self._on_history_select)

        # bottom: open + re-monitor buttons
        brow=tk.Frame(root,bg=C["bg"])
        brow.pack(fill="x",padx=20,pady=(0,12))
        tk.Button(brow,text="🌐 Open in GitHub",bg=C["card"],fg=C["text"],
                  relief="flat",padx=12,pady=5,cursor="hand2",
                  command=self._open_selected_run).pack(side="left")
        tk.Button(brow,text="📡 Monitor this run",bg=C["accent"],fg=C["white"],
                  relief="flat",padx=12,pady=5,cursor="hand2",
                  command=self._monitor_selected_run).pack(side="left",padx=(10,0))
        tk.Button(brow,text="⬇ Download artifacts",bg=C["card"],fg=C["text"],
                  relief="flat",padx=12,pady=5,cursor="hand2",
                  command=self._download_selected_run).pack(side="left",padx=(10,0))

    def _refresh_history(self):
        api=self._get_api(quiet=True)
        if not api: return
        self._set_status("Loading history…")
        def _go():
            try:
                runs=api.list_runs(per_page=20)
                self.after(0,lambda r=runs: self._populate_history(r))
            except Exception as e:
                self.after(0,lambda: self._set_status(f"History error: {e}"))
        threading.Thread(target=_go,daemon=True).start()

    def _populate_history(self, runs):
        for row in self._hist_tree.get_children():
            self._hist_tree.delete(row)
        for run in runs:
            status    =run.get("status","?")
            conclusion=run.get("conclusion") or "—"
            created   =fmt_time(run.get("created_at",""))
            # duration
            dur="—"
            try:
                from datetime import datetime as dt
                s=dt.strptime(run["created_at"][:19],"%Y-%m-%dT%H:%M:%S")
                u=dt.strptime(run["updated_at"][:19],"%Y-%m-%dT%H:%M:%S")
                secs=int((u-s).total_seconds())
                dur=f"{secs//60}m {secs%60}s"
            except: pass
            # theme from display_title or name
            theme="?"
            for field in (run.get("display_title",""),run.get("name","")):
                for t in ALL_THEMES:
                    if t in field.lower(): theme=t; break
                if theme!="?": break
            icon=status_icon(status,run.get("conclusion"))
            self._hist_tree.insert("","end",iid=str(run["id"]),
                                    values=(run["id"],
                                            f"{icon} {status}",
                                            conclusion,theme,created,dur))
        self._set_status(f"Loaded {len(runs)} runs")

    def _selected_run_id(self):
        sel=self._hist_tree.selection()
        return int(sel[0]) if sel else None

    def _on_history_select(self, event):
        self._monitor_selected_run()

    def _open_selected_run(self):
        rid=self._selected_run_id()
        api=self._get_api(quiet=True)
        if rid and api:
            webbrowser.open(f"https://github.com/{api.repo}/actions/runs/{rid}")

    def _monitor_selected_run(self):
        rid=self._selected_run_id()
        if not rid: return
        self._active_run_id=rid
        self._nb.select(1)
        self._start_monitor_polling()

    def _download_selected_run(self):
        rid=self._selected_run_id()
        api=self._get_api(quiet=True)
        if not rid or not api: return
        out_dir=Path(self.cfg.get("out_dir",Path.home()/"Downloads"))
        self._nb.select(1)

        def _go():
            try:
                arts=api.get_artifacts(rid)
                if not arts:
                    self.after(0,lambda: self._log_monitor("No artifacts for this run.",C["warning"]))
                    return
                self.after(0,lambda a=arts: self._render_artifacts(a,rid,api))
            except Exception as e:
                self.after(0,lambda: self._log_monitor(f"Error: {e}",C["danger"]))
        threading.Thread(target=_go,daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    #  SETTINGS TAB
    # ─────────────────────────────────────────────────────────────────────────
    def _build_settings_tab(self):
        root=self._tab_settings
        pad=dict(padx=28,pady=8)

        ttk.Label(root,text="GitHub Connection",style="Heading.TLabel").pack(anchor="w",**pad)

        # token
        ttk.Label(root,text="Personal Access Token  (needs repo + workflow scopes)",
                  style="Muted.TLabel").pack(anchor="w",padx=28,pady=(8,2))
        tok_row=tk.Frame(root,bg=C["bg"])
        tok_row.pack(fill="x",padx=28,pady=(0,4))
        self._token_var=tk.StringVar(value=self.cfg.get("token",""))
        self._tok_entry=ttk.Entry(tok_row,textvariable=self._token_var,
                                   show="•",font=("Helvetica",12),width=52)
        self._tok_entry.pack(side="left",ipady=6,fill="x",expand=True)
        tk.Button(tok_row,text="Show",bg=C["card"],fg=C["text"],relief="flat",
                  padx=8,pady=4,cursor="hand2",
                  command=self._toggle_token_vis).pack(side="left",padx=(8,0))
        tk.Button(tok_row,text="🌐 Get token",bg=C["card"],fg=C["text"],relief="flat",
                  padx=8,pady=4,cursor="hand2",
                  command=lambda: webbrowser.open("https://github.com/settings/tokens")
                  ).pack(side="left",padx=(6,0))

        # repo
        ttk.Label(root,text="Repository  (owner/repo)",
                  style="Muted.TLabel").pack(anchor="w",padx=28,pady=(10,2))
        self._repo_var=tk.StringVar(value=self.cfg.get("repo",""))
        ttk.Entry(root,textvariable=self._repo_var,
                  font=("Helvetica",12),width=40).pack(anchor="w",padx=28,ipady=6)

        # branch
        ttk.Label(root,text="Branch",
                  style="Muted.TLabel").pack(anchor="w",padx=28,pady=(10,2))
        self._branch_var=tk.StringVar(value=self.cfg.get("branch","main"))
        ttk.Entry(root,textvariable=self._branch_var,
                  font=("Helvetica",12),width=22).pack(anchor="w",padx=28,ipady=6)

        # output dir
        ttk.Label(root,text="Download folder",
                  style="Muted.TLabel").pack(anchor="w",padx=28,pady=(10,2))
        out_row=tk.Frame(root,bg=C["bg"])
        out_row.pack(fill="x",padx=28)
        self._outdir_var=tk.StringVar(value=self.cfg.get("out_dir",str(Path.home()/"Downloads")))
        ttk.Entry(out_row,textvariable=self._outdir_var,font=("Helvetica",12),width=44
                  ).pack(side="left",ipady=6)
        tk.Button(out_row,text="Browse…",bg=C["card"],fg=C["text"],relief="flat",
                  padx=8,pady=4,cursor="hand2",
                  command=self._browse_outdir).pack(side="left",padx=(8,0))

        # buttons
        tk.Frame(root,bg=C["border"],height=1).pack(fill="x",padx=28,pady=16)
        btn_row=tk.Frame(root,bg=C["bg"])
        btn_row.pack(anchor="w",padx=28)

        tk.Button(btn_row,text="💾  Save & Connect",bg=C["accent"],fg=C["white"],
                  font=("Helvetica",12,"bold"),relief="flat",padx=16,pady=8,cursor="hand2",
                  command=self._save_settings).pack(side="left")
        tk.Button(btn_row,text="🔍  Test Connection",bg=C["card"],fg=C["text"],
                  font=("Helvetica",11),relief="flat",padx=14,pady=8,cursor="hand2",
                  command=self._test_connection).pack(side="left",padx=(10,0))

        self._settings_status=ttk.Label(root,text="",style="Muted.TLabel")
        self._settings_status.pack(anchor="w",padx=28,pady=(8,0))

    def _toggle_token_vis(self):
        cur=self._tok_entry.cget("show")
        self._tok_entry.config(show="" if cur=="•" else "•")

    def _browse_outdir(self):
        d=filedialog.askdirectory(title="Select download folder")
        if d: self._outdir_var.set(d)

    def _save_settings(self):
        self.cfg["token"]  =self._token_var.get().strip()
        self.cfg["repo"]   =self._repo_var.get().strip()
        self.cfg["branch"] =self._branch_var.get().strip() or "main"
        self.cfg["out_dir"]=self._outdir_var.get().strip()
        save_config(self.cfg)
        self._api=None   # reset
        self._try_restore_api()
        self._settings_status.config(text="✅  Settings saved",foreground=C["success"])

    def _test_connection(self):
        self._settings_status.config(text="Testing…",foreground=C["muted"])
        token=self._token_var.get().strip()
        repo =self._repo_var.get().strip()
        if not token or not repo:
            self._settings_status.config(text="Enter token and repo first.",foreground=C["warning"])
            return
        api=GitHubAPI(token,repo,self._branch_var.get().strip() or "main")
        def _go():
            ok,msg=api.validate_token()
            colour=C["success"] if ok else C["danger"]
            text=f"✅  Connected as @{msg}" if ok else f"❌  {msg}"
            self.after(0,lambda: self._settings_status.config(text=text,foreground=colour))
            if ok:
                self.after(0,lambda: self._status_dot.config(
                    text=f"⬤  {msg} / {repo}",fg=C["success"]))
        threading.Thread(target=_go,daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    #  SHARED HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    def _try_restore_api(self):
        t=self.cfg.get("token","")
        r=self.cfg.get("repo","")
        if t and r:
            self._api=GitHubAPI(t,r,self.cfg.get("branch","main"))
            self._status_dot.config(text=f"⬤  {r}",fg=C["success"])
        else:
            self._status_dot.config(text="⬤  not connected",fg=C["muted"])

    def _get_api(self, quiet=False) -> GitHubAPI | None:
        if self._api: return self._api
        if not quiet:
            messagebox.showwarning("Not connected",
                "Go to ⚙ Settings, enter your GitHub token and repo, then click Save & Connect.")
        return None

    def _set_status(self, msg: str):
        self._sbar.config(text=f"  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = PaperPlaneStudio()
    app.mainloop()
