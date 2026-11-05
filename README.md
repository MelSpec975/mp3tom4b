# mp3tom4b

Merge a folder of audiobook MP3 files into a single chaptered audiobook file, with cover art and metadata. Choose **M4B** for Apple Books, or one of three other formats for everything else.

It comes in two forms that share the same engine:

- **MP3 to M4B**, a desktop app for Linux
- **`mp3tom4b`**, a command-line tool

![MP3 to M4B desktop app](docs/screenshot.png)

## Output formats

| Format | Best for | Notes |
|---|---|---|
| **M4B** (default) | Apple Books on iPhone, iPad and Mac, and most audiobook apps | AAC audio, 64 kbps by default |
| **MP3 with chapters** | Playing anywhere | Joins the original MP3 audio with no re-encoding, so it takes seconds and loses no quality. Chapters work in podcast and Android audiobook apps, but not in Apple Books. |
| **Opus** | The smallest files | About half the size of M4B at similar quality for speech. Plays in VLC, mpv and many Android apps. |
| **MKA** (Matroska) | Archiving | Keeps the original audio untouched. Best in desktop players like VLC and mpv. |

If you ask to keep the original audio but the MP3s don't match (different sample rates, or a mix of mono and stereo), they're re-encoded automatically, because joining mismatched MP3s as-is produces a broken file. The app tells you when this happens.

## Features

- One chapter per MP3, named from the MP3's title tag, the file name, or "Chapter 1, 2, 3"
- Rename chapters and reorder files before converting
- Natural file ordering (`Part 2` comes before `Part 10`), including multi-disc folders
- Cover art from `cover.jpg`/`folder.jpg`, art embedded in the MP3s, or any image you choose
- Title, author, narrator, year, genre and description, filled in from the MP3 tags when possible
- Progress with time remaining, and a Cancel button that leaves no half-written file
- Mono option for smaller files
- Skips an already-combined audiobook sitting in the same folder, and refuses to overwrite one of its own input files

## Requirements

- Python 3.8+
- ffmpeg, built with libmp3lame and libopus (standard builds include both)
- PySide6 (desktop app only)

```bash
sudo pacman -S ffmpeg python pyside6                              # Arch
sudo apt install ffmpeg python3 python3-pyside6.qtwidgets         # Debian / Ubuntu
sudo dnf install ffmpeg python3 python3-pyside6                   # Fedora (ffmpeg via RPM Fusion)
```

## Install

```bash
git clone https://github.com/MelSpec975/mp3tom4b.git
cd mp3tom4b
sudo ./install.sh
```

This installs both the `mp3tom4b` command and **MP3 to M4B** in your app menu. To remove them:

```bash
sudo ./install.sh --uninstall
```

You can also run either one straight from the folder without installing: `./mp3tom4b_gui.py` or `./mp3tom4b.py`.

## Using the desktop app

1. Drag a folder of MP3s (or individual files) into the window, or use **Add Folder**.
2. Check the order. Drag rows to reorder, or double-click a chapter title to rename it.
3. Check the title, author and cover. Most of it is filled in from the MP3 tags.
4. Pick a **Format** and **Quality**. The app remembers your choice for each format.
5. Press **Convert**. When it's done, **Show in Folder** takes you to the file.

## Using the command line

```bash
# Convert every MP3 in a folder to M4B (title/author read from the tags)
mp3tom4b ~/Audiobooks/Dune/

# Set metadata yourself
mp3tom4b *.mp3 -t "Dune" -a "Frank Herbert" -n "Scott Brick" -o Dune.m4b

# Other formats: pick one with -F, or just name the output file
mp3tom4b ~/Audiobooks/Dune/ -F opus            # smallest files
mp3tom4b ~/Audiobooks/Dune/ -o Dune.mp3        # one chaptered MP3, no re-encoding
mp3tom4b ~/Audiobooks/Dune/ -F mka             # archival copy

# Multi-disc folders, mono output, explicit cover
mp3tom4b "Disc 1" "Disc 2" -r --mono --cover cover.jpg

# Preview the chapter list without encoding
mp3tom4b ~/Audiobooks/Dune/ --dry-run
```

| Option | What it does |
|---|---|
| `-o FILE` | Output file (default: `<title>.<format>`). Its extension picks the format. |
| `-F m4b\|mp3\|opus\|mka` | Output format (default: m4b) |
| `-b RATE` | Bitrate like `64k`, or `copy` to keep the original audio (mp3 and mka). Defaults: m4b 64k, mp3 copy, opus 32k, mka copy |
| `-t`, `-a`, `-n` | Title, author, narrator |
| `-c FILE` / `--no-cover` | Choose cover art, or skip it |
| `--mono` | Downmix to mono, roughly halving the size |
| `--chapter-names tag\|filename\|number` | How chapters are named |
| `-r` | Search subfolders |
| `--dry-run` | Show what would happen without encoding |
| `-f` | Overwrite an existing output file |

Run `mp3tom4b --help` for everything.

## Getting the book onto your devices

- **Mac:** open the `.m4b` with Books, or drag it onto the Books app.
- **iPhone/iPad:** AirDrop the `.m4b` from a Mac, or save it to Files and open it with Books.
- **Android:** copy the file to your phone and open it in an audiobook app. MP3 and Opus are the safest choices there.

## How it's built

`mp3tom4b.py` holds the conversion engine and the command-line interface. `mp3tom4b_gui.py` is a PySide6 (Qt) window that imports the same engine and runs conversions on a background thread. ffmpeg does the actual audio work.

## License

MIT. See [LICENSE](LICENSE).
