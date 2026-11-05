#!/usr/bin/env python3
"""
mp3tom4b - merge audiobook MP3 files into one chaptered audiobook file.

Output formats: M4B (Apple Books), MP3 with chapters, Opus, and MKA.

Requires: ffmpeg and ffprobe on PATH.

This file is both the command-line tool and the conversion engine that the
GUI (mp3tom4b_gui.py) imports.

Examples:
  mp3tom4b ~/Audiobooks/Dune/                       # every MP3 in a folder
  mp3tom4b part*.mp3 -o "Dune.m4b" --title Dune --author "Frank Herbert"
  mp3tom4b disc1/ disc2/ --cover cover.jpg --bitrate 96k --mono
  mp3tom4b ~/Audiobooks/Dune/ --format opus         # smallest files
  mp3tom4b ~/Audiobooks/Dune/ -o Dune.mp3           # one chaptered MP3, no re-encode
"""

import argparse
import base64
import functools
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

VERSION = "2.1.0"
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
    channels: int = 2
    codec: str = "mp3"
    chapters: int = 0          # chapters already inside this file
    tags: dict = field(default_factory=dict)
    chapter: str = ""          # chapter title; filled by chapter_title() if empty


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    ext: str
    muxer: str
    encoder: str               # ffmpeg encoder used when re-encoding
    qualities: tuple           # (value, label); value is a bitrate like "64k" or "copy"
    default_quality: str
    copy_fallback: str = ""    # bitrate used when "copy" isn't possible
    hint: str = ""


FORMATS = {
    "m4b": Format(
        "m4b", "M4B (Apple Books)", ".m4b", "ipod", "aac",
        (("32k", "32 kbps (smallest)"), ("48k", "48 kbps"), ("64k", "64 kbps (good for speech)"),
         ("96k", "96 kbps"), ("128k", "128 kbps (music-heavy books)")), "64k",
        hint="For Apple Books on iPhone, iPad and Mac, and most audiobook apps."),
    "mp3": Format(
        "mp3", "MP3 with chapters", ".mp3", "mp3", "libmp3lame",
        (("copy", "Original (no re-encode, fastest)"), ("64k", "Re-encode at 64 kbps"),
         ("96k", "Re-encode at 96 kbps"), ("128k", "Re-encode at 128 kbps")), "copy", "96k",
        hint="Plays anywhere. Chapters work in podcast and Android apps, not Apple Books."),
    "opus": Format(
        "opus", "Opus (smallest files)", ".opus", "opus", "libopus",
        (("16k", "16 kbps (tiny)"), ("24k", "24 kbps"), ("32k", "32 kbps (good for speech)"),
         ("48k", "48 kbps"), ("64k", "64 kbps")), "32k",
        hint="About half the size of M4B. Plays in VLC, mpv and many Android apps."),
    "mka": Format(
        "mka", "MKA (archival)", ".mka", "matroska", "libopus",
        (("copy", "Original audio (no re-encode)"), ("32k", "Opus 32 kbps"),
         ("48k", "Opus 48 kbps"), ("64k", "Opus 64 kbps")), "copy", "48k",
        hint="Keeps the original audio untouched. Best in VLC and mpv on a computer."),
}
EXT_TO_FORMAT = {".m4b": "m4b", ".m4a": "m4b", ".mp4": "m4b", ".mp3": "mp3",
                 ".opus": "opus", ".ogg": "opus", ".mka": "mka"}


def format_for_path(path):
    """Format key implied by a file name's extension, or None."""
    return EXT_TO_FORMAT.get(os.path.splitext(path)[1].lower())


def with_extension(path, fmt):
    """Make path end in fmt's extension: keep a matching one, swap another, or append."""
    root, ext = os.path.splitext(path)
    if EXT_TO_FORMAT.get(ext.lower()) == fmt:
        return path
    if ext.lower() in EXT_TO_FORMAT:
        return root + FORMATS[fmt].ext
    return path + FORMATS[fmt].ext


def copy_problem(tracks):
    """Why the MP3s can't be joined without re-encoding, or '' if they can."""
    if any(t.codec != "mp3" for t in tracks):
        return "some files aren't MP3 audio"
    if len({t.sample_rate for t in tracks}) > 1:
        return "the files use different sample rates"
    if len({t.channels for t in tracks}) > 1:
        return "some files are mono and some are stereo"
    return ""


@functools.lru_cache(maxsize=1)
def _encoders():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return {line.split()[1] for line in r.stdout.splitlines()
            if len(line.split()) > 1 and line.startswith(" A")}


# ================================================================ engine

def natural_key(path):
    """Sort 'Part 2' before 'Part 10'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(path))]


def missing_tools():
    return [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]


def is_audio(path):
    return path.lower().endswith(AUDIO_EXTS)


def expand_paths(paths, recursive=False, warn=None, found_in_folder=None):
    """
    Turn a mix of files and folders into an ordered, de-duplicated list of MP3s.
    If found_in_folder is a set, the real paths of files discovered by scanning a
    folder (rather than named directly) are added to it.
    """
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
            if found_in_folder is not None:
                found_in_folder.update(os.path.realpath(f) for f in found)
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
         "-show_format", "-show_streams", "-show_chapters", path],
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
                 sample_rate=int(audio[0].get("sample_rate", 44100)),
                 channels=int(audio[0].get("channels", 2)),
                 codec=audio[0].get("codec_name", "mp3"),
                 chapters=len(info.get("chapters", [])), tags=tags)


def looks_combined(track):
    """An MP3 that already has chapters is almost certainly a book this tool (or
    another) already joined, e.g. a previous MP3 output saved into the same folder."""
    return track.chapters > 1


def combined_note(track):
    return (f"skipped {os.path.basename(track.path)}: it already has {track.chapters} "
            "chapters, so it looks like a combined audiobook (add it on its own to include it)")


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


def default_output(title, folder="", fmt="m4b"):
    name = re.sub(r'[\\/:*?"<>|]', "_", title or "Audiobook").strip() or "Audiobook"
    return os.path.join(folder, name + FORMATS[fmt].ext)


def _ffmeta_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", str(s))


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _prepare_cover(cover, workdir, max_size):
    """Re-save the cover as a JPEG no larger than max_size. Returns (path, width, height)."""
    out = os.path.join(workdir, "cover.jpg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", cover, "-frames:v", "1",
                        "-vf", f"scale='min({max_size},iw)':-2", "-q:v", "3", out],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise ConversionError(f"couldn't read the cover image {os.path.basename(cover)}")
    dims = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", out],
                          capture_output=True, text=True).stdout.strip().split(",")
    try:
        w, h = int(dims[0]), int(dims[1])
    except (ValueError, IndexError):
        w = h = 0
    return out, w, h


def _opus_picture_tag(jpeg, width, height):
    """Ogg/Opus cover art: a FLAC picture block, base64-encoded (METADATA_BLOCK_PICTURE)."""
    img = open(jpeg, "rb").read()
    mime = b"image/jpeg"
    block = (struct.pack(">II", 3, len(mime)) + mime + struct.pack(">I", 0) +
             struct.pack(">IIIII", width, height, 24, 0, len(img)) + img)
    return base64.b64encode(block).decode("ascii")


def convert(tracks, output, *, title, author="", narrator="", year="", genre="Audiobook",
            description="", cover=None, chapters=True, fmt="m4b", quality=None,
            sample_rate=None, mono=False, overwrite=False, progress=None,
            should_cancel=None, notice=None, bitrate=None):
    """
    Join tracks (in order) into one chaptered audiobook at `output`.

    fmt is a key of FORMATS. quality is a bitrate like "64k", or "copy" to keep the
    original MP3 audio (mp3 and mka only). progress(fraction 0..1, seconds_done) is
    called during encoding; should_cancel() is polled; notice(message) reports things
    the user should know, such as falling back to re-encoding.
    Raises Cancelled / ConversionError. Returns the output path.
    """
    if fmt not in FORMATS:
        raise ConversionError(f"unknown format {fmt!r}")
    F = FORMATS[fmt]
    quality = quality or bitrate or F.default_quality
    if missing := missing_tools():
        raise ConversionError(f"{' and '.join(missing)} not found. Install ffmpeg first.")
    if not tracks:
        raise ConversionError("no MP3 files to convert")
    if quality == "copy" and not F.copy_fallback:
        raise ConversionError(f"{F.label} can't keep the original audio; choose a bitrate")
    if quality != "copy" and not re.fullmatch(r"\d+k", quality):
        raise ConversionError(f"bitrate should look like 64k, not {quality!r}")
    output = with_extension(output, fmt)
    if os.path.realpath(output) in {os.path.realpath(t.path) for t in tracks}:
        raise ConversionError(f"{os.path.basename(output)} is one of the files being joined. "
                              "Save the audiobook under a different name.")
    if os.path.exists(output) and not overwrite:
        raise ConversionError(f"{output} already exists")
    if cover and not os.path.isfile(cover):
        raise ConversionError(f"cover image not found: {cover}")
    out_dir = os.path.dirname(os.path.abspath(output))
    if not os.path.isdir(out_dir):
        raise ConversionError(f"folder does not exist: {out_dir}")

    copy = quality == "copy"
    if copy and mono:
        copy, quality = False, F.copy_fallback
        if notice:
            notice(f"Mono needs re-encoding, so the audio was re-encoded at {quality}bps.")
    elif copy and (why := copy_problem(tracks)):
        copy, quality = False, F.copy_fallback
        if notice:
            notice(f"The audio was re-encoded at {quality}bps because {why}.")
    if not copy and F.encoder not in _encoders():
        raise ConversionError(f"this ffmpeg can't encode {F.label} (missing {F.encoder}). "
                              "Install a full ffmpeg build.")

    total = sum(t.duration for t in tracks)
    workdir = tempfile.mkdtemp(prefix="mp3tom4b_")
    proc = None
    try:
        jpeg = None
        if cover:
            jpeg, cw, ch = _prepare_cover(cover, workdir, 800 if fmt == "opus" else 1400)

        listfile = os.path.join(workdir, "files.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for t in tracks:
                p = os.path.abspath(t.path).replace("'", "'\\''")
                fh.write(f"file '{p}'\n")

        metafile = os.path.join(workdir, "meta.txt")
        with open(metafile, "w", encoding="utf-8") as fh:
            fh.write(";FFMETADATA1\n")
            meta = {"title": title, "album": title, "artist": author, "album_artist": author,
                    "genre": genre, "date": year, "composer": narrator,
                    "comment": description, "description": description}
            if fmt == "m4b":
                meta["media_type"] = "2"           # iTunes "Audiobook"
            if fmt == "opus" and jpeg:
                meta["METADATA_BLOCK_PICTURE"] = _opus_picture_tag(jpeg, cw, ch)
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
        picture_stream = jpeg and fmt in ("m4b", "mp3")
        if picture_stream:
            cmd += ["-i", jpeg]
        cmd += ["-map", "0:a:0"]
        if picture_stream:
            cmd += ["-map", "2:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        cmd += ["-map_metadata", "1", "-map_chapters", "1" if chapters else "-1"]

        if copy:
            cmd += ["-c:a", "copy"]
        else:
            if fmt == "opus" or (fmt == "mka" and F.encoder == "libopus"):
                rate = 48000                        # Opus always runs at 48 kHz
            else:
                rate = sample_rate or min(max(t.sample_rate for t in tracks), 48000)
            cmd += ["-c:a", F.encoder, "-b:a", quality, "-ar", str(rate),
                    "-ac", "1" if mono else str(min(max(t.channels for t in tracks), 2))]
            if F.encoder == "libopus":
                cmd += ["-application", "voip" if int(quality[:-1]) <= 24 else "audio"]

        if fmt == "m4b":
            cmd += ["-movflags", "+faststart", "-f", "ipod"]
            if output.lower().endswith(".m4b"):
                cmd += ["-brand", "M4B "]
        elif fmt == "mp3":
            cmd += ["-id3v2_version", "3", "-write_id3v1", "1", "-f", "mp3"]
        elif fmt == "opus":
            cmd += ["-f", "opus"]
        elif fmt == "mka":
            if jpeg:
                cmd += ["-attach", jpeg, "-metadata:s:t:0", "mimetype=image/jpeg",
                        "-metadata:s:t:0", "filename=cover.jpg"]
            cmd += ["-f", "matroska"]
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
        errfile.close()
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
        description="Merge audiobook MP3 files into one chaptered audiobook file "
                    "(M4B, MP3, Opus or MKA).",
        epilog="Files inside a folder are ordered naturally (2 before 10). "
               "Files named on the command line keep the order you give. "
               "The format is taken from --format, or from the -o file extension. "
               "Run mp3tom4b-gui for the graphical version.")
    p.add_argument("inputs", nargs="+", help="MP3 files and/or folders containing MP3s")
    p.add_argument("-o", "--output", help="output file (default: <title>.<format>)")
    p.add_argument("-F", "--format", choices=list(FORMATS),
                   help="m4b (Apple Books, default), mp3 (one chaptered MP3), "
                        "opus (smallest) or mka (archival)")
    p.add_argument("-t", "--title", help="book title (default: album tag or folder name)")
    p.add_argument("-a", "--author", help="author (default: artist tag)")
    p.add_argument("-n", "--narrator", help="narrator")
    p.add_argument("-y", "--year", help="publication year")
    p.add_argument("-g", "--genre", default="Audiobook", help="genre (default: Audiobook)")
    p.add_argument("-d", "--description", default="", help="description / summary")
    p.add_argument("-c", "--cover", help="cover image (default: cover.jpg/folder.jpg or embedded art)")
    p.add_argument("--no-cover", action="store_true", help="don't embed cover art")
    p.add_argument("-b", "--bitrate", metavar="RATE",
                   help="bitrate like 64k, or 'copy' to keep the original audio (mp3/mka). "
                        "Defaults: m4b 64k, mp3 copy, opus 32k, mka copy")
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

        # format: --format wins, then the -o extension, then M4B
        implied = format_for_path(args.output) if args.output else None
        fmt = args.format or implied or "m4b"
        if args.format and implied and implied != args.format:
            die(f"-o {args.output} looks like {FORMATS[implied].label}, "
                f"but --format is {args.format}")
        F = FORMATS[fmt]
        quality = (args.bitrate or F.default_quality).lower()
        if quality == "copy" and not F.copy_fallback:
            die(f"{F.label} can't keep the original audio; give a bitrate like {F.default_quality}")
        if quality != "copy" and not re.fullmatch(r"\d+k", quality):
            die(f"bitrate should look like 64k (or 'copy'), not {args.bitrate!r}")

        scanned = set()
        files = expand_paths(args.inputs, args.recursive, found_in_folder=scanned,
                             warn=lambda m: print(f"warning: {m}", file=sys.stderr))
        if not files:
            die("no MP3 files to convert")
        if args.reverse:
            files.reverse()

        print(f"Reading {len(files)} file(s)...")
        tracks = []
        for f in files:
            t = probe_track(f)
            if looks_combined(t) and os.path.realpath(f) in scanned:
                print(f"warning: {combined_note(t)}", file=sys.stderr)
                continue
            tracks.append(t)
        if not tracks:
            die("no MP3 files to convert")
        files = [t.path for t in tracks]
        for i, t in enumerate(tracks, 1):
            t.chapter = chapter_title(t, i, args.chapter_names)

        guess = guess_metadata(tracks)
        title = args.title or guess["title"]
        author = args.author or guess["author"]
        year = args.year or guess["year"]
        output = with_extension(args.output, fmt) if args.output else default_output(title, fmt=fmt)
        if os.path.exists(output) and not args.force and not args.dry_run:
            die(f"{output} already exists (use -f to overwrite)")

        if quality == "copy" and not args.mono and (why := copy_problem(tracks)):
            audio = f"re-encode at {F.copy_fallback}bps ({why})"
        elif quality == "copy" and not args.mono:
            audio = "original MP3 audio, no re-encode"
        else:
            q = F.copy_fallback if quality == "copy" else quality
            codec = {"aac": "AAC", "libmp3lame": "MP3", "libopus": "Opus"}[F.encoder]
            audio = f"{codec} {q}bps{', mono' if args.mono else ''}"

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
            print(f"Format:   {F.label}, {audio}")
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
                          chapters=not args.no_chapters, fmt=fmt, quality=quality,
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
