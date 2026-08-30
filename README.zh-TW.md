[English](README.md) · **繁體中文**

# ttcut

**桌球比賽影片：標記、剪去撿球、疊上常駐計分板。一個工具做完。**

把手機拍的整場比賽丟進去，標記每一次發球與得分，ttcut 會剪掉中間撿球的空檔，並在畫面上疊一塊會即時更新的計分板，輸出一支乾淨的成片。

<!-- 截圖放這裡。在 GitHub 編輯 README 時，把圖片檔直接拖進文字框，GitHub 會自動上傳並產生連結。
     建議放一張介面截圖，或一段 20 秒的 GIF：載入影片 → 標記幾個點 → 渲染 → 成品有計分板。 -->

<img width="1905" height="934" alt="ttcut demo" src="https://github.com/user-attachments/assets/51a75d9a-97bb-4390-902d-ff49c3d31e2b" />


---

## 需要什麼

- **Python 3.8 以上**
- **ffmpeg**（不隨本工具散布，請自行安裝）

```bash
# macOS
brew install ffmpeg

# 裝完可以確認字幕濾鏡在（計分板需要它）
ffmpeg -filters | grep subtitles
```

Windows 把 `ffmpeg.exe` 放在腳本旁邊即可，或用 `--ffmpeg` 指定資料夾。從 [ffmpeg.org](https://ffmpeg.org/download.html) 下載。

## 開始使用

```bash
python3 ttcut_v2_2.py
```

工具會啟動一個本機伺服器並自動開啟瀏覽器。**只監聽 `127.0.0.1`，不對外開放。**

1. 按左上角 **載入影片**，用系統原生的檔案對話框選檔
2. 填入雙方名字與首先發球方，確認影格率與賽制
3. 播放影片，每次發球按 `S`，每次得分按 `A` 或 `B`
4. 右側會即時顯示比分與剪接統計，確認無誤後按渲染
5. 成品輸出到來源影片旁邊，檔名是 `<原檔名>.cut.mp4`

> **注意：重新整理瀏覽器會清空已標記的事件。** 影片路徑和進行中的渲染工作會保留，但標記本身不會。長場比賽建議中途按 **匯出 JSON** 存一份。

## 鍵盤快捷鍵

| 鍵 | 動作 |
|---|---|
| `Space` | 播放 / 暫停 |
| `←` `→` | 前後移動一格 |
| `Shift` + `←` `→` | 前後移動 1 秒 |
| `Alt` + `←` `→` | 前後移動 5 秒 |
| `S` | 標記發球 |
| `A` | A 得分 |
| `B` | B 得分 |
| `N` | 換局 |
| `Z` | 復原上一個標記 |
| `1` `2` `3` `4` | 播放速度 0.5× / 1× / 1.5× / 2× |

游標在輸入框裡時快捷鍵不會觸發，可以安心打字。

## 剪接規則

一個「得分 → 下一次發球」之間的空檔就是一段撿球，ttcut 剪掉的是這一段。

| 設定 | 預設 | 說明 |
|---|---|---|
| 得分後留 | 1.0 秒 | 得分瞬間之後保留多久，讓球落地畫面完整 |
| 發球前留 | 0.3 秒 | 下一次發球前保留多久，避免切得太緊 |
| 最短剪點 | 2.0 秒 | 短於這個長度就不剪，避免無意義的跳接 |
| 剪掉重發間隔 | 關閉 | 開啟後連重發（let）之間的撿球也一起剪 |
| 重發後留 | 1.5 秒 | 上一項開啟時才有作用 |

## 計分規則

- **每局分數**可調，預設 11 分
- **標準賽制**：10:10 後需淨勝 2 分
- **封頂賽制**：10:10 後先到封頂分（預設 12）即勝
- **起始局數**：適合一局一個檔案、要接續同一場比賽的情況
- **讓分**：可設定雙方起始分數，並選擇「每局都讓」或「只讓第一局」
- **發球輪次**自動處理，包含 deuce 後改為每分換發

計分邏輯只有 Python 一份實作，介面顯示與最終渲染用的是同一個函式，不會出現兩邊算出不同比分的問題。

## 輸出設定

| 品質 | 解析度 | CRF | preset | 說明 |
|---|---|---|---|---|
| `fast` | 0.70× | 21 | veryfast | 先看剪點對不對用的草稿 |
| `high` | 1.00× | 18 | medium | 預設，維持原解析度 |
| `max` | 1.40× | 16 | slow | 強制軟體編碼，最慢也最好 |

影格率預設跟著片源。Mac 上預設啟用 `h264_videotoolbox` 硬體編碼與 `videotoolbox` 硬體解碼；Windows 上偵測不到時會自動退回 `libx264`。HDR 片源預設 tone-map 成 SDR。

## 命令列

有現成的標記 JSON 就可以跳過介面直接渲染：

```bash
# 基本用法
python3 ttcut_v2_2.py match.tags.json match.MOV

# 指定輸出與品質
python3 ttcut_v2_2.py match.tags.json match.MOV -o final.mp4 --quality max

# 只印剪接表，不真的渲染（確認剪點用）
python3 ttcut_v2_2.py match.tags.json match.MOV --dry-run
```

<details>
<summary>完整參數列表</summary>

| 參數 | 說明 |
|---|---|
| `-o, --out` | 輸出路徑，預設 `<來源>.cut.mp4` |
| `--lead` / `--tail` | 發球前 / 得分後保留秒數，預設讀 JSON |
| `--min-cut` | 最短剪點秒數，預設 2.0 |
| `--cut-lets` | 連重發之間的撿球也剪掉 |
| `--let-tail` | 重發後保留秒數，預設 1.5 |
| `--quality` | `fast` / `high` / `max`，預設 `high` |
| `--encoder` | 指定編碼器 |
| `--crf` | 覆寫品質值，越小越好 |
| `--preset` | libx264 的 preset |
| `--bitrate` | 覆寫碼率，例如 `40M` |
| `--fps` | `source` 跟著片源，或直接給數字 |
| `--size` | 覆寫解析度，例如 `1920x1080` |
| `--hdr` | `auto` / `tonemap` / `keep` / `ignore` |
| `--hwaccel` | 硬體解碼，預設 `auto` |
| `--accent` | 計分板強調色，覆寫 JSON 裡的設定 |
| `--font` | 計分板字型名稱 |
| `--ffmpeg` | ffmpeg 所在資料夾 |
| `--port` | 指定連接埠 |
| `--no-browser` | 不要自動開瀏覽器 |
| `--dry-run` | 只印剪接表，不渲染 |

</details>

## 標記 JSON 格式

匯出的檔案是純 JSON，可攜、可版控、可手改。同一份檔案在 Mac 標記、拿到 Windows 渲染沒有問題。

```json
{
  "version": 2,
  "generator": "ttcut V2.2",
  "source": "IMG_1496.MOV",
  "fps": 30,
  "players": { "A": "選手 A", "B": "選手 B" },
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

事件型別有三種：`serve`（發球）、`point`（得分，帶 `winner`）、`game`（換局）。渲染時也會在輸出影片旁存一份對應的 `.tags.json`。

## 兩個語言版本

| 檔案 | 介面語言 |
|---|---|
| `ttcut_v2_2.py` | 繁體中文 |
| `ttcut_v2_2_EN.py` | English |

**兩版的計分、剪接與渲染邏輯完全相同**，差別只在介面文字與程式註解。標記 JSON 可以互通——中文版標的檔可以拿給英文版渲染，反之亦然。輸出的計分板 ASS 檔除了一行版本註解之外逐字相同。

## 疑難排解

**瀏覽器裡看不到影片畫面，但檔案是好的**
片源可能是 HEVC。Chrome 不支援 HEVC 預覽，Safari 可以。Mac 上建議用 Safari 標記，順便吃到原生 4K H.264 硬體解碼。

**macOS 出現 libass 找不到 PingFang 字型路徑的警告**
無害，可以忽略。字型實際上有在 AssetsV2 裡被找到，計分板會正常輸出。

**Windows 說找不到 tkinter**
部分 Python 安裝沒有內建 tkinter，檔案對話框會開不起來。改用命令列模式渲染，或安裝含 tkinter 的 Python 發行版。

**找不到 ffmpeg**
介面會顯示提示，此時仍然可以標記與匯出 JSON，只是無法產出成片。裝好 ffmpeg 或用 `--ffmpeg` 指定路徑後重開即可。

**輸出的畫質比預期差**
如果目標碼率低於片源，工具會在渲染前印出警告並建議數值。可以用 `--bitrate` 或 `--quality max` 覆寫。片源本身碼率就低的話，畫質上限受限於拍攝端，這部分無解。

## 版本

遵循「小改動 +0.1，架構或輸出格式的重大變更才進位」的規則。完整更新紀錄在程式檔開頭的 docstring 裡。

- **V2.2** — 數字欄位加寬，修正 pad 秒數與 29.97 影格率被截斷的問題
- **V2.1** — 計分板強調色可自訂，存進 JSON 隨檔案走
- **V2** — 標記器與渲染器合併成單一工具；計分邏輯統一由 Python 實作；ffmpeg 進度條；原生檔案對話框
- **V1.22** — 起始局數、讓分、封頂賽制
- **V1.2** — 修正 V1.1 漏掉 `-c:v` 導致靜默退回預設編碼器的嚴重問題
- **V1.1** — 計分板改版；畫質分檔
- **V1** — 第一個可用版本

## 授權

MIT，詳見 [LICENSE](LICENSE)。

本工具透過外部指令呼叫 ffmpeg，**不包含也不散布 ffmpeg 本身**。ffmpeg 有自己的授權條款，請自行從官方管道取得。

開發過程使用 Anthropic Claude 協助撰寫程式碼。

---

Copyright (c) 2026 MikaDD (Taiwan)
