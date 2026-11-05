# mp3tom4b

Merge a folder of audiobook MP3 files into a single **M4B** file that Apple Books (iPhone, iPad, Mac) recognizes as an audiobook, with chapters, cover art and metadata.

It comes in two forms that share the same engine:

- **MP3 to M4B**, a desktop app for Linux
- **`mp3tom4b`**, a command-line tool

![MP3 to M4B desktop app](docs/screenshot.png)

## Features

- One chapter per MP3, named from the MP3's title tag, the file name, or "Chapter 1, 2, 3"
- Rename chapters and reorder files before converting
- Natural file ordering (`Part 2` comes before `Part 10`), including multi-disc folders
- Cover art from `cover.jpg`/`folder.jpg`, art embedded in the MP3s, or any image you choose
- Title, author, narrator, year, genre and description, filled in from the MP3 tags when possible
- Progress with time remaining, and a Cancel button that leaves no half-written file
- Mono option for smaller files

## Requirements

- Python 3.8+
- ffmpeg
- PySide6 (desktop app only)

```bash
sudo pacman -S ffmpeg python pyside6                              # Arch
sudo apt install ffmpeg python3 python3-pyside6.qtwidgets         # Debian / Ubuntu
sudo dnf install ffmpeg python3 python3-pyside6                   # Fedora (ffmpeg via RPM Fusion)
```

## Install

```bash
git clone https://github.com/YOUR-USERNAME/mp3tom4b.git
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
4. Press **Convert**. When it's done, **Show in Folder** takes you to the file.

## Using the command line

```bash
# Convert every MP3 in a folder (title/author read from the tags)
mp3tom4b ~/Audiobooks/Dune/

# Set metadata yourself
mp3tom4b *.mp3 -t "Dune" -a "Frank Herbert" -n "Scott Brick" -o Dune.m4b

# Multi-disc folders, mono output, explicit cover
mp3tom4b "Disc 1" "Disc 2" -r --mono --cover cover.jpg

# Preview the chapter list without encoding
mp3tom4b ~/Audiobooks/Dune/ --dry-run
```

| Option | What it does |
|---|---|
| `-o FILE` | Output file (default: `<title>.m4b`) |
| `-t`, `-a`, `-n` | Title, author, narrator |
| `-c FILE` / `--no-cover` | Choose cover art, or skip it |
| `-b 64k` | AAC bitrate (64k is fine for speech) |
| `--mono` | Downmix to mono, roughly halving the size |
| `--chapter-names tag\|filename\|number` | How chapters are named |
| `-r` | Search subfolders |
| `--dry-run` | Show what would happen without encoding |
| `-f` | Overwrite an existing output file |

Run `mp3tom4b --help` for everything.

## Getting the book onto an Apple device

- **Mac:** open the `.m4b` with Books, or drag it onto the Books app.
- **iPhone/iPad:** AirDrop it from a Mac, or save it to Files and open it with Books.

## How it's built

`mp3tom4b.py` holds the conversion engine and the command-line interface. `mp3tom4b_gui.py` is a PySide6 (Qt) window that imports the same engine and runs conversions on a background thread. ffmpeg does the actual audio work.

## License

MIT. See [LICENSE](LICENSE).
