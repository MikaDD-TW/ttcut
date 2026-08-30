**English** · [繁體中文](README.zh-TW.md)

# ttcut

**Table tennis match video: tag the rallies, cut the ball-chasing, burn in a persistent scoreboard. One tool, start to finish.**

Drop in a full match recorded on your phone, tag each serve and each point, and ttcut removes the dead time spent retrieving the ball while overlaying a scoreboard that updates as the match goes on. Out comes a tight, watchable video.

<img width="1905" height="934" alt="ttcut demo" src="https://github.com/user-attachments/assets/d8df4502-ef8d-4333-9e4c-b5e97cfd7cc1" />


---

## Requirements

- **Python 3.8 or newer**
- **ffmpeg** — not bundled, install it yourself

```bash
# macOS
brew install ffmpeg

# Confirm the subtitles filter is present (the scoreboard needs it)
ffmpeg -filters | grep subtitles
```

On Windows, drop `ffmpeg.exe` next to the script, or point `--ffmpeg` at the folder containing it. Get it from [ffmpeg.org](https://ffmpeg.org/download.html).

## Getting started

```bash
python3 ttcut_v2_2_EN.py
```

The tool starts a local server and opens your browser. **It binds to `127.0.0.1` only and is not reachable from the network.**

1. Click **Load video** at the top left and pick your file from the native OS dialog
2. Fill in both player names and who serves first; check the frame rate and match format
3. Play the video. Press `S` on every serve, `A` or `B` on every point
4. The panel on the right shows the running score and cut statistics as you go
5. Hit render. The result lands next to the source video as `<name>.cut.mp4`

> **Refreshing the browser clears your tags.** The video path and any render in progress survive a reload, but the tagged events do not. On a long match, hit **Export JSON** partway through.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` `→` | Step one frame |
| `Shift` + `←` `→` | Step one second |
| `Alt` + `←` `→` | Step five seconds |
| `S` | Tag a serve |
| `A` | Point to A |
| `B` | Point to B |
| `N` | New game |
| `Z` | Undo the last tag |
| `1` `2` `3` `4` | Playback speed 0.5× / 1× / 1.5× / 2× |

Shortcuts do not fire while the cursor is in a text field, so you can type names freely.

## Cutting rules

The gap between a point and the next serve is someone fetching the ball. That gap is what ttcut removes.

| Setting | Default | What it does |
|---|---|---|
| Hold after point | 1.0 s | How long to keep after the point lands, so the ball finishes its bounce on screen |
| Hold before serve | 0.3 s | How long to keep before the next serve, so the cut does not feel clipped |
| Minimum cut | 2.0 s | Anything shorter than this is left alone, to avoid pointless jump cuts |
| Cut between lets | off | When on, the ball-chasing between lets is cut too |
| Hold after let | 1.5 s | Only applies when the setting above is on |

## Scoring rules

- **Points per game** is configurable, 11 by default
- **Standard**: after 10-10, you must win by two
- **Capped**: after 10-10, first to the cap (12 by default) takes the game
- **Starting game count** — for when each game is its own file and you are continuing a match
- **Handicap** — set a starting score for either player, applied either every game or only the first
- **Serve rotation** is handled automatically, including the switch to alternating serves after deuce

Scoring is implemented once, in Python. The interface and the final render call the same function, so the two can never disagree about the score.

## Output settings

| Quality | Scale | CRF | preset | Notes |
|---|---|---|---|---|
| `fast` | 0.70× | 21 | veryfast | Draft, for checking that the cuts land right |
| `high` | 1.00× | 18 | medium | Default; keeps the source resolution |
| `max` | 1.40× | 16 | slow | Forces software encoding — slowest and best |

Frame rate follows the source by default. On Mac, `h264_videotoolbox` hardware encoding and `videotoolbox` hardware decoding are on by default; on Windows the tool detects their absence and falls back to `libx264`. HDR sources are tone-mapped to SDR by default.

## Command line

With a tags JSON already in hand, you can skip the interface and render directly:

```bash
# Basic
python3 ttcut_v2_2_EN.py match.tags.json match.MOV

# Choose the output path and quality
python3 ttcut_v2_2_EN.py match.tags.json match.MOV -o final.mp4 --quality max

# Print the cut table without rendering, to sanity-check the cut points
python3 ttcut_v2_2_EN.py match.tags.json match.MOV --dry-run
```

<details>
<summary>Full flag list</summary>

| Flag | Description |
|---|---|
| `-o, --out` | Output path; defaults to `<source>.cut.mp4` |
| `--lead` / `--tail` | Seconds held before the serve / after the point; read from the JSON by default |
| `--min-cut` | Minimum cut length in seconds, default 2.0 |
| `--cut-lets` | Also cut the ball-chasing between lets |
| `--let-tail` | Seconds held after a let, default 1.5 |
| `--quality` | `fast` / `high` / `max`, default `high` |
| `--encoder` | Pick the encoder explicitly |
| `--crf` | Override the quality value; lower is better |
| `--preset` | libx264 preset |
| `--bitrate` | Override the bitrate, e.g. `40M` |
| `--fps` | `source` to follow the input, or a number |
| `--size` | Override the resolution, e.g. `1920x1080` |
| `--hdr` | `auto` / `tonemap` / `keep` / `ignore` |
| `--hwaccel` | Hardware decoding, default `auto` |
| `--accent` | Scoreboard accent colour; wins over the value in the JSON |
| `--font` | Scoreboard font name |
| `--ffmpeg` | Folder containing ffmpeg |
| `--port` | Pick the port |
| `--no-browser` | Do not open a browser automatically |
| `--dry-run` | Print the cut table, render nothing |

</details>

## Tags JSON format

The exported file is plain JSON — portable, diffable, and editable by hand. Tag on a Mac and render on Windows with the same file; that works.

```json
{
  "version": 2,
  "generator": "ttcut V2.2-EN",
  "source": "IMG_1496.MOV",
  "fps": 30,
  "players": { "A": "Player A", "B": "Player B" },
  "firstServer": "A",
  "format": { "pointsPerGame": 11, "deuce": "standard", "cap": 12 },
  "start": {
    "games":  { "A": 0, "B": 0 },
    "points": { "A": 0, "B": 0 },
    "handicapScope": "every"
  },
  "pads": { "tail": 1.0, "lead": 0.3 },
  "scoreboard": { "accent": "#FF7A18" },
  "events": [
    { "t": 12.400, "frame": 372, "type": "serve" },
    { "t": 18.933, "frame": 568, "type": "point", "winner": "A" },
    { "t": 45.100, "frame": 1353, "type": "game" }
  ]
}
```

There are three event types: `serve`, `point` (which carries a `winner`), and `game`. A matching `.tags.json` is also written next to the rendered video.

## Language versions

| File | Interface language |
|---|---|
| `ttcut_v2_2_EN.py` | English |
| `ttcut_v2_2.py` | Traditional Chinese |

**The two builds share identical scoring, cutting and rendering logic** — only the interface text and code comments differ. Tags JSON is interchangeable: tag with one build and render with the other, in either direction. The generated scoreboard `.ass` files are byte-identical apart from a single version comment line.

## Troubleshooting

**The video will not display in the browser, but the file is fine**
The source is probably HEVC. Chrome cannot preview HEVC; Safari can. On a Mac, tagging in Safari is the better choice anyway — you get native 4K H.264 hardware decoding.

**macOS warns that libass cannot find a PingFang font path**
Harmless, ignore it. The font is found in AssetsV2 and the scoreboard renders correctly.

**Windows says tkinter is missing**
Some Python installs ship without tkinter, so the file dialog cannot open. Render from the command line instead, or install a Python distribution that includes tkinter.

**ffmpeg not found**
The interface says so on startup. You can still tag and export JSON, you just cannot produce a finished video. Install ffmpeg or point `--ffmpeg` at it, then restart.

**The output looks worse than expected**
If the target bitrate falls below the source, the tool prints a warning before rendering along with a suggested value. Override it with `--bitrate` or `--quality max`. If the source itself was recorded at a low bitrate, quality is capped by the recording and there is nothing to be done about it here.

## Versions

Small changes bump by 0.1; architectural or output-format changes bump the whole number. The full changelog lives in the docstring at the top of the script.

- **V2.2** — Wider number fields; fixed pad seconds and 29.97 frame rates getting clipped
- **V2.1** — Configurable scoreboard accent colour, stored in the JSON so it travels with the file
- **V2** — Tagger and renderer merged into one tool; scoring unified in Python; real ffmpeg progress bar; native file dialog
- **V1.22** — Starting game count, handicap scores, capped format
- **V1.2** — Fixed a fatal bug where a dropped `-c:v` made ffmpeg silently fall back to the default encoder
- **V1.1** — Scoreboard redesign; quality tiers
- **V1** — First working version

## Licence

MIT — see [LICENSE](LICENSE).

This tool calls ffmpeg as an external program. It **neither contains nor distributes ffmpeg itself**. ffmpeg is licensed separately; get it from official sources.

Written with the assistance of Anthropic Claude.

---

Copyright (c) 2026 MikaDD (Taiwan)
