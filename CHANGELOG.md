# Changelog

## 2.1.0

- New output formats: **MP3 with chapters** (joins the original audio with no re-encoding), **Opus** (smallest files) and **MKA** (archival).
- Desktop app: Format dropdown with a short description of each format, quality options that fit the chosen format, and a remembered quality for each format.
- Command line: `-F/--format`, the format is inferred from the `-o` extension, and `-b copy` keeps the original audio.
- MP3s that don't match (different sample rates, mono and stereo mixed) are re-encoded automatically instead of producing a broken file, with a note saying why.
- An already-combined audiobook found in a scanned folder is skipped instead of being added as a chapter.
- Refuses to overwrite one of its own input files.

## 2.0.0

- New desktop app (PySide6): drag-and-drop, reordering, chapter renaming, cover preview, progress with time remaining, Cancel.
- `install.sh` installs the command and an app-menu entry with an icon.
- The conversion engine is shared by the app and the command line.

## 1.0.0

- First release: a command-line tool that merges MP3s into one chaptered M4B for Apple Books.
