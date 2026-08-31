"""
Fully automated Shorts pipeline.

Put this script and a video named exactly "input.mp4" in the same folder,
then run it (or double-click run.bat). It will:

  1. Transcribe input.mp4 with word-level timestamps (faster-whisper, GPU)
  2. Ask Claude (Fable 5) to pick the strongest ~5 standalone clips
  3. Render each one as a finished vertical Short with blurred background,
     centered footage, a burned-in title, and word-highlighted captions

No manual approval step - whatever Claude suggests gets rendered.

Output folder: shorts_output/ (next to this script)
Cache file:    input_transcript.json (next to this script) - if this
               already exists, transcription is skipped on the next run
               to save time. Delete it (or pass --fresh) to force a
               fresh transcription, e.g. after replacing input.mp4.

Requires:
    pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12 anthropic
    ffmpeg on PATH
    ANTHROPIC_API_KEY set as an environment variable
"""

import sys
import os
import json
import subprocess
import textwrap
from pathlib import Path

# ============================================================
# CONFIG - your tuned settings, carried over as-is
# ============================================================

TARGET_CLIP_COUNT = 3
MIN_CLIP_SECONDS = 10
MAX_CLIP_SECONDS = 80
CLIP_MODEL = "claude-fable-5"

OUT_WIDTH = 1080
OUT_HEIGHT = 1920
BLUR_SIGMA = 80
TITLE_FONTSIZE = 64
TITLE_GAP_ABOVE_VIDEO = 100
TITLE_SIDE_MARGIN = 90
TITLE_LINE_SPACING = -30
CAPTION_FONTSIZE = 72
CAPTION_MAX_WORDS_PER_CHUNK = 4
CAPTION_MAX_CHARS_PER_CHUNK = 28
HIGHLIGHT_COLOR_ASS = "&H0000FFFF&"
DEFAULT_TEXT_COLOR_ASS = "&H00FFFFFF&"
FONT_FILE = "C:/Windows/Fonts/arialbd.ttf"
ASS_FONT_NAME = "Arial"

WHISPER_MODEL_SIZE = "large-v3"

# ============================================================
# Windows CUDA DLL fix (from step 1)
# ============================================================
if os.name == "nt":
    try:
        import nvidia.cublas as _cublas
        import nvidia.cudnn as _cudnn
        for pkg in (_cublas, _cudnn):
            for p in pkg.__path__:
                bin_dir = os.path.join(p, "bin")
                if os.path.isdir(bin_dir):
                    os.add_dll_directory(bin_dir)
    except ImportError:
        pass


# ============================================================
# STEP 1: transcription
# ============================================================

def transcribe(video_path: Path) -> dict:
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{WHISPER_MODEL_SIZE}'...")
    try:
        model = WhisperModel(WHISPER_MODEL_SIZE, device="cuda", compute_type="float16")
        print("Using GPU (CUDA).")
    except Exception as e:
        print(f"GPU load failed ({e}), falling back to CPU (slower).")
        model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"Transcribing: {video_path.name}")
    segments_gen, info = model.transcribe(
        str(video_path), word_timestamps=True, vad_filter=True,
    )
    print(f"Detected language: {info.language} (confidence {info.language_probability:.2f})")

    segments_out, words_out, full_text_parts = [], [], []
    for seg in segments_gen:
        seg_dict = {"id": seg.id, "start": round(seg.start, 3),
                    "end": round(seg.end, 3), "text": seg.text.strip()}
        segments_out.append(seg_dict)
        full_text_parts.append(seg.text.strip())
        if seg.words:
            for w in seg.words:
                words_out.append({"word": w.word.strip(),
                                   "start": round(w.start, 3), "end": round(w.end, 3)})
        print(f"  [{seg_dict['start']:>7.1f}s] {seg_dict['text'][:70]}")

    return {
        "source_video": str(video_path),
        "language": info.language,
        "full_text": " ".join(full_text_parts),
        "segments": segments_out,
        "words": words_out,
    }


# ============================================================
# STEP 2: clip suggestion
# ============================================================

def build_prompt(segments, video_duration_minutes):
    lines = [f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}" for s in segments]
    transcript_block = "\n".join(lines)

    return f"""You are selecting standalone clips from a {video_duration_minutes:.0f}-minute \
technical YouTube video transcript (circuit board design / electronics content), to be \
repurposed as YouTube Shorts for an audience of engineers, hobbyists, and students.

Rules:
- Each clip must be self-contained: it should make sense to someone who has NOT watched \
the rest of the video, with no unexplained references to "as I said earlier" etc.
- Clip length must be between {MIN_CLIP_SECONDS} and {MAX_CLIP_SECONDS} seconds.
- Prioritize genuine technical substance over manufactured hype. Favor moments where the \
creator: explains a specific design decision and why it was made, walks through a concrete \
mistake and its fix, explains a technique, tradeoff, or rule of thumb someone could actually \
apply to their own board, or gives a clear specific number/measurement/result with context. \
Do NOT prioritize a moment just because it sounds dramatic or vague ("this changed \
everything") if it lacks real technical content.
- It's fine, even good, if a clip is dense or technical - the audience for this channel wants \
substance, not oversimplified hooks. A clip is strong if a viewer walks away having actually \
learned something concrete, not just because the opening line is punchy.
- Avoid clips that are purely reactive commentary, hype, or vague teasers with no payoff \
within the clip itself.
- Prioritize quality over quantity. Aim for around {TARGET_CLIP_COUNT} clips - only the \
strongest, most substantive moments in the whole video. Do not pad the list to hit a number; \
if fewer than {TARGET_CLIP_COUNT} moments genuinely meet this bar, return fewer.
- Use the exact start/end timestamps from the transcript lines below (you may trim a few \
seconds off either end to tighten the opening, but stay within the source timing).

Transcript (format is [start-end] text):
{transcript_block}

Return ONLY valid JSON (no markdown fences, no prose before or after), matching this schema:
{{
  "clips": [
    {{
      "start": <float seconds>,
      "end": <float seconds>,
      "title": "<short internal label for this clip>",
      "takeaway": "<the concrete technical thing a viewer learns from this clip>",
      "reason": "<one sentence on why this is a genuinely useful standalone clip>"
    }}
  ]
}}
"""


def call_claude(prompt: str):
    from anthropic import Anthropic
    client = Anthropic()
    response = client.messages.create(
        model=CLIP_MODEL, max_tokens=12000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    usage = response.usage
    est_cost = (usage.input_tokens * 10 + usage.output_tokens * 50) / 1_000_000
    print(f"Tokens used: {usage.input_tokens} in / {usage.output_tokens} out (~${est_cost:.3f})")

    if response.stop_reason == "max_tokens":
        print("WARNING: response was cut off before finishing (hit max_tokens).")

    try:
        return json.loads(text)["clips"]
    except json.JSONDecodeError:
        print("\nCould not parse Claude's response as JSON. Raw response was:\n")
        print(text[:2000])
        raise


# ============================================================
# STEP 3: rendering (from step 3, unchanged logic)
# ============================================================

def ffmpeg_escape_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:")


def get_video_dimensions(video_path: Path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def compute_foreground_top(src_width, src_height) -> float:
    fg_height = int(OUT_WIDTH * src_height / src_width)
    if fg_height % 2 != 0:
        fg_height -= 1
    return (OUT_HEIGHT - fg_height) / 2


def load_words_for_clip(transcript_words, clip_start, clip_end):
    result = []
    for w in transcript_words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        rel_start = max(0.0, w["start"] - clip_start)
        rel_end = min(clip_end - clip_start, w["end"] - clip_start)
        result.append({"word": w["word"], "start": rel_start, "end": rel_end})
    return result


def chunk_words(words):
    chunks, current, current_chars = [], [], 0
    for w in words:
        word_len = len(w["word"]) + 1
        overflow = (len(current) >= CAPTION_MAX_WORDS_PER_CHUNK
                    or current_chars + word_len > CAPTION_MAX_CHARS_PER_CHUNK)
        if current and overflow:
            chunks.append(current)
            current, current_chars = [], 0
        current.append(w)
        current_chars += word_len
    if current:
        chunks.append(current)
    return chunks


def seconds_to_ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass_subtitles(words, out_path: Path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_WIDTH}
PlayResY: {OUT_HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{ASS_FONT_NAME},{CAPTION_FONTSIZE},{DEFAULT_TEXT_COLOR_ASS},&H00000000&,&H00000000&,&H00000000&,1,0,0,0,100,100,0,0,1,4,0,2,60,60,500,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for chunk in chunk_words(words):
        for i, active_word in enumerate(chunk):
            start = seconds_to_ass_time(active_word["start"])
            if i + 1 < len(chunk):
                end = seconds_to_ass_time(chunk[i + 1]["start"])
            else:
                end = seconds_to_ass_time(active_word["end"])
            parts = []
            for j, w in enumerate(chunk):
                text = w["word"]
                if j == i:
                    parts.append(f"{{\\c{HIGHLIGHT_COLOR_ASS}}}{text}{{\\c{DEFAULT_TEXT_COLOR_ASS}}}")
                else:
                    parts.append(text)
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{' '.join(parts)}\n")
    out_path.write_text("".join(lines), encoding="utf-8")


def seconds_to_srt_time(t: float) -> str:
    if t < 0:
        t = 0.0
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words, out_path: Path):
    """Plain-text SRT captions (no color highlighting) - the kind YouTube
    accepts as an uploadable caption track, separate from the burned-in
    captions already in the video."""
    lines = []
    for i, chunk in enumerate(chunk_words(words), start=1):
        start = seconds_to_srt_time(chunk[0]["start"])
        end = seconds_to_srt_time(chunk[-1]["end"])
        text = " ".join(w["word"] for w in chunk)
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def build_upload_info(clip: dict, words) -> str:
    """A ready-to-paste title + description for the YouTube upload form."""
    title = clip.get("title", "").strip()
    if len(title) > 95:
        title = title[:92].rstrip() + "..."
    title = f"{title} #Shorts"

    full_text = " ".join(w["word"] for w in words)
    takeaway = clip.get("takeaway", "").strip()

    description = takeaway + "\n\n" + full_text + "\n\n#Shorts #Electronics #PCBDesign"

    return f"TITLE:\n{title}\n\nDESCRIPTION:\n{description}\n"


def wrap_title(text: str) -> str:
    max_width_px = OUT_WIDTH - (2 * TITLE_SIDE_MARGIN)
    avg_char_width_px = TITLE_FONTSIZE * 0.56
    max_chars_per_line = max(8, int(max_width_px / avg_char_width_px))
    return "\n".join(textwrap.wrap(text, width=max_chars_per_line))


def build_filter_complex(ass_path: Path, title_txt_path: Path, fg_top: float) -> str:
    ass_e = ffmpeg_escape_path(ass_path)
    title_e = ffmpeg_escape_path(title_txt_path)
    font_e = ffmpeg_escape_path(Path(FONT_FILE))
    title_bottom = fg_top - TITLE_GAP_ABOVE_VIDEO
    y_expr = f"{title_bottom}-text_h"

    return (
        f"[0:v]scale={OUT_WIDTH}:{OUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUT_WIDTH}:{OUT_HEIGHT},gblur=sigma={BLUR_SIGMA}[bg];"
        f"[0:v]scale={OUT_WIDTH}:-2:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base]drawtext=fontfile='{font_e}':textfile='{title_e}':expansion=none:"
        f"fontsize={TITLE_FONTSIZE}:fontcolor=white:borderw=4:bordercolor=black:"
        f"text_align=C:line_spacing={TITLE_LINE_SPACING}:"
        f"x=(w-text_w)/2:y={y_expr}[titled];"
        f"[titled]subtitles='{ass_e}'[outv]"
    )


def render_clip(source_video, clip, words_all, out_dir, index, fg_top):
    start, end = clip["start"], clip["end"]
    title = clip.get("title", f"clip_{index}")
    print(f"\nRendering clip {index}: {title}  [{start:.1f}s - {end:.1f}s]")

    words = load_words_for_clip(words_all, start, end)
    work_dir = out_dir / "_work"
    work_dir.mkdir(exist_ok=True, parents=True)
    ass_path = work_dir / f"clip_{index}_captions.ass"
    title_txt_path = work_dir / f"clip_{index}_title.txt"

    build_ass_subtitles(words, ass_path)
    title_txt_path.write_text(wrap_title(title), encoding="utf-8")

    srt_path = out_dir / f"{index}.srt"
    build_srt(words, srt_path)

    upload_info_path = out_dir / f"{index}_info.txt"
    upload_info_path.write_text(build_upload_info(clip, words), encoding="utf-8")

    filter_complex = build_filter_complex(ass_path, title_txt_path, fg_top)
    out_file = out_dir / f"{index}.mp4"

    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(source_video), "-t", str(end - start),
           "-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "192k", str(out_file)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFMPEG ERROR:")
        print(result.stderr[-3000:])
        return None
    print(f"Saved: {out_file}")
    return out_file


# ============================================================
# ORCHESTRATION
# ============================================================

def main():
    script_dir = Path(__file__).resolve().parent
    video_path = script_dir / "input.mp4"
    transcript_cache = script_dir / "input_transcript.json"
    candidates_cache = script_dir / "input_clip_candidates.json"

    fresh = "--fresh" in sys.argv

    if not video_path.exists():
        print(f"ERROR: expected a video at {video_path}")
        print('Put your video in this folder and name it exactly "input.mp4".')
        sys.exit(1)

    if "ANTHROPIC_API_KEY" not in os.environ:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    if fresh:
        transcript_cache.unlink(missing_ok=True)
        candidates_cache.unlink(missing_ok=True)

    # ---- Step 1: transcription (cached) ----
    if transcript_cache.exists():
        print(f"Using cached transcript: {transcript_cache}")
        transcript = json.loads(transcript_cache.read_text(encoding="utf-8"))
    else:
        transcript = transcribe(video_path)
        transcript_cache.write_text(json.dumps(transcript, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
        print(f"Transcript cached to {transcript_cache}")

    # ---- Step 2: clip suggestions ----
    segments = transcript["segments"]
    duration_minutes = segments[-1]["end"] / 60 if segments else 0
    print(f"\nVideo is ~{duration_minutes:.0f} min. Asking Claude ({CLIP_MODEL}) for clips...")

    prompt = build_prompt(segments, duration_minutes)
    clips = call_claude(prompt)
    candidates_cache.write_text(json.dumps(clips, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nGot {len(clips)} clip(s) to render (no approval step - rendering all):")
    for c in clips:
        print(f"  [{c['start']:.0f}s-{c['end']:.0f}s] {c['title']}")

    # ---- Step 3: render ----
    out_dir = script_dir / "shorts_output"
    out_dir.mkdir(exist_ok=True)

    src_width, src_height = get_video_dimensions(video_path)
    fg_top = compute_foreground_top(src_width, src_height)
    print(f"\nSource video: {src_width}x{src_height} -> foreground top at y={fg_top:.0f}px")

    for i, clip in enumerate(clips, start=1):
        render_clip(video_path, clip, transcript["words"], out_dir, i, fg_top)

    print(f"\nAll done. {len(clips)} short(s) saved to: {out_dir}")


if __name__ == "__main__":
    main()
