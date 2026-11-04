# mp3tom4b

A small Linux command-line tool that merges a folder of audiobook MP3 files into a single **M4B** file that Apple Books (iPhone, iPad, Mac) recognizes as an audiobook. It keeps chapters, cover art and metadata.

## Features

- One chapter per MP3, titled from the ID3 tag or the filename
- Natural file ordering (`Part 2` comes before `Part 10`)
- Cover art from `--cover`, a `cover.jpg`/`folder.jpg` in the folder, or art embedded in the MP3
- Title, author, narrator, year, genre and description metadata
- Progress bar, dry-run preview, mono option for smaller files
- A single Python file with no Python dependencies. It only needs `ffmpeg`.

## Requirements

- Python 3.8+
- ffmpeg (includes ffprobe)

```bash
sudo apt install ffmpeg
```

## Install

```bash
git clone https://github.com/MelSpec975/mp3tom4b.git
cd mp3tom4b
sudo install -m 755 mp3tom4b.py /usr/local/bin/mp3tom4b
```

## Usage

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

Run `mp3tom4b --help` for all options.

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

## Getting the book onto an Apple device

- **Mac:** open the `.m4b` with Books, or drag it onto the Books app.
- **iPhone/iPad:** AirDrop it from a Mac, or save it to Files and open it with Books.

## License

MIT. See [LICENSE](LICENSE).
