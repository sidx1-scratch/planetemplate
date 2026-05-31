"""
Paper Plane AI — v5  (the best tool ever)
=========================================
Turns any URL / YouTube video / file into a pro-grade illustrated paper airplane
blueprint PDF, with:

  • 17 plane-body skins (camo, flame, galaxy, skull, …)
  • Custom image mapped ONTO the plane body (—image path.png)
  • Standalone CAD-preview render (no LLM / no PDF needed)
  • Measurement annotations, shadow, wing-panel shading
  • Multi-page PDF: cover + step cards + tips/launch guide

Usage
-----
  # full run
  python main.py --web URL --theme flame
  python main.py --youtube VIDEO_ID --theme galaxy --image logo.png
  python main.py --file notes.txt --theme skull

  # CAD preview only (no LLM call, no PDF)
  python main.py --cad-preview --theme rainbow
  python main.py --cad-preview --theme fighter_jet --image logo.png --out-png cad.png

  # list all themes with colour swatches
  python main.py --list-themes
"""

import os, sys, json, math, argparse, textwrap, logging, random, colorsys
from pathlib import Path

import requests as http_req
from bs4 import BeautifulSoup
from groq import Groq
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageChops
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("paperplane")

# ── Groq (only initialised if actually needed) ────────────────────────────────
_groq_client = None
def groq_client():
    global _groq_client
    if _groq_client is None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            sys.exit("ERROR: GROQ_API_KEY env var not set.")
        _groq_client = Groq(api_key=key)
    return _groq_client

FREE_MODELS  = ["llama3-70b-8192","llama3-8b-8192","gemma2-9b-it","mixtral-8x7b-32768"]
DEFAULT_MODEL= FREE_MODELS[0]

# ─────────────────────────────────────────────────────────────────────────────
#  COLOUR / MATH HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def h2r(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2],16) for i in (0,2,4))

def r2h(rgb: tuple) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])

def lerp(a, b, t): return a + (b-a)*t
def lerp_col(c1, c2, t):
    return tuple(int(lerp(a,b,t)) for a,b in zip(c1,c2))

def poly_mask(size, pts):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m

def rotate_pts(pts, cx, cy, deg):
    r = math.radians(deg)
    out=[]
    for x,y in pts:
        dx,dy = x-cx, y-cy
        out.append((cx + dx*math.cos(r)-dy*math.sin(r),
                    cy + dx*math.sin(r)+dy*math.cos(r)))
    return out

# ─────────────────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────────────────
def _tf(n,s):
    try: return ImageFont.truetype(n,s)
    except: return None

def load_fonts():
    B=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
       "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
       "DejaVuSans-Bold.ttf"]
    R=["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
       "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
       "DejaVuSans.ttf"]
    def pick(P,sz):
        for p in P:
            f=_tf(p,sz)
            if f: return f
        return ImageFont.load_default()
    return dict(title=pick(B,34),heading=pick(B,22),body=pick(R,15),
                small=pick(R,12),tiny=pick(R,10),step_num=pick(B,26),mono=pick(R,13))

FONTS = load_fonts()

def wrap_text(text, font, max_w):
    words=text.split(); lines=[]; cur=""
    dd=ImageDraw.Draw(Image.new("RGB",(1,1)))
    for w in words:
        t=(cur+" "+w).strip()
        if dd.textlength(t,font=font)<=max_w: cur=t
        else:
            if cur: lines.append(cur)
            cur=w
    if cur: lines.append(cur)
    return lines

# ─────────────────────────────────────────────────────────────────────────────
#  PLANE GEOMETRY  — richer model with 8 named parts + annotations
# ─────────────────────────────────────────────────────────────────────────────
W, H = 1400, 1800

def plane_parts(cx, cy, s=2.2):
    """
    Returns a dict with:
      polygons  — dict[name -> list of (x,y)]  filled regions
      lines     — dict[name -> ((x0,y0),(x1,y1))]  fold/crease lines
      points    — dict[name -> (x,y)]  special single points
      dims      — list of ((x0,y0),(x1,y1), label)  measurement annotations
    """
    def p(dx,dy): return (cx+dx*s, cy+dy*s)
    def pi(dx,dy): return (int(cx+dx*s), int(cy+dy*s))

    # Main polygon vertices (unfolded plan view)
    nose      = pi(0,  -110)
    tip_r     = pi(220,  55)
    tip_l     = pi(-220, 55)
    tail_join = pi(0,    25)
    tail_r    = pi(80,   95)
    tail_l    = pi(-80,  95)
    tail_tip  = pi(0,   115)
    wingfold_r= pi(5,   -50)
    wingfold_l= pi(-5,  -50)
    center_bot= pi(0,    25)

    polygons = {
        # main body (fuselage strip down the centre)
        "fuselage":  [nose, pi(18,-60), pi(18,25), pi(0,25), pi(-18,25), pi(-18,-60)],
        # right wing (big triangular panel)
        "wing_r":    [wingfold_r, tip_r, tail_join, pi(5,25)],
        # left wing
        "wing_l":    [wingfold_l, tip_l, tail_join, pi(-5,25)],
        # right tail fin
        "tail_r":    [tail_join, tail_r, tail_tip, pi(0,90)],
        # left tail fin
        "tail_l":    [tail_join, tail_l, tail_tip, pi(0,90)],
        # cockpit window (small inset on fuselage)
        "cockpit":   [pi(-8,-85), pi(8,-85), pi(10,-55), pi(-10,-55)],
        # wing inner fold panel (gives 3D lifted effect)
        "inner_r":   [pi(5,-50), pi(40,10), pi(5,25)],
        "inner_l":   [pi(-5,-50), pi(-40,10), pi(-5,25)],
    }
    lines = {
        "crease_centre": (nose,          tail_tip),
        "wing_fold_r":   (wingfold_r,    tip_r),
        "wing_fold_l":   (wingfold_l,    tip_l),
        "tail_fold_r":   (tail_join,     tail_r),
        "tail_fold_l":   (tail_join,     tail_l),
        "inner_crease_r":(pi(5,-50),     pi(5,25)),
        "inner_crease_l":(pi(-5,-50),    pi(-5,25)),
    }
    points = {
        "nose":      nose,
        "tail_tip":  tail_tip,
        "wing_r_tip":tip_r,
        "wing_l_tip":tip_l,
    }
    # measurement dimension lines
    wingspan    = abs(tip_r[0]-tip_l[0])
    body_height = abs(nose[1]-tail_tip[1])
    dims = [
        (tip_l, tip_r,   f"wingspan  {wingspan:.0f}u"),
        (nose,  tail_tip,f"length    {body_height:.0f}u"),
    ]
    return dict(polygons=polygons, lines=lines, points=points, dims=dims)

# ─────────────────────────────────────────────────────────────────────────────
#  SKIN SYSTEM — 17 built-in skins + custom image skin
# ─────────────────────────────────────────────────────────────────────────────
TEX = 900

def _tex(): return Image.new("RGBA",(TEX,TEX),(255,255,255,255))

def skin_default(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(248,246,240,255))
    for i in range(0,TEX*2,20):
        c=228+rng.randint(-5,5)
        d.line([(i,0),(0,i)],fill=(c,c,c+6,255),width=1)
    return tex

def skin_fighter_jet(tex,rng):
    d=ImageDraw.Draw(tex)
    panels=[((0,0,TEX,TEX//2),(160,170,185)),((0,TEX//2,TEX,TEX),(140,150,165)),
            ((0,0,TEX//2,TEX//2),(155,165,180)),((TEX//2,0,TEX,TEX//2),(170,178,192))]
    for rect,col in panels: d.rectangle(rect,fill=col+(255,))
    for x in [TEX//3,TEX*2//3]: d.line([(x,0),(x,TEX)],fill=(100,110,125,200),width=3)
    for y in [TEX//4,TEX//2,TEX*3//4]: d.line([(0,y),(TEX,y)],fill=(100,110,125,200),width=2)
    for _ in range(100):
        rx,ry=rng.randint(10,TEX-10),rng.randint(10,TEX-10)
        d.ellipse([rx-3,ry-3,rx+3,ry+3],fill=(90,100,115,255),outline=(200,205,215,255))
    d.rectangle([0,TEX//2-8,TEX,TEX//2+8],fill=(255,107,53,255))
    d.rectangle([TEX//3,0,TEX*2//3,TEX//7],fill=(80,130,180,180))
    return tex

def skin_military(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(90,105,70,255))
    for col,n,mr in [((120,90,60),65,42),((60,75,45),60,38),((160,140,90),48,32),
                     ((50,65,40),70,45),((140,120,75),42,28)]:
        for _ in range(n):
            bx,by=rng.randint(0,TEX),rng.randint(0,TEX)
            rx,ry=rng.randint(mr//2,mr),rng.randint(mr//2,mr)
            d.ellipse([bx-rx,by-ry,bx+rx,by+ry],fill=col+(255,))
    for _ in range(400):
        px,py=rng.randint(0,TEX-1),rng.randint(0,TEX-1)
        v=rng.randint(-18,18); c=max(0,min(255,80+v))
        d.point((px,py),fill=(c,c+5,c-10,255))
    return tex

def skin_flame(tex,rng):
    d=ImageDraw.Draw(tex)
    for y in range(TEX):
        t=1-(y/TEX)
        d.line([(0,y),(TEX,y)],fill=(int(30+200*t),int(5+80*t),0,255))
    for _ in range(70):
        fx,fy=rng.randint(0,TEX),rng.randint(TEX//3,TEX)
        fw,fh=rng.randint(8,45),rng.randint(60,220)
        br=rng.random()
        col=(255,220,50,220) if br>0.7 else ((255,120,10,200) if br>0.4 else (200,30,0,180))
        d.polygon([(fx,fy),(fx-fw,fy+fh//3),(fx+rng.randint(-fw//2,fw//2),fy+fh)],fill=col)
    for _ in range(20):
        sx=rng.randint(TEX//4,TEX*3//4)
        d.line([(sx,TEX),(sx+rng.randint(-25,25),0)],fill=(255,255,200,110),width=rng.randint(1,4))
    return tex

def skin_ocean(tex,rng):
    d=ImageDraw.Draw(tex)
    for y in range(TEX):
        t=y/TEX
        d.line([(0,y),(TEX,y)],fill=(int(5+30*t),int(80+60*(1-t)),int(180+50*(1-t)),255))
    for _ in range(30):
        yb,amp,freq,ph=rng.randint(0,TEX),rng.randint(10,45),rng.uniform(.005,.022),rng.uniform(0,math.pi*2)
        pts=[(x,yb+int(amp*math.sin(freq*x+ph))) for x in range(0,TEX+1,4)]
        if len(pts)>1: d.line(pts,fill=(255,255,255,rng.randint(50,150)),width=rng.randint(1,3))
    for _ in range(150):
        fx,fy=rng.randint(0,TEX),rng.randint(0,TEX)
        fr=rng.randint(2,9)
        d.ellipse([fx-fr,fy-fr,fx+fr,fy+fr],fill=(220,240,255,130))
    return tex

def skin_jungle(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(34,60,20,255))
    cols=[(20,80,25),(45,100,30),(15,55,15),(60,120,40),(25,70,20)]
    for _ in range(140):
        lx,ly=rng.randint(0,TEX),rng.randint(0,TEX)
        lw,lh=rng.randint(12,65),rng.randint(25,110)
        angle=rng.uniform(0,math.pi); col=rng.choice(cols)+(255,)
        ex=int(lw*math.cos(angle)); ey=int(lw*math.sin(angle))
        d.polygon([(lx,ly-lh//2),(lx+ex,ly),(lx,ly+lh//2),(lx-ex,ly)],fill=col)
        d.line([(lx,ly-lh//2),(lx,ly+lh//2)],fill=(10,40,10,200),width=1)
    for _ in range(35):
        bx,by=rng.randint(0,TEX),rng.randint(0,TEX)
        r=rng.randint(20,75)
        d.ellipse([bx-r,by-r,bx+r,by+r],fill=(10,35,10,200))
    return tex

def skin_arctic(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(230,240,255,255))
    for _ in range(60):
        bx,by=rng.randint(0,TEX),rng.randint(0,TEX)
        rx,ry=rng.randint(18,90),rng.randint(12,65)
        col=rng.choice([(200,220,240),(180,205,230),(210,225,245)])
        d.ellipse([bx-rx,by-ry,bx+rx,by+ry],fill=col+(rng.randint(35,115),))
    for _ in range(35):
        cx2,cy2=rng.randint(0,TEX),rng.randint(0,TEX)
        length=rng.randint(14,55)
        for arm in range(6):
            ang=math.radians(60*arm)
            d.line([(cx2,cy2),(cx2+int(length*math.cos(ang)),cy2+int(length*math.sin(ang)))],
                   fill=(200,215,235,180),width=2)
    for _ in range(250):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        d.ellipse([sx-2,sy-2,sx+2,sy+2],fill=(255,255,255,220))
    return tex

def skin_galaxy(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(5,3,20,255))
    for col,n in [((80,20,120),90),((20,50,140),80),((140,30,80),70),
                  ((30,80,100),85),((100,10,60),75)]:
        for _ in range(n):
            nx,ny=rng.randint(0,TEX),rng.randint(0,TEX)
            nr=rng.randint(18,110)
            d.ellipse([nx-nr,ny-nr,nx+nr,ny+nr],fill=col+(rng.randint(15,85),))
    for _ in range(400):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        sr=rng.randint(1,4); bright=rng.randint(180,255)
        d.ellipse([sx-sr,sy-sr,sx+sr,sy+sr],fill=(bright,bright,bright,255))
    for _ in range(25):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        d.line([(sx,sy),(sx+rng.randint(-35,35),sy+rng.randint(-6,6))],
               fill=(255,255,200,110),width=1)
    return tex

def skin_bumblebee(tex,rng):
    d=ImageDraw.Draw(tex)
    sw=TEX//8
    for i in range(16):
        d.rectangle([i*sw,0,(i+1)*sw,TEX],fill=((255,215,0,255) if i%2==0 else (20,20,20,255)))
    d.polygon([(0,TEX//3),(TEX,0),(TEX,TEX//4),(0,TEX//2)],fill=(0,0,0,55))
    d.rectangle([0,TEX*7//8,TEX,TEX],fill=(20,20,20,255))
    return tex

def skin_patriot(tex,rng):
    d=ImageDraw.Draw(tex)
    sh=TEX//13
    for i in range(13):
        d.rectangle([0,i*sh,TEX,(i+1)*sh],fill=((180,20,20,255) if i%2==0 else (240,240,240,255)))
    cw,ch=TEX*2//5,sh*7
    d.rectangle([0,0,cw,ch],fill=(30,50,140,255))
    xg,yg=cw//6,ch//10
    for r in range(9):
        off=(xg//2) if r%2==1 else 0
        for c in range(5 if r%2==0 else 6):
            _star(d,off+xg//2+c*xg,yg//2+r*yg,8,5,(255,255,255,255))
    return tex

def _star(d,cx,cy,r,pts,col):
    pp=[]
    for i in range(pts*2):
        ang=math.radians(i*180/pts-90)
        rad=r if i%2==0 else r*0.4
        pp.append((cx+rad*math.cos(ang),cy+rad*math.sin(ang)))
    d.polygon(pp,fill=col)

def skin_sakura(tex,rng):
    d=ImageDraw.Draw(tex)
    for y in range(TEX):
        t=y/TEX
        d.line([(0,y),(TEX,y)],fill=(int(255-20*t),int(220-40*t),int(225-20*t),255))
    for _ in range(90):
        px,py=rng.randint(0,TEX),rng.randint(0,TEX)
        pr=rng.randint(8,32)
        col=rng.choice([(255,182,193),(255,160,170),(250,200,210),(255,192,203)])
        for petal in range(5):
            ang=math.radians(72*petal)
            ex,ey=px+int(pr*1.5*math.cos(ang)),py+int(pr*1.5*math.sin(ang))
            d.ellipse([ex-pr//2,ey-pr//2,ex+pr//2,ey+pr//2],
                      fill=col+(rng.randint(120,220),))
        d.ellipse([px-4,py-4,px+4,py+4],fill=(255,240,240,255))
    for _ in range(70):
        px,py=rng.randint(0,TEX),rng.randint(0,TEX)
        d.ellipse([px-6,py-3,px+6,py+3],fill=(255,170,180,rng.randint(80,160)))
    return tex

def skin_lightning(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(15,10,40,255))
    for _ in range(6):
        gx,gy=rng.randint(0,TEX),rng.randint(0,TEX)
        gr=rng.randint(60,160)
        d.ellipse([gx-gr,gy-gr,gx+gr,gy+gr],fill=(80,60,200,rng.randint(12,38)))
    for _ in range(14):
        x=rng.randint(50,TEX-50); y=0
        col=rng.choice([(255,255,100),(200,220,255),(255,200,50)])
        pts=[(x,y)]
        while y<TEX:
            x=max(5,min(TEX-5,x+rng.randint(-65,65))); y+=rng.randint(40,110)
            pts.append((x,min(y,TEX)))
        w=rng.randint(1,4)
        if len(pts)>1:
            d.line(pts,fill=col+(220,),width=w)
            d.line(pts,fill=col+(55,),width=w+7)
    for _ in range(70):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        d.ellipse([sx-3,sy-3,sx+3,sy+3],fill=(255,255,180,200))
    return tex

def skin_racing(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(220,220,220,255))
    d.rectangle([0,TEX//4,TEX,TEX*3//8],fill=(200,20,20,255))
    d.rectangle([0,TEX//8,TEX,TEX//5],fill=(20,20,200,255))
    sq=28
    for row in range(TEX//sq):
        for col in range(TEX//sq):
            if (row+col)%2==0:
                d.rectangle([col*sq,row*sq,(col+1)*sq,(row+1)*sq],fill=(10,10,10,200))
    d.rectangle([0,0,TEX,TEX//16],fill=(220,220,220,255))
    d.rectangle([0,TEX-TEX//16,TEX,TEX],fill=(220,220,220,255))
    for _ in range(6):
        rx,ry=rng.randint(0,TEX-110),rng.randint(TEX//2,TEX-40)
        rw,rh=rng.randint(60,120),rng.randint(20,36)
        col=rng.choice([(255,200,0),(0,200,50),(200,0,50),(0,100,220)])
        d.rectangle([rx,ry,rx+rw,ry+rh],fill=col+(220,))
    return tex

def skin_graffiti(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(30,30,30,255))
    cols=[(255,50,50),(50,255,50),(50,50,255),(255,255,50),
          (255,50,255),(50,255,255),(255,150,50),(150,50,255)]
    for _ in range(35):
        gx,gy=rng.randint(0,TEX),rng.randint(0,TEX)
        gr=rng.randint(30,130)
        d.ellipse([gx-gr,gy-gr,gx+gr,gy+gr],fill=rng.choice(cols)+(rng.randint(90,200),))
    for _ in range(45):
        dx,dy=rng.randint(0,TEX),rng.randint(0,TEX//2)
        dl,dw=rng.randint(30,160),rng.randint(4,15)
        col=rng.choice(cols)+(200,)
        d.rectangle([dx-dw//2,dy,dx+dw//2,dy+dl],fill=col)
        d.ellipse([dx-dw//2,dy+dl,dx+dw//2,dy+dl+dw],fill=col)
    for _ in range(10):
        ox,oy=rng.randint(0,TEX),rng.randint(0,TEX)
        ow,oh=rng.randint(40,110),rng.randint(20,65)
        d.rectangle([ox,oy,ox+ow,oy+oh],outline=rng.choice(cols)+(255,),width=5)
    return tex

def skin_skull(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(18,16,18,255))
    for _ in range(250):
        px,py=rng.randint(0,TEX),rng.randint(0,TEX)
        v=rng.randint(25,48)
        d.point((px,py),fill=(v,v,v,255))
    for _ in range(9):
        sx,sy=rng.randint(40,TEX-40),rng.randint(40,TEX-80)
        sr=rng.randint(22,44); alpha=rng.randint(120,220)
        col=(180,175,180,alpha)
        d.ellipse([sx-sr,sy-sr,sx+sr,sy+int(sr*0.8)],fill=col)
        jw,jh=int(sr*0.7),int(sr*0.5)
        d.rectangle([sx-jw,sy+int(sr*0.4),sx+jw,sy+int(sr*0.9)],fill=col)
        ew=max(4,sr//3)
        d.ellipse([sx-sr//2-ew,sy-ew,sx-sr//2+ew,sy+ew],fill=(18,16,18,255))
        d.ellipse([sx+sr//2-ew,sy-ew,sx+sr//2+ew,sy+ew],fill=(18,16,18,255))
        d.polygon([(sx,sy+sr//6),(sx-sr//5,sy+sr//2),(sx+sr//5,sy+sr//2)],fill=(18,16,18,255))
        for t in range(4):
            tx=sx-jw+jw//2*t; ty=sy+int(sr*0.6)
            d.rectangle([tx,ty,tx+jw//4-2,ty+int(sr*0.25)],fill=(18,16,18,255))
    for _ in range(5):
        bx,by=rng.randint(0,TEX),rng.randint(0,TEX)
        bl=rng.randint(40,90)
        d.line([(bx-bl,by-bl),(bx+bl,by+bl)],fill=(120,115,120,100),width=5)
        d.line([(bx+bl,by-bl),(bx-bl,by+bl)],fill=(120,115,120,100),width=5)
    return tex

def skin_rust(tex,rng):
    d=ImageDraw.Draw(tex)
    d.rectangle([0,0,TEX,TEX],fill=(110,70,40,255))
    for _ in range(50):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        ex=sx+rng.randint(-90,90); ey=sy+rng.randint(-6,6)
        d.line([(sx,sy),(ex,ey)],fill=(80,55,30,180),width=rng.randint(1,3))
    for _ in range(80):
        rx,ry=rng.randint(0,TEX),rng.randint(0,TEX)
        rr=rng.randint(8,65)
        col=rng.choice([(180,70,20),(200,90,30),(150,55,15),(190,80,25),(220,100,40)])
        d.ellipse([rx-rr,ry-rr,rx+rr,ry+rr],fill=col+(rng.randint(140,230),))
    for _ in range(25):
        px,py=rng.randint(0,TEX),rng.randint(0,TEX)
        pw,ph=rng.randint(14,55),rng.randint(9,32)
        d.ellipse([px,py,px+pw,py+ph],fill=(160,155,145,rng.randint(90,180)))
    for _ in range(60):
        px,py=rng.randint(0,TEX),rng.randint(0,TEX)
        pr=rng.randint(3,11)
        d.ellipse([px-pr,py-pr,px+pr,py+pr],fill=(40,25,15,200))
    return tex

def skin_rainbow(tex,rng):
    d=ImageDraw.Draw(tex)
    bands=[(255,0,0),(255,127,0),(255,255,0),(0,200,0),(0,100,255),(75,0,130),(148,0,211)]
    bw=TEX//len(bands)
    for i,col in enumerate(bands): d.rectangle([i*bw,0,(i+1)*bw,TEX],fill=col+(255,))
    for i in range(len(bands)-1):
        c1,c2=bands[i],bands[i+1]; bx=(i+1)*bw
        for off in range(-22,22):
            t=(off+22)/44
            bc=lerp_col(c1,c2,t)
            d.line([(bx+off,0),(bx+off,TEX)],fill=bc+(190,),width=1)
    for _ in range(180):
        sx,sy=rng.randint(0,TEX),rng.randint(0,TEX)
        d.ellipse([sx-3,sy-3,sx+3,sy+3],fill=(255,255,255,110))
    return tex

def skin_custom_image(path: str) -> Image.Image:
    """Load a user image, resize to TEX, return as RGBA skin texture."""
    raw = Image.open(path).convert("RGBA")
    # centre-crop to square
    w,h = raw.size
    side = min(w,h)
    left=(w-side)//2; top=(h-side)//2
    raw = raw.crop((left,top,left+side,top+side))
    raw = raw.resize((TEX,TEX), Image.LANCZOS)
    # slight desaturate so fold lines stay visible
    rgb = ImageEnhance.Color(raw.convert("RGB")).enhance(0.80)
    out = rgb.convert("RGBA")
    out.putalpha(raw.split()[3] if raw.mode=="RGBA" else Image.new("L",(TEX,TEX),230))
    return out

SKINS = {
    "default":skin_default,"fighter_jet":skin_fighter_jet,"military":skin_military,
    "flame":skin_flame,"ocean":skin_ocean,"jungle":skin_jungle,"arctic":skin_arctic,
    "galaxy":skin_galaxy,"bumblebee":skin_bumblebee,"patriot":skin_patriot,
    "sakura":skin_sakura,"lightning":skin_lightning,"racing":skin_racing,
    "graffiti":skin_graffiti,"skull":skin_skull,"rust":skin_rust,"rainbow":skin_rainbow,
}
SKIN_EMOJI={"default":"✈","fighter_jet":"🛩","military":"🪖","flame":"🔥","ocean":"🌊",
            "jungle":"🌿","arctic":"❄","galaxy":"🌌","bumblebee":"🐝","patriot":"🇺🇸",
            "sakura":"🌸","lightning":"⚡","racing":"🏎","graffiti":"🎨",
            "skull":"💀","rust":"🦾","rainbow":"🌈"}
SKIN_DESC={"default":"Clean engineering paper","fighter_jet":"Grey metal panels & rivets",
           "military":"Green/brown camo blotches","flame":"Orange-red fire streaks",
           "ocean":"Blue depth gradient & waves","jungle":"Dense green leaf camo",
           "arctic":"Ice patches & snowflakes","galaxy":"Deep space nebula & stars",
           "bumblebee":"Bold yellow-black stripes","patriot":"Stars and stripes",
           "sakura":"Pink cherry blossom petals","lightning":"Electric yellow bolts",
           "racing":"Checkered flag & sponsor panels","graffiti":"Spray-paint drips & tags",
           "skull":"Dark with skull motifs","rust":"Aged metal rust patches",
           "rainbow":"Full spectrum colour bands"}

# ── page palettes ─────────────────────────────────────────────────────────────
def _pal(bg,panel,accent,fv,fm,grid,td,tl,tm,arrow,card,tip,warn):
    return dict(bg=bg,panel=panel,accent=accent,fold_val=fv,fold_mnt=fm,
                grid=grid,td=td,tl=tl,tm=tm,arrow=arrow,card=card,tip=tip,warn=warn)

PAGE_PAL = {
"default":   _pal("#F8F6F0","#1A1A2E","#E94560","#2563EB","#DC2626","#D1D5DB","#111827","#F9FAFB","#6B7280","#059669","#FFFFFF","#F0FDF4","#FFF7ED"),
"fighter_jet":_pal("#0D1117","#161B22","#FF6B35","#00D4FF","#FF3B3B","#21262D","#E6EDF3","#F0F6FC","#8B949E","#3FB950","#161B22","#0D2818","#2D1B00"),
"military":  _pal("#1C200F","#0F1208","#8BC34A","#CDDC39","#FF7043","#2E3318","#C5D86D","#F0F0E0","#6D7A3A","#8BC34A","#1C200F","#1A2208","#221408"),
"flame":     _pal("#120500","#1C0800","#FF6B00","#FF9500","#FF2200","#2A0C00","#FFD580","#FFEECC","#AA4400","#FFD700","#1C0800","#1A0D00","#200800"),
"ocean":     _pal("#020F1C","#031828","#00B4D8","#48CAE4","#0077B6","#0A2540","#CAF0F8","#E0F7FF","#4A8FA8","#00D4AA","#031828","#021520","#0A0A20"),
"jungle":    _pal("#0A1208","#060D04","#6DBF67","#A8D5A2","#F4A261","#162010","#B8D4A8","#E8F5E2","#5A7A4A","#90EE90","#0F180A","#0A1808","#1A1205"),
"arctic":    _pal("#E8F4FF","#1A3050","#4FC3F7","#0288D1","#E53935","#B8D8F0","#0D2540","#FFFFFF","#5088A8","#00ACC1","#FFFFFF","#E0F7FF","#FFF3E0"),
"galaxy":    _pal("#050310","#0A0820","#A855F7","#60A5FA","#F472B6","#1A1535","#E2E8F0","#F8FAFC","#6366F1","#34D399","#0D0B22","#080520","#180820"),
"bumblebee": _pal("#1A1A00","#111100","#FFD700","#FFD700","#FF4400","#2A2A00","#FFEE44","#FFFF99","#AA8800","#FFD700","#222200","#1A1A00","#220A00"),
"patriot":   _pal("#F0F0FF","#1A2A6C","#B21F35","#1A2A6C","#B21F35","#C8C8E8","#0D1A40","#FFFFFF","#5060A0","#B21F35","#FFFFFF","#EEF2FF","#FFF0F0"),
"sakura":    _pal("#FFF0F5","#6B2D3E","#FF85A1","#FF6B9D","#C0392B","#FFD6E0","#4A1020","#FFF0F5","#A06070","#FF85A1","#FFFFFF","#FFF5F8","#FFF0EC"),
"lightning": _pal("#0A0820","#080618","#FFE600","#A0A0FF","#FF4444","#181030","#E0E0FF","#FFFFFF","#6060AA","#FFE600","#0D0A28","#080520","#180808"),
"racing":    _pal("#F0F0F0","#1A1A1A","#CC0000","#0000CC","#CC0000","#DDDDDD","#111111","#FFFFFF","#555555","#00AA00","#FFFFFF","#F0FFF0","#FFF0F0"),
"graffiti":  _pal("#1A1A1A","#111111","#FF3366","#00FF88","#FF3366","#2A2A2A","#FFFFFF","#FFFFFF","#888888","#00FF88","#222222","#102010","#201010"),
"skull":     _pal("#0F0D0F","#080608","#AAAAAA","#888888","#CC2222","#1A181A","#CCCCCC","#EEEEEE","#666666","#AAAAAA","#141214","#101010","#180808"),
"rust":      _pal("#1C1008","#120A04","#D2691E","#CD853F","#8B0000","#2C1A0A","#E8C090","#F5DEB3","#8B6040","#D2691E","#1C1008","#141008","#180808"),
"rainbow":   _pal("#FFFFFF","#222222","#FF00AA","#0066FF","#FF3300","#EEEEEE","#111111","#FFFFFF","#888888","#00CC44","#FFFFFF","#F8FFF8","#FFF8F8"),
}

def get_pal(theme): return PAGE_PAL.get(theme, PAGE_PAL["default"])

# ─────────────────────────────────────────────────────────────────────────────
#  SKIN TEXTURE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_skin(theme: str, image_path: str|None=None, seed: int=42) -> Image.Image:
    if image_path:
        log.info("Building custom-image skin from: %s", image_path)
        return skin_custom_image(image_path)
    log.info("Building skin: %s", theme)
    rng = random.Random(seed)
    tex = _tex()
    return SKINS.get(theme, skin_default)(tex, rng)

# ─────────────────────────────────────────────────────────────────────────────
#  PLANE RENDERER  (shared by CAD preview AND blueprint pages)
# ─────────────────────────────────────────────────────────────────────────────

def render_plane(base: Image.Image, skin: Image.Image,
                 cx: int, cy: int, s: float,
                 P: dict, theme: str,
                 show_dims: bool=False,
                 show_shadow: bool=True,
                 grid_overlay: bool=False) -> Image.Image:
    """
    Draw the full skinned plane onto `base` at position (cx,cy) scale s.
    Returns base (modified in place).
    """
    geo   = plane_parts(cx, cy, s)
    polys = geo["polygons"]
    lines = geo["lines"]
    pts   = geo["points"]
    dims  = geo["dims"]

    cw,ch = base.size

    # ── shadow ────────────────────────────────────────────────────────────
    if show_shadow:
        shadow = Image.new("RGBA",(cw,ch),(0,0,0,0))
        sd     = ImageDraw.Draw(shadow)
        offset = int(s*8)
        for name,poly in polys.items():
            if name=="cockpit": continue
            shifted = [(x+offset,y+offset) for x,y in poly]
            sd.polygon(shifted, fill=(0,0,0,55))
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(s*6)))
        base   = Image.alpha_composite(base.convert("RGBA"), shadow).convert("RGB")

    # ── skin texture pasted per polygon ───────────────────────────────────
    skin_scaled = skin.resize((cw,ch), Image.LANCZOS).convert("RGBA")

    # shading layers per part
    shade_map = {
        "fuselage":  0.00,   # no shade — main highlight
        "wing_r":    0.12,
        "wing_l":    0.12,
        "tail_r":    0.22,
        "tail_l":    0.22,
        "inner_r":   0.35,   # inner folds darkest
        "inner_l":   0.35,
        "cockpit":   None,   # drawn separately
    }

    for name,poly in polys.items():
        if name=="cockpit": continue
        mask = poly_mask((cw,ch), poly)
        base.paste(skin_scaled.convert("RGB"), (0,0), mask)
        # apply shade on top
        shade = shade_map.get(name, 0.0)
        if shade>0:
            dark = Image.new("RGB",(cw,ch),(0,0,0))
            shade_mask = Image.new("L",(cw,ch),0)
            ImageDraw.Draw(shade_mask).polygon(poly, fill=int(shade*255))
            base.paste(dark,(0,0),shade_mask)

    d = ImageDraw.Draw(base)

    # ── polygon outlines ──────────────────────────────────────────────────
    dark_themes = {"galaxy","skull","flame","lightning","graffiti","military",
                   "fighter_jet","ocean","jungle","bumblebee","rust","racing"}
    outline_col = h2r(P["tl"]) if theme in dark_themes else h2r(P["td"])

    for name,poly in polys.items():
        if name=="cockpit": continue
        d.polygon(poly, outline=outline_col+(255,), fill=None)

    # ── cockpit window ─────────────────────────────────────────────────────
    cockpit_col = (80,140,200,200) if theme not in ("skull","graffiti") else (20,20,20,200)
    d.polygon(polys["cockpit"], fill=cockpit_col)
    d.polygon(polys["cockpit"], outline=outline_col+(200,))
    # cockpit glare
    cp = polys["cockpit"]
    d.line([cp[0], ((cp[0][0]+cp[1][0])//2, (cp[0][1]+cp[1][1])//2)],
           fill=(255,255,255,120),width=2)

    # ── fold lines ────────────────────────────────────────────────────────
    fold_types = {
        "crease_centre": "valley",
        "wing_fold_r":   "mountain",
        "wing_fold_l":   "mountain",
        "tail_fold_r":   "valley",
        "tail_fold_l":   "valley",
        "inner_crease_r":"valley",
        "inner_crease_l":"valley",
    }
    for lname,(p0,p1) in lines.items():
        ft = fold_types.get(lname,"none")
        _draw_fold_line(d,p0,p1,ft,P,width=max(2,int(s*1.2)))

    # ── nose dot ──────────────────────────────────────────────────────────
    nose = pts["nose"]
    nr   = int(s*5)
    d.ellipse([nose[0]-nr,nose[1]-nr,nose[0]+nr,nose[1]+nr],
              fill=h2r(P["accent"])+(255,))

    # ── theme decals ──────────────────────────────────────────────────────
    _draw_decals(d, geo, theme, P, s)

    # ── dimension annotations ─────────────────────────────────────────────
    if show_dims:
        _draw_dimensions(d, dims, P, s)

    # ── engineering grid overlay ──────────────────────────────────────────
    if grid_overlay:
        _draw_grid_overlay(d, cw, ch, P, s)

    return base

def _draw_fold_line(d, p0, p1, fold_type, P, width=3):
    vc=h2r(P["fold_val"]); mc=h2r(P["fold_mnt"]); mu=h2r(P["tm"])
    x0,y0=p0; x1,y1=p1
    segs=24
    if fold_type=="valley":
        for i in range(segs):
            if i%2==0:
                t0=i/segs; t1=(i+1)/segs
                d.line([(x0+int((x1-x0)*t0),y0+int((y1-y0)*t0)),
                        (x0+int((x1-x0)*t1),y0+int((y1-y0)*t1))],
                       fill=vc+(200,),width=width)
    elif fold_type=="mountain":
        steps=32
        for i in range(steps):
            if i%2==0:
                px=int(x0+(x1-x0)*i/steps); py=int(y0+(y1-y0)*i/steps)
                r=max(2,width)
                d.ellipse([px-r,py-r,px+r,py+r],fill=mc+(200,))
    else:
        d.line([(x0,y0),(x1,y1)],fill=mu+(150,),width=max(1,width-1))

def _draw_dimensions(d, dims, P, s):
    dim_col = h2r(P["accent"])
    tick    = int(s*8)
    for (x0,y0),(x1,y1),label in dims:
        # draw dimension arrow line
        d.line([(x0,y0),(x1,y1)],fill=dim_col+(180,),width=1)
        # ticks
        # perpendicular direction
        dx,dy=x1-x0,y1-y0; length=math.hypot(dx,dy)
        if length==0: continue
        nx,ny=-dy/length,dx/length
        for px,py in [(x0,y0),(x1,y1)]:
            d.line([(px+nx*tick,py+ny*tick),(px-nx*tick,py-ny*tick)],
                   fill=dim_col+(200,),width=2)
        # label at midpoint
        mx,my=(x0+x1)//2,(y0+y1)//2
        d.text((mx+int(nx*tick*1.4)+4,my+int(ny*tick*1.4)),
               label,font=FONTS["tiny"],fill=dim_col+(220,))

def _draw_grid_overlay(d, cw, ch, P, s):
    gc=h2r(P["grid"])+(25,)
    step=int(s*20)
    for x in range(0,cw,step): d.line([(x,0),(x,ch)],fill=gc,width=1)
    for y in range(0,ch,step): d.line([(0,y),(cw,y)],fill=gc,width=1)

def _draw_decals(d, geo, theme, P, s):
    polys=geo["polygons"]
    nose=geo["points"]["nose"]
    fuselage=polys["fuselage"]
    cx_fus=sum(p[0] for p in fuselage)//len(fuselage)
    cy_fus=sum(p[1] for p in fuselage)//len(fuselage)
    ac=h2r(P["accent"])

    if theme=="fighter_jet":
        for r,col in [(int(s*14),ac),(int(s*9),(255,255,255)),(int(s*5),ac)]:
            d.ellipse([cx_fus-r,cy_fus-r,cx_fus+r,cy_fus+r],fill=col+(220,))
        d.rectangle([polys["wing_l"][1][0]+10,polys["wing_l"][1][1]-6,
                     polys["wing_r"][1][0]-10,polys["wing_r"][1][1]+6],
                    fill=ac+(120,))
    elif theme=="military":
        _star(d,cx_fus,cy_fus,int(s*14),5,(255,255,255,220))
        _star(d,cx_fus,cy_fus,int(s*12),5,ac+(220,))
    elif theme=="patriot":
        for wp in [geo["points"]["wing_r_tip"],geo["points"]["wing_l_tip"]]:
            mx=(cx_fus+wp[0])//2; my=(cy_fus+wp[1])//2
            _star(d,mx,my,int(s*10),5,(255,255,255,200))
    elif theme=="racing":
        d.text((cx_fus-int(s*12),cy_fus-int(s*10)),"07",
               font=FONTS["heading"],fill=ac+(220,))
    elif theme=="skull":
        _star(d,cx_fus,cy_fus,int(s*10),6,ac+(150,))
    elif theme=="bumblebee":
        nr=int(s*7)
        d.ellipse([nose[0]-nr,nose[1]-nr//2,nose[0]+nr,nose[1]+nr*2],
                  fill=(20,20,20,200))

# ─────────────────────────────────────────────────────────────────────────────
#  CAD PREVIEW PAGE  — standalone engineering-style view of the plane
# ─────────────────────────────────────────────────────────────────────────────

CAD_W, CAD_H = 1600, 1200

def make_cad_preview(theme: str, skin: Image.Image, title: str="CAD Preview") -> Image.Image:
    """
    Produce a CAD-style engineering view with:
      - Top/front/side orthographic-style panels
      - Title block (bottom right)
      - Full measurement annotations
      - Grid overlay
      - Fold legend
    """
    P   = get_pal(theme)
    img = Image.new("RGB",(CAD_W,CAD_H),h2r(P["bg"]))
    d   = ImageDraw.Draw(img)

    # ── engineering grid ──────────────────────────────────────────────────
    grid_c = h2r(P["grid"])
    minor  = 40; major = 200
    for x in range(0,CAD_W,minor):
        w = 2 if x%major==0 else 1
        d.line([(x,0),(x,CAD_H)],fill=grid_c+(40,),width=w)
    for y in range(0,CAD_H,minor):
        w = 2 if y%major==0 else 1
        d.line([(0,y),(CAD_W,y)],fill=grid_c+(40,),width=w)

    # ── 3-panel layout: top-plan (large), front view, side view ──────────
    # Top plan view (main, left 2/3)
    img = render_plane(img, skin,
                       cx=CAD_W//3, cy=CAD_H//2,
                       s=2.6, P=P, theme=theme,
                       show_dims=True, show_shadow=True, grid_overlay=False)
    d   = ImageDraw.Draw(img)

    # panel dividers
    div_col = h2r(P["td"])+(80,)
    d.line([(CAD_W*2//3,0),(CAD_W*2//3,CAD_H)],fill=div_col,width=2)
    d.line([(CAD_W*2//3,CAD_H//2),(CAD_W,CAD_H//2)],fill=div_col,width=2)

    # Front view (top-right panel) — simplified face-on silhouette
    _draw_front_view(d, cx=CAD_W*5//6, cy=CAD_H//4, s=1.1, P=P, theme=theme)
    # Side view (bottom-right panel) — simplified side profile
    _draw_side_view(d, cx=CAD_W*5//6, cy=CAD_H*3//4, s=1.1, P=P, theme=theme)

    # panel labels
    for label,x,y in [("TOP PLAN VIEW", 30, 12),
                       ("FRONT VIEW",  CAD_W*2//3+12, 12),
                       ("SIDE VIEW",   CAD_W*2//3+12, CAD_H//2+12)]:
        d.text((x,y),label,font=FONTS["small"],fill=h2r(P["tm"]))

    # ── title block (bottom-right corner) ────────────────────────────────
    _draw_title_block(d, CAD_W, CAD_H, theme, title, P)

    # ── fold legend (bottom-left) ─────────────────────────────────────────
    _draw_cad_legend(d, P)

    # ── border ────────────────────────────────────────────────────────────
    d.rectangle([4,4,CAD_W-4,CAD_H-4],outline=h2r(P["accent"])+(220,),width=4)
    d.rectangle([12,12,CAD_W-12,CAD_H-12],outline=h2r(P["td"])+(60,),width=1)

    return img


def _draw_front_view(d, cx, cy, s, P, theme):
    """Simplified front-on view — shows wing dihedral."""
    ac=h2r(P["accent"]); tc=h2r(P["td"])
    # fuselage (vertical line)
    d.line([(cx,cy-int(80*s)),(cx,cy+int(40*s))],fill=tc+(200,),width=int(s*6))
    # left wing (angled down = dihedral)
    d.line([(cx,cy-int(10*s)),(cx-int(180*s),cy+int(20*s))],
           fill=tc+(200,),width=int(s*3))
    # right wing
    d.line([(cx,cy-int(10*s)),(cx+int(180*s),cy+int(20*s))],
           fill=tc+(200,),width=int(s*3))
    # tail
    d.line([(cx-int(60*s),cy+int(40*s)),(cx+int(60*s),cy+int(40*s))],
           fill=tc+(180,),width=int(s*2))
    # nose dot
    d.ellipse([cx-int(s*5),cy-int(80*s)-int(s*5),cx+int(s*5),cy-int(80*s)+int(s*5)],
              fill=ac+(255,))

def _draw_side_view(d, cx, cy, s, P, theme):
    """Simplified side profile view — shows depth."""
    ac=h2r(P["accent"]); tc=h2r(P["td"])
    # profile shape
    pts=[(cx-int(10*s),cy-int(80*s)),(cx+int(40*s),cy+int(20*s)),
         (cx+int(40*s),cy+int(40*s)),(cx-int(10*s),cy+int(40*s))]
    d.polygon(pts,fill=h2r(P["card"])+(180,),outline=tc+(200,))
    # fold crease lines
    d.line([(cx-int(10*s),cy-int(80*s)),(cx-int(10*s),cy+int(40*s))],
           fill=h2r(P["fold_val"])+(180,),width=2)
    d.ellipse([cx-int(10*s)-int(s*4),cy-int(80*s)-int(s*4),
               cx-int(10*s)+int(s*4),cy-int(80*s)+int(s*4)],fill=ac+(255,))

def _draw_title_block(d, cw, ch, theme, title, P):
    bw,bh=360,180
    x0,y0=cw-bw-8,ch-bh-8
    d.rectangle([x0,y0,x0+bw,y0+bh],fill=h2r(P["panel"]),outline=h2r(P["td"])+(200,),width=2)
    emoji=SKIN_EMOJI.get(theme,"✈")
    d.text((x0+12,y0+10),f"{emoji}  PAPER PLANE AI",font=FONTS["heading"],fill=h2r(P["tl"]))
    d.line([(x0,y0+40),(x0+bw,y0+40)],fill=h2r(P["accent"])+(200,),width=2)
    d.text((x0+12,y0+48),f"THEME: {theme.upper().replace('_',' ')}",
           font=FONTS["small"],fill=h2r(P["tm"]))
    d.text((x0+12,y0+68),f"TITLE: {title[:30]}",font=FONTS["small"],fill=h2r(P["tm"]))
    d.text((x0+12,y0+88),"SCALE: 1:1  |  UNITS: px",font=FONTS["small"],fill=h2r(P["tm"]))
    d.text((x0+12,y0+108),"VIEW: ORTHOGRAPHIC PLAN",font=FONTS["small"],fill=h2r(P["tm"]))
    d.line([(x0,y0+128),(x0+bw,y0+128)],fill=h2r(P["grid"])+(120,),width=1)
    d.text((x0+12,y0+134),"Paper Plane AI  •  github.com/sidx1-scratch",
           font=FONTS["tiny"],fill=h2r(P["tm"]))

def _draw_cad_legend(d, P):
    x,y=16,CAD_H-120
    d.text((x,y),"FOLD LEGEND",font=FONTS["small"],fill=h2r(P["td"])); y+=22
    for ft,label in [("valley","Valley fold (toward you)"),
                     ("mountain","Mountain fold (away)"),
                     ("none","Crease / reference")]:
        _draw_fold_line(d,(x,y+8),(x+80,y+8),ft,P,width=2)
        d.text((x+90,y),label,font=FONTS["tiny"],fill=h2r(P["td"])); y+=22

# ─────────────────────────────────────────────────────────────────────────────
#  BLUEPRINT PAGES
# ─────────────────────────────────────────────────────────────────────────────

STEPS_PER_PAGE=4; CARD_H=310; CARD_M=44; CARD_GAP=22; CARD_W=W-2*CARD_M

def drr(d,xy,r=16,fill=None,outline=None,w=2):
    x0,y0,x1,y1=xy
    d.rounded_rectangle([x0,y0,x1,y1],radius=r,fill=fill,outline=outline,width=w)

def make_cover_page(bp, theme, skin) -> Image.Image:
    P   = get_pal(theme)
    img = Image.new("RGB",(W,H),h2r(P["bg"]))

    # ── big skinned plane schematic ───────────────────────────────────────
    img = render_plane(img,skin,cx=W//2,cy=560,s=2.5,P=P,theme=theme,
                       show_dims=True,show_shadow=True)
    d   = ImageDraw.Draw(img)

    # ── header ────────────────────────────────────────────────────────────
    emoji=SKIN_EMOJI.get(theme,"✈")
    drr(d,[0,0,W,160],r=0,fill=h2r(P["panel"]))
    d.text((44,30),f"{emoji}  PAPER PLANE AI",font=FONTS["title"],fill=h2r(P["tl"]))
    d.text((44,96),f"Theme: {theme.replace('_',' ').title()}  •  {SKIN_DESC.get(theme,'')}  •  AI-generated blueprint",
           font=FONTS["small"],fill=h2r(P["tm"]))
    d.rectangle([0,160,W,172],fill=h2r(P["accent"]))

    # ── name ──────────────────────────────────────────────────────────────
    d.text((44,180),bp.get("name","Blueprint"),font=FONTS["title"],fill=h2r(P["td"]))

    # ── spec cards ────────────────────────────────────────────────────────
    specs=[("Difficulty",bp.get("difficulty","—")),("Flight Style",bp.get("flight_style","—")),
           ("Paper",bp.get("paper_size","A4")),("Range",f"{bp.get('estimated_fly_range_m','?')} m")]
    cw=255; cy0=855
    for i,(label,val) in enumerate(specs):
        cx2=44+i*(cw+18)
        drr(d,[cx2,cy0,cx2+cw,cy0+108],r=12,fill=h2r(P["panel"]))
        d.text((cx2+14,cy0+14),label,font=FONTS["small"],fill=h2r(P["tm"]))
        d.text((cx2+14,cy0+42),str(val),font=FONTS["heading"],fill=h2r(P["tl"]))

    # ── fold legend ───────────────────────────────────────────────────────
    ly=1000
    d.text((44,ly),"FOLD LEGEND",font=FONTS["heading"],fill=h2r(P["td"])); ly+=38
    for ft,label in [("valley","Valley fold — fold toward you"),
                     ("mountain","Mountain fold — fold away from you"),
                     ("none","Crease / reference line")]:
        _draw_fold_line(d,(44,ly+8),(155,ly+8),ft,P,width=3)
        d.text((170,ly),label,font=FONTS["body"],fill=h2r(P["td"])); ly+=36

    # ── materials ─────────────────────────────────────────────────────────
    ly+=30
    d.text((44,ly),"MATERIALS",font=FONTS["heading"],fill=h2r(P["td"])); ly+=36
    for m in bp.get("materials",["1 sheet of paper"])[:6]:
        d.ellipse([44,ly+6,56,ly+18],fill=h2r(P["accent"]))
        d.text((68,ly),m,font=FONTS["body"],fill=h2r(P["td"])); ly+=30

    # ── fun fact ──────────────────────────────────────────────────────────
    ff=bp.get("fun_fact","")
    if ff and ly<H-160:
        ly+=24
        drr(d,[44,ly,W-44,ly+118],r=12,fill=h2r(P["card"]))
        d.text((66,ly+14),"✨  FUN FACT",font=FONTS["small"],fill=h2r(P["fold_val"]))
        for i,line in enumerate(wrap_text(ff,FONTS["body"],W-130)[:3]):
            d.text((66,ly+38+i*26),line,font=FONTS["body"],fill=h2r(P["td"]))

    # ── footer ────────────────────────────────────────────────────────────
    d.rectangle([0,H-58,W,H],fill=h2r(P["panel"]))
    d.text((44,H-40),"Generated by Paper Plane AI  •  github.com/sidx1-scratch",
           font=FONTS["small"],fill=h2r(P["tm"]))
    return img


def make_step_page(steps_chunk, page_num, bp_name, theme, skin) -> Image.Image:
    P   = get_pal(theme)
    img = Image.new("RGB",(W,H),h2r(P["bg"]))

    # mini plane corner
    img = render_plane(img,skin,cx=W-170,cy=130,s=0.7,P=P,theme=theme,show_shadow=False)
    d   = ImageDraw.Draw(img)

    emoji=SKIN_EMOJI.get(theme,"✈")
    drr(d,[0,0,W,82],r=0,fill=h2r(P["panel"]))
    d.text((44,24),f"{emoji}  {bp_name}  —  Folding Steps",font=FONTS["heading"],fill=h2r(P["tl"]))
    d.text((W-108,28),f"p.{page_num}",font=FONTS["small"],fill=h2r(P["tm"]))
    d.rectangle([0,82,W,90],fill=h2r(P["accent"]))

    y=112
    for step in steps_chunk:
        _step_card(d,step,y,P); y+=CARD_H+CARD_GAP

    d.rectangle([0,H-50,W,H],fill=h2r(P["panel"]))
    d.text((44,H-34),"Paper Plane AI",font=FONTS["small"],fill=h2r(P["tm"]))
    return img


def _step_card(d,step,y,P):
    num=step.get("number","?"); title=step.get("title","Step")
    desc=step.get("description",""); fold=step.get("fold_type","none")
    symm=step.get("symmetry",False)

    drr(d,[CARD_M,y,CARD_M+CARD_W,y+CARD_H],r=16,
        fill=h2r(P["card"]),outline=h2r(P["td"])+(80,),w=2)

    # step number bubble
    bx,by=CARD_M+44,y+54
    d.ellipse([bx-32,by-32,bx+32,by+32],fill=h2r(P["accent"]))
    d.text((bx-len(str(num))*8,by-15),str(num),font=FONTS["step_num"],fill=h2r(P["tl"]))

    d.text((CARD_M+94,y+28),title.upper(),font=FONTS["heading"],fill=h2r(P["td"]))

    badge_col={"valley":P["fold_val"],"mountain":P["fold_mnt"],"crease":"#6B7280",
               "unfold":"#D97706","cut":"#7C3AED","none":"#9CA3AF"}
    drr(d,[W-CARD_M-135,y+26,W-CARD_M-10,y+56],r=8,fill=h2r(badge_col.get(fold,"#9CA3AF")))
    d.text((W-CARD_M-129,y+31),fold.upper(),font=FONTS["small"],fill=h2r(P["tl"]))

    for i,line in enumerate(wrap_text(desc,FONTS["body"],CARD_W-130)[:6]):
        d.text((CARD_M+94,y+78+i*26),line,font=FONTS["body"],fill=h2r(P["td"]))

    # mini fold diagram
    dx=W-CARD_M-165; dy=y+72
    _draw_fold_line(d,(dx,dy),(dx+125,dy+85),fold,P,width=2)
    # arrow
    d.polygon([(dx+62,dy+100),(dx+80,dy+112),(dx+62,dy+124)],
              fill=h2r(P["arrow"])+(220,))

    if symm:
        d.text((CARD_M+94,y+CARD_H-30),"↔  Repeat symmetrically on both sides",
               font=FONTS["small"],fill=h2r(P["fold_val"]))


def make_tips_page(bp, theme, skin) -> Image.Image:
    P   = get_pal(theme)
    img = Image.new("RGB",(W,H),h2r(P["bg"]))

    img = render_plane(img,skin,cx=W-170,cy=130,s=0.7,P=P,theme=theme,show_shadow=False)
    d   = ImageDraw.Draw(img)

    emoji=SKIN_EMOJI.get(theme,"✈")
    drr(d,[0,0,W,82],r=0,fill=h2r(P["panel"]))
    d.text((44,24),f"{emoji}  Tips, Warnings & Launch Guide",
           font=FONTS["heading"],fill=h2r(P["tl"]))
    d.rectangle([0,82,W,90],fill=h2r(P["accent"]))

    y=118
    d.text((44,y),"PRO TIPS",font=FONTS["heading"],fill=h2r(P["td"])); y+=42
    for tip in bp.get("tips",[])[:5]:
        drr(d,[44,y,W-44,y+82],r=10,fill=h2r(P["tip"]))
        d.text((72,y+12),"✔",font=FONTS["heading"],fill=h2r(P["arrow"]))
        for i,line in enumerate(wrap_text(tip,FONTS["body"],W-168)[:2]):
            d.text((112,y+14+i*26),line,font=FONTS["body"],fill=h2r(P["td"]))
        y+=98

    y+=18
    d.text((44,y),"WATCH OUT FOR",font=FONTS["heading"],fill=h2r(P["td"])); y+=42
    for warn in bp.get("warnings",[])[:4]:
        drr(d,[44,y,W-44,y+82],r=10,fill=h2r(P["warn"]))
        d.text((72,y+12),"⚠",font=FONTS["heading"],fill=h2r("#D97706"))
        for i,line in enumerate(wrap_text(warn,FONTS["body"],W-168)[:2]):
            d.text((112,y+14+i*26),line,font=FONTS["body"],fill=h2r(P["td"]))
        y+=98

    # ── launch guide ──────────────────────────────────────────────────────
    y+=28
    d.text((44,y),"OPTIMAL LAUNCH ANGLES",font=FONTS["heading"],fill=h2r(P["td"])); y+=36
    _launch_diagram(d,120,y+90,P)

    d.rectangle([0,H-50,W,H],fill=h2r(P["panel"]))
    d.text((44,H-34),f"Happy flying! {emoji}",font=FONTS["small"],fill=h2r(P["tm"]))
    return img


def _launch_diagram(d,cx,cy,P):
    ac=h2r(P["accent"]); tc=h2r(P["td"]); mu=h2r(P["tm"])
    d.line([(cx-30,cy),(cx+380,cy)],fill=tc,width=3)
    for deg,label,col in [(30,"30° distance",ac),(45,"45° height",mu),(15,"15° low",mu)]:
        ar=math.radians(deg)
        ex=cx+int(260*math.cos(ar)); ey=cy-int(260*math.sin(ar))
        d.line([(cx,cy),(ex,ey)],fill=col+(200,),width=2)
        d.text((ex+6,ey-4),label,font=FONTS["small"],fill=col+(220,))
        # arc
        d.arc([cx-60,cy-60,cx+60,cy+60],start=-deg,end=0,fill=mu+(100,),width=1)
    # plane icon
    d.polygon([(cx+210,cy-int(210*math.tan(math.radians(30)))-16),
               (cx+230,cy-int(230*math.tan(math.radians(30)))),
               (cx+210,cy-int(210*math.tan(math.radians(30)))+16)],
              fill=ac+(220,))

# ─────────────────────────────────────────────────────────────────────────────
#  INPUT INGESTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_web(url,lim=9000):
    log.info("Fetching: %s",url)
    try:
        r=http_req.get(url,timeout=14,headers={"User-Agent":"Mozilla/5.0 (PaperPlaneAI/5.0)"})
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        text="\n".join(t.get_text(strip=True) for t in soup.find_all(["h1","h2","h3","p"]) if t.get_text(strip=True))
        log.info("Web: %d chars",len(text)); return text[:lim]
    except Exception as e: log.warning("Web fail: %s",e); return ""

def youtube_text(vid,lim=9000):
    log.info("YT: %s",vid)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        t=" ".join(e["text"] for e in YouTubeTranscriptApi.get_transcript(vid))
        log.info("YT: %d chars",len(t)); return t[:lim]
    except Exception as e: log.warning("YT fail: %s",e); return ""

def read_file(path,lim=9000):
    log.info("File: %s",path)
    try:
        t=Path(path).read_text(encoding="utf-8",errors="replace")
        log.info("File: %d chars",len(t)); return t[:lim]
    except Exception as e: log.warning("File fail: %s",e); return ""

# ─────────────────────────────────────────────────────────────────────────────
#  LLM
# ─────────────────────────────────────────────────────────────────────────────

SYS=textwrap.dedent("""\
You are a precision engineering assistant. Convert any source text into a paper
airplane blueprint. Reply with ONLY valid JSON matching this schema exactly.
No markdown fences, no preamble, no explanation.
{
  "name":"creative name inspired by source",
  "difficulty":"Beginner|Intermediate|Advanced",
  "flight_style":"Glider|Distance|Aerobatic|Dart",
  "materials":["list"],
  "paper_size":"A4|Letter|A5",
  "estimated_fly_range_m":number,
  "fun_fact":"one sentence",
  "steps":[{"number":1,"title":"short","description":"precise instruction",
             "fold_type":"valley|mountain|unfold|crease|cut|none","symmetry":true}],
  "tips":["3-5 tips"],
  "warnings":["tricky spots"]
}
""")

PERSONALITY={
    "fighter_jet":"Military fighter jet personality and name.",
    "military":"Tactical army personality.","flame":"Fast fiery personality.",
    "ocean":"Calm oceanic glider personality.","jungle":"Stealthy nature personality.",
    "arctic":"Cold precise icy personality.","galaxy":"Cosmic space personality.",
    "bumblebee":"Bold buzzy personality.","patriot":"Proud patriotic personality.",
    "sakura":"Elegant Japanese personality.","lightning":"Electric fast personality.",
    "racing":"Speed-obsessed racing personality.","graffiti":"Urban rebellious personality.",
    "skull":"Dark daring punk personality.","rust":"Weathered veteran personality.",
    "rainbow":"Joyful colourful personality.",
}

def build_blueprint(text,model=DEFAULT_MODEL,theme="default"):
    log.info("LLM: model=%s",model)
    hint=PERSONALITY.get(theme,"")
    prompt=f"{hint}\n\nConvert this into a blueprint. Return ONLY JSON.\n\nSOURCE:\n{text[:11000]}"
    for attempt in range(3):
        try:
            res=groq_client().chat.completions.create(
                model=model,messages=[{"role":"system","content":SYS},
                                       {"role":"user","content":prompt}],
                temperature=0.3,max_tokens=2048)
            raw=res.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw=raw.split("```")[1]
                if raw.startswith("json"): raw=raw[4:]
            bp=json.loads(raw)
            log.info("Blueprint: '%s'",bp.get("name","?")); return bp
        except json.JSONDecodeError as e: log.warning("JSON fail #%d: %s",attempt+1,e)
        except Exception as e:
            log.error("Groq fail #%d: %s",attempt+1,e)
            if attempt==2: raise
    raise RuntimeError("Blueprint failed after 3 attempts.")

# ─────────────────────────────────────────────────────────────────────────────
#  EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def save_pdf(pages,path="output.pdf"):
    log.info("PDF: %d pages → %s",len(pages),path)
    c=rl_canvas.Canvas(path,pagesize=letter)
    pw,ph=letter
    for p in pages:
        c.drawImage(ImageReader(p),0,0,width=pw,height=ph,preserveAspectRatio=True)
        c.showPage()
    c.save(); return path

def save_png(img,path):
    img.save(path); log.info("PNG: %s",path); return path

# ─────────────────────────────────────────────────────────────────────────────
#  THEME LISTING  (--list-themes)
# ─────────────────────────────────────────────────────────────────────────────

def list_themes():
    print("\n  PAPER PLANE AI — Available themes\n  " + "─"*50)
    for name,desc in SKIN_DESC.items():
        emoji=SKIN_EMOJI.get(name,"✈")
        P=get_pal(name)
        # ASCII colour swatch using ANSI
        try:
            r,g,b=h2r(P["accent"])
            swatch=f"\033[48;2;{r};{g};{b}m   \033[0m"
        except:
            swatch="   "
        print(f"  {swatch} {emoji}  {name:<14}  {desc}")
    print()

# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    themes=list(SKINS.keys())
    p=argparse.ArgumentParser(
        description="Paper Plane AI v5 — the best paper plane tool ever built",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
          FULL RUN (LLM + PDF):
            python main.py --web URL --theme flame
            python main.py --youtube ID --theme galaxy
            python main.py --file notes.txt --theme skull --image logo.png

          CAD PREVIEW (no LLM, no PDF):
            python main.py --cad-preview --theme rainbow
            python main.py --cad-preview --theme fighter_jet --image mylogo.png --out-png cad.png

          LIST THEMES:
            python main.py --list-themes

          Themes: {', '.join(themes)}
        """))
    src=p.add_argument_group("input sources (for full run)")
    src.add_argument("--web",metavar="URL")
    src.add_argument("--youtube",metavar="VIDEO_ID")
    src.add_argument("--file",metavar="PATH")
    p.add_argument("--theme",default="default",choices=themes,
                   help="Plane body skin theme")
    p.add_argument("--image",metavar="PATH",
                   help="Your own image — mapped directly onto the plane body")
    p.add_argument("--model",default=DEFAULT_MODEL,choices=FREE_MODELS)
    p.add_argument("--out-pdf",default="output.pdf")
    p.add_argument("--out-png",default="preview.png")
    p.add_argument("--cad-preview",action="store_true",
                   help="Render CAD view only — no LLM call, no PDF")
    p.add_argument("--list-themes",action="store_true",
                   help="Print all available themes and exit")
    return p.parse_args()


def run():
    args=parse_args()

    if args.list_themes:
        list_themes(); return

    theme=args.theme
    image_path=args.image or os.getenv("IMAGE_PATH")

    log.info("Building skin: theme=%s image=%s",theme,image_path or "none")
    skin=build_skin(theme,image_path)

    # ── CAD preview only ──────────────────────────────────────────────────
    if args.cad_preview:
        log.info("Rendering CAD preview…")
        cad=make_cad_preview(theme,skin,title=theme.replace("_"," ").title())
        path=save_png(cad,args.out_png)
        print(f"\n✅  CAD preview saved → {path}")
        try: cad.show()
        except: pass
        return

    # ── full run ──────────────────────────────────────────────────────────
    text=""
    if args.web:     text+=extract_web(args.web)
    if args.youtube: text+=youtube_text(args.youtube)
    if args.file:    text+=read_file(args.file)
    if not text.strip():
        if os.getenv("WEB_URL"):    text+=extract_web(os.getenv("WEB_URL"))
        if os.getenv("YOUTUBE_ID"):text+=youtube_text(os.getenv("YOUTUBE_ID"))
        if os.getenv("FILE_PATH"): text+=read_file(os.getenv("FILE_PATH"))
    if not text.strip():
        sys.exit("ERROR: No input. Use --web, --youtube, --file, or --cad-preview.")

    bp=build_blueprint(text,model=args.model,theme=theme)

    pages=[]
    log.info("Rendering cover…")
    pages.append(make_cover_page(bp,theme,skin))

    steps=bp.get("steps",[])
    for i in range(0,len(steps),STEPS_PER_PAGE):
        log.info("Rendering step page %d…",i//STEPS_PER_PAGE+2)
        pages.append(make_step_page(steps[i:i+STEPS_PER_PAGE],
                                    i//STEPS_PER_PAGE+2,bp.get("name","Blueprint"),
                                    theme,skin))

    log.info("Rendering tips page…")
    pages.append(make_tips_page(bp,theme,skin))

    pdf=save_pdf(pages,args.out_pdf)
    png=save_png(pages[0],args.out_png)

    print(f"\n✅  Done!")
    print(f"   PDF      → {pdf}")
    print(f"   Preview  → {png}")
    print(f"   Plane    : {bp.get('name')}  [{theme}]  ({bp.get('difficulty')} / {bp.get('flight_style')})")
    print(f"   Tip      : Run with --cad-preview first to check the skin before generating!")


if __name__=="__main__":
    run()
