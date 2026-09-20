# Changelog

Versioning: small changes bump the decimal (V1 → V1.1 → V1.2). Architectural or
output-format changes bump the whole number.

The `_EN` build carries the same version number as the Chinese build it was made
from; only user-facing text differs.

## V2.5 — 2026-09-20

### Fixed
- **Stars were lost when a tags file was loaded.** The loader copied only time,
  type and winner, so after reopening a tags file every star was gone: the next
  render made no highlight clips, gave no warning, saved the starless tags back
  to disk, and left the previous render's clips in the folder — which looked as
  if clip padding and quality could not be changed.
- The highlight readout showed "clips · 9.0s + reel 3" with the count moved to
  the end, and the output path gained a stray trailing `/`. Both came from the
  right-to-left trick used to truncate long paths.
- Event-list rows wrapped onto two lines, with the star and delete buttons on a
  second line. The grid had four columns for five or six items; this dates back
  to V2.2 and got worse when the star was added.
- In shorter windows the sidebar pushed the render button off-screen and the
  whole page scrolled, video included. The sidebar now scrolls on its own.
- The output path was blank after a page refresh.

### Changed
- Quality is saved in the tags file (`render.quality`) and restored on load,
  like every other render setting. `--quality` falls back to it when not given.
- Auto bitrate caps first and applies the quality multiplier after. At 4K50/60
  on VideoToolbox, "fast" drops to 84M and now sits clearly below "standard" at
  120M; before, the cap squeezed them to 104M vs 120M. Every other source, tier
  and platform produces an identical command to V2.4 (46 of 48 combinations
  checked).
- Highlight padding sits on its own row labelled "before serve / after point",
  matching the film's padding fields, and steps by 0.1s instead of 0.5s.

### Added
- With highlights ticked and nothing starred, the sidebar warns in amber and
  the render log repeats it; warnings now stay visible after a render finishes.

### Unchanged
- Scoring, cut planning, highlight ranges and both scoreboards are identical to
  V2.4 across 300 randomized matches.

## V2.4 — 2026-09-14

### Added
- **Highlight clips.** Press `H` while tagging to star the rally that just
  finished; starred points show a ★ in the event list, clickable to toggle.
- Each starred rally is exported as its own file next to the film:
  `<output>-1.mp4`, `-2`, `-3`, in time order.
- A clip runs from the *last* serve before the point to the point. Lets in
  between are excluded — those rallies never got going.
- Clip padding is separate from the film's: 1.5s before the serve and 2.5s
  after the point by default, so a standalone clip shows the wind-up and the
  reaction.
- Clips carry the same scoreboard, showing the score going into the rally and
  updating when the point lands.
- With two or more clips, `<output>-highlights.mp4` is also produced by
  concatenating them without re-encoding — instant and lossless.
- Settings persist in the tags JSON under `highlights`; `--highlights`,
  `--no-highlights`, `--hl-lead`, `--hl-tail` and `--no-reel` override them.

### Changed
- Rendering is now a sequence of stages rather than one ffmpeg run. The progress
  bar weights stages by duration and names the current one.
- Clips seek on the input (`-ss`/`-t`), so only those seconds are decoded. Ten
  starred rallies add seconds to a render, not minutes.

### Handled
- A starred rally with no serve tagged before it starts 10 seconds earlier and
  logs a warning, rather than being dropped.

### Unchanged
- With highlights off, the generated ffmpeg command is identical to V2.3's.
  Verified across 300 randomized matches, along with the V2.2 and V2.3
  byte-identical checks for the cut list and both scoreboards.

## V2.3 — 2026-09-13

### Added
- **Stats board.** Tick *Stats board* and the last frame is frozen at the end of
  the film with a centred summary drawn over it. The corner scoreboard ends
  where the footage does, so the card has the screen to itself.
- Top half: the per-game score table in broadcast order — name, games won, then
  each game's score, winner bright and loser dimmed, games leader in the accent.
- Bottom half: a side-by-side comparison — A's figure left, B's right, metric
  name between them — showing **total points**, **service win rate** (with the
  raw fraction) and **longest point streak**. The leading side takes the accent.
- The longest streak carries across games, so two 11:0 games back to back reads
  as 22.
- Hold duration is adjustable, 1 second by default. Audio is padded with silence
  over the freeze, so picture and sound stay the same length.
- The same numbers appear live in the sidebar while tagging, before any render.
- Settings persist in the tags JSON under `stats`; `--stats`, `--no-stats` and
  `--stats-hold` override them from the command line.

### Fixed
- Player names containing `{`, `}` or `\` no longer break the ASS overlay.
- Install instructions said `brew install ffmpeg-full`, which is not a Homebrew
  formula. It is `brew install ffmpeg`.

### Unchanged
- With the stats box unticked, the generated `.ass` and filter graph are
  byte-identical to V2.2. Verified across 400 randomized matches.

## V2.2 — 2026-08-30
- Wider number fields: pad seconds (0.5, 1.0) and frame rate (29.97) used to get
  clipped.

## V2.1 — 2026-08-30
- Scoreboard accent colour is configurable; the point digits and the bar beside
  the names change together.
- Stored in the tags JSON as `scoreboard.accent`, so it travels with the file.
- New `--accent` flag, which wins over the JSON value.
- With no colour given, the generated `.ass` is byte-identical to V2.

## V2 — 2026-08-24
- Merged into a single tool: run it and it starts a server, opens the browser,
  and one button turns tags into a finished video.
- Scoring lives in Python only (`/fold`); the tagger's JS copy is gone, so rule
  changes happen in one place.
- Serve-rotation logic moved into the same `fold()`.
- Fixed the mismatch between tagger and renderer on minimum cut length (0.15s
  shown, 2.0s actually cut). Both now use one value, adjustable in the UI.
- Video is served by Python with HTTP Range support, so scrubbing and Safari
  playback work.
- The video path comes from a native file dialog (browsers never expose the real
  path).
- `ffmpeg -progress` drives a real progress bar; rendering no longer blocks the
  UI.
- JSON export/import and the command-line render path behave as in V1.22.

## V1.22 — 2026-08-20
- Starting game count, for matches split across several video files.
- Starting score / handicap, applied every game or first game only.
- Capped mode: after 10:10 the first to the cap wins, no win-by-two.
- JSON gains `format` and `start` blocks; older files fall back to defaults.

## V1.2 — 2026-08-20
- Fixed a fatal bug: the V1.1 refactor dropped `-c:v`, so ffmpeg had been
  silently falling back to the default libx264.
- Added `--hwaccel`; videotoolbox hardware decoding on by default on Mac.
- `--hdr keep` falls back automatically on non-HDR sources.
- libx264/libx265 switch to bitrate mode when `--bitrate` is given.

## V1.1 — 2026-08-20
- Scoreboard: game count became a filled chip with dark digits.
- Quality: frame rate follows the source, bitrate derived from resolution ×
  frame rate, three `--quality` tiers, HDR detection.

## V1 — 2026-08-20
- First working version.
