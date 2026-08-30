#!/usr/bin/env python3
"""
ttcut V2 — 桌球比賽影片：標記、剪去撿球、疊上常駐計分板，一個工具做完。

Copyright (c) 2026 MikaDD (Taiwan)
以 MIT 授權釋出，完整條款見同目錄的 LICENSE。

開發過程使用 Anthropic Claude 協助撰寫程式碼。
本工具透過外部指令呼叫 ffmpeg，不含也不散布 ffmpeg 本身；
ffmpeg 有自己的授權，請自行至 https://ffmpeg.org 取得。

版本規則
    小改動 +0.1（V1 → V1.1 → V1.2 …）
    架構或輸出格式的重大變更才進位到 V2

更新紀錄
    V2.2 2026-08-30
        · 加寬數字輸入欄位：緩衝秒數（0.5、1.0）與影格率（29.97）原本會被截掉
    V2.1 2026-08-30
        · 計分板強調色可調：得分數字與名字左側裝飾條連動改色，兩處一起換
        · 顏色存進標記 JSON 的 scoreboard.accent，跟著檔案跨機器
        · 命令列新增 --accent，優先於 JSON 裡的設定
        · 未指定顏色時產出的 .ass 與 V2 逐字相同，其餘顏色不受影響
    V2  2026-08-24
        · 整合成單一工具：直接執行就開伺服器並打開瀏覽器，標記完按鈕即可產出成片
        · 計分邏輯統一由 Python 提供（/fold），tagger 的 JS 版本已移除
          —— 以後改規則只要改一處，不必再做 JS／Python 雙邊交叉驗證
        · 換發球輪次邏輯從 JS 移進 Python，納入同一份 fold()
        · 修正 tagger 與 ttcut 的最短剪點不一致（顯示用 0.15s、實際剪 2.0s）
          現在兩邊都用同一個值，且可在介面上調整
        · 影片改由 Python 提供並支援 HTTP Range，滑桿拖曳與 Safari 播放才正常
        · 影片路徑由 Python 開原生檔案對話框取得（瀏覽器拿不到真實路徑）
        · ffmpeg -progress 進度條，背景執行不卡 UI
        · 保留：匯出／讀入 JSON、命令列渲染路徑，兩者行為與 V1.22 相同
    V1.22 2026-08-20
        · 支援起始局數（一局一支影片、接續前面局數時用）
        · 支援起始分數／讓分，可選每局套用或僅第一局
        · 新增封頂制：10:10 後先到第 12 分者勝，不必贏兩分
        · JSON 新增 format / start 兩個區塊；舊檔缺欄位會自動退回預設值
    V1.2 2026-08-20
        · 修正致命 bug：V1.1 重構時漏掉 -c:v，ffmpeg 一直默默用預設的 libx264
        · 新增 --hwaccel，Mac 預設開 videotoolbox 硬體解碼
        · --hdr keep 遇到非 HDR 片源會自動退回
        · libx264/libx265 給了 --bitrate 就改走碼率模式
    V1.1 2026-08-20
        · 計分板：局數改成「填色底板 + 深色數字」
        · 畫質：影格率跟著片源、碼率依 解析度×影格率 換算、--quality 三檔、HDR 偵測
    V1  2026-08-20  首個可用版本

用法:
    python3 ttcut_v2_2.py                                   ← 開介面（一般用這個）
    python3 ttcut_v2_2.py IMG_1496.tags.json IMG_1496.MOV   ← 命令列直接渲染
    python3 ttcut_v2_2.py tags.json video.MOV --quality max --dry-run

需求:
    Python 3.8+ 與 ffmpeg（本版本不打包，請自行安裝）
    Mac    : brew install ffmpeg
             裝完可用 ffmpeg -filters | grep subtitles 確認字幕濾鏡在
    Windows: 下載 ffmpeg.exe 放在本腳本旁邊，或用 --ffmpeg 指定資料夾
"""

import argparse, json, mimetypes, os, platform, re, shutil, socket
import subprocess, sys, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

VERSION = "V2.2"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

# ─────────────────────────────────────────── 版面（以 1920×1080 為基準，會自動縮放）

BASE_W, BASE_H = 1920, 1080
PAD_L, PAD_B   = 64, 64
PANEL_W        = 480
ROW_H          = 54
COL_GAMES_X    = 312          # 局數欄左邊界（相對 panel 左緣）
COL_POINTS_X   = 392          # 得分欄左邊界
FS_NUM         = 44           # 局數與得分共用同一個字級

C_PANEL    = "&H40250A&"      # 深藍底（ASS 是 BGR）
C_ACCENT   = "&H187AFF&"      # 橘 #FF7A18
C_NAME     = "&HF9F2EA&"      # 近白
C_GAMES_BG = "&HEDE3D6&"      # 局數欄底色：亮色塊，跟深底做反差
C_GAMES    = "&H40250A&"      # 局數字：深藍壓在亮底上
C_POINTS   = "&H187AFF&"      # 得分：橘字壓在深底上
C_RULE     = "&H6E4820&"      # 分隔線

A_PANEL, A_CHIP, A_RULE = 0x1E, 0x00, 0x40    # 0x00 全不透明 → 0xFF 全透明

DEFAULT_ACCENT = "#FF7A18"    # 得分數字與名字左側裝飾條共用的強調色


def ass_colour(hex_rgb, fallback=C_ACCENT):
    """把 #RRGGBB 轉成 ASS 的 &HBBGGRR&。ASS 是 BGR 順序，寫反了顏色會整個跑掉。"""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (hex_rgb or "").strip())
    if not m:
        return fallback
    s = m.group(1).upper()
    return f"&H{s[4:6]}{s[2:4]}{s[0:2]}&"

# 各平台實際裝得到的中文字型
FONT_NAME = ("PingFang TC" if IS_MAC else
             "Microsoft JhengHei" if IS_WIN else "Noto Sans CJK TC")
FONT_NUM  = ("Helvetica Neue" if IS_MAC else
             "Segoe UI" if IS_WIN else "DejaVu Sans")

# 各平台的硬體編碼器
HW_ENCODER = "h264_videotoolbox" if IS_MAC else "libx264"

# --quality 三檔：碼率倍率 / CRF / x264 preset / 是否強制走純軟體編碼
QUALITY = {
    "fast": dict(scale=0.70, crf=21, preset="veryfast", force_sw=False),
    "high": dict(scale=1.00, crf=18, preset="medium",   force_sw=False),
    "max":  dict(scale=1.40, crf=16, preset="slow",     force_sw=True),
}

DEFAULT_MIN_CUT = 2.0         # 短於此秒數就不剪，避免無意義的跳接


# ─────────────────────────────────────────── 比分推導（唯一權威版本）

def fold_full(events, fmt, start, first_server=0):
    """把事件流摺成計分狀態。這是全專案唯一的計分實作，介面與命令列共用。

    fmt   = dict(target, deuce='standard'|'capped', cap)
    start = dict(games=(gA, gB), points=(a, b), scope='every'|'first')
    first_server = 0(A) / 1(B)

    回傳 dict:
        states  [(來源時間, gA, gB, a, b), ...]   給 ASS 計分板用，第一筆時間為 None
        snaps   與 events 等長；point 事件給 dict，其餘為 None，給介面事件列表用
        cur     目前狀態，含下一球該誰發

    換發球以「實際打過的分數」計算，讓分的起始分不計入輪次；
    雙方都到達 target-1 之後（deuce）改成每分換發。
    """
    T, mode, cap = fmt["target"], fmt["deuce"], fmt["cap"]
    gA, gB = start["games"]
    sp, scope = list(start["points"]), start["scope"]

    def init_pts(gi):
        return list(sp) if (scope == "every" or gi == 0) else [0, 0]

    gi = 0
    a, b = init_pts(0)
    server, served_in_turn = first_server, 0
    pending = False                        # 局末：先把比分留在畫面上，下一分才歸零
    states = [(None, gA, gB, a, b)]        # 開頭狀態，時間稍後補
    snaps = []

    def is_deuce():
        return a >= T - 1 and b >= T - 1

    def game_over():
        hi, lo = max(a, b), min(a, b)
        if mode == "capped" and hi >= cap:      # 10:10 後先到 cap 者勝，不必贏兩分
            return True
        return hi >= T and hi - lo >= 2          # 標準：11 分且領先 2 分

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
            server, served_in_turn = (first_server + gi + 1) % 2, 0   # 下一局換人先發
        else:
            served_in_turn += 1
            if served_in_turn >= (1 if is_deuce() else 2):
                server, served_in_turn = 1 - server, 0
        states.append((e["t"], gA, gB, a, b))

    cur = dict(a=a, b=b, gA=gA, gB=gB, gi=gi, server=server, pending=pending,
               gameNo=gA + gB + 1 - (1 if pending else 0))
    return dict(states=states, snaps=snaps, cur=cur)


def fold(events, fmt, start):
    """給 ASS 計分板用的狀態序列（與 V1.22 的 fold() 輸出完全相同）。"""
    return fold_full(events, fmt, start)["states"]


def read_format(doc):
    """從 JSON 讀賽制與起始比分，舊檔缺欄位時退回預設值。"""
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


# ─────────────────────────────────────────── 剪接區間

def build_cuts(events, tail, lead, min_cut, cut_lets, let_tail):
    """得分→下次發球 = 剪。發球→發球（重發）= 預設保留，可選擇也剪。"""
    cuts = []
    for i, e in enumerate(events):
        if e["type"] == "point":
            nxt = next((x for x in events[i + 1:] if x["type"] == "serve"), None)
            if nxt:
                cuts.append((e["t"] + tail, nxt["t"] - lead, "得分後撿球"))
        elif e["type"] == "serve" and cut_lets:
            nxt = events[i + 1] if i + 1 < len(events) else None
            if nxt and nxt["type"] == "serve":
                cuts.append((e["t"] + let_tail, nxt["t"] - lead, "重發後撿球"))

    kept, dropped = [], []
    for f, t, why in cuts:
        (kept if t - f >= min_cut else dropped).append((f, t, why))
    return sorted(kept), sorted(dropped)


def keeps_from_cuts(head, end, cuts):
    """剪點的補集 = 要保留的片段。"""
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
    """來源時間 → 成片時間。落在剪掉區間內的時間點會貼到下一段的起點。"""
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


# ─────────────────────────────────────────── ASS 計分板

def ts(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def rect(x, y, w, h, colour, alpha, layer=0):
    """實心矩形。注意 \\alpha 必須寫在 \\1a 前面，否則會把 \\1a 蓋掉。"""
    tags = (f"\\an7\\pos({x},{y})\\p1\\bord0\\shad0"
            f"\\alpha&H00&\\1c{colour}\\1a&H{alpha:02X}&")
    return layer, f"{{{tags}}}m 0 0 l {w} 0 l {w} {h} l 0 {h}"


def build_ass(states, src2out, total, names, width, height,
              font_name=FONT_NAME, font_num=FONT_NUM, accent=None):
    # 得分數字與名字左側的裝飾條共用同一個色，一起換
    c_accent = c_points = accent or C_ACCENT
    k = min(width / BASE_W, height / BASE_H)
    S = lambda v: round(v * k)                        # 縮放
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

    # ── 底板、局數色塊、橘色側邊、分隔線（整片常駐）
    # 同一 layer 內依出現順序疊，所以分隔線放最後才會壓在色塊上面
    for layer, d in [
        rect(x0, y0, pw, rh * 2, C_PANEL, A_PANEL),                    # 底板
        rect(gx, y0, gw, rh, C_GAMES_BG, A_CHIP),                      # 局數色塊（上）
        rect(gx, y0 + rh, gw, rh, C_GAMES_BG, A_CHIP),                 # 局數色塊（下）
        rect(x0, y0, S(5), rh * 2, c_accent, 0x00),                    # 側邊裝飾條
        rect(x0, y0 + rh, pw, max(1, S(2)), C_RULE, A_RULE),           # 橫向分隔
        rect(gx, y0, max(1, S(2)), rh * 2, C_RULE, A_RULE),
        rect(x0 + S(COL_POINTS_X), y0, max(1, S(2)), rh * 2, C_RULE, A_RULE),
    ]:
        add(layer, "Gfx", 0, total, d)

    # ── 選手名（常駐）
    for i, nm in enumerate(names):
        add(1, "Nm", 0, total,
            f"{{\\an4\\pos({name_x},{row_y[i]})\\1c{C_NAME}}}{nm}")

    # ── 局數與該局得分（隨事件變動）：同字級、同字重，靠底色分辨
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
    """依序找 ffmpeg：指定路徑 → PATH → 腳本／影片旁邊 → Windows 常見安裝位置。"""
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
    """回傳片源規格 dict，讀不到就回 None。"""
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
    """依像素率換算碼率。桌球是高動態畫面，抓得比一般影片寬。"""
    mpix_s = w * h * max(fps, 1) / 1e6          # 1080p30 ≈ 62 Mpix/s
    mbps = mpix_s * 0.30 * scale                # → 約 19 Mbps
    return f"{max(8.0, min(120.0, mbps)):.0f}M"


def video_encoder_args(enc, crf, preset, bitrate, pix_fmt, use_bitrate=False):
    """不同編碼器的品質參數長得都不一樣，這裡統一翻譯。
    注意第一組一定是 -c:v——V1.1 就是漏了它，害 ffmpeg 默默退回預設的 libx264。"""
    c = ["-c:v", enc]
    if enc in ("libx264", "libx265"):
        q = (["-b:v", bitrate, "-maxrate", bitrate,
              "-bufsize", f"{float(bitrate[:-1]) * 2:.0f}M"] if use_bitrate
             else ["-crf", str(crf)])
        return c + ["-preset", preset, *q, "-pix_fmt", pix_fmt]
    if "videotoolbox" in enc:      # VideoToolbox 沒有 CRF，只能給碼率
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
    """用 select 串流篩選，而不是 trim+concat——後者會把整段解碼結果緩衝在記憶體裡。
    先 fps 強制固定影格率，避免 iPhone VFR 造成聲畫不同步。
    tone-map 放在字幕之前，計分板顏色才不會被一起壓縮動態範圍。"""
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
    return f"{b / 1e6:.1f} Mbps" if b else "未知"


# ─────────────────────────────────────────── 規劃（介面與命令列共用）

class PlanError(Exception):
    pass


def plan(doc, opt):
    """從標記 JSON 算出剪接計畫。不碰 ffmpeg，介面即時預覽也用這個。"""
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
        return dict(ok=False, reason="事件裡沒有發球或得分，無法剪接。",
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
    """寫出 .ass 與 filter，組出 ffmpeg 指令。回傳 (cmd, workdir, info)。"""
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
        log("⚠ 讀不到影片解析度，計分板以 1920×1080 排版。")

    # ── 影格率：預設跟著片源，桌球快動作不要隨便砍成 30
    fps_opt = opt.get("fps", "source")
    if fps_opt and fps_opt != "source":
        fps_val, fps_arg = float(fps_opt), str(fps_opt)
    elif info and info["fps_frac"]:
        fps_val, fps_arg = info["fps"], info["fps_frac"]
    else:
        fps_val, fps_arg = float(doc.get("fps", 30)), str(doc.get("fps", 30))

    # ── 編碼器與 HDR
    enc = opt.get("encoder") or ("libx264" if q["force_sw"] else HW_ENCODER)
    hdr = is_hdr(info)
    mode = opt.get("hdr", "auto")
    if mode == "auto":
        mode = "keep" if (hdr and "hevc" in enc) else ("tonemap" if hdr else "ignore")
    if mode == "keep" and not hdr:
        log("⚠ 片源不是 HDR，--hdr keep 沒有意義，已忽略。")
        mode = "ignore"
    if mode == "keep" and "hevc" not in enc:
        log("⚠ --hdr keep 需要 HEVC 編碼器，改用 tone-map。")
        mode = "tonemap"
    tonemap = mode == "tonemap" and hdr
    pix_fmt = "p010le" if mode == "keep" else "yuv420p"

    bitrate = opt.get("bitrate") or auto_bitrate(w, h, fps_val, q["scale"])
    sw_bitrate = bool(opt.get("bitrate")) and enc in ("libx264", "libx265")

    hw = opt.get("hwaccel", "auto")
    if hw == "auto":
        hw = "videotoolbox" if IS_MAC else "none"

    # ── 畫質診斷：一眼看出瓶頸在片源還是在轉檔
    if info:
        depth = "10-bit" if is_10bit(info) else "8-bit"
        hdr_tag = f" · {info['trc']} HDR" if hdr else ""
        log(f"片源      {info['w']}×{info['h']} · {info['fps']:.2f} fps · "
            f"{info['codec']} · {depth}{hdr_tag} · {human_bitrate(info['bitrate'])}")
    log(f"輸出      {w}×{h} · {fps_val:.2f} fps · {enc} · {pix_fmt}"
        f"{' · 已 tone-map 成 SDR' if tonemap else ''}")
    log(f"品質      {opt.get('quality', 'high')} · "
        + ("CRF " + str(crf) + f" · preset {preset}"
           if enc in ("libx264", "libx265") and not sw_bitrate
           else "目標碼率 " + bitrate)
        + (f" · 硬體解碼 {hw}" if hw != "none" else ""))
    if info and info["bitrate"]:
        src_mbps = info["bitrate"] / 1e6
        if enc not in ("libx264", "libx265") or sw_bitrate:
            tgt = float(bitrate.rstrip("M"))
            if tgt < src_mbps * 0.8:
                log(f"⚠ 目標碼率低於片源（{tgt:.0f}M < {src_mbps:.0f}M），"
                    f"想保畫質可改 --bitrate {src_mbps * 1.2:.0f}M 或品質選 max")
        if src_mbps < 12 and w * h >= 1920 * 1080:
            log(f"⚠ 片源碼率只有 {src_mbps:.0f} Mbps，畫質上限本來就受限於拍攝端。")

    # ffmpeg 在輸出資料夾裡執行，濾鏡只吃檔名——Windows 的 C:\ 不必跳脫
    workdir = os.path.dirname(os.path.abspath(out)) or "."
    ass_name = os.path.basename(stem) + ".ass"
    flt_name = os.path.basename(stem) + ".filter.txt"

    # 強調色：命令列 --accent 優先，其次讀標記 JSON，都沒有就用預設橘
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
        f.write(fgraph)          # 留一份純供除錯查看

    colour_tags = (["-color_primaries", "bt2020", "-color_trc", info["trc"],
                    "-colorspace", "bt2020nc"] if mode == "keep" else
                   ["-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709"])
    tag = ["-tag:v", "hvc1"] if "hevc" in enc else []

    # 只讀到最後一個保留片段為止，不然 ffmpeg 會把整個檔案解碼完
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
    """命令列與介面共用的摘要文字。"""
    fmt, start = plan_d["fmt"], plan_d["start"]
    out = [f"來源      {os.path.basename(video)}",
           f"標記範圍  {ts(plan_d['head'])} → {ts(plan_d['end'])}   {plan_d['span']:.1f}s"]
    rule = ("標準 deuce（勝 2 分）" if fmt["deuce"] == "standard"
            else f"封頂制（10:10 後先到 {fmt['cap']} 分者勝）")
    bits = [f"每局 {fmt['target']} 分", rule]
    if any(start["games"]):
        bits.append(f"起始局數 {start['games'][0]}:{start['games'][1]}")
    if any(start["points"]):
        sc = "每局" if start["scope"] == "every" else "僅第一局"
        bits.append(f"讓分 {start['points'][0]}:{start['points'][1]}（{sc}）")
    out.append(f"賽制      {' · '.join(bits)}")
    out.append(f"事件      {plan_d['points']} 分 · {plan_d['serves']} 發球 · "
               f"{plan_d['serves'] - plan_d['points']} 次重發")
    out.append(f"參數      得分後留 {plan_d['tail']}s · 發球前留 {plan_d['lead']}s · "
               f"最短剪點 {opt.get('min_cut', DEFAULT_MIN_CUT)}s"
               f"{' · 重發也剪' if opt.get('cut_lets') else ''}")
    span, total = plan_d["span"], plan_d["total"]
    out.append(f"剪去      {len(plan_d['cuts'])} 段 · {span - total:.1f}s")
    out.append(f"成片      {total:.1f}s   壓縮 {(span - total) / span * 100:.0f}%"
               if span > 0 else "成片      0s")
    return out


# ─────────────────────────────────────────── 原生檔案對話框

_MAC_PICK = ('POSIX path of (choose file with prompt "選擇比賽影片"'
             ' of type {"public.movie","public.video"})')

_TK_PICK = (
    "import sys,tkinter,tkinter.filedialog as fd\n"
    "r=tkinter.Tk();r.withdraw();r.attributes('-topmost',True)\n"
    "p=fd.askopenfilename(title='選擇比賽影片',filetypes=["
    "('影片','*.mp4 *.mov *.MOV *.MP4 *.m4v *.avi *.mkv'),('全部','*.*')])\n"
    "sys.stdout.write(p or '')\n")


def native_pick_video():
    """開系統原生檔案對話框，回傳絕對路徑；使用者取消回 None。
    瀏覽器的 <input type=file> 拿不到真實路徑，而 ffmpeg 需要路徑，所以走這裡。"""
    try:
        if IS_MAC:
            r = subprocess.run(["osascript", "-e", _MAC_PICK],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None                      # 使用者按取消
            p = r.stdout.strip()
        else:
            r = subprocess.run([sys.executable, "-c", _TK_PICK],
                               capture_output=True, text=True, timeout=300)
            p = r.stdout.strip()
        return p if p and os.path.isfile(p) else None
    except Exception:
        return None


# ─────────────────────────────────────────── 伺服器狀態

STATE = {
    "video": None,          # 目前載入的影片絕對路徑
    "ffmpeg": None,
    "ffprobe": "ffprobe",
    "job": None,            # 進行中的渲染
}
STATE_LOCK = threading.Lock()


class Job:
    def __init__(self, out, total):
        self.out = out
        self.total = max(total, 0.001)
        self.pct = 0.0
        self.state = "running"      # running / done / error / cancelled
        self.message = "準備中…"
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
    """跑 ffmpeg 並解析 -progress 輸出。在背景執行緒中執行。"""
    try:
        job.proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as ex:
        job.state, job.message = "error", f"啟動 ffmpeg 失敗：{ex}"
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

    job.message = "編碼中…"
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
        job.message = "已取消"
        return
    if job.proc.returncode == 0:
        job.state, job.pct, job.message = "done", 100.0, "完成"
    else:
        job.state = "error"
        job.message = f"ffmpeg 結束碼 {job.proc.returncode}"
        job.log = err_tail[-12:]


# ─────────────────────────────────────────── 內嵌介面

HTML = r"""<meta charset="utf-8">
<title>ttcut __VERSION__ — 桌球回合標記與剪輯</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --table:#08203A; --table-2:#0F3055; --panel:#0C2A49;
    --line:#3D6B96; --line-soft:#20486E;
    --ink:#E9F2FA; --ink-dim:#8FB2CE;
    --ball:#FF7A18; --warn:#FFC24D; --good:#4ADE80; --bad:#FF6B6B;
    --score:#FF7A18;   /* 計分板強調色，跟著選色器走 */
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
  #fps{width:78px}   /* 自動偵測會填入 29.97 / 119.88 這類值，需要更寬 */
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
    <button class="btn" id="pick">載入影片</button>
    <span class="srcname" id="srcname">尚未載入</span>
    <div class="ctl">A<input type="text" id="nameA" value="選手 A"></div>
    <div class="ctl">B<input type="text" id="nameB" value="選手 B"></div>
    <div class="ctl">首發<select id="firstServer"><option value="0">A</option><option value="1">B</option></select></div>
    <span style="flex:1"></span>
    <label class="btn file">讀入標記<input type="file" id="load" accept=".json"></label>
    <button class="btn" id="save">匯出 JSON</button>
  </header>

  <div class="setbar">
    <div class="grp"><span class="tag">影格</span>
      <input type="number" id="fps" value="30" min="1" max="240" step="1"></div>
    <div class="grp"><span class="tag">每局</span>
      <input type="number" id="target" value="11" min="1" step="1"><span class="tag">分</span></div>
    <div class="grp"><span class="tag">賽制</span>
      <select id="deuce">
        <option value="standard">標準 · 勝 2 分</option>
        <option value="capped">封頂 · 先到即勝</option>
      </select>
      <span class="tag">封頂</span><input type="number" id="cap" value="12" min="2" step="1" disabled></div>
    <div class="grp"><span class="tag">起始局數</span>
      <input type="number" id="sgA" value="0" min="0" step="1">
      <span class="tag">:</span>
      <input type="number" id="sgB" value="0" min="0" step="1"></div>
    <div class="grp"><span class="tag">起始分數</span>
      <input type="number" id="spA" value="0" min="0" step="1">
      <span class="tag">:</span>
      <input type="number" id="spB" value="0" min="0" step="1">
      <select id="scope">
        <option value="every">每局套用</option>
        <option value="first">僅第一局</option>
      </select></div>
    <div class="grp"><span class="tag">計分板色</span>
      <input type="color" id="accent" value="#FF7A18"
             title="得分數字與名字左側裝飾條共用這個顏色">
      <button class="btn" id="accentReset" title="回到預設橘色">重設</button></div>
  </div>

  <div class="stage">
    <div class="screen" id="screen">
      <div class="empty" id="empty">
        按左上角 <b>載入影片</b> 選擇比賽影片。<br>
        影片直接從這台電腦讀取，不會上傳到任何地方。
      </div>
    </div>

    <div class="transport">
      <div class="tc"><span id="tc">00:00.00</span><small id="frameno">frame 0</small></div>
      <input type="range" class="scrub" id="scrub" min="0" max="0" step="0.001" value="0" aria-label="播放位置">
      <div class="speed" id="speed">
        <button class="btn" data-r="0.5">.5×</button>
        <button class="btn" data-r="1" aria-pressed="true">1×</button>
        <button class="btn" data-r="1.5">1.5×</button>
        <button class="btn" data-r="2">2×</button>
      </div>
    </div>

    <div class="legend">
      <span><kbd>space</kbd>播放／暫停</span>
      <span><kbd>S</kbd>發球</span>
      <span><kbd>A</kbd>A 得分</span>
      <span><kbd>B</kbd>B 得分</span>
      <span><kbd>N</kbd>換局</span>
      <span><kbd>Z</kbd>復原</span>
      <span><kbd>← →</kbd>逐格</span>
      <span><kbd>⇧← →</kbd>1 秒</span>
      <span><kbd>⌥← →</kbd>5 秒</span>
      <span><kbd>1-4</kbd>速度</span>
    </div>
  </div>

  <div class="rail">
    <div class="board">
      <div class="cards">
        <div class="card" id="cardA">
          <div class="who" id="whoA">選手 A</div>
          <div class="nums"><span class="g" id="gmA">0</span><span class="pts" id="ptsA">0</span></div>
          <div class="cap">局 · 分</div>
        </div>
        <div class="card" id="cardB">
          <div class="who" id="whoB">選手 B</div>
          <div class="nums"><span class="g" id="gmB">0</span><span class="pts" id="ptsB">0</span></div>
          <div class="cap">局 · 分</div>
        </div>
      </div>
      <div class="boardmeta">
        <span>第 <b id="gameNo">1</b> 局<span id="ruleNote" class="rule"></span></span>
        <span>應由 <b id="expServer">A</b> 發球</span>
      </div>
    </div>

    <div class="streamhead"><span>事件</span><span id="evcount">0</span></div>
    <div class="stream" id="stream"></div>

    <div class="cutout">
      <div class="row"><span>可剪去區間</span><b id="cutN">0</b></div>
      <div class="row"><span>剪去長度</span><b class="save" id="cutT">0.0s</b></div>
      <div class="row"><span>成片長度</span><b id="outT">0.0s</b></div>
      <div class="pads">
        <div class="ctl">得分後留<input type="number" id="tailPad" value="1.0" step="0.1" min="0">s</div>
        <div class="ctl">發球前留<input type="number" id="leadPad" value="0.3" step="0.1" min="0">s</div>
        <div class="ctl">最短剪點<input type="number" id="minCut" value="2.0" step="0.1" min="0">s</div>
      </div>
    </div>

    <div class="render">
      <div class="line">
        <span>品質</span>
        <select id="quality">
          <option value="fast">快</option>
          <option value="high" selected>標準</option>
          <option value="max">最好（慢）</option>
        </select>
        <label class="ctl" style="margin-left:auto"><input type="checkbox" id="cutLets">重發也剪</label>
      </div>
      <div class="out" id="outPath">—</div>
      <button class="btn hot go" id="go" disabled>製作成片</button>
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

  /* ───────────────────────── 送去 Python 的資料
     計分與剪接統計一律由 Python 算，介面不再自己實作一份。 */
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

  /* ───────────────────────── 影片 */
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
      banner('讀取影片失敗：' + e.message);
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
    video.addEventListener('error', () => banner('影片無法播放，可能是瀏覽器不支援這個編碼。'));
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

  /* ───────────────────────── 事件 */
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

  /* ───────────────────────── 向 Python 要計分結果 */
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
      if (mine !== seq) return;               // 過期回應直接丟掉
      paint(st);
      banner(null);
    } catch (e) {
      banner('連不到本機服務，請確認終端機視窗還開著。');
    }
  }

  /* ───────────────────────── 畫面 */
  function paint(st) {
    const nm = names(), cur = st.cur;
    $('whoA').textContent = nm[0]; $('whoB').textContent = nm[1];
    $('ptsA').textContent = cur.a;  $('ptsB').textContent = cur.b;
    $('gmA').textContent = cur.gA;  $('gmB').textContent = cur.gB;
    $('gameNo').textContent = cur.gameNo;
    $('ruleNote').textContent = deuce() === 'capped' ? ` · 封頂 ${capVal()}` : '';
    $('expServer').textContent = nm[cur.server];
    $('cardA').classList.toggle('serving', cur.server === 0);
    $('cardB').classList.toggle('serving', cur.server === 1);

    const stream = $('stream');
    $('evcount').textContent = events.length;
    if (!events.length) {
      stream.innerHTML = '<div class="streamempty">還沒有事件。<br>播放影片，在發球觸拍的瞬間按 <b style="color:var(--ink)">S</b>，得分時按 <b style="color:var(--ink)">A</b> 或 <b style="color:var(--ink)">B</b>。</div>';
    } else {
      let prevServe = false, html = '';
      events.forEach((e, i) => {
        const s = st.snaps[i];
        let label, cls;
        if (e.type === 'serve') { cls = 'serve'; label = prevServe ? '發球 · 重發' : '發球'; }
        else if (e.type === 'game') { cls = 'game'; label = '換局'; }
        else { cls = 'point'; label = nm[e.winner === 'A' ? 0 : 1] + ' 得分'; }
        prevServe = e.type === 'serve';
        const sc = s ? (s.won ? `<em>${s.gA}–${s.gB} 局</em>` : `${s.a}–${s.b}`) : '';
        html += `<div class="ev ${cls}" data-i="${i}">
          <time>${fmt(e.t)}</time>
          <span class="lbl"><i class="dot"></i>${label}</span>
          <span class="sc">${s && s.won ? `${s.a}–${s.b}` : ''}</span>
          <span class="sc">${sc}</span>
          <button class="kill" data-kill="${i}" title="刪除">×</button>
        </div>`;
      });
      stream.innerHTML = html;
      stream.scrollTop = stream.scrollHeight;
    }

    const c = st.cuts || {};
    $('cutN').textContent = c.n || 0;
    $('cutT').textContent = (c.seconds || 0).toFixed(1) + 's';
    $('outT').textContent = (c.outSeconds || 0).toFixed(1) + 's'
      + (c.pct ? `　壓縮 ${c.pct}%` : '');
    updateGo(st.ok);
  }

  function updateGo(planOK) {
    const running = polling !== null;
    const ok = !!srcPath && ffmpegOK && events.length > 0 && planOK !== false;
    $('go').disabled = running || !ok;
    if (!ffmpegOK && srcPath) $('go').textContent = '找不到 ffmpeg';
    else $('go').textContent = running ? '製作中…' : '製作成片';
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

  /* ───────────────────────── 鍵盤 */
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

  /* ───────────────────────── 渲染 */
  $('go').addEventListener('click', async () => {
    $('go').disabled = true;
    $('plog').hidden = true; $('plog').classList.remove('bad');
    $('bar').classList.remove('done','bad');
    $('progWrap').hidden = false;
    setBar(0, '啟動 ffmpeg…');
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
          setBar(s.pct, (s.eta != null ? '剩約 ' + mmss(s.eta) : '') +
                        (s.speed ? '　' + s.speed : ''));
          updateGo();
        } else {
          clearInterval(polling); polling = null;
          if (s.state === 'done') {
            setBar(100, '耗時 ' + mmss(s.elapsed));
            $('bar').classList.add('done');
            $('plog').hidden = false;
            $('plog').textContent = '完成 → ' + s.out;
          } else if (s.state === 'cancelled') {
            setBar(s.pct, '已取消');
          } else {
            fail((s.message || '渲染失敗') + '\n' + (s.log || []).join('\n'));
          }
          updateGo();
        }
      } catch (e) {
        clearInterval(polling); polling = null;
        fail('失去與本機服務的連線。');
      }
    }, 400);
    updateGo();
  }

  /* ───────────────────────── 存讀 */
  $('save').addEventListener('click', async () => {
    const doc = docPayload();
    try {                       // 讓匯出的 JSON 也帶上剪點，跟 V1.22 格式一致
      const st = await (await fetch('/fold', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc, opt: optPayload()})
      })).json();
      if (st.cutList) doc.cuts = st.cutList;
    } catch (e) { /* 拿不到就不帶，不影響主要資料 */ }
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
        banner('讀不到這個檔案的標記資料，請確認是本工具匯出的 JSON。');
      }
    };
    r.readAsText(f);
    e.target.value = '';
  });

  /* ───────────────────────── 起始：接回已載入的狀態（重新整理也不會掉） */
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
      if (!s.ffmpeg) banner('找不到 ffmpeg，可以標記與匯出 JSON，但無法產出成片。');
      if (s.job && s.job.state === 'running') { $('progWrap').hidden = false; startPolling(); }
    } catch (e) { /* 服務還沒起來就算了 */ }
    refresh();
  })();
})();
</script>
"""

# ─────────────────────────────────────────── HTTP 伺服器

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
        pass                                    # 別把每個 range 請求都印出來洗版

    # ── 小工具
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
            pass                                # 瀏覽器中斷 range 請求是常態

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ── 路由
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
        """擋掉別的網頁對這個本機服務發請求（例如偷偷叫出檔案對話框）。
        同源的 fetch 一定會帶 Origin，所以「有帶但對不上」就拒絕。"""
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

    # ── 首頁
    def _html(self):
        body = HTML.replace("__VERSION__", VERSION).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(body)

    # ── 影片串流（必須支援 Range，否則滑桿拖不動、Safari 可能不播）
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
                elif e:                          # bytes=-N 取尾端 N 位元組
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

    # ── 計分（唯一權威）
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

    # ── 原生檔案對話框
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

    # ── 渲染
    def _render(self):
        with STATE_LOCK:
            job = STATE["job"]
            video = STATE["video"]
            ffmpeg = STATE["ffmpeg"]
        if job and job.state == "running":
            return self._json(dict(error="已經有一個渲染在進行中。"), 409)
        if not video:
            return self._json(dict(error="還沒有載入影片。"), 400)
        if not ffmpeg:
            return self._json(dict(error="找不到 ffmpeg，無法渲染。"), 400)

        req = self._body()
        doc = req.get("doc") or {}
        opt = req.get("opt") or {}
        out = req.get("out") or default_out(video)
        out = os.path.abspath(out)

        pl = plan(doc, opt)
        if not pl["ok"]:
            return self._json(dict(error=pl["reason"]), 400)

        # 順手把標記存一份在成片旁邊，之後要重渲染或改參數都還原得回來
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
    print(f"介面      {url}")
    print(f"ffmpeg    {ff or '找不到——可以標記與匯出 JSON，但無法渲染'}")
    if not ff:
        print("          Mac: brew install ffmpeg-full")
        print("          Windows: 把 ffmpeg.exe 放在本腳本旁邊")
    print("\n只監聽 127.0.0.1，不對外開放。按 Ctrl-C 結束。\n")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n結束。")


# ─────────────────────────────────────────── 命令列渲染（與 V1.22 行為相同）

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
        print(f"\n略過 {len(pl['dropped'])} 個過短剪點（留著比跳接好）:")
        for f, t, why in pl["dropped"]:
            print(f"   {ts(f)} → {ts(t)}   {t - f:.2f}s   {why}")

    if args.dry_run:
        print("\n保留片段:")
        for i, (s, e) in enumerate(pl["keeps"]):
            print(f"   {i + 1:2d}  {ts(s)} → {ts(e)}   {e - s:6.2f}s")
        print()
        return

    ffmpeg = find_ffmpeg(args.ffmpeg, os.path.dirname(os.path.abspath(args.video)))
    if not ffmpeg:
        sys.exit("\n找不到 ffmpeg。\n"
                 "  Mac    : brew install ffmpeg-full\n"
                 "  Windows: 把 ffmpeg.exe 放到這個腳本旁邊，或用 "
                 "--ffmpeg \"C:\\ffmpeg\\bin\" 指定位置")
    ffprobe = os.path.join(os.path.dirname(ffmpeg),
                           "ffprobe.exe" if IS_WIN else "ffprobe")
    if not os.path.isfile(ffprobe):
        ffprobe = "ffprobe"
    print(f"ffmpeg    {ffmpeg}")
    print()

    cmd, workdir, flt = build_render(doc, pl, args.video, out, opt,
                                     ffmpeg, ffprobe, log=print)
    print("\n" + " ".join(
        (c if len(c) < 60 else f"<濾鏡 {len(c)} 字元，見 {flt}>") for c in cmd) + "\n")
    subprocess.run(cmd, check=True, cwd=workdir)
    print(f"\n完成 → {out}")


def main():
    for stream in (sys.stdout, sys.stderr):      # Windows 主控台預設非 UTF-8
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        description=f"ttcut {VERSION} — 桌球標記與剪輯。不給參數就開介面。")
    p.add_argument("-v", "--version", action="version", version=f"ttcut {VERSION}")
    p.add_argument("tags", nargs="?", help="標記 JSON（省略則開介面）")
    p.add_argument("video", nargs="?", help="來源影片（省略則開介面）")
    p.add_argument("-o", "--out", default=None)
    p.add_argument("--lead", type=float, default=None, help="發球前保留秒數（預設讀 JSON）")
    p.add_argument("--tail", type=float, default=None, help="得分後保留秒數（預設讀 JSON）")
    p.add_argument("--min-cut", type=float, default=DEFAULT_MIN_CUT,
                   help="短於此秒數就不剪，避免無意義跳接")
    p.add_argument("--cut-lets", action="store_true", help="重發之間的撿球也剪掉")
    p.add_argument("--let-tail", type=float, default=1.5, help="重發後保留秒數")

    g = p.add_argument_group("畫質")
    g.add_argument("--quality", choices=list(QUALITY), default="high",
                   help="fast=快、high=預設、max=最好（走 libx264 CRF，慢很多）")
    g.add_argument("--encoder", default=None,
                   help="Mac: h264_videotoolbox / Windows: h264_nvenc, h264_qsv, "
                        "h264_amf / libx264（純 CPU，最好也最慢）")
    g.add_argument("--crf", type=int, default=None, help="覆寫品質值，越小越好")
    g.add_argument("--preset", default=None, help="libx264 的 preset")
    g.add_argument("--bitrate", default=None, help="覆寫碼率，例如 40M")
    g.add_argument("--fps", default="source", help="source＝跟著片源，或直接給數字")
    g.add_argument("--hdr", choices=["auto", "tonemap", "keep", "ignore"], default="auto")
    g.add_argument("--size", default=None, help="覆寫解析度，例如 1920x1080")
    g.add_argument("--hwaccel", default="auto",
                   help="硬體解碼：auto（Mac 用 videotoolbox）/ none / cuda / qsv")

    s = p.add_argument_group("介面")
    s.add_argument("--port", type=int, default=None, help="指定連接埠")
    s.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")

    p.add_argument("--accent", default=None,
                   help=f"計分板強調色（得分數字與側邊裝飾條），例如 \"#FF7A18\"。"
                        f"預設 {DEFAULT_ACCENT}")
    p.add_argument("--font", default=FONT_NAME, help="計分板中文字型名稱")
    p.add_argument("--ffmpeg", default=None,
                   help="ffmpeg.exe 的路徑或所在資料夾（沒裝進 PATH 時用）")
    p.add_argument("--dry-run", action="store_true", help="只印剪接表，不渲染")
    args = p.parse_args()

    if args.tags and args.video:
        cli_render(args)
    elif args.tags or args.video:
        p.error("命令列渲染需要同時給 標記JSON 與 影片；只想開介面請不要帶參數。")
    else:
        serve(open_browser=not args.no_browser, port=args.port,
              ffmpeg_hint=args.ffmpeg)


if __name__ == "__main__":
    main()
