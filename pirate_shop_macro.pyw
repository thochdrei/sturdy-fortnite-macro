"""
PIRATE SHOP MACRO  v1.2  -  windowed app (no console)
Fortnite auto-ready / return-to-lobby loop. Works on any graphics card.

How it knows you're out (no NVIDIA needed):
  * "Return to Lobby" button  -> your whole team is eliminated   (click it)
  * "Eliminated by" text      -> you died, teammates still alive (Esc > menu > leave)

Cycle:
  1. Find READY and click it.
  2. Every scan interval, look for the two screens above.
  3. Leave the match, count the elimination, repeat.

Mouse movement is human-like: a curved sine-wave path with natural
acceleration, a random click point inside each button, and a random pause
before every click.

First time? Open the Setup page: the app captures the button pictures for you.
Everything else (settings, stats, hotkeys) lives in the app window too.

Double-click this .pyw file to open the app.
"""

import ctypes
import json
import math
import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import keyboard
import pyautogui

try:
    import winsound
except Exception:  # not on Windows
    winsound = None

APP_VERSION = "1.2"

# ---------------- fixed settings (edit here if you really want) ----------------
READY_RETRY = 2            # seconds between searches for the ready button
STEP_TIMEOUT = 10          # seconds to keep looking for menu / leave buttons
POLL_INTERVAL = 1.0        # seconds between searches while hunting for menu/leave
REMEMBER_PAD = 40          # pixels of padding around a remembered button location
CAPTURE_KEY = "f9"         # hotkey used by the screenshot wizard

# Search only where each button appears: (left, top, width, height). None = whole screen.
REGIONS = {"ready": None, "eliminated": None, "return_lobby": None,
           "menu": None, "leave": None, "failsafe": None}

# ---------------- settings that can be changed inside the app ----------------
SPEEDS = {"Slow": 1.5, "Normal": 1.0, "Fast": 0.65}      # cursor travel-time multiplier
STOP_KEY_CHOICES = ["f6", "f7", "f8", "f10"]
THEME_NAMES = ["Gold", "Ocean", "Emerald", "Violet", "Rose"]
CHOICES = {"mouse_speed": list(SPEEDS), "stop_key": STOP_KEY_CHOICES, "theme": THEME_NAMES}

DEFAULTS = {
    # detection
    "use_return_lobby": True,  # watch for the Return to Lobby button (team wiped)
    "use_eliminated": True,    # watch for the "Eliminated by" text (teammates alive)
    "check_interval": 25,      # seconds between scans
    "scan_jitter": 0,          # random extra seconds added to each scan interval
    "confidence": 0.80,        # image match strictness (0-1)
    # mouse
    "human_mouse": True,       # curved sine-wave cursor movement
    "wave_amplitude": 14,      # max sideways swing of the curve, in pixels
    "mouse_speed": "Normal",
    "click_delay_min": 0.35,   # random pause before each click (seconds)
    "click_delay_max": 0.90,
    # timing
    "start_delay": 5,          # seconds after pressing Start before it begins
    "ready_timeout": 15,       # give up looking for Ready after this long
    "lobby_wait": 10,          # seconds to wait in the lobby after leaving
    "step_delay": 1.0,         # pause between Esc / menu / leave
    "failsafe_delay": 5,       # seconds after the extra popup click before checking Ready
    # app
    "stop_after_deaths": 0,    # 0 = never
    "stop_after_minutes": 0,   # 0 = never
    "sound": True,
    "always_on_top": False,
    "stop_key": "f8",
    "theme": "Gold",
}
# ------------------------------------------------------------------------

if getattr(sys, "frozen", False):          # running as a built .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE_DIR, "images")
MEMORY_FILE = os.path.join(BASE_DIR, "locations.json")   # remembered button spots
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")  # in-app settings
STATS_FILE = os.path.join(BASE_DIR, "stats.json")        # all-time stats

IMAGE_NAMES = {
    "ready": "ready.png", "eliminated": "eliminated.png",
    "return_lobby": "return_lobby.png", "menu": "menu.png",
    "leave": "leave.png", "failsafe": "failsafe.png",
}
# key, title, what to capture, optional?, setting that turns it on/off
STEPS = [
    ("ready", "Ready / Play button",
     "Go to the lobby and capture the button you click to start a match.", False, None),
    ("eliminated", "\u201CEliminated by\u201D text",
     "Get eliminated while a teammate is alive and capture the \u201CELIMINATED BY\u201D text.",
     False, "use_eliminated"),
    ("menu", "Menu button",
     "Still in the match? Press Esc and capture the menu button you would click next.",
     False, "use_eliminated"),
    ("leave", "Leave Match button",
     "Click the menu button, then capture the Leave Match button.", False, "use_eliminated"),
    ("return_lobby", "\u201CReturn to Lobby\u201D button",
     "When your whole team is out, capture the Return to Lobby button.", False, "use_return_lobby"),
    ("failsafe", "Extra popup  (optional)",
     "Optional: any popup you want clicked automatically during a match.", True, None),
]
STEP_BY_KEY = {s[0]: s for s in STEPS}

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0


# ---------------- small helpers ----------------
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_time(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_short(seconds):
    if seconds is None:
        return "--"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def fmt_ago(ts):
    if not ts:
        return "--"
    d = time.time() - ts
    if d < 5:
        return "just now"
    return fmt_short(d) + " ago"


def fmt_date(ts):
    return time.strftime("%b %d, %Y", time.localtime(ts)) if ts else "--"


# ---------------- settings & lifetime stats ----------------
def valid_setting(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, str):
        return value in CHOICES.get(key, [])
    return isinstance(value, (int, float)) and not isinstance(value, bool)


CFG = dict(DEFAULTS)
for _k, _v in load_json(SETTINGS_FILE, {}).items():
    if _k in DEFAULTS and valid_setting(_k, _v):
        CFG[_k] = int(_v) if isinstance(DEFAULTS[_k], int) and not isinstance(DEFAULTS[_k], bool) else _v

LIFE = {"sessions": 0, "runtime": 0.0, "deaths": 0, "wipes": 0, "elims": 0,
        "scans": 0, "clicks": 0, "fastest": None, "longest": None,
        "first": None, "last": None, "best_session": 0, "longest_session": 0.0,
        "hours": [0] * 24, "daily": {}}
_loaded = load_json(STATS_FILE, {})
for _k in LIFE:
    if _k in _loaded and type(_loaded[_k]) is type(LIFE[_k]) or (
            _k in _loaded and LIFE[_k] is None):
        LIFE[_k] = _loaded[_k]
if len(LIFE["hours"]) != 24:
    LIFE["hours"] = [0] * 24

# ---------------- shared state ----------------
STATE = {
    "running": False, "start": 0.0, "end": None, "finalized": True,
    "deaths": 0, "wipes": 0, "elims": 0,
    "status": "Idle", "kind": "idle",
    "next_check": None, "scan_total": 25,
    "log": [], "log_seq": 0,
    "matches": [],            # (seconds from ready to elimination, timestamp, "wipe"/"elim")
    "match_start": None, "in_match": False, "last_death": None,
    "scans": 0, "clicks": 0, "readyups": 0, "ready_missed": 0,
    "failsafe": 0, "leave_fail": 0, "errors": 0,
}
STOP = threading.Event()
NOTIFY = []                   # (text, kind) toasts queued for the window


def reset_session():
    STATE.update(
        running=True, start=time.time(), end=None, finalized=False,
        deaths=0, wipes=0, elims=0, next_check=None, matches=[], match_start=None,
        in_match=False, last_death=None, scans=0, clicks=0, readyups=0,
        ready_missed=0, failsafe=0, leave_fail=0, errors=0,
    )
    LIFE["sessions"] += 1
    LIFE["first"] = LIFE["first"] or time.time()


def finalize_session():
    """Fold this session into the all-time stats (safe to call twice)."""
    if STATE["finalized"]:
        return
    STATE["finalized"] = True
    end = STATE["end"] or time.time()
    elapsed = max(0.0, end - STATE["start"])
    LIFE["runtime"] += elapsed
    LIFE["deaths"] += STATE["deaths"]
    LIFE["wipes"] += STATE["wipes"]
    LIFE["elims"] += STATE["elims"]
    LIFE["scans"] += STATE["scans"]
    LIFE["clicks"] += STATE["clicks"]
    LIFE["best_session"] = max(LIFE["best_session"], STATE["deaths"])
    LIFE["longest_session"] = max(LIFE["longest_session"], elapsed)
    for m in STATE["matches"]:
        if LIFE["fastest"] is None or m[0] < LIFE["fastest"]:
            LIFE["fastest"] = m[0]
        if LIFE["longest"] is None or m[0] > LIFE["longest"]:
            LIFE["longest"] = m[0]
    LIFE["last"] = time.time()
    save_json(STATS_FILE, LIFE)


def session_elapsed(now=None):
    now = now or time.time()
    return ((STATE["end"] or now) - STATE["start"]) if STATE["start"] else 0.0


def life_view(now=None):
    """All-time numbers including the session that is running right now."""
    now = now or time.time()
    live = not STATE["finalized"]
    elapsed = session_elapsed(now) if live else 0.0
    sdurs = [m[0] for m in STATE["matches"]] if live else []
    fast = [x for x in [LIFE["fastest"]] + sdurs if x is not None]
    longs = [x for x in [LIFE["longest"]] + sdurs if x is not None]
    runtime = LIFE["runtime"] + elapsed
    deaths = LIFE["deaths"] + (STATE["deaths"] if live else 0)
    return {
        "sessions": LIFE["sessions"], "runtime": runtime, "deaths": deaths,
        "wipes": LIFE["wipes"] + (STATE["wipes"] if live else 0),
        "elims": LIFE["elims"] + (STATE["elims"] if live else 0),
        "scans": LIFE["scans"] + (STATE["scans"] if live else 0),
        "clicks": LIFE["clicks"] + (STATE["clicks"] if live else 0),
        "fastest": min(fast) if fast else None,
        "longest": max(longs) if longs else None,
        "best_session": max(LIFE["best_session"], STATE["deaths"] if live else 0),
        "longest_session": max(LIFE["longest_session"], elapsed),
        "rate": deaths / (runtime / 3600) if runtime >= 60 and deaths else None,
        "first": LIFE["first"], "last": LIFE["last"], "live": live,
    }


# ---------------- ranks & achievements ----------------
RANKS = [(0, "Deckhand"), (10, "Sailor"), (50, "Quartermaster"), (100, "First Mate"),
         (250, "Captain"), (500, "Admiral"), (1000, "Pirate King")]


def rank_info(total):
    """-> (index, name, next_goal or None, progress 0-1, next_name or None)"""
    idx = 0
    for i, (need, _) in enumerate(RANKS):
        if total >= need:
            idx = i
    if idx == len(RANKS) - 1:
        return idx, RANKS[idx][1], None, 1.0, None
    cur, nxt = RANKS[idx][0], RANKS[idx + 1][0]
    return idx, RANKS[idx][1], nxt, (total - cur) / (nxt - cur), RANKS[idx + 1][1]


# icon, title, description, metric, goal
ACHIEVEMENTS = [
    ("\u2693", "First Voyage", "1 elimination", "deaths", 1),
    ("\u2605", "Sea Legs", "10 eliminations", "deaths", 10),
    ("\u2694", "Plunderer", "50 eliminations", "deaths", 50),
    ("\u2620", "Centurion", "100 eliminations", "deaths", 100),
    ("\u265B", "Kraken Slayer", "500 eliminations", "deaths", 500),
    ("\u2726", "Seven Seas Legend", "1000 eliminations", "deaths", 1000),
    ("\u2600", "Steady Sailor", "1 hour of run time", "runtime", 3600),
    ("\u263E", "Night Watch", "5 hours of run time", "runtime", 5 * 3600),
    ("\u23F3", "Ghost Ship", "24 hours of run time", "runtime", 24 * 3600),
    ("\u265E", "Hot Streak", "25 in one session", "best_session", 25),
]


def set_status(text, kind="warn"):
    STATE["status"] = text
    STATE["kind"] = kind


def log(text):
    STATE["log_seq"] += 1
    STATE["log"].append((STATE["log_seq"], f"[{time.strftime('%H:%M:%S')}] {text}"))
    del STATE["log"][:-300]


def notify(text, kind="info"):
    NOTIFY.append((text, kind))


def beep(kind="ok"):
    if not CFG.get("sound") or winsound is None:
        return
    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if kind == "done" else winsound.MB_OK)
    except Exception:
        pass


def stopped():
    return STOP.is_set()


def sleep_interruptible(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if stopped():
            return False
        time.sleep(0.05)
    return True


def img(key):
    return os.path.join(IMG_DIR, IMAGE_NAMES[key])


def step_enabled(key):
    toggle = STEP_BY_KEY[key][4]
    return True if toggle is None else bool(CFG[toggle])


def required_keys():
    keys = ["ready"]
    if CFG["use_eliminated"]:
        keys += ["eliminated", "menu", "leave"]
    if CFG["use_return_lobby"]:
        keys += ["return_lobby"]
    return keys


def missing_images():
    return [IMAGE_NAMES[k] for k in required_keys() if not os.path.exists(img(k))]


def limit_reason():
    d, m = int(CFG["stop_after_deaths"]), int(CFG["stop_after_minutes"])
    if d > 0 and STATE["deaths"] >= d:
        return f"Reached {d} eliminations"
    if m > 0 and session_elapsed() >= m * 60:
        return f"Reached {m} minutes"
    return None


# ---------------- pure helpers used by the screenshot wizard ----------------
def scale_box(x0, y0, x1, y1, sx, sy, max_w, max_h):
    """Window-drag coordinates -> a sorted, clamped box in screenshot pixels."""
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return (int(clamp(left * sx, 0, max_w)), int(clamp(top * sy, 0, max_h)),
            int(clamp(right * sx, 0, max_w)), int(clamp(bottom * sy, 0, max_h)))


def save_crop(shot, box, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shot.crop(box).convert("RGB").save(path)


# ---------------- remembered locations ----------------
def load_memory():
    data = load_json(MEMORY_FILE, {})
    try:
        if data.get("screen") == list(pyautogui.size()):
            return {k: tuple(v) for k, v in data.get("spots", {}).items()}
    except Exception:
        pass
    return {}


def save_memory():
    save_json(MEMORY_FILE, {"screen": list(pyautogui.size()),
                            "spots": {k: list(v) for k, v in MEMORY.items()}})


MEMORY = load_memory()


def remember(key, box):
    sw, sh = pyautogui.size()
    left = max(0, int(box.left) - REMEMBER_PAD)
    top = max(0, int(box.top) - REMEMBER_PAD)
    right = min(sw, int(box.left + box.width) + REMEMBER_PAD)
    bottom = min(sh, int(box.top + box.height) + REMEMBER_PAD)
    region = (left, top, right - left, bottom - top)
    if MEMORY.get(key) != region:
        first = key not in MEMORY
        MEMORY[key] = region
        save_memory()
        log(f"{'Remembered' if first else 'Updated'} {key} location")


# ---------------- finding things on screen ----------------
def locate(key, region):
    try:
        return pyautogui.locateOnScreen(
            img(key), confidence=CFG["confidence"], grayscale=True, region=region)
    except pyautogui.ImageNotFoundException:
        return None


def find(key):
    """Return the Box where an image was found (remembered spot first), else None."""
    if not os.path.exists(img(key)):
        return None
    spot = MEMORY.get(key)
    if spot:
        box = locate(key, spot)
        if box:
            return box
    box = locate(key, REGIONS[key])
    if box:
        remember(key, box)
        return box
    return None


# ---------------- human-like mouse ----------------
def _min_jerk(t):
    """Smooth ease-in/ease-out: slow start, fast middle, slow finish."""
    return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5


def _glide(x0, y0, x1, y1, duration, amp, cycles):
    """Move from (x0,y0) to (x1,y1) along a sine-wave curve. False if stopped."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1:
        return True
    nx, ny = -dy / dist, dx / dist              # unit vector sideways to the path
    sw, sh = pyautogui.size()
    steps = max(20, int(duration * 100))
    for i in range(1, steps + 1):
        if stopped():
            return False
        e = _min_jerk(i / steps)
        wave = amp * math.sin(cycles * 2 * math.pi * e) * math.sin(math.pi * e)
        x = clamp(int(x0 + dx * e + nx * wave), 0, sw - 1)
        y = clamp(int(y0 + dy * e + ny * wave), 0, sh - 1)
        pyautogui.moveTo(x, y, _pause=False)
        time.sleep(duration / steps * random.uniform(0.8, 1.2))
    return True


def human_move(x, y):
    x0, y0 = pyautogui.position()
    dist = math.hypot(x - x0, y - y0)
    if dist < 3:
        return True
    speed = SPEEDS.get(CFG["mouse_speed"], 1.0)
    duration = min(1.3 * speed, (0.30 + dist / 1500 + random.uniform(0.05, 0.30)) * speed)
    amp = (min(float(CFG["wave_amplitude"]), dist * 0.09)
           * random.uniform(0.55, 1.0) * random.choice((-1, 1)))
    cycles = random.uniform(1.0, 2.4)
    if dist > 300 and random.random() < 0.5:   # slight overshoot, then settle back
        ux, uy = (x - x0) / dist, (y - y0) / dist
        ox = x + ux * random.uniform(4, 11)
        oy = y + uy * random.uniform(4, 11)
        if not _glide(x0, y0, ox, oy, duration, amp, cycles):
            return False
        return _glide(ox, oy, x, y, random.uniform(0.08, 0.18), 0, 1)
    return _glide(x0, y0, x, y, duration, amp, cycles)


def click(box):
    """Move to a random spot inside the box, pause (configurable), click."""
    tx = box.left + box.width * random.uniform(0.25, 0.75)
    ty = box.top + box.height * random.uniform(0.25, 0.75)
    if CFG["human_mouse"]:
        if not human_move(tx, ty):
            return False
    else:
        pyautogui.moveTo(tx, ty, duration=0.2, _pause=False)
    lo, hi = sorted((float(CFG["click_delay_min"]), float(CFG["click_delay_max"])))
    if not sleep_interruptible(random.uniform(lo, hi)):
        return False
    pyautogui.mouseDown(_pause=False)
    time.sleep(random.uniform(0.04, 0.12))
    pyautogui.mouseUp(_pause=False)
    STATE["clicks"] += 1
    return True


def press_key(name):
    pyautogui.keyDown(name, _pause=False)
    time.sleep(random.uniform(0.04, 0.11))
    pyautogui.keyUp(name, _pause=False)


# ---------------- the macro ----------------
def click_when_found(key, label):
    end = time.time() + STEP_TIMEOUT
    while time.time() < end:
        if stopped():
            return False
        box = find(key)
        if box:
            if not click(box):
                return False
            log(f"Clicked {label}")
            return True
        if not sleep_interruptible(POLL_INTERVAL):
            return False
    log(f"Couldn't find {label}")
    return False


def ready_up():
    """Click Ready if found within the timeout. Returns False only if stopped."""
    set_status("Looking for the ready button", "warn")
    end = time.time() + float(CFG["ready_timeout"])
    while time.time() < end:
        if stopped():
            return False
        box = find("ready")
        if box:
            if not click(box):
                return False
            STATE["readyups"] += 1
            log("Readied up")
            return True
        if not sleep_interruptible(READY_RETRY):
            return False
    STATE["ready_missed"] += 1
    log("Ready not found, skipping to alive check")
    return True


def record_death(kind, duration):
    now = time.time()
    STATE["deaths"] += 1
    STATE["wipes" if kind == "wipe" else "elims"] += 1
    STATE["matches"].append((duration, now, kind))
    STATE["last_death"] = now
    STATE["in_match"] = False
    lt = time.localtime(now)
    LIFE["hours"][lt.tm_hour] += 1
    day = time.strftime("%Y-%m-%d", lt)
    LIFE["daily"][day] = LIFE["daily"].get(day, 0) + 1
    for old in sorted(LIFE["daily"])[:-90]:
        del LIFE["daily"][old]
    save_json(STATS_FILE, LIFE)
    label = "Team wiped" if kind == "wipe" else "Eliminated"
    log(f"{label} (#{STATE['deaths']}) after {fmt_short(duration)}")
    notify(f"{label}  \u2022  #{STATE['deaths']}  \u2022  {fmt_short(duration)} match", "bad")
    beep("ok")


def wait_until_dead():
    """Returns "wipe", "elim", "limit", or None if stopped."""
    STATE["match_start"] = time.time()
    STATE["in_match"] = True
    set_status("Watching for elimination", "ok")
    while True:
        interval = float(CFG["check_interval"]) + random.uniform(0, float(CFG["scan_jitter"]))
        STATE["scan_total"] = interval
        STATE["next_check"] = time.time() + interval
        if not sleep_interruptible(interval):
            STATE["next_check"] = None
            return None
        STATE["next_check"] = None
        set_status("Scanning the screen", "scan")
        STATE["scans"] += 1
        kind = None
        if CFG["use_return_lobby"] and find("return_lobby"):
            kind = "wipe"
        elif CFG["use_eliminated"] and find("eliminated"):
            kind = "elim"
        if kind:
            record_death(kind, time.time() - STATE["match_start"])
            return kind
        box = find("failsafe")
        if box:
            if not click(box):
                return None
            STATE["failsafe"] += 1
            delay = float(CFG["failsafe_delay"])
            log("Extra popup clicked")
            set_status(f"Popup clicked, checking ready in {int(delay)}s", "leave")
            if not sleep_interruptible(delay):
                return None
            ready_box = find("ready")
            if ready_box:
                if not click(ready_box):
                    return None
                log("Ready found after popup - clicked")
            else:
                log("Ready not found after popup")
        else:
            log("Still alive")
        if limit_reason():
            return "limit"
        set_status("Watching for elimination", "ok")


def leave_match():
    """Teammates alive: Esc > menu > leave."""
    set_status("Leaving the match", "leave")
    press_key("esc")
    log("Pressed Esc")
    if not sleep_interruptible(float(CFG["step_delay"])):
        return False
    if not click_when_found("menu", "menu"):
        STATE["leave_fail"] += 1
        return not stopped()
    if not sleep_interruptible(float(CFG["step_delay"])):
        return False
    if not click_when_found("leave", "leave"):
        STATE["leave_fail"] += 1
        return not stopped()
    set_status("Back in the lobby", "warn")
    return sleep_interruptible(float(CFG["lobby_wait"]))


def return_to_lobby():
    """Whole team out: just click Return to Lobby."""
    set_status("Returning to the lobby", "leave")
    if not click_when_found("return_lobby", "return to lobby"):
        STATE["leave_fail"] += 1
        return not stopped()
    set_status("Back in the lobby", "warn")
    return sleep_interruptible(float(CFG["lobby_wait"]))


def worker():
    try:
        set_status(f"Starting in {int(CFG['start_delay'])}s - click into Fortnite", "warn")
        if not sleep_interruptible(float(CFG["start_delay"])):
            return
        log("Macro started")
        while not stopped():
            reason = limit_reason()
            if reason:
                log(f"{reason} - stopping")
                notify(f"{reason} - macro stopped", "good")
                beep("done")
                break
            if not ready_up():
                break
            result = wait_until_dead()
            if result is None:
                break
            if result == "limit":
                reason = limit_reason() or "Limit reached"
                log(f"{reason} - stopping")
                notify(f"{reason} - macro stopped", "good")
                beep("done")
                break
            ok = return_to_lobby() if result == "wipe" else leave_match()
            if not ok:
                break
    except pyautogui.FailSafeException:
        log("Stopped (mouse in screen corner)")
    except Exception as e:  # keep the window alive and show what went wrong
        STATE["errors"] += 1
        log(f"Error: {e}")
    finally:
        STATE["running"] = False
        STATE["in_match"] = False
        STATE["next_check"] = None
        STATE["end"] = time.time()
        set_status("Stopped", "stop")
        log("Macro stopped")
        finalize_session()


# =====================================================================
#                              THE WINDOW
# =====================================================================
THEMES = {
    "Gold": dict(BG="#120b07", SIDEBAR="#0d0704", PANEL="#1d130b", PANEL2="#2a1a0d",
                 EDGE="#4d3216", AMBER="#e49c24", GOLD="#e4b454", CREAM="#fce4b4",
                 DIM="#a58a62", FAINT="#6f5c3e"),
    "Ocean": dict(BG="#07101a", SIDEBAR="#040a12", PANEL="#0d1b2a", PANEL2="#13273b",
                  EDGE="#1f4160", AMBER="#2bb3e6", GOLD="#6fd0f2", CREAM="#d6f1ff",
                  DIM="#7fa3bd", FAINT="#4b6a82"),
    "Emerald": dict(BG="#08120d", SIDEBAR="#040b07", PANEL="#0f1f17", PANEL2="#17301f",
                    EDGE="#24543a", AMBER="#2fd47e", GOLD="#7de8ab", CREAM="#d9ffe9",
                    DIM="#84b59b", FAINT="#4f7a63"),
    "Violet": dict(BG="#100a1a", SIDEBAR="#0a0612", PANEL="#1a1229", PANEL2="#271a3d",
                   EDGE="#473066", AMBER="#a66bff", GOLD="#c9a3ff", CREAM="#efe4ff",
                   DIM="#9f8cc2", FAINT="#67578a"),
    "Rose": dict(BG="#1a0a10", SIDEBAR="#12060a", PANEL="#2a1219", PANEL2="#3a1a24",
                 EDGE="#6a2c3d", AMBER="#ff5f8f", GOLD="#ff9ab8", CREAM="#ffe3ec",
                 DIM="#c38c9c", FAINT="#8a5a68"),
}
_T = THEMES.get(CFG["theme"], THEMES["Gold"])
BG, SIDEBAR, PANEL, PANEL2, EDGE = _T["BG"], _T["SIDEBAR"], _T["PANEL"], _T["PANEL2"], _T["EDGE"]
AMBER, GOLD, CREAM, DIM, FAINT = _T["AMBER"], _T["GOLD"], _T["CREAM"], _T["DIM"], _T["FAINT"]
GOOD, BAD, INFO, ORANGE = "#9be37f", "#ee6a4f", "#7fd3f0", "#f0a35a"
KIND_COLORS = {"idle": DIM, "ok": GOOD, "warn": GOLD, "scan": INFO, "leave": ORANGE, "stop": BAD}
KIND_TITLES = {
    "idle": "Ready to start", "ok": "In match", "warn": "Working",
    "scan": "Scanning", "leave": "Leaving match", "stop": "Stopped",
}
TOAST_COLORS = {"info": AMBER, "good": GOOD, "bad": BAD, "warn": ORANGE}
FONT = {"ui": "Segoe UI", "num": "Segoe UI", "title": "Segoe UI", "mono": "Consolas"}

WIN_W, WIN_H = 1020, 690
SIDE_W = 214
PAGE_PAD = 26
CONTENT_W = WIN_W - SIDE_W - 2 * PAGE_PAD     # 754


def init_fonts(root):
    try:
        fams = set(tkfont.families(root))
    except Exception:
        return
    if "Bahnschrift" in fams:
        FONT["num"] = "Bahnschrift"
    if "Segoe UI Black" in fams:
        FONT["title"] = "Segoe UI Black"
    if "Cascadia Mono" in fams:
        FONT["mono"] = "Cascadia Mono"


def mix(c1, c2, t):
    t = clamp(t, 0.0, 1.0)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def find_logo():
    for folder in (BASE_DIR, getattr(sys, "_MEIPASS", None)):
        if folder:
            path = os.path.join(folder, "logo.png")
            if os.path.exists(path):
                return path
    return None


def load_logo(size):
    path = find_logo()
    if not path:
        return None
    try:
        from PIL import Image, ImageTk
        resample = getattr(Image, "Resampling", Image).LANCZOS
        im = Image.open(path).convert("RGBA").resize((size, size), resample)
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


def make_thumb(path, w, h):
    try:
        from PIL import Image, ImageTk
        im = Image.open(path).convert("RGBA")
        im.thumbnail((w, h))
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


def make_card(parent, w, h, r=18, fill=None, bg=None):
    c = tk.Canvas(parent, width=w, height=h, bg=bg or BG, highlightthickness=0, bd=0)
    rrect(c, 1, 1, w - 1, h - 1, r, fill=fill or PANEL, outline=EDGE)
    return c


def card_title(c, w, text, right=""):
    c.create_text(22, 24, text=text.upper(), anchor="w", fill=AMBER,
                  font=(FONT["ui"], 8, "bold"))
    c.create_line(22, 38, 50, 38, fill=AMBER, width=2)
    if right:
        c.create_text(w - 22, 24, text=right, anchor="e", fill=FAINT,
                      font=(FONT["ui"], 8))


def gradient_bar(c, x0, x1, y, color_a, color_b, seg=6, width=2):
    n = max(1, (x1 - x0) // seg)
    for i in range(n):
        c.create_line(x0 + i * seg, y, x0 + (i + 1) * seg, y,
                      fill=mix(color_a, color_b, i / n), width=width)


# ---------------- custom widgets ----------------
class RoundButton(tk.Canvas):
    def __init__(self, parent, text, command, width=206, height=48, bg=None,
                 hover=None, fg=None, canvas_bg=None, radius=16, size=11):
        super().__init__(parent, width=width, height=height, bg=canvas_bg or BG,
                         highlightthickness=0, bd=0)
        self.cmd, self.bw, self.bh, self.label = command, width, height, text
        self.colors = (bg or AMBER, hover or GOLD, fg or BG)
        self.radius, self.fsize = radius, size
        self.enabled, self.hovering = True, False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.draw()

    def draw(self):
        self.delete("all")
        bg, hover, fg = self.colors
        if self.enabled:
            fill = hover if self.hovering else bg
            edge, tcol = mix(fill, "#ffffff", 0.25), fg
        else:
            fill, edge, tcol = PANEL2, EDGE, FAINT
        rrect(self, 2, 2, self.bw - 2, self.bh - 2, self.radius, fill=fill, outline=edge)
        self.create_text(self.bw / 2, self.bh / 2, text=self.label, fill=tcol,
                         font=(FONT["ui"], self.fsize, "bold"))
        self.config(cursor="hand2" if self.enabled else "arrow")

    def _hover(self, on):
        self.hovering = on
        self.draw()

    def _click(self, _):
        if self.enabled:
            self.cmd()

    def set_enabled(self, on):
        if on != self.enabled:
            self.enabled = on
            self.hovering = False
            self.draw()

    def set_text(self, text):
        if text != self.label:
            self.label = text
            self.draw()


class NavButton(tk.Canvas):
    def __init__(self, parent, icon, text, command, width=190, height=42):
        super().__init__(parent, width=width, height=height, bg=SIDEBAR,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.nw, self.nh, self.icon, self.label, self.cmd = width, height, icon, text, command
        self.active, self.hovering, self.badge = False, False, ""
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.cmd())
        self.draw()

    def draw(self):
        self.delete("all")
        fg, ifg = DIM, FAINT
        if self.active:
            rrect(self, 1, 1, self.nw - 1, self.nh - 1, 12, fill=PANEL2, outline=EDGE)
            self.create_rectangle(0, 10, 4, self.nh - 10, fill=AMBER, outline="")
            fg, ifg = CREAM, AMBER
        elif self.hovering:
            rrect(self, 1, 1, self.nw - 1, self.nh - 1, 12,
                  fill=mix(SIDEBAR, PANEL2, 0.55), outline="")
            fg, ifg = GOLD, GOLD
        self.create_text(26, self.nh / 2, text=self.icon, fill=ifg,
                         font=("Segoe UI Symbol", 13))
        self.create_text(52, self.nh / 2, text=self.label, anchor="w", fill=fg,
                         font=(FONT["ui"], 11, "bold" if self.active else "normal"))
        if self.badge:
            self.create_oval(self.nw - 34, self.nh / 2 - 10, self.nw - 14, self.nh / 2 + 10,
                             fill=ORANGE, outline="")
            self.create_text(self.nw - 24, self.nh / 2, text=self.badge, fill=BG,
                             font=(FONT["ui"], 9, "bold"))

    def _hover(self, on):
        self.hovering = on
        self.draw()

    def set_active(self, on):
        if on != self.active:
            self.active = on
            self.draw()

    def set_badge(self, text):
        if text != self.badge:
            self.badge = text
            self.draw()


class Toggle(tk.Canvas):
    def __init__(self, parent, value, on_change, bg=None):
        super().__init__(parent, width=54, height=30, bg=bg or PANEL, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.on, self.on_change = bool(value), on_change
        self.bind("<ButtonRelease-1>", self._click)
        self.draw()

    def draw(self):
        self.delete("all")
        rrect(self, 2, 3, 52, 27, 12, fill=AMBER if self.on else EDGE,
              outline=GOLD if self.on else FAINT)
        kx = 39 if self.on else 15
        self.create_oval(kx - 9, 6, kx + 9, 24, fill=CREAM if self.on else DIM, outline="")

    def _click(self, _):
        self.on = not self.on
        self.draw()
        self.on_change(self.on)

    def set(self, value):
        self.on = bool(value)
        self.draw()


class Slider(tk.Canvas):
    def __init__(self, parent, width, vmin, vmax, value, step, fmt, on_change,
                 on_release=None, bg=None):
        super().__init__(parent, width=width, height=34, bg=bg or PANEL, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.sw, self.vmin, self.vmax, self.vstep = width, vmin, vmax, step
        self.val, self.fmt = value, fmt
        self.on_change, self.on_release = on_change, on_release
        self.pad, self.track_end = 12, width - 74
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.draw()

    def _frac(self):
        return clamp((self.val - self.vmin) / (self.vmax - self.vmin), 0.0, 1.0)

    def _text(self):
        return self.fmt(self.val) if callable(self.fmt) else self.fmt.format(self.val)

    def draw(self):
        self.delete("all")
        x = self.pad + self._frac() * (self.track_end - self.pad)
        self.create_line(self.pad, 17, self.track_end, 17, fill=EDGE, width=6,
                         capstyle="round")
        if x > self.pad:
            self.create_line(self.pad, 17, x, 17, fill=AMBER, width=6, capstyle="round")
        self.create_oval(x - 9, 8, x + 9, 26, fill=CREAM, outline=AMBER, width=2)
        self.create_text(self.sw - 4, 17, text=self._text(), anchor="e",
                         fill=CREAM, font=(FONT["num"], 11, "bold"))

    def _drag(self, event):
        x = clamp(event.x, self.pad, self.track_end)
        frac = (x - self.pad) / (self.track_end - self.pad)
        v = self.vmin + frac * (self.vmax - self.vmin)
        v = round(v / self.vstep) * self.vstep
        v = round(clamp(v, self.vmin, self.vmax), 4)
        if v != self.val:
            self.val = v
            self.draw()
            self.on_change(v)

    def _release(self, _):
        if self.on_release:
            self.on_release()

    def set(self, value):
        self.val = value
        self.draw()


class Segmented(tk.Canvas):
    """A pill of options (like tabs). options = [(value, label), ...]"""

    def __init__(self, parent, options, value, on_change, width=300, height=34,
                 canvas_bg=None, size=9):
        super().__init__(parent, width=width, height=height, bg=canvas_bg or BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.opts, self.sel, self.on_change = options, value, on_change
        self.sw, self.sh, self.fsize = width, height, size
        self.bind("<ButtonRelease-1>", self._click)
        self.draw()

    def draw(self):
        self.delete("all")
        n = len(self.opts)
        seg = (self.sw - 4) / n
        rrect(self, 1, 1, self.sw - 1, self.sh - 1, self.sh // 2, fill=PANEL, outline=EDGE)
        for i, (value, label) in enumerate(self.opts):
            x0 = 2 + i * seg
            on = value == self.sel
            if on:
                rrect(self, x0 + 2, 3, x0 + seg - 2, self.sh - 3, (self.sh - 6) // 2,
                      fill=AMBER, outline="")
            self.create_text(x0 + seg / 2, self.sh / 2, text=label,
                             fill=BG if on else DIM,
                             font=(FONT["ui"], self.fsize, "bold"))

    def _click(self, event):
        i = int(clamp(event.x - 2, 0, self.sw - 5) // ((self.sw - 4) / len(self.opts)))
        value = self.opts[i][0]
        if value != self.sel:
            self.sel = value
            self.draw()
            self.on_change(value)

    def set(self, value):
        self.sel = value
        self.draw()


class Tile(tk.Canvas):
    def __init__(self, parent, label, accent, width=178, height=98):
        super().__init__(parent, width=width, height=height, bg=BG,
                         highlightthickness=0, bd=0)
        rrect(self, 1, 1, width - 1, height - 1, 16, fill=PANEL, outline=EDGE)
        self.create_oval(17, 19, 25, 27, fill=accent, outline="")
        self.create_text(33, 23, text=label.upper(), anchor="w", fill=DIM,
                         font=(FONT["ui"], 8, "bold"))
        self.val_item = self.create_text(18, 56, text="--", anchor="w", fill=CREAM,
                                         font=(FONT["num"], 26, "bold"))
        self.cap_item = self.create_text(18, 83, text="", anchor="w", fill=FAINT,
                                         font=(FONT["ui"], 8))
        self.last = None

    def set(self, value, caption=""):
        key = (value, caption)
        if key != self.last:
            self.last = key
            self.itemconfig(self.val_item, text=value)
            self.itemconfig(self.cap_item, text=caption)


class Badge(tk.Canvas):
    """An achievement badge: glows when unlocked, shows progress when locked."""

    def __init__(self, parent, icon, title, desc, width=142, height=112):
        super().__init__(parent, width=width, height=height, bg=BG,
                         highlightthickness=0, bd=0)
        self.icon, self.btitle, self.desc, self.bw, self.bh = icon, title, desc, width, height
        self.last = None
        self.draw(0.0, False, "")

    def draw(self, frac, unlocked, progress):
        key = (round(frac, 3), unlocked, progress)
        if key == self.last:
            return
        self.last = key
        self.delete("all")
        w, h, cx = self.bw, self.bh, self.bw / 2
        rrect(self, 1, 1, w - 1, h - 1, 16, fill=PANEL2 if unlocked else PANEL,
              outline=AMBER if unlocked else EDGE)
        if unlocked:
            for r, t in ((27, 0.10), (22, 0.20), (17, 0.34)):
                self.create_oval(cx - r, 34 - r, cx + r, 34 + r,
                                 fill=mix(PANEL2, AMBER, t), outline="")
        else:
            self.create_oval(cx - 20, 14, cx + 20, 54, fill=BG, outline=EDGE)
        self.create_text(cx, 34, text=self.icon, fill=CREAM if unlocked else FAINT,
                         font=("Segoe UI Symbol", 18))
        self.create_text(cx, 70, text=self.btitle, fill=CREAM if unlocked else DIM,
                         font=(FONT["ui"], 9, "bold"))
        if unlocked:
            self.create_text(cx, 90, text=self.desc, fill=GOLD, font=(FONT["ui"], 8))
        else:
            self.create_line(16, 96, w - 16, 96, fill=EDGE, width=4, capstyle="round")
            if frac > 0.01:
                self.create_line(16, 96, 16 + (w - 32) * frac, 96, fill=AMBER, width=4,
                                 capstyle="round")
            self.create_text(cx, 86, text=progress or self.desc, fill=FAINT,
                             font=(FONT["ui"], 8))


class LogView:
    """Fills a Text widget with new log lines, colour-coded."""

    def __init__(self, text, max_lines=300):
        self.text, self.max_lines, self.last = text, max_lines, 0
        text.tag_config("ts", foreground=FAINT)
        text.tag_config("info", foreground=mix(CREAM, DIM, 0.45))
        text.tag_config("good", foreground=GOOD)
        text.tag_config("bad", foreground=BAD)

    @staticmethod
    def tag_for(msg):
        low = msg.lower()
        if any(w in low for w in ("eliminated", "team wiped", "error", "couldn't",
                                  "not found", "stopped")):
            return "bad"
        if any(w in low for w in ("clicked", "readied", "remembered", "updated", "started")):
            return "good"
        return "info"

    def reset(self):
        self.last = STATE["log_seq"]
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def update(self):
        new = [e for e in STATE["log"] if e[0] > self.last]
        if not new:
            return
        self.text.config(state="normal")
        for _, line in new:
            ts, _, msg = line.partition("] ")
            self.text.insert("end", ts + "]  ", "ts")
            self.text.insert("end", msg + "\n", self.tag_for(msg))
        self.last = new[-1][0]
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > self.max_lines:
            self.text.delete("1.0", f"{lines - self.max_lines}.0")
        self.text.see("end")
        self.text.config(state="disabled")


# ---------------- charts ----------------
def draw_bars(c, box, values, labels, tag, fmt, avg_line=False, show_values=False, empty=""):
    """Bar chart on a canvas. Everything is tagged so it can be redrawn."""
    c.delete(tag)
    x0, y0, x1, y1 = box
    if not values or max(values) <= 0:
        c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=empty, fill=FAINT,
                      justify="center", font=(FONT["ui"], 10), tags=tag)
        return
    avg = sum(values) / len(values)
    top_val = max(max(values), avg if avg_line else 0) * 1.18
    for f in (0, 0.5, 1):
        y = y1 - (y1 - y0) * f
        c.create_line(x0, y, x1, y, fill=EDGE if f else FAINT, tags=tag)
        c.create_text(x0 - 8, y, text=fmt(top_val * f), anchor="e", fill=FAINT,
                      font=(FONT["ui"], 8), tags=tag)
    slot = (x1 - x0) / len(values)
    bw = min(26, slot * 0.62)
    muted = mix(AMBER, BG, 0.45)
    for i, v in enumerate(values):
        cx = x0 + slot * (i + 0.5)
        top = y1 - (y1 - y0) * v / top_val
        newest = i == len(values) - 1
        if v > 0:
            c.create_rectangle(cx - bw / 2, top, cx + bw / 2, y1, outline="",
                               fill=GOLD if newest else muted, tags=tag)
            c.create_rectangle(cx - bw / 2, top, cx + bw / 2, top + 3, outline="",
                               fill=CREAM if newest else GOLD, tags=tag)
            if show_values:
                c.create_text(cx, top - 8, text=fmt(v), fill=CREAM,
                              font=(FONT["ui"], 8, "bold"), tags=tag)
        if labels[i]:
            c.create_text(cx, y1 + 12, text=labels[i], fill=FAINT,
                          font=(FONT["ui"], 8), tags=tag)
    if avg_line:
        ya = y1 - (y1 - y0) * avg / top_val
        c.create_line(x0, ya, x1, ya, fill=CREAM, dash=(4, 3), tags=tag)
        c.create_text(x1, ya - 9, text=f"avg {fmt(avg)}", anchor="e", fill=CREAM,
                      font=(FONT["ui"], 8, "bold"), tags=tag)


def draw_donut(c, cx, cy, r, width, parts, tag):
    """parts = [(value, color), ...] drawn clockwise from the top."""
    c.delete(tag)
    box = (cx - r, cy - r, cx + r, cy + r)
    c.create_oval(*box, outline=EDGE, width=width, tags=tag)
    total = sum(v for v, _ in parts)
    if total <= 0:
        return
    start = 90.0
    for v, color in parts:
        if v <= 0:
            continue
        extent = -min(359.9, 360.0 * v / total)
        c.create_arc(*box, start=start, extent=extent, style="arc", outline=color,
                     width=width, tags=tag)
        start += extent


# ---------------- screenshot wizard overlay ----------------
class SnipOverlay(tk.Toplevel):
    """Full-screen frozen screenshot: drag a box to save just that part."""

    def __init__(self, app, shot, label, on_done):
        super().__init__(app)
        from PIL import ImageEnhance, ImageTk
        self.on_done, self.shot = on_done, shot
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.sw, self.sh = sw, sh
        self.sx, self.sy = shot.width / sw, shot.height / sh
        self.overrideredirect(True)
        self.geometry(f"{sw}x{sh}+0+0")
        self.attributes("-topmost", True)
        base = shot if shot.size == (sw, sh) else shot.resize((sw, sh))
        self.base = base.convert("RGB")
        self.dim_img = ImageTk.PhotoImage(ImageEnhance.Brightness(self.base).enhance(0.40))
        self.spot_img = None
        self.cv = tk.Canvas(self, width=sw, height=sh, highlightthickness=0, bd=0,
                            cursor="crosshair")
        self.cv.pack()
        self.cv.create_image(0, 0, anchor="nw", image=self.dim_img)
        bw = 640
        rrect(self.cv, sw / 2 - bw / 2, 20, sw / 2 + bw / 2, 82, 16, fill=PANEL, outline=AMBER)
        self.cv.create_text(sw / 2, 40, text=f"Drag a box around the  {label}", fill=CREAM,
                            font=(FONT["ui"], 14, "bold"))
        self.cv.create_text(sw / 2, 65, fill=DIM, font=(FONT["ui"], 10),
                            text="Keep it snug \u2022 right-click or Esc to go back")
        self.start = None
        self.rect = self.spot = self.size_txt = None
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._motion)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Button-3>", lambda e: self._finish(None))
        self.bind("<Escape>", lambda e: self._finish(None))
        self.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)

    def _motion(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        left, right = sorted((x0, e.x))
        top, bottom = sorted((y0, e.y))
        for item in (self.rect, self.spot, self.size_txt):
            if item:
                self.cv.delete(item)
        if right - left > 2 and bottom - top > 2:
            from PIL import ImageTk
            self.spot_img = ImageTk.PhotoImage(self.base.crop((left, top, right, bottom)))
            self.spot = self.cv.create_image(left, top, anchor="nw", image=self.spot_img)
        self.rect = self.cv.create_rectangle(left, top, right, bottom, outline=AMBER, width=2)
        w, h = int((right - left) * self.sx), int((bottom - top) * self.sy)
        warn = w > 450 or h > 220
        self.size_txt = self.cv.create_text(
            left, max(12, top - 12), anchor="w", font=(FONT["ui"], 9, "bold"),
            fill=ORANGE if warn else GOLD,
            text=f"{w} x {h}px" + ("   (a bit big - try a tighter box)" if warn else ""))

    def _release(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        self.start = None
        box = scale_box(x0, y0, e.x, e.y, self.sx, self.sy, self.shot.width, self.shot.height)
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            for item in (self.rect, self.spot, self.size_txt):
                if item:
                    self.cv.delete(item)
            return
        self._finish(box)

    def _finish(self, box):
        self.destroy()
        self.on_done(box)


RANK_ICONS = ["\u2693", "\u2605", "\u2694", "\u2620", "\u265B", "\u2726", "\u265A"]
INT_KEYS = {"check_interval", "scan_jitter", "wave_amplitude", "start_delay", "ready_timeout",
            "lobby_wait", "failsafe_delay", "stop_after_deaths", "stop_after_minutes"}
ROW_H = 58


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        try:  # keep text sized for a 96-dpi layout even on scaled displays
            self.tk.call("tk", "scaling", 96 / 72)
        except Exception:
            pass
        self.title("Pirate Shop Macro")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg=BG)
        init_fonts(self)
        self.logo = load_logo(96)
        self.icon = load_logo(64)
        if self.icon:
            self.iconphoto(True, self.icon)
        self._dark_titlebar()

        self.frame_no = 0
        self.ui_error = False
        self.current = "dashboard"
        self.stats_view = "session"
        self.draw_keys = {}
        self.log_views = []
        self.slider_widgets, self.toggle_widgets, self.choice_widgets = {}, {}, {}
        self.thumbs, self.test_results, self.step_items = {}, {}, {}
        self.capture_key, self.cap_request, self.cap_handle = None, False, None
        self.bar, self.overlay, self.cap_shot, self.grabbing = None, None, None, False
        self.stop_handle = None
        self.toast_widget, self.toast_job = None, None

        self._build_sidebar()
        self.main = tk.Frame(self, bg=BG)
        self.main.place(x=SIDE_W, y=0, width=WIN_W - SIDE_W, height=WIN_H)
        self.pages = {}
        for name in ("dashboard", "setup", "stats", "settings", "log"):
            f = tk.Frame(self.main, bg=BG)
            f.place(x=0, y=0, relwidth=1, relheight=1)
            self.pages[name] = f
        self._build_dashboard(self.pages["dashboard"])
        self._build_setup(self.pages["setup"])
        self._build_stats(self.pages["stats"])
        self._build_settings(self.pages["settings"])
        self._build_log(self.pages["log"])

        self.attributes("-topmost", bool(CFG["always_on_top"]))
        self.set_hotkey(CFG["stop_key"])
        if missing_images():
            self.show_page("setup")
            self.toast("Welcome! Capture each button below, then press START.", "info")
        else:
            self.show_page("dashboard")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    # ---------- window chrome ----------
    def _dark_titlebar(self):
        try:
            self.update()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

    def set_hotkey(self, key):
        if self.stop_handle is not None:
            try:
                keyboard.remove_hotkey(self.stop_handle)
            except Exception:
                pass
            self.stop_handle = None
        try:
            self.stop_handle = keyboard.add_hotkey(key, self.stop)
        except Exception:
            self.stop_handle = None

    def toast(self, text, kind="info"):
        if self.toast_job:
            try:
                self.after_cancel(self.toast_job)
            except Exception:
                pass
        if self.toast_widget:
            self.toast_widget.destroy()
        color = TOAST_COLORS.get(kind, AMBER)
        w = int(clamp(60 + len(text) * 7, 230, 560))
        c = tk.Canvas(self.main, width=w, height=44, bg=BG, highlightthickness=0, bd=0)
        rrect(c, 1, 1, w - 1, 43, 14, fill=PANEL2, outline=color)
        c.create_oval(15, 16, 27, 28, fill=color, outline="")
        c.create_text(38, 22, text=text, anchor="w", fill=CREAM, font=(FONT["ui"], 10, "bold"))
        c.place(relx=1.0, rely=1.0, x=-24, y=-20, anchor="se")
        self.toast_widget = c
        self.toast_job = self.after(3800, self._clear_toast)

    def _clear_toast(self):
        if self.toast_widget:
            self.toast_widget.destroy()
        self.toast_widget, self.toast_job = None, None

    # ---------- sidebar ----------
    def _build_sidebar(self):
        sb = tk.Canvas(self, width=SIDE_W, height=WIN_H, bg=SIDEBAR,
                       highlightthickness=0, bd=0)
        sb.place(x=0, y=0)
        sb.create_line(SIDE_W - 1, 0, SIDE_W - 1, WIN_H, fill=EDGE)
        self.sidebar = sb

        cx, cy = SIDE_W // 2, 84
        self.glow_items = []
        for r in range(60, 20, -3):
            self.glow_items.append(
                (sb.create_oval(cx - r, cy - r, cx + r, cy + r, fill=SIDEBAR, outline=""),
                 ((60 - r) / 40) ** 1.5))
        if self.logo:
            sb.create_image(cx, cy, image=self.logo)
        sb.create_text(cx, 164, text="PIRATE SHOP", fill=GOLD,
                       font=(FONT["title"], 15, "bold"))
        sb.create_text(cx, 186, text="M A C R O", fill=CREAM, font=(FONT["ui"], 10, "bold"))
        gradient_bar(sb, 40, SIDE_W - 40, 206, SIDEBAR, AMBER, seg=4)

        self.nav = {}
        items = [("dashboard", "\u25C9", "Dashboard"), ("setup", "\u2726", "Setup"),
                 ("stats", "\u25A6", "Stats"), ("settings", "\u2699", "Settings"),
                 ("log", "\u2263", "Activity log")]
        for i, (key, icon, label) in enumerate(items):
            btn = NavButton(sb, icon, label, lambda k=key: self.show_page(k))
            sb.create_window(12, 226 + i * 46, window=btn, anchor="nw")
            self.nav[key] = btn

        # rank mini panel
        sb.create_text(24, 478, text="RANK", anchor="w", fill=FAINT, font=(FONT["ui"], 8, "bold"))
        self.rank_name = sb.create_text(24, 500, text="Deckhand", anchor="w", fill=GOLD,
                                        font=(FONT["title"], 13, "bold"))
        sb.create_line(24, 524, SIDE_W - 24, 524, fill=EDGE, width=6, capstyle="round")
        self.rank_fill = sb.create_line(24, 524, 25, 524, fill=AMBER, width=6, capstyle="round")
        self.rank_cap = sb.create_text(24, 543, text="", anchor="w", fill=FAINT,
                                       font=(FONT["ui"], 8))

        self.side_dot = sb.create_oval(24, WIN_H - 70, 36, WIN_H - 58, fill=DIM, outline="")
        self.side_status = sb.create_text(46, WIN_H - 64, text="Idle", anchor="w", fill=CREAM,
                                          font=(FONT["ui"], 10, "bold"))
        self.side_sub = sb.create_text(24, WIN_H - 40, text="", anchor="w", fill=FAINT,
                                       font=(FONT["ui"], 8))
        sb.create_text(24, WIN_H - 20, text=f"v{APP_VERSION}", anchor="w", fill=FAINT,
                       font=(FONT["ui"], 8))

    def show_page(self, name):
        self.current = name
        self.pages[name].tkraise()
        for k, b in self.nav.items():
            b.set_active(k == name)
        if self.toast_widget:
            self.toast_widget.lift()
        self.draw_keys.clear()
        if name == "setup":
            self.refresh_setup()
        self.update_data(time.time())

    def _page_header(self, parent, title, subtitle):
        tk.Label(parent, text=title, fg=CREAM, bg=BG, anchor="w",
                 font=(FONT["title"], 21, "bold")).place(x=PAGE_PAD, y=20)
        tk.Label(parent, text=subtitle, fg=DIM, bg=BG, anchor="w",
                 font=(FONT["ui"], 10)).place(x=PAGE_PAD + 2, y=58)

    def _changed(self, name, key):
        if self.draw_keys.get(name) != key:
            self.draw_keys[name] = key
            return True
        return False

    # ---------- dashboard ----------
    def _build_dashboard(self, page):
        self._page_header(page, "Dashboard", "Live view of your macro")
        top = 92

        hero = make_card(page, CONTENT_W, 176)
        hero.place(x=PAGE_PAD, y=top)
        gradient_bar(hero, 24, CONTENT_W - 24, 2, PANEL, AMBER, seg=6)
        self.hero = hero
        self.ring_geo = (96, 80, 50)
        rx, ry, rr = self.ring_geo
        self.wave_lines = [hero.create_line(24, 158, 60, 158, 96, 158, fill=PANEL, width=2,
                                            smooth=True) for _ in range(2)]
        self.ring_pulses = [hero.create_oval(rx, ry, rx, ry, outline=PANEL, width=2)
                            for _ in range(2)]
        hero.create_oval(rx - rr, ry - rr, rx + rr, ry + rr, outline=EDGE, width=9)
        self.ring_arc = hero.create_arc(rx - rr, ry - rr, rx + rr, ry + rr, start=90,
                                        extent=0, style="arc", outline=AMBER, width=9)
        self.ring_val = hero.create_text(rx, ry - 4, text="--", fill=CREAM,
                                         font=(FONT["num"], 24, "bold"))
        self.ring_cap = hero.create_text(rx, ry + 22, text="IDLE", fill=FAINT,
                                         font=(FONT["ui"], 8, "bold"))
        self.hero_title = hero.create_text(184, 44, text="Ready to start", anchor="w",
                                           fill=DIM, font=(FONT["title"], 22, "bold"))
        self.hero_detail = hero.create_text(185, 82, text="", anchor="w", fill=CREAM,
                                            width=340, font=(FONT["ui"], 11))
        self.hero_info = hero.create_text(185, 116, text="", anchor="w", fill=FAINT,
                                          width=350, font=(FONT["ui"], 9))
        self.start_btn = RoundButton(hero, "\u25B6   START", self.start, width=170, height=48,
                                     canvas_bg=PANEL)
        hero.create_window(CONTENT_W - 190, 26, window=self.start_btn, anchor="nw")
        self.stop_btn = RoundButton(hero, "\u25A0   STOP", self.stop, width=170, height=48,
                                    bg="#a8402a", hover="#c9573b", fg=CREAM, canvas_bg=PANEL)
        hero.create_window(CONTENT_W - 190, 82, window=self.stop_btn, anchor="nw")

        ty = top + 176 + 12
        self.tiles = {}
        specs = [("deaths", "Eliminations", BAD), ("time", "Run time", GOOD),
                 ("rate", "Per hour", AMBER), ("avg", "Avg match length", INFO)]
        for i, (key, label, accent) in enumerate(specs):
            t = Tile(page, label, accent)
            t.place(x=PAGE_PAD + i * (178 + 14), y=ty)
            self.tiles[key] = t

        ry0 = ty + 98 + 12
        self.chart_w, self.chart_h = 442, 226
        self.chart = make_card(page, self.chart_w, self.chart_h)
        self.chart.place(x=PAGE_PAD, y=ry0)
        card_title(self.chart, self.chart_w, "Match length", "last 20 eliminations")

        feed_w = CONTENT_W - self.chart_w - 12
        feed = make_card(page, feed_w, self.chart_h)
        feed.place(x=PAGE_PAD + self.chart_w + 12, y=ry0)
        card_title(feed, feed_w, "Activity")
        txt = tk.Text(feed, bg=PANEL, fg=DIM, relief="flat", font=(FONT["mono"], 9),
                      state="disabled", wrap="none", padx=2, pady=2, bd=0,
                      highlightthickness=0, cursor="arrow")
        feed.create_window(16, 50, window=txt, anchor="nw", width=feed_w - 30,
                           height=self.chart_h - 62)
        self.log_views.append(LogView(txt, 60))

    def draw_match_chart(self):
        durs = [m[0] for m in STATE["matches"]]
        data = durs[-20:]
        first = len(durs) - len(data) + 1
        labels = [f"#{first + i}" if (len(data) <= 10 or i % 2 == 0) else ""
                  for i in range(len(data))]
        draw_bars(self.chart, (58, 62, self.chart_w - 24, self.chart_h - 36), data, labels,
                  "chart", fmt_short, avg_line=True,
                  empty="No eliminations yet.\nMatch lengths will appear here.")

    # ---------- setup wizard ----------
    def _build_setup(self, page):
        self._page_header(page, "Setup", "Capture each button once. It takes about two minutes.")
        top = 92
        prog = make_card(page, CONTENT_W, 74)
        prog.place(x=PAGE_PAD, y=top)
        self.prog_card = prog
        self.prog_title = prog.create_text(24, 26, text="0 of 0 captured", anchor="w",
                                           fill=CREAM, font=(FONT["title"], 14, "bold"))
        self.prog_sub = prog.create_text(
            24, 52, anchor="w", fill=DIM, font=(FONT["ui"], 9),
            text=f"Press Capture, show the button on screen, hit {CAPTURE_KEY.upper()}, "
                 "then drag a box around it.")
        bx0, bx1 = 360, CONTENT_W - 190
        prog.create_line(bx0, 37, bx1, 37, fill=EDGE, width=10, capstyle="round")
        self.prog_fill = prog.create_line(bx0, 37, bx0 + 1, 37, fill=AMBER, width=10,
                                          capstyle="round")
        self.prog_bar = (bx0, bx1)
        folder = RoundButton(prog, "Open images folder", self.open_images_folder, width=160,
                             height=34, bg=PANEL2, hover=EDGE, fg=CREAM, canvas_bg=PANEL,
                             radius=14, size=9)
        prog.create_window(CONTENT_W - 20, 37, window=folder, anchor="e")

        sy = top + 74 + 10
        for i, (key, title, hint, optional, toggle) in enumerate(STEPS):
            c = make_card(page, CONTENT_W, 72, r=16)
            c.place(x=PAGE_PAD, y=sy + i * 78)
            it = {"card": c}
            it["circle"] = c.create_oval(18, 20, 50, 52, fill=PANEL2, outline=EDGE, width=2)
            it["num"] = c.create_text(34, 36, text=str(i + 1), fill=DIM,
                                      font=(FONT["num"], 13, "bold"))
            rrect(c, 64, 10, 188, 62, 10, fill=BG, outline=EDGE)
            it["thumb"] = c.create_image(126, 36, anchor="center", state="hidden")
            it["ph"] = c.create_text(126, 36, text="no picture yet", fill=FAINT,
                                     font=(FONT["ui"], 8))
            it["title"] = c.create_text(204, 24, text=title, anchor="w", fill=CREAM,
                                        font=(FONT["ui"], 11, "bold"))
            it["hint"] = c.create_text(204, 48, text=hint, anchor="w", fill=DIM, width=340,
                                       font=(FONT["ui"], 9))
            it["result"] = c.create_text(CONTENT_W - 90, 11, text="", fill=GOOD,
                                         font=(FONT["ui"], 8, "bold"))
            it["capture"] = RoundButton(c, "Capture", lambda k=key: self.begin_capture(k),
                                        width=104, height=34, canvas_bg=PANEL, radius=14, size=10)
            c.create_window(CONTENT_W - 18 - 78, 38, window=it["capture"], anchor="e")
            it["test"] = RoundButton(c, "Test", lambda k=key: self.test_step(k), width=70,
                                     height=34, bg=PANEL2, hover=EDGE, fg=CREAM,
                                     canvas_bg=PANEL, radius=14, size=10)
            c.create_window(CONTENT_W - 18, 38, window=it["test"], anchor="e")
            self.step_items[key] = it

    def refresh_setup(self):
        for key, title, hint, optional, toggle in STEPS:
            it, c = self.step_items[key], self.step_items[key]["card"]
            path = img(key)
            exists = os.path.exists(path)
            enabled = step_enabled(key)
            if exists:
                mtime = os.path.getmtime(path)
                cached = self.thumbs.get(key)
                if not cached or cached[0] != mtime:
                    self.thumbs[key] = (mtime, make_thumb(path, 116, 46))
                photo = self.thumbs[key][1]
                if photo:
                    c.itemconfig(it["thumb"], image=photo, state="normal")
                    c.itemconfig(it["ph"], state="hidden")
                else:
                    c.itemconfig(it["thumb"], state="hidden")
                    c.itemconfig(it["ph"], text="saved", state="normal")
            else:
                c.itemconfig(it["thumb"], state="hidden")
                c.itemconfig(it["ph"], text="no picture yet", state="normal")
            idx = [s[0] for s in STEPS].index(key) + 1
            if exists:
                c.itemconfig(it["circle"], fill=GOOD, outline=GOOD)
                c.itemconfig(it["num"], text="\u2713", fill=BG)
            elif enabled and not optional:
                c.itemconfig(it["circle"], fill=PANEL2, outline=AMBER)
                c.itemconfig(it["num"], text=str(idx), fill=AMBER)
            else:
                c.itemconfig(it["circle"], fill=PANEL2, outline=EDGE)
                c.itemconfig(it["num"], text=str(idx), fill=FAINT)
            c.itemconfig(it["title"], text=title if enabled else f"{title}   (turned off)",
                         fill=CREAM if enabled else FAINT)
            c.itemconfig(it["hint"], fill=DIM if enabled else FAINT)
            it["capture"].set_text("Re-capture" if exists else "Capture")
            it["capture"].set_enabled(enabled)
            it["test"].set_enabled(enabled and exists)
        req = required_keys()
        have = sum(1 for k in req if os.path.exists(img(k)))
        total = len(req)
        done = have >= total
        self.prog_card.itemconfig(
            self.prog_title, text=("All set!  Press START on the Dashboard." if done
                                   else f"{have} of {total} captured"),
            fill=GOOD if done else CREAM)
        bx0, bx1 = self.prog_bar
        self.prog_card.coords(self.prog_fill, bx0, 37,
                              bx0 + max(1, (bx1 - bx0) * have / max(1, total)), 37)
        self.prog_card.itemconfig(self.prog_fill, fill=GOOD if done else AMBER)

    def open_images_folder(self):
        os.makedirs(IMG_DIR, exist_ok=True)
        try:
            os.startfile(IMG_DIR)
        except Exception:
            self.toast(f"Images folder: {IMG_DIR}", "info")

    # ----- testing a captured picture -----
    def test_step(self, key):
        if not os.path.exists(img(key)):
            self.toast("Capture it first.", "warn")
            return
        self.test_results[key] = (time.time(), "busy")
        threading.Thread(target=self._test_worker, args=(key,), daemon=True).start()

    def _test_worker(self, key):
        try:
            box = locate(key, None)
            self.test_results[key] = (time.time(), "found" if box else "missing")
        except Exception:
            self.test_results[key] = (time.time(), "missing")

    def update_test_results(self, now):
        for key, it in self.step_items.items():
            res = self.test_results.get(key)
            text, color = "", GOOD
            if res and now - res[0] < 6:
                if res[1] == "busy":
                    text, color = "searching...", DIM
                elif res[1] == "found":
                    text, color = "\u2713 found on screen", GOOD
                else:
                    text, color = "not on screen right now", ORANGE
            it["card"].itemconfig(it["result"], text=text, fill=color)

    # ----- the capture wizard -----
    def begin_capture(self, key):
        if self.capture_key:
            return
        self.capture_key = key
        self.cap_request = False
        self.grabbing = False
        self.withdraw()
        self._make_capture_bar(key)
        try:
            self.cap_handle = keyboard.add_hotkey(CAPTURE_KEY, self._on_capture_hotkey)
        except Exception:
            self.cap_handle = None

    def _on_capture_hotkey(self):
        self.cap_request = True        # picked up by tick() on the UI thread

    def _make_capture_bar(self, key):
        _, title, hint, _, _ = STEP_BY_KEY[key]
        bar = tk.Toplevel(self)
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg=BG)
        w, h = 640, 116
        sw = self.winfo_screenwidth()
        bar.geometry(f"{w}x{h}+{(sw - w) // 2}+18")
        c = make_card(bar, w, h)
        c.pack()
        c.create_text(24, 26, anchor="w", text=f"Capturing:  {title}", fill=AMBER,
                      font=(FONT["ui"], 12, "bold"))
        c.create_text(24, 54, anchor="w", text=hint, fill=CREAM, width=w - 48,
                      font=(FONT["ui"], 10))
        c.create_text(24, 90, anchor="w", fill=GOLD, font=(FONT["ui"], 10, "bold"),
                      text=f"Show it on screen, then press {CAPTURE_KEY.upper()}")
        cancel = RoundButton(c, "Cancel", self.cancel_capture, width=90, height=34,
                             bg=PANEL2, hover=EDGE, fg=CREAM, canvas_bg=PANEL, radius=14, size=10)
        c.create_window(w - 20, 90, window=cancel, anchor="e")
        now_btn = RoundButton(c, "Capture now", self.do_grab, width=130, height=34,
                              canvas_bg=PANEL, radius=14, size=10)
        c.create_window(w - 20 - 98, 90, window=now_btn, anchor="e")
        self.bar = bar

    def do_grab(self):
        if not self.capture_key or self.overlay or self.grabbing or not self.bar:
            return
        self.grabbing = True
        self.bar.withdraw()
        self.after(250, self._grab_now)    # give the bar time to disappear

    def _grab_now(self):
        self.grabbing = False
        if not self.capture_key:
            return
        try:
            from PIL import ImageGrab
            self.cap_shot = ImageGrab.grab()
        except Exception as e:
            self.end_capture(False, f"Couldn't take a screenshot: {e}")
            return
        title = STEP_BY_KEY[self.capture_key][1]
        self.overlay = SnipOverlay(self, self.cap_shot, title, self._overlay_done)

    def _overlay_done(self, box):
        self.overlay = None
        if not self.capture_key:
            return
        if box is None:                    # backed out: show the bar again to retry
            if self.bar:
                self.bar.deiconify()
            return
        key = self.capture_key
        try:
            save_crop(self.cap_shot, box, img(key))
            MEMORY.pop(key, None)
            save_memory()
        except Exception as e:
            self.end_capture(False, f"Couldn't save the picture: {e}")
            return
        self.end_capture(True, f"Saved {IMAGE_NAMES[key]}  \u2022  use Test while it's on screen")

    def cancel_capture(self):
        self.end_capture(False, "Capture cancelled")

    def end_capture(self, ok, message):
        if self.cap_handle is not None:
            try:
                keyboard.remove_hotkey(self.cap_handle)
            except Exception:
                pass
            self.cap_handle = None
        if self.overlay:
            try:
                self.overlay.destroy()
            except Exception:
                pass
            self.overlay = None
        if self.bar:
            self.bar.destroy()
            self.bar = None
        self.capture_key, self.cap_shot, self.grabbing = None, None, False
        self.deiconify()
        self.lift()
        self.show_page("setup")
        self.toast(message, "good" if ok else "warn")

    # ---------- stats ----------
    def _stat_rows(self, card, x_label, x_value, y0, step, rows):
        items = {}
        for i, (key, label) in enumerate(rows):
            y = y0 + i * step
            card.create_text(x_label, y, text=label, anchor="w", fill=DIM,
                             font=(FONT["ui"], 10))
            items[key] = card.create_text(x_value, y, text="--", anchor="e", fill=CREAM,
                                          font=(FONT["num"], 11, "bold"))
            if i < len(rows) - 1:
                card.create_line(x_label, y + step / 2, x_value, y + step / 2, fill=EDGE)
        return items

    def _build_stats(self, page):
        self._page_header(page, "Stats", "How your runs are going")
        seg = Segmented(page, [("session", "This session"), ("alltime", "All time"),
                               ("badges", "Rank & badges")], "session", self._stats_tab,
                        width=440, height=34, size=10)
        seg.place(x=PAGE_PAD, y=94)
        self.stats_frames = {}
        for name in ("session", "alltime", "badges"):
            f = tk.Frame(page, bg=BG)
            f.place(x=0, y=142, width=WIN_W - SIDE_W, height=WIN_H - 142)
            self.stats_frames[name] = f
        half = (CONTENT_W - 12) // 2 - 4          # 365
        right_w = CONTENT_W - half - 12           # 377
        rx = PAGE_PAD + half + 12

        # ---- this session ----
        f = self.stats_frames["session"]
        left = make_card(f, half, 402)
        left.place(x=PAGE_PAD, y=0)
        card_title(left, half, "This session")
        self.sess_card = left
        self.sess_rows = self._stat_rows(left, 22, half - 22, 64, 23.5, [
            ("runtime", "Run time"), ("deaths", "Eliminations"),
            ("kinds", "Team wipes / eliminated"), ("rate", "Per hour"),
            ("avg", "Average match"), ("fastest", "Fastest match"),
            ("longest", "Longest match"), ("inmatch", "Time in match"),
            ("scans", "Scans"), ("clicks", "Clicks"),
            ("ready", "Ready-ups (ok / missed)"), ("failsafe", "Extra popup clicks"),
            ("leavefail", "Leave failures"), ("last", "Last elimination")])
        self.donut = make_card(f, right_w, 190)
        self.donut.place(x=rx, y=0)
        card_title(self.donut, right_w, "Time in match")
        self.recent = make_card(f, right_w, 200)
        self.recent.place(x=rx, y=202)
        card_title(self.recent, right_w, "Recent matches", "newest first")
        self.right_w = right_w

        # ---- all time ----
        f = self.stats_frames["alltime"]
        left = make_card(f, half, 402)
        left.place(x=PAGE_PAD, y=0)
        card_title(left, half, "All time")
        self.life_card = left
        self.life_rows = self._stat_rows(left, 22, half - 22, 64, 26, [
            ("sessions", "Sessions"), ("runtime", "Total run time"),
            ("deaths", "Total eliminations"), ("kinds", "Team wipes / eliminated"),
            ("rate", "Per hour"), ("fastest", "Fastest match ever"),
            ("longest", "Longest match ever"), ("best_session", "Best session (eliminations)"),
            ("longest_session", "Longest session"), ("scans", "Total scans"),
            ("clicks", "Total clicks"), ("first", "First session"), ("last", "Last session")])
        self.daily = make_card(f, right_w, 190)
        self.daily.place(x=rx, y=0)
        card_title(self.daily, right_w, "Last 7 days", "eliminations")
        self.hours = make_card(f, right_w, 200)
        self.hours.place(x=rx, y=202)
        card_title(self.hours, right_w, "Busiest hours", "when eliminations happen")

        # ---- rank & badges ----
        f = self.stats_frames["badges"]
        rank = make_card(f, CONTENT_W, 120)
        rank.place(x=PAGE_PAD, y=0)
        self.rank_card = rank
        self.rank_glow = []
        for r, t in ((46, 0.08), (40, 0.14), (34, 0.22), (28, 0.32)):
            self.rank_glow.append(rank.create_oval(24 + 46 - r, 60 - r, 24 + 46 + r, 60 + r,
                                                   fill=mix(PANEL, AMBER, t), outline=""))
        self.rank_icon = rank.create_text(70, 60, text="\u2693", fill=CREAM,
                                          font=("Segoe UI Symbol", 28))
        rank.create_text(150, 26, text="CURRENT RANK", anchor="w", fill=AMBER,
                         font=(FONT["ui"], 8, "bold"))
        self.rank_title = rank.create_text(150, 54, text="Deckhand", anchor="w", fill=CREAM,
                                           font=(FONT["title"], 22, "bold"))
        self.rank_bar = (150, CONTENT_W - 30)
        rank.create_line(150, 86, CONTENT_W - 30, 86, fill=EDGE, width=10, capstyle="round")
        self.rank_progress = rank.create_line(150, 86, 151, 86, fill=AMBER, width=10,
                                              capstyle="round")
        self.rank_text = rank.create_text(150, 104, text="", anchor="w", fill=DIM,
                                          font=(FONT["ui"], 9))
        self.badges = []
        for i, (icon, title, desc, metric, goal) in enumerate(ACHIEVEMENTS):
            b = Badge(f, icon, title, desc)
            b.place(x=PAGE_PAD + (i % 5) * 153, y=132 + (i // 5) * 124)
            self.badges.append(b)
        self._stats_tab("session")

    def _stats_tab(self, name):
        self.stats_view = name
        self.stats_frames[name].tkraise()
        self.draw_keys.clear()

    # ---------- settings ----------
    def _build_settings(self, page):
        self._page_header(page, "Settings", "Changes apply instantly and save automatically")
        tabs = [("detect", "Detection"), ("mouse", "Mouse"), ("timing", "Timing"),
                ("app", "App & look")]
        seg = Segmented(page, tabs, "detect", self._settings_tab, width=480, height=34, size=10)
        seg.place(x=PAGE_PAD, y=94)
        fmt_off = lambda unit: (lambda v: "Off" if v == 0 else f"{int(v)}{unit}")
        spec = {
            "detect": [
                ("use_return_lobby", "Detect team wipe",
                 "Watch for the Return to Lobby button (your whole team is out)", ("toggle",)),
                ("use_eliminated", "Detect elimination",
                 "Watch for the \u201CEliminated by\u201D text (teammates still alive)", ("toggle",)),
                ("check_interval", "Scan interval", "How often to check the screen",
                 ("slider", 5, 90, 1, "{:.0f}s")),
                ("scan_jitter", "Random extra wait", "Adds 0 to this many seconds to each scan",
                 ("slider", 0, 15, 1, "{:.0f}s")),
                ("confidence", "Match confidence",
                 "Lower finds more, higher avoids wrong clicks",
                 ("slider", 0.60, 0.95, 0.01, "{:.2f}")),
            ],
            "mouse": [
                ("human_mouse", "Human-like mouse",
                 "Curved sine-wave cursor path with natural speed", ("toggle",)),
                ("wave_amplitude", "Wave strength", "How wide the cursor's curve swings",
                 ("slider", 0, 30, 1, "{:.0f}px")),
                ("mouse_speed", "Cursor speed", "How fast the cursor travels",
                 ("choice", [(k, k) for k in SPEEDS], 270)),
                ("click_delay_min", "Click pause (shortest)",
                 "Quickest wait between reaching a button and clicking",
                 ("slider", 0.0, 2.0, 0.05, "{:.2f}s")),
                ("click_delay_max", "Click pause (longest)",
                 "Slowest wait before clicking, picked at random",
                 ("slider", 0.0, 2.0, 0.05, "{:.2f}s")),
            ],
            "timing": [
                ("start_delay", "Start delay", "Time to click into Fortnite after pressing START",
                 ("slider", 0, 15, 1, "{:.0f}s")),
                ("ready_timeout", "Ready wait", "How long to look for Ready before moving on",
                 ("slider", 5, 60, 1, "{:.0f}s")),
                ("lobby_wait", "Lobby wait", "Pause in the lobby after leaving a match",
                 ("slider", 3, 40, 1, "{:.0f}s")),
                ("step_delay", "Menu pause", "Pause between Esc, menu and leave",
                 ("slider", 0.2, 3.0, 0.1, "{:.1f}s")),
                ("failsafe_delay", "Popup follow-up", "Wait after the extra popup before checking Ready",
                 ("slider", 1, 15, 1, "{:.0f}s")),
            ],
            "app": [
                ("stop_after_deaths", "Stop after eliminations", "Stops the macro automatically",
                 ("slider", 0, 500, 5, fmt_off(""))),
                ("stop_after_minutes", "Stop after minutes", "Stops the macro automatically",
                 ("slider", 0, 600, 5, fmt_off(" min"))),
                ("sound", "Sound effects", "A soft beep on eliminations and when it stops",
                 ("toggle",)),
                ("always_on_top", "Always on top", "Keep this window above Fortnite",
                 ("toggle",)),
                ("stop_key", "Stop hotkey", "Works from inside the game",
                 ("choice", [(k, k.upper()) for k in STOP_KEY_CHOICES], 270)),
                ("theme", "Colour theme", "Restart the app to apply a new theme",
                 ("choice", [(k, k) for k in THEME_NAMES], 360)),
            ],
        }
        self.settings_frames = {}
        for name, rows in spec.items():
            f = tk.Frame(page, bg=BG)
            f.place(x=0, y=142, width=WIN_W - SIDE_W, height=WIN_H - 142 - 76)
            self.settings_frames[name] = f
            self._settings_card(f, rows)

        by = WIN_H - 62
        RoundButton(page, "Reset to defaults", self.reset_defaults, width=180, height=40,
                    bg=PANEL2, hover=EDGE, fg=CREAM, size=10).place(x=PAGE_PAD, y=by)
        RoundButton(page, "Forget button locations", self.forget_locations, width=210,
                    height=40, bg=PANEL2, hover=EDGE, fg=CREAM, size=10
                    ).place(x=PAGE_PAD + 192, y=by)
        RoundButton(page, "Open images folder", self.open_images_folder, width=180,
                    height=40, bg=PANEL2, hover=EDGE, fg=CREAM, size=10
                    ).place(x=PAGE_PAD + 414, y=by)
        self._settings_tab("detect")

    def _settings_tab(self, name):
        self.settings_frames[name].tkraise()

    def _settings_card(self, frame, rows):
        card = make_card(frame, CONTENT_W, 24 + ROW_H * len(rows))
        card.place(x=PAGE_PAD, y=0)
        for i, (key, title, desc, spec) in enumerate(rows):
            y = 12 + i * ROW_H
            card.create_text(26, y + 19, text=title, anchor="w", fill=CREAM,
                             font=(FONT["ui"], 11, "bold"))
            card.create_text(26, y + 39, text=desc, anchor="w", fill=FAINT,
                             font=(FONT["ui"], 9))
            if i < len(rows) - 1:
                card.create_line(26, y + ROW_H - 2, CONTENT_W - 26, y + ROW_H - 2, fill=EDGE)
            if spec[0] == "slider":
                _, lo, hi, step, fmt = spec
                s = Slider(card, 320, lo, hi, CFG[key], step, fmt,
                           lambda v, k=key: self._set_cfg(k, v), self._save_cfg)
                card.create_window(CONTENT_W - 26, y + 29, window=s, anchor="e")
                self.slider_widgets[key] = s
            elif spec[0] == "toggle":
                t = Toggle(card, CFG[key], lambda v, k=key: self._set_cfg(k, v, save=True))
                card.create_window(CONTENT_W - 30, y + 29, window=t, anchor="e")
                self.toggle_widgets[key] = t
            else:
                _, options, width = spec
                seg = Segmented(card, options, CFG[key],
                                lambda v, k=key: self._set_cfg(k, v, save=True),
                                width=width, height=32, canvas_bg=PANEL, size=9)
                card.create_window(CONTENT_W - 26, y + 29, window=seg, anchor="e")
                self.choice_widgets[key] = seg

    def _set_cfg(self, key, value, save=False):
        if key in INT_KEYS:
            value = int(round(value))
        elif isinstance(DEFAULTS[key], float):
            value = round(float(value), 2)
        CFG[key] = value
        if key == "click_delay_min" and CFG["click_delay_max"] < value:
            CFG["click_delay_max"] = value
            self.slider_widgets["click_delay_max"].set(value)
        if key == "click_delay_max" and CFG["click_delay_min"] > value:
            CFG["click_delay_min"] = value
            self.slider_widgets["click_delay_min"].set(value)
        if key == "always_on_top":
            self.attributes("-topmost", bool(value))
        elif key == "stop_key":
            self.set_hotkey(value)
        elif key == "theme":
            self.toast("Theme saved - restart the app to see it", "info")
        elif key in ("use_return_lobby", "use_eliminated"):
            self.refresh_setup()
            if not (CFG["use_return_lobby"] or CFG["use_eliminated"]):
                self.toast("Turn at least one detection on", "warn")
        if save:
            self._save_cfg()

    def _save_cfg(self):
        save_json(SETTINGS_FILE, CFG)

    def reset_defaults(self):
        CFG.update(DEFAULTS)
        for k, s in self.slider_widgets.items():
            s.set(CFG[k])
        for k, t in self.toggle_widgets.items():
            t.set(CFG[k])
        for k, c in self.choice_widgets.items():
            c.set(CFG[k])
        self.attributes("-topmost", bool(CFG["always_on_top"]))
        self.set_hotkey(CFG["stop_key"])
        self.refresh_setup()
        self._save_cfg()
        log("Settings reset to defaults")
        self.toast("Settings reset to defaults", "good")

    def forget_locations(self):
        MEMORY.clear()
        try:
            os.remove(MEMORY_FILE)
        except OSError:
            pass
        log("Forgot saved button locations")
        self.toast("Forgot saved button locations", "good")

    # ---------- activity log page ----------
    def _build_log(self, page):
        self._page_header(page, "Activity log", "Everything the macro has done")
        card_h = WIN_H - 92 - 28
        card = make_card(page, CONTENT_W, card_h)
        card.place(x=PAGE_PAD, y=92)
        txt = tk.Text(card, bg=PANEL, fg=DIM, relief="flat", font=(FONT["mono"], 10),
                      state="disabled", wrap="none", padx=4, pady=4, bd=0,
                      highlightthickness=0, cursor="arrow")
        card.create_window(18, 62, window=txt, anchor="nw", width=CONTENT_W - 36,
                           height=card_h - 82)
        card_title(card, CONTENT_W, "Log")
        self.log_views.append(LogView(txt, 300))
        for i, (label, cmd) in enumerate([("Copy", self.copy_log), ("Clear", self.clear_log)]):
            b = RoundButton(card, label, cmd, width=84, height=30, bg=PANEL2, hover=EDGE,
                            fg=CREAM, canvas_bg=PANEL, radius=12, size=9)
            card.create_window(CONTENT_W - 22 - i * 94, 24, window=b, anchor="e")

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append("\n".join(line for _, line in STATE["log"]))
        self.toast("Log copied to clipboard", "good")

    def clear_log(self):
        STATE["log"].clear()
        for v in self.log_views:
            v.reset()

    # ---------- controls ----------
    def start(self):
        if STATE["running"]:
            return
        if not (CFG["use_return_lobby"] or CFG["use_eliminated"]):
            self.toast("Turn on at least one detection in Settings first", "warn")
            self.show_page("settings")
            return
        missing = missing_images()
        if missing:
            self.show_page("setup")
            self.toast(f"Finish setup first: {len(missing)} picture(s) still needed", "warn")
            return
        reset_session()
        STOP.clear()
        threading.Thread(target=worker, daemon=True).start()
        self.toast(f"Starting in {int(CFG['start_delay'])}s - click into Fortnite", "info")

    def stop(self):
        STOP.set()

    def on_close(self):
        STOP.set()
        if not STATE["finalized"]:
            STATE["end"] = STATE["end"] or time.time()
            finalize_session()
        self.destroy()

    # ---------- the update loop ----------
    def tick(self):
        try:
            now = time.time()
            self.frame_no += 1
            if self.cap_request:
                self.cap_request = False
                self.do_grab()
            while NOTIFY:
                text, kind = NOTIFY.pop(0)
                self.toast(text, kind)
            self.animate(now)
            if self.frame_no % 5 == 0:
                self.update_data(now)
        except Exception as e:
            if not self.ui_error:
                self.ui_error = True
                log(f"UI error: {e}")
        self.after(50, self.tick)

    def animate(self, now):
        running = STATE["running"]
        kind = STATE["kind"]
        color = KIND_COLORS.get(kind, DIM)

        pulse = 0.5 + 0.5 * math.sin(now * 2.2) if running else 0.5
        for item, depth in self.glow_items:
            self.sidebar.itemconfig(
                item, fill=mix(SIDEBAR, AMBER, depth * 0.5 * (0.55 + 0.45 * pulse)))
        blink = running and int(now * 2) % 2 == 0
        self.sidebar.itemconfig(self.side_dot, fill=mix(color, SIDEBAR, 0.5) if blink else color)

        if self.current != "dashboard":
            return
        h = self.hero
        nc = STATE["next_check"]
        rx, ry, rr = self.ring_geo
        if running and nc:
            rem = max(0.0, nc - now)
            frac = rem / max(1.0, STATE["scan_total"])
            h.itemconfig(self.ring_arc, extent=-359.9 * frac, outline=color)
            h.itemconfig(self.ring_val, text=str(math.ceil(rem)), fill=CREAM)
            h.itemconfig(self.ring_cap, text="NEXT SCAN", fill=DIM)
        elif running:
            h.itemconfig(self.ring_arc, extent=-359.9, outline=mix(color, PANEL, 0.4))
            h.itemconfig(self.ring_val, text="\u2022\u2022\u2022", fill=color)
            h.itemconfig(self.ring_cap, text="WORKING", fill=DIM)
        else:
            h.itemconfig(self.ring_arc, extent=0)
            h.itemconfig(self.ring_val, text="--", fill=FAINT)
            h.itemconfig(self.ring_cap, text="IDLE", fill=FAINT)
        for k, ring in enumerate(self.ring_pulses):
            if running and nc:
                phase = (now * 0.5 + k * 0.5) % 1.0
                r = rr + 8 + phase * 18
                h.coords(ring, rx - r, ry - r, rx + r, ry + r)
                h.itemconfig(ring, outline=mix(color, PANEL, 0.25 + 0.75 * phase))
            else:
                h.itemconfig(ring, outline=PANEL)
        # ocean waves along the bottom of the hero card
        amp = 6.0 if running else 2.0
        spin = now * (2.4 if running else 0.7)
        for idx, line in enumerate(self.wave_lines):
            pts = []
            for x in range(24, CONTENT_W - 23, 10):
                y = 158 + amp * math.sin(x * 0.035 + spin * (1 if idx == 0 else -0.8) + idx * 1.7)
                pts += [x, y]
            h.coords(line, *pts)
            h.itemconfig(line, fill=mix(color, PANEL, 0.55 if idx == 0 else 0.8))

    def update_data(self, now):
        running = STATE["running"]
        kind = STATE["kind"]
        color = KIND_COLORS.get(kind, DIM)
        elapsed = session_elapsed(now)
        deaths = STATE["deaths"]
        durs = [m[0] for m in STATE["matches"]]
        avg = sum(durs) / len(durs) if durs else None
        rate = deaths / (elapsed / 3600) if elapsed >= 60 and deaths else None
        in_match = sum(durs)
        if STATE["in_match"] and STATE["match_start"]:
            in_match += now - STATE["match_start"]
        uptime = (in_match / elapsed * 100) if elapsed > 0 else 0
        lv = life_view(now)
        missing = missing_images()

        # sidebar
        self.sidebar.itemconfig(self.side_status, text="Running" if running else "Idle")
        self.sidebar.itemconfig(self.side_sub, text=f"{CFG['stop_key'].upper()} stops from anywhere")
        _, rname, nxt, rfrac, _n = rank_info(lv["deaths"])
        self.sidebar.itemconfig(self.rank_name, text=rname)
        self.sidebar.coords(self.rank_fill, 24, 524, 24 + max(1, (SIDE_W - 48) * rfrac), 524)
        self.sidebar.itemconfig(
            self.rank_cap, text=f"{lv['deaths']} / {nxt} eliminations" if nxt else "Top rank reached")
        self.nav["setup"].set_badge(str(len(missing)) if missing else "")

        # hero
        h = self.hero
        h.itemconfig(self.hero_title, text=KIND_TITLES.get(kind, "Working"), fill=color)
        if STATE["start"]:
            detail = STATE["status"]
        elif missing:
            detail = "Finish the Setup page first, then press START."
        else:
            detail = "Everything is set. Press START when you're in the lobby."
        h.itemconfig(self.hero_detail, text=detail)
        detect = []
        if CFG["use_return_lobby"]:
            detect.append("team wipe")
        if CFG["use_eliminated"]:
            detect.append("elimination")
        h.itemconfig(self.hero_info, text=(
            f"Watching for: {' + '.join(detect) or 'nothing'}\n"
            f"Scan every {int(CFG['check_interval'])}s  \u2022  "
            f"Human mouse {'on' if CFG['human_mouse'] else 'off'}  \u2022  "
            f"{STATE['clicks']} clicks"))
        self.start_btn.set_enabled(not running)
        self.stop_btn.set_enabled(running)
        self.stop_btn.set_text(f"\u25A0   STOP  ({CFG['stop_key'].upper()})")

        # dashboard tiles
        self.tiles["deaths"].set(str(deaths),
                                 f"{STATE['wipes']} wipes \u2022 {STATE['elims']} elim.")
        self.tiles["time"].set(fmt_time(elapsed), f"{uptime:.0f}% of it in match")
        self.tiles["rate"].set(f"{rate:.1f}" if rate else "--",
                               "eliminations per hour" if rate else "after the first minute")
        self.tiles["avg"].set(fmt_short(avg) if avg else "--",
                              f"best {fmt_short(min(durs))}" if durs else "after your first one")

        if self.current == "dashboard" and self._changed("dash_chart", len(durs)):
            self.draw_match_chart()
        if self.current == "setup":
            self.update_test_results(now)
        if self.current == "stats":
            if self.stats_view == "session":
                self.update_session_stats(now, elapsed, durs, avg, rate, in_match)
            elif self.stats_view == "alltime":
                self.update_alltime_stats(lv)
            else:
                self.update_badges(lv)
        for v in self.log_views:
            v.update()

    def update_session_stats(self, now, elapsed, durs, avg, rate, in_match):
        s, c = self.sess_rows, self.sess_card
        vals = {
            "runtime": fmt_time(elapsed), "deaths": str(STATE["deaths"]),
            "kinds": f"{STATE['wipes']} / {STATE['elims']}",
            "rate": f"{rate:.1f} / hr" if rate else "--",
            "avg": fmt_short(avg) if avg else "--",
            "fastest": fmt_short(min(durs)) if durs else "--",
            "longest": fmt_short(max(durs)) if durs else "--",
            "inmatch": f"{fmt_short(in_match)}  ({in_match / elapsed * 100:.0f}%)" if elapsed > 0 else "--",
            "scans": str(STATE["scans"]), "clicks": str(STATE["clicks"]),
            "ready": f"{STATE['readyups']} / {STATE['ready_missed']}",
            "failsafe": str(STATE["failsafe"]), "leavefail": str(STATE["leave_fail"]),
            "last": fmt_ago(STATE["last_death"]),
        }
        for k, v in vals.items():
            c.itemconfig(s[k], text=v)

        # donut: how much of the session was spent actually in a match
        d, w = self.donut, self.right_w
        out = max(0.0, elapsed - in_match)
        draw_donut(d, 96, 112, 50, 16, [(in_match, AMBER), (out, EDGE)], "dn")
        d.delete("dnt")
        pct = in_match / elapsed * 100 if elapsed > 0 else 0
        d.create_text(96, 112, text=f"{pct:.0f}%", fill=CREAM, tags="dnt",
                      font=(FONT["num"], 18, "bold"))
        for i, (label, val, col) in enumerate([("In a match", in_match, AMBER),
                                                ("Menus, lobby & waiting", out, FAINT)]):
            y = 82 + i * 40
            d.create_rectangle(188, y - 6, 200, y + 6, fill=col, outline="", tags="dnt")
            d.create_text(210, y - 8, text=label, anchor="w", fill=DIM, tags="dnt",
                          font=(FONT["ui"], 9))
            d.create_text(210, y + 10, text=fmt_short(val), anchor="w", fill=CREAM, tags="dnt",
                          font=(FONT["num"], 12, "bold"))
        wipes, elims = STATE["wipes"], STATE["elims"]
        total = wipes + elims
        d.create_line(22, 170, w - 22, 170, fill=EDGE, width=8, capstyle="round", tags="dnt")
        if total:
            split = 22 + (w - 44) * wipes / total
            if wipes:
                d.create_line(22, 170, max(23, split), 170, fill=AMBER, width=8,
                              capstyle="round", tags="dnt")
            if elims:
                d.create_line(min(w - 23, split), 170, w - 22, 170, fill=INFO, width=8,
                              capstyle="round", tags="dnt")
        d.create_text(22, 184, text=f"Team wipes {wipes}", anchor="w", fill=AMBER, tags="dnt",
                      font=(FONT["ui"], 8, "bold"))
        d.create_text(w - 22, 184, text=f"Eliminated {elims}", anchor="e", fill=INFO, tags="dnt",
                      font=(FONT["ui"], 8, "bold"))

        if self._changed("recent", len(durs)):
            self.draw_recent()

    def draw_recent(self):
        c, w = self.recent, self.right_w
        c.delete("rec")
        for x, label in ((22, "MATCH"), (76, "TYPE"), (170, "LENGTH"), (262, "VS AVERAGE")):
            c.create_text(x, 58, text=label, anchor="w", fill=FAINT,
                          font=(FONT["ui"], 8, "bold"), tags="rec")
        matches = STATE["matches"]
        if not matches:
            c.create_text(w / 2, 124, text="Nothing yet.\nMatches show up after your first elimination.",
                          fill=FAINT, justify="center", font=(FONT["ui"], 10), tags="rec")
            return
        durs = [m[0] for m in matches]
        avg = sum(durs) / len(durs)
        total = len(matches)
        for i, (dur, ts, kind) in enumerate(reversed(matches[-6:])):
            y = 82 + i * 20
            diff = dur - avg
            if abs(diff) < 1:
                dtxt, dcol = "on average", DIM
            elif diff > 0:
                dtxt, dcol = f"+{fmt_short(diff)}", GOOD
            else:
                dtxt, dcol = f"-{fmt_short(-diff)}", BAD
            tcol = AMBER if kind == "wipe" else INFO
            for x, txt, col, bold in ((22, f"#{total - i}", CREAM, True),
                                      (76, "Wipe" if kind == "wipe" else "Eliminated", tcol, False),
                                      (170, fmt_short(dur), CREAM, False), (262, dtxt, dcol, False)):
                c.create_text(x, y, text=txt, anchor="w", fill=col, tags="rec",
                              font=(FONT["num"], 10, "bold" if bold else "normal"))

    def update_alltime_stats(self, lv):
        c, rows = self.life_card, self.life_rows
        vals = {
            "sessions": str(lv["sessions"]), "runtime": fmt_time(lv["runtime"]),
            "deaths": str(lv["deaths"]), "kinds": f"{lv['wipes']} / {lv['elims']}",
            "rate": f"{lv['rate']:.1f} / hr" if lv["rate"] else "--",
            "fastest": fmt_short(lv["fastest"]), "longest": fmt_short(lv["longest"]),
            "best_session": str(lv["best_session"]),
            "longest_session": fmt_short(lv["longest_session"]) if lv["longest_session"] else "--",
            "scans": str(lv["scans"]), "clicks": str(lv["clicks"]),
            "first": fmt_date(lv["first"]), "last": "now" if lv["live"] else fmt_date(lv["last"]),
        }
        for k, v in vals.items():
            c.itemconfig(rows[k], text=v)

        now = time.time()
        days = [time.localtime(now - i * 86400) for i in range(6, -1, -1)]
        keys = [time.strftime("%Y-%m-%d", d) for d in days]
        values = [LIFE["daily"].get(k, 0) for k in keys]
        if self._changed("daily", tuple(values)):
            draw_bars(self.daily, (46, 62, self.right_w - 22, 190 - 34), values,
                      [time.strftime("%a", d) for d in days], "dl", lambda v: f"{int(v)}",
                      show_values=True, empty="No eliminations in the last 7 days.")
        hours = list(LIFE["hours"])
        if self._changed("hours", tuple(hours)):
            names = ["12a", "3a", "6a", "9a", "12p", "3p", "6p", "9p"]
            labels = [names[h // 3] if h % 3 == 0 else "" for h in range(24)]
            draw_bars(self.hours, (46, 62, self.right_w - 22, 200 - 34), hours, labels, "hr",
                      lambda v: f"{int(v)}", empty="Hourly stats appear after your first elimination.")

    def update_badges(self, lv):
        idx, name, nxt, frac, nxt_name = rank_info(lv["deaths"])
        rc = self.rank_card
        rc.itemconfig(self.rank_icon, text=RANK_ICONS[idx])
        rc.itemconfig(self.rank_title, text=name)
        x0, x1 = self.rank_bar
        rc.coords(self.rank_progress, x0, 86, x0 + max(1, (x1 - x0) * frac), 86)
        rc.itemconfig(self.rank_text, text=(
            f"{lv['deaths']} / {nxt} eliminations to become {nxt_name}" if nxt
            else "Top rank reached. You are the Pirate King."))
        for badge, (icon, title, desc, metric, goal) in zip(self.badges, ACHIEVEMENTS):
            v = lv[metric] or 0
            progress = (f"{fmt_short(v)} / {fmt_short(goal)}" if metric == "runtime"
                        else f"{int(v)} / {goal}")
            badge.draw(min(1.0, v / goal), v >= goal, progress)


if __name__ == "__main__":
    App().mainloop()
