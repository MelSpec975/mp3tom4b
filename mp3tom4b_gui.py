#!/usr/bin/env python3
"""
mp3tom4b-gui - graphical front end for mp3tom4b.

Drag MP3 files or folders into the window, check the details, press Convert.
Requires PySide6 (Arch: sudo pacman -S pyside6) and ffmpeg.
"""

import os
import shutil
import sys
import tempfile
import time

# Find mp3tom4b.py next to this file, even when launched through a symlink.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import mp3tom4b as engine  # noqa: E402

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl, QSize  # noqa: E402
from PySide6.QtGui import (QPixmap, QDesktopServices, QKeySequence, QShortcut,  # noqa: E402
                           QPainter, QIcon, QAction)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMainWindow, QWidget, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QPushButton, QLabel, QLineEdit, QPlainTextEdit, QComboBox, QCheckBox, QProgressBar,
    QFileDialog, QMessageBox, QHBoxLayout, QVBoxLayout, QFormLayout, QGroupBox, QSplitter,
    QHeaderView, QStyle)

APP_NAME = "MP3 to M4B"
CHAPTER_MODES = [("tag", "From MP3 titles"), ("filename", "From file names"),
                 ("number", "Chapter 1, 2, 3…")]
BITRATES = [("32k", "32 kbps (smallest)"), ("48k", "48 kbps"),
            ("64k", "64 kbps (good for speech)"), ("96k", "96 kbps"),
            ("128k", "128 kbps (music-heavy books)")]
COL_NUM, COL_CHAPTER, COL_FILE, COL_LEN = range(4)


# ================================================================ background work

class Job(QThread):
    """Runs fn(job) on a worker thread so the window never freezes."""
    progressed = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, bool)          # message, was_cancelled

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel_requested = False

    def run(self):
        try:
            self.succeeded.emit(self.fn(self))
        except engine.Cancelled:
            self.failed.emit("Cancelled", True)
        except engine.ConversionError as e:
            self.failed.emit(str(e), False)
        except Exception as e:  # never let a worker crash the app silently
            self.failed.emit(f"Unexpected error: {e}", False)


# ================================================================ widgets

class TrackList(QTreeWidget):
    """Track table: drag to reorder, drop files/folders in from the file manager."""
    pathsDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["#", "Chapter title", "File", "Length"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.itemDoubleClicked.connect(lambda item, col: self.editItem(item, COL_CHAPTER))
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(COL_NUM, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(COL_CHAPTER, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_FILE, QHeaderView.Interactive)
        h.setSectionResizeMode(COL_LEN, QHeaderView.ResizeToContents)
        self.setColumnWidth(COL_FILE, 220)

    # external drops (files from the file manager) vs. internal reordering
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            e.acceptProposedAction()
            if paths:
                self.pathsDropped.emit(paths)
        else:
            super().dropEvent(e)
            self.renumber()

    def renumber(self):
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setText(COL_NUM, str(i + 1))

    def tracks(self):
        out = []
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            t = item.data(0, Qt.UserRole)
            t.chapter = item.text(COL_CHAPTER).strip()
            out.append(t)
        return out

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.topLevelItemCount() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            f = p.font()
            f.setPointSizeF(f.pointSizeF() * 1.2)
            p.setFont(f)
            p.drawText(self.viewport().rect(), Qt.AlignCenter,
                       "Drop MP3 files or folders here\n\nor use Add Files / Add Folder below")


class CoverLabel(QLabel):
    """Shows the cover; accepts a dropped image file."""
    imageDropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(200, 200))
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setStyleSheet("QLabel { border: 1px solid palette(mid); border-radius: 6px; }")
        self.clear_cover()

    def clear_cover(self):
        self.setPixmap(QPixmap())
        self.setText("No cover\n\nDrop an image here")

    def show_cover(self, path):
        pm = QPixmap(path)
        if pm.isNull():
            self.clear_cover()
            return False
        self.setPixmap(pm.scaled(self.size() - QSize(8, 8), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation))
        return True

    def dragEnterEvent(self, e):
        if not self.window().btn_cover.isEnabled():   # busy converting
            return
        urls = e.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(engine.IMAGE_EXTS):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.imageDropped.emit(e.mimeData().urls()[0].toLocalFile())


# ================================================================ main window

class MainWindow(QMainWindow):
    def __init__(self, initial_paths=()):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon.fromTheme("mp3tom4b", QIcon.fromTheme("audio-x-generic")))
        self.settings = QSettings("mp3tom4b", "mp3tom4b")
        self.tmpdir = tempfile.mkdtemp(prefix="mp3tom4b_gui_")
        self.cover_path = None
        self.cover_user_removed = False
        self.output_custom = False
        self.job = None
        self.started_at = 0.0
        self._build_ui()
        self._restore_settings()
        self._update_state()

        if missing := engine.missing_tools():
            QMessageBox.critical(
                self, "ffmpeg is missing",
                f"{' and '.join(missing)} could not be found.\n\nInstall ffmpeg, then reopen "
                "this app:\n\nArch:  sudo pacman -S ffmpeg\nDebian/Ubuntu:  sudo apt install ffmpeg")
        if initial_paths:
            self.add_paths(list(initial_paths))

    # ---------------------------------------------------------------- layout
    def _build_ui(self):
        # left: track list and its buttons
        self.list = TrackList()
        self.list.pathsDropped.connect(self.add_paths)
        self.list.model().rowsInserted.connect(lambda *_: self._update_state())
        self.list.model().rowsRemoved.connect(lambda *_: self._update_state())
        self.list.itemSelectionChanged.connect(self._update_state)

        self.btn_add_files = QPushButton("Add Files…")
        self.btn_add_folder = QPushButton("Add Folder…")
        self.btn_remove = QPushButton("Remove")
        self.btn_up = QPushButton("Move Up")
        self.btn_down = QPushButton("Move Down")
        self.btn_sort = QPushButton("Sort by Name")
        self.btn_clear = QPushButton("Clear")
        self.btn_add_files.clicked.connect(self.choose_files)
        self.btn_add_folder.clicked.connect(self.choose_folder)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_sort.clicked.connect(self.sort_by_name)
        self.btn_clear.clicked.connect(self.clear_all)

        btns = QHBoxLayout()
        for b in (self.btn_add_files, self.btn_add_folder):
            btns.addWidget(b)
        btns.addStretch()
        for b in (self.btn_up, self.btn_down, self.btn_sort, self.btn_remove, self.btn_clear):
            btns.addWidget(b)

        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(placeholder-text);")
        hint = QLabel("Drag rows to reorder · double-click a chapter title to rename it")
        hint.setStyleSheet("color: palette(placeholder-text);")
        foot = QHBoxLayout()
        foot.addWidget(self.summary)
        foot.addStretch()
        foot.addWidget(hint)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.list, 1)
        lv.addLayout(foot)
        lv.addLayout(btns)

        # right: cover, details, options
        self.cover = CoverLabel()
        self.cover.imageDropped.connect(self.set_cover)
        self.btn_cover = QPushButton("Choose…")
        self.btn_cover_remove = QPushButton("Remove")
        self.btn_cover.clicked.connect(self.choose_cover)
        self.btn_cover_remove.clicked.connect(self.remove_cover)
        cb = QHBoxLayout()
        cb.addWidget(self.btn_cover)
        cb.addWidget(self.btn_cover_remove)
        cover_box = QGroupBox("Cover")
        cvl = QVBoxLayout(cover_box)
        cvl.addWidget(self.cover, 0, Qt.AlignHCenter)
        cvl.addLayout(cb)

        self.f_title = QLineEdit()
        self.f_author = QLineEdit()
        self.f_narrator = QLineEdit()
        self.f_year = QLineEdit()
        self.f_year.setMaxLength(4)
        self.f_year.setFixedWidth(70)
        self.f_genre = QLineEdit("Audiobook")
        self.f_desc = QPlainTextEdit()
        self.f_desc.setFixedHeight(70)
        self.f_desc.setPlaceholderText("Optional summary")
        self.f_title.textEdited.connect(self._title_changed)
        details = QGroupBox("Details")
        fl = QFormLayout(details)
        fl.addRow("Title", self.f_title)
        fl.addRow("Author", self.f_author)
        fl.addRow("Narrator", self.f_narrator)
        fl.addRow("Year", self.f_year)
        fl.addRow("Genre", self.f_genre)
        fl.addRow("Description", self.f_desc)

        self.o_bitrate = QComboBox()
        for val, label in BITRATES:
            self.o_bitrate.addItem(label, val)
        self.o_mono = QCheckBox("Mono (smaller file, fine for speech)")
        self.o_chapters = QCheckBox("Create a chapter for each file")
        self.o_chapters.setChecked(True)
        self.o_names = QComboBox()
        for val, label in CHAPTER_MODES:
            self.o_names.addItem(label, val)
        self.o_names.activated.connect(self.retitle_chapters)
        self.o_chapters.toggled.connect(self.o_names.setEnabled)
        opts = QGroupBox("Options")
        ol = QFormLayout(opts)
        ol.addRow("Quality", self.o_bitrate)
        ol.addRow("", self.o_mono)
        ol.addRow("", self.o_chapters)
        ol.addRow("Chapter names", self.o_names)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(cover_box)
        rv.addWidget(details)
        rv.addWidget(opts)
        rv.addStretch()
        right.setMinimumWidth(340)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        self.split = split

        # bottom: output + convert
        self.f_output = QLineEdit()
        self.f_output.setPlaceholderText("Where to save the .m4b file")
        self.f_output.textEdited.connect(lambda _: setattr(self, "output_custom", True))
        self.f_output.textChanged.connect(lambda _: self._update_state())
        self.btn_output = QPushButton("Browse…")
        self.btn_output.clicked.connect(self.choose_output)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.status = QLabel("Add some MP3 files to get started.")
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setDefault(True)
        self.btn_convert.setMinimumWidth(130)
        self.btn_convert.setMinimumHeight(34)
        self.btn_convert.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_convert.clicked.connect(self.convert_or_cancel)

        outrow = QHBoxLayout()
        outrow.addWidget(QLabel("Save as"))
        outrow.addWidget(self.f_output, 1)
        outrow.addWidget(self.btn_output)
        statrow = QHBoxLayout()
        sv = QVBoxLayout()
        sv.addWidget(self.status)
        sv.addWidget(self.progress)
        statrow.addLayout(sv, 1)
        statrow.addSpacing(12)
        statrow.addWidget(self.btn_convert)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(12, 12, 12, 12)
        cv.addWidget(split, 1)
        cv.addLayout(outrow)
        cv.addLayout(statrow)
        self.setCentralWidget(central)

        # editing widgets get disabled while a conversion runs
        self.lockable = [self.list, self.btn_add_files, self.btn_add_folder, self.btn_remove,
                         self.btn_up, self.btn_down, self.btn_sort, self.btn_clear,
                         self.btn_cover, self.btn_cover_remove, details, opts,
                         self.f_output, self.btn_output]

        QShortcut(QKeySequence.Delete, self.list, activated=self.remove_selected)
        add = QAction(self)
        add.setShortcut(QKeySequence.Open)
        add.triggered.connect(self.choose_files)
        self.addAction(add)

    # ---------------------------------------------------------------- settings
    def _restore_settings(self):
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1100, 720)
        i = self.o_bitrate.findData(self.settings.value("bitrate", "64k"))
        self.o_bitrate.setCurrentIndex(max(i, 0))
        self.o_mono.setChecked(self.settings.value("mono", False, type=bool))

    def _save_settings(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("bitrate", self.o_bitrate.currentData())
        self.settings.setValue("mono", self.o_mono.isChecked())

    def last_dir(self):
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        return d if os.path.isdir(d) else os.path.expanduser("~")

    # ---------------------------------------------------------------- adding files
    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add MP3 files", self.last_dir(),
                                                "MP3 audio (*.mp3 *.MP3);;All files (*)")
        if files:
            self.settings.setValue("last_dir", os.path.dirname(files[0]))
            self.add_paths(files)

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder of MP3s", self.last_dir())
        if d:
            self.settings.setValue("last_dir", os.path.dirname(d))
            self.add_paths([d])

    def add_paths(self, paths):
        if self.job and self.job.isRunning():
            return
        existing = {os.path.realpath(t.path) for t in self.list.tracks()}
        want_cover = self.cover_path is None and not self.cover_user_removed
        tmpdir = self.tmpdir

        def work(job):
            files = [f for f in engine.expand_paths(paths, recursive=True)
                     if engine.is_audio(f) and os.path.realpath(f) not in existing]
            tracks, errors = [], []
            for i, f in enumerate(files, 1):
                job.progressed.emit(i / max(len(files), 1), f"Reading {i} of {len(files)}…")
                try:
                    tracks.append(engine.probe_track(f))
                except engine.ConversionError as e:
                    errors.append(str(e))
            cover = None
            if want_cover and tracks:
                cover = engine.find_folder_cover([t.path for t in tracks]) or \
                    engine.extract_embedded_cover(
                        tracks[0].path, os.path.join(tmpdir, f"cover_{time.time_ns()}.jpg"))
            return tracks, errors, cover

        self._start_job(work, self._tracks_added)

    def _tracks_added(self, result):
        tracks, errors, cover = result
        start = self.list.topLevelItemCount()
        mode = self.o_names.currentData()
        for i, t in enumerate(tracks, start + 1):
            item = QTreeWidgetItem([str(i), engine.chapter_title(t, i, mode),
                                    os.path.basename(t.path), engine.fmt_time(t.duration)])
            item.setData(0, Qt.UserRole, t)
            item.setToolTip(COL_FILE, t.path)
            item.setFlags((item.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
                          & ~Qt.ItemIsDropEnabled)
            item.setTextAlignment(COL_LEN, Qt.AlignRight | Qt.AlignVCenter)
            self.list.addTopLevelItem(item)

        all_tracks = self.list.tracks()
        guess = engine.guess_metadata(all_tracks)
        for field, key in ((self.f_title, "title"), (self.f_author, "author"),
                           (self.f_year, "year")):
            if not field.text().strip() and guess[key]:
                field.setText(guess[key])
        if cover:
            self.set_cover(cover)
        self._refresh_output()

        if tracks:
            self.status.setText(f"Added {len(tracks)} file{'s' * (len(tracks) != 1)}.")
        elif not errors:
            self.status.setText("No new MP3 files found there.")
        if errors:
            shown = "\n".join(errors[:10]) + (f"\n…and {len(errors) - 10} more"
                                              if len(errors) > 10 else "")
            QMessageBox.warning(self, "Some files were skipped", shown)
        self._update_state()

    # ---------------------------------------------------------------- list editing
    def selected_rows(self):
        return sorted(self.list.indexOfTopLevelItem(i) for i in self.list.selectedItems())

    def remove_selected(self):
        for row in reversed(self.selected_rows()):
            self.list.takeTopLevelItem(row)
        self.list.renumber()
        self._update_state()

    def move_selected(self, step):
        rows = self.selected_rows()
        if not rows:
            return
        n = self.list.topLevelItemCount()
        if (step < 0 and rows[0] == 0) or (step > 0 and rows[-1] == n - 1):
            return
        items = [self.list.topLevelItem(r) for r in rows]
        # moving up: handle the top row first; moving down: the bottom row first
        order = rows if step < 0 else list(reversed(rows))
        for r in order:
            item = self.list.takeTopLevelItem(r)
            self.list.insertTopLevelItem(r + step, item)
        self.list.clearSelection()
        for it in items:
            it.setSelected(True)
        self.list.scrollToItem(items[0])
        self.list.renumber()

    def sort_by_name(self):
        items = [self.list.takeTopLevelItem(0) for _ in range(self.list.topLevelItemCount())]
        items.sort(key=lambda it: engine.natural_key(it.data(0, Qt.UserRole).path))
        self.list.addTopLevelItems(items)
        self.list.renumber()

    def clear_all(self):
        self.list.clear()
        for f in (self.f_title, self.f_author, self.f_narrator, self.f_year, self.f_output):
            f.clear()
        self.f_desc.clear()
        self.cover_path = None
        self.cover_user_removed = False
        self.output_custom = False
        self.cover.clear_cover()
        self.status.setText("Add some MP3 files to get started.")
        self._update_state()

    def retitle_chapters(self):
        mode = self.o_names.currentData()
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            item.setText(COL_CHAPTER, engine.chapter_title(item.data(0, Qt.UserRole), i + 1, mode))

    # ---------------------------------------------------------------- cover
    def choose_cover(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose cover image", self.last_dir(),
                                           "Images (*.jpg *.jpeg *.png)")
        if f:
            self.set_cover(f)

    def set_cover(self, path):
        if self.cover.show_cover(path):
            self.cover_path = path
            self.cover_user_removed = False
        else:
            QMessageBox.warning(self, "Not an image", f"Couldn't open {os.path.basename(path)}.")

    def remove_cover(self):
        self.cover_path = None
        self.cover_user_removed = True
        self.cover.clear_cover()

    # ---------------------------------------------------------------- output
    def _title_changed(self, _):
        self._refresh_output()

    def _refresh_output(self):
        if self.output_custom or self.list.topLevelItemCount() == 0:
            return
        folder = os.path.dirname(os.path.abspath(self.list.topLevelItem(0).data(0, Qt.UserRole).path))
        self.f_output.setText(engine.default_output(self.f_title.text(), folder))

    def choose_output(self):
        start = self.f_output.text() or engine.default_output(self.f_title.text(), self.last_dir())
        f, _ = QFileDialog.getSaveFileName(self, "Save audiobook as", start,
                                           "Audiobook (*.m4b)")
        if f:
            if not f.lower().endswith(".m4b"):
                f += ".m4b"
            self.f_output.setText(f)
            self.output_custom = True

    # ---------------------------------------------------------------- converting
    def convert_or_cancel(self):
        if self.job and self.job.isRunning():
            self.job.cancel_requested = True
            self.btn_convert.setEnabled(False)
            self.status.setText("Cancelling…")
            return

        tracks = self.list.tracks()
        title = self.f_title.text().strip()
        output = self.f_output.text().strip()
        if not title:
            QMessageBox.information(self, APP_NAME, "Give the book a title first.")
            self.f_title.setFocus()
            return
        if not output.lower().endswith((".m4b", ".m4a", ".mp4")):
            output += ".m4b"
            self.f_output.setText(output)
        overwrite = False
        if os.path.exists(output):
            r = QMessageBox.question(self, "Replace file?",
                                     f"{os.path.basename(output)} already exists. Replace it?")
            if r != QMessageBox.Yes:
                return
            overwrite = True
        if self.o_chapters.isChecked() and any(not t.chapter for t in tracks):
            for i, t in enumerate(tracks, 1):
                t.chapter = t.chapter or f"Chapter {i}"

        opts = dict(title=title, author=self.f_author.text().strip(),
                    narrator=self.f_narrator.text().strip(), year=self.f_year.text().strip(),
                    genre=self.f_genre.text().strip() or "Audiobook",
                    description=self.f_desc.toPlainText().strip(), cover=self.cover_path,
                    chapters=self.o_chapters.isChecked(), bitrate=self.o_bitrate.currentData(),
                    mono=self.o_mono.isChecked(), overwrite=overwrite)
        total = sum(t.duration for t in tracks)
        self._save_settings()

        def work(job):
            started = time.monotonic()

            def report(frac, done):
                elapsed = time.monotonic() - started
                msg = f"Encoding… {frac * 100:.0f}%  ({engine.fmt_time(done)} of {engine.fmt_time(total)})"
                if frac > 0.03 and elapsed > 3:
                    left = elapsed / frac - elapsed
                    msg += f" · about {self._human(left)} left"
                job.progressed.emit(frac, msg)

            out = engine.convert(tracks, output, progress=report,
                                 should_cancel=lambda: job.cancel_requested, **opts)
            return out, time.monotonic() - started

        self.status.setText("Starting…")
        self._start_job(work, self._converted, converting=True)

    @staticmethod
    def _human(sec):
        sec = int(sec)
        if sec < 60:
            return f"{sec} sec"
        if sec < 3600:
            return f"{round(sec / 60)} min"
        return f"{sec // 3600} hr {sec % 3600 // 60} min"

    def _converted(self, result):
        out, took = result
        size = os.path.getsize(out) / 1024 / 1024
        self.status.setText(f"Saved {os.path.basename(out)} ({size:.1f} MB) in {self._human(took)}.")
        self.progress.setValue(1000)
        box = QMessageBox(self)
        box.setWindowTitle("Done")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Your audiobook is ready.\n\n{out}\n{size:.1f} MB")
        show = box.addButton("Show in Folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is show:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(out)))

    # ---------------------------------------------------------------- job plumbing
    def _start_job(self, fn, on_success, converting=False):
        self.job = Job(fn, self)
        self.job.converting = converting
        self.job.progressed.connect(self._on_progress)
        self.job.succeeded.connect(on_success)
        self.job.failed.connect(self._on_failed)
        self.job.finished.connect(self._job_finished)
        self.progress.setValue(0)
        self._lock(True, converting)
        self.job.start()

    def _on_progress(self, frac, msg):
        self.progress.setValue(int(frac * 1000))
        self.status.setText(msg)

    def _on_failed(self, msg, cancelled):
        self.progress.setValue(0)
        if cancelled:
            self.status.setText("Cancelled. Nothing was saved.")
        else:
            self.status.setText("Something went wrong.")
            QMessageBox.critical(self, "Couldn't finish", msg)

    def _job_finished(self):
        self._lock(False)
        self._update_state()

    def _lock(self, locked, converting=False):
        for w in self.lockable:
            w.setEnabled(not locked)
        if locked and converting:
            self.btn_convert.setText("Cancel")
            self.btn_convert.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
            self.btn_convert.setEnabled(True)
        else:
            self.btn_convert.setText("Convert")
            self.btn_convert.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            self.btn_convert.setEnabled(not locked)

    def _update_state(self):
        n = self.list.topLevelItemCount()
        busy = bool(self.job and self.job.isRunning())
        has_sel = bool(self.list.selectedItems())
        for b in (self.btn_remove, self.btn_up, self.btn_down):
            b.setEnabled(has_sel and not busy)
        for b in (self.btn_sort, self.btn_clear):
            b.setEnabled(n > 0 and not busy)
        if not busy:
            self.btn_convert.setEnabled(n > 0 and bool(self.f_output.text().strip())
                                        and not engine.missing_tools())
        if n:
            total = sum(self.list.topLevelItem(i).data(0, Qt.UserRole).duration for i in range(n))
            self.summary.setText(f"{n} file{'s' * (n != 1)} · {engine.fmt_time(total)} total")
        else:
            self.summary.setText("")
        self.list.viewport().update()

    # ---------------------------------------------------------------- closing
    def closeEvent(self, e):
        if self.job and self.job.isRunning():
            if getattr(self.job, "converting", False):
                r = QMessageBox.question(self, "Stop converting?",
                                         "A conversion is still running. Stop it and quit?")
                if r != QMessageBox.Yes:
                    e.ignore()
                    return
            self.job.cancel_requested = True
            self.job.wait(10000)
        self._save_settings()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        e.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("mp3tom4b")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("mp3tom4b")
    win = MainWindow(initial_paths=[p for p in sys.argv[1:] if os.path.exists(p)])
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
