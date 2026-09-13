# Changelog

Versioning: small changes bump the decimal (V1 → V1.1 → V1.2). Architectural or
output-format changes bump the whole number.

The `_EN` build carries the same version number as the Chinese build it was made
from; only user-facing text differs.

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
