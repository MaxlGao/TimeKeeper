import time
import tkinter as tk
from tkinter import messagebox, Scrollbar, Canvas, Frame, simpledialog
from collections import defaultdict
import win32gui
import win32api
import json
import os
import ctypes
import re
from datetime import datetime, timezone

# exe instructions
# cd "C:\{WHATEVER_PATH_TO_TIMEKEEPER}\TimeKeeper"
# pyinstaller TimeKeeper.py --onefile --noconsole --distpath . --clean
# Then clean up build files as desired

# TODO:
# Custom sorting of categories

# Config / Defaults (will be overwritten by settings file if present)
AFK_TIMEOUT = 60  # seconds; count at AFK if inactive for this long
SAVE_FILE = "window_times.json"
SETTINGS_FILE = "timekeeper_settings.json"
SAVE_TIME = 60  # seconds between saving to file
MIN_DISPLAY_TIME = 60  # Seconds threshold for displaying individual entries
TOP_PER_GROUP = 5  # # of entries to show per category
PURGE_THRESHOLD = 10  # seconds; purge entries below this when requested
TITLE_TRUNCATE = 50  # characters for display truncation

# Dictionary to store window time tracking
window_times = defaultdict(float)  # key: canonical_title, value: seconds
window_original_titles = {}  # canonical_title -> representative original title (for nicer display)
window_groups = defaultdict(dict)
current_window = None
last_switch_time = time.time()
AFK_time = 0.0
RESET_DATE = datetime.now(timezone.utc).isoformat()  # saved/loaded
last_input_time = time.time()

# Collapsed state for groups (in-memory)
collapsed_groups = defaultdict(lambda: False)

# Define grouping rules. Strictly an 'ends with' type deal.
GROUP_RULES = {
    "Office": [" - Word", " - PowerPoint", " - Excel", "- Adobe Acrobat Reader (64-bit)",
               " — LibreOffice Writer", " - Google Docs — Mozilla Firefox", " - Notepad", " - Obsidian"],
    "Work": [" - Visual Studio Code", ".pdf", " - Arizona State University Mail — Mozilla Firefox"],
    "Unimportant": [" - YouTube — Mozilla Firefox", "YouTube — Mozilla Firefox", "Bluesky — Mozilla Firefox"],
    "Social": [" - Discord", " - Slack"]
}

# Structure to query last input time
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_active_window_title():
    hwnd = win32gui.GetForegroundWindow()
    return win32gui.GetWindowText(hwnd)

def is_afk():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        elapsed_s = (win32api.GetTickCount() - lii.dwTime) / 1000.0
    else:
        elapsed_s = 0.0
    return elapsed_s > AFK_TIMEOUT

def format_time(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"

# Normalizer function for Obsidian dynamicness and unsaved markers
def normalize_title(raw_title: str) -> str:
    """Return a canonical title used as the key in window_times."""
    if not raw_title:
        return "Unknown"
    title = raw_title.strip()

    # Remove leading bullet or unsaved marker "● " or similar
    title = re.sub(r'^[\u25CF\u2022\*\s]+', '', title).strip()

    # Normalize Obsidian versions like "Obsidian v1.11.4" or "Obsidian 1.11.4"
    title = re.sub(r'\b(Obsidian)(?:\s*v?\d+(\.\d+)*)', r'\1', title, flags=re.IGNORECASE)

    # Remove version numbers for other apps that append versions e.g. "AppName v1.2.3" or "AppName 1.2.3"
    title = re.sub(r'\s+v?\d+(\.\d+){1,}(?:\S*)?$', '', title)
    title = title.strip()
    return title

def truncate_display(s: str) -> str:
    if not s:
        return s
    if len(s) <= TITLE_TRUNCATE:
        return s
    return s[:TITLE_TRUNCATE - 3].rstrip() + "..."

def classify_window_by_group(canonical_title: str):
    """Return (group, clean_title) for the canonical title. clean_title is truncated for display."""
    if not canonical_title:
        # ruh roh, oh well
        return "Unknown", "Unknown"

    # Check group suffix rules
    for group, suffixes in GROUP_RULES.items():
        for suffix in suffixes:
            if canonical_title.endswith(suffix):
                # remove the suffix from display name
                clean_title = canonical_title.replace(suffix, "").strip()
                # Truncate clean titles to TITLE_TRUNCATE chars for display
                clean_title_disp = truncate_display(clean_title or canonical_title)
                return group, clean_title_disp

    # Otherwise, it's in the Uncategorized Group
    return "Uncategorized", truncate_display(canonical_title)

def total_tracked_time():
    return sum(window_times.values())

def update_window_time():
    global current_window, last_switch_time, AFK_time

    raw_title = get_active_window_title()
    canonical = normalize_title(raw_title)
    now = time.time()

    # Determine group early for AFK logic
    group, clean_title = classify_window_by_group(canonical)

    if is_afk() and group != "Unimportant":
        # Unimportant implies watching a video, so AFK is irrelevant
        AFK_time += now - last_switch_time
        last_switch_time = now  # Reset tracking if AFK and not on an 'unimportant' window
    else:
        if current_window:
            window_times[current_window] += now - last_switch_time

        # When window changed, ensure canonical key exists in mapping
        if canonical and canonical != current_window:
            current_window = canonical
            if canonical not in window_original_titles or (raw_title and len(raw_title) < len(window_original_titles.get(canonical, ""))):
                window_original_titles[canonical] = raw_title or canonical
            # ensure key exists in window_times (so it appears in grouped lists even with 0 time)
            _ = window_times[current_window]  # default dict ensures key exists

        last_switch_time = now

    root.after(500, update_window_time)  # Update per 0.5 seconds

def purge_insignificant():
    """Remove entries below PURGE_THRESHOLD seconds after confirmation."""
    insignificant_keys = [k for k, v in window_times.items() if v < PURGE_THRESHOLD]
    if not insignificant_keys:
        messagebox.showinfo("Purge", f"No entries under {PURGE_THRESHOLD}s to purge.")
        return
    if not messagebox.askyesno("Confirm Purge", f"Purge {len(insignificant_keys)} entries below {PURGE_THRESHOLD}s? This cannot be undone."):
        return
    for k in insignificant_keys:
        window_times.pop(k, None)
        window_original_titles.pop(k, None)
    save_data()
    refresh_display()

def toggle_group(group_name: str):
    collapsed_groups[group_name] = not collapsed_groups[group_name]
    refresh_display()

def refresh_display():
    for widget in frame.winfo_children():
        widget.destroy()

    # Compute days since reset and average hours/day
    try:
        reset_dt = datetime.fromisoformat(RESET_DATE)
    except Exception:
        reset_dt = datetime.now(timezone.utc)
    delta_days = max(1.0, (datetime.now(timezone.utc) - reset_dt).total_seconds() / 86400.0)
    avg_hours_per_day = (total_tracked_time() / 3600.0) / delta_days

    # Count insignificant entries
    insignificant_count = sum(1 for _, v in window_times.items() if v < MIN_DISPLAY_TIME)

    # Split header into two lines: top = totals; bottom = reset + avg + insignificant count
    total_time_top = f"Active: {format_time(total_tracked_time())} | AFK: {format_time(AFK_time)}"
    total_time_bottom = f"Since {reset_dt.date()}, Active {avg_hours_per_day:.2f} hrs/day | Entries below {MIN_DISPLAY_TIME}s: {insignificant_count}"
    total_time_label_top.config(text=total_time_top)
    total_time_label_bottom.config(text=total_time_bottom)

    # Build grouped lists. Items should always have a group to them, including 'Uncategorized'
    grouped_items = defaultdict(list)  # group -> list of (canonical, display_title, duration)
    other_global_time = 0.0

    for canonical, duration in window_times.items():
        group, clean_title_disp = classify_window_by_group(canonical)
        # We'll use canonical and the display truncated version
        if duration < MIN_DISPLAY_TIME:
            other_global_time += duration
            continue
        grouped_items[group].append((canonical, clean_title_disp, duration))

    # Get current window's group (if any)
    current_group = classify_window_by_group(current_window)[0] if current_window else None

    # Sort grouped items
    for group in sorted(grouped_items.keys()):
        items = grouped_items[group]
        if not items:
            continue

        # compute group totals
        group_total = sum(d for (_, _, d) in items)
        items_sorted = sorted(items, key=lambda x: x[2], reverse=True)

        # chunk top N and others
        top = items_sorted[:TOP_PER_GROUP]
        others = items_sorted[TOP_PER_GROUP:]
        others_time = sum(d for (_, _, d) in others)
        others_canonicals = {c for (c, _, _) in others}

        # header shows group name and sum
        # determine header color: highlighted if current window belongs to this group
        is_current_in_group = (current_group == group)
        if is_current_in_group:
            # color depends on AFK state
            header_bg = "darkred" if is_afk() else "darkgreen"
        else:
            header_bg = "gray40"

        header_text = f"{group} — {format_time(group_total)}"
        header = tk.Label(frame, text=header_text, bg=header_bg, 
                          fg="white", font=("Arial", 12, "bold"), anchor='w')
        header.pack(fill='x', pady=2)
        # # bind click to toggle collapse
        # header.bind("<Button-1>", lambda e, g=group: toggle_group(g))

        if collapsed_groups[group]:
            collapsed_label = tk.Label(frame, text=f"[{len(items)} entries] (click to expand)", bg="gray25", 
                                       fg="white", font=("Arial", 10, "italic"), anchor='w')
            collapsed_label.pack(fill='x', padx=12, pady=1)
            # # also bind on this guy
            # collapsed_label.bind("<Button-1>", lambda e, g=group: toggle_group(g))
            continue

        # show top N items
        for canonical, title, duration in top:
            color = "gray30"
            if canonical == current_window:
                color = "darkred" if is_afk() else "darkgreen"
            label = tk.Label(frame, text=f"{title}: {format_time(duration)}", bg=color, 
                             fg="white", font=("Arial", 13), anchor='w', relief='solid', bd=1)
            label.pack(fill='x', pady=1, padx=10)

        # if there are grouped-other items, show a single aggregated line
        if others_time > 0:
            # Highlight other if the current window canonical is inside this group's 'others'
            is_current_in_others = (current_window in others_canonicals)
            color = "gray30" if not is_current_in_others else ("darkred" if is_afk() else "darkgreen")
            label = tk.Label(frame, text=f"[{group} Other]: {format_time(others_time)} ({len(others)} entries)", bg=color, fg="white", font=("Arial", 13, "italic"), anchor='w', relief='solid', bd=1)
            label.pack(fill='x', pady=1, padx=10)

    # Global other (insignificant entries)
    if other_global_time > 0:
        # Highlight global other if current_window is insignificant (duration < MIN_DISPLAY_TIME)
        current_duration = window_times.get(current_window, 0.0)
        is_current_insignificant = current_duration < MIN_DISPLAY_TIME and current_duration > 0
        color = "gray30" if not is_current_insignificant else ("darkred" if is_afk() else "darkgreen")
        label = tk.Label(frame, text=f"[Global Insignificant Other]: {format_time(other_global_time)} ({insignificant_count} entries)", bg=color, fg="white", font=("Arial", 13, "italic"), anchor='w', relief='solid', bd=1)
        label.pack(fill='x', pady=1)

    root.after(500, refresh_display)

def save_data():
    payload = {
        "window_times": dict(window_times),
        "AFK_time": AFK_time,
        "reset_date": RESET_DATE,
        "window_original_titles": window_original_titles
    }
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print("Error saving:", e)

    # also persist settings
    try:
        settings_payload = {
            "AFK_TIMEOUT": AFK_TIMEOUT,
            "SAVE_TIME": SAVE_TIME,
            "MIN_DISPLAY_TIME": MIN_DISPLAY_TIME,
            "TOP_PER_GROUP": TOP_PER_GROUP,
            "PURGE_THRESHOLD": PURGE_THRESHOLD,
            "TITLE_TRUNCATE": TITLE_TRUNCATE,
            "RESET_DATE": RESET_DATE
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as sf:
            json.dump(settings_payload, sf, indent=2)
    except Exception as e:
        print("Error saving settings:", e)

    root.after(int(SAVE_TIME*1000), save_data)

def load_data():
    global window_times, AFK_time, RESET_DATE, window_original_titles
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                wt = data.get("window_times", {})
                # ensure numeric values
                for k, v in wt.items():
                    window_times[k] = float(v)
                AFK_time = float(data.get("AFK_time", 0.0))
                RESET_DATE = data.get("reset_date", RESET_DATE)
                window_original_titles.update(data.get("window_original_titles", {}))
        except Exception as e:
            print("Error loading save file:", e)

    # load settings if present
    global AFK_TIMEOUT, SAVE_TIME, MIN_DISPLAY_TIME, TOP_PER_GROUP, PURGE_THRESHOLD, TITLE_TRUNCATE
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as sf:
                s = json.load(sf)
                AFK_TIMEOUT = int(s.get("AFK_TIMEOUT", AFK_TIMEOUT))
                SAVE_TIME = int(s.get("SAVE_TIME", SAVE_TIME))
                MIN_DISPLAY_TIME = int(s.get("MIN_DISPLAY_TIME", MIN_DISPLAY_TIME))
                TOP_PERGroup_val = s.get("TOP_PER_GROUP", TOP_PER_GROUP)
                TOP_PER_GROUP = int(TOP_PERGroup_val)
                PURGE_THRESHOLD = int(s.get("PURGE_THRESHOLD", PURGE_THRESHOLD))
                TITLE_TRUNCATE = int(s.get("TITLE_TRUNCATE", TITLE_TRUNCATE))
                # allow reset_date override if present
                RESET_DATE = s.get("RESET_DATE", RESET_DATE)
        except Exception as e:
            print("Error loading settings:", e)

def open_file_manager():
    os.system(f'explorer {os.path.abspath(SAVE_FILE)}')

def clear_data():
    if messagebox.askyesno("Confirm", "Are you sure you want to clear all tracked data? This will reset the tracked history and reset date."):
        global window_times, AFK_time, RESET_DATE, window_original_titles
        window_times.clear()
        window_original_titles.clear()
        AFK_time = 0.0
        RESET_DATE = datetime.now(timezone.utc).isoformat()
        save_data()
        refresh_display()

def open_settings_dialog():
    """Open a simple settings dialog allowing edits to numeric constants."""
    global AFK_TIMEOUT, SAVE_TIME, MIN_DISPLAY_TIME, TOP_PER_GROUP, PURGE_THRESHOLD, TITLE_TRUNCATE

    dlg = tk.Toplevel(root)
    dlg.title("Settings")
    dlg.geometry("360x320")
    dlg.transient(root)
    dlg.grab_set()

    entries = {}

    def add_row(label_text, var_name, row, current_value):
        lbl = tk.Label(dlg, text=label_text)
        lbl.grid(row=row, column=0, sticky='w', padx=8, pady=6)
        ent = tk.Entry(dlg)
        ent.insert(0, str(current_value))
        ent.grid(row=row, column=1, padx=8, pady=6)
        entries[var_name] = ent

    add_row("AFK timeout (s):", "AFK_TIMEOUT", 0, AFK_TIMEOUT)
    add_row("Save interval (s):", "SAVE_TIME", 1, SAVE_TIME)
    add_row("Min display time (s):", "MIN_DISPLAY_TIME", 2, MIN_DISPLAY_TIME)
    add_row("Top per group:", "TOP_PER_GROUP", 3, TOP_PER_GROUP)
    add_row("Purge threshold (s):", "PURGE_THRESHOLD", 4, PURGE_THRESHOLD)
    add_row("Title truncate (chars):", "TITLE_TRUNCATE", 5, TITLE_TRUNCATE)

    def on_save():
        global AFK_TIMEOUT, SAVE_TIME, MIN_DISPLAY_TIME, TOP_PER_GROUP, PURGE_THRESHOLD, TITLE_TRUNCATE
        nonlocal entries
        try:
            AFK_TIMEOUT = int(entries["AFK_TIMEOUT"].get())
            SAVE_TIME = int(entries["SAVE_TIME"].get())
            MIN_DISPLAY_TIME = int(entries["MIN_DISPLAY_TIME"].get())
            TOP_PER_GROUP = int(entries["TOP_PER_GROUP"].get())
            PURGE_THRESHOLD = int(entries["PURGE_THRESHOLD"].get())
            TITLE_TRUNCATE = int(entries["TITLE_TRUNCATE"].get())
        except ValueError:
            messagebox.showerror("Invalid", "Please enter valid integer values.")
            return
        save_data()
        dlg.destroy()
        refresh_display()

    save_btn = tk.Button(dlg, text="Save", command=on_save)
    save_btn.grid(row=6, column=0, padx=8, pady=12)
    cancel_btn = tk.Button(dlg, text="Cancel", command=dlg.destroy)
    cancel_btn.grid(row=6, column=1, padx=8, pady=12)

# Initialize GUI
root = tk.Tk()
root.title("Window Focus Tracker")
root.geometry("480x600")
root.configure(bg="gray20")

# Toolbar
toolbar = tk.Frame(root, bg="gray30")
toolbar.pack(fill='x')
file_button = tk.Button(toolbar, text="Open Save File", command=open_file_manager)
file_button.pack(side='left', padx=5, pady=5)
purge_button = tk.Button(toolbar, text=f"Purge...", command=purge_insignificant)
purge_button.pack(side='left', padx=5, pady=5)
settings_button = tk.Button(toolbar, text="Settings", command=open_settings_dialog)
settings_button.pack(side='left', padx=5, pady=5)
clear_button = tk.Button(toolbar, text="Clear Data", command=clear_data)
clear_button.pack(side='right', padx=5, pady=5)

# Create Header Objects (two lines)
total_time_label_top = tk.Label(root, text="", bg="gray30", fg="white", font=("Arial", 14))
total_time_label_top.pack(fill='x')
total_time_label_bottom = tk.Label(root, text="", bg="gray30", fg="white", font=("Arial", 11))
total_time_label_bottom.pack(fill='x')

# Create a frame with a canvas and scrollbar
canvas = Canvas(root, bg="gray20")
scrollbar = Scrollbar(root, orient="vertical", command=canvas.yview)
frame = Frame(canvas, bg="gray20")

frame.bind(
    "<Configure>",
    lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")
    )
)

canvas.create_window((0, 0), window=frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

# Enable scrolling with mouse wheel
def _on_mouse_wheel(event):
    canvas.yview_scroll(-1 * (event.delta // 120), "units")

canvas.bind_all("<MouseWheel>", _on_mouse_wheel)

load_data()
update_window_time()
refresh_display()
save_data()

root.mainloop()
