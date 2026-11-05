#!/usr/bin/env python3
"""
mp3tom4b - merge audiobook MP3 files into a single chaptered M4B for Apple Books.

Requires: ffmpeg and ffprobe on PATH.

This file is both the command-line tool and the conversion engine that the
GUI (mp3tom4b_gui.py) imports.

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
from dataclasses import dataclass, field

VERSION = "2.0.0"
COVER_NAMES = ("cover", "folder", "front", "album", "art")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
AUDIO_EXTS = (".mp3",)


class ConversionError(Exception):
    """Anything that stops a conversion. The message is safe to show a user."""


class Cancelled(ConversionError):
    pass


@dataclass
class Track:
    path: str
    duration: float
    sample_rate: int = 44100
    tags: dict = field(default_factory=dict)
    chapter: str = ""          # chapter title; filled by chapter_title() if empty


# ================================================================ engine

def natural_key(path):
    """Sort 'Part 2' before 'Part 10'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(path))]


def missing_tools():
    return [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]


def is_audio(path):
    return path.lower().endswith(AUDIO_EXTS)


def expand_paths(paths, recursive=False, warn=None):
    """Turn a mix of files and folders into an ordered, de-duplicated list of MP3s."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                found = []
                for root, _, names in os.walk(p):
                    found += [os.path.join(root, n) for n in names if is_audio(n)]
                found.sort(key=lambda f: [natural_key(part) for part in
                                          os.path.relpath(f, p).split(os.sep)])
            else:
                found = sorted((os.path.join(p, n) for n in os.listdir(p) if is_audio(n)),
                               key=natural_key)
            if not found and warn:
                warn(f"no MP3 files in {p}")
            files += found
        elif os.path.isfile(p):
            files.append(p)
        else:
            raise ConversionError(f"not found: {p}")
    seen, unique = set(), []
    for f in files:
        rp = os.path.realpath(f)
        if rp not in seen:
            seen.add(rp)
            unique.append(f)
    return unique


def probe_track(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise ConversionError(f"cannot read {os.path.basename(path)}: "
                              f"{out.stderr.strip() or 'ffprobe failed'}")
    info = json.loads(out.stdout)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise ConversionError(f"no audio in {os.path.basename(path)}")
    dur = float(info.get("format", {}).get("duration") or audio[0].get("duration") or 0)
    if dur <= 0:
        raise ConversionError(f"could not determine the length of {os.path.basename(path)}")
    tags = {k.lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
    return Track(path=path, duration=dur,
                 sample_rate=int(audio[0].get("sample_rate", 44100)), tags=tags)


def chapter_title(track, index, mode="tag"):
    if mode == "number":
        return f"Chapter {index}"
    if mode == "tag" and track.tags.get("title"):
        return track.tags["title"]
    stem = os.path.splitext(os.path.basename(track.path))[0]
    cleaned = re.sub(r"^(track\s*)?\d+\s*[-._)\]]*\s*", "", stem, flags=re.I).strip()
    return cleaned or stem


def guess_metadata(tracks):
    """Title / author / year from the first track's tags, falling back to the folder name."""
    if not tracks:
        return {"title": "", "author": "", "year": ""}
    t = tracks[0].tags
    folder = os.path.basename(os.path.dirname(os.path.abspath(tracks[0].path)))
    return {
        "title": t.get("album") or folder or "Audiobook",
        "author": t.get("album_artist") or t.get("artist") or "",
        "year": (t.get("date") or t.get("year") or "")[:4],
    }


def find_folder_cover(paths):
    for d in dict.fromkeys(os.path.dirname(os.path.abspath(f)) for f in paths):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            stem, ext = os.path.splitext(name.lower())
            if ext in IMAGE_EXTS and stem in COVER_NAMES:
                return os.path.join(d, name)
    return None


def extract_embedded_cover(mp3, dest):
    """Write the MP3's embedded art to dest (a .jpg path). Returns dest or None."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3, "-an",
                        "-map", "0:v:0", "-frames:v", "1", dest], capture_output=True)
    return dest if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) else None


def default_output(title, folder=""):
    name = re.sub(r'[\\/:*?"<>|]', "_", title or "Audiobook").strip() or "Audiobook"
    return os.path.join(folder, name + ".m4b")


def _ffmeta_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", str(s))


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def convert(tracks, output, *, title, author="", narrator="", year="", genre="Audiobook",
            description="", cover=None, chapters=True, bitrate="64k", sample_rate=None,
            mono=False, overwrite=False, progress=None, should_cancel=None):
    """
    Encode tracks (in order) into one M4B at `output`.

    progress(fraction 0..1, seconds_done) is called during encoding.
    should_cancel() is polled; return True to stop. Raises Cancelled / ConversionError.
    Each track's .chapter is used as its chapter title (filled in if empty).
    """
    if missing := missing_tools():
        raise ConversionError(f"{' and '.join(missing)} not found. Install ffmpeg first.")
    if not tracks:
        raise ConversionError("no MP3 files to convert")
    if not output.lower().endswith((".m4b", ".m4a", ".mp4")):
        output += ".m4b"
    if os.path.exists(output) and not overwrite:
        raise ConversionError(f"{output} already exists")
    if cover and not os.path.isfile(cover):
        raise ConversionError(f"cover image not found: {cover}")
    out_dir = os.path.dirname(os.path.abspath(output))
    if not os.path.isdir(out_dir):
        raise ConversionError(f"folder does not exist: {out_dir}")

    total = sum(t.duration for t in tracks)
    workdir = tempfile.mkdtemp(prefix="mp3tom4b_")
    proc = None
    try:
        listfile = os.path.join(workdir, "files.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for t in tracks:
                p = os.path.abspath(t.path).replace("'", "'\\''")
                fh.write(f"file '{p}'\n")

        metafile = os.path.join(workdir, "meta.txt")
        with open(metafile, "w", encoding="utf-8") as fh:
            fh.write(";FFMETADATA1\n")
            meta = {"title": title, "album": title, "artist": author, "album_artist": author,
                    "genre": genre, "media_type": "2", "date": year, "composer": narrator,
                    "comment": description, "description": description}
            for k, v in meta.items():
                if v:
                    fh.write(f"{k}={_ffmeta_escape(v)}\n")
            if chapters:
                pos = 0.0
                for i, t in enumerate(tracks, 1):
                    name = t.chapter or chapter_title(t, i)
                    fh.write(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(pos * 1000)}\n"
                             f"END={int((pos + t.duration) * 1000)}\n"
                             f"title={_ffmeta_escape(name)}\n")
                    pos += t.duration

        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", listfile, "-i", metafile]
        if cover:
            cmd += ["-i", cover]
        cmd += ["-map", "0:a:0"]
        if cover:
            cmd += ["-map", "2:v:0", "-c:v", "png" if cover.lower().endswith(".png") else "mjpeg",
                    "-vf", "scale='min(1400,iw)':-2", "-disposition:v:0", "attached_pic"]
        cmd += ["-map_metadata", "1", "-map_chapters", "1" if chapters else "-1",
                "-c:a", "aac", "-b:a", bitrate,
                "-ar", str(sample_rate or min(max(t.sample_rate for t in tracks), 48000)),
                "-ac", "1" if mono else "2",
                "-movflags", "+faststart", "-f", "ipod"]
        if output.lower().endswith(".m4b"):
            cmd += ["-brand", "M4B "]
        cmd += ["-progress", "pipe:1", "-nostats", output]

        errfile = open(os.path.join(workdir, "ffmpeg.log"), "w+", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile,
                                stdin=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            if should_cancel and should_cancel():
                proc.kill()
                proc.wait()
                raise Cancelled("cancelled")
            if line.startswith("out_time_us=") and progress:
                try:
                    done = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                progress(min(1.0, done / total) if total else 0.0, done)
        proc.wait()
        if should_cancel and should_cancel():
            raise Cancelled("cancelled")
        if proc.returncode != 0:
            errfile.seek(0)
            raise ConversionError("ffmpeg failed:\n" + (errfile.read().strip() or "unknown error"))
        if progress:
            progress(1.0, total)
        return output
    except BaseException:
        if proc and proc.poll() is None:
            proc.kill()
        if os.path.exists(output) and proc is not None:
            os.remove(output)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ================================================================ command line

def main():
    p = argparse.ArgumentParser(
        prog="mp3tom4b",
        description="Merge audiobook MP3 files into one chaptered M4B for Apple devices.",
        epilog="Files inside a folder are ordered naturally (2 before 10). "
               "Files named on the command line keep the order you give. "
               "Run mp3tom4b-gui for the graphical version.")
    p.add_argument("inputs", nargs="+", help="MP3 files and/or folders containing MP3s")
    p.add_argument("-o", "--output", help="output file (default: <title>.m4b)")
    p.add_argument("-t", "--title", help="book title (default: album tag or folder name)")
    p.add_argument("-a", "--author", help="author (default: artist tag)")
    p.add_argument("-n", "--narrator", help="narrator")
    p.add_argument("-y", "--year", help="publication year")
    p.add_argument("-g", "--genre", default="Audiobook", help="genre (default: Audiobook)")
    p.add_argument("-d", "--description", default="", help="description / summary")
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
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    args = p.parse_args()

    def die(msg, code=1):
        print(f"error: {msg}", file=sys.stderr)
        sys.exit(code)

    try:
        if missing := missing_tools():
            die(f"{' and '.join(missing)} not found. Install ffmpeg "
                "(Arch: sudo pacman -S ffmpeg, Debian/Ubuntu: sudo apt install ffmpeg)")
        files = expand_paths(args.inputs, args.recursive,
                             warn=lambda m: print(f"warning: {m}", file=sys.stderr))
        if not files:
            die("no MP3 files to convert")
        if args.reverse:
            files.reverse()

        print(f"Reading {len(files)} file(s)...")
        tracks = [probe_track(f) for f in files]
        for i, t in enumerate(tracks, 1):
            t.chapter = chapter_title(t, i, args.chapter_names)

        guess = guess_metadata(tracks)
        title = args.title or guess["title"]
        author = args.author or guess["author"]
        year = args.year or guess["year"]
        output = args.output or default_output(title)
        if not output.lower().endswith((".m4b", ".m4a", ".mp4")):
            output += ".m4b"
        if os.path.exists(output) and not args.force and not args.dry_run:
            die(f"{output} already exists (use -f to overwrite)")

        with tempfile.TemporaryDirectory(prefix="mp3tom4b_cover_") as tmp:
            cover = None
            if not args.no_cover:
                cover = args.cover or find_folder_cover(files) or \
                    extract_embedded_cover(files[0], os.path.join(tmp, "cover.jpg"))
            total = sum(t.duration for t in tracks)

            print(f"Title:    {title}")
            print(f"Author:   {author or '(none)'}")
            if args.narrator:
                print(f"Narrator: {args.narrator}")
            print(f"Chapters: {0 if args.no_chapters else len(tracks)}")
            print(f"Cover:    {('embedded art' if cover and cover.startswith(tmp) else os.path.basename(cover)) if cover else '(none)'}")
            print(f"Length:   {fmt_time(total)}  ->  {output}")

            if args.dry_run:
                print("\n(dry run) chapter list:")
                pos = 0.0
                for t in tracks:
                    print(f"  {fmt_time(pos)}  {t.chapter}")
                    pos += t.duration
                return

            tty = sys.stdout.isatty()

            def show(frac, done):
                if tty:
                    bar = "#" * int(frac * 40)
                    print(f"\rEncoding [{bar:<40}] {frac * 100:5.1f}%  {fmt_time(done)}",
                          end="", flush=True)

            out = convert(tracks, output, title=title, author=author, narrator=args.narrator or "",
                          year=year, genre=args.genre, description=args.description, cover=cover,
                          chapters=not args.no_chapters, bitrate=args.bitrate,
                          sample_rate=args.sample_rate, mono=args.mono, overwrite=args.force,
                          progress=show)
            if tty:
                print()
            print(f"Done: {out} ({os.path.getsize(out) / 1024 / 1024:.1f} MB)")
    except ConversionError as e:
        die(str(e))
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
