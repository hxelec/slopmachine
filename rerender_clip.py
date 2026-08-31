"""
Re-render ONE clip using its existing _work/clip_N_captions.ass and
_work/clip_N_title.txt files - no API call, no re-transcription.

Use this after manually editing a title.txt (or .ass) file in shorts_output/_work
to fix a single broken clip, without regenerating everything.

Must sit in the same folder as run_pipeline.py and input.mp4.

Usage:
    python rerender_clip.py 5
"""

import sys
from pathlib import Path

# Reuse the actual pipeline code - guarantees the exact same rendering logic
from run_pipeline import (
    ffmpeg_escape_path, build_filter_complex, get_video_dimensions,
    compute_foreground_top, FONT_FILE,
)
import subprocess
import json


def main(index: int):
    script_dir = Path(__file__).resolve().parent
    video_path = script_dir / "input.mp4"
    out_dir = script_dir / "shorts_output"
    work_dir = out_dir / "_work"

    ass_path = work_dir / f"clip_{index}_captions.ass"
    title_txt_path = work_dir / f"clip_{index}_title.txt"

    for p in (video_path, ass_path, title_txt_path):
        if not p.exists():
            print(f"ERROR: expected file not found: {p}")
            sys.exit(1)

    # Need the clip's start/end to know how much of the source video to cut
    candidates = json.loads((script_dir / "input_clip_candidates.json").read_text(encoding="utf-8"))
    clip = candidates[index - 1]
    start, end = clip["start"], clip["end"]

    src_width, src_height = get_video_dimensions(video_path)
    fg_top = compute_foreground_top(src_width, src_height)

    filter_complex = build_filter_complex(ass_path, title_txt_path, fg_top)
    out_file = out_dir / f"{index}.mp4"

    print(f"Re-rendering clip {index} [{start:.1f}s-{end:.1f}s] using edited title/captions...")

    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(video_path), "-t", str(end - start),
           "-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "192k", str(out_file)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFMPEG ERROR:")
        print(result.stderr[-3000:])
        sys.exit(1)

    print(f"Done. Saved: {out_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rerender_clip.py <clip_number>")
        sys.exit(1)
    main(int(sys.argv[1]))
