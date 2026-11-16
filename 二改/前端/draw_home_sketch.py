# ruff: noqa
"""画 2026-11-16 那张首页草图（静态，不接数据）。

用法：python3 二改/前端/draw_home_sketch.py  → 在同目录生成 首页草图.html
几何尺寸、颜色、月相的画法都在这里，做真页面时照着搬到 JS 里。说明见 二改记录.md「阶段五」。
"""
import math, random, json, sys
from datetime import datetime, timezone, timedelta
random.seed(1116)
SYN = 29.530588
NEW = [datetime(2026,10,10,15,50,tzinfo=timezone.utc), datetime(2026,11,9,7,2,tzinfo=timezone.utc), datetime(2026,12,9,0,52,tzinfo=timezone.utc)]
def phase(dt):
    prev = max(n for n in NEW if n <= dt)
    age = (dt - prev).total_seconds()/86400
    f = (1 - math.cos(2*math.pi*age/SYN))/2
    return age, f, age < SYN/2
def f2(x): return f"{x:.2f}".rstrip("0").rstrip(".")
def dark_path(cx, cy, r, f, waxing):
    rx = abs(1-2*f)*r
    top, bot = f"{f2(cx)} {f2(cy-r)}", f"{f2(cx)} {f2(cy+r)}"
    if waxing:   # 暗面在左
        s2 = 0 if f < 0.5 else 1
        return f"M {top} A {f2(r)} {f2(r)} 0 0 0 {bot} A {f2(rx)} {f2(r)} 0 0 {s2} {top} Z"
    else:        # 暗面在右
        s2 = 1 if f < 0.5 else 0
        return f"M {top} A {f2(r)} {f2(r)} 0 0 1 {bot} A {f2(rx)} {f2(r)} 0 0 {s2} {top} Z"

today = datetime(2026,11,16,12,0,tzinfo=timezone.utc)
age, f, wax = phase(today)
print("today age %.2f illum %.2f waxing %s" % (age, f, wax), file=sys.stderr)

# ---------- 上半：月亮和拖出去的线 ----------
W, H = 390, 312
cx, cy, r = 262, 112, 72
g = []
# 一道红弧（第四张图里的那种）
g.append(f'<path d="M 344 -6 A 430 430 0 0 0 190 {H+6}" fill="none" stroke="#C0452F" stroke-width="0.9" opacity="0.85"></path>')
# 外圈细环 + 12 个刻度 + 顶上的小三角
g.append(f'<circle cx="{cx}" cy="{cy}" r="{r+18}" fill="none" stroke="currentColor" stroke-width="0.7" opacity="0.55"></circle>')
for k in range(12):
    a = math.radians(k*30 + 15)
    x1, y1 = cx + (r+27)*math.cos(a), cy + (r+27)*math.sin(a)
    x2, y2 = cx + (r+35)*math.cos(a), cy + (r+35)*math.sin(a)
    g.append(f'<line x1="{f2(x1)}" y1="{f2(y1)}" x2="{f2(x2)}" y2="{f2(y2)}" stroke="currentColor" stroke-width="0.9" opacity="0.7"></line>')
g.append(f'<path d="M {cx-4.5} {cy-r-36} L {cx+4.5} {cy-r-36} L {cx} {cy-r-28} Z" fill="currentColor"></path>')
# 月面：暗面实心，亮面点画
g.append(f'<path d="{dark_path(cx, cy, r, f, wax)}" fill="currentColor"></path>')
g.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="currentColor" stroke-width="1"></circle>')
dots = []
n = 0
while n < 300:
    x, y = random.uniform(-1,1), random.uniform(-1,1)
    if x*x + y*y > 0.97: continue
    xt = (1-2*f)*math.sqrt(max(0,1-y*y))
    if x <= xt + 0.015: continue
    d = (x - xt)                      # 离明暗线越近越密
    if random.random() > math.exp(-3.2*d): continue
    rr = random.choice([0.55,0.7,0.7,0.85,1.0])
    dots.append(f'<circle cx="{f2(cx+x*r)}" cy="{f2(cy+y*r)}" r="{rr}" fill="currentColor"></circle>')
    n += 1
g.append('<g opacity="0.85">' + "".join(dots) + '</g>')
# 中心小点
# 五条拖出去的线
ux, uy = -math.cos(math.radians(38)), math.sin(math.radians(38))
spec = [(200,124),(182,150),(164,134),(146,160),(128,144)]
def star(x, y, s):
    return (f'<path d="M {f2(x)} {f2(y-s)} Q {f2(x)} {f2(y)} {f2(x+s)} {f2(y)} Q {f2(x)} {f2(y)} {f2(x)} {f2(y+s)} '
            f'Q {f2(x)} {f2(y)} {f2(x-s)} {f2(y)} Q {f2(x)} {f2(y)} {f2(x)} {f2(y-s)} Z" fill="currentColor"></path>')
ends = []
for i, (adeg, L) in enumerate(spec):
    a = math.radians(adeg)
    sx, sy = cx + (r+18)*math.cos(a), cy + (r+18)*math.sin(a)
    ex, ey = sx + L*ux, sy + L*uy
    ends.append((ex, ey))
    g.append(f'<line x1="{f2(sx)}" y1="{f2(sy)}" x2="{f2(ex)}" y2="{f2(ey)}" stroke="currentColor" stroke-width="0.8"></line>')
    g.append(f'<circle cx="{f2(sx)}" cy="{f2(sy)}" r="2.2" fill="currentColor"></circle>')
    g.append(f'<circle cx="{f2(ex)}" cy="{f2(ey)}" r="3.2" fill="currentColor"></circle>')
    for t, kind in [(0.28+0.05*(i%2),"dot"), (0.52-0.04*(i%3),"star"), (0.74+0.03*(i%2),"dot")]:
        px, py = sx + L*t*ux, sy + L*t*uy
        g.append(star(px, py, 5.5) if kind == "star" else f'<circle cx="{f2(px)}" cy="{f2(py)}" r="1.7" fill="currentColor"></circle>')
    # 线尾再散三个小点
    for j, gap in enumerate((9, 16, 22)):
        g.append(f'<circle cx="{f2(ex+gap*ux)}" cy="{f2(ey+gap*uy)}" r="{f2(1.4-0.35*j)}" fill="currentColor"></circle>')
    # 编号放在这条线自己的延长线上（平行线之间不会互相压到）
    g.append(f'<text x="{f2(ex+35*ux)}" y="{f2(ey+35*uy+4)}" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11" fill="currentColor" letter-spacing="0.5">0{i+1}</text>')
top_svg = (f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="今晚的月亮，上弦前一夜。五条线从月亮拖出去，对应今天留下的五件事。" style="display: block">' + "".join(g) + '</svg>')

# ---------- 这一章的月相条：10·23 到 11·22 ----------
start = datetime(2026,10,23,12,0,tzinfo=timezone.utc)
N = 31; rr = 4.3; step = (342 - 2*rr)/(N-1)
m = []
for d in range(N):
    dt = start + timedelta(days=d)
    _, ff, ww = phase(dt)
    x = rr + d*step; y = 8
    op = "1" if dt <= today else "0.42"
    m.append(f'<g opacity="{op}"><path d="{dark_path(x, y, rr, ff, ww)}" fill="currentColor"></path>'
             f'<circle cx="{f2(x)}" cy="{y}" r="{rr}" fill="none" stroke="currentColor" stroke-width="0.6"></circle></g>')
    if dt.date() == today.date():
        m.append(f'<path d="M {f2(x-3.2)} 24 L {f2(x+3.2)} 24 L {f2(x)} 18 Z" fill="#C0452F"></path>')
strip_svg = ('<svg viewBox="0 0 342 26" width="342" height="26" role="img" '
             'aria-label="这一章三十一个夜晚的月相，今天是第二十五夜。" style="display: block">' + "".join(m) + '</svg>')

rows = [
 ("01", "［今天写下的第一条 · 标题］", "HOLD"),
 ("02", "［今天写下的第二条 · 标题］", "HOLD"),
 ("03", "［今天登记的一条约定］", "PLAN"),
 ("04", "［今天从归档里捞回的一条］", "捞回"),
 ("05", "［今天从归档里捞回的另一条］", "捞回"),
]
row_html = "".join(
 f'''<button type="button" style="display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; column-gap: 10px; align-items: baseline; width: 100%; min-height: 44px; padding: 10px 0; margin: 0; border: 0; border-top: 1px solid rgba(27, 42, 143, 0.18); background: transparent; color: inherit; text-align: left; cursor: pointer">
<span style="font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 0.5px; opacity: 0.78">{n}</span>
<span style="font-family: 'Noto Serif SC', serif; font-size: 15px; line-height: 1.45">{t}</span>
<span style="font-family: 'IBM Plex Mono', 'Noto Serif SC', monospace; font-size: 11px; letter-spacing: 1px; opacity: 0.78">{k}</span>
</button>''' for n, t, k in rows)

nav = [("扉页","PINS"),("目录","MAP"),("夹页","LETTERS"),("封底","SETTINGS")]
nav_html = "".join(
 f'''<button type="button" style="flex: 1 1 0; min-height: 56px; margin: 0; padding: 8px 0 6px; border: 0; background: transparent; color: inherit; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; cursor: pointer">
<span style="font-family: 'Noto Serif SC', serif; font-size: 15px; font-weight: 600; letter-spacing: 2px">{a}</span>
<span style="font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 1.5px; opacity: 0.78">{b}</span>
</button>''' for a, b in nav)

html = f'''<!doctype html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>记忆库首页草图 · 今晚</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@1,500&family=IBM+Plex+Mono:wght@400;500&family=Noto+Serif+SC:wght@400;600&display=swap" rel="stylesheet">
<style>
html,body{{margin:0;background:#E9E6DE}}
body{{display:flex;justify-content:center;padding:24px 0}}
</style>
</head>
<body>
<!-- 这是 2026-11-16 的首页草图，静态的，没有接任何数据。标题是占位。说明见 二改记录.md「阶段五」。 -->
<div style="width: 390px; height: 844px; box-sizing: border-box; background: #F1EEE6; color: #1B2A8F; display: flex; flex-direction: column; overflow: hidden; border-radius: 28px; font-family: 'Noto Serif SC', 'Songti SC', 'Source Han Serif SC', serif">

<div style="display: flex; align-items: center; justify-content: space-between; padding: 14px 24px 0">
<div style="display: flex; align-items: center; gap: 10px">
<svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" style="display: block"><circle cx="10" cy="10" r="8.5" fill="none" stroke="currentColor" stroke-width="1"></circle><path d="M 10 1.5 A 8.5 8.5 0 0 0 10 18.5 Z" fill="currentColor"></path></svg>
<span style="font-family: 'IBM Plex Mono', monospace; font-size: 12px; font-weight: 500; letter-spacing: 3px">OMBRE BRAIN</span>
</div>
<button type="button" aria-label="搜索记忆" style="width: 44px; height: 44px; margin: 0 -10px 0 0; padding: 0; border: 0; background: transparent; color: inherit; display: flex; align-items: center; justify-content: center; cursor: pointer">
<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"></circle><line x1="15.5" y1="15.5" x2="21" y2="21"></line></svg>
</button>
</div>

<div style="display: flex; justify-content: space-between; align-items: baseline; padding: 6px 24px 0; font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 1.5px; opacity: 0.78">
<span>PL. 11 / 16</span>
<span>2026 · MON</span>
</div>

<div style="height: 312px; flex: none">
{top_svg}
</div>

<div style="padding: 0 24px; display: flex; flex-direction: column; gap: 4px">
<div style="width: 44px; height: 1.5px; background: currentColor"></div>
<p style="margin: 6px 0 0; font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 18px; line-height: 1.25">five things kept today,<br>the night before first quarter</p>
<p style="margin: 2px 0 8px; font-size: 13px; line-height: 1.5; opacity: 0.82">今天留下的五件事 · 明晚上弦</p>
</div>

<div style="padding: 0 24px; display: flex; flex-direction: column">
{row_html}
</div>

<div style="margin-top: auto; padding: 10px 24px 8px; display: flex; flex-direction: column; gap: 8px; border-top: 1px solid rgba(27, 42, 143, 0.18)">
<div style="display: flex; justify-content: space-between; align-items: baseline; font-family: 'IBM Plex Mono', 'Noto Serif SC', monospace; font-size: 11px; letter-spacing: 1px; opacity: 0.78">
<span>这一章 · 10·23 — 11·22</span>
<span>第 25 夜 / 31</span>
</div>
{strip_svg}
</div>

<div style="display: flex; padding: 0 12px 6px; border-top: 1px solid rgba(27, 42, 143, 0.18)">
{nav_html}
</div>

</div>
</body>
</html>
'''
import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "首页草图.html")
open(out, "w", encoding="utf-8").write(html)
print("写好了", out, file=sys.stderr)
