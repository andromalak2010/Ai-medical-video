"""Build a timed draft of a video from a shot list (placeholder backgrounds + Arabic on-screen text).

Replace the placeholder color cards with generated clips later; the timing and text stay the same.

Usage: python scripts/build_draft.py <out.mp4> <out.ass>
"""
import subprocess
import sys

W, H, FPS = 1080, 1920, 30

# (start_sec, end_sec, on_screen_text, placeholder_color_hex)
SHOTS = [
    (0, 4, "اختناق مفاجئ!", "3FB8B0"),
    (4, 8, "", "2E8F88"),
    (8, 12, "١) ٥ خبطات بين الكتفين", "3FB8B0"),
    (12, 16, "٢) ٥ ضغطات على البطن", "2E8F88"),
    (16, 20, "كرر لحد ما تطلع الحتة", "3FB8B0"),
    (20, 25, "٣) وقع؟ اتصل بـ ١٢٣", "2E8F88"),
    (25, 30, "", "3FB8B0"),
    (30, 35, "غلط شائع!", "2E8F88"),
    (35, 38, "Dr.Andro Malak", "3FB8B0"),
]

DISCLAIMER = "للتثقيف الصحي فقط، وليس نصيحة طبية"


def ts(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def write_ass(path, total):
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
        "Style: Big,FreeSerif,110,&H00FFFFFF,&H00000000,&H80000000,-1,1,7,2,5,60,60,0",
        "Style: Small,FreeSerif,50,&H00FFFFFF,&H00000000,&H80000000,0,1,4,1,2,60,60,420",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Text",
    ]
    for start, end, text, _ in SHOTS:
        if text:
            lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Big,{{\\an5}}{text}")
    lines.append(f"Dialogue: 0,{ts(0)},{ts(total)},Small,{DISCLAIMER}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(out_mp4, out_ass):
    total = SHOTS[-1][1]
    write_ass(out_ass, total)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for start, end, _, color in SHOTS:
        cmd += ["-f", "lavfi", "-t", str(end - start), "-i", f"color=c=0x{color}:s={W}x{H}:r={FPS}"]
    n = len(SHOTS)
    labels = "".join(f"[{i}:v]" for i in range(n))
    cmd += ["-filter_complex", f"{labels}concat=n={n}:v=1:a=0[v];[v]subtitles={out_ass}[out]",
            "-map", "[out]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", out_mp4]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
