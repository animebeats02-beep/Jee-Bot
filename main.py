import os
import json
import threading
import time
import random
from datetime import datetime, timedelta, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

DATA_DIR = "/tmp/data" if os.getenv("RENDER") else "data"
os.makedirs(DATA_DIR, exist_ok=True)
lock = threading.Lock()

# ---------- memory ----------
def load_json(name, default):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        save_json(name, default)
        return default
    with open(path, "r") as f:
        return json.load(f)

def save_json(name, data):
    path = os.path.join(DATA_DIR, f"{name}.json")
    with lock:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

def init_syllabus():
    chapters = {
        "Physics_Simple_Harmonic_Motion": {"subject":"Physics","chapter":"Simple Harmonic Motion","class":12,"status":"not_started","sub_subject":"Physics"},
        "Physics_Geometrical_Optics": {"subject":"Physics","chapter":"Geometrical Optics","class":12,"status":"not_started","sub_subject":"Physics"},
        "Physics_Electrostatics": {"subject":"Physics","chapter":"Electrostatics","class":12,"status":"going_on","sub_subject":"Physics"},
        "Physics_Gravitation": {"subject":"Physics","chapter":"Gravitation","class":12,"status":"going_on","sub_subject":"Physics"},
        "Physics_Current_Electricity": {"subject":"Physics","chapter":"Current Electricity","class":12,"status":"revision_needed","sub_subject":"Physics"},
        "Chemistry_Solid_State": {"subject":"Chemistry","chapter":"Solid State","class":12,"status":"revision_needed","sub_subject":"Physical"},
        "Chemistry_Liquid_Solutions": {"subject":"Chemistry","chapter":"Liquid Solutions","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Chemical_Kinetics": {"subject":"Chemistry","chapter":"Chemical Kinetics","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Thermodynamics": {"subject":"Chemistry","chapter":"Thermodynamics","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Haloalkanes": {"subject":"Chemistry","chapter":"Haloalkanes","class":12,"status":"going_on","sub_subject":"Organic"},
        "Chemistry_Coordination_Compounds": {"subject":"Chemistry","chapter":"Coordination Compounds","class":12,"status":"going_on","sub_subject":"Inorganic"},
        "Maths_Functions_Relations": {"subject":"Maths","chapter":"Functions & Relations","class":12,"status":"completed","sub_subject":"Maths"},
        "Maths_ITF": {"subject":"Maths","chapter":"ITF","class":12,"status":"completed","sub_subject":"Maths"},
        "Maths_Limits": {"subject":"Maths","chapter":"Limits","class":12,"status":"going_on","sub_subject":"Maths"},
        "Maths_Continuity": {"subject":"Maths","chapter":"Continuity","class":12,"status":"going_on","sub_subject":"Maths"},
        "Maths_Differentiability": {"subject":"Maths","chapter":"Differentiability","class":12,"status":"going_on","sub_subject":"Maths"},
        "Maths_MOD": {"subject":"Maths","chapter":"MOD","class":12,"status":"going_on","sub_subject":"Maths"},
        "Maths_Tangent_Normal": {"subject":"Maths","chapter":"Tangent & Normal","class":12,"status":"going_on","sub_subject":"Maths"},
        "Maths_Monotonicity": {"subject":"Maths","chapter":"Monotonicity Max/Min","class":12,"status":"weak","sub_subject":"Maths"},
        "Maths_Integral_Calculus": {"subject":"Maths","chapter":"Integral Calculus","class":12,"status":"going_on","sub_subject":"Maths"},
    }
    # 11th defaults
    orig_physics = ["Units & Measurements","Motion in Straight Line","Motion in Plane","Laws of Motion","Work Energy Power","Rotational Motion","Gravitation","Mechanical Properties Solids","Mechanical Properties Fluids","Thermal Properties","Thermodynamics","Kinetic Theory","Oscillations","Waves"]
    orig_chem = ["Some Basic Concepts","Structure Atom","States Matter","Thermodynamics","Equilibrium","Redox Reactions","Electrochemistry","Chemical Kinetics","Surface Chemistry","Classification Periodicity","Hydrogen","s-Block","p-Block 11","Environmental","Metallurgy","p-Block 12","d & f Block","Chemical Bonding","Organic Basic Principles","Hydrocarbons","Haloalkanes","Alcohols Phenols Ethers","Aldehydes Ketones","Amines","Biomolecules","Polymers","Chemistry Everyday"]
    orig_maths = ["Sets","Relations Functions","Trigonometric Functions","Mathematical Induction","Complex Numbers","Linear Inequalities","Permutations Combinations","Binomial Theorem","Sequences Series","Straight Lines","Conic Sections","3D Geometry","Limits Derivatives","Mathematical Reasoning","Statistics","Probability 11"]
    for ch in orig_physics:
        key = f"Physics_{ch.replace(' ','_')}"
        if key not in chapters:
            chapters[key] = {"subject":"Physics","chapter":ch,"class":11,"status":"not_started","sub_subject":"Physics"}
    for ch in orig_chem:
        key = f"Chemistry_{ch.replace(' ','_')}"
        if key not in chapters:
            sub = "Physical"
            if ch in ["Chemical Bonding","Organic Basic Principles","Hydrocarbons","Haloalkanes","Alcohols Phenols Ethers","Aldehydes Ketones","Amines","Biomolecules","Polymers","Chemistry Everyday"]:
                sub = "Organic"
            elif ch in ["Classification Periodicity","Hydrogen","s-Block","p-Block 11","Environmental","Metallurgy","p-Block 12","d & f Block"]:
                sub = "Inorganic"
            chapters[key] = {"subject":"Chemistry","chapter":ch,"class":11,"status":"not_started","sub_subject":sub}
    for ch in orig_maths:
        key = f"Maths_{ch.replace(' ','_')}"
        if key not in chapters:
            chapters[key] = {"subject":"Maths","chapter":ch,"class":11,"status":"not_started","sub_subject":"Maths"}
    return chapters

memory = {
    "backlog": load_json("backlog", {"tasks": []}),
    "today": load_json("today", {"date": "", "plan": [], "generated": False}),
    "schedule": load_json("schedule", {"wake_up": "07:00", "sleep": "22:00", "study_hours": 8,
                                       "weekly_timetable": "", "last_updated": ""}),
    "progress": load_json("progress", {"logs": []}),
    "stats": load_json("stats", {
        "productivity": [], "consistency": 0, "fatigue_flags": 0,
        "streak": 0, "longest_streak": 0, "last_study_date": "",
        "total_study_days": 0, "total_study_hours": 0.0,
        "weekly_hours": {}, "mood_log": {}
    }),
    "syllabus": load_json("syllabus", {"chapters": init_syllabus()}),
    "tests": load_json("tests", {
        "upcoming": [], "past": [],
        "next_test_date": None,
        "next_test_11th_syllabus": [],
        "test_asked_today": False
    }),
    "homework": load_json("homework", {"date": "", "tasks": []}),
    "chapter_exercises": load_json("chapter_exercises", {"exercises": {}}),
}

RECOMMENDED_SLEEP = 7.5
EXERCISE_TIMES = {"O1":5,"O2":7,"O3":7,"O4":5,"JM":5,"JA":8,"Gyanoday":10}
MATHS_DEFAULTS = {"O1":30,"O2":20,"O3":20,"O4":10,"JM":30,"JA":20}

# ---------- helpers ----------
def get_ongoing_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v["status"] == "going_on"]

def get_weak_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v["status"] in ("weak","revision_needed")]

def compute_priority(task, test_chapters, ongoing_chapters):
    score = 50
    if task.get("test_link") in [t["name"] for t in memory["tests"]["upcoming"]]:
        score += 30
    chapter_key = task.get("chapter_key","")
    if chapter_key in test_chapters:
        score += 30
    if chapter_key in ongoing_chapters:
        score += 20
    if chapter_key in get_weak_chapters():
        score += 25
    if task.get("source") in ("test","AI"):
        score += 15
    return score

def should_skip_task(task, skip_keywords):
    if not skip_keywords: return False
    text = (task.get("id","") + " " + task.get("description","")).lower()
    for kw in skip_keywords:
        if kw.strip().lower() in text:
            return True
    return False

def estimate_backlog_days(daily_hours):
    pending = [t for t in memory["backlog"]["tasks"] if t["status"]!="done"]
    if not pending: return 0
    total_min = sum(t.get("estimated_time",45) for t in pending)
    if daily_hours<=0: daily_hours=1
    return round(total_min/(daily_hours*60),1)

def estimate_homework_time(exercise_counts):
    total = 0
    for ex, count in exercise_counts.items():
        total += count * EXERCISE_TIMES.get(ex,3)
    return max(total,10)

def update_streak_and_hours(study_hours, mood=None, sleep_hours=None):
    today = date.today().isoformat()
    stats = memory["stats"]
    last = stats.get("last_study_date","")
    if last == today: return
    if last:
        last_date = date.fromisoformat(last)
        if (date.today() - last_date).days == 1:
            stats["streak"] += 1
        else:
            stats["streak"] = 1
    else:
        stats["streak"] = 1
    if stats["streak"] > stats.get("longest_streak",0):
        stats["longest_streak"] = stats["streak"]
    stats["last_study_date"] = today
    stats["total_study_days"] = stats.get("total_study_days",0) + 1
    stats["total_study_hours"] = stats.get("total_study_hours",0) + study_hours
    week_key = date.today().strftime("%Y-W%W")
    weekly = stats.get("weekly_hours",{})
    weekly[week_key] = weekly.get(week_key,0) + study_hours
    stats["weekly_hours"] = weekly
    if mood is not None:
        mood_log = stats.get("mood_log",{})
        mood_log[today] = {"mood": mood, "sleep": sleep_hours}
        stats["mood_log"] = mood_log
    save_json("stats", stats)

def generate_revision_tasks_for_11th():
    next_test_date = memory["tests"].get("next_test_date")
    if not next_test_date: return []
    try:
        test_date = date.fromisoformat(next_test_date)
    except:
        return []
    days_left = (test_date - date.today()).days
    if days_left < 0: return []
    chapters_11th = memory["tests"].get("next_test_11th_syllabus", [])
    if not chapters_11th: return []
    chapters_per_day = max(1, len(chapters_11th) // max(days_left, 1))
    start_idx = (date.today() - (test_date - timedelta(days=days_left))).days
    assigned = []
    for i in range(chapters_per_day):
        idx = (start_idx + i) % len(chapters_11th)
        assigned.append(chapters_11th[idx])
    tasks = []
    for ch_key in assigned:
        chap = memory["syllabus"]["chapters"].get(ch_key)
        if chap:
            tasks.append({
                "id": f"rev11_{ch_key}_{datetime.now().strftime('%Y%m%d')}",
                "subject": chap["subject"],
                "chapter": chap["chapter"],
                "type": "11th Revision",
                "estimated_time": 30,
                "source": "test",
                "status": "pending",
                "chapter_key": ch_key,
                "priority_score": 80
            })
    return tasks

def generate_smart_revision_tasks(remaining_minutes):
    if remaining_minutes <= 0: return []
    tasks = []
    weak = get_weak_chapters()
    for ch_key in weak:
        if remaining_minutes <= 0: break
        chap = memory["syllabus"]["chapters"].get(ch_key)
        if chap:
            tasks.append({
                "id": f"revweak_{ch_key}_{datetime.now().strftime('%Y%m%d%H%M')}",
                "subject": chap["subject"],
                "chapter": chap["chapter"],
                "type": "Revision (Weak)",
                "estimated_time": 30,
                "source": "AI",
                "status": "pending",
                "chapter_key": ch_key,
                "priority_score": 90
            })
            remaining_minutes -= 30
    ongoing = get_ongoing_chapters()
    for ch_key in ongoing:
        if remaining_minutes <= 0: break
        chap = memory["syllabus"]["chapters"].get(ch_key)
        if chap:
            tasks.append({
                "id": f"revongo_{ch_key}_{datetime.now().strftime('%Y%m%d%H%M')}",
                "subject": chap["subject"],
                "chapter": chap["chapter"],
                "type": "Revision (Ongoing)",
                "estimated_time": 30,
                "source": "AI",
                "status": "pending",
                "chapter_key": ch_key,
                "priority_score": 70
            })
            remaining_minutes -= 30
    return tasks

def generate_todo_list(study_hours_override=None, skip_keywords=None,
                       test_chapters=None, ongoing_chapters=None):
    study_mins = (study_hours_override if study_hours_override else memory["schedule"]["study_hours"]) * 60
    hw = memory["homework"]["tasks"] if memory["homework"]["date"] == datetime.now().strftime("%Y-%m-%d") else []
    backlog = [t for t in memory["backlog"]["tasks"] if t["status"]!="done"]
    if skip_keywords:
        backlog = [t for t in backlog if not should_skip_task(t, skip_keywords)]
    if test_chapters is None: test_chapters = []
    if ongoing_chapters is None: ongoing_chapters = get_ongoing_chapters()

    revision_11th = generate_revision_tasks_for_11th()
    all_mandatory = revision_11th + hw + backlog
    for t in all_mandatory:
        t["priority_score"] = compute_priority(t, test_chapters, ongoing_chapters)
    all_mandatory.sort(key=lambda x: x["priority_score"], reverse=True)

    total_mandatory_min = sum(t["estimated_time"] for t in all_mandatory)
    remaining = study_mins - total_mandatory_min
    smart_rev = []
    if remaining > 30:
        smart_rev = generate_smart_revision_tasks(remaining)

    full_list = all_mandatory + smart_rev
    total_min = sum(t["estimated_time"] for t in full_list)

    memory["today"] = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "todo": full_list,
        "generated": True
    }
    save_json("today", memory["today"])
    return full_list, total_min, study_mins

# ---------- AI ----------
def ask_ai(prompt, chat_history=None):
    if not GROQ_KEY: return "AI not available."
    time.sleep(1)
    weak = get_weak_chapters()[:5]
    backlog_count = len([t for t in memory["backlog"]["tasks"] if t["status"]!="done"])
    tests = [t["name"] for t in memory["tests"]["upcoming"]]
    context = f"""
[SYSTEM MEMORY]
Syllabus progress: {sum(1 for v in memory['syllabus']['chapters'].values() if v['status'] in ('completed','going_on'))} chapters completed/ongoing.
Weak topics: {weak}.
Backlog tasks: {backlog_count}.
Upcoming tests: {tests}.
Today's plan: {'Generated' if memory['today']['generated'] else 'Not yet'}.
"""
    system_msg = "You are JEE Study OS, a strict Kota JEE coach. Answer using the memory context."
    messages = [{"role":"system","content": system_msg + "\n\n" + context}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role":"user","content": prompt})
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"}
    payload = {"model": GROQ_MODEL, "messages": messages, "temperature":0.7, "max_tokens":500}
    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code==200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            return f"AI error: {resp.status_code}"
    except Exception as e:
        return f"AI error: {str(e)}"

MOTIVATIONAL_QUOTES = [
    "“Success is no accident. It is hard work, perseverance, learning, studying, sacrifice and most of all, love of what you are doing.” – Pelé",
    "“Don't watch the clock; do what it does. Keep going.” – Sam Levenson",
    "“The difference between ordinary and extraordinary is that little extra.” – Jimmy Johnson",
    "“There is no substitute for hard work.” – Thomas Edison",
    "“I find that the harder I work, the more luck I seem to have.” – Thomas Jefferson",
    "“It's not about how bad you want it. It's about how hard you're willing to work for it.” – Unknown",
    "“Success is the sum of small efforts, repeated day in and day out.” – Robert Collier",
    "“You don't have to be great to start, but you have to start to be great.” – Zig Ziglar",
]

# ---------- shared homework entry state (used by both check‑in and /start_day) ----------
homework_states: Dict[int, Dict[str, Any]] = {}

def start_homework_entry(chat_id, context: ContextTypes.DEFAULT_TYPE):
    homework_states[chat_id] = {
        "homework": [],
        "skip_keywords": [],
        "current_chapter": None,
        "pending_exercise_types": [],
        "current_exercise_counts": {},
        "waiting_for_skip": False,
        "final_callback": None,  # will be called when done
    }

async def handle_homework_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in homework_states:
        return False
    state = homework_states[chat_id]
    text = update.message.text.strip()

    if state.get("waiting_for_skip"):
        # We are in the skip prompt
        if text.lower() == "none":
            state["skip_keywords"] = []
        else:
            state["skip_keywords"] = [kw.strip() for kw in text.split(",") if kw.strip()]
        # finalize
        await finalize_homework_and_generate(update, context)
        return True

    # homework chapter entry
    if text.lower() == "done":
        state["waiting_for_skip"] = True
        if state["homework"]:
            task_list = "\n".join(f"{t['subject']} - {t['chapter']} ({t['type']})" for t in state["homework"])
            await update.message.reply_text(f"Your homework:\n{task_list}")
        else:
            await update.message.reply_text("No homework recorded.")
        await update.message.reply_text("🙅 Any homework tasks to skip? Send keywords/comma‑separated or type `none`.")
        return True

    # chapter key entry
    chapter_key = text.strip()
    if chapter_key not in memory["syllabus"]["chapters"]:
        # auto-create new chapter
        parts = chapter_key.split('_', 1)
        if len(parts) == 2 and parts[0] in ("Physics","Chemistry","Maths"):
            subject = parts[0]
            chapter = parts[1].replace('_',' ')
            sub = subject
            if subject == "Chemistry":
                if any(w in chapter for w in ["Haloalkane","Alcohol","Aldehyde","Ketone","Amine","Polymer","Biomolecule","Ether","Organic","Hydrocarbon"]):
                    sub = "Organic"
                elif any(w in chapter for w in ["Coordination","d & f","p-Block","s-Block","Metallurgy","Hydrogen"]):
                    sub = "Inorganic"
                else:
                    sub = "Physical"
            memory["syllabus"]["chapters"][chapter_key] = {
                "subject": subject, "chapter": chapter,
                "class": 12, "status": "not_started", "sub_subject": sub
            }
            save_json("syllabus", memory["syllabus"])
            await update.message.reply_text(f"📌 New chapter added: {subject} - {chapter}")
        else:
            await update.message.reply_text("❌ Invalid chapter key. Format `Subject_Chapter`.")
            return True

    chapter_info = memory["syllabus"]["chapters"][chapter_key]
    subject = chapter_info["subject"]
    state["current_chapter"] = chapter_key
    state["state"] = "waiting_exercise_types"
    await update.message.reply_text(
        f"📋 {subject} - {chapter_info['chapter']}: exercise types? (O1,O2,O3,O4,JM,JA,Gyanoday)"
    )
    return True

async def handle_exercise_types(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = homework_states.get(chat_id)
    if not state or state.get("waiting_for_skip"):
        return False
    text = update.message.text.strip()
    if text.lower() == "done":
        # skip this chapter
        state["state"] = None
        await update.message.reply_text("Chapter skipped. Next chapter key or `done`.")
        return True
    types = [t.strip() for t in text.split(",") if t.strip()]
    valid_types = [t for t in types if t in EXERCISE_TIMES]
    if not valid_types:
        await update.message.reply_text("No valid types. Use O1,O2,O3,O4,JM,JA,Gyanoday.")
        return True
    state["pending_exercise_types"] = valid_types
    state["current_exercise_counts"] = {}
    chapter_key = state["current_chapter"]
    subject = memory["syllabus"]["chapters"][chapter_key]["subject"]
    if subject == "Maths":
        for ex in valid_types:
            state["current_exercise_counts"][ex] = MATHS_DEFAULTS.get(ex,0)
        msg = "🔢 Default Maths counts:\n" + "\n".join(f"{k}: {v}" for k,v in state["current_exercise_counts"].items())
        msg += "\nReply `ok` if correct, or send `O2=25`."
        await update.message.reply_text(msg)
        state["state"] = "waiting_exercise_counts"
    else:
        await update.message.reply_text(f"How many questions in **{valid_types[0]}**? (send a number)")
        state["state"] = "waiting_exercise_counts"
    return True

async def handle_exercise_counts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = homework_states.get(chat_id)
    if not state or state.get("waiting_for_skip"):
        return False
    text = update.message.text.strip()
    if text.lower() == "ok":
        chapter_key = state["current_chapter"]
        save_chapter_exercise_counts(chapter_key, state["current_exercise_counts"])
        total_time = estimate_homework_time(state["current_exercise_counts"])
        task = {
            "id": str(int(datetime.timestamp(datetime.now()))),
            "subject": memory["syllabus"]["chapters"][chapter_key]["subject"],
            "chapter": memory["syllabus"]["chapters"][chapter_key]["chapter"],
            "type": "mixed",
            "estimated_time": total_time,
            "source": "coaching",
            "status": "pending",
            "chapter_key": chapter_key,
            "exercise_counts": state["current_exercise_counts"]
        }
        state["homework"].append(task)
        state["state"] = None
        await update.message.reply_text(f"✅ Added homework for {task['subject']} - {task['chapter']} (est. {total_time} min). Next chapter key or `done`.")
        return True

    if "=" in text:
        parts = text.split("=")
        ex = parts[0].strip()
        try:
            count = int(parts[1])
            if ex in state["current_exercise_counts"]:
                state["current_exercise_counts"][ex] = count
                await update.message.reply_text(f"Updated {ex} to {count}. Send another or `ok`.")
            else:
                await update.message.reply_text("Type not in list. Send `ok` to finish.")
        except ValueError:
            await update.message.reply_text("Invalid number. Use `O2=25`.")
        return True

    try:
        count = int(text)
        remaining_types = [t for t in state["pending_exercise_types"] if t not in state["current_exercise_counts"]]
        if not remaining_types:
            await update.message.reply_text("All types have counts. Reply `ok` to finalize.")
            return True
        current_type = remaining_types[0]
        state["current_exercise_counts"][current_type] = count
        next_types = [t for t in state["pending_exercise_types"] if t not in state["current_exercise_counts"]]
        if next_types:
            await update.message.reply_text(f"How many questions in **{next_types[0]}**? (send a number)")
        else:
            await update.message.reply_text("All types entered. Reply `ok` to finalize this chapter.")
        return True
    except ValueError:
        await update.message.reply_text("Please enter a number, or `ok` to finish.")
        return True

async def finalize_homework_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = homework_states.pop(chat_id, None)
    if not state:
        return
    # save homework
    memory["homework"] = {"date": datetime.now().strftime("%Y-%m-%d"), "tasks": state["homework"]}
    save_json("homework", memory["homework"])
    # generate to-do
    todo, total_est, avail_mins = generate_todo_list(skip_keywords=state["skip_keywords"])
    daily_hrs = memory["schedule"]["study_hours"]
    days = estimate_backlog_days(daily_hrs)
    update_streak_and_hours(daily_hrs)
    # time windows
    wake = memory["schedule"]["wake_up"]
    try:
        wake_dt = datetime.strptime(wake, "%H:%M")
        wake_hours = wake_dt.hour + wake_dt.minute/60
    except:
        wake_hours = 7.0
    sleep_time = memory["schedule"]["sleep"]
    try:
        sleep_dt = datetime.strptime(sleep_time, "%H:%M")
        sleep_hours = sleep_dt.hour + sleep_dt.minute/60
    except:
        sleep_hours = 22.0
    morning_hours = max(0, 12 - wake_hours)
    evening_hours = max(0, sleep_hours - 20)
    total_free = round(morning_hours + evening_hours, 1)

    def emoji(score):
        if score>=80: return "🔴"
        if score>=60: return "🟠"
        if score>=40: return "🟡"
        return "🟢"

    msg = "📅 *Today's To‑Do List* (Classes: 12 PM – 8 PM)\n"
    msg += f"🕒 Free hours: ~{total_free}h (morning {morning_hours}h + evening {evening_hours}h)\n"
    msg += f"⏱️ Total task time: {total_est} min ({total_est/60:.1f}h)\n"
    if total_est > avail_mins:
        msg += "⚠️ Task time exceeds available study time.\n"
    msg += "\n"
    for task in todo:
        msg += f"{emoji(task.get('priority_score',50))} {task['subject']} - {task['chapter']} ({task['type']}) – {task['estimated_time']} min\n"
    msg += f"\n⏳ *Backlog estimate:* ~{days} day(s) at {daily_hrs}h/day."
    await update.message.reply_text(msg, parse_mode='Markdown')

# ---------- morning check‑in ----------
checkin_states: Dict[int, Dict[str, Any]] = {}

async def start_morning_checkin(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    checkin_states[chat_id] = {
        "state": "waiting_sleep",
        "sleep": None,
        "wake_time": None,
        "mood": None,
        "study_hours": None,
    }
    await context.bot.send_message(chat_id, "🌅 Good morning! How many hours did you sleep last night? (e.g., 6.5)")

async def handle_checkin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in checkin_states:
        return False
    state = checkin_states[chat_id]
    text = update.message.text.strip()

    if state["state"] == "waiting_sleep":
        try:
            hours = float(text)
            state["sleep"] = hours
            state["state"] = "waiting_wake"
            await update.message.reply_text("⏰ What time did you wake up today? (HH:MM, e.g., 06:45)")
        except ValueError:
            await update.message.reply_text("Please enter a number (e.g., 7).")
        return True
    elif state["state"] == "waiting_wake":
        try:
            datetime.strptime(text, "%H:%M")
            state["wake_time"] = text
            state["state"] = "waiting_mood"
            await update.message.reply_text("😊 How is your mood today? (1‑10)")
        except ValueError:
            await update.message.reply_text("Please enter a valid time (HH:MM).")
        return True
    elif state["state"] == "waiting_mood":
        try:
            mood = int(text)
            if 1 <= mood <= 10:
                state["mood"] = mood
                state["state"] = "waiting_study_hours"
                await update.message.reply_text("📘 How many hours can you study today? (e.g., 8)")
            else:
                await update.message.reply_text("1‑10 please.")
        except ValueError:
            await update.message.reply_text("Number (1‑10).")
        return True
    elif state["state"] == "waiting_study_hours":
        try:
            hours = float(text)
            state["study_hours"] = hours
            # Transition to homework entry
            del checkin_states[chat_id]  # clear check-in state
            start_homework_entry(chat_id, context)
            await update.message.reply_text("📝 Enter today's homework chapter keys (e.g., `Physics_Electrostatics`) or type `done`.\nIf it's a new chapter, I'll add it automatically.")
        except ValueError:
            await update.message.reply_text("Number (e.g., 8).")
        return True
    return False

# ---------- other commands ----------
async def start(update, context):
    await update.message.reply_text("🚀 JEE Study OS ready! /help for commands.")
    context.bot_data["user_chat_id"] = update.effective_chat.id

async def help_cmd(update, context):
    text = """
📚 JEE Study OS Commands

🌅 Morning Check‑in: automatic at wake‑up time.
/start_day – Manually enter today's homework and get your to‑do list.

💬 /chat – AI chat (/stop to end)
/ask <q> – One‑shot AI
/motivate – Random quote
/stats – Streak & hours
/week_report – This week's summary
/set_test – Monthly test date & 11th syllabus
/view_tests – Show upcoming test

📌 Manual:
/set_schedule – wake|sleep|study_hours
/add_backlog – Add backlog
/view_backlog – Show backlog
/update_syllabus – Change chapter status
/view_syllabus – Full progress
/complete_task – Mark done
/week_update – Timetable entry
"""
    await update.message.reply_text(text)

async def chat_cmd(update, context):
    context.user_data['mode'] = 'chat'
    context.user_data['chat_history'] = []
    await update.message.reply_text("💬 Chat mode. /stop to end.")

async def stop_cmd(update, context):
    if context.user_data.get('mode') == 'chat':
        context.user_data['mode'] = None
        context.user_data.pop('chat_history', None)
        await update.message.reply_text("Chat ended.")
    else:
        await update.message.reply_text("No active chat.")

async def motivate_cmd(update, context):
    await update.message.reply_text(random.choice(MOTIVATIONAL_QUOTES))

async def stats_cmd(update, context):
    s = memory["stats"]
    streak = s.get("streak",0)
    longest = s.get("longest_streak",0)
    total_days = s.get("total_study_days",0)
    total_hours = s.get("total_study_hours",0)
    avg_hours = round(total_hours/total_days,1) if total_days else 0
    today_str = datetime.now().strftime("%Y-%m-%d")
    done_today = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(today_str))
    mood = s.get("mood_log",{}).get(today_str,{}).get("mood","?")
    msg = (
        f"📊 *Your Stats*\n"
        f"🔥 Streak: {streak} days | 🏆 Best: {longest} days\n"
        f"📅 Total days: {total_days} | ⏱️ Total hrs: {total_hours}\n"
        f"📈 Avg hrs/day: {avg_hours} | ✅ Today: {done_today} tasks\n"
        f"😊 Today's mood: {mood}/10"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def week_report_cmd(update, context):
    today = date.today()
    week_key = today.strftime("%Y-W%W")
    hours = memory["stats"].get("weekly_hours",{}).get(week_key,0)
    week_logs = [l for l in memory["progress"]["logs"] if (today - date.fromisoformat(l["timestamp"][:10])).days < 7]
    tasks_done = len(week_logs)
    msg = f"📅 *This Week* – Hours: {hours} | Tasks: {tasks_done} | Streak: {memory['stats']['streak']}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_tests_cmd(update, context):
    nxt = memory["tests"].get("next_test_date")
    if nxt:
        chaps = memory["tests"].get("next_test_11th_syllabus",[])
        msg = f"📅 Next test: {nxt}\n11th syllabus: {', '.join(chaps[:10])}{'...' if len(chaps)>10 else ''}"
    else:
        msg = "No upcoming test set. Use /set_test."
    await update.message.reply_text(msg)

async def set_schedule_cmd(update, context):
    await update.message.reply_text("Send: `wake_up|sleep|study_hours`")
    context.user_data['mode'] = 'schedule'

async def add_backlog_cmd(update, context):
    await update.message.reply_text("Send backlog tasks like `Physics|Gravitation|Theory|90` – type `done`.")
    context.user_data['mode'] = 'backlog'
    context.user_data['temp'] = []

async def add_test_cmd(update, context):
    await update.message.reply_text("Send test info: `Name|YYYY-MM-DD|chapter_keys`")
    context.user_data['mode'] = 'test'

async def update_syllabus_cmd(update, context):
    await update.message.reply_text("Send: `chapter_key|status` (e.g., `Physics_Electrostatics|going_on`)")
    context.user_data['mode'] = 'syllabus'

async def view_syllabus(update, context):
    chaps = memory["syllabus"]["chapters"]
    weak = get_weak_chapters()
    ongoing = get_ongoing_chapters()
    msg = "📖 *Syllabus*\n" + "\n".join(
        f"{'🟢' if v['status'] in ('completed','going_on') else '🔴' if v['status']=='weak' else '⚪'} {v['subject']} ({v.get('sub_subject','')}) - {v['chapter']} [{v['status']}]"
        for k,v in list(chaps.items())[:30])
    if ongoing:
        msg += "\n📌 Current: " + ", ".join(ongoing[:5])
    if weak:
        msg += "\n⚠️ Weak: " + ", ".join(weak[:5])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"]["tasks"] if t["status"]!="done"]
    if not tasks:
        await update.message.reply_text("No pending backlog.")
        return
    msg = "📋 *Backlog*\n" + "\n".join(f"• {t['subject']} {t['chapter']} ({t['type']}) est.{t['estimated_time']}min" for t in tasks)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def complete_task_cmd(update, context):
    await update.message.reply_text("Send task ID (from /view_plan) or description keyword.")
    context.user_data['mode'] = 'complete'

async def ask_cmd(update, context):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    reply = ask_ai(question)
    await update.message.reply_text(reply)

async def week_update_cmd(update, context):
    await update.message.reply_text("Send your weekly class timetable (any format) or type `skip`.")
    context.user_data['mode'] = 'weekly'

async def start_day(update, context):
    chat_id = update.effective_chat.id
    # Start homework entry flow
    start_homework_entry(chat_id, context)
    await update.message.reply_text("📝 Enter today's homework chapter keys (e.g., `Physics_Electrostatics`) or type `done`.\nIf it's a new chapter, I'll add it automatically.")

async def view_plan(update, context):
    today = memory["today"]
    if not today.get("generated"):
        await update.message.reply_text("No to‑do list yet. Use /start_day or morning check‑in.")
        return
    todo = today.get("todo", [])
    msg = "📅 *Today's To‑Do*\n" + "\n".join(
        f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']} min" for t in todo
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ---------- message router ----------
async def handle_message(update, context):
    chat_id = update.effective_chat.id
    if update.message.text.startswith('/'):
        return
    # 1. check if in chat mode
    if context.user_data.get('mode') == 'chat':
        history = context.user_data.get('chat_history', [])
        user_msg = update.message.text
        history.append({"role":"user","content": user_msg})
        reply = ask_ai(user_msg, chat_history=history)
        history.append({"role":"assistant","content": reply})
        context.user_data['chat_history'] = history
        await update.message.reply_text(reply)
        return
    # 2. check if in set_next_test mode
    if context.user_data.get('mode') == 'set_next_test':
        await handle_set_test(update, context)
        return
    # 3. morning check‑in
    if chat_id in checkin_states:
        handled = await handle_checkin_message(update, context)
        if handled:
            return
    # 4. homework entry (shared state)
    if chat_id in homework_states:
        state = homework_states[chat_id]
        if state.get("waiting_for_skip"):
            await handle_homework_entry(update, context)
            return
        if state.get("state") == "waiting_exercise_types":
            await handle_exercise_types(update, context)
            return
        if state.get("state") == "waiting_exercise_counts":
            await handle_exercise_counts(update, context)
            return
        # default: assume chapter entry
        await handle_homework_entry(update, context)
        return
    # 5. PDF mode
    if context.bot_data.get("expecting_pdf",{}).get(chat_id):
        await handle_document(update, context)
        return
    # 6. other modes (backlog, test, etc.)
    mode = context.user_data.get('mode')
    text = update.message.text
    if mode == 'backlog':
        if text.lower() == 'done':
            for t in context.user_data['temp']:
                t.setdefault("chapter_key", f"{t.get('subject','')}_{t.get('chapter','').replace(' ','_')}")
                memory["backlog"]["tasks"].append(t)
            save_json("backlog", memory["backlog"])
            context.user_data['mode'] = None
            await update.message.reply_text("Backlog updated.")
        else:
            parts = text.split('|')
            if len(parts) >= 4:
                task = {
                    "id": str(int(datetime.timestamp(datetime.now()))),
                    "subject": parts[0], "chapter": parts[1], "type": parts[2],
                    "estimated_time": int(parts[3]),
                    "test_link": parts[4] if len(parts)>4 else "",
                    "status": "pending", "source": "self",
                    "chapter_key": f"{parts[0]}_{parts[1].replace(' ','_')}"
                }
                context.user_data['temp'].append(task)
                await update.message.reply_text("Added. Next or 'done'.")
        return
    elif mode == 'test':
        if text.lower() == 'done':
            context.user_data['mode'] = None
        else:
            parts = text.split('|')
            if len(parts) >= 2:
                test = {"name":parts[0], "date":parts[1],
                        "syllabus":[s.strip() for s in parts[2].split(',')] if len(parts)>2 else [],
                        "importance":int(parts[3]) if len(parts)>3 else 5}
                memory["tests"]["upcoming"].append(test)
                save_json("tests", memory["tests"])
                context.user_data['mode'] = None
                await update.message.reply_text("Test added.")
        return
    elif mode == 'schedule':
        parts = text.split('|')
        if len(parts) == 3:
            memory["schedule"]["wake_up"] = parts[0]; memory["schedule"]["sleep"] = parts[1]
            memory["schedule"]["study_hours"] = int(parts[2])
            save_json("schedule", memory["schedule"])
            for job in context.job_queue.jobs():
                if job.name == "morning_checkin":
                    job.schedule_removal()
            schedule_morning_checkin(context.job_queue, parts[0], update.effective_chat.id)
            await update.message.reply_text("Schedule updated.")
            context.user_data['mode'] = None
        return
    elif mode == 'weekly':
        if text.lower() != 'skip':
            memory["schedule"]["weekly_timetable"] = text
            memory["schedule"]["last_updated"] = datetime.now().isoformat()
            save_json("schedule", memory["schedule"])
        context.user_data['mode'] = None
        await update.message.reply_text("Timetable saved.")
        return
    elif mode == 'syllabus':
        parts = text.split('|')
        if len(parts) == 2:
            key, status = parts[0].strip(), parts[1].strip()
            if key in memory["syllabus"]["chapters"]:
                memory["syllabus"]["chapters"][key]["status"] = status
                save_json("syllabus", memory["syllabus"])
                await update.message.reply_text("Syllabus updated.")
            else:
                await update.message.reply_text("Invalid key.")
            context.user_data['mode'] = None
        return
    elif mode == 'complete':
        today = memory["today"]
        found = False
        for task in today.get("todo", []):
            if text.lower() in task.get("id","").lower() or text.lower() in task.get("description","").lower():
                task["status"] = "done"
                found = True
                memory["progress"]["logs"].append({
                    "task_id": task["id"], "description": task.get("description",""),
                    "timestamp": datetime.now().isoformat()
                })
                save_json("progress", memory["progress"])
                break
        if found:
            memory["today"] = today
            save_json("today", memory["today"])
            done_today = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(datetime.now().strftime("%Y-%m-%d")))
            memory["stats"]["productivity"].append(done_today)
            save_json("stats", memory["stats"])
            await update.message.reply_text("✅ Task marked done.")
        else:
            await update.message.reply_text("Task not found.")
        context.user_data['mode'] = None
        return
    # default coach mode
    await update.message.reply_text("I'm in coach mode. Use /help to see commands, or wait for your morning check‑in.")

# ---------- PDF / schedule ----------
async def weekly_schedule_prompt(context):
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id, "📅 It's Saturday! Please upload your class schedule PDF for batch CETQAS.")
    context.bot_data.setdefault("expecting_pdf", {})
    context.bot_data["expecting_pdf"][chat_id] = True

async def handle_document(update, context):
    chat_id = update.effective_chat.id
    if context.bot_data.get("expecting_pdf",{}).get(chat_id):
        doc = update.message.document
        if doc.mime_type == "application/pdf":
            file = await context.bot.get_file(doc.file_id)
            file_path = f"/tmp/{chat_id}_schedule.pdf"
            await file.download_to_drive(file_path)
            text = ""
            if PDF_SUPPORT:
                try:
                    with open(file_path, "rb") as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages:
                            text += page.extract_text() or ""
                except: pass
            if "CETQAS" in text:
                lines = text.split('\n')
                timetable = [line.strip() for line in lines if any(day in line for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])]
                if timetable:
                    memory["schedule"]["weekly_timetable"] = "\n".join(timetable)
                    save_json("schedule", memory["schedule"])
                    await update.message.reply_text("✅ Timetable extracted and saved.")
                else:
                    await update.message.reply_text("Found CETQAS but couldn't parse timetable. Use /week_update.")
            else:
                await update.message.reply_text("Could not find CETQAS. Use /week_update.")
            os.remove(file_path)
        else:
            await update.message.reply_text("Please send a PDF file.")
        context.bot_data["expecting_pdf"][chat_id] = False

def schedule_morning_checkin(job_queue, wake_up_str, chat_id):
    wake_time = datetime.strptime(wake_up_str, "%H:%M").time()
    job_queue.run_daily(start_morning_checkin, time=wake_time, chat_id=chat_id, name="morning_checkin")

def schedule_weekly_pdf_prompt(job_queue, chat_id):
    job_queue.run_daily(weekly_schedule_prompt, time=datetime.strptime("08:00","%H:%M").time(),
                        days=(5,), chat_id=chat_id, name="weekly_pdf_prompt")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT",8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("chat", chat_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("motivate", motivate_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("week_report", week_report_cmd))
    app.add_handler(CommandHandler("set_test", set_test_cmd))
    app.add_handler(CommandHandler("view_tests", view_tests_cmd))
    app.add_handler(CommandHandler("set_schedule", set_schedule_cmd))
    app.add_handler(CommandHandler("add_backlog", add_backlog_cmd))
    app.add_handler(CommandHandler("add_test", add_test_cmd))
    app.add_handler(CommandHandler("update_syllabus", update_syllabus_cmd))
    app.add_handler(CommandHandler("view_syllabus", view_syllabus))
    app.add_handler(CommandHandler("view_backlog", view_backlog))
    app.add_handler(CommandHandler("complete_task", complete_task_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("week_update", week_update_cmd))
    app.add_handler(CommandHandler("start_day", start_day))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    if app.job_queue:
        schedule = memory["schedule"]
        schedule_morning_checkin(app.job_queue, schedule["wake_up"], None)
        schedule_weekly_pdf_prompt(app.job_queue, None)
        # test follow-ups still exist (unused here but kept)
    print("Bot polling...")
    app.run_polling()