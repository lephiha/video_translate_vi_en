def _format_timestamp(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(segments: list[dict], output_path: str, text_field: str = "text") -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        text = (seg.get(text_field) or "").strip()
        if not text:
            continue
        start = _format_timestamp(seg["start"])
        end = _format_timestamp(seg["end"])
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path
