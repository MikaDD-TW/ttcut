#!/usr/bin/env python3
"""
ttcut V2 — table tennis match video: tag rallies, cut the ball-chasing, burn in a
persistent scoreboard. One tool, start to finish.

Copyright (c) 2026 MikaDD (Taiwan)
Released under the MIT Licence. See the LICENSE file for the full terms.

Written with the assistance of Anthropic Claude.
This tool calls ffmpeg as an external program; it neither contains nor
distributes ffmpeg itself. ffmpeg is licensed separately -- get it from
https://ffmpeg.org

Versioning
    small changes +0.1 (V1 -> V1.1 -> V1.2 ...)
    architectural or output-format changes bump the whole number (V2)

Changelog
    V2.2-EN 2026-08-30
        - English interface build of V2.2. Same scoring, cutting and rendering
          behaviour; only user-facing text and comments changed.
        - Default player names are now "Player A" / "Player B"
    V2.2 2026-08-30
        - Wider number fields: pad seconds (0.5, 1.0) and frame rate (29.97)
          used to get clipped
    V2.1 2026-08-30
        - Scoreboard accent colour is configurable: the point digits and the
          bar to the left of the names change together
        - The colour is stored in the tags JSON as scoreboard.accent, so it
          travels with the file across machines
        - New --accent flag on the command line, which wins over the JSON value
        - With no colour given, the generated .ass is byte-identical to V2;
          every other colour is untouched
    V2  2026-08-24
        - Merged into a single tool: run it and it starts a server, opens the
          browser, and one button turns your tags into a finished video
        - Scoring lives in Python only (/fold); the tagger's JS copy is gone
          -- rule changes now happen in one place, no JS/Python cross-checking
        - Serve-rotation logic moved out of JS into the same fold()
        - Fixed the mismatch between tagger and ttcut on the minimum cut length
          (0.15s shown, 2.0s actually cut). Both now use one value, adjustable
          in the interface
        - Video is served by Python with HTTP Range support, so scrubbing and
          Safari playback work
        - The video path comes from a native file dialog opened by Python
          (browsers never hand over the real path)
        - ffmpeg -progress drives a real progress bar; rendering runs in the
          background and does not block the UI
        - Unchanged: JSON export/import and the command-line render path both
          behave exactly as in V1.22
    V1.22 2026-08-20
        - Starting game count (for one video per game, continuing a match)
        - Starting score / handicap, applied every game or first game only
        - Capped mode: after 10:10 the first to 12 wins, no need to win by two
        - JSON gains format / start blocks; older files fall back to defaults
    V1.2 2026-08-20
        - Fixed a fatal bug: the V1.1 refactor dropped -c:v, so ffmpeg had been
          silently falling back to the default libx264
        - Added --hwaccel; videotoolbox hardware decoding is on by default on Mac
        - --hdr keep falls back automatically on non-HDR sources
        - libx264/libx265 switch to bitrate mode when --bitrate is given
    V1.1 2026-08-20
        - Scoreboard: game count became "filled chip + dark digits"
        - Quality: frame rate follows the source, bitrate derived from
          resolution x frame rate, three --quality tiers, HDR detection
    V1  2026-08-20  first working version

Usage:
    python3 ttcut_v2_2_EN.py                                    <- open the UI (the usual way)
    python3 ttcut_v2_2_EN.py IMG_1496.tags.json IMG_1496.MOV    <- render from the command line
    python3 ttcut_v2_2_EN.py tags.json video.MOV --quality max --dry-run

Requirements:
    Python 3.8+ and ffmpeg (not bundled, install it yourself)
    Mac    : brew install ffmpeg
             check with: ffmpeg -filters | grep subtitles
    Windows: drop ffmpeg.exe next to this script, or point --ffmpeg at its folder
"""

import argparse, json, mimetypes, os, platform, re, shutil, socket
import subprocess, sys, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

VERSION = "V2.2-EN"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

# ─────────────────────────────────────────── Layout (authored at 1920x1080, scales automatically)

BASE_W, BASE_H = 1920, 1080
PAD_L, PAD_B   = 64, 64
PANEL_W        = 480
ROW_H          = 54
COL_GAMES_X    = 312          # left edge of the games column (relative to the panel)
COL_POINTS_X   = 392          # left edge of the points column
FS_NUM         = 44           # games and points share one type size

C_PANEL    = "&H40250A&"      # deep blue panel (ASS is BGR)
C_ACCENT   = "&H187AFF&"      # orange #FF7A18
C_NAME     = "&HF9F2EA&"      # near white
C_GAMES_BG = "&HEDE3D6&"      # games chip: a light block against the dark panel
C_GAMES    = "&H40250A&"      # games digits: dark blue on the light chip
C_POINTS   = "&H187AFF&"      # points: orange digits on the dark panel
C_RULE     = "&H6E4820&"      # divider lines

A_PANEL, A_CHIP, A_RULE = 0x1E, 0x00, 0x40    # 0x00 fully opaque -> 0xFF fully transparent

DEFAULT_ACCENT = "#FF7A18"    # accent shared by the point digits and the bar beside the names


def ass_colour(hex_rgb, fallback=C_ACCENT):
    """Convert #RRGGBB to ASS &HBBGGRR&. ASS is BGR; get the order wrong and
    every colour comes out wrong."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (hex_rgb or "").strip())
    if not m:
        return fallback
    s = m.group(1).upper()
    return f"&H{s[4:6]}{s[2:4]}{s[0:2]}&"

# Fonts that actually ship on each platform (CJK-capable, so Chinese names
# in the tags JSON still render correctly)
FONT_NAME = ("PingFang TC" if IS_MAC else
             "Microsoft JhengHei" if IS_WIN else "Noto Sans CJK TC")
FONT_NUM  = ("Helvetica Neue" if IS_MAC else
             "Segoe UI" if IS_WIN else "DejaVu Sans")

# Hardware encoder per platform
HW_ENCODER = "h264_videotoolbox" if IS_MAC else "libx264"

# Three --quality tiers: bitrate multiplier / CRF / x264 preset / force software encoding
QUALITY = {
    "fast": dict(scale=0.70, crf=21, preset="veryfast", force_sw=False),
    "high": dict(scale=1.00, crf=18, preset="medium",   force_sw=False),
    "max":  dict(scale=1.40, crf=16, preset="slow",     force_sw=True),
}

DEFAULT_MIN_CUT = 2.0         # anything shorter than this is left alone, to avoid pointless jump cuts


# ─────────────────────────────────────────── Scoring (the single source of truth)

def fold_full(events, fmt, start, first_server=0):
    """Fold the event stream into scoreboard states. This is the only scoring
    implementation in the project; the UI and the CLI both go through it.

    fmt   = dict(target, deuce='standard'|'capped', cap)
    start = dict(games=(gA, gB), points=(a, b), scope='every'|'first')
    first_server = 0(A) / 1(B)

    Returns dict:
        states  [(source time, gA, gB, a, b), ...]  for the ASS scoreboard;
                the first entry has time None
        snaps   same length as events; a dict for point events, None otherwise,
                used by the event list in the UI
        cur     current state, including who serves next

    Serve rotation counts only points actually played, so handicap starting
    points do not shift the rotation; once both sides reach target-1 (deuce)
    the serve changes every point.
    """
    T, mode, cap = fmt["target"], fmt["deuce"], fmt["cap"]
    gA, gB = start["games"]
    sp, scope = list(start["points"]), start["scope"]

    def init_pts(gi):
        return list(sp) if (scope == "every" or gi == 0) else [0, 0]

    gi = 0
    a, b = init_pts(0)
    server, served_in_turn = first_server, 0
    pending = False                        # end of game: keep the score on screen until the next point
    states = [(None, gA, gB, a, b)]        # opening state; its timestamp is filled in later
    snaps = []

    def is_deuce():
        return a >= T - 1 and b >= T - 1

    def game_over():
        hi, lo = max(a, b), min(a, b)
        if mode == "capped" and hi >= cap:      # after 10:10 the first to cap wins, no win-by-two
            return True
        return hi >= T and hi - lo >= 2          # standard: 11 points and two clear

    for e in events:
        if e["type"] == "game":
            gi += 1
            a, b = init_pts(gi)
            server, served_in_turn = (first_server + gi) % 2, 0
            pending = False
            states.append((e["t"], gA, gB, a, b))
            snaps.append(None)
            continue
        if e["type"] != "point":
            snaps.append(None)
            continue
        if pending:
            gi += 1
            a, b = init_pts(gi)
            pending = False
        if e["winner"] == "A":
            a += 1
        else:
            b += 1
        won = game_over()
        snaps.append(dict(a=a, b=b, won=won,
                          gA=gA + (1 if won and a > b else 0),
                          gB=gB + (1 if won and b > a else 0)))
        if won:
            if a > b:
                gA += 1
            else:
                gB += 1
            pending = True
            server, served_in_turn = (first_server + gi + 1) % 2, 0   # other player serves first next game
        else:
            served_in_turn += 1
            if served_in_turn >= (1 if is_deuce() else 2):
                server, served_in_turn = 1 - server, 0
        states.append((e["t"], gA, gB, a, b))

    cur = dict(a=a, b=b, gA=gA, gB=gB, gi=gi, server=server, pending=pending,
               gameNo=gA + gB + 1 - (1 if pending else 0))
    return dict(states=states, snaps=snaps, cur=cur)


def fold(events, fmt, start):
    """State sequence for the ASS scoreboard (identical output to V1.22 fold())."""
    return fold_full(events, fmt, start)["states"]


def read_format(doc):
    """Read match format and starting score from JSON; older files without
    these fields fall back to defaults."""
    f = doc.get("format", {}) or {}
    target = int(f.get("pointsPerGame", doc.get("pointsPerGame", 11)))
    mode = f.get("deuce", "standard")
    cap = int(f.get("cap", target + 1))
    st = doc.get("start", {}) or {}
    g = st.get("games", {}) or {}
    p = st.get("points", {}) or {}
    fmt = dict(target=target, deuce=mode, cap=cap)
    start = dict(games=[int(g.get("A", 0)), int(g.get("B", 0))],
                 points=[int(p.get("A", 0)), int(p.get("B", 0))],
                 scope=st.get("handicapScope", "every"))
    return fmt, start


# ─────────────────────────────────────────── Cut ranges

def build_cuts(events, tail, lead, min_cut, cut_lets, let_tail):
    """point -> next serve = cut. serve -> serve (a let) = kept by default,
    optionally cut as well."""
    cuts = []
    for i, e in enumerate(events):
        if e["type"] == "point":
            nxt = next((x for x in events[i + 1:] if x["type"] == "serve"), None)
            if nxt:
                cuts.append((e["t"] + tail, nxt["t"] - lead, "ball retrieval after point"))
        elif e["type"] == "serve" and cut_lets:
            nxt = events[i + 1] if i + 1 < len(events) else None
            if nxt and nxt["type"] == "serve":
                cuts.append((e["t"] + let_tail, nxt["t"] - lead, "ball retrieval after let"))

    kept, dropped = [], []
    for f, t, why in cuts:
        (kept if t - f >= min_cut else dropped).append((f, t, why))
    return sorted(kept), sorted(dropped)


def keeps_from_cuts(head, end, cuts):
    """The complement of the cuts is what we keep."""
    segs, cur = [], head
    for f, t, _ in cuts:
        if f > cur:
            segs.append((cur, min(f, end)))
        cur = max(cur, t)
        if cur >= end:
            break
    if cur < end:
        segs.append((cur, end))
    return [(s, e) for s, e in segs if e - s > 0.04]


def make_mapper(keeps):
    """Source time -> output time. Times inside a cut snap to the start of
    the next kept segment."""
    acc, table = 0.0, []
    for s, e in keeps:
        table.append((s, e, acc))
        acc += e - s
    total = acc

    def src2out(t):
        for s, e, base in table:
            if t < s:
                return base
            if t <= e:
                return base + (t - s)
        return total

    return src2out, total


# ─────────────────────────────────────────── ASS scoreboard

def ts(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def rect(x, y, w, h, colour, alpha, layer=0):
    """Filled rectangle. \\alpha must come before \\1a, otherwise it overrides it."""
    tags = (f"\\an7\\pos({x},{y})\\p1\\bord0\\shad0"
            f"\\alpha&H00&\\1c{colour}\\1a&H{alpha:02X}&")
    return layer, f"{{{tags}}}m 0 0 l {w} 0 l {w} {h} l 0 {h}"


def build_ass(states, src2out, total, names, width, height,
              font_name=FONT_NAME, font_num=FONT_NUM, accent=None):
    # The point digits and the bar beside the names share one colour
    c_accent = c_points = accent or C_ACCENT
    k = min(width / BASE_W, height / BASE_H)
    S = lambda v: round(v * k)                        # scale
    x0 = S(PAD_L)
    y0 = height - S(PAD_B) - S(ROW_H * 2)
    pw, rh = S(PANEL_W), S(ROW_H)
    row_y = (y0 + rh // 2, y0 + rh + rh // 2)
    name_x = x0 + S(28)
    gx, gw = x0 + S(COL_GAMES_X), S(COL_POINTS_X) - S(COL_GAMES_X)
    games_cx = gx + gw // 2
    points_cx = x0 + S(COL_POINTS_X) + (pw - S(COL_POINTS_X)) // 2
    fs = S(FS_NUM)

    head = f"""[Script Info]
; ttcut {VERSION}
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Nm,{font_name},{S(29)},{C_NAME},{C_NAME},&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,4,0,0,0,1
Style: Nu,{font_num},{fs},{c_points},{c_points},&H00000000&,&H00000000&,1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Gfx,Arial,20,&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []
    add = lambda layer, style, a, b, txt: lines.append(
        f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{txt}")

    # ── Panel, games chips, accent bar, dividers (on screen for the whole film)
    # Within one layer things stack in the order written, so the dividers come
    # last in order to sit on top of the chips
    for layer, d in [
        rect(x0, y0, pw, rh * 2, C_PANEL, A_PANEL),                    # panel
        rect(gx, y0, gw, rh, C_GAMES_BG, A_CHIP),                      # games chip (top)
        rect(gx, y0 + rh, gw, rh, C_GAMES_BG, A_CHIP),                 # games chip (bottom)
        rect(x0, y0, S(5), rh * 2, c_accent, 0x00),                    # accent bar
        rect(x0, y0 + rh, pw, max(1, S(2)), C_RULE, A_RULE),           # horizontal divider
        rect(gx, y0, max(1, S(2)), rh * 2, C_RULE, A_RULE),
        rect(x0 + S(COL_POINTS_X), y0, max(1, S(2)), rh * 2, C_RULE, A_RULE),
    ]:
        add(layer, "Gfx", 0, total, d)

    # ── Player names (always on screen)
    for i, nm in enumerate(names):
        add(1, "Nm", 0, total,
            f"{{\\an4\\pos({name_x},{row_y[i]})\\1c{C_NAME}}}{nm}")

    # ── Games and points (change with events): same size and weight, told
    # apart by the background behind them
    stamped = [(0.0, *states[0][1:])] if states[0][0] is None else []
    stamped += [(src2out(t), gA, gB, a, b) for t, gA, gB, a, b in states if t is not None]

    for i, (t, gA, gB, a, b) in enumerate(stamped):
        end = stamped[i + 1][0] if i + 1 < len(stamped) else total
        if end - t < 0.02:
            continue
        for row, (g, p) in enumerate(((gA, a), (gB, b))):
            add(2, "Nu", t, end,
                f"{{\\an5\\pos({games_cx},{row_y[row]})\\fs{fs}\\b1\\1c{C_GAMES}}}{g}")
            add(2, "Nu", t, end,
                f"{{\\an5\\pos({points_cx},{row_y[row]})\\fs{fs}\\b1\\1c{c_points}}}{p}")

    return head + "\n".join(lines) + "\n"


# ─────────────────────────────────────────── ffmpeg

def find_ffmpeg(explicit, *hint_dirs):
    """Look for ffmpeg in order: explicit path -> PATH -> next to the script
    or the video -> common Windows install locations."""
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    if explicit:
        p = os.path.abspath(explicit)
        if os.path.isdir(p):
            p = os.path.join(p, exe)
        return p if os.path.isfile(p) else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    cands = [os.path.dirname(os.path.abspath(__file__)), *hint_dirs]
    if IS_WIN:
        cands += [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin",
                  os.path.expanduser(r"~\ffmpeg\bin"),
                  os.path.expanduser(r"~\scoop\shims")]
    for d in cands:
        if not d:
            continue
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    return None


def probe(path, ff="ffprobe"):
    """Return a dict of source specs, or None if it cannot be read."""
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,avg_frame_rate,r_frame_rate,pix_fmt,codec_name,"
             "color_transfer,color_primaries,bit_rate:format=bit_rate,duration",
             "-of", "json", path],
            capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        s = d["streams"][0]

        def rate(x):
            try:
                n, den = str(x).split("/")
                return float(n) / float(den) if float(den) else 0.0
            except Exception:
                return 0.0

        frac = s.get("avg_frame_rate") or "0/0"
        if rate(frac) <= 0:
            frac = s.get("r_frame_rate") or "0/0"
        br = s.get("bit_rate") or d.get("format", {}).get("bit_rate")
        dur = d.get("format", {}).get("duration")
        return {
            "w": int(s["width"]), "h": int(s["height"]),
            "fps": rate(frac), "fps_frac": frac if rate(frac) > 0 else None,
            "pix_fmt": s.get("pix_fmt", ""), "codec": s.get("codec_name", ""),
            "trc": s.get("color_transfer", ""), "prim": s.get("color_primaries", ""),
            "bitrate": int(br) if br and str(br).isdigit() else None,
            "duration": float(dur) if dur else None,
        }
    except Exception:
        return None


def is_hdr(info):
    return bool(info) and info.get("trc") in ("arib-std-b67", "smpte2084")


def is_10bit(info):
    pf = (info or {}).get("pix_fmt", "")
    return "10" in pf or "p010" in pf or "12" in pf


def auto_bitrate(w, h, fps, scale=1.0):
    """Derive a bitrate from the pixel rate. Table tennis is high-motion, so
    this runs more generous than a general-purpose figure."""
    mpix_s = w * h * max(fps, 1) / 1e6          # 1080p30 ≈ 62 Mpix/s
    mbps = mpix_s * 0.30 * scale                # -> about 19 Mbps
    return f"{max(8.0, min(120.0, mbps)):.0f}M"


def video_encoder_args(enc, crf, preset, bitrate, pix_fmt, use_bitrate=False):
    """Every encoder spells its quality knobs differently; this translates them.
    The first pair must always be -c:v -- V1.1 dropped it and ffmpeg silently
    fell back to the default libx264."""
    c = ["-c:v", enc]
    if enc in ("libx264", "libx265"):
        q = (["-b:v", bitrate, "-maxrate", bitrate,
              "-bufsize", f"{float(bitrate[:-1]) * 2:.0f}M"] if use_bitrate
             else ["-crf", str(crf)])
        return c + ["-preset", preset, *q, "-pix_fmt", pix_fmt]
    if "videotoolbox" in enc:      # VideoToolbox has no CRF, bitrate only
        return c + ["-b:v", bitrate, "-maxrate", bitrate,
                    "-bufsize", f"{float(bitrate[:-1]) * 2:.0f}M", "-pix_fmt", pix_fmt]
    if "nvenc" in enc:
        return c + ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(crf),
                    "-b:v", "0", "-maxrate", bitrate, "-pix_fmt", pix_fmt]
    if "qsv" in enc:
        return c + ["-preset", "veryslow", "-global_quality", str(crf), "-pix_fmt", pix_fmt]
    if "amf" in enc:
        return c + ["-quality", "quality", "-rc", "cqp",
                    "-qp_i", str(crf), "-qp_p", str(crf), "-pix_fmt", pix_fmt]
    return c + ["-b:v", bitrate, "-pix_fmt", pix_fmt]


TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def filter_script(keeps, ass_name, fps, tonemap=False):
    """Filter the stream with select rather than trim+concat -- the latter
    buffers whole decoded segments in memory.
    fps first, to force a constant frame rate, otherwise iPhone VFR drifts audio
    out of sync.
    tone-map before subtitles, so the scoreboard colours are not dragged through
    the dynamic-range compression."""
    expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keeps)
    esc = ass_name.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    v = [f"[0:v]fps={fps}", f"select='{expr}'", "setpts=N/FRAME_RATE/TB"]
    if tonemap:
        v.append(TONEMAP)
    return "".join([
        ",".join(v) + "[vc];",
        f"[0:a]aselect='{expr}',asetpts=N/SR/TB[ac];",
        f"[vc]subtitles='{esc}'[vout]",
    ])


def human_bitrate(b):
    return f"{b / 1e6:.1f} Mbps" if b else "unknown"


# ─────────────────────────────────────────── Planning (shared by UI and CLI)

class PlanError(Exception):
    pass


def plan(doc, opt):
    """Work out the cut plan from the tags JSON. Touches no ffmpeg, so the UI
    uses it for live preview too."""
    events = sorted(doc.get("events", []), key=lambda e: e["t"])
    pads = doc.get("pads", {}) or {}
    lead = opt.get("lead") if opt.get("lead") is not None else pads.get("lead", 1.4)
    tail = opt.get("tail") if opt.get("tail") is not None else pads.get("tail", 1.0)
    min_cut = opt.get("min_cut", DEFAULT_MIN_CUT)
    cut_lets = bool(opt.get("cut_lets", False))
    let_tail = opt.get("let_tail", 1.5)
    fmt, start = read_format(doc)
    first_server = 1 if doc.get("firstServer") == "B" else 0

    sc = fold_full(events, fmt, start, first_server)

    serves = [e for e in events if e["type"] == "serve"]
    points = [e for e in events if e["type"] == "point"]
    if not serves or not points:
        return dict(ok=False, reason="No serve or point events, nothing to cut.",
                    scoring=sc, events=events, fmt=fmt, start=start,
                    cuts=[], dropped=[], keeps=[], total=0.0, span=0.0)

    head = serves[0]["t"] - lead
    end = points[-1]["t"] + tail
    cuts, dropped = build_cuts(events, tail, lead, min_cut, cut_lets, let_tail)
    keeps = keeps_from_cuts(head, end, cuts)
    src2out, total = make_mapper(keeps)

    return dict(ok=True, reason=None, scoring=sc, events=events,
                fmt=fmt, start=start, lead=lead, tail=tail,
                cuts=cuts, dropped=dropped, keeps=keeps,
                src2out=src2out, total=total,
                head=head, end=end, span=end - head,
                serves=len(serves), points=len(points))


def build_render(doc, plan_d, video, out, opt, ffmpeg, ffprobe, log=print,
                 progress=False):
    """Write the .ass and the filter graph, assemble the ffmpeg command.
    Returns (cmd, workdir, info)."""
    q = QUALITY[opt.get("quality", "high")]
    crf = opt.get("crf") if opt.get("crf") is not None else q["crf"]
    preset = opt.get("preset") or q["preset"]
    stem = os.path.splitext(out)[0]
    names = [doc.get("players", {}).get("A", "A"), doc.get("players", {}).get("B", "B")]

    info = probe(video, ffprobe)
    if opt.get("size"):
        w, h = (int(x) for x in str(opt["size"]).lower().split("x"))
    elif info:
        w, h = info["w"], info["h"]
    else:
        w, h = BASE_W, BASE_H
        log("! Could not read the video resolution; laying out the scoreboard for 1920x1080.")

    # ── Frame rate: follows the source by default. Fast table tennis motion
    # should not be casually dropped to 30.
    fps_opt = opt.get("fps", "source")
    if fps_opt and fps_opt != "source":
        fps_val, fps_arg = float(fps_opt), str(fps_opt)
    elif info and info["fps_frac"]:
        fps_val, fps_arg = info["fps"], info["fps_frac"]
    else:
        fps_val, fps_arg = float(doc.get("fps", 30)), str(doc.get("fps", 30))

    # ── Encoder and HDR
    enc = opt.get("encoder") or ("libx264" if q["force_sw"] else HW_ENCODER)
    hdr = is_hdr(info)
    mode = opt.get("hdr", "auto")
    if mode == "auto":
        mode = "keep" if (hdr and "hevc" in enc) else ("tonemap" if hdr else "ignore")
    if mode == "keep" and not hdr:
        log("! Source is not HDR, so --hdr keep means nothing; ignored.")
        mode = "ignore"
    if mode == "keep" and "hevc" not in enc:
        log("! --hdr keep needs an HEVC encoder; falling back to tone-map.")
        mode = "tonemap"
    tonemap = mode == "tonemap" and hdr
    pix_fmt = "p010le" if mode == "keep" else "yuv420p"

    bitrate = opt.get("bitrate") or auto_bitrate(w, h, fps_val, q["scale"])
    sw_bitrate = bool(opt.get("bitrate")) and enc in ("libx264", "libx265")

    hw = opt.get("hwaccel", "auto")
    if hw == "auto":
        hw = "videotoolbox" if IS_MAC else "none"

    # ── Quality readout: tells you at a glance whether the source or the
    # transcode is the bottleneck
    if info:
        depth = "10-bit" if is_10bit(info) else "8-bit"
        hdr_tag = f" · {info['trc']} HDR" if hdr else ""
        log(f"Source    {info['w']}x{info['h']} · {info['fps']:.2f} fps · "
            f"{info['codec']} · {depth}{hdr_tag} · {human_bitrate(info['bitrate'])}")
    log(f"Output    {w}x{h} · {fps_val:.2f} fps · {enc} · {pix_fmt}"
        f"{' · tone-mapped to SDR' if tonemap else ''}")
    log(f"Quality   {opt.get('quality', 'high')} · "
        + ("CRF " + str(crf) + f" · preset {preset}"
           if enc in ("libx264", "libx265") and not sw_bitrate
           else "target bitrate " + bitrate)
        + (f" · hw decode {hw}" if hw != "none" else ""))
    if info and info["bitrate"]:
        src_mbps = info["bitrate"] / 1e6
        if enc not in ("libx264", "libx265") or sw_bitrate:
            tgt = float(bitrate.rstrip("M"))
            if tgt < src_mbps * 0.8:
                log(f"! Target bitrate is below the source ({tgt:.0f}M < {src_mbps:.0f}M); "
                    f"to preserve quality try --bitrate {src_mbps * 1.2:.0f}M or quality max")
        if src_mbps < 12 and w * h >= 1920 * 1080:
            log(f"! Source is only {src_mbps:.0f} Mbps, so quality is capped by the recording itself.")

    # ffmpeg runs inside the output folder and the filter only sees a bare
    # filename, so a Windows C:\ drive letter needs no escaping
    workdir = os.path.dirname(os.path.abspath(out)) or "."
    ass_name = os.path.basename(stem) + ".ass"
    flt_name = os.path.basename(stem) + ".filter.txt"

    # Accent: --accent wins, then the tags JSON, then the default orange
    accent_hex = (opt.get("accent")
                  or (doc.get("scoreboard", {}) or {}).get("accent")
                  or DEFAULT_ACCENT)

    with open(os.path.join(workdir, ass_name), "w", encoding="utf-8") as f:
        f.write(build_ass(plan_d["scoring"]["states"], plan_d["src2out"],
                          plan_d["total"], names, w, h,
                          opt.get("font") or FONT_NAME, FONT_NUM,
                          ass_colour(accent_hex)))
    fgraph = filter_script(plan_d["keeps"], ass_name, fps_arg, tonemap)
    with open(os.path.join(workdir, flt_name), "w", encoding="utf-8") as f:
        f.write(fgraph)          # kept purely for debugging

    colour_tags = (["-color_primaries", "bt2020", "-color_trc", info["trc"],
                    "-colorspace", "bt2020nc"] if mode == "keep" else
                   ["-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709"])
    tag = ["-tag:v", "hvc1"] if "hevc" in enc else []

    # Read only as far as the last kept segment, otherwise ffmpeg decodes the whole file
    cmd = [ffmpeg, "-y",
           *(["-progress", "pipe:1", "-nostats"] if progress else []),
           *(["-hwaccel", hw] if hw != "none" else []),
           "-to", f"{plan_d['keeps'][-1][1] + 1:.3f}", "-i", os.path.abspath(video),
           "-filter_complex", fgraph,
           "-map", "[vout]", "-map", "[ac]",
           *video_encoder_args(enc, crf, preset, bitrate, pix_fmt, sw_bitrate),
           *colour_tags, *tag,
           "-c:a", "aac", "-b:a", "256k",
           "-metadata", f"comment=ttcut {VERSION}",
           "-movflags", "+faststart", os.path.basename(out)]
    return cmd, workdir, flt_name


def summary_lines(plan_d, opt, video):
    """Summary text shared by the CLI and the UI."""
    fmt, start = plan_d["fmt"], plan_d["start"]
    out = [f"Source    {os.path.basename(video)}",
           f"Tagged    {ts(plan_d['head'])} -> {ts(plan_d['end'])}   {plan_d['span']:.1f}s"]
    rule = ("standard deuce (win by 2)" if fmt["deuce"] == "standard"
            else f"capped (after 10:10, first to {fmt['cap']} wins)")
    bits = [f"{fmt['target']} points per game", rule]
    if any(start["games"]):
        bits.append(f"starting games {start['games'][0]}:{start['games'][1]}")
    if any(start["points"]):
        sc = "every game" if start["scope"] == "every" else "first game only"
        bits.append(f"handicap {start['points'][0]}:{start['points'][1]} ({sc})")
    out.append(f"Format    {' · '.join(bits)}")
    out.append(f"Events    {plan_d['points']} points · {plan_d['serves']} serves · "
               f"{plan_d['serves'] - plan_d['points']} lets")
    out.append(f"Padding   {plan_d['tail']}s after point · {plan_d['lead']}s before serve · "
               f"min cut {opt.get('min_cut', DEFAULT_MIN_CUT)}s"
               f"{' · lets cut too' if opt.get('cut_lets') else ''}")
    span, total = plan_d["span"], plan_d["total"]
    out.append(f"Removed   {len(plan_d['cuts'])} segments · {span - total:.1f}s")
    out.append(f"Result    {total:.1f}s   {(span - total) / span * 100:.0f}% shorter"
               if span > 0 else "Result    0s")
    return out


# ─────────────────────────────────────────── Native file dialog

_MAC_PICK = ('POSIX path of (choose file with prompt "Choose match video"'
             ' of type {"public.movie","public.video"})')

_TK_PICK = (
    "import sys,tkinter,tkinter.filedialog as fd\n"
    "r=tkinter.Tk();r.withdraw();r.attributes('-topmost',True)\n"
    "p=fd.askopenfilename(title='Choose match video',filetypes=["
    "('Video','*.mp4 *.mov *.MOV *.MP4 *.m4v *.avi *.mkv'),('All files','*.*')])\n"
    "sys.stdout.write(p or '')\n")


def native_pick_video():
    """Open the OS file dialog and return an absolute path, or None if the user
    cancels. A browser <input type=file> never exposes the real path, and ffmpeg
    needs one, hence this detour."""
    try:
        if IS_MAC:
            r = subprocess.run(["osascript", "-e", _MAC_PICK],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None                      # user cancelled
            p = r.stdout.strip()
        else:
            r = subprocess.run([sys.executable, "-c", _TK_PICK],
                               capture_output=True, text=True, timeout=300)
            p = r.stdout.strip()
        return p if p and os.path.isfile(p) else None
    except Exception:
        return None


# ─────────────────────────────────────────── Server state

STATE = {
    "video": None,          # absolute path of the loaded video
    "ffmpeg": None,
    "ffprobe": "ffprobe",
    "job": None,            # render in progress
}
STATE_LOCK = threading.Lock()


class Job:
    def __init__(self, out, total):
        self.out = out
        self.total = max(total, 0.001)
        self.pct = 0.0
        self.state = "running"      # running / done / error / cancelled
        self.message = "Preparing…"
        self.log = []
        self.proc = None
        self.started = time.time()
        self.speed = ""

    def snapshot(self):
        el = time.time() - self.started
        eta = None
        if self.state == "running" and self.pct > 2:
            eta = el * (100 - self.pct) / self.pct
        return dict(state=self.state, pct=round(self.pct, 1),
                    message=self.message, out=self.out,
                    elapsed=round(el), eta=round(eta) if eta else None,
                    speed=self.speed, log=self.log[-12:])


_TIME_RE = re.compile(r"out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def run_job(job, cmd, workdir):
    """Run ffmpeg and parse its -progress output. Runs on a background thread."""
    try:
        job.proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as ex:
        job.state, job.message = "error", f"Could not start ffmpeg: {ex}"
        return

    err_tail = []

    def drain_err():
        for line in job.proc.stderr:
            line = line.rstrip()
            if line:
                err_tail.append(line)
                del err_tail[:-40]
    t = threading.Thread(target=drain_err, daemon=True)
    t.start()

    job.message = "Encoding…"
    for line in job.proc.stdout:
        line = line.strip()
        m = _TIME_RE.search(line)
        if m:
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            job.pct = min(99.5, secs / job.total * 100)
        elif line.startswith("speed="):
            job.speed = line.split("=", 1)[1].strip()
        elif line == "progress=end":
            job.pct = 100.0

    job.proc.wait()
    t.join(timeout=2)

    if job.state == "cancelled":
        job.message = "Cancelled"
        return
    if job.proc.returncode == 0:
        job.state, job.pct, job.message = "done", 100.0, "Done"
    else:
        job.state = "error"
        job.message = f"ffmpeg exited with code {job.proc.returncode}"
        job.log = err_tail[-12:]


# ─────────────────────────────────────────── Embedded interface

HTML = r"""<meta charset="utf-8">
<title>ttcut __VERSION__ — table tennis rally tagging &amp; cutting</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --table:#08203A; --table-2:#0F3055; --panel:#0C2A49;
    --line:#3D6B96; --line-soft:#20486E;
    --ink:#E9F2FA; --ink-dim:#8FB2CE;
    --ball:#FF7A18; --warn:#FFC24D; --good:#4ADE80; --bad:#FF6B6B;
    --score:#FF7A18;   /* scoreboard accent, follows the colour picker */
    --disp:"Avenir Next Condensed","Helvetica Neue Condensed","PingFang TC",system-ui,sans-serif;
    --body:"Helvetica Neue","PingFang TC",system-ui,sans-serif;
    --mono:ui-monospace,"SF Mono",Menlo,monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--table);color:var(--ink);
    font-family:var(--body);font-size:14px;-webkit-font-smoothing:antialiased}
  button,input,select{font:inherit;color:inherit}

  .shell{display:grid;grid-template-columns:1fr 336px;grid-template-rows:auto auto 1fr;height:100vh}
  header,.setbar{grid-column:1/-1;display:flex;gap:16px;align-items:center;flex-wrap:wrap;
    padding:9px 16px;background:var(--panel);border-bottom:1px solid var(--line-soft)}
  .setbar{padding:7px 16px;gap:14px;background:var(--table-2)}
  .setbar .grp{display:flex;align-items:center;gap:7px;padding-right:14px;
    border-right:1px solid var(--line-soft)}
  .setbar .grp:last-child{border-right:0}
  .setbar .tag{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-dim)}

  .brand{font-family:var(--disp);font-size:19px;letter-spacing:.12em;text-transform:uppercase;
    display:flex;align-items:center;gap:9px;white-space:nowrap}
  .brand i{width:9px;height:9px;border-radius:50%;background:var(--ball);display:block}
  .brand small{font-family:var(--mono);font-size:10px;letter-spacing:0;color:var(--ink-dim)}
  .ctl{display:flex;align-items:center;gap:6px;color:var(--ink-dim);font-size:12px}
  input[type=text],input[type=number],select{
    background:var(--table);border:1px solid var(--line-soft);border-radius:3px;
    padding:5px 7px;color:var(--ink);font-family:var(--mono);font-size:12px}
  input[type=text]{width:92px;font-family:var(--body)}
  input[type=number]{width:64px}
  #fps{width:78px}   /* auto-detect fills in values like 29.97 / 119.88, needs the room */
  select{font-family:var(--body)}
  input:disabled{opacity:.35}
  input[type=color]{width:34px;height:26px;padding:2px;background:var(--table);
    border:1px solid var(--line-soft);border-radius:3px;cursor:pointer}
  .btn{background:transparent;border:1px solid var(--line);border-radius:3px;
    padding:6px 11px;cursor:pointer;font-size:12px;letter-spacing:.04em;transition:background .12s}
  .btn:hover{background:var(--line-soft)}
  .btn:disabled{opacity:.4;cursor:default}
  .btn:disabled:hover{background:transparent}
  .btn:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--ball);outline-offset:1px}
  .btn.hot{border-color:var(--ball);color:var(--ball)}
  .btn.hot:hover:not(:disabled){background:rgba(255,122,24,.14)}
  label.file{position:relative;overflow:hidden}
  label.file input{position:absolute;inset:0;opacity:0;cursor:pointer}
  .srcname{font-family:var(--mono);font-size:11.5px;color:var(--ink-dim);
    max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  .stage{display:flex;flex-direction:column;min-width:0;min-height:0;padding:14px 16px;gap:12px}
  .screen{flex:1;min-height:0;background:#04121F;border:1px solid var(--line-soft);
    border-radius:4px;display:flex;align-items:center;justify-content:center}
  video{max-width:100%;max-height:100%;display:block}
  .empty{color:var(--ink-dim);text-align:center;padding:30px;line-height:1.8;max-width:430px}
  .empty b{color:var(--ink);font-weight:500}

  .transport{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .tc{font-family:var(--mono);font-size:20px;font-variant-numeric:tabular-nums}
  .tc small{font-size:12px;color:var(--ink-dim);margin-left:7px}
  .scrub{flex:1;min-width:160px;accent-color:var(--ball)}
  .speed{display:flex;gap:3px}
  .speed button{padding:4px 8px;font-family:var(--mono);font-size:11px}
  .speed button[aria-pressed=true]{border-color:var(--ball);color:var(--ball)}

  .legend{display:flex;gap:8px;flex-wrap:wrap;font-size:11.5px;color:var(--ink-dim)}
  .legend span{border:1px solid var(--line-soft);border-radius:3px;padding:3px 8px}
  .legend kbd{font-family:var(--mono);color:var(--ink);margin-right:5px}

  .rail{border-left:1px solid var(--line-soft);display:flex;flex-direction:column;
    min-height:0;background:var(--panel)}
  .board{padding:16px 16px 12px;border-bottom:1px solid var(--line-soft)}
  .cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .card{position:relative;background:var(--table);border:1px solid var(--line-soft);
    border-radius:4px;padding:8px 0 10px;text-align:center}
  .card.serving{border-color:var(--ball)}
  .card .who{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-dim);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 8px}
  .card .nums{display:flex;align-items:baseline;justify-content:center;gap:12px;margin-top:2px}
  .card .g{font-family:var(--disp);font-size:30px;line-height:1;color:var(--ink-dim)}
  .card .pts{font-family:var(--disp);font-size:58px;line-height:.98;font-variant-numeric:tabular-nums}
  .card.serving .pts{color:var(--score)}
  .card .cap{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-dim);opacity:.7}
  .boardmeta{display:flex;justify-content:space-between;margin-top:10px;font-size:11.5px;color:var(--ink-dim)}
  .boardmeta b{color:var(--ink);font-weight:500}
  .boardmeta .rule{color:var(--warn)}

  .streamhead{display:flex;justify-content:space-between;align-items:center;padding:9px 14px;
    border-bottom:1px solid var(--line-soft);font-size:11px;letter-spacing:.13em;
    text-transform:uppercase;color:var(--ink-dim)}
  .stream{flex:1;overflow-y:auto;min-height:80px}
  .ev{display:grid;grid-template-columns:60px 1fr auto auto;gap:8px;align-items:center;
    padding:6px 14px;border-bottom:1px solid rgba(32,72,110,.5);cursor:pointer;font-size:12.5px}
  .ev:hover{background:var(--table-2)}
  .ev time{font-family:var(--mono);font-size:11.5px;color:var(--ink-dim)}
  .ev .lbl{display:flex;align-items:center;gap:7px;min-width:0}
  .ev .dot{width:6px;height:6px;border-radius:50%;background:var(--line);flex:none}
  .ev.serve .dot{background:var(--ball)}
  .ev.game .dot{background:var(--warn)}
  .ev .sc{font-family:var(--mono);font-size:11.5px;color:var(--ink-dim)}
  .ev .sc em{color:var(--warn);font-style:normal}
  .ev .kill{border:0;background:none;color:var(--ink-dim);cursor:pointer;padding:0 3px;font-size:15px}
  .ev .kill:hover{color:var(--ball)}
  .streamempty{padding:22px 14px;color:var(--ink-dim);font-size:12.5px;line-height:1.7}

  .cutout{border-top:1px solid var(--line-soft);padding:11px 14px;font-size:12px;
    color:var(--ink-dim);display:flex;flex-direction:column;gap:5px}
  .cutout .row{display:flex;justify-content:space-between}
  .cutout b{color:var(--ink);font-family:var(--mono);font-weight:400}
  .cutout .save{color:var(--ball)}
  .pads{display:flex;gap:8px 10px;margin-top:4px;flex-wrap:wrap}
  .pads .ctl{font-size:11px}

  .render{border-top:1px solid var(--line-soft);padding:11px 14px;display:flex;
    flex-direction:column;gap:8px;background:var(--table-2)}
  .render .line{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink-dim)}
  .render .out{font-family:var(--mono);font-size:11px;color:var(--ink-dim);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left}
  .go{width:100%;padding:9px;font-size:13px;letter-spacing:.06em}
  .bar{height:5px;background:var(--table);border-radius:3px;overflow:hidden}
  .bar i{display:block;height:100%;width:0;background:var(--ball);
    transition:width .3s linear}
  .bar.done i{background:var(--good)}
  .bar.bad i{background:var(--bad)}
  .pmeta{display:flex;justify-content:space-between;font-size:11px;
    font-family:var(--mono);color:var(--ink-dim)}
  .plog{font-family:var(--mono);font-size:10.5px;color:var(--ink-dim);
    max-height:76px;overflow-y:auto;line-height:1.55;white-space:pre-wrap;word-break:break-all}
  .plog.bad{color:var(--bad)}
  .note{font-size:11px;color:var(--warn);line-height:1.5}

  @media (max-width:960px){
    .shell{grid-template-columns:1fr;grid-template-rows:auto auto auto 1fr;height:auto}
    .rail{border-left:0;border-top:1px solid var(--line-soft)}
    .stage{height:54vh}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none !important}}
</style>

<div class="shell">
  <header>
    <div class="brand"><i></i>ttcut<small>__VERSION__</small></div>
    <button class="btn" id="pick">Load video</button>
    <span class="srcname" id="srcname">nothing loaded</span>
    <div class="ctl">A<input type="text" id="nameA" value="Player A"></div>
    <div class="ctl">B<input type="text" id="nameB" value="Player B"></div>
    <div class="ctl">First serve<select id="firstServer"><option value="0">A</option><option value="1">B</option></select></div>
    <span style="flex:1"></span>
    <label class="btn file">Load tags<input type="file" id="load" accept=".json"></label>
    <button class="btn" id="save">Export JSON</button>
  </header>

  <div class="setbar">
    <div class="grp"><span class="tag">fps</span>
      <input type="number" id="fps" value="30" min="1" max="240" step="1"></div>
    <div class="grp"><span class="tag">game to</span>
      <input type="number" id="target" value="11" min="1" step="1"><span class="tag">pts</span></div>
    <div class="grp"><span class="tag">format</span>
      <select id="deuce">
        <option value="standard">Standard · by 2</option>
        <option value="capped">Capped · first to cap</option>
      </select>
      <span class="tag">cap</span><input type="number" id="cap" value="12" min="2" step="1" disabled></div>
    <div class="grp"><span class="tag">games</span>
      <input type="number" id="sgA" value="0" min="0" step="1">
      <span class="tag">:</span>
      <input type="number" id="sgB" value="0" min="0" step="1"></div>
    <div class="grp"><span class="tag">handicap</span>
      <input type="number" id="spA" value="0" min="0" step="1">
      <span class="tag">:</span>
      <input type="number" id="spB" value="0" min="0" step="1">
      <select id="scope">
        <option value="every">Every game</option>
        <option value="first">First game</option>
      </select></div>
    <div class="grp"><span class="tag">accent</span>
      <input type="color" id="accent" value="#FF7A18"
             title="Shared by the point digits and the bar beside the names">
      <button class="btn" id="accentReset" title="Back to the default orange">Reset</button></div>
  </div>

  <div class="stage">
    <div class="screen" id="screen">
      <div class="empty" id="empty">
        Click <b>Load video</b> at the top left to choose a match recording.<br>
        The file is read straight from this computer and is never uploaded anywhere.
      </div>
    </div>

    <div class="transport">
      <div class="tc"><span id="tc">00:00.00</span><small id="frameno">frame 0</small></div>
      <input type="range" class="scrub" id="scrub" min="0" max="0" step="0.001" value="0" aria-label="Playback position">
      <div class="speed" id="speed">
        <button class="btn" data-r="0.5">.5×</button>
        <button class="btn" data-r="1" aria-pressed="true">1×</button>
        <button class="btn" data-r="1.5">1.5×</button>
        <button class="btn" data-r="2">2×</button>
      </div>
    </div>

    <div class="legend">
      <span><kbd>space</kbd>play / pause</span>
      <span><kbd>S</kbd>serve</span>
      <span><kbd>A</kbd>point A</span>
      <span><kbd>B</kbd>point B</span>
      <span><kbd>N</kbd>new game</span>
      <span><kbd>Z</kbd>undo</span>
      <span><kbd>← →</kbd>frame</span>
      <span><kbd>⇧← →</kbd>1 s</span>
      <span><kbd>⌥← →</kbd>5 s</span>
      <span><kbd>1-4</kbd>speed</span>
    </div>
  </div>

  <div class="rail">
    <div class="board">
      <div class="cards">
        <div class="card" id="cardA">
          <div class="who" id="whoA">Player A</div>
          <div class="nums"><span class="g" id="gmA">0</span><span class="pts" id="ptsA">0</span></div>
          <div class="cap">games · points</div>
        </div>
        <div class="card" id="cardB">
          <div class="who" id="whoB">Player B</div>
          <div class="nums"><span class="g" id="gmB">0</span><span class="pts" id="ptsB">0</span></div>
          <div class="cap">games · points</div>
        </div>
      </div>
      <div class="boardmeta">
        <span>Game <b id="gameNo">1</b><span id="ruleNote" class="rule"></span></span>
        <span><b id="expServer">A</b> to serve</span>
      </div>
    </div>

    <div class="streamhead"><span>events</span><span id="evcount">0</span></div>
    <div class="stream" id="stream"></div>

    <div class="cutout">
      <div class="row"><span>Cuttable segments</span><b id="cutN">0</b></div>
      <div class="row"><span>Time removed</span><b class="save" id="cutT">0.0s</b></div>
      <div class="row"><span>Output length</span><b id="outT">0.0s</b></div>
      <div class="pads">
        <div class="ctl">after point<input type="number" id="tailPad" value="1.0" step="0.1" min="0">s</div>
        <div class="ctl">before serve<input type="number" id="leadPad" value="0.3" step="0.1" min="0">s</div>
        <div class="ctl">min cut<input type="number" id="minCut" value="2.0" step="0.1" min="0">s</div>
      </div>
    </div>

    <div class="render">
      <div class="line">
        <span>Quality</span>
        <select id="quality">
          <option value="fast">Fast</option>
          <option value="high" selected>Standard</option>
          <option value="max">Best (slow)</option>
        </select>
        <label class="ctl" style="margin-left:auto"><input type="checkbox" id="cutLets">cut lets too</label>
      </div>
      <div class="out" id="outPath">—</div>
      <button class="btn hot go" id="go" disabled>Render video</button>
      <div id="progWrap" hidden>
        <div class="bar" id="bar"><i></i></div>
        <div class="pmeta"><span id="pctTxt">0%</span><span id="etaTxt"></span></div>
      </div>
      <div class="plog" id="plog" hidden></div>
      <div class="note" id="note" hidden></div>
    </div>
  </div>
</div>

<script>
(() => {
  const VERSION = "__VERSION__";
  const $ = id => document.getElementById(id);
  const screenEl = $('screen'), emptyEl = $('empty');

  let video = null, events = [], mediaTime = 0, srcName = '', srcPath = '';
  let outPath = '', ffmpegOK = false, polling = null;

  const num = (id, d) => { const v = +$(id).value; return isFinite(v) ? v : d; };
  const fps    = () => Math.max(1, num('fps', 30));
  const target = () => Math.max(1, num('target', 11));
  const deuce  = () => $('deuce').value;
  const capVal = () => Math.max(target(), num('cap', target() + 1));
  const scope  = () => $('scope').value;
  const firstServer = () => +$('firstServer').value;
  const names  = () => [$('nameA').value || 'A', $('nameB').value || 'B'];
  const startGames  = () => [Math.max(0, num('sgA', 0)), Math.max(0, num('sgB', 0))];
  const startPoints = () => [Math.max(0, num('spA', 0)), Math.max(0, num('spB', 0))];

  const fmt = t => {
    if (!isFinite(t) || t < 0) t = 0;
    const m = Math.floor(t / 60), s = t - m * 60;
    return String(m).padStart(2,'0') + ':' + s.toFixed(2).padStart(5,'0');
  };
  const frameOf = t => Math.round(t * fps());
  const mmss = s => {
    if (s == null) return '';
    s = Math.round(s);
    return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
  };

  /* ───────────────────────── Payload sent to Python
     Scoring and cut statistics are computed in Python only; the interface no
     longer keeps its own copy of that logic. */
  function docPayload() {
    const nm = names(), sg = startGames(), sp = startPoints();
    return {
      version: 2, generator: 'ttcut ' + VERSION, source: srcName, fps: fps(),
      pointsPerGame: target(),
      players: {A: nm[0], B: nm[1]},
      firstServer: firstServer() === 0 ? 'A' : 'B',
      format: {pointsPerGame: target(), deuce: deuce(), cap: capVal()},
      start: {games: {A: sg[0], B: sg[1]},
              points: {A: sp[0], B: sp[1]},
              handicapScope: scope()},
      pads: {tail: num('tailPad', 1), lead: num('leadPad', 0.3)},
      scoreboard: {accent: $('accent').value},
      events: events.map(e => ({
        t: +e.t.toFixed(3), frame: frameOf(e.t), type: e.type,
        ...(e.winner === undefined ? {} : {winner: e.winner})
      }))
    };
  }
  const optPayload = () => ({
    min_cut: num('minCut', 2), cut_lets: $('cutLets').checked,
    quality: $('quality').value
  });

  /* ───────────────────────── Video */
  $('pick').addEventListener('click', async () => {
    $('pick').disabled = true;
    try {
      const r = await fetch('/pick-video', {method: 'POST'});
      const d = await r.json();
      if (d.cancelled || !d.path) return;
      srcPath = d.path; srcName = d.name; outPath = d.defaultOut;
      $('srcname').textContent = d.name;
      $('srcname').title = d.path;
      $('outPath').textContent = d.defaultOut;
      if (d.info) {
        if (d.info.fps) $('fps').value = Math.round(d.info.fps * 100) / 100;
        $('srcname').title = `${d.path}\n${d.info.w}×${d.info.h} · ${d.info.fps}fps · ${d.info.codec}`;
      }
      mountVideo();
      updateGo();
      refresh();
    } catch (e) {
      banner('Could not load the video: ' + e.message);
    } finally { $('pick').disabled = false; }
  });

  function mountVideo() {
    if (video) { video.pause(); video.remove(); }
    video = document.createElement('video');
    video.src = '/video?t=' + Date.now();
    video.preload = 'auto'; video.playsInline = true;
    emptyEl.style.display = 'none';
    screenEl.appendChild(video);
    video.addEventListener('loadedmetadata', () => {
      $('scrub').max = video.duration || 0; tick();
    });
    video.addEventListener('timeupdate', tick);
    video.addEventListener('seeked', tick);
    video.addEventListener('error', () => banner('This video will not play; the browser may not support its codec.'));
    pumpFrames();
  }

  function pumpFrames() {
    if (!video || !video.requestVideoFrameCallback) return;
    video.requestVideoFrameCallback((now, meta) => {
      mediaTime = meta.mediaTime; tick(); pumpFrames();
    });
  }
  function now() {
    if (!video) return 0;
    const t = (video.requestVideoFrameCallback && !video.seeking) ? mediaTime : video.currentTime;
    return isFinite(t) ? t : video.currentTime;
  }
  function tick() {
    if (!video) return;
    const t = now();
    $('tc').textContent = fmt(t);
    $('frameno').textContent = 'frame ' + frameOf(t);
    if (document.activeElement !== $('scrub')) $('scrub').value = t;
  }
  $('scrub').addEventListener('input', e => { if (video) video.currentTime = +e.target.value; });

  function seekBy(sec) {
    if (!video) return;
    video.pause();
    const t = Math.max(0, Math.min(video.duration || 0, now() + sec));
    video.currentTime = Math.round(t * fps()) / fps();
  }
  function setRate(r) {
    if (video) video.playbackRate = r;
    [...$('speed').children].forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.r === r)));
  }
  $('speed').addEventListener('click', e => {
    const b = e.target.closest('button'); if (b) setRate(+b.dataset.r);
  });

  /* ───────────────────────── Events */
  function add(type, winner) {
    if (!video) return;
    const t = Math.round(now() * fps()) / fps();
    events.push(winner === undefined ? {t, type} : {t, type, winner});
    events.sort((a,b) => a.t - b.t);
    refresh();
  }
  function undo() {
    if (!events.length) return;
    let idx = 0;
    for (let i = 1; i < events.length; i++) if (events[i].t >= events[idx].t) idx = i;
    events.splice(idx, 1); refresh();
  }

  /* ───────────────────────── Ask Python for the score */
  let seq = 0, timer = null;
  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(doRefresh, 50);
  }
  async function doRefresh() {
    const mine = ++seq;
    $('cap').disabled = deuce() !== 'capped';
    try {
      const r = await fetch('/fold', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc: docPayload(), opt: optPayload()})
      });
      const st = await r.json();
      if (mine !== seq) return;               // drop stale responses
      paint(st);
      banner(null);
    } catch (e) {
      banner('Cannot reach the local service. Is the terminal window still open?');
    }
  }

  /* ───────────────────────── Painting */
  function paint(st) {
    const nm = names(), cur = st.cur;
    $('whoA').textContent = nm[0]; $('whoB').textContent = nm[1];
    $('ptsA').textContent = cur.a;  $('ptsB').textContent = cur.b;
    $('gmA').textContent = cur.gA;  $('gmB').textContent = cur.gB;
    $('gameNo').textContent = cur.gameNo;
    $('ruleNote').textContent = deuce() === 'capped' ? ` · cap ${capVal()}` : '';
    $('expServer').textContent = nm[cur.server];
    $('cardA').classList.toggle('serving', cur.server === 0);
    $('cardB').classList.toggle('serving', cur.server === 1);

    const stream = $('stream');
    $('evcount').textContent = events.length;
    if (!events.length) {
      stream.innerHTML = '<div class="streamempty">No events yet.<br>Play the video and press <b style="color:var(--ink)">S</b> the moment the ball leaves the bat on a serve, then <b style="color:var(--ink)">A</b> or <b style="color:var(--ink)">B</b> when the point is won.</div>';
    } else {
      let prevServe = false, html = '';
      events.forEach((e, i) => {
        const s = st.snaps[i];
        let label, cls;
        if (e.type === 'serve') { cls = 'serve'; label = prevServe ? 'Serve · let' : 'Serve'; }
        else if (e.type === 'game') { cls = 'game'; label = 'New game'; }
        else { cls = 'point'; label = 'Point ' + nm[e.winner === 'A' ? 0 : 1]; }
        prevServe = e.type === 'serve';
        const sc = s ? (s.won ? `<em>${s.gA}–${s.gB} games</em>` : `${s.a}–${s.b}`) : '';
        html += `<div class="ev ${cls}" data-i="${i}">
          <time>${fmt(e.t)}</time>
          <span class="lbl"><i class="dot"></i>${label}</span>
          <span class="sc">${s && s.won ? `${s.a}–${s.b}` : ''}</span>
          <span class="sc">${sc}</span>
          <button class="kill" data-kill="${i}" title="Delete">×</button>
        </div>`;
      });
      stream.innerHTML = html;
      stream.scrollTop = stream.scrollHeight;
    }

    const c = st.cuts || {};
    $('cutN').textContent = c.n || 0;
    $('cutT').textContent = (c.seconds || 0).toFixed(1) + 's';
    $('outT').textContent = (c.outSeconds || 0).toFixed(1) + 's'
      + (c.pct ? ` · ${c.pct}% shorter` : '');
    updateGo(st.ok);
  }

  function updateGo(planOK) {
    const running = polling !== null;
    const ok = !!srcPath && ffmpegOK && events.length > 0 && planOK !== false;
    $('go').disabled = running || !ok;
    if (!ffmpegOK && srcPath) $('go').textContent = 'ffmpeg not found';
    else $('go').textContent = running ? 'Rendering…' : 'Render video';
  }

  function banner(msg) {
    const n = $('note');
    if (!msg) { n.hidden = true; return; }
    n.hidden = false; n.textContent = msg;
  }

  $('stream').addEventListener('click', e => {
    const k = e.target.closest('[data-kill]');
    if (k) { events.splice(+k.dataset.kill, 1); refresh(); return; }
    const row = e.target.closest('.ev');
    if (row && video) { video.pause(); video.currentTime = events[+row.dataset.i].t; }
  });

  function paintAccent() {
    document.documentElement.style.setProperty('--score', $('accent').value);
  }
  $('accent').addEventListener('input', paintAccent);
  $('accentReset').addEventListener('click', () => {
    $('accent').value = '#FF7A18'; paintAccent();
  });

  ['nameA','nameB','firstServer','target','fps','tailPad','leadPad','minCut',
   'deuce','cap','sgA','sgB','spA','spB','scope','cutLets']
    .forEach(id => $(id).addEventListener('input', refresh));

  /* ───────────────────────── Keyboard */
  addEventListener('keydown', e => {
    const el = document.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
    if (e.metaKey || e.ctrlKey) return;
    const step = e.altKey ? 5 : e.shiftKey ? 1 : 1 / fps();
    switch (e.key) {
      case ' ':          if (video) video.paused ? video.play() : video.pause(); break;
      case 'ArrowLeft':  seekBy(-step); break;
      case 'ArrowRight': seekBy(step); break;
      case 's': case 'S': add('serve'); break;
      case 'a': case 'A': add('point', 'A'); break;
      case 'b': case 'B': add('point', 'B'); break;
      case 'n': case 'N': add('game'); break;
      case 'z': case 'Z': undo(); break;
      case '1': setRate(0.5); break;
      case '2': setRate(1); break;
      case '3': setRate(1.5); break;
      case '4': setRate(2); break;
      default: return;
    }
    e.preventDefault();
  });

  /* ───────────────────────── Rendering */
  $('go').addEventListener('click', async () => {
    $('go').disabled = true;
    $('plog').hidden = true; $('plog').classList.remove('bad');
    $('bar').classList.remove('done','bad');
    $('progWrap').hidden = false;
    setBar(0, 'starting ffmpeg…');
    try {
      const r = await fetch('/render', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc: docPayload(), opt: optPayload(), out: outPath})
      });
      const d = await r.json();
      if (d.error) { fail(d.error); return; }
      if (d.notes && d.notes.length) {
        $('plog').hidden = false; $('plog').textContent = d.notes.join('\n');
      }
      startPolling();
    } catch (e) { fail(e.message); }
  });

  function setBar(pct, right) {
    $('bar').querySelector('i').style.width = Math.max(0, Math.min(100, pct)) + '%';
    $('pctTxt').textContent = pct.toFixed(0) + '%';
    $('etaTxt').textContent = right || '';
  }
  function fail(msg) {
    $('bar').classList.add('bad');
    $('plog').hidden = false; $('plog').classList.add('bad');
    $('plog').textContent = msg;
    polling = null; updateGo();
  }

  function startPolling() {
    polling = setInterval(async () => {
      try {
        const s = await (await fetch('/render/status')).json();
        if (s.state === 'running') {
          setBar(s.pct, (s.eta != null ? '~' + mmss(s.eta) + ' left' : '') +
                        (s.speed ? ' · ' + s.speed : ''));
          updateGo();
        } else {
          clearInterval(polling); polling = null;
          if (s.state === 'done') {
            setBar(100, mmss(s.elapsed) + ' elapsed');
            $('bar').classList.add('done');
            $('plog').hidden = false;
            $('plog').textContent = 'Done -> ' + s.out;
          } else if (s.state === 'cancelled') {
            setBar(s.pct, 'cancelled');
          } else {
            fail((s.message || 'Render failed') + '\n' + (s.log || []).join('\n'));
          }
          updateGo();
        }
      } catch (e) {
        clearInterval(polling); polling = null;
        fail('Lost the connection to the local service.');
      }
    }, 400);
    updateGo();
  }

  /* ───────────────────────── Save and load */
  $('save').addEventListener('click', async () => {
    const doc = docPayload();
    try {                       // include the cut list in the export too, matching the V1.22 format
      const st = await (await fetch('/fold', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc, opt: optPayload()})
      })).json();
      if (st.cutList) doc.cuts = st.cutList;
    } catch (e) { /* if it fails, leave it out; the main data is unaffected */ }
    const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], {type:'application/json'}));
    const a = document.createElement('a');
    a.href = url;
    a.download = (srcName.replace(/\.[^.]+$/, '') || 'match') + '.tags.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  $('load').addEventListener('change', e => {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      try {
        const d = JSON.parse(r.result);
        events = (d.events || []).map(x => ({
          t: x.t, type: x.type,
          ...(x.winner === undefined ? {} : {winner: x.winner})
        })).sort((a,b) => a.t - b.t);
        if (d.fps) $('fps').value = d.fps;
        if (d.players) { $('nameA').value = d.players.A; $('nameB').value = d.players.B; }
        if (d.firstServer) $('firstServer').value = d.firstServer === 'B' ? '1' : '0';
        if (d.pads) { $('tailPad').value = d.pads.tail; $('leadPad').value = d.pads.lead; }

        const F = d.format || {};
        $('target').value = F.pointsPerGame || d.pointsPerGame || 11;
        $('deuce').value  = F.deuce || 'standard';
        $('cap').value    = F.cap || (+$('target').value + 1);
        const S = d.start || {}, g = S.games || {}, p = S.points || {};
        $('sgA').value = g.A || 0; $('sgB').value = g.B || 0;
        $('spA').value = p.A || 0; $('spB').value = p.B || 0;
        $('scope').value = S.handicapScope || 'every';
        $('accent').value = (d.scoreboard || {}).accent || '#FF7A18';
        paintAccent();
        refresh();
      } catch (err) {
        banner('No usable tag data in that file. Is it a JSON exported by this tool?');
      }
    };
    r.readAsText(f);
    e.target.value = '';
  });

  /* ───────────────────────── Startup: pick the loaded state back up, so a
     browser refresh does not lose anything */
  (async () => {
    try {
      const s = await (await fetch('/state')).json();
      ffmpegOK = !!s.ffmpeg;
      if (s.video) {
        srcPath = s.video; srcName = s.videoName;
        $('srcname').textContent = s.videoName;
        $('srcname').title = s.video;
        mountVideo();
      }
      if (!s.ffmpeg) banner('ffmpeg not found. Tagging and JSON export still work, but rendering does not.');
      if (s.job && s.job.state === 'running') { $('progWrap').hidden = false; startPolling(); }
    } catch (e) { /* service not up yet, never mind */ }
    refresh();
  })();
})();
</script>
"""

# ─────────────────────────────────────────── HTTP server

def guess_type(path):
    t, _ = mimetypes.guess_type(path)
    if not t:
        ext = os.path.splitext(path)[1].lower()
        t = {".mov": "video/quicktime", ".mp4": "video/mp4",
             ".m4v": "video/x-m4v", ".mkv": "video/x-matroska"}.get(ext, "video/mp4")
    return t


class Handler(BaseHTTPRequestHandler):
    server_version = f"ttcut/{VERSION}"

    def log_message(self, *a):
        pass                                    # do not spam the console with every range request

    # ── Helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(body)

    def _write(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass                                # browsers abort range requests all the time

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ── Routes
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._html()
        if p == "/video":
            return self._video()
        if p == "/state":
            with STATE_LOCK:
                v = STATE["video"]
                job = STATE["job"]
            return self._json(dict(
                video=v, videoName=os.path.basename(v) if v else None,
                ffmpeg=STATE["ffmpeg"],
                job=job.snapshot() if job else None))
        if p == "/render/status":
            with STATE_LOCK:
                job = STATE["job"]
            return self._json(job.snapshot() if job else dict(state="idle"))
        self.send_error(404)

    def _same_origin(self):
        """Block other web pages from poking this local service (for instance to
        pop up the file dialog). A same-origin fetch always sends Origin, so
        "present but mismatched" is refused."""
        o = self.headers.get("Origin")
        if o is None:
            return True
        host = self.headers.get("Host", "")
        return o in (f"http://{host}", f"https://{host}")

    def do_POST(self):
        p = urlparse(self.path).path
        if not self._same_origin():
            return self._json(dict(error="cross-origin request rejected"), 403)
        try:
            if p == "/fold":
                return self._fold()
            if p == "/pick-video":
                return self._pick()
            if p == "/render":
                return self._render()
            if p == "/render/cancel":
                return self._cancel()
        except Exception as ex:
            return self._json(dict(error=f"{type(ex).__name__}: {ex}"), 500)
        self.send_error(404)

    # ── Index page
    def _html(self):
        body = HTML.replace("__VERSION__", VERSION).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(body)

    # ── Video streaming (Range support is required, or scrubbing breaks and
    # Safari may refuse to play at all)
    def _video(self):
        with STATE_LOCK:
            path = STATE["video"]
        if not path or not os.path.isfile(path):
            return self.send_error(404, "no video loaded")
        size = os.path.getsize(path)
        ctype = guess_type(path)
        rng = self.headers.get("Range")

        start, end = 0, size - 1
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                s, e = m.group(1), m.group(2)
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                elif e:                          # bytes=-N means the last N bytes
                    start = max(0, size - int(e))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Scoring (single source of truth)
    def _fold(self):
        req = self._body()
        doc = req.get("doc") or {}
        opt = req.get("opt") or {}
        pl = plan(doc, opt)
        sc = pl["scoring"]
        res = dict(cur=sc["cur"], snaps=sc["snaps"], ok=pl["ok"], reason=pl["reason"])
        if pl["ok"]:
            span, total = pl["span"], pl["total"]
            res["cuts"] = dict(
                n=len(pl["cuts"]),
                seconds=round(span - total, 1),
                dropped=len(pl["dropped"]),
                outSeconds=round(total, 1),
                pct=round((span - total) / span * 100) if span > 0 else 0)
            res["cutList"] = [{"from": round(f, 3), "to": round(t, 3)}
                              for f, t, _ in pl["cuts"]]
        else:
            res["cuts"] = dict(n=0, seconds=0.0, dropped=0, outSeconds=0.0, pct=0)
        return self._json(res)

    # ── Native file dialog
    def _pick(self):
        p = native_pick_video()
        if not p:
            return self._json(dict(cancelled=True))
        with STATE_LOCK:
            STATE["video"] = p
        info = probe(p, STATE["ffprobe"])
        return self._json(dict(
            path=p, name=os.path.basename(p),
            info=dict(w=info["w"], h=info["h"], fps=round(info["fps"], 2),
                      codec=info["codec"], duration=info["duration"],
                      bitrate=info["bitrate"]) if info else None,
            defaultOut=default_out(p)))

    # ── Render
    def _render(self):
        with STATE_LOCK:
            job = STATE["job"]
            video = STATE["video"]
            ffmpeg = STATE["ffmpeg"]
        if job and job.state == "running":
            return self._json(dict(error="A render is already running."), 409)
        if not video:
            return self._json(dict(error="No video loaded yet."), 400)
        if not ffmpeg:
            return self._json(dict(error="ffmpeg not found, cannot render."), 400)

        req = self._body()
        doc = req.get("doc") or {}
        opt = req.get("opt") or {}
        out = req.get("out") or default_out(video)
        out = os.path.abspath(out)

        pl = plan(doc, opt)
        if not pl["ok"]:
            return self._json(dict(error=pl["reason"]), 400)

        # Drop a copy of the tags next to the output, so a re-render or a
        # parameter change can always be reproduced
        try:
            with open(os.path.splitext(out)[0] + ".tags.json", "w",
                      encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        logs = []
        cmd, workdir, _ = build_render(doc, pl, video, out, opt, ffmpeg,
                                       STATE["ffprobe"], log=logs.append,
                                       progress=True)
        new = Job(out, pl["total"])
        new.log = logs
        with STATE_LOCK:
            STATE["job"] = new
        threading.Thread(target=run_job, args=(new, cmd, workdir),
                         daemon=True).start()
        return self._json(dict(ok=True, out=out, total=round(pl["total"], 1),
                               summary=summary_lines(pl, opt, video), notes=logs))

    def _cancel(self):
        with STATE_LOCK:
            job = STATE["job"]
        if job and job.state == "running" and job.proc:
            job.state = "cancelled"
            try:
                job.proc.terminate()
            except Exception:
                pass
        return self._json(dict(ok=True))


def default_out(video):
    return os.path.join(os.path.dirname(os.path.abspath(video)),
                        os.path.splitext(os.path.basename(video))[0] + ".cut.mp4")


def free_port(preferred=8770):
    for port in (preferred, 0):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            p = s.getsockname()[1]
            s.close()
            return p
        except OSError:
            continue
    return 8770


def serve(open_browser=True, port=None, ffmpeg_hint=None):
    ff = find_ffmpeg(ffmpeg_hint)
    STATE["ffmpeg"] = ff
    if ff:
        probe_path = os.path.join(os.path.dirname(ff),
                                  "ffprobe.exe" if IS_WIN else "ffprobe")
        STATE["ffprobe"] = probe_path if os.path.isfile(probe_path) else "ffprobe"

    port = port or free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"

    print(f"\nttcut {VERSION}")
    print(f"UI        {url}")
    print(f"ffmpeg    {ff or 'not found -- tagging and JSON export work, rendering does not'}")
    if not ff:
        print("          Mac: brew install ffmpeg-full")
        print("          Windows: put ffmpeg.exe next to this script")
    print("\nListening on 127.0.0.1 only, not reachable from outside. Ctrl-C to stop.\n")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ─────────────────────────────────────────── Command-line render (same behaviour as V1.22)

def cli_render(args):
    doc = json.load(open(args.tags, encoding="utf-8"))
    opt = dict(lead=args.lead, tail=args.tail, min_cut=args.min_cut,
               cut_lets=args.cut_lets, let_tail=args.let_tail,
               quality=args.quality, encoder=args.encoder, crf=args.crf,
               preset=args.preset, bitrate=args.bitrate, fps=args.fps,
               hdr=args.hdr, size=args.size, hwaccel=args.hwaccel, font=args.font,
               accent=args.accent)

    pl = plan(doc, opt)
    out = args.out or default_out(args.video)

    print(f"\nttcut {VERSION}")
    if not pl["ok"]:
        sys.exit(pl["reason"])
    for line in summary_lines(pl, opt, args.video)[:5]:
        print(line)
    print()
    for line in summary_lines(pl, opt, args.video)[5:]:
        print(line)
    if pl["dropped"]:
        print(f"\nSkipped {len(pl['dropped'])} cuts that were too short (keeping them beats a jump cut):")
        for f, t, why in pl["dropped"]:
            print(f"   {ts(f)} → {ts(t)}   {t - f:.2f}s   {why}")

    if args.dry_run:
        print("\nKept segments:")
        for i, (s, e) in enumerate(pl["keeps"]):
            print(f"   {i + 1:2d}  {ts(s)} → {ts(e)}   {e - s:6.2f}s")
        print()
        return

    ffmpeg = find_ffmpeg(args.ffmpeg, os.path.dirname(os.path.abspath(args.video)))
    if not ffmpeg:
        sys.exit("\nffmpeg not found.\n"
                 "  Mac    : brew install ffmpeg-full\n"
                 "  Windows: put ffmpeg.exe next to this script, or point at it with "
                 "--ffmpeg \"C:\\ffmpeg\\bin\"")
    ffprobe = os.path.join(os.path.dirname(ffmpeg),
                           "ffprobe.exe" if IS_WIN else "ffprobe")
    if not os.path.isfile(ffprobe):
        ffprobe = "ffprobe"
    print(f"ffmpeg    {ffmpeg}")
    print()

    cmd, workdir, flt = build_render(doc, pl, args.video, out, opt,
                                     ffmpeg, ffprobe, log=print)
    print("\n" + " ".join(
        (c if len(c) < 60 else f"<filter graph, {len(c)} chars, see {flt}>") for c in cmd) + "\n")
    subprocess.run(cmd, check=True, cwd=workdir)
    print(f"\nDone -> {out}")


def main():
    for stream in (sys.stdout, sys.stderr):      # the Windows console is not UTF-8 by default
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        description=f"ttcut {VERSION} — table tennis tagging and cutting. "
                    f"With no arguments it opens the interface.")
    p.add_argument("-v", "--version", action="version", version=f"ttcut {VERSION}")
    p.add_argument("tags", nargs="?", help="tags JSON (omit to open the interface)")
    p.add_argument("video", nargs="?", help="source video (omit to open the interface)")
    p.add_argument("-o", "--out", default=None)
    p.add_argument("--lead", type=float, default=None, help="seconds kept before each serve (defaults to the JSON)")
    p.add_argument("--tail", type=float, default=None, help="seconds kept after each point (defaults to the JSON)")
    p.add_argument("--min-cut", type=float, default=DEFAULT_MIN_CUT,
                   help="cuts shorter than this are left alone, to avoid pointless jump cuts")
    p.add_argument("--cut-lets", action="store_true", help="also cut the ball retrieval between lets")
    p.add_argument("--let-tail", type=float, default=1.5, help="seconds kept after a let")

    g = p.add_argument_group("quality")
    g.add_argument("--quality", choices=list(QUALITY), default="high",
                   help="fast, high (default), max (libx264 CRF, much slower)")
    g.add_argument("--encoder", default=None,
                   help="Mac: h264_videotoolbox / Windows: h264_nvenc, h264_qsv, "
                        "h264_amf / libx264 (pure CPU, best and slowest)")
    g.add_argument("--crf", type=int, default=None, help="override the quality value; lower is better")
    g.add_argument("--preset", default=None, help="libx264 preset")
    g.add_argument("--bitrate", default=None, help="override the bitrate, e.g. 40M")
    g.add_argument("--fps", default="source", help="source = follow the source, or give a number")
    g.add_argument("--hdr", choices=["auto", "tonemap", "keep", "ignore"], default="auto")
    g.add_argument("--size", default=None, help="override the resolution, e.g. 1920x1080")
    g.add_argument("--hwaccel", default="auto",
                   help="hardware decoding: auto (videotoolbox on Mac) / none / cuda / qsv")

    s = p.add_argument_group("interface")
    s.add_argument("--port", type=int, default=None, help="port to listen on")
    s.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")

    p.add_argument("--accent", default=None,
                   help=f"scoreboard accent colour (point digits and side bar), "
                        f"e.g. \"#FF7A18\". Default {DEFAULT_ACCENT}")
    p.add_argument("--font", default=FONT_NAME, help="font name used for the scoreboard names")
    p.add_argument("--ffmpeg", default=None,
                   help="path to ffmpeg.exe or its folder (when it is not on PATH)")
    p.add_argument("--dry-run", action="store_true", help="print the cut list only, do not render")
    args = p.parse_args()

    if args.tags and args.video:
        cli_render(args)
    elif args.tags or args.video:
        p.error("a command-line render needs both the tags JSON and the video; "
                "to open the interface, pass no arguments.")
    else:
        serve(open_browser=not args.no_browser, port=args.port,
              ffmpeg_hint=args.ffmpeg)


if __name__ == "__main__":
    main()
