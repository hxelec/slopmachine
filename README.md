# YouTube Shorts Pipeline

Turn a single long-form video into several ready-to-upload vertical Shorts,
fully automated: transcription, clip selection, and rendering all happen
in one run.

## What it does

1. **Transcribes** `input.mp4` with word-level timestamps (faster-whisper)
2. **Selects clips** by sending the transcript to Claude (Fable 5), which
   picks the strongest self-contained, substantive moments - no clickbait,
   no padding to hit a target number
3. **Renders** each clip as a finished vertical Short:
   - Blurred, cropped background filling the 9:16 frame
   - Original footage centered on top, scaled to fit
   - Title burned in above the video
   - Word-by-word highlighted captions burned in below

No manual approval step - whatever Claude selects gets rendered straight
to video files.

## Output

Running the pipeline on a folder produces a `shorts_output/` directory
containing, per clip:

| File | What it is |
|---|---|
| `1.mp4`, `2.mp4`, ... | Finished vertical video, ready to upload |
| `1.srt`, `2.srt`, ... | Plain caption file (for YouTube's caption upload field) |
| `1_info.txt`, `2_info.txt`, ... | Suggested title + description, ready to paste in |
| `_work/` | Intermediate files (subtitle/title source) - safe to ignore, useful if you want to hand-edit a title and re-render just one clip |

It also caches `input_transcript.json` and `input_clip_candidates.json`
next to the script, so re-running after a config tweak doesn't re-transcribe
or re-call the API unless you pass `--fresh`.

## Cost

Each run makes one Claude API call (a few thousand tokens) - typically
**$0.30-0.70 per video** using Fable 5, and less than $0.10 with any of the other models, depending on length. Transcription runs locally
and is free.

---

## Requirements

- Python 3.10+
- ffmpeg (with ffprobe)
- A [Claude API key](https://platform.claude.com) with billing enabled
- An NVIDIA GPU is **optional** - the script auto-detects CUDA and falls
  back to CPU if unavailable. GPU is faster but not required.

---

## Setup

<details>
<summary><b>Windows</b></summary>

**1. Install Python**
Download from [python.org](https://www.python.org/downloads/).
Check "Add python.exe to PATH" during install.

**2. Install ffmpeg**
```
winget install ffmpeg
```

**3. Install Python packages**
```
pip install faster-whisper anthropic
```

If you have an NVIDIA GPU (optional, for faster transcription):
```
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

**4. Set your API key**
```
setx ANTHROPIC_API_KEY "sk-ant-your-actual-key-here"
```
Close and reopen your terminal afterward - `setx` only applies to new windows.

**5. Verify**
```
python --version
ffmpeg -version
echo %ANTHROPIC_API_KEY%
```

</details>

<details>
<summary><b>macOS</b></summary>

**1. Install Homebrew** (if you don't have it)
```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**2. Install Python and ffmpeg**
```
brew install python ffmpeg
```

**3. Install Python packages**
```
pip3 install faster-whisper anthropic
```
(No NVIDIA packages needed - Macs don't have CUDA-capable GPUs; the
script will run transcription on CPU automatically.)

**4. Set your API key**
```
echo 'export ANTHROPIC_API_KEY="sk-ant-your-actual-key-here"' >> ~/.zshrc
source ~/.zshrc
```

**5. Required code change**
Open `run_pipeline.py` and change the font path, since the default is
Windows-only:
```python
FONT_FILE = "C:/Windows/Fonts/arialbd.ttf"
```
to:
```python
FONT_FILE = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
```
Any bold `.ttf`/`.otf` on your system works if that exact path doesn't exist.

**6. Verify**
```
python3 --version
ffmpeg -version
echo $ANTHROPIC_API_KEY
```

</details>

<details>
<summary><b>Linux</b></summary>

**1. Install Python, pip, and ffmpeg** (Debian/Ubuntu example - adjust for your distro)
```
sudo apt update
sudo apt install python3 python3-pip ffmpeg fontconfig
```

**2. Install Python packages**
```
pip3 install faster-whisper anthropic
```

If you have an NVIDIA GPU with drivers installed (optional, for speed):
```
pip3 install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

**3. Set your API key**
```
echo 'export ANTHROPIC_API_KEY="sk-ant-your-actual-key-here"' >> ~/.bashrc
source ~/.bashrc
```
(Use `~/.zshrc` instead if you're on zsh.)

**4. Required code change**
Update the font path for Linux. Find an installed bold font:
```
fc-list | grep -i bold
```
Then set `FONT_FILE` in `run_pipeline.py` to one of the returned paths,
e.g.:
```python
FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
```

**5. Verify**
```
python3 --version
ffmpeg -version
echo $ANTHROPIC_API_KEY
```

</details>

---

## Usage

1. Put `run_pipeline.py` in a folder with your video
2. Rename the video to exactly `input.mp4`
3. Run:
```
python run_pipeline.py
```
(`python3` on macOS/Linux)

Force a fresh transcription and clip selection (e.g. after swapping in a
new video with the same filename):
```
python run_pipeline.py --fresh
```

### Re-rendering a single clip

If you want to hand-edit a title or caption and re-render just one clip
without calling the API again, edit the relevant file in
`shorts_output/_work/`, then run:
```
python rerender_clip.py <clip_number>
```

---

## Configuration

All tunable settings live near the top of `run_pipeline.py`:

| Setting | Default | Controls |
|---|---|---|
| `TARGET_CLIP_COUNT` | `3` | Roughly how many clips Claude aims to select |
| `MIN_CLIP_SECONDS` / `MAX_CLIP_SECONDS` | `10` / `80` | Allowed clip length range |
| `CLIP_MODEL` | `claude-fable-5` | Which Claude model picks the clips |
| `WHISPER_MODEL_SIZE` | `large-v3` | Transcription accuracy/speed tradeoff |
| `BLUR_SIGMA` | `80` | Background blur intensity |
| `TITLE_FONTSIZE` / `CAPTION_FONTSIZE` | `64` / `72` | Text sizes |
| `TITLE_GAP_ABOVE_VIDEO` | `100` | Pixel gap between title and video |
| `TITLE_SIDE_MARGIN` | `90` | Safe margin before title text wraps |
| `HIGHLIGHT_COLOR_ASS` | yellow | Caption word-highlight color (ASS format) |
| `FONT_FILE` / `ASS_FONT_NAME` | Windows Arial | Font used for title and captions - **must be changed on macOS/Linux** |

---

## Troubleshooting

- **`ffmpeg: command not found`** - ffmpeg isn't installed or isn't on PATH
- **`ANTHROPIC_API_KEY environment variable is not set`** - open a *new*
  terminal window after setting the key, or re-source your shell config
- **Title text looks wrong or fails to render** - check `FONT_FILE` points
  to a font that actually exists on your system
- **A single clip looks corrupted** - could be a very long output path
  (Windows has a 260-character path limit) or unusual characters in the
  title; try `rerender_clip.py` after checking the `_work` files
