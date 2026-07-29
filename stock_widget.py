from __future__ import annotations

import argparse
from http.client import RemoteDisconnected
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import winsound
except ImportError:  # pragma: no cover - Windows target
    winsound = None


APP_NAME = "台股提醒"
APP_VERSION = "v1.0.7"
API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
API_REFERER = "https://mis.twse.com.tw/stock/index.jsp"
SETTINGS_PATH = Path(os.getenv("TWSTOCKWIDGET_SETTINGS_PATH", Path(os.getenv("LOCALAPPDATA", Path.home())) / "TWStockWidget" / "settings.json"))

LIGHT_THEME = {
    "bg": "#F4F6F8",
    "card": "#FFFFFF",
    "text": "#17202A",
    "muted": "#73808C",
    "up": "#D94B4B",
    "down": "#14866D",
    "accent": "#356AE6",
    "border": "#E4E8ED",
}
DARK_THEME = {
    "bg": "#171A1F",
    "card": "#232830",
    "text": "#F2F4F7",
    "muted": "#9BA6B2",
    "up": "#FF5B5B",
    "down": "#3BC98A",
    "accent": "#5B8CFF",
    "border": "#38414D",
}


def theme_for(dark_mode: bool) -> dict[str, str]:
    return DARK_THEME if dark_mode else LIGHT_THEME


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    exchange: str
    price: float
    previous_close: float
    change_pct: float
    trade_date: str
    trade_time: str
    has_current_price: bool = True


def default_settings() -> dict:
    return {
        "refresh_seconds": 30,
        "always_on_top": True,
        "dark_mode": False,
        "window": {"x": 24, "y": 80},
        "items": [
            {"code": "TAIEX", "threshold": -2.0},
            {"code": "2330", "threshold": -3.0},
        ],
    }


def normalize_code(value: str) -> str:
    code = value.strip().upper()
    if code in {"TAIEX", "T00", "大盤"}:
        return "TAIEX"
    if not re.fullmatch(r"[0-9A-Z]{4,10}", code):
        raise ValueError("請輸入 4–10 碼台股代號，例如 2330、0050、00631L")
    return code


def normalize_threshold(value: object) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("通知門檻必須是數字，例如 -3 或 5") from exc
    if not math.isfinite(threshold) or not -100 <= threshold <= 100:
        raise ValueError("通知門檻必須介於 -100% 到 100%")
    return round(threshold, 2)


def clean_settings(raw: object) -> dict:
    base = default_settings()
    if not isinstance(raw, dict):
        return base

    try:
        refresh = max(10, min(600, int(raw.get("refresh_seconds", 30))))
    except (TypeError, ValueError):
        refresh = 30

    items: dict[str, float] = {"TAIEX": -2.0}
    for item in raw.get("items", []):
        if not isinstance(item, dict):
            continue
        try:
            items[normalize_code(str(item.get("code", "")))] = normalize_threshold(
                item.get("threshold", 0)
            )
        except ValueError:
            continue

    window = raw.get("window", {})
    try:
        x, y = int(window.get("x", 24)), int(window.get("y", 80))
    except (AttributeError, TypeError, ValueError):
        x, y = 24, 80

    return {
        "refresh_seconds": refresh,
        "always_on_top": bool(raw.get("always_on_top", True)),
        "dark_mode": bool(raw.get("dark_mode", False)),
        "window": {"x": x, "y": y},
        "items": [{"code": code, "threshold": threshold} for code, threshold in items.items()],
    }


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    try:
        return clean_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return default_settings()


def save_settings(settings: dict, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(clean_settings(settings), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(path)


def number(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_quote(message: dict, *, use_previous_close: bool = False) -> Quote | None:
    raw_code = str(message.get("c", "")).upper()
    if not raw_code:
        return None
    code = "TAIEX" if raw_code == "T00" else raw_code
    previous = number(message.get("y"))
    # z is the current trade; pz is the previous trade. y is yesterday's close,
    # not the current price, so never use it as a live-price fallback.
    price = next(
        (candidate for candidate in (number(message.get("z")), number(message.get("pz"))) if candidate is not None),
        None,
    )
    has_current_price = price is not None
    if price is None and use_previous_close:
        price = previous
    if price is None or previous is None or previous <= 0:
        return None
    return Quote(
        code=code,
        name=str(message.get("n") or code),
        exchange=str(message.get("ex") or "tse"),
        price=price,
        previous_close=previous,
        change_pct=(price - previous) / previous * 100 if has_current_price else 0.0,
        trade_date=str(message.get("d") or ""),
        trade_time=str(message.get("t") or message.get("%") or ""),
        has_current_price=has_current_price,
    )


def query_channels(codes: list[str]) -> list[str]:
    channels: list[str] = []
    for code in codes:
        normalized = normalize_code(code)
        if normalized == "TAIEX":
            channels.append("tse_t00.tw")
        else:
            channels.extend((f"tse_{normalized}.tw", f"otc_{normalized}.tw"))
    return channels

def twse_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # TWSE's chain lacks an SKI extension rejected by Python 3.14 strict mode.
    # CA and hostname verification remain enabled.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context




def fetch_quotes(codes: list[str], previous_quotes: dict[str, Quote] | None = None) -> dict[str, Quote]:
    channels = query_channels(codes)
    params = urllib.parse.urlencode(
        {"ex_ch": "|".join(channels), "json": "1", "delay": "0"}
    )
    request = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={"User-Agent": "Mozilla/5.0 TWStockWidget/1.0", "Referer": API_REFERER},
    )
    with urllib.request.urlopen(request, timeout=10, context=twse_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("rtcode") != "0000":
        raise RuntimeError(payload.get("rtmessage") or "證交所回傳未知錯誤")

    wanted = {normalize_code(code) for code in codes}
    quotes: dict[str, Quote] = {}
    messages: dict[str, dict] = {}
    for message in payload.get("msgArray", []):
        raw_code = str(message.get("c", "")).upper()
        code = "TAIEX" if raw_code == "T00" else raw_code
        if code in wanted:
            messages[code] = message
        quote = parse_quote(message)
        if quote and quote.code in wanted:
            quotes[quote.code] = quote

    for code in wanted - quotes.keys():
        if previous_quotes and code in previous_quotes:
            quotes[code] = previous_quotes[code]
        elif code in messages:
            quote = parse_quote(messages[code], use_previous_close=True)
            if quote:
                quotes[code] = quote
    return quotes


def threshold_reached(change_pct: float, threshold: float) -> bool:
    if threshold == 0:
        return False
    displayed_change = round(change_pct, 2)
    return displayed_change <= threshold if threshold < 0 else displayed_change >= threshold


def price_text(value: float) -> str:
    return f"{value:,.2f}"


class StockWidget:
    def __init__(self, root: tk.Tk, *, auto_refresh: bool = True) -> None:
        self.root = root
        self.settings = load_settings()
        self.theme = theme_for(self.settings["dark_mode"])
        self.quotes: dict[str, Quote] = {}
        self.alerted: set[tuple[str, str, float]] = set()
        self.last_notification = ""
        self.refreshing = False
        self.refresh_job: str | None = None
        self.drag_offset = (0, 0)

        root.title(APP_NAME)
        root.configure(bg=self.theme["bg"])
        root.overrideredirect(True)
        root.attributes("-topmost", self.settings["always_on_top"])
        root.bind("<Escape>", lambda _event: self.close())

        self._build_ui()
        self._set_geometry()
        self.render_rows()
        if auto_refresh:
            root.after(150, self.refresh)

    def _build_ui(self) -> None:
        c = self.theme
        shell = tk.Frame(self.root, bg=c["bg"], highlightbackground=c["border"], highlightthickness=1)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=c["card"], height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self.start_drag)
        header.bind("<B1-Motion>", self.drag)

        title = tk.Label(header, text="台股提醒", bg=c["card"], fg=c["text"], font=("Microsoft JhengHei UI", 13, "bold"))
        title.pack(side="left", padx=(14, 4))
        title.bind("<ButtonPress-1>", self.start_drag)
        title.bind("<B1-Motion>", self.drag)
        tk.Label(header, text="TWSE", bg=c["card"], fg=c["muted"], font=("Segoe UI", 8)).pack(side="left", pady=(5, 0))

        self._header_button(header, "×", self.close, "#A33A3A").pack(side="right", padx=(0, 8))
        self._header_button(header, "⚙", self.open_settings).pack(side="right")
        self.refresh_button = self._header_button(header, "↻", self.refresh)
        self.refresh_button.pack(side="right")

        list_shell = tk.Frame(shell, bg=c["bg"])
        list_shell.pack(fill="both", expand=True, padx=(8, 4), pady=(8, 4))
        self.canvas = tk.Canvas(list_shell, bg=c["bg"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(list_shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.rows = tk.Frame(self.canvas, bg=c["bg"])
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.canvas_window, width=event.width))
        self.canvas.bind_all("<MouseWheel>", self.mouse_wheel)

        footer = tk.Frame(shell, bg=c["bg"], height=30)
        footer.pack(fill="x", padx=10, pady=(0, 5))
        footer.pack_propagate(False)
        self.status = tk.Label(footer, text="等待更新", bg=c["bg"], fg=c["muted"], anchor="w", font=("Microsoft JhengHei UI", 8))
        self.status.pack(side="left", fill="both", expand=True)
        self.last_notice = tk.Label(footer, text=self.last_notification, bg=c["bg"], fg=c["muted"], anchor="e", font=("Microsoft JhengHei UI", 8))
        self.last_notice.pack(side="right", padx=(8, 8))
        tk.Label(footer, text=APP_VERSION, bg=c["bg"], fg=c["muted"], anchor="e", font=("Segoe UI", 8)).pack(side="right")

    def _header_button(self, parent: tk.Widget, text: str, command, fg: str | None = None) -> tk.Button:
        c = self.theme
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=c["card"],
            fg=fg or c["muted"],
            activebackground=c["bg"],
            activeforeground=c["text"],
            relief="flat",
            bd=0,
            width=3,
            cursor="hand2",
            font=("Segoe UI Symbol", 12),
        )

    def _set_geometry(self) -> None:
        count = len(self.settings["items"])
        height = 91 + min(max(count, 1), 8) * 64
        position = self.settings["window"]
        self.root.geometry(f"360x{height}+{position['x']}+{position['y']}")

    def start_drag(self, event: tk.Event) -> None:
        self.drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag(self, event: tk.Event) -> None:
        x = event.x_root - self.drag_offset[0]
        y = event.y_root - self.drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def mouse_wheel(self, event: tk.Event) -> None:
        if self.root.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def render_rows(self) -> None:
        c = self.theme
        for child in self.rows.winfo_children():
            child.destroy()

        for item in self.settings["items"]:
            code = item["code"]
            threshold = item["threshold"]
            quote = self.quotes.get(code)
            card = tk.Frame(self.rows, bg=c["card"], height=56, highlightbackground=c["border"], highlightthickness=1)
            card.pack(fill="x", pady=(0, 7))
            card.pack_propagate(False)

            name = quote.name if quote else ("發行量加權指數" if code == "TAIEX" else "等待行情")
            tk.Label(card, text=code, bg=c["card"], fg=c["text"], font=("Segoe UI", 10, "bold"), anchor="w").place(x=11, y=7, width=78)
            tk.Label(card, text=name, bg=c["card"], fg=c["muted"], font=("Microsoft JhengHei UI", 8), anchor="w").place(x=11, y=30, width=132)

            if quote:
                color = c["muted"] if not quote.has_current_price else c["up"] if quote.change_pct > 0 else c["down"] if quote.change_pct < 0 else c["muted"]
                tk.Label(card, text=price_text(quote.price), bg=c["card"], fg=color, font=("Segoe UI", 13, "bold"), anchor="e").place(x=140, y=6, width=106)
                change_text = "—" if not quote.has_current_price else f"{quote.change_pct:+.2f}%"
                tk.Label(card, text=change_text, bg=c["card"], fg=color, font=("Segoe UI", 11, "bold"), anchor="e").place(x=250, y=8, width=88)
            else:
                tk.Label(card, text="—", bg=c["card"], fg=c["muted"], font=("Segoe UI", 13), anchor="e").place(x=140, y=7, width=198)

            threshold_text = "通知關閉" if threshold == 0 else f"門檻 {threshold:+g}%"
            tk.Label(card, text=threshold_text, bg=c["card"], fg=c["muted"], font=("Microsoft JhengHei UI", 8), anchor="e").place(x=210, y=33, width=128)

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.status.configure(text="更新中…", fg=self.theme["muted"])
        codes = [item["code"] for item in self.settings["items"]]

        def worker() -> None:
            try:
                try:
                    result = fetch_quotes(codes)
                except (ConnectionError, RemoteDisconnected, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
                    time.sleep(2)
                    result = fetch_quotes(codes)
                self.root.after(0, lambda: self.finish_refresh(result, None))
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda: self.finish_refresh({}, message))

        threading.Thread(target=worker, daemon=True).start()

    def finish_refresh(self, quotes: dict[str, Quote], error: str | None) -> None:
        self.refreshing = False
        self.refresh_button.configure(state="normal")
        if error:
            self.status.configure(text=f"更新失敗：{error}", fg=self.theme["up"])
        else:
            self.quotes.update(
                {
                    code: quote
                    for code, quote in quotes.items()
                    if quote.has_current_price or code not in self.quotes
                }
            )
            missing = len(self.settings["items"]) - len(quotes)
            latest = max((f"{q.trade_date} {q.trade_time}" for q in quotes.values()), default="無資料")
            suffix = f" · {missing} 個代號無資料" if missing else ""
            self.status.configure(text=f"TWSE MIS · {latest}{suffix}", fg=self.theme["muted"])
            self.render_rows()
            self.notify_reached_thresholds()
        self.schedule_refresh()

    def schedule_refresh(self) -> None:
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(self.settings["refresh_seconds"] * 1000, self.refresh)

    def notify_reached_thresholds(self) -> None:
        notices: list[str] = []
        for item in self.settings["items"]:
            quote = self.quotes.get(item["code"])
            threshold = item["threshold"]
            if not quote or not quote.has_current_price or not threshold_reached(quote.change_pct, threshold):
                continue
            key = (quote.code, quote.trade_date, threshold)
            if key in self.alerted:
                continue
            self.alerted.add(key)
            notices.append(f"{quote.code} {quote.name}  {quote.change_pct:+.2f}%")
        if notices:
            self.last_notification = "通知 " + "、".join(notices)
            self.last_notice.configure(text=self.last_notification)
            self.show_notification("已達通知門檻", "\n".join(notices))

    def show_notification(self, title: str, body: str) -> None:
        accent = self.theme["accent"]
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=accent)
        width, height = 330, 72 + 23 * body.count("\n")
        x = popup.winfo_screenwidth() - width - 24
        y = popup.winfo_screenheight() - height - 64
        popup.geometry(f"{width}x{height}+{x}+{y}")
        tk.Label(popup, text=title, bg=accent, fg="white", font=("Microsoft JhengHei UI", 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(popup, text=body, bg=accent, fg="white", font=("Microsoft JhengHei UI", 9), justify="left", anchor="w").pack(fill="both", expand=True, padx=14, pady=(0, 10))
        popup.bind("<Button-1>", lambda _event: popup.destroy())
        popup.after(10000, lambda: popup.destroy() if popup.winfo_exists() else None)

    def rebuild_ui(self) -> None:
        self.theme = theme_for(self.settings["dark_mode"])
        for child in self.root.winfo_children():
            child.destroy()
        self.root.configure(bg=self.theme["bg"])
        self._build_ui()
        self._set_geometry()
        self.render_rows()

    def open_settings(self) -> None:
        c = self.theme
        dialog = tk.Toplevel(self.root)
        dialog.title("追蹤設定")
        dialog.configure(bg=c["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.attributes("-topmost", True)
        dialog.geometry(f"430x430+{self.root.winfo_x() + 24}+{self.root.winfo_y() + 40}")

        tk.Label(dialog, text="追蹤代號與通知門檻", bg=c["bg"], fg=c["text"], font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(dialog, text="負數表示跌幅、正數表示漲幅；0 表示不通知。", bg=c["bg"], fg=c["muted"], font=("Microsoft JhengHei UI", 9)).pack(anchor="w", padx=16, pady=(0, 10))

        style = ttk.Style(dialog)
        style.configure("Stock.Treeview", background=c["card"], fieldbackground=c["card"], foreground=c["text"])
        style.map("Stock.Treeview", background=[("selected", c["accent"])], foreground=[("selected", "white")])
        tree = ttk.Treeview(dialog, columns=("code", "threshold"), show="headings", height=9, selectmode="browse", style="Stock.Treeview")
        tree.heading("code", text="代號")
        tree.heading("threshold", text="通知門檻")
        tree.column("code", width=180, anchor="center")
        tree.column("threshold", width=180, anchor="center")
        tree.pack(fill="x", padx=16)

        working = [dict(item) for item in self.settings["items"]]

        def fill_tree() -> None:
            tree.delete(*tree.get_children())
            for item in working:
                tree.insert("", "end", values=(item["code"], f"{item['threshold']:+g}%"))

        form = tk.Frame(dialog, bg=c["bg"])
        form.pack(fill="x", padx=16, pady=10)
        code_var, threshold_var = tk.StringVar(), tk.StringVar(value="-3")
        entry_options = {"bg": c["card"], "fg": c["text"], "insertbackground": c["text"], "selectbackground": c["accent"], "selectforeground": "white"}
        tk.Entry(form, textvariable=code_var, font=("Segoe UI", 10), relief="solid", bd=1, **entry_options).pack(side="left", fill="x", expand=True, ipady=5)
        tk.Entry(form, textvariable=threshold_var, font=("Segoe UI", 10), width=9, relief="solid", bd=1, **entry_options).pack(side="left", padx=7, ipady=5)

        def select_item(_event=None) -> None:
            selected = tree.selection()
            if not selected:
                return
            code, threshold = tree.item(selected[0], "values")
            code_var.set(code)
            threshold_var.set(str(threshold).rstrip("%"))

        def add_or_update() -> None:
            try:
                code = normalize_code(code_var.get())
                threshold = normalize_threshold(threshold_var.get())
            except ValueError as exc:
                messagebox.showerror("設定錯誤", str(exc), parent=dialog)
                return
            match = next((item for item in working if item["code"] == code), None)
            if match:
                match["threshold"] = threshold
            else:
                working.append({"code": code, "threshold": threshold})
            fill_tree()
            code_var.set("")

        def remove() -> None:
            selected = tree.selection()
            if not selected:
                return
            code = tree.item(selected[0], "values")[0]
            if code == "TAIEX":
                messagebox.showinfo("保留大盤", "TAIEX 是大盤追蹤項目，不能移除；可將門檻設為 0。", parent=dialog)
                return
            working[:] = [item for item in working if item["code"] != code]
            fill_tree()

        tk.Button(form, text="新增／更新", command=add_or_update, bg=c["accent"], fg="white", relief="flat", padx=10, pady=5).pack(side="left")
        tree.bind("<<TreeviewSelect>>", select_item)

        controls = tk.Frame(dialog, bg=c["bg"])
        controls.pack(fill="x", padx=16)
        tk.Button(controls, text="移除選取", command=remove, relief="flat", fg=c["up"], bg=c["bg"]).pack(side="left")
        tk.Label(controls, text="更新秒數", bg=c["bg"], fg=c["muted"]).pack(side="left", padx=(20, 5))
        refresh_var = tk.StringVar(value=str(self.settings["refresh_seconds"]))
        tk.Spinbox(controls, from_=10, to=600, increment=5, textvariable=refresh_var, width=5, **entry_options).pack(side="left")
        topmost_var = tk.BooleanVar(value=self.settings["always_on_top"])
        tk.Checkbutton(controls, text="保持最上層", variable=topmost_var, bg=c["bg"], fg=c["text"], activebackground=c["bg"], selectcolor=c["card"]).pack(side="right")
        dark_mode_var = tk.BooleanVar(value=self.settings["dark_mode"])
        tk.Checkbutton(controls, text="深色模式", variable=dark_mode_var, bg=c["bg"], fg=c["text"], activebackground=c["bg"], selectcolor=c["card"]).pack(side="right", padx=(0, 12))

        def apply() -> None:
            try:
                refresh_seconds = max(1, min(600, int(refresh_var.get())))
            except ValueError:
                messagebox.showerror("設定錯誤", "更新秒數必須是 1–600 的整數", parent=dialog)
                return
            self.settings["items"] = working
            self.settings["refresh_seconds"] = refresh_seconds
            self.settings["always_on_top"] = topmost_var.get()
            self.settings["dark_mode"] = dark_mode_var.get()
            save_settings(self.settings)
            self.root.attributes("-topmost", topmost_var.get())
            dialog.destroy()
            self.rebuild_ui()
            self.refresh()

        tk.Button(dialog, text="儲存設定", command=apply, bg=c["text"], fg=c["bg"], relief="flat", padx=18, pady=7).pack(side="right", padx=16, pady=14)
        fill_tree()
        dialog.grab_set()

    def close(self) -> None:
        self.settings["window"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
        try:
            save_settings(self.settings)
        finally:
            self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows 台股桌面提醒 Widget")
    parser.add_argument("--smoke-test", action="store_true", help="建立並關閉介面，用於自動檢查")
    args = parser.parse_args()
    root = tk.Tk()
    StockWidget(root, auto_refresh=not args.smoke_test)
    if args.smoke_test:
        root.update_idletasks()
        root.update()
        root.destroy()
        print("GUI smoke test passed")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
