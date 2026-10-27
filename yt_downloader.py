#!/usr/bin/env python3
"""
YT Downloader - giao diện kiểu IDM, dùng yt-dlp.

Cài đặt:
    pip install -U yt-dlp
    (khuyến nghị) cài thêm FFmpeg để ghép video+audio chất lượng cao và chuyển MP3:
        Windows: winget install ffmpeg      macOS: brew install ffmpeg
        Linux:   sudo apt install ffmpeg

Chạy:
    python yt_downloader.py
"""
import itertools
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, sanitize_filename
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Thiếu thư viện",
        "Chưa cài yt-dlp.\n\nMở terminal và chạy:\n    pip install -U yt-dlp",
    )
    sys.exit(1)

# Khi đóng gói --windowed, stdout/stderr là None -> tránh crash khi thư viện in ra
for _s in ("stdout", "stderr"):
    if getattr(sys, _s) is None:
        setattr(sys, _s, open(os.devnull, "w"))

# Tìm ffmpeg đi kèm (cạnh file .exe hoặc được đóng gói bên trong)
_dirs = [os.path.dirname(os.path.abspath(sys.argv[0])), getattr(sys, "_MEIPASS", "")]
os.environ["PATH"] = os.pathsep.join([d for d in _dirs if d] + [os.environ.get("PATH", "")])

CONFIG_PATH = Path.home() / ".yt_idm_downloader.json"
HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_DENO = shutil.which("deno") is not None  # JS runtime yt-dlp cần để giải thử thách của YouTube
try:
    import yt_dlp_ejs  # noqa: F401  (bộ giải thử thách đi kèm yt-dlp[default])
    HAS_EJS = True
except ImportError:
    HAS_EJS = False
URL_RE = re.compile(r"^https?://\S+$", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# (loại, tham số)
PRESETS = {
    "Video • Tốt nhất": ("video", None),
    "Video • 2160p (4K)": ("video", 2160),
    "Video • 1440p": ("video", 1440),
    "Video • 1080p": ("video", 1080),
    "Video • 720p": ("video", 720),
    "Video • 480p": ("video", 480),
    "Video • 360p": ("video", 360),
    "Audio • MP3 (320kbps)": ("audio", "mp3"),
    "Audio • M4A": ("audio", "m4a"),
    "Audio • Gốc (chất lượng cao nhất)": ("audio", "best"),
}

MAX_ATTEMPTS = 6    # số lần thử tối đa cho mỗi video (403, chậm, lỗi mạng...)
SLOW_GRACE = 6      # số giây đầu mỗi lần thử không tính vào việc đo tốc độ
SLOW_SECONDS = 10   # chậm liên tục quá số giây này -> tải lại bằng link mới

RETRY_KEYS = ("403", "forbidden", "429", "too many requests", "timed out", "timeout", "connection",
              "incompleteread", "reset by peer", "remote end closed", "http error 5",
              "unable to download", "ssl", "temporary failure", "network is unreachable", "getaddrinfo")
NO_RETRY_KEYS = ("private video", "video unavailable", "members-only", "age-restricted",
                 "confirm your age", "copyright", "has been removed", "this video is not available",
                 "premieres in", "live event will begin", "not a bot", "sign in to confirm")


# Khi YouTube yêu cầu "xác minh bot", thử lần lượt các client khác (không cần cookies).
# None = client mặc định của yt-dlp.
CLIENT_CHAIN = [None, "tv", "android_vr", "mweb", "web_safari", "web_embedded"]


IP_AUTO = "Tự động"
IP_V4 = "Chỉ IPv4"
IP_V6 = "Chỉ IPv6"
IP_MODES = [IP_AUTO, IP_V4, IP_V6]
IP_SOURCE = {IP_V4: "0.0.0.0", IP_V6: "::"}  # source_address của yt-dlp (giống -4 / -6)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # video công khai ổn định, dùng để kiểm tra mạng


def is_bot_error(msg):
    low = msg.lower()
    return "not a bot" in low or "sign in to confirm" in low


def env_summary():
    return (f"yt-dlp {yt_dlp.version.__version__} | Deno: {'có' if HAS_DENO else 'KHÔNG'} | "
            f"yt-dlp-ejs: {'có' if HAS_EJS else 'KHÔNG'}")


class RestartDownload(DownloadCancelled):
    """Ngắt lượt tải hiện tại để bắt đầu lại bằng link mới (tiếp tục từ file .part)."""


ACTIVE = {"Starting", "Downloading", "Merging", "Converting"}
STATUS_VI = {
    "Queued": "Đang chờ",
    "Starting": "Khởi động...",
    "Downloading": "Đang tải",
    "Merging": "Đang ghép video+audio",
    "Converting": "Đang chuyển đổi",
    "Paused": "Tạm dừng",
    "Completed": "Hoàn tất",
    "Error": "Lỗi",
    "Cancelled": "Đã hủy",
}


# ----------------------------------------------------------------- helpers
def fmt_bytes(n):
    if not n:
        return "--"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def fmt_speed(s):
    return fmt_bytes(s) + "/s" if s else "--"


def fmt_time(sec):
    if sec is None:
        return "--"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def text_bar(p, n=18):
    p = max(0.0, min(100.0, p))
    f = int(p / 100 * n)
    return "█" * f + "░" * (n - f) + f" {p:5.1f}%"


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:  # noqa
        messagebox.showerror("Lỗi", str(e))


def reveal_file(path):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        open_path(os.path.dirname(path))


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa
        return {}


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa
        pass


# -------------------------------------------------------------------- item
class Item:
    _ids = itertools.count(1)

    def __init__(self, index, url, title, preset, outdir, subdir="", prefix=""):
        self.id = next(Item._ids)
        self.index = index
        self.url = url
        self.title = title or url
        self.preset = preset
        self.outdir = outdir
        self.subdir = subdir
        self.prefix = prefix
        self.compat = False  # True = chỉ H.264 + AAC (không AV1/VP9)
        self.note = ""
        self.attempt = 1
        self.attempt_start = 0.0
        self.slow_since = None
        self.min_speed = 0

        self.status = "Queued"
        self.percent = 0.0
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self.eta = None
        self.stage = ""
        self.error = ""
        self.filepath = ""
        self.streams = {}  # filename -> [downloaded, total]
        self.n_streams = 1
        self.expected_total = 0
        self.elapsed = 0.0
        self.t_run = None
        self.stop = None  # None | "pause" | "remove"
        self.thread = None

    @property
    def folder(self):
        return os.path.join(self.outdir, self.subdir) if self.subdir else self.outdir

    def elapsed_now(self):
        return self.elapsed + (time.time() - self.t_run if self.t_run else 0)

    def eta_value(self):
        if self.speed and self.total and self.total > self.downloaded:
            return (self.total - self.downloaded) / self.speed
        return self.eta

    def thread_alive(self):
        return self.thread is not None and self.thread.is_alive()


# --------------------------------------------------------------------- app
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.items = []
        self.by_iid = {}
        self.row_cache = {}
        self.counter = 0
        self.ui_queue = queue.Queue()
        self.cooldown_until = 0.0  # tạm nghỉ khi YouTube chặn
        self.next_start = 0.0      # giãn cách giữa các lần bắt đầu tải
        self.bot_streak = 0        # số lần liên tiếp bị yêu cầu xác minh
        self.client_pref = 0       # client YouTube đang dùng được (chỉ số trong CLIENT_CHAIN)
        self.busy_msg = ""          # thông báo tạm trên thanh trạng thái (vd: đang kiểm tra mạng)
        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<FocusIn>", self.on_focus)
        root.after(250, self.tick)

    # ------------------------------------------------------------------ UI
    def build_ui(self):
        r = self.root
        r.title(f"YT Downloader  •  yt-dlp {yt_dlp.version.__version__}")
        r.geometry("1150x700")
        r.minsize(940, 580)

        style = ttk.Style()
        if sys.platform.startswith("win") and "vista" in style.theme_names():
            style.theme_use("vista")
        elif sys.platform.startswith("linux") and "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=24)
        style.configure("Big.Horizontal.TProgressbar", thickness=22)

        # --- hàng nhập link
        top = ttk.Frame(r, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Link:").pack(side="left")
        self.url_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.url_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda e: self.on_add())
        entry.focus_set()
        ttk.Button(top, text="Dán", command=self.paste).pack(side="left")
        self.btn_add = ttk.Button(top, text="＋ Thêm && Tải".replace("&&", "&"), command=self.on_add)
        self.btn_add.pack(side="left", padx=(6, 0))

        # --- tuỳ chọn
        opt = ttk.Frame(r, padding=(10, 2, 10, 4))
        opt.pack(fill="x")
        ttk.Label(opt, text="Định dạng:").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=self.cfg.get("preset", "Video • Tốt nhất"))
        ttk.Combobox(opt, textvariable=self.preset_var, values=list(PRESETS), state="readonly",
                     width=30).grid(row=0, column=1, padx=6)

        ttk.Label(opt, text="Lưu vào:").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.dir_var = tk.StringVar(value=self.cfg.get("outdir", str(Path.home() / "Downloads")))
        ttk.Entry(opt, textvariable=self.dir_var, width=42).grid(row=0, column=3, padx=6)
        ttk.Button(opt, text="Chọn...", command=self.choose_dir).grid(row=0, column=4)

        ttk.Label(opt, text="Tải đồng thời:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.conc_var = tk.StringVar(value=str(self.cfg.get("concurrent", 2)))
        ttk.Spinbox(opt, from_=1, to=8, textvariable=self.conc_var, width=5).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(opt, text="Luồng/file:").grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        self.conn_var = tk.StringVar(value=str(self.cfg.get("conns", 4)))
        ttk.Spinbox(opt, from_=1, to=16, textvariable=self.conn_var, width=5).grid(
            row=1, column=3, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(opt, text="Tải lại nếu chậm hơn (KB/s, 0 = tắt):").grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.minspeed_var = tk.StringVar(value=str(self.cfg.get("min_speed", 300)))
        ttk.Spinbox(opt, from_=0, to=5000, increment=50, textvariable=self.minspeed_var, width=7).grid(
            row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(opt, text="Nghỉ giữa video (giây):").grid(
            row=2, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        self.delay_var = tk.StringVar(value=str(self.cfg.get("delay", 3)))
        ttk.Spinbox(opt, from_=0, to=30, textvariable=self.delay_var, width=5).grid(
            row=2, column=3, sticky="w", padx=6, pady=(6, 0))

        self.sub_var = tk.BooleanVar(value=self.cfg.get("subfolder", True))
        ttk.Checkbutton(opt, text="Playlist → tạo thư mục riêng + đánh số thứ tự",
                        variable=self.sub_var).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.compat_var = tk.BooleanVar(value=self.cfg.get("compat", True))
        ttk.Checkbutton(opt, text="Tương thích TV / thiết bị cũ: chỉ H.264 + AAC, không AV1/VP9 "
                                  "(tối đa 1080p)",
                        variable=self.compat_var).grid(row=5, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(opt, text="Kết nối IP:").grid(row=6, column=0, sticky="w", pady=(4, 0))
        mode = self.cfg.get("ip_mode", IP_AUTO)
        self.ip_var = tk.StringVar(value=mode if mode in IP_MODES else IP_AUTO)
        ttk.Combobox(opt, textvariable=self.ip_var, values=IP_MODES, state="readonly", width=30).grid(
            row=6, column=1, padx=6, pady=(4, 0))
        ttk.Label(opt, text="Dùng nút 'Kiểm tra mạng' để biết nên chọn IPv4 hay IPv6",
                  foreground="gray").grid(row=6, column=2, columnspan=3, sticky="w", padx=(10, 0), pady=(4, 0))
        self.single_var = tk.BooleanVar(value=self.cfg.get("single", True))
        ttk.Checkbutton(opt, text="Link video có kèm list=... → chỉ tải 1 video đó",
                        variable=self.single_var).grid(row=4, column=0, columnspan=4, sticky="w")

        # --- thanh công cụ
        tb = ttk.Frame(r, padding=(10, 6, 10, 2))
        tb.pack(fill="x")
        for text, cmd in (
            ("☑ Chọn tất cả", self.select_all),
            ("☐ Bỏ chọn", self.select_none),
            ("⏸ Tạm dừng", self.pause_selected),
            ("▶ Tiếp tục / Thử lại", self.resume_selected),
            ("🗑 Xóa", self.remove_selected),
            ("✔ Dọn mục đã xong", self.clear_completed),
            ("📄 Mở file", self.open_file_selected),
            ("📁 Mở thư mục", self.open_folder_selected),
            ("🔍 Kiểm tra mạng", self.check_network),
        ):
            ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=(0, 6))

        # --- danh sách
        mid = ttk.Frame(r, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        cols = ("idx", "title", "status", "progress", "size", "done", "speed", "eta", "elapsed")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        heads = {
            "idx": ("#", 40, "center"), "title": ("Tên", 330, "w"),
            "status": ("Trạng thái", 260, "w"), "progress": ("Tiến trình", 180, "w"),
            "size": ("Dung lượng", 90, "e"), "done": ("Đã tải", 90, "e"),
            "speed": ("Tốc độ", 95, "e"), "eta": ("Còn lại", 70, "e"),
            "elapsed": ("Đã chạy", 70, "e"),
        }
        for c, (t, w, a) in heads.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "title"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("err", foreground="#cf222e")
        self.tree.tag_configure("pause", foreground="#808080")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_detail())
        self.tree.bind("<Double-1>", lambda e: self.open_file_selected(only_selected=True))
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.tree.bind("<Control-A>", lambda e: (self.select_all(), "break")[1])
        self.tree.bind("<Button-3>", self.popup_menu)
        self.tree.bind("<Button-2>", self.popup_menu)

        self.menu = tk.Menu(r, tearoff=0)
        self.menu.add_command(label="Tạm dừng", command=self.pause_selected)
        self.menu.add_command(label="Tiếp tục / Thử lại", command=self.resume_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Mở file", command=self.open_file_selected)
        self.menu.add_command(label="Mở thư mục", command=self.open_folder_selected)
        self.menu.add_command(label="Sao chép link", command=self.copy_link)
        self.menu.add_separator()
        self.menu.add_command(label="Xóa khỏi danh sách", command=self.remove_selected)

        # --- khung chi tiết (giống hộp thoại "Download status" của IDM)
        det = ttk.LabelFrame(r, text="Chi tiết tải xuống", padding=8)
        det.pack(fill="x", padx=10, pady=(4, 4))
        self.dv = {k: tk.StringVar(value="--") for k in
                   ("title", "status", "size", "done", "speed", "eta", "elapsed", "stage", "path", "msg")}
        ttk.Label(det, textvariable=self.dv["title"], font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w")
        self.big_bar = ttk.Progressbar(det, maximum=100, style="Big.Horizontal.TProgressbar")
        self.big_bar.grid(row=1, column=0, columnspan=5, sticky="we", pady=4)
        self.big_pct = ttk.Label(det, text="0.0%", font=("", 11, "bold"), width=8)
        self.big_pct.grid(row=1, column=5)
        pairs = [("Trạng thái", "status"), ("Dung lượng", "size"), ("Đã tải", "done"),
                 ("Tốc độ", "speed"), ("Còn lại", "eta"), ("Đã chạy", "elapsed")]
        for i, (lbl, key) in enumerate(pairs):
            ttk.Label(det, text=lbl + ":", foreground="gray").grid(
                row=2 + i // 3, column=(i % 3) * 2, sticky="w", pady=1)
            ttk.Label(det, textvariable=self.dv[key]).grid(
                row=2 + i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(4, 24))
        ttk.Label(det, text="Luồng:", foreground="gray").grid(row=4, column=0, sticky="w")
        ttk.Label(det, textvariable=self.dv["stage"]).grid(row=4, column=1, sticky="w", padx=4)
        ttk.Label(det, text="Lưu tại:", foreground="gray").grid(row=4, column=2, sticky="w")
        ttk.Label(det, textvariable=self.dv["path"]).grid(row=4, column=3, columnspan=3, sticky="w", padx=4)
        self.msg_lbl = ttk.Label(det, textvariable=self.dv["msg"], foreground="#cf222e", wraplength=1000)
        self.msg_lbl.grid(row=5, column=0, columnspan=6, sticky="w")
        det.columnconfigure(1, weight=1)
        det.columnconfigure(3, weight=1)
        det.columnconfigure(4, weight=1)

        # --- status bar
        bar = ttk.Frame(r, padding=(10, 2, 10, 8))
        bar.pack(fill="x")
        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        warns = []
        if not HAS_FFMPEG:
            warns.append("⚠ Không tìm thấy FFmpeg: video giới hạn ~720p, không có MP3")
        if not HAS_DENO:
            warns.append("⚠ Chưa có Deno (JS runtime của yt-dlp): YouTube dễ tải chậm / lỗi 403. "
                         "Cài: winget install DenoLand.Deno (hoặc đặt deno.exe cạnh file exe)")
        if not HAS_EJS:
            warns.append("⚠ Thiếu gói yt-dlp-ejs: chạy  pip install -U \"yt-dlp[default]\"  "
                         "(tạm thời app sẽ tải bộ giải từ GitHub)")
        if warns:
            wf = ttk.Frame(r, padding=(10, 0, 10, 6))
            wf.pack(fill="x")
            for w in warns:
                ttk.Label(wf, text=w, foreground="#bf8700").pack(anchor="w")

    # ------------------------------------------------------- basic actions
    def paste(self):
        try:
            self.url_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass

    def on_focus(self, _e=None):
        if self.url_var.get().strip():
            return
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if URL_RE.match(clip) and ("youtube.com" in clip or "youtu.be" in clip):
            self.url_var.set(clip)

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if d:
            self.dir_var.set(d)

    def min_speed_bytes(self):
        try:
            return max(0, int(float(self.minspeed_var.get()))) * 1024
        except ValueError:
            return 0

    def ip_source(self):
        return IP_SOURCE.get(self.ip_var.get())  # None = tự động

    def check_network(self):
        """Thử lấy thông tin 1 video công khai qua IPv4 và IPv6 riêng để biết đường nào bị YouTube chặn."""
        if self.busy_msg:
            return
        self.busy_msg = "Đang kiểm tra mạng (IPv4 / IPv6)... vui lòng đợi vài chục giây"
        threading.Thread(target=self._check_network, daemon=True).start()

    def _check_network(self):
        results = []
        for label, sa in (("IPv4", IP_SOURCE[IP_V4]), ("IPv6", IP_SOURCE[IP_V6])):
            outcome = ""
            for client in (None, "tv", "android_vr"):
                opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                        "socket_timeout": 10, "source_address": sa}
                if client:
                    opts["extractor_args"] = {"youtube": {"player_client": [client]}}
                if not HAS_EJS:
                    opts["remote_components"] = ["ejs:github"]
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.extract_info(TEST_VIDEO_URL, download=False)
                    outcome = f"OK (client: {client or 'mặc định'})"
                    break
                except Exception as e:  # noqa
                    msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").strip()
                    low = msg.lower()
                    if is_bot_error(msg):
                        outcome = "BỊ CHẶN (YouTube yêu cầu xác minh bot)"
                        time.sleep(1.0)
                        continue  # thử client khác
                    if any(k in low for k in ("unreachable", "no route", "getaddrinfo", "10051", "10049",
                                              "cannot assign", "address family", "errno 101")):
                        outcome = f"không dùng được (mạng của bạn không có kết nối {label})"
                    else:
                        outcome = "lỗi: " + (msg.splitlines()[0][:90] if msg else "không rõ")
                    break
            results.append((label, outcome))
        self.ui_queue.put(lambda r=results: self._network_result(r))

    def _network_result(self, results):
        self.busy_msg = ""
        lines = "\n".join(f"• {label}: {out}" for label, out in results)
        ok = [label for label, out in results if out.startswith("OK")]
        tail = "\n\n" + env_summary()
        if len(ok) == 2:
            messagebox.showinfo("Kết quả kiểm tra mạng",
                                lines + "\n\nCả IPv4 và IPv6 đều dùng được. Để 'Kết nối IP' = Tự động." + tail)
        elif len(ok) == 1:
            if messagebox.askyesno("Kết quả kiểm tra mạng",
                                   lines + f"\n\nChỉ {ok[0]} dùng được. Đặt 'Kết nối IP' = Chỉ {ok[0]} luôn?" + tail):
                self.ip_var.set(IP_V4 if ok[0] == "IPv4" else IP_V6)
        else:
            messagebox.showwarning(
                "Kết quả kiểm tra mạng",
                lines + "\n\nCả hai đường đều không dùng được ngay lúc này. Nếu là 'BỊ CHẶN' thì IP của bạn đang bị "
                "YouTube gắn cờ: hãy đợi, tắt VPN/proxy, đổi mạng (ví dụ phát 4G từ điện thoại) hoặc khởi động lại "
                "modem để đổi IP, rồi kiểm tra lại." + tail)

    def start_delay(self):
        try:
            return max(0.0, float(self.delay_var.get()))
        except ValueError:
            return 3.0

    def max_concurrent(self):
        try:
            return max(1, min(8, int(self.conc_var.get())))
        except ValueError:
            return 2

    def connections(self):
        try:
            return max(1, min(16, int(self.conn_var.get())))
        except ValueError:
            return 4

    # ------------------------------------------------------------ analysis
    def on_add(self):
        urls = [u for u in self.url_var.get().split() if u]
        if not urls:
            messagebox.showinfo("Chưa có link", "Hãy dán link video hoặc playlist YouTube.")
            return
        bad = [u for u in urls if not URL_RE.match(u)]
        if bad:
            messagebox.showerror("Link không hợp lệ", f"Không phải URL http(s):\n{bad[0]}")
            return
        outdir = self.dir_var.get().strip()
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Thư mục không hợp lệ", str(e))
            return
        preset = self.preset_var.get()
        single = self.single_var.get()
        direct = [u for u in urls if not self.needs_analysis(u, single)]
        for u in direct:  # video đơn lẻ: thêm thẳng, tên video sẽ cập nhật khi bắt đầu tải
            self.add_item(u, None, preset, outdir)
        to_analyze = [u for u in urls if u not in direct]
        if not to_analyze:
            self.url_var.set("")
            return
        self.btn_add.state(["disabled"])
        self.status_var.set("Đang phân tích link...")
        args = (to_analyze, preset, outdir, self.sub_var.get(), single, self.ip_source())
        threading.Thread(target=self._analyze, args=args, daemon=True).start()

    @staticmethod
    def needs_analysis(url, single_only):
        """Chỉ playlist/kênh (hoặc site khác YouTube) mới cần phân tích trước."""
        u = urlparse(url)
        host = u.netloc.lower()
        if "youtube.com" not in host and "youtu.be" not in host:
            return True
        path = u.path.lower()
        if any(path.startswith(x) for x in ("/playlist", "/channel/", "/c/", "/user/", "/@")):
            return True
        qs = parse_qs(u.query)
        if "list" in qs:
            return not (single_only and ("v" in qs or "youtu.be" in host))
        return False

    def _analyze(self, urls, preset, outdir, subfolder, single, source_address=None):
        opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "noplaylist": single, "skip_download": True, "socket_timeout": 30}
        if source_address:
            opts["source_address"] = source_address
        for url in urls:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                self.ui_queue.put(lambda i=info, u=url: self._analysis_done(u, i, preset, outdir, subfolder))
            except Exception as e:  # noqa
                self.ui_queue.put(lambda e=e, u=url: self._analysis_failed(u, e))
        self.ui_queue.put(self._analysis_finish)

    def _analysis_finish(self):
        self.btn_add.state(["!disabled"])
        self.url_var.set("")
        self.status_var.set("Sẵn sàng.")

    def _analysis_failed(self, url, err):
        msg = ANSI_RE.sub("", str(err)).replace("ERROR: ", "").strip()
        if is_bot_error(msg):
            msg = ("YouTube đang tạm chặn yêu cầu từ mạng của bạn (kiểm tra \"không phải bot\").\n"
                   "Thử: đợi vài phút, tắt VPN/proxy, đổi mạng (ví dụ phát 4G từ điện thoại) "
                   "hoặc khởi động lại modem để đổi IP.\n\n"
                   f"Chi tiết kỹ thuật: {env_summary()}")
        messagebox.showerror("Không phân tích được link", f"{url}\n\n{msg}")

    def _analysis_done(self, url, info, preset, outdir, subfolder):
        if info is None:
            return
        if info.get("_type") == "playlist" or "entries" in info:
            entries = [e for e in (info.get("entries") or []) if e and e.get("ie_key") != "YoutubeTab"]
            if not entries:
                messagebox.showwarning("Playlist trống", "Không tìm thấy video nào trong link này.")
                return
            pl_title = info.get("title") or "Playlist"
            if not messagebox.askyesno(
                    "Playlist", f"“{pl_title}”\nCó {len(entries)} video.\n\nThêm tất cả vào danh sách tải?"):
                return
            subdir = sanitize_filename(pl_title, restricted=False) if subfolder else ""
            width = len(str(len(entries)))
            for n, e in enumerate(entries, 1):
                vurl = e.get("url") or e.get("webpage_url") or ""
                if not vurl.startswith("http"):
                    vurl = f"https://www.youtube.com/watch?v={e.get('id') or vurl}"
                prefix = f"{n:0{width}d} - " if subfolder else ""
                self.add_item(vurl, e.get("title"), preset, outdir, subdir, prefix)
        else:
            self.add_item(info.get("webpage_url") or url, info.get("title"), preset, outdir)

    def add_item(self, url, title, preset, outdir, subdir="", prefix=""):
        self.counter += 1
        it = Item(self.counter, url, title, preset, outdir, subdir, prefix)
        it.compat = self.compat_var.get()
        self.items.append(it)
        iid = str(it.id)
        self.by_iid[iid] = it
        self.tree.insert("", "end", iid=iid, values=self.row_values(it))
        self.tree.see(iid)

    # -------------------------------------------------------------- worker
    def build_opts(self, it):
        kind, val = PRESETS[it.preset]
        folder = it.folder.replace("%", "%%")
        opts = {
            "outtmpl": os.path.join(folder, f"{it.prefix}%(title)s [%(id)s].%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "continuedl": True,
            "retries": 3,
            "fragment_retries": 5,
            "file_access_retries": 5,
            "socket_timeout": 10,
            "http_chunk_size": 10 * 1024 * 1024,
            "concurrent_fragment_downloads": self.connections(),
            "progress_hooks": [lambda d, it=it: self.progress_hook(it, d)],
            "postprocessor_hooks": [lambda d, it=it: self.pp_hook(it, d)],
        }
        if not HAS_EJS:  # dự phòng khi thiếu gói yt-dlp-ejs: cho phép tải bộ giải từ GitHub
            opts["remote_components"] = ["ejs:github"]
        sa = self.ip_source()
        if sa:
            opts["source_address"] = sa  # IPv4 ("0.0.0.0") hoặc IPv6 ("::")
        if kind == "video":
            cap = f"[height<=?{val}]" if val else ""
            if it.compat:
                # Chỉ lấy H.264 (avc1) + AAC (mp4a); fallback cuối cùng vẫn loại AV1
                if HAS_FFMPEG:
                    opts["format"] = (
                        f"bv*[vcodec^=avc1]{cap}+ba[acodec^=mp4a]"
                        f"/bv*[vcodec^=avc1]{cap}+ba"
                        f"/b[vcodec^=avc1]{cap}"
                        f"/bv*{cap}[vcodec!^=av01]+ba/b{cap}[vcodec!^=av01]"
                    )
                    opts["format_sort"] = ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
                    opts["merge_output_format"] = "mp4"
                else:
                    opts["format"] = f"b[vcodec^=avc1]{cap}/b{cap}[vcodec!^=av01]"
            elif HAS_FFMPEG:
                opts["format"] = f"bv*{cap}+ba/b{cap}"
                opts["format_sort"] = ["res", "ext:mp4:m4a"]
                opts["merge_output_format"] = "mp4"
            else:
                opts["format"] = f"b{cap}/b"
        else:
            codec = val
            if not HAS_FFMPEG or codec == "best":
                opts["format"] = "ba/b"
            else:
                opts["format"] = "ba[ext=m4a]/ba/b" if codec == "m4a" else "ba/b"
                pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
                if codec == "mp3":
                    pp["preferredquality"] = "320"
                opts["postprocessors"] = [pp, {"key": "FFmpegMetadata"}]
        return opts

    def progress_hook(self, it, d):
        if it.stop:
            raise DownloadCancelled()
        info = d.get("info_dict") or {}
        if it.title == it.url and info.get("title"):
            it.title = info["title"]
        if not it.expected_total:
            rf = info.get("requested_formats") or [info]
            it.n_streams = max(1, len(rf))
            it.expected_total = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in rf)

        fn = d.get("filename") or d.get("tmpfilename") or ""
        st = d.get("status")
        if st == "downloading":
            it.status = "Downloading"
            self.bot_streak = 0
            done = d.get("downloaded_bytes") or 0
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            it.streams[fn] = [done, tot]
            it.speed = d.get("speed") or 0
            it.eta = d.get("eta")
            now = time.time()
            spd = d.get("speed")
            if (it.min_speed and it.attempt < MAX_ATTEMPTS and spd is not None
                    and now - it.attempt_start > SLOW_GRACE):
                if spd < it.min_speed:
                    if it.slow_since is None:
                        it.slow_since = now
                    elif now - it.slow_since > SLOW_SECONDS:
                        raise RestartDownload()
                else:
                    it.slow_since = None
        elif st == "finished":
            size = d.get("total_bytes") or d.get("downloaded_bytes") or 0
            it.streams[fn] = [size, size]
            it.speed = 0
        # tổng hợp tiến độ của tất cả luồng (video + audio)
        done = sum(v[0] for v in it.streams.values())
        total = max(sum(v[1] for v in it.streams.values()), it.expected_total)
        it.downloaded = done
        it.total = total
        if total:
            it.percent = min(99.9, done * 100.0 / total)
        it.stage = f"{min(len(it.streams), it.n_streams)}/{it.n_streams}"
        it.filepath = fn if st == "finished" and it.n_streams == 1 else it.filepath

    def pp_hook(self, it, d):
        if d.get("status") == "started":
            name = d.get("postprocessor", "")
            if "Merger" in name:
                it.status = "Merging"
                it.speed = 0
            elif "ExtractAudio" in name:
                it.status = "Converting"
                it.speed = 0

    @staticmethod
    def is_retriable(msg):
        low = msg.lower()
        if any(k in low for k in NO_RETRY_KEYS):
            return False
        return any(k in low for k in RETRY_KEYS)

    @staticmethod
    def short_reason(msg):
        low = msg.lower()
        if "403" in low or "forbidden" in low:
            return "HTTP 403"
        if "429" in low or "too many requests" in low:
            return "HTTP 429"
        if "timed out" in low or "timeout" in low:
            return "quá thời gian chờ"
        return msg.splitlines()[0][:40] if msg else "lỗi mạng"

    @staticmethod
    def wait_interruptible(it, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if it.stop:
                raise DownloadCancelled()
            time.sleep(0.25)

    @staticmethod
    def apply_client(opts, idx):
        name = CLIENT_CHAIN[idx]
        if name is None:
            opts.pop("extractor_args", None)
        else:
            opts["extractor_args"] = {"youtube": {"player_client": [name]}}

    def run_item(self, it, opts):
        it.t_run = time.time()
        try:
            info = None
            retries = 0
            client_idx = self.client_pref
            while True:
                it.attempt = retries + 1
                it.attempt_start = time.time()
                it.slow_since = None
                try:
                    self.apply_client(opts, client_idx)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(it.url, download=True)
                    self.client_pref = client_idx  # nhớ client đã dùng được cho các video sau
                    break
                except RestartDownload:
                    retries += 1
                    reason = f"thử lại {retries}/{MAX_ATTEMPTS - 1} - tốc độ chậm"
                except DownloadCancelled:
                    raise
                except Exception as e:  # noqa
                    if it.stop:
                        raise DownloadCancelled()
                    msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").strip()
                    can_switch = client_idx < len(CLIENT_CHAIN) - 1
                    if can_switch and (is_bot_error(msg) or (
                            client_idx > 0 and "requested format is not available" in msg.lower())):
                        client_idx += 1
                        reason = f"đổi client YouTube: {CLIENT_CHAIN[client_idx]}"
                    elif retries >= MAX_ATTEMPTS - 1 or not self.is_retriable(msg):
                        raise
                    else:
                        retries += 1
                        reason = f"thử lại {retries}/{MAX_ATTEMPTS - 1} - {self.short_reason(msg)}"
                # sang lượt mới: lấy link mới, tiếp tục từ file .part đã tải
                it.note = reason
                it.status = "Starting"
                it.speed = 0
                self.wait_interruptible(it, min(1.5 * (retries + 1), 6) + random.uniform(0.5, 1.5))
            try:
                it.filepath = info["requested_downloads"][0]["filepath"]
            except Exception:  # noqa
                pass
            if info and it.title == it.url:
                it.title = info.get("title") or it.title
            it.percent = 100.0
            if it.total:
                it.downloaded = it.total
            it.status = "Completed"
        except DownloadCancelled:
            it.status = "Paused" if it.stop == "pause" else "Cancelled"
        except Exception as e:  # noqa
            if it.stop:
                it.status = "Paused" if it.stop == "pause" else "Cancelled"
            else:
                msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").strip()
                low = msg.lower()
                tip = "" if HAS_DENO else (
                    "\nMáy chưa có Deno (cần cho YouTube): cài bằng 'winget install DenoLand.Deno' "
                    "hoặc đặt deno.exe cạnh file exe.")
                if is_bot_error(msg):
                    msg += ("\nGợi ý: YouTube đang tạm chặn mạng của bạn (đã thử cả các client dự phòng). "
                            "Hãy đợi vài phút, tắt VPN/proxy, đổi mạng hoặc khởi động lại modem để đổi IP; "
                            "giảm 'Tải đồng thời' xuống 1 và tăng 'Nghỉ giữa video'." + tip
                            + "\n" + env_summary())
                    it.error = msg
                    it.status = "Error"
                    self.client_pref = 0  # lượt sau thử lại từ client mặc định
                    self.bot_streak += 1
                    if self.bot_streak < 3:  # thử lại sau một lúc nghỉ
                        self.cooldown_until = time.time() + 45
                        it.status = "Queued"
                    elif self.bot_streak == 3:  # chặn kéo dài -> dừng cả hàng đợi
                        self.ui_queue.put(self.on_bot_blocked)
                else:
                    if self.is_retriable(msg):
                        msg += (f"\nĐã tự thử lại {MAX_ATTEMPTS - 1} lần nhưng vẫn lỗi. Bấm 'Tiếp tục / Thử lại' "
                                "hoặc cập nhật yt-dlp:  pip install -U \"yt-dlp[default]\"" + tip)
                    elif "nsig" in low or "unable to extract" in low:
                        msg += "\nGợi ý: thử cập nhật yt-dlp:  pip install -U \"yt-dlp[default]\"" + tip
                    it.error = msg
                    it.status = "Error"
        finally:
            if it.t_run:
                it.elapsed += time.time() - it.t_run
            it.t_run = None
            it.speed = 0
            it.eta = None

    def start_item(self, it):
        it.status = "Starting"
        it.stop = None
        it.error = ""
        it.note = ""
        it.streams = {}
        it.min_speed = self.min_speed_bytes()
        opts = self.build_opts(it)  # tạo ở luồng giao diện (đọc biến Tk an toàn)
        it.thread = threading.Thread(target=self.run_item, args=(it, opts), daemon=True)
        it.thread.start()

    def schedule(self):
        now = time.time()
        if now < self.cooldown_until:
            return
        limit = self.max_concurrent()
        active = sum(1 for i in self.items if i.status in ACTIVE)
        for it in self.items:
            if active >= limit or now < self.next_start:
                break
            if it.status == "Queued" and not it.thread_alive():
                self.start_item(it)
                active += 1
                d = self.start_delay()
                self.next_start = now + random.uniform(d, d * 1.5)

    def on_bot_blocked(self):
        for it in self.items:
            if it.status == "Queued":
                it.status = "Paused"
        extra = "" if HAS_DENO else (
            "\n\n⚠ Máy chưa có Deno (JS runtime của yt-dlp). Thiếu nó, YouTube dễ bị chậm/lỗi.\n"
            "Cài: winget install DenoLand.Deno")
        messagebox.showwarning(
            "YouTube đang tạm chặn",
            "YouTube đang tạm chặn mạng của bạn (kiểm tra \"không phải bot\") dù app đã thử các client "
            "dự phòng. Đã tạm dừng hàng đợi.\n\n"
            "Cách khắc phục:\n"
            "1. Đợi vài phút (có thể lâu hơn nếu trước đó đã tải dồn dập).\n"
            "2. Tắt VPN/proxy; thử đổi mạng (ví dụ phát 4G từ điện thoại) hoặc khởi động lại modem để đổi IP.\n"
            "3. Giảm 'Tải đồng thời' xuống 1, tăng 'Nghỉ giữa video' lên 5-10 giây.\n"
            "4. Bấm 'Chọn tất cả' rồi 'Tiếp tục / Thử lại'.\n\n" + env_summary() + extra)

    # ------------------------------------------------------------- hiển thị
    def status_text(self, it):
        text = STATUS_VI.get(it.status, it.status)
        if it.note and it.status in ACTIVE:
            text += f" ({it.note})"
        return text

    def row_values(self, it):
        downloading = it.status == "Downloading"
        return (
            it.index, it.title, self.status_text(it), text_bar(it.percent),
            fmt_bytes(it.total), fmt_bytes(it.downloaded),
            fmt_speed(it.speed) if downloading else "--",
            fmt_time(it.eta_value()) if downloading else "--",
            fmt_time(it.elapsed_now()) if (it.t_run or it.elapsed) else "--",
        )

    @staticmethod
    def row_tag(it):
        return {"Completed": "ok", "Error": "err", "Paused": "pause", "Cancelled": "pause"}.get(it.status, "")

    def refresh_rows(self):
        for it in self.items:
            iid = str(it.id)
            vals = self.row_values(it)
            key = (vals, it.status)
            if self.row_cache.get(iid) != key:
                self.row_cache[iid] = key
                self.tree.item(iid, values=vals, tags=(self.row_tag(it),))

    def selected_items(self):
        return [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]

    def update_detail(self):
        sel = self.selected_items()
        if not sel:
            active = [i for i in self.items if i.status in ACTIVE]
            sel = active[:1]
        if not sel:
            for k in self.dv:
                self.dv[k].set("--" if k != "msg" else "")
            self.big_bar["value"] = 0
            self.big_pct.config(text="0.0%")
            return
        it = sel[0]
        downloading = it.status == "Downloading"
        self.dv["title"].set(it.title)
        self.dv["status"].set(self.status_text(it))
        self.dv["size"].set(fmt_bytes(it.total))
        self.dv["done"].set(fmt_bytes(it.downloaded))
        self.dv["speed"].set(fmt_speed(it.speed) if downloading else "--")
        self.dv["eta"].set(fmt_time(it.eta_value()) if downloading else "--")
        self.dv["elapsed"].set(fmt_time(it.elapsed_now()) if (it.t_run or it.elapsed) else "--")
        self.dv["stage"].set(it.stage or "--")
        self.dv["path"].set(it.filepath or it.folder)
        self.dv["msg"].set(it.error if it.status == "Error" else "")
        self.big_bar["value"] = it.percent
        self.big_pct.config(text=f"{it.percent:.1f}%")

    def update_statusbar(self):
        if self.busy_msg:
            self.status_var.set(self.busy_msg)
            return
        c = {"act": 0, "q": 0, "ok": 0, "err": 0, "pause": 0}
        speed = 0
        for it in self.items:
            if it.status in ACTIVE:
                c["act"] += 1
                speed += it.speed or 0
            elif it.status == "Queued":
                c["q"] += 1
            elif it.status == "Completed":
                c["ok"] += 1
            elif it.status == "Error":
                c["err"] += 1
            else:
                c["pause"] += 1
        wait = self.cooldown_until - time.time()
        self.status_var.set(
            f"Đang tải: {c['act']}   |   Chờ: {c['q']}   |   Tạm dừng: {c['pause']}   |   "
            f"Hoàn tất: {c['ok']}   |   Lỗi: {c['err']}   |   Tốc độ tổng: {fmt_speed(speed)}"
            + (f"   |   ⏳ Tạm nghỉ {int(wait)}s (YouTube chặn tạm thời)" if wait > 0 else ""))

    def tick(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.schedule()
        self.refresh_rows()
        self.update_detail()
        if self.btn_add.instate(["!disabled"]):
            self.update_statusbar()
        self.root.after(250, self.tick)

    # ------------------------------------------------------ item operations
    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def select_none(self):
        self.tree.selection_remove(self.tree.selection())

    def targets(self):
        sel = self.selected_items()
        if not sel:
            messagebox.showinfo("Chưa chọn dòng nào",
                                "Hãy chọn dòng cần thao tác (Ctrl+A hoặc nút 'Chọn tất cả').")
        return sel

    def pause_selected(self):
        for it in self.targets():
            if it.status in ACTIVE:
                it.stop = "pause"
            elif it.status == "Queued":
                it.status = "Paused"

    def resume_selected(self):
        targets = self.targets()
        if targets:
            self.bot_streak = 0
            self.cooldown_until = 0.0
        for it in targets:
            if it.status in ("Paused", "Error", "Cancelled") and not it.thread_alive():
                it.status = "Queued"
                it.error = ""

    def remove_selected(self):
        sel = self.targets()
        if not sel:
            return
        if len(sel) > 3 and not messagebox.askyesno("Xóa", f"Xóa {len(sel)} mục khỏi danh sách?"):
            return
        for it in sel:
            if it.status in ACTIVE:
                it.stop = "remove"
            self.items.remove(it)
            iid = str(it.id)
            self.by_iid.pop(iid, None)
            self.row_cache.pop(iid, None)
            self.tree.delete(iid)

    def clear_completed(self):
        for it in [i for i in self.items if i.status == "Completed"]:
            self.items.remove(it)
            iid = str(it.id)
            self.by_iid.pop(iid, None)
            self.row_cache.pop(iid, None)
            self.tree.delete(iid)

    def open_file_selected(self, only_selected=False):
        sel = self.selected_items()
        if not sel:
            return
        it = sel[0]
        if it.filepath and os.path.exists(it.filepath):
            open_path(it.filepath)
        elif not only_selected:
            messagebox.showinfo("Chưa có file", "File này chưa tải xong.")

    def open_folder_selected(self):
        sel = self.selected_items()
        it = sel[0] if sel else (self.items[0] if self.items else None)
        folder = it.folder if it else self.dir_var.get()
        if it and it.filepath and os.path.exists(it.filepath):
            reveal_file(it.filepath)
        elif os.path.isdir(folder):
            open_path(folder)
        else:
            open_path(self.dir_var.get())

    def copy_link(self):
        sel = self.selected_items()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(i.url for i in sel))

    def popup_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        self.menu.tk_popup(event.x_root, event.y_root)

    # --------------------------------------------------------------- close
    def on_close(self):
        if any(i.status in ACTIVE for i in self.items):
            if not messagebox.askyesno("Thoát", "Đang có file đang tải. Thoát và tạm dừng tất cả?\n"
                                                "(File tải dở vẫn được giữ, lần sau có thể tải tiếp.)"):
                return
        for it in self.items:
            if it.status in ACTIVE:
                it.stop = "pause"
        save_config({
            "preset": self.preset_var.get(), "outdir": self.dir_var.get(),
            "concurrent": self.max_concurrent(), "conns": self.connections(),
            "subfolder": self.sub_var.get(), "single": self.single_var.get(),
            "compat": self.compat_var.get(),
            "min_speed": self.min_speed_bytes() // 1024, "delay": self.start_delay(),
            "ip_mode": self.ip_var.get(),
        })
        self.root.destroy()


def main():
    if sys.platform.startswith("win"):
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
