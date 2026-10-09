#!/usr/bin/env python3
"""
SkySail gallery builder
-----------------------
Drop photos AND videos into the  gallery/  folder, then run:

    python build_gallery.py            # rebuild once
    python build_gallery.py --watch    # keep running: new files are added automatically

Photos  (.jpg .jpeg .png .webp .heic): shrunk for the web and stored inside index.html.
Videos  (.mp4 .mov .m4v .webm): converted to a small web-friendly MP4 (max 1280 px, H.264) with a
        preview picture, saved in the  media/  folder next to index.html, and played from there.
        Needs ffmpeg (the GitHub workflow installs it for you).

Naming (optional):  "07 - Sunrise over the farms, Bengaluru.jpg"
  - the number sets the order (files are sorted by name, photos and videos together)
  - the text after the dash becomes the caption
  - camera-style names (IMG_1234.jpg, WhatsApp Image ..., Untitled.jpg) get a default caption

Needs:  pip install pillow   (add pillow-heif to also accept iPhone .heic photos)
"""
import argparse, base64, hashlib, io, re, shutil, subprocess, sys, time
from pathlib import Path
try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is missing. Install it with:  pip install pillow")

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
try:                                   # iPhone photos (.heic) work if pillow-heif is installed
    from pillow_heif import register_heif_opener
    register_heif_opener()
    EXTS |= {".heic", ".heif"}
except ImportError:
    pass

BLOCK = re.compile(r"/\* GALLERY:AUTO-START.*?\*/.*?/\* GALLERY:AUTO-END \*/", re.S)
GENERIC = re.compile(r"^(img|dsc|pxl|mvimg|screenshot|whatsapp|untitled|photo|image|thumbnail|vid|video|mov)", re.I)
DEFAULT_CAPTION = "SkySail flight photo"
DEFAULT_VIDEO_CAPTION = "SkySail flight video"


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name.lower())]


def caption_for(p: Path) -> str:
    stem = re.sub(r"^\s*\d+\s*[-_.)]*\s*", "", p.stem).replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" -")
    if not stem or GENERIC.match(stem):
        return DEFAULT_VIDEO_CAPTION if p.suffix.lower() in VIDEO_EXTS else DEFAULT_CAPTION
    return stem


def to_data_uri(p: Path, max_side: int, quality: int, cache: Path) -> str:
    raw = p.read_bytes()
    key = hashlib.sha1(raw + f"{max_side}:{quality}".encode()).hexdigest()
    hit = cache / f"{key}.webp"
    if hit.exists():
        data = hit.read_bytes()
    else:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=quality, method=4)
        data = buf.getvalue()
        cache.mkdir(exist_ok=True)
        hit.write_bytes(data)
    return "data:image/webp;base64," + base64.b64encode(data).decode()


def file_hash(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_video(p: Path, media: Path, max_seconds: int):
    """Convert a video to a small MP4 + preview picture. Returns (mp4 path, poster path)."""
    name = f"{re.sub(r'[^a-z0-9]+', '-', caption_for(p).lower()).strip('-')[:40] or 'video'}-{file_hash(p)[:8]}"
    out, poster = media / f"{name}.mp4", media / f"{name}.jpg"
    if out.exists() and out.stat().st_size > 0 and poster.exists():
        return out, poster                                   # already converted
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed, so videos can't be converted")
    media.mkdir(exist_ok=True)
    scale = "scale=w='if(gt(iw,ih),min(1280,iw),-2)':h='if(gt(iw,ih),-2,min(1280,ih))'"
    run = lambda cmd: subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    try:
        run([ffmpeg, "-y", "-v", "error", "-i", str(p), "-t", str(max_seconds), "-vf", scale,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)])
        for ss in ("1", "0"):                                # picture from 1 s in, or the first frame for tiny clips
            poster.unlink(missing_ok=True)
            run([ffmpeg, "-y", "-v", "error", "-ss", ss, "-i", str(out), "-frames:v", "1",
                 "-vf", "scale=w='min(900,iw)':h=-2", "-q:v", "4", str(poster)])
            if poster.exists() and poster.stat().st_size > 0:
                break
        else:
            raise RuntimeError("couldn't make a preview picture")
    except subprocess.CalledProcessError as e:
        out.unlink(missing_ok=True)
        raise RuntimeError(e.stderr.decode(errors="ignore").strip().splitlines()[-1] if e.stderr else "ffmpeg failed")
    return out, poster


def js_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\u003c") + '"'


def build(html: Path, folder: Path, max_side: int, quality: int, max_seconds: int) -> int:
    if not html.exists():
        sys.exit(f"Can't find {html}")
    text = html.read_text(encoding="utf-8")
    if not BLOCK.search(text):
        sys.exit("The GALLERY:AUTO-START / GALLERY:AUTO-END markers are missing from the HTML.")
    files = sorted((p for p in folder.iterdir()
                    if p.suffix.lower() in EXTS | VIDEO_EXTS and not p.name.startswith(".")), key=natural_key)
    if not files:
        print("  ! the gallery folder is empty, so the page was left unchanged.")
        return -1
    media = html.parent / "media"
    keep, rows, n_vid = set(), [], 0
    for p in files:
        try:
            if p.suffix.lower() in VIDEO_EXTS:
                out, poster = process_video(p, media, max_seconds)
                keep |= {out.name, poster.name}
                rows.append(f"  {{ type:\"video\", src:{js_str('media/' + out.name)}, "
                            f"poster:{js_str('media/' + poster.name)}, caption:{js_str(caption_for(p))} }}")
                n_vid += 1
            else:
                rows.append(f"  {{ src:{js_str(to_data_uri(p, max_side, quality, folder.parent / '.gallery_cache'))}, "
                            f"caption:{js_str(caption_for(p))} }}")
        except Exception as e:  # unreadable, not really a media file, or conversion failed
            print(f"  ! skipped {p.name}: {e}")
    if media.exists():                                       # remove converted files whose original was deleted
        for old in media.iterdir():
            if old.is_file() and old.name not in keep:
                old.unlink()
    block = ("/* GALLERY:AUTO-START (generated by build_gallery.py, do not edit by hand) */\n"
             "const GALLERY = [\n" + ",\n".join(rows) + "\n];\n/* GALLERY:AUTO-END */")
    new = BLOCK.sub(lambda m: block, text, count=1)
    if new != text:
        tmp = html.with_suffix(".tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(html)
    print(f"  {len(rows) - n_vid} photos, {n_vid} videos")
    return len(rows)


def signature(folder: Path):
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                        for p in folder.iterdir() if p.suffix.lower() in EXTS | VIDEO_EXTS))


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Add every photo and video in gallery/ to the SkySail Gallery page.")
    ap.add_argument("--html", default=str(here / "index.html"))
    ap.add_argument("--folder", default=str(here / "gallery"))
    ap.add_argument("--max-size", type=int, default=1400, help="longest side of photos in pixels (default 1400)")
    ap.add_argument("--quality", type=int, default=80, help="photo WebP quality 1-100 (default 80)")
    ap.add_argument("--max-seconds", type=int, default=90, help="longest video length kept, in seconds (default 90)")
    ap.add_argument("--watch", action="store_true", help="keep running and rebuild when the folder changes")
    a = ap.parse_args()
    html, folder = Path(a.html), Path(a.folder)
    folder.mkdir(exist_ok=True)

    n = build(html, folder, a.max_size, a.quality, a.max_seconds)
    if n >= 0:
        print(f"Gallery built: {n} items -> {html.name} ({html.stat().st_size/1e6:.1f} MB)")
    if not a.watch:
        return
    print(f"Watching {folder} ... drop photos and videos in. Press Ctrl+C to stop.")
    last = signature(folder)
    try:
        while True:
            time.sleep(2)
            cur = signature(folder)
            if cur != last:
                time.sleep(1.5)                      # let a big copy finish
                cur = signature(folder)
                n = build(html, folder, a.max_size, a.quality, a.max_seconds)
                print(f"{time.strftime('%H:%M:%S')}  rebuilt: {n} items")
                last = cur
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
