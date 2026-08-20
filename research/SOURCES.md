# SOURCES — every source that has ever fed CIA

**PURPOSE.** Record where each idea came from, so we can tell *which teacher's
strategy is working and which is not*, and so a link is never lost again.

**RULE: the moment the user gives a link, it gets written here — before any
download, transcription or analysis.** The Romeo links were lost because they
lived only in a chat message. That must not happen twice.

Columns: `id` = the prefix used in filenames. `status` = what we actually hold.

---

## S1 — "HK" / the user's group mentor  ⭐ THIS IS WHAT CIA RUNS TODAY

The 3-4 Google-Meet recordings behind the live **CRT 1.0** strategy.
Host on the recording is "Vishal"; participants incl. Hassan Khan (HK),
Jameel Ahmed, Naveed Masood, Abdullah Khan. Recorded ~20 Jul 2026.

| # | YouTube ID | title | secs | local file |
|---|---|---|---|---|
| 1 | `vuOlOFlT4LA` | Part 1: Foundations & Core Concepts | 6897 | `D:\CRT playlist\CRT-Candle-Range-Theory-Series-Part-1-.mp4` |
| 2 | `yyCvEgR62xQ` | Part 2: Understanding Rejection Blocks | 5185 | `...Part-2.mp4` |
| 3 | `LTlnmwKY-2U` | Stop Treating CRT Like a Pattern (Part 3) | 5208 | `Stop-Treating-CRT-Like-a-Pattern-Part-3_.mp4` |
| 4 | `Iz6rFD4hJHA` | CRT Complete Guide: The Final Summary Session | 9539 | `CRT-Complete-Guide-The-Final-Summary-.mp4` |

- **video:** ✅ local mp4s, 1080p30, 7.45 h total
- **audio → text:** ✅ faster-whisper `base` int8, `task="translate"` (speech is
  Hinglish/Urdu-English). 4 files in `research/crt-playlist/*.txt`, **timestamped**.
- **visuals:** ✅ contact sheets + full-res frames (method in `crt-playlist-ingest` memory)
- **charts the user marked by hand:** `D:\CRT manual\*.jpg` (21 BTC charts, 59 marked CRTs)
- **rules extracted:** `research/crt-playlist/_RULES.md`, `research/crt-manual/_RULEBOOK.md`
- **code:** `strategies/smc/crt.py::detect_crt_10`, live as `CRT 1.0` (`ENABLE_CRT_10=True`)

## S2 — "Romeo" / CRT University (romeotpt.com)  ← CRT 2.0 WILL BE BUILT FROM THIS

Claims to be the originator of CRT + Turtle Soup. 16 videos pulled 2026-08-09.

- **✅ LINKS RECOVERED 2026-08-20** — channel `https://www.youtube.com/@Romeotpt` (romeotpt.com).
  All 16 IDs in `research/crt-transcripts/_channel_list.txt`, matched to our transcripts
  by title + duration. **6.32 h total.**
- **video:** ✅ **all 16 downloaded 2026-08-20** to `D:\CRT romeo\vNN_*.mp4`, 1.2 GB,
  1080p where offered. Every file's duration ffprobe-verified against the channel
  listing (all within 1 s = YouTube rounding), so timestamps are trustworthy.
- **audio:** not extracted — not needed, see below.
- **visuals:** ⏳ next step. `research/harness/sheets_romeo.py` contact-sheets each
  video at 1 frame/min tiled 5x5 (25 min of footage per readable image); pull
  full-res single frames only at the interesting moments.
- **text:** ⚠️ TWO SETS, do not confuse them.
  - **OLD (2026-08-09):** `research/crt-transcripts/v01..v16*.txt`, ~41,500 words.
    yt-dlp auto-subs that were cleaned, so **ALL TIMESTAMPS WERE STRIPPED** and
    each file is one unbroken line. Quotable, but you can never jump to the
    moment on the chart. This is what motivated the re-ingest.
  - **NEW (2026-08-20):** the user is supplying YouTube's own transcripts, WITH
    timestamps, into `D:\CRT romeo\transcripts\vNN.txt`. Prefer these. Local
    whisper was written (`research/harness/transcribe_romeo.py`) but NOT run —
    YouTube's own text is more accurate and cost nothing.
- **rules extracted:** partially, folded into `research/crt-manual/_RULEBOOK.md`
- **code:** none yet. **CRT 2.0 (`ENABLE_CRT_20`) will be built from this and run
  ALONGSIDE CRT 1.0, not replacing it** — separate strategy slot, separate label,
  so the journal can say which teacher's version actually earns. Agreed 2026-08-20.
- ⚠️ **Romeo's "80-85% world record accuracy" claim is marketing, not a measurement.**
  Every rule of his is a hypothesis to be tested exactly like HK's.

| id | file | read as of 2026-08-09? |
|---|---|---|
| v01 | whatisCRT | ✅ |
| v02 | CRTology_Intro | ❌ (possibly a different creator) |
| v03 | CRTology_ep1 | ❌ (possibly a different creator) |
| v04 | TurtleSoup | ✅ |
| v05 | livetape1 | ❌ execution tape |
| v06 | livetape2 | ❌ execution tape |
| v07 | ep1 one CRT model (#1) | ✅ |
| v08 | ep2 kiss of death | ✅ |
| v09 | ep3 journey | ❌ |
| v10 | ep4 candle anatomy | ❌ |
| v11 | ep5 key level | ✅ |
| v12 | ep6 SMT | ✅ |
| v13 | ep7 candle 3 | ✅ |
| v14 | ep8 why CRT fails | ✅ |
| v15 | ep9 connecting dots | ✅ |
| v16 | ep10 clean close | ✅ |

## S3 — ICT (Inner Circle Trader), official YouTube channel

| | |
|---|---|
| **links** | ✅ kept — `research/ict-transcripts/_channel_list.txt`, `id\|secs\|title` |
| **text** | ✅ 14 transcripts, `research/ict-transcripts/m*.txt` |
| **video/visuals** | ❌ |
| **findings** | `research/ict-transcripts/_FINDINGS.md` |
| **code** | `strategies/smc/ict_model.py` — the "ICT new" 2022 model. Currently OFF. |

## S4 — the user's own chart marks and live verdicts  ⭐ HIGHEST-VALUE SOURCE

Not a teacher — the user's own take/skip decisions. Has produced more real
detector defects than all 36 backtests combined.

| | |
|---|---|
| **where** | `research/crt-manual/_LABELS.md`, `research/journal/seed.py` |
| **tooling** | `research/journal/journal.py check` (replays every verdict) |
| **TradingView** | BTC 1D marked layout, chart id `IAdFJF0P` |

---

## Adding a new source — the checklist

1. Write the row in this file FIRST, with the raw URL.
2. `yt-dlp --flat-playlist --print "%(id)s|%(duration)s|%(title)s"` → save as `_channel_list.txt`.
3. Download audio (or video if the charts matter).
4. Transcribe locally with faster-whisper — **keep the timestamps**.
5. Contact-sheet the video for the visuals; full-res frames only at the good bits.
6. Extract rules into a `_RULES.md` with a `file@mm:ss` citation on every rule.
7. Only then write code, and give it its own strategy slot so it can be
   measured separately from every other source.
