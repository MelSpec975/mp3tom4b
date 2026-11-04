#!/usr/bin/env python3
"""
mp3tom4b - merge audiobook MP3 files into a single chaptered M4B for Apple Books.

Requires: ffmpeg and ffprobe on PATH (sudo apt install ffmpeg).

Examples:
  mp3tom4b ~/Audiobooks/Dune/                       # every MP3 in a folder
  mp3tom4b part*.mp3 -o "Dune.m4b" --title Dune --author "Frank Herbert"
  mp3tom4b disc1/ disc2/ --cover cover.jpg --bitrate 96k --mono
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = "1.0.0"
COVER_NAMES = ("cover", "folder", "front", "album", "art")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")


# ---------------------------------------------------------------- helpers

def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def natural_key(path):
    """Sort 'Part 2' before 'Part 10'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(path))]


def check_tools():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} not found. Install it with: sudo apt install ffmpeg")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


def tags_of(info):
    tags = {k.lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
    return tags


def collect_inputs(paths, recursive):
    files = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                found = []
                for root, _, names in os.walk(p):
                    found += [os.path.join(root, n) for n in names
                              if n.lower().endswith(".mp3")]
                # sort by relative path so disc1/ comes before disc2/
                found.sort(key=lambda f: [natural_key(part) for part in
                                          os.path.relpath(f, p).split(os.sep)])
            else:
                found = sorted((os.path.join(p, n) for n in os.listdir(p)
                                if n.lower().endswith(".mp3")), key=natural_key)
            if not found:
                print(f"warning: no MP3 files in {p}", file=sys.stderr)
            files += found
        elif os.path.isfile(p):
            files.append(p)
        else:
            die(f"not found: {p}")
    # drop duplicates, keep order
    seen, unique = set(), []
    for f in files:
        rp = os.path.realpath(f)
        if rp not in seen:
            seen.add(rp)
            unique.append(f)
    return unique


def find_folder_cover(files):
    for d in dict.fromkeys(os.path.dirname(os.path.abspath(f)) for f in files):
        for name in sorted(os.listdir(d)):
            stem, ext = os.path.splitext(name.lower())
            if ext in IMAGE_EXTS and stem in COVER_NAMES:
                return os.path.join(d, name)
    return None


def extract_embedded_cover(mp3, workdir):
    out = os.path.join(workdir, "embedded_cover.jpg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3, "-an",
                        "-map", "0:v:0", "-frames:v", "1", out],
                       capture_output=True)
    return out if r.returncode == 0 and os.path.getsize(out) > 0 else None


def ffmeta_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", str(s))


def chapter_title(path, tags, index, mode):
    if mode == "number":
        return f"Chapter {index}"
    if mode == "tag" and tags.get("title"):
        return tags["title"]
    stem = os.path.splitext(os.path.basename(path))[0]
    # strip leading track numbers like "01 - ", "01. ", "Track 01_"
    cleaned = re.sub(r"^(track\s*)?\d+\s*[-._)\]]*\s*", "", stem, flags=re.I).strip()
    return cleaned or stem


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------- main work

def build(args):
    check_tools()
    files = collect_inputs(args.inputs, args.recursive)
    if not files:
        die("no MP3 files to convert")
    if args.reverse:
        files.reverse()

    print(f"Reading {len(files)} file(s)...")
    entries, total, sample_rates = [], 0.0, set()
    for i, f in enumerate(files, 1):
        try:
            info = probe(f)
        except Exception as e:
            die(f"cannot read {f}: {e}")
        audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
        if not audio:
            die(f"no audio stream in {f}")
        dur = float(info["format"].get("duration") or audio[0].get("duration") or 0)
        if dur <= 0:
            die(f"could not determine duration of {f}")
        sample_rates.add(int(audio[0].get("sample_rate", 44100)))
        entries.append({"path": f, "dur": dur, "tags": tags_of(info)})
        total += dur

    first = entries[0]["tags"]
    title = args.title or first.get("album") or \
        os.path.basename(os.path.dirname(os.path.abspath(files[0]))) or "Audiobook"
    author = args.author or first.get("album_artist") or first.get("artist") or ""
    year = args.year or (first.get("date") or first.get("year") or "")[:4]
    genre = args.genre or "Audiobook"

    output = args.output or re.sub(r'[\\/:*?"<>|]', "_", title) + ".m4b"
    if not output.lower().endswith((".m4b", ".m4a", ".mp4")):
        output += ".m4b"
    if os.path.exists(output) and not args.force:
        die(f"{output} already exists (use -f to overwrite)")

    workdir = tempfile.mkdtemp(prefix="mp3tom4b_")
    try:
        # cover art: explicit > folder image > embedded in first mp3
        cover = None
        if not args.no_cover:
            if args.cover:
                if not os.path.isfile(args.cover):
                    die(f"cover not found: {args.cover}")
                cover = args.cover
            else:
                cover = find_folder_cover(files) or extract_embedded_cover(files[0], workdir)

        # concat list
        listfile = os.path.join(workdir, "files.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for e in entries:
                p = os.path.abspath(e["path"]).replace("'", "'\\''")
                fh.write(f"file '{p}'\n")

        # metadata + chapters
        metafile = os.path.join(workdir, "meta.txt")
        with open(metafile, "w", encoding="utf-8") as fh:
            fh.write(";FFMETADATA1\n")
            meta = {"title": title, "album": title, "artist": author,
                    "album_artist": author, "genre": genre, "media_type": "2"}
            if year:
                meta["date"] = year
            if args.narrator:
                meta["composer"] = args.narrator
            if args.description:
                meta["comment"] = args.description
                meta["description"] = args.description
            for k, v in meta.items():
                if v:
                    fh.write(f"{k}={ffmeta_escape(v)}\n")
            if not args.no_chapters:
                t = 0.0
                for i, e in enumerate(entries, 1):
                    start, end = int(t * 1000), int((t + e["dur"]) * 1000)
                    fh.write("\n[CHAPTER]\nTIMEBASE=1/1000\n")
                    fh.write(f"START={start}\nEND={end}\n")
                    name = chapter_title(e["path"], e["tags"], i, args.chapter_names)
                    fh.write(f"title={ffmeta_escape(name)}\n")
                    t += e["dur"]

        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", listfile,
               "-i", metafile]
        if cover:
            cmd += ["-i", cover]
        cmd += ["-map", "0:a:0"]
        if cover:
            cmd += ["-map", "2:v:0", "-c:v", "mjpeg" if not cover.lower().endswith(".png") else "png",
                    "-vf", "scale='min(1400,iw)':-2", "-disposition:v:0", "attached_pic"]
        cmd += ["-map_metadata", "1", "-map_chapters", "1" if not args.no_chapters else "-1",
                "-c:a", "aac", "-b:a", args.bitrate,
                "-ar", str(args.sample_rate or min(max(sample_rates), 48000)),
                "-ac", "1" if args.mono else "2",
                "-movflags", "+faststart",
                "-f", "ipod"] + (["-brand", "M4B "] if output.lower().endswith(".m4b") else []) + [
                "-progress", "pipe:1", "-nostats",
                output]

        print(f"Title:    {title}")
        print(f"Author:   {author or '(none)'}")
        if args.narrator:
            print(f"Narrator: {args.narrator}")
        print(f"Chapters: {0 if args.no_chapters else len(entries)}")
        print(f"Cover:    {os.path.basename(cover) if cover else '(none)'}")
        print(f"Length:   {fmt_time(total)}  ->  {output}")

        if args.dry_run:
            print("\n(dry run) chapter list:")
            t = 0.0
            for i, e in enumerate(entries, 1):
                print(f"  {fmt_time(t)}  {chapter_title(e['path'], e['tags'], i, args.chapter_names)}")
                t += e["dur"]
            return

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        tty = sys.stdout.isatty()
        for line in proc.stdout:
            if line.startswith("out_time_us=") and tty:
                try:
                    done = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                pct = min(100.0, done / total * 100) if total else 0
                bar = "#" * int(pct / 2.5)
                print(f"\rEncoding [{bar:<40}] {pct:5.1f}%  {fmt_time(done)}", end="", flush=True)
        err = proc.stderr.read()
        proc.wait()
        if tty:
            print()
        if proc.returncode != 0:
            if os.path.exists(output):
                os.remove(output)
            die(f"ffmpeg failed:\n{err.strip()}")

        size = os.path.getsize(output) / 1024 / 1024
        print(f"Done: {output} ({size:.1f} MB)")
    finally:
        if args.keep_temp:
            print(f"temp files kept in {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(
        prog="mp3tom4b",
        description="Merge audiobook MP3 files into one chaptered M4B for Apple devices.",
        epilog="Files inside a folder are ordered naturally (2 before 10). "
               "Files named on the command line keep the order you give.")
    p.add_argument("inputs", nargs="+", help="MP3 files and/or folders containing MP3s")
    p.add_argument("-o", "--output", help="output file (default: <title>.m4b)")
    p.add_argument("-t", "--title", help="book title (default: album tag or folder name)")
    p.add_argument("-a", "--author", help="author (default: artist tag)")
    p.add_argument("-n", "--narrator", help="narrator")
    p.add_argument("-y", "--year", help="publication year")
    p.add_argument("-g", "--genre", help="genre (default: Audiobook)")
    p.add_argument("-d", "--description", help="description / summary")
    p.add_argument("-c", "--cover", help="cover image (default: cover.jpg/folder.jpg or embedded art)")
    p.add_argument("--no-cover", action="store_true", help="don't embed cover art")
    p.add_argument("-b", "--bitrate", default="64k", help="AAC bitrate (default: 64k; 96k-128k for music-heavy books)")
    p.add_argument("--sample-rate", type=int, help="output sample rate (default: match input)")
    p.add_argument("--mono", action="store_true", help="downmix to mono (halves size, fine for speech)")
    p.add_argument("--chapter-names", choices=["tag", "filename", "number"], default="tag",
                   help="chapter titles from ID3 title, the filename, or 'Chapter N' (default: tag, falls back to filename)")
    p.add_argument("--no-chapters", action="store_true", help="don't create chapters")
    p.add_argument("-r", "--recursive", action="store_true", help="search folders recursively (e.g. Disc 1/, Disc 2/)")
    p.add_argument("--reverse", action="store_true", help="reverse the file order")
    p.add_argument("-f", "--force", action="store_true", help="overwrite the output file")
    p.add_argument("--dry-run", action="store_true", help="show what would happen, don't encode")
    p.add_argument("--keep-temp", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    args = p.parse_args()
    try:
        build(args)
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
