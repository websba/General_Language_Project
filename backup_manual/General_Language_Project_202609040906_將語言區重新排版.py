"""語言聽說練習：選語言、點中文詞彙，播放對應外語語音。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from deep_translator import GoogleTranslator, MyMemoryTranslator
from deep_translator.exceptions import TooManyRequests, TranslationNotFound
import edge_tts

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "Language_Words.txt"
CACHE_DIR = APP_DIR / "audio_cache"
POLL_MS = 800
WORD_MIN_CELL_PX = 160
WORD_GRID_PAD_PX = 12
WORD_BTN_INNER_PAD_PX = 20
WORD_REFLOW_THRESHOLD_PX = 8
DEFAULT_WIN_W = 740
DEFAULT_WIN_H = 520

BUILTIN_LANG_CODES = {
    "英文": "en",
    "日文": "ja",
    "印尼文": "id",
    "越南文": "vi",
    "菲律賓文": "tl",
    "西班牙文": "es",
    "俄文": "ru",
    "阿拉伯文": "ar",
    "拉丁文": "la",
}

LANGUAGE_VOICES = {
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "id": "id-ID-GadisNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "tl": "fil-PH-BlessicaNeural",
    "fil": "fil-PH-BlessicaNeural",
    "th": "th-TH-PremwadeeNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "pt": "pt-BR-FranciscaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "la": "it-IT-ElsaNeural",
    "it": "it-IT-ElsaNeural",
}

MYMEMORY_CODES = {
    "zh-tw": "zh-TW",
    "en": "en-GB",
    "ja": "ja-JP",
    "id": "id-ID",
    "vi": "vi-VN",
    "tl": "tl-PH",
    "fil": "fil-PH",
    "es": "es-ES",
    "ru": "ru-RU",
    "ar": "ar-SA",
    "la": "la-XN",
    "th": "th-TH",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "de": "de-DE",
    "pt": "pt-BR",
    "zh": "zh-CN",
    "it": "it-IT",
}


def parse_language_words(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """讀取 [Languages] 與 [Words]。回傳 [(顯示名稱, 語言代碼), ...] 與詞彙清單。"""
    text = path.read_text(encoding="utf-8-sig")
    languages: list[tuple[str, str]] = []
    words: list[str] = []
    section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section == "Languages":
            if "=" in line:
                name, code = line.split("=", 1)
                name, code = name.strip(), code.strip().lower()
            else:
                name, code = line, BUILTIN_LANG_CODES.get(line, "").lower()
            if name and code:
                languages.append((name, code))
        elif section == "Words":
            words.append(line)

    return languages, words


def resolve_voice(lang_code: str) -> str:
    code = lang_code.lower()
    if code in LANGUAGE_VOICES:
        return LANGUAGE_VOICES[code]
    prefix = code.split("-")[0]
    return LANGUAGE_VOICES.get(prefix, "en-US-JennyNeural")


def mymemory_code(lang_code: str) -> str:
    code = lang_code.lower()
    if code in MYMEMORY_CODES:
        return MYMEMORY_CODES[code]
    prefix = code.split("-")[0]
    return MYMEMORY_CODES.get(prefix, lang_code)


def format_play_error(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, TranslationNotFound) or "No translation was found" in text:
        detail = "翻譯服務暫時沒有結果，請再試一次"
    elif isinstance(exc, TooManyRequests) or "too many requests" in text.lower():
        detail = "翻譯請求太頻繁，請稍候再試"
    else:
        detail = text
    if detail.startswith("播放失敗："):
        return detail
    return f"播放失敗：{detail}"


def translate_text(
    word: str,
    target_lang: str,
    *,
    source_lang: str = "zh-TW",
    on_status: Callable[[str], None] | None = None,
) -> str:
    last_error: BaseException | None = None
    for attempt in range(2):
        try:
            result = GoogleTranslator(source=source_lang, target=target_lang).translate(
                word
            )
            if result and result.strip():
                return result.strip()
            last_error = RuntimeError("翻譯結果是空的")
        except Exception as exc:  # noqa: BLE001 — 改走重試或備援
            last_error = exc
        if attempt == 0:
            time.sleep(0.4)

    if on_status is not None:
        on_status("Google 暫時失敗，改用備用翻譯器…")
    try:
        result = MyMemoryTranslator(
            source=mymemory_code(source_lang),
            target=mymemory_code(target_lang),
        ).translate(word)
        if result and result.strip():
            return result.strip()
        last_error = RuntimeError("翻譯結果是空的")
    except Exception as exc:  # noqa: BLE001 — 轉成中文錯誤訊息
        last_error = exc

    raise last_error or RuntimeError("翻譯失敗")


def cache_path_for(lang_code: str, word: str) -> Path:
    digest = hashlib.md5(f"{lang_code}:{word}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.mp3"


def text_cache_path_for(lang_code: str, word: str) -> Path:
    return cache_path_for(lang_code, word).with_suffix(".json")


def load_text_cache(path: Path, word: str) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    translated = str(data.get("translated") or "").strip()
    if str(data.get("src") or "").strip() != word or not translated:
        return None
    back = str(data.get("back") or "").strip() or "回譯失敗"
    return {"translated": translated, "back": back}


def save_text_cache(path: Path, word: str, translated: str, back: str) -> None:
    path.parent.mkdir(exist_ok=True)
    payload = {"src": word, "translated": translated, "back": back}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def format_play_triple(word: str, translated: str, back: str) -> str:
    return f"{word} → {translated} → {back}"


async def generate_speech(text: str, voice: str, dest: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(dest))


class LanguagePracticeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("語言聽說練習")
        self.root.minsize(480, 360)
        self.root.geometry(f"{DEFAULT_WIN_W}x{DEFAULT_WIN_H}")

        self.languages: list[tuple[str, str]] = []
        self.words: list[str] = []
        self.lang_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就緒")
        self._last_mtime: float | None = None
        self._play_lock = threading.Lock()
        self._busy = False
        self.last_word: str | None = None
        self._lang_layout_width = 0
        self._word_cols = 0
        self._word_layout_width = 0

        CACHE_DIR.mkdir(exist_ok=True)
        pygame.mixer.init()

        self._build_layout()
        self.reload_config(initial=True)
        self.root.after(POLL_MS, self.poll_config_file)

    def _build_layout(self) -> None:
        pad = {"padx": 12, "pady": 8}

        lang_box = tk.LabelFrame(self.root, text="選擇語言", padx=8, pady=8)
        lang_box.pack(fill=tk.X, **pad)
        self.lang_frame = tk.Frame(lang_box)
        self.lang_frame.pack(fill=tk.X)
        self.lang_frame.pack_propagate(False)
        self.lang_frame.bind("<Configure>", self._on_lang_frame_configure)

        words_box = tk.LabelFrame(self.root, text="中文詞彙 / 例句", padx=8, pady=8)
        words_box.pack(fill=tk.BOTH, expand=True, **pad)

        scroll_holder = tk.Frame(words_box)
        scroll_holder.pack(fill=tk.BOTH, expand=True)

        self.words_canvas = tk.Canvas(scroll_holder, highlightthickness=0)
        self.words_scroll = tk.Scrollbar(
            scroll_holder,
            orient=tk.VERTICAL,
            command=self.words_canvas.yview,
        )
        self.words_canvas.configure(yscrollcommand=self.words_scroll.set)
        self.words_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.words_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.words_frame = tk.Frame(self.words_canvas)
        self._words_window = self.words_canvas.create_window(
            (0, 0), window=self.words_frame, anchor="nw"
        )
        self.words_frame.bind("<Configure>", self._on_words_inner_configure)
        self.words_canvas.bind("<Configure>", self._on_words_canvas_configure)
        self._bind_word_wheel(self.words_canvas)
        self._bind_word_wheel(self.words_frame)
        self._bind_word_wheel(self.words_scroll)

        self.status_label = tk.Label(
            words_box,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
            wraplength=400,
            relief=tk.SUNKEN,
        )
        self.status_label.pack(fill=tk.X, pady=(8, 0))
        self.status_label.bind("<Configure>", self._on_status_configure)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _on_status_configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.status_label:
            return
        wrap = max(80, event.width - 8)
        current = int(str(self.status_label.cget("wraplength") or 0))
        if current != wrap:
            self.status_label.configure(wraplength=wrap)

    def poll_config_file(self) -> None:
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            self.root.after(POLL_MS, self.poll_config_file)
            return

        if self._last_mtime is None:
            self._last_mtime = mtime
        elif mtime != self._last_mtime:
            self._last_mtime = mtime
            self.reload_config(initial=False)

        self.root.after(POLL_MS, self.poll_config_file)

    def reload_config(self, initial: bool) -> None:
        try:
            languages, words = parse_language_words(CONFIG_PATH)
        except OSError as exc:
            if initial:
                messagebox.showerror("讀取失敗", f"無法讀取 {CONFIG_PATH.name}：\n{exc}")
                self.root.destroy()
            else:
                self.set_status(f"讀取失敗，維持目前介面：{exc}")
            return
        except Exception as exc:  # noqa: BLE001 — 檔案可能正在寫入
            if initial:
                messagebox.showerror("格式錯誤", str(exc))
                self.root.destroy()
            else:
                self.set_status(f"檔案尚未就緒，稍後再試：{exc}")
            return

        if not languages:
            msg = "[Languages] 沒有有效的語言（需名稱與代碼，例如 英文=en）"
            if initial:
                messagebox.showerror("設定錯誤", msg)
                self.root.destroy()
            else:
                self.set_status(msg)
            return

        prev_lang = self.lang_var.get()
        lang_changed = languages != self.languages
        words_changed = words != self.words

        if lang_changed:
            self.languages = languages
            self._rebuild_language_buttons(prev_lang)

        if words_changed:
            self.words = words
            self._rebuild_word_buttons()
            if self.last_word is not None and self.last_word not in self.words:
                self.last_word = None

        if initial:
            self.set_status("就緒。點中文詞彙聽外語；再點語言可換語重聽同一句。")
        elif words_changed or lang_changed:
            parts = []
            if lang_changed:
                parts.append("語言")
            if words_changed:
                parts.append("詞彙")
            self.set_status(f"已更新{'、'.join(parts)}，不必重開程式。")

    def _rebuild_language_buttons(self, prev_lang: str) -> None:
        for child in self.lang_frame.winfo_children():
            child.destroy()

        names = [name for name, _code in self.languages]
        selected = prev_lang if prev_lang in names else names[0]
        self.lang_var.set(selected)
        self._lang_layout_width = 0

        for name, _code in self.languages:
            button = tk.Button(
                self.lang_frame,
                text=name,
                command=lambda n=name: self.on_language_click(n),
            )
            button.place(x=0, y=0)

        self._refresh_language_button_state()
        self.lang_frame.after_idle(self._reflow_language_buttons)

    def _refresh_language_button_state(self) -> None:
        selected = self.lang_var.get()
        for child in self.lang_frame.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(
                    relief=tk.SUNKEN if str(child.cget("text")) == selected else tk.RAISED
                )

    def _on_lang_frame_configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.lang_frame:
            return
        self._reflow_language_buttons(event.width)

    def _reflow_language_buttons(self, width: int | None = None) -> None:
        buttons = [
            child
            for child in self.lang_frame.winfo_children()
            if isinstance(child, tk.Button)
        ]
        if not buttons:
            return
        if width is None or width <= 1:
            width = self.lang_frame.winfo_width()
        if width <= 1:
            return
        if abs(width - self._lang_layout_width) <= 2 and self._lang_layout_width > 0:
            return
        self._lang_layout_width = width

        padx = 6
        pady = 2
        x = 0
        y = 0
        row_h = 0
        for button in buttons:
            need = button.winfo_reqwidth() + padx * 2
            height = button.winfo_reqheight() + pady * 2
            if x > 0 and x + need > width:
                x = 0
                y += row_h
                row_h = 0
            button.place(x=x + padx, y=y + pady)
            x += need
            row_h = max(row_h, height)

        total_h = max(y + row_h, 1)
        if int(self.lang_frame.cget("height") or 0) != total_h:
            self.lang_frame.configure(height=total_h)

    def _rebuild_word_buttons(self) -> None:
        for child in self.words_frame.winfo_children():
            child.destroy()

        self._word_cols = 0
        self._word_layout_width = 0

        if not self.words:
            empty = tk.Label(self.words_frame, text="[Words] 目前沒有詞彙")
            empty.pack(anchor="w")
            self._bind_word_wheel(empty)
            self.words_frame.after_idle(self._sync_words_scrollregion)
            return

        for word in self.words:
            button = tk.Button(
                self.words_frame,
                text=word,
                justify="center",
                command=lambda w=word: self.on_word_click(w),
            )
            button.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
            self._bind_word_wheel(button)

        self.words_frame.after_idle(self._reflow_word_buttons)
        self.words_frame.after_idle(self._sync_words_scrollregion)

    def _bind_word_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_word_mousewheel)

    def _on_word_mousewheel(self, event: tk.Event[tk.Misc]) -> str:
        self.words_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _sync_words_scrollregion(self) -> None:
        bbox = self.words_canvas.bbox("all")
        if bbox is not None:
            self.words_canvas.configure(scrollregion=bbox)

    def _on_words_inner_configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.words_frame:
            return
        self._sync_words_scrollregion()

    def _on_words_canvas_configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.words_canvas:
            return
        self.words_canvas.itemconfigure(self._words_window, width=event.width)
        self._reflow_word_buttons(event.width)

    def _reflow_word_buttons(self, width: int | None = None) -> None:
        buttons = [
            child
            for child in self.words_frame.winfo_children()
            if isinstance(child, tk.Button)
        ]
        if not buttons:
            return
        if width is None or width <= 1:
            width = self.words_canvas.winfo_width()
        if width <= 1:
            return

        cols = min(len(buttons), max(1, width // WORD_MIN_CELL_PX))
        if (
            cols == self._word_cols
            and abs(width - self._word_layout_width) <= WORD_REFLOW_THRESHOLD_PX
        ):
            return

        old_cols = self._word_cols
        self._word_cols = cols
        self._word_layout_width = width

        cell_w = max(1, width // cols)
        btn_font = tkfont.nametofont("TkDefaultFont")
        row = 0
        col = 0
        for button in buttons:
            text_w = btn_font.measure(str(button.cget("text"))) + WORD_BTN_INNER_PAD_PX
            span = min(cols, max(1, math.ceil(text_w / cell_w)))
            if col + span > cols:
                row += 1
                col = 0
            usable = max(1, span * cell_w - WORD_GRID_PAD_PX)
            if text_w > usable:
                button.configure(wraplength=max(80, usable))
            else:
                button.configure(wraplength=0)
            button.grid(
                row=row,
                column=col,
                columnspan=span,
                padx=6,
                pady=6,
                sticky="nsew",
            )
            col += span
            if col >= cols:
                row += 1
                col = 0

        for index in range(cols):
            self.words_frame.grid_columnconfigure(index, weight=1)
        for index in range(cols, max(old_cols, cols)):
            self.words_frame.grid_columnconfigure(index, weight=0)

    def selected_language(self) -> tuple[str, str] | None:
        name = self.lang_var.get()
        for lang_name, code in self.languages:
            if lang_name == name:
                return lang_name, code
        return None

    def on_language_click(self, name: str) -> None:
        self.lang_var.set(name)
        self._refresh_language_button_state()
        self._lang_layout_width = 0
        self.lang_frame.after_idle(self._reflow_language_buttons)
        if self.last_word is None:
            self.set_status("請先點一個中文詞彙")
            return
        self._play_word(self.last_word)

    def on_word_click(self, word: str) -> None:
        self.last_word = word
        self._play_word(word)

    def _play_word(self, word: str) -> None:
        selected = self.selected_language()
        if selected is None:
            self.set_status("請先選擇語言")
            return
        if self._busy:
            self.set_status("正在處理上一句，請稍候…")
            return

        lang_name, lang_code = selected
        self._busy = True
        self.set_status(f"準備播放：{word} → {lang_name}")
        thread = threading.Thread(
            target=self._speak_worker,
            args=(word, lang_name, lang_code),
            daemon=True,
        )
        thread.start()

    def _speak_worker(self, word: str, lang_name: str, lang_code: str) -> None:
        dest = cache_path_for(lang_code, word)
        text_dest = text_cache_path_for(lang_code, word)

        def status_cb(message: str) -> None:
            self.root.after(0, lambda m=message: self.set_status(m))

        try:
            cached = load_text_cache(text_dest, word) if text_dest.exists() else None
            if cached is None:
                status_cb(f"翻譯中：{word} → {lang_name}")
                translated = translate_text(word, lang_code, on_status=status_cb)
                status_cb(f"回譯中：{word} → {translated}")
                try:
                    back = translate_text(
                        translated,
                        "zh-TW",
                        source_lang=lang_code,
                        on_status=status_cb,
                    )
                except Exception:  # noqa: BLE001 — 回譯失敗仍播放
                    back = "回譯失敗"
                save_text_cache(text_dest, word, translated, back)
            else:
                translated = cached["translated"]
                back = cached["back"]

            triple = format_play_triple(word, translated, back)
            if not dest.exists() or dest.stat().st_size == 0:
                status_cb(f"產生語音：{triple}")
                voice = resolve_voice(lang_code)
                dest.parent.mkdir(exist_ok=True)
                tmp_path = dest.with_suffix(".tmp.mp3")
                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(
                        asyncio.WindowsSelectorEventLoopPolicy()
                    )
                asyncio.run(generate_speech(translated, voice, tmp_path))
                tmp_path.replace(dest)

            status_cb(f"播放中：{triple}")
            with self._play_lock:
                pygame.mixer.music.stop()
                pygame.mixer.music.load(str(dest))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.05)
            status_cb(f"播放完成：{triple}")
        except Exception as exc:  # noqa: BLE001 — 顯示給使用者
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
            self.root.after(0, lambda e=exc: self.set_status(format_play_error(e)))
        finally:
            self._busy = False


def main() -> None:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到設定檔：{CONFIG_PATH}")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    root = tk.Tk()
    LanguagePracticeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
