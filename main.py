import os
import json
import threading
import random
import re
import csv
import io
import math
import time
import hashlib
import logging
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional, Tuple, Union

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
import requests

# ---------- Logging ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Configuration ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
DATA_DIR = "/tmp/data" if os.getenv("RENDER") else "data"
os.makedirs(DATA_DIR, exist_ok=True)
lock = threading.Lock()
MAX_CONVERSATION_HISTORY = 30

# ---------- Safe JSON helpers ----------
def safe_load_json(name, default):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        save_json(name, default)
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Corrupted {name}.json: {e}. Restoring default.")
        save_json(name, default)
        return default

def save_json(name, data):
    path = os.path.join(DATA_DIR, f"{name}.json")
    with lock:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

# ---------- Full syllabus (11th & 12th) ----------
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

# ---------- Global memory (all persistent data) ----------
memory = {
    "backlog": safe_load_json("backlog", {"tasks": []}),
    "today": safe_load_json("today", {"date": "", "todo": [], "generated": False}),
    "schedule": safe_load_json("schedule", {"wake_up": "07:00", "sleep": "22:00", "study_hours": 8,
                                            "weekly_timetable": "", "last_updated": "",
                                            "notifications": {"enabled": True, "interval_minutes": 120}}),
    "progress": safe_load_json("progress", {"logs": []}),
    "stats": safe_load_json("stats", {
        "productivity": [], "consistency": 0, "fatigue_flags": 0,
        "streak": 0, "longest_streak": 0, "last_study_date": "",
        "total_study_days": 0, "total_study_hours": 0.0,
        "weekly_hours": {}, "mood_log": {}
    }),
    "syllabus": safe_load_json("syllabus", {"chapters": init_syllabus()}),
    "tests": safe_load_json("tests", {
        "upcoming": [], "past": [],
        "next_test_date": None,
        "next_test_11th_syllabus": [],
        "test_asked_today": False
    }),
    "homework": safe_load_json("homework", {"date": "", "tasks": []}),
    "chapter_exercises": safe_load_json("chapter_exercises", {"exercises": {}}),
    "conversation": safe_load_json("conversation", {}),
    "goals": safe_load_json("goals", {"daily_hours": None, "weekly_hours": None, "streak_goal": None}),
    "pomodoro": safe_load_json("pomodoro", {"active": False, "end_time": None, "chat_id": None}),
    "reminders": safe_load_json("reminders", []),
    "points": safe_load_json("points", {"total": 0, "history": []}),
    "custom_chapters": safe_load_json("custom_chapters", {}),
    "flashcards": safe_load_json("flashcards", {"decks": {}}),          # NEW: flashcards
    "study_notes": safe_load_json("study_notes", {"notes": []}),        # NEW: personal notes
    "daily_quotes": safe_load_json("daily_quotes", {"last_quote_date": None, "quote": ""}),  # NEW: daily quote
    "revision_tracker": safe_load_json("revision_tracker", {}),         # NEW: spaced repetition
}

# ---------- Constants ----------
RECOMMENDED_SLEEP = 7.5
EXERCISE_TIMES = {"O1":5,"O2":7,"O3":7,"O4":5,"JM":5,"JA":8,"Gyanoday":10}
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
STUDY_TIPS = [
    "Use the Pomodoro technique: 25 min study, 5 min break.",
    "Active recall > passive reading. Test yourself!",
    "Teach a concept to someone else to master it.",
    "Spaced repetition: review after 1, 3, 7 days.",
    "Solve previous years' JEE papers under time constraints.",
    "Keep a formula notebook and revise daily.",
    "Sleep 7-8 hours; it improves memory consolidation.",
    "Use the Feynman technique: explain in simple words.",
    "Study in a distraction-free environment.",
    "Take handwritten notes – they improve retention.",
]
JEE_FORMULAS = {
    "Kinematics": "v = u + at, s = ut + ½at², v² = u² + 2as",
    "Newton's Laws": "F = ma, Action = Reaction",
    "Work Energy": "W = F·d, KE = ½mv², PE = mgh",
    "Thermodynamics": "ΔU = Q - W, Carnot efficiency = 1 - T₂/T₁",
    "Electrostatics": "Coulomb: F = kq₁q₂/r², E = kq/r²",
    "Current Electricity": "V = IR, P = VI, R = ρL/A",
    "Magnetism": "F = qvB sinθ, B = μ₀I/(2πr)",
    "Optics": "1/f = 1/v + 1/u, n = c/v",
    "Modern Physics": "E = hf, λ = h/p",
    "Chemistry": "Mole concept: n = m/M, Ideal gas: PV = nRT",
    "Maths": "Quadratic: x = [-b ± √(b²-4ac)]/(2a)",
}

# ---------- Helper functions (extended) ----------
def get_ongoing_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v.get("status") == "going_on"]

def get_weak_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v.get("status") in ("weak","revision_needed")]

def compute_priority(task, test_chapters, ongoing_chapters):
    score = 50
    if task.get("test_link") in [t["name"] for t in memory["tests"].get("upcoming",[])]:
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
    text = (task.get("id","") + " " + task.get("description","") + " " + task.get("chapter","")).lower()
    for kw in skip_keywords:
        if kw.strip().lower() in text:
            return True
    return False

def estimate_backlog_days(daily_hours):
    pending = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
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
            stats["streak"] = stats.get("streak",0) + 1
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
        if today not in mood_log:
            mood_log[today] = {}
        mood_log[today]["mood"] = mood
        if sleep_hours is not None:
            mood_log[today]["sleep"] = sleep_hours
        stats["mood_log"] = mood_log
    save_json("stats", stats)

def add_points(amount: int, reason: str):
    memory["points"]["total"] += amount
    memory["points"]["history"].append({"amount": amount, "reason": reason, "timestamp": datetime.now().isoformat()})
    save_json("points", memory["points"])
    return memory["points"]["total"]

def get_daily_quote():
    today = date.today().isoformat()
    if memory["daily_quotes"].get("last_quote_date") != today:
        new_quote = random.choice(MOTIVATIONAL_QUOTES)
        memory["daily_quotes"]["last_quote_date"] = today
        memory["daily_quotes"]["quote"] = new_quote
        save_json("daily_quotes", memory["daily_quotes"])
    return memory["daily_quotes"]["quote"]

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
                "subject": chap.get("subject","Unknown"),
                "chapter": chap.get("chapter","Unknown"),
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
                "id": f"revweak_{ch_key}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "subject": chap.get("subject","Unknown"),
                "chapter": chap.get("chapter","Unknown"),
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
                "id": f"revongo_{ch_key}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "subject": chap.get("subject","Unknown"),
                "chapter": chap.get("chapter","Unknown"),
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
    try:
        study_mins = (float(study_hours_override) if study_hours_override else float(memory["schedule"]["study_hours"])) * 60
        homework_tasks = memory["homework"].get("tasks", []) if memory["homework"].get("date") == datetime.now().strftime("%Y-%m-%d") else []
        if not isinstance(homework_tasks, list):
            homework_tasks = []
        backlog_tasks = [t for t in memory["backlog"].get("tasks", []) if t.get("status") != "done"]
        if not isinstance(backlog_tasks, list):
            backlog_tasks = []
        if skip_keywords:
            backlog_tasks = [t for t in backlog_tasks if not should_skip_task(t, skip_keywords)]
        if test_chapters is None: test_chapters = []
        if ongoing_chapters is None: ongoing_chapters = get_ongoing_chapters()

        revision_11th = generate_revision_tasks_for_11th()
        all_mandatory = revision_11th + homework_tasks + backlog_tasks
        for t in all_mandatory:
            if "estimated_time" not in t:
                t["estimated_time"] = 30
            t["priority_score"] = compute_priority(t, test_chapters, ongoing_chapters)
        all_mandatory.sort(key=lambda x: x.get("priority_score",0), reverse=True)

        total_mandatory_min = sum(t.get("estimated_time",0) for t in all_mandatory)
        remaining = study_mins - total_mandatory_min
        smart_rev = []
        if remaining > 30:
            smart_rev = generate_smart_revision_tasks(remaining)

        full_list = all_mandatory + smart_rev
        total_min = sum(t.get("estimated_time",0) for t in full_list)

        overflow_tasks = []
        if total_min > study_mins:
            full_list.sort(key=lambda x: x.get("priority_score",0))
            while full_list and sum(t.get("estimated_time",0) for t in full_list) > study_mins:
                removed = full_list.pop(0)
                overflow_tasks.append(removed)
            if overflow_tasks:
                for task in overflow_tasks:
                    task["status"] = "pending"
                    task["source"] = task.get("source", "overflow")
                    task["id"] = f"overflow_{task.get('id','')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    memory["backlog"]["tasks"].append(task)
                save_json("backlog", memory["backlog"])
            total_min = sum(t.get("estimated_time",0) for t in full_list)
            full_list.sort(key=lambda x: x.get("priority_score",0), reverse=True)

        memory["today"] = {"date": datetime.now().strftime("%Y-%m-%d"), "todo": full_list, "generated": True}
        save_json("today", memory["today"])
        return full_list, total_min, study_mins, overflow_tasks
    except Exception as e:
        logger.error(f"generate_todo_list error: {e}", exc_info=True)
        raise

# ---------- AI engine with persistent conversation and advanced memory ----------
def get_conversation_context(chat_id: int) -> List[Dict]:
    return memory["conversation"].get(str(chat_id), [])[-MAX_CONVERSATION_HISTORY:]

def add_to_conversation(chat_id: int, role: str, content: str):
    key = str(chat_id)
    if key not in memory["conversation"]:
        memory["conversation"][key] = []
    memory["conversation"][key].append({"role": role, "content": content, "time": datetime.now().isoformat()})
    if len(memory["conversation"][key]) > MAX_CONVERSATION_HISTORY:
        memory["conversation"][key] = memory["conversation"][key][-MAX_CONVERSATION_HISTORY:]
    save_json("conversation", memory["conversation"])

def ask_ai_smart(user_message: str, chat_id: int) -> Dict:
    if not GROQ_KEY:
        return {"response": "AI not available. Please set GROQ_API_KEY.", "updates": []}
    weak_topics = get_weak_chapters()[:5]
    backlog_count = len([t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"])
    upcoming_tests = memory["tests"].get("upcoming", [])
    next_test_date = memory["tests"].get("next_test_date", "None")
    syllabus_status = {k: v.get("status") for k, v in list(memory["syllabus"]["chapters"].items())[:20]}
    today_hours = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(date.today().isoformat()))
    context_str = f"""
WAKE_UP: {memory['schedule']['wake_up']}
SLEEP: {memory['schedule']['sleep']}
STUDY_HOURS_TARGET: {memory['schedule']['study_hours']}
TODAY_PLAN_GENERATED: {memory['today'].get('generated', False)}
BACKLOG_COUNT: {backlog_count}
WEAK_CHAPTERS: {weak_topics}
UPCOMING_TESTS: {[t.get('name') for t in upcoming_tests]}
NEXT_TEST_DATE: {next_test_date}
SAMPLE_SYLLABUS: {syllabus_status}
TODAY_MOOD: {memory['stats'].get('mood_log', {}).get(date.today().isoformat(), {}).get('mood', 'Not recorded')}
TODAY_STUDY_HOURS: {today_hours}
GOALS: {memory['goals']}
POINTS: {memory['points']['total']}
"""
    conv = get_conversation_context(chat_id)
    conv_text = "\n".join([f"{m['role']}: {m['content']}" for m in conv]) if conv else "(No recent conversation)"
    system_prompt = f"""You are JEE Study OS, a super‑intelligent JEE coach. You have access to the student's memory (above). Your task:
- Answer questions about their data (backlog, syllabus, schedule, stats) using the memory.
- Answer general JEE academic questions (Physics, Chemistry, Maths) using your knowledge.
- Update their data when they tell you (e.g., "add backlog Physics Gravitation 45 min", "set wake-up to 7am", "my mood is 8").
- Give proactive advice based on their data (e.g., "You have a test in 3 days, revise weak topics", "You slept only 5h, rest more").
- Be friendly but strict.

Return ONLY a JSON object with:
{{"response": "your reply", "updates": [list of update objects]}}
Each update object has "action" and "data". Possible actions:
- add_backlog: data {{"subject": str, "chapter": str, "estimated_time": int}}
- update_schedule: data {{"wake_up": str (HH:MM), "sleep": str, "study_hours": int}} (any can be omitted)
- update_syllabus_status: data {{"chapter_key": str, "status": str}}
- record_mood: data {{"mood": int (1-10)}}
- add_homework: data {{"subject": str, "chapter": str, "exercises": {{"O1": int, ...}}}}
- set_test: data {{"date": "YYYY-MM-DD", "chapters_11th": [str]}}
- complete_task: data {{"task_id_or_chapter": str}}
- add_points: data {{"amount": int, "reason": str}}
- set_goal: data {{"goal_type": "daily_hours"|"weekly_hours"|"streak_goal", "value": float/int}}
- none: data {{}} (no update)

If no update, send "updates": [].

Student message: {user_message}
Recent conversation:
{conv_text}
Memory:
{context_str}
"""
    try:
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        payload = {"model": GROQ_MODEL, "messages": [{"role": "system", "content": system_prompt}], "temperature": 0.5, "max_tokens": 800, "response_format": {"type": "json_object"}}
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            result = resp.json()["choices"][0]["message"]["content"]
            try:
                return json.loads(result)
            except:
                return {"response": "Sorry, I had trouble understanding. Please rephrase.", "updates": []}
        else:
            return {"response": f"AI error (status {resp.status_code}). Try again.", "updates": []}
    except Exception as e:
        return {"response": f"Network error: {str(e)}", "updates": []}

def apply_updates(updates: List[Dict]) -> str:
    results = []
    for upd in updates:
        action = upd.get("action")
        data = upd.get("data", {})
        if action == "add_backlog":
            subj = data.get("subject")
            chap = data.get("chapter")
            et = data.get("estimated_time", 45)
            if subj and chap:
                task = {"id": str(int(datetime.timestamp(datetime.now()))), "subject": subj, "chapter": chap, "type": "backlog", "estimated_time": et, "source": "AI", "status": "pending", "chapter_key": f"{subj}_{chap.replace(' ','_')}"}
                memory["backlog"]["tasks"].append(task)
                save_json("backlog", memory["backlog"])
                results.append(f"➕ Added backlog: {subj} - {chap} ({et} min)")
            else:
                results.append("❌ Could not add backlog: missing subject or chapter")
        elif action == "update_schedule":
            changed = []
            if "wake_up" in data:
                memory["schedule"]["wake_up"] = data["wake_up"]
                changed.append(f"wake‑up to {data['wake_up']}")
            if "sleep" in data:
                memory["schedule"]["sleep"] = data["sleep"]
                changed.append(f"sleep to {data['sleep']}")
            if "study_hours" in data:
                memory["schedule"]["study_hours"] = data["study_hours"]
                changed.append(f"study hours to {data['study_hours']}")
            if changed:
                save_json("schedule", memory["schedule"])
                results.append(f"✅ Schedule updated: {', '.join(changed)}")
            else:
                results.append("ℹ️ No schedule changes")
        elif action == "update_syllabus_status":
            key = data.get("chapter_key")
            status = data.get("status")
            if key and status in ("not_started","going_on","completed","weak","revision_needed"):
                if key in memory["syllabus"]["chapters"]:
                    memory["syllabus"]["chapters"][key]["status"] = status
                    save_json("syllabus", memory["syllabus"])
                    results.append(f"📘 Chapter {key} marked as {status}")
                else:
                    results.append(f"❌ Chapter key '{key}' not found")
            else:
                results.append("❌ Invalid chapter update")
        elif action == "record_mood":
            mood = data.get("mood")
            if isinstance(mood, int) and 1 <= mood <= 10:
                today_str = date.today().isoformat()
                mood_log = memory["stats"].get("mood_log", {})
                if today_str not in mood_log:
                    mood_log[today_str] = {}
                mood_log[today_str]["mood"] = mood
                memory["stats"]["mood_log"] = mood_log
                save_json("stats", memory["stats"])
                results.append(f"😊 Mood recorded: {mood}/10")
            else:
                results.append("❌ Mood must be 1‑10")
        elif action == "add_homework":
            subj = data.get("subject")
            chap = data.get("chapter")
            ex = data.get("exercises", {})
            if subj and chap and ex:
                ckey = f"{subj}_{chap.replace(' ','_')}"
                if ckey not in memory["syllabus"]["chapters"]:
                    memory["syllabus"]["chapters"][ckey] = {"subject": subj, "chapter": chap, "class": 12, "status": "not_started", "sub_subject": subj if subj != "Chemistry" else "Physical"}
                    save_json("syllabus", memory["syllabus"])
                total = estimate_homework_time(ex)
                task = {"id": str(int(datetime.timestamp(datetime.now()))), "subject": subj, "chapter": chap, "type": "mixed", "estimated_time": total, "source": "AI", "status": "pending", "chapter_key": ckey, "exercise_counts": ex}
                memory["backlog"]["tasks"].append(task)
                save_json("backlog", memory["backlog"])
                results.append(f"📚 Homework added: {subj} - {chap} ({total} min)")
            else:
                results.append("❌ Could not add homework")
        elif action == "set_test":
            dstr = data.get("date")
            ch11 = data.get("chapters_11th", [])
            if dstr:
                try:
                    date.fromisoformat(dstr)
                    memory["tests"]["next_test_date"] = dstr
                    memory["tests"]["next_test_11th_syllabus"] = ch11
                    save_json("tests", memory["tests"])
                    results.append(f"📅 Test set for {dstr} with {len(ch11)} 11th chapters")
                except:
                    results.append("❌ Invalid date format")
            else:
                results.append("❌ Missing test date")
        elif action == "complete_task":
            ref = data.get("task_id_or_chapter")
            if ref:
                today_plan = memory["today"].get("todo", [])
                found = False
                for task in today_plan:
                    if ref.lower() in task.get("id","").lower() or ref.lower() in task.get("chapter","").lower():
                        task["status"] = "done"
                        found = True
                        memory["progress"]["logs"].append({"task_id": task["id"], "description": f"{task['subject']} - {task['chapter']}", "timestamp": datetime.now().isoformat()})
                        save_json("progress", memory["progress"])
                        add_points(10, f"Completed task: {task['subject']} - {task['chapter']}")
                        results.append(f"✅ Task '{ref}' marked done (+10 points)")
                        break
                if not found:
                    results.append(f"❌ Task '{ref}' not found in today's plan")
            else:
                results.append("❌ Missing task reference")
        elif action == "add_points":
            amt = data.get("amount", 0)
            reason = data.get("reason", "AI reward")
            add_points(amt, reason)
            results.append(f"🏆 +{amt} points: {reason}")
        elif action == "set_goal":
            gtype = data.get("goal_type")
            val = data.get("value")
            if gtype in ["daily_hours", "weekly_hours", "streak_goal"] and val is not None:
                memory["goals"][gtype] = val
                save_json("goals", memory["goals"])
                results.append(f"🎯 Goal set: {gtype} = {val}")
            else:
                results.append("❌ Invalid goal")
        else:
            results.append(f"❓ Unknown action: {action}")
    return "\n".join(results)

# ---------- Daily check‑in state and handlers ----------
daily_states: Dict[int, Dict] = {}

async def start_daily_checkin(chat_id, context):
    daily_states[chat_id] = {
        "state": "sleep", "sleep": None, "wake_time": None, "mood": None,
        "study_hours": None, "homework": [], "skip_keywords": []
    }
    await context.bot.send_message(chat_id, "🌅 Good morning! How many hours did you sleep last night? (e.g., 6.5)")

async def handle_daily_checkin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid not in daily_states:
        return False
    state = daily_states[cid]
    text = update.message.text.strip()
    s = state["state"]
    if s == "sleep":
        try:
            h = float(text)
            state["sleep"] = h
            state["state"] = "wake"
            await update.message.reply_text("⏰ What time did you wake up? (HH:MM)")
        except:
            await update.message.reply_text("Please enter a number (e.g., 7).")
        return True
    elif s == "wake":
        try:
            datetime.strptime(text, "%H:%M")
            state["wake_time"] = text
            state["state"] = "mood"
            await update.message.reply_text("😊 Mood today? (1‑10)")
        except:
            await update.message.reply_text("Invalid time. Use HH:MM")
        return True
    elif s == "mood":
        try:
            m = int(text)
            if 1 <= m <= 10:
                state["mood"] = m
                state["state"] = "study_hours"
                await update.message.reply_text("📘 How many hours can you study today?")
            else:
                await update.message.reply_text("Number between 1 and 10")
        except:
            await update.message.reply_text("Please enter a number 1‑10")
        return True
    elif s == "study_hours":
        try:
            h = float(text)
            state["study_hours"] = h
            state["state"] = "homework"
            await update.message.reply_text("📝 Enter homework (e.g., 'Physics Electrostatics O1 30') or type 'done'")
        except:
            await update.message.reply_text("Please enter a number")
        return True
    elif s == "homework":
        if text.lower() == "done":
            state["state"] = "skip"
            if state["homework"]:
                await update.message.reply_text("Homework recorded. Now, any keywords to skip? (comma‑separated) or 'none'")
            else:
                await update.message.reply_text("No homework. Any keywords to skip? (or 'none')")
            return True
        m = re.match(r"(\w+)\s+(\w+)\s+O1\s+(\d+)", text, re.I)
        if m:
            subj, chap, cnt = m.group(1), m.group(2), int(m.group(3))
            ex = {"O1": cnt}
            ckey = f"{subj}_{chap.replace(' ','_')}"
            if ckey not in memory["syllabus"]["chapters"]:
                memory["syllabus"]["chapters"][ckey] = {"subject": subj, "chapter": chap, "class": 12, "status": "not_started", "sub_subject": subj if subj != "Chemistry" else "Physical"}
                save_json("syllabus", memory["syllabus"])
            total = estimate_homework_time(ex)
            task = {"id": str(int(datetime.timestamp(datetime.now()))), "subject": subj, "chapter": chap, "type": "mixed", "estimated_time": total, "source": "coaching", "status": "pending", "chapter_key": ckey, "exercise_counts": ex}
            state["homework"].append(task)
            await update.message.reply_text(f"✅ Added {subj} - {chap} ({total} min). Send more or 'done'")
        else:
            await update.message.reply_text("Could not understand. Use: 'Subject Chapter O1 count' or 'done'")
        return True
    elif s == "skip":
        if text.lower() == "none":
            state["skip_keywords"] = []
        else:
            state["skip_keywords"] = [kw.strip() for kw in text.split(",") if kw.strip()]
        await finalize_daily_checkin(update, context)
        return True
    return False

async def finalize_daily_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    state = daily_states.pop(cid, None)
    if not state:
        await update.message.reply_text("❌ Session expired. Use /start_day again.")
        return
    try:
        await context.bot.send_chat_action(cid, action="typing")
        sleep_msg = ""
        if state["sleep"] is not None:
            diff = state["sleep"] - RECOMMENDED_SLEEP
            if diff >= 1:
                sleep_msg = f"You slept {state['sleep']}h — well rested! (+{diff:.1f}h)"
            elif diff <= -1:
                sleep_msg = f"You slept {state['sleep']}h — less than recommended ({RECOMMENDED_SLEEP}h)"
            else:
                sleep_msg = f"You slept {state['sleep']}h — adequate"
        memory["homework"] = {"date": datetime.now().strftime("%Y-%m-%d"), "tasks": state["homework"]}
        save_json("homework", memory["homework"])
        study_hours = state["study_hours"] if state["study_hours"] else memory["schedule"]["study_hours"]
        todo, total_est, avail_mins, overflow = generate_todo_list(
            study_hours_override=study_hours, skip_keywords=state["skip_keywords"]
        )
        daily_hrs = study_hours
        days = estimate_backlog_days(daily_hrs)
        update_streak_and_hours(daily_hrs, mood=state["mood"], sleep_hours=state["sleep"])
        add_points(5, "Completed morning check‑in")
        wake = state["wake_time"] or memory["schedule"]["wake_up"]
        try:
            wake_dt = datetime.strptime(wake, "%H:%M")
            wake_hours = wake_dt.hour + wake_dt.minute/60
        except:
            wake_hours = 7.0
        sleep_time_str = memory["schedule"]["sleep"]
        try:
            sleep_dt = datetime.strptime(sleep_time_str, "%H:%M")
            sleep_hours_val = sleep_dt.hour + sleep_dt.minute/60
        except:
            sleep_hours_val = 22.0
        morning_hours = max(0, 12 - wake_hours)
        evening_hours = max(0, sleep_hours_val - 20)
        total_free = round(morning_hours + evening_hours, 1)
        def emoji(score):
            if score >= 80: return "🔴"
            if score >= 60: return "🟠"
            if score >= 40: return "🟡"
            return "🟢"
        msg = f"{sleep_msg}\n\n📅 *Today's To‑Do* (Classes 12 PM – 8 PM)\n"
        msg += f"🕒 Free hours: ~{total_free}h (morning {morning_hours}h + evening {evening_hours}h)\n"
        msg += f"⏱️ Total task time: {total_est} min ({total_est/60:.1f}h)\n"
        if total_est > avail_mins:
            msg += "⚠️ Task time exceeds available study time.\n"
        if overflow:
            msg += "📦 Moved to backlog:\n"
            for t in overflow:
                msg += f"• {t['subject']} - {t['chapter']} ({t['type']})\n"
        msg += "\n"
        for task in todo:
            msg += f"{emoji(task.get('priority_score',50))} {task['subject']} - {task['chapter']} ({task['type']}) – {task['estimated_time']} min\n"
        msg += f"\n⏳ Backlog estimate: ~{days} day(s) at {daily_hrs}h/day."
        msg += f"\n🏆 Points earned today: +5 (check‑in)"
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error generating plan: {str(e)[:100]}. Please try /start_day again.")

# ---------- Test management ----------
def schedule_test_followups(app):
    if not app.job_queue: return
    nxt = memory["tests"].get("next_test_date")
    if not nxt: return
    try:
        test_date = date.fromisoformat(nxt)
    except:
        return
    if test_date < date.today(): return
    for job in app.job_queue.jobs():
        if job.name in ("test_day_prompt", "post_test_prompt"):
            job.schedule_removal()
    test_day_dt = datetime.combine(test_date, datetime.strptime("18:00", "%H:%M").time())
    app.job_queue.run_once(post_test_prompt, when=test_day_dt, chat_id=None, name="test_day_prompt")
    next_prompt_date = test_date + timedelta(days=2)
    next_prompt_dt = datetime.combine(next_prompt_date, datetime.strptime("12:00", "%H:%M").time())
    app.job_queue.run_once(ask_next_test_info, when=next_prompt_dt, chat_id=None, name="post_test_prompt")

async def post_test_prompt(context):
    cid = context.job.chat_id or context.bot_data.get("user_chat_id")
    if cid:
        await context.bot.send_message(cid, "📝 How did your monthly test go? Any feedback?")

async def ask_next_test_info(context):
    cid = context.job.chat_id or context.bot_data.get("user_chat_id")
    if cid:
        await context.bot.send_message(cid, "📅 Please set your next monthly test.\nSend: `Test Date (YYYY-MM-DD) | 11th Chapter Keys (comma separated)`")

# ---------- Periodic notifications ----------
async def send_periodic_notification(context):
    cid = context.job.chat_id
    if not cid: return
    now = datetime.now()
    wh = int(memory["schedule"]["wake_up"].split(":")[0])
    sh = int(memory["schedule"]["sleep"].split(":")[0])
    if not (wh <= now.hour < sh): return
    typ = random.choice(["quote", "backlog", "progress", "checkin"])
    if typ == "quote":
        msg = random.choice(MOTIVATIONAL_QUOTES)
    elif typ == "backlog":
        pending = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
        if pending:
            total_min = sum(t.get("estimated_time",45) for t in pending)
            msg = f"📦 Backlog: {len(pending)} tasks, ~{total_min/60:.1f}h left. Keep going!"
        else:
            msg = "🎉 No backlog! Great job."
    elif typ == "progress":
        tp = memory["today"].get("todo", [])
        if tp:
            done = sum(1 for l in memory["progress"].get("logs",[]) if l.get("timestamp","").startswith(date.today().isoformat()))
            msg = f"📊 Today: {done}/{len(tp)} tasks done. Keep it up!"
        else:
            msg = "⏰ Don't forget to start your day with /start_day."
    else:
        msg = "😊 How is your study session? Reply with /mood <1-10> or /progress <hours>."
    try:
        await context.bot.send_message(cid, msg)
    except Exception as e:
        logger.error(f"Notify error: {e}")

def schedule_notifications(job_queue, cid, interval=120, enabled=True):
    for job in job_queue.jobs():
        if job.name == "periodic_notify" and job.chat_id == cid:
            job.schedule_removal()
    if enabled and interval >= 15:
        job_queue.run_repeating(send_periodic_notification, interval=interval*60, first=60, chat_id=cid, name="periodic_notify")
        return True
    return False

# ---------- Extended command handlers ----------
async def start(update, context):
    cid = update.effective_chat.id
    context.bot_data["user_chat_id"] = cid
    if "notifications" not in memory["schedule"]:
        memory["schedule"]["notifications"] = {"enabled": True, "interval_minutes": 120}
        save_json("schedule", memory["schedule"])
    ns = memory["schedule"]["notifications"]
    schedule_notifications(context.application.job_queue, cid, ns.get("interval_minutes",120), ns.get("enabled",True))
    wake_up = memory["schedule"].get("wake_up", "07:00")
    schedule_morning_checkin(context.application.job_queue, wake_up, cid)
    schedule_weekly_pdf_prompt(context.application.job_queue, cid)
    await update.message.reply_text(
        "🚀 **JEE Study OS – Ultra Extensive AI Coach**\n\n"
        "I understand natural language. Try:\n"
        "• *'What's my backlog?'*\n"
        "• *'Add backlog Physics Gravitation 45 min'*\n"
        "• *'Mark Electrostatics as completed'*\n"
        "• *'My mood is 8'*\n"
        "• *'Start pomodoro'*\n"
        "• *'Remind me at 6pm to solve 10 problems'*\n"
        "• *'How to solve quadratic equations?'*\n\n"
        "I'll ask you every morning at your wake‑up time.\n"
        "Type /help for all commands.",
        parse_mode='Markdown'
    )

async def help_cmd(update, context):
    text = """
📚 **JEE Study OS – All Commands**

**🌅 Daily Routine**
/start_day – Manual morning check‑in
/view_plan – Today's to‑do list

**📊 Analytics**
/stats – Study streak and totals
/weekly_report – Hours this week
/trends – Weekly study hours trend
/correlation – Mood vs productivity
/efficiency – Tasks per hour

**🎯 Goals**
/set_goal daily|weekly|streak <value>
/goal_status – Progress towards goals

**⏲️ Focus Tools**
/pomodoro start|stop|status
/break – Suggestion for break
/focus on|off – (placeholder)

**📝 Backlog & Tasks**
/view_backlog – Pending tasks
/add_backlog – Add manually
/complete_task – Mark done
/brain_dump – Send unstructured text → AI organises

**📖 Syllabus**
/view_syllabus – All chapters
/update_syllabus – Change status
/custom_chapter add|list|delete

**📅 Test Manager**
/set_test – Monthly test date
/view_tests – Upcoming test

**🔔 Notifications**
/notify on|off|interval <min>
/remind_me <HH:MM> <message>

**🎮 Gamification**
/points – Your reward points
/challenge – Weekly challenge
/reward – Redeem points

**💡 Tips & Resources**
/study_tips – Random study technique
/motivate – Motivational quote
/formula <topic> – Get JEE formula
/daily_quote – Today's inspiration

**📤 Data**
/export_data – Download CSV of all logs

**💬 AI Chat** – Just talk naturally!
"""
    await update.message.reply_text(text, parse_mode='Markdown')

async def formula_cmd(update, context):
    topic = " ".join(context.args).title()
    if not topic:
        await update.message.reply_text("Usage: /formula <topic> (e.g., /formula kinematics)")
        return
    found = None
    for key, value in JEE_FORMULAS.items():
        if key.lower() in topic.lower() or topic.lower() in key.lower():
            found = value
            break
    if found:
        await update.message.reply_text(f"📐 *{topic}*\n`{found}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"No formula found for '{topic}'. Try: kinematics, thermodynamics, optics, etc.")

async def daily_quote_cmd(update, context):
    quote = get_daily_quote()
    await update.message.reply_text(f"✨ *Daily Inspiration*\n{quote}", parse_mode='Markdown')

async def chat_cmd(update, context):
    context.user_data['mode'] = 'chat'
    context.user_data['chat_history'] = []
    await update.message.reply_text("💬 Chat mode active. /stop to end. (You can also just talk normally without /chat)")

async def stop_cmd(update, context):
    if context.user_data.get('mode') == 'chat':
        context.user_data['mode'] = None
        context.user_data.pop('chat_history', None)
        await update.message.reply_text("Chat ended.")
    else:
        await update.message.reply_text("No active chat.")

async def stats_cmd(update, context):
    s = memory["stats"]
    today_hours = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(date.today().isoformat()))
    await update.message.reply_text(
        f"📊 *Stats*\n"
        f"🔥 Streak: {s.get('streak',0)} days (best {s.get('longest_streak',0)})\n"
        f"📅 Total study days: {s.get('total_study_days',0)}\n"
        f"⏱️ Total hours: {s.get('total_study_hours',0)}\n"
        f"📈 Avg hours/day: {round(s.get('total_study_hours',0)/max(1,s.get('total_study_days',0)),1)}\n"
        f"📆 Today: {today_hours} hours\n"
        f"😊 Today's mood: {s.get('mood_log',{}).get(date.today().isoformat(),{}).get('mood','?')}/10\n"
        f"🏆 Points: {memory['points']['total']}",
        parse_mode='Markdown'
    )

async def week_report_cmd(update, context):
    wk = date.today().strftime("%Y-W%W")
    hrs = memory["stats"].get("weekly_hours",{}).get(wk,0)
    goal = memory["goals"].get("weekly_hours")
    msg = f"📅 This week ({wk}): {hrs} study hours."
    if goal:
        msg += f" Goal: {goal}h ({int((hrs/goal)*100)}%)"
    await update.message.reply_text(msg)

async def view_plan(update, context):
    td = memory["today"]
    if not td.get("generated"):
        await update.message.reply_text("No plan today. Use /start_day or morning check‑in.")
        return
    todo = td.get("todo", [])
    if not todo:
        await update.message.reply_text("No tasks today. Enjoy!")
        return
    msg = "📅 *Today's Plan*\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in todo)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def set_schedule_cmd(update, context):
    await update.message.reply_text("Send: `wake_up|sleep|study_hours` (e.g., `07:00|22:00|8`)")
    context.user_data['mode'] = 'schedule'

async def add_backlog_cmd(update, context):
    await update.message.reply_text("Send: `Subject|Chapter|Type|Time` – type `done` when finished.")
    context.user_data['mode'] = 'backlog'
    context.user_data['temp'] = []

async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
    if not tasks:
        await update.message.reply_text("No backlog.")
        return
    msg = "📋 *Backlog*\n" + "\n".join(f"• {t['subject']} {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in tasks[:15])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def complete_task_cmd(update, context):
    await update.message.reply_text("Send task ID or chapter name to mark done.")
    context.user_data['mode'] = 'complete'

async def update_syllabus_cmd(update, context):
    await update.message.reply_text("Send: `chapter_key|status` (e.g., `Physics_Electrostatics|completed`)")
    context.user_data['mode'] = 'syllabus'

async def view_syllabus(update, context):
    ch = memory["syllabus"]["chapters"]
    msg = "📖 *Syllabus* (first 30)\n"
    for k,v in list(ch.items())[:30]:
        em = "🟢" if v.get("status") in ("completed","going_on") else ("🔴" if v.get("status")=="weak" else "⚪")
        msg += f"{em} {v['subject']} - {v['chapter']} [{v['status']}]\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def set_test_cmd(update, context):
    await update.message.reply_text("Send: `YYYY-MM-DD | chapter_key1, chapter_key2`")
    context.user_data['mode'] = 'set_next_test'

async def view_tests_cmd(update, context):
    nxt = memory["tests"].get("next_test_date")
    if nxt:
        await update.message.reply_text(f"📅 Next test: {nxt}\n11th chapters: {len(memory['tests'].get('next_test_11th_syllabus',[]))}")
    else:
        await update.message.reply_text("No upcoming test set. Use /set_test")

async def motivate_cmd(update, context):
    await update.message.reply_text(random.choice(MOTIVATIONAL_QUOTES))

async def study_tips_cmd(update, context):
    await update.message.reply_text(f"💡 Tip: {random.choice(STUDY_TIPS)}")

async def points_cmd(update, context):
    pts = memory["points"]["total"]
    await update.message.reply_text(f"🏆 Your points: {pts}\nEarn points by completing tasks, pomodoros, and maintaining streaks!")

async def pomodoro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /pomodoro start|stop|status")
        return
    action = args[0].lower()
    chat_id = update.effective_chat.id
    if action == "start":
        duration = 25
        if len(args) > 1:
            try:
                duration = int(args[1])
            except:
                pass
        end_time = datetime.now() + timedelta(minutes=duration)
        memory["pomodoro"] = {"active": True, "end_time": end_time.isoformat(), "chat_id": chat_id, "duration": duration}
        save_json("pomodoro", memory["pomodoro"])
        context.application.job_queue.run_once(pomodoro_end_callback, duration * 60, chat_id=chat_id, name=f"pomodoro_{chat_id}")
        await update.message.reply_text(f"🍅 Pomodoro started for {duration} minutes. Focus!")
    elif action == "stop":
        memory["pomodoro"]["active"] = False
        save_json("pomodoro", memory["pomodoro"])
        await update.message.reply_text("Pomodoro stopped.")
    elif action == "status":
        if memory["pomodoro"].get("active"):
            end = datetime.fromisoformat(memory["pomodoro"]["end_time"])
            remaining = max(0, (end - datetime.now()).total_seconds() // 60)
            await update.message.reply_text(f"Pomodoro active. {int(remaining)} minutes remaining.")
        else:
            await update.message.reply_text("No active pomodoro.")
    else:
        await update.message.reply_text("Unknown action.")

async def pomodoro_end_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id, "🔔 Pomodoro finished! Take a 5‑minute break. /break for suggestions.")
    memory["pomodoro"]["active"] = False
    save_json("pomodoro", memory["pomodoro"])
    add_points(5, "Completed a pomodoro session")

async def break_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breaks = [
        "Stretch your arms and back.",
        "Walk around for 2 minutes.",
        "Drink a glass of water.",
        "Close your eyes and breathe deeply for 1 minute.",
        "Do 10 jumping jacks.",
        "Look away from the screen for 20 seconds.",
        "Meditate for 2 minutes.",
        "Eat a healthy snack.",
        "Step outside for fresh air."
    ]
    await update.message.reply_text(f"🧘 Recommended break: {random.choice(breaks)}")

async def remind_me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /remind_me <HH:MM> <message> (e.g., /remind_me 18:00 Solve physics problems)")
        return
    time_str = args[0]
    message = " ".join(args[1:])
    try:
        reminder_time = datetime.strptime(time_str, "%H:%M").time()
        now = datetime.now()
        target = datetime.combine(now.date(), reminder_time)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        job = context.application.job_queue.run_once(send_reminder, delay, chat_id=update.effective_chat.id, data=message, name=f"remind_{update.effective_chat.id}_{int(datetime.timestamp(now))}")
        reminder_entry = {"time": time_str, "message": message, "chat_id": update.effective_chat.id, "job_name": job.name}
        memory["reminders"].append(reminder_entry)
        save_json("reminders", memory["reminders"])
        await update.message.reply_text(f"⏰ Reminder set for {time_str}: '{message}'")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(context.job.chat_id, f"⏰ Reminder: {context.job.data}")

async def set_goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /set_goal daily|weekly|streak <value>")
        return
    goal_type = args[0].lower()
    try:
        value = float(args[1])
        if goal_type == "daily":
            memory["goals"]["daily_hours"] = value
            await update.message.reply_text(f"Daily study goal set to {value} hours.")
        elif goal_type == "weekly":
            memory["goals"]["weekly_hours"] = value
            await update.message.reply_text(f"Weekly study goal set to {value} hours.")
        elif goal_type == "streak":
            memory["goals"]["streak_goal"] = int(value)
            await update.message.reply_text(f"Streak goal set to {int(value)} days.")
        else:
            await update.message.reply_text("Invalid goal type.")
        save_json("goals", memory["goals"])
    except:
        await update.message.reply_text("Invalid value.")

async def goal_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goals = memory["goals"]
    stats = memory["stats"]
    today_hours = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(date.today().isoformat()))
    week_hours = stats.get("weekly_hours", {}).get(date.today().strftime("%Y-W%W"), 0)
    streak = stats.get("streak", 0)
    msg = "🎯 **Goal Progress**\n"
    if goals.get("daily_hours"):
        percent = min(100, int((today_hours / goals["daily_hours"]) * 100))
        msg += f"Daily: {today_hours}/{goals['daily_hours']}h ({percent}%)\n"
    if goals.get("weekly_hours"):
        percent = min(100, int((week_hours / goals["weekly_hours"]) * 100))
        msg += f"Weekly: {week_hours}/{goals['weekly_hours']}h ({percent}%)\n"
    if goals.get("streak_goal"):
        percent = min(100, int((streak / goals["streak_goal"]) * 100))
        msg += f"Streak: {streak}/{goals['streak_goal']} days ({percent}%)\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def trends_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    weekly = memory["stats"].get("weekly_hours", {})
    last_weeks = sorted(weekly.items())[-4:]
    if not last_weeks:
        await update.message.reply_text("Not enough data.")
        return
    msg = "📈 Study hours trend (last 4 weeks):\n"
    for wk, hrs in last_weeks:
        msg += f"{wk}: {hrs}h\n"
    await update.message.reply_text(msg)

async def correlation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mood_log = memory["stats"].get("mood_log", {})
    data = []
    for day, info in mood_log.items():
        if "mood" in info:
            study_hours = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(day))
            data.append((info["mood"], study_hours))
    if len(data) < 5:
        await update.message.reply_text("Not enough data for correlation. Keep logging mood and study hours.")
        return
    n = len(data)
    sum_x = sum(m for m,_ in data)
    sum_y = sum(h for _,h in data)
    sum_xy = sum(m*h for m,h in data)
    sum_x2 = sum(m*m for m,_ in data)
    denom = (n*sum_x2 - sum_x*sum_x)
    if denom == 0:
        await update.message.reply_text("Cannot compute correlation.")
        return
    r = (n*sum_xy - sum_x*sum_y) / denom
    await update.message.reply_text(f"📊 Mood‑productivity correlation (approx): {r:.2f}\n(1 = strong positive, -1 = strong negative)")

async def efficiency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_hours = memory["stats"].get("total_study_hours", 0)
    total_tasks = len(memory["progress"].get("logs", []))
    if total_hours == 0:
        await update.message.reply_text("No study hours logged yet.")
        return
    efficiency = total_tasks / total_hours
    await update.message.reply_text(f"⚡ Study efficiency: {efficiency:.2f} tasks per hour. Keep improving!")

async def export_data_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Type", "Date", "Details"])
    for log in memory["progress"]["logs"]:
        writer.writerow(["Task Completed", log["timestamp"], log["description"]])
    for day, mood_info in memory["stats"].get("mood_log", {}).items():
        writer.writerow(["Mood", day, f"Mood: {mood_info.get('mood','')}, Sleep: {mood_info.get('sleep','')}"])
    for task in memory["backlog"]["tasks"]:
        writer.writerow(["Backlog", task.get("timestamp", ""), f"{task['subject']} - {task['chapter']} ({task['status']})"])
    output.seek(0)
    await update.message.reply_document(document=output.getvalue().encode(), filename="jee_study_export.csv")

async def brain_dump_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send your brain dump (multiple lines). I'll parse it into tasks.")
    context.user_data['brain_dump_mode'] = True

async def custom_chapter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /custom_chapter add|list|delete <subject> <chapter> [class]")
        return
    action = args[0].lower()
    if action == "add" and len(args) >= 3:
        subject = args[1]
        chapter = " ".join(args[2:])
        key = f"Custom_{subject}_{chapter.replace(' ','_')}"
        memory["custom_chapters"][key] = {"subject": subject, "chapter": chapter, "class": 12, "status": "not_started", "sub_subject": subject}
        memory["syllabus"]["chapters"][key] = memory["custom_chapters"][key]
        save_json("custom_chapters", memory["custom_chapters"])
        save_json("syllabus", memory["syllabus"])
        await update.message.reply_text(f"✅ Custom chapter added: {subject} - {chapter}")
    elif action == "list":
        if not memory["custom_chapters"]:
            await update.message.reply_text("No custom chapters.")
        else:
            msg = "📖 Custom chapters:\n" + "\n".join(f"• {v['subject']} - {v['chapter']}" for v in memory["custom_chapters"].values())
            await update.message.reply_text(msg)
    elif action == "delete" and len(args) >= 2:
        key_to_delete = None
        for k, v in memory["custom_chapters"].items():
            if args[1].lower() in v["chapter"].lower():
                key_to_delete = k
                break
        if key_to_delete:
            del memory["custom_chapters"][key_to_delete]
            del memory["syllabus"]["chapters"][key_to_delete]
            save_json("custom_chapters", memory["custom_chapters"])
            save_json("syllabus", memory["syllabus"])
            await update.message.reply_text("Custom chapter deleted.")
        else:
            await update.message.reply_text("Chapter not found.")
    else:
        await update.message.reply_text("Invalid command.")

async def challenge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    challenges = [
        "Study 6 hours every day for 5 days → earn 50 points.",
        "Complete all backlog tasks in 3 days → earn 100 points.",
        "Take a 10‑minute break after every pomodoro → earn 10 points per day.",
        "Maintain a 7‑day streak → earn 70 points.",
        "Log your mood every day for a week → earn 30 points.",
        "Revise 3 weak chapters this week → earn 40 points.",
        "No study breaks longer than 15 minutes → earn 20 points."
    ]
    await update.message.reply_text(f"🏅 Weekly challenge: {random.choice(challenges)}")

async def reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = memory["points"]["total"]
    if pts >= 50:
        await update.message.reply_text("🎉 You've earned a reward! Choose: /motivate_special, /study_tips_advanced, or /custom_reward")
        add_points(-50, "Redeemed reward")
        await update.message.reply_text("50 points deducted. Enjoy your reward!")
    else:
        await update.message.reply_text(f"Need 50 points. You have {pts}. Keep studying!")

async def focus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔇 Focus mode: future feature. For now, use /pomodoro.")

async def week_update_cmd(update, context):
    await update.message.reply_text("Send your weekly timetable (text) or 'skip'.")
    context.user_data['mode'] = 'weekly'

async def start_day_wrapper(update, context):
    await start_daily_checkin(update.effective_chat.id, context)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pomodoro_stop":
        memory["pomodoro"]["active"] = False
        save_json("pomodoro", memory["pomodoro"])
        await query.edit_message_text("Pomodoro stopped.")

# ---------- Main message router ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if update.message.text.startswith('/'):
        return
    # Chat mode
    if context.user_data.get('mode') == 'chat':
        await context.bot.send_chat_action(cid, action="typing")
        hist = context.user_data.get('chat_history', [])
        umsg = update.message.text
        hist.append({"role":"user","content": umsg})
        res = ask_ai_smart(umsg, cid)
        reply = res.get("response", "Sorry, error.")
        hist.append({"role":"assistant","content": reply})
        context.user_data['chat_history'] = hist
        await update.message.reply_text(reply)
        return
    # Brain dump mode
    if context.user_data.get('brain_dump_mode'):
        await context.bot.send_chat_action(cid, action="typing")
        res = ask_ai_smart(f"Organise this brain dump into a list of backlog tasks: {update.message.text}", cid)
        reply = res.get("response", "Could not parse. Use /add_backlog manually.")
        updates = res.get("updates", [])
        if updates:
            upd_msg = apply_updates(updates)
            if upd_msg:
                reply += f"\n\n{upd_msg}"
        context.user_data['brain_dump_mode'] = False
        await update.message.reply_text(reply)
        return
    # Manual mode handlers (backlog, schedule, syllabus, complete, set_next_test)
    # Check for active flashcard review session
    if context.user_data.get("review_session") and context.user_data["review_session"].get("awaiting_quality"):
        await handle_review_answer(update, context)
        return
    if context.user_data.get('mode') == 'set_next_test':
        await handle_set_test(update, context)
        return
    if cid in daily_states:
        await handle_daily_checkin_message(update, context)
        return
    if context.bot_data.get("expecting_pdf",{}).get(cid):
        await handle_document(update, context)
        return
    mode = context.user_data.get('mode')
    text = update.message.text
    if mode == 'backlog':
        if text.lower() == 'done':
            for t in context.user_data['temp']:
                t["chapter_key"] = f"{t['subject']}_{t['chapter'].replace(' ','_')}"
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
                    "status": "pending", "source": "self"
                }
                context.user_data['temp'].append(task)
                await update.message.reply_text("Added. Next or 'done'.")
            else:
                await update.message.reply_text("Invalid format. Use: Subject|Chapter|Type|Time")
        return
    elif mode == 'schedule':
        parts = text.split('|')
        if len(parts) == 3:
            w, s, h = parts[0].strip(), parts[1].strip(), int(parts[2].strip())
            memory["schedule"]["wake_up"] = w
            memory["schedule"]["sleep"] = s
            memory["schedule"]["study_hours"] = h
            save_json("schedule", memory["schedule"])
            for job in context.application.job_queue.jobs():
                if job.name == "morning_checkin" and job.chat_id == cid:
                    job.schedule_removal()
            schedule_morning_checkin(context.application.job_queue, w, cid)
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
            k, st = parts[0].strip(), parts[1].strip()
            if k in memory["syllabus"]["chapters"]:
                memory["syllabus"]["chapters"][k]["status"] = st
                save_json("syllabus", memory["syllabus"])
                await update.message.reply_text("Syllabus updated.")
            else:
                await update.message.reply_text("Invalid key.")
            context.user_data['mode'] = None
        return
    elif mode == 'complete':
        today_plan = memory["today"].get("todo", [])
        found = False
        for task in today_plan:
            if text.lower() in task.get("id","").lower() or text.lower() in task.get("chapter","").lower():
                task["status"] = "done"
                found = True
                memory["progress"]["logs"].append({"task_id": task["id"], "description": f"{task['subject']} - {task['chapter']}", "timestamp": datetime.now().isoformat()})
                save_json("progress", memory["progress"])
                add_points(10, f"Completed task: {task['subject']} - {task['chapter']}")
                break
        if found:
            memory["today"]["todo"] = today_plan
            save_json("today", memory["today"])
            await update.message.reply_text("✅ Task marked done (+10 points).")
        else:
            await update.message.reply_text("Task not found.")
        context.user_data['mode'] = None
        return
    # Natural language
    await handle_natural_language(update, context)

async def handle_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cid = update.effective_chat.id
    msg = update.message.text
    low = msg.lower()
    # Fast rule‑based for common queries
    if "backlog" in low and ("what" in low or "show" in low or "tell" in low):
        tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
        if not tasks:
            await update.message.reply_text("No backlog tasks.")
        else:
            await update.message.reply_text("📋 Backlog:\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in tasks[:10]))
        return True
    if "plan" in low or "todo" in low:
        todo = memory["today"].get("todo", [])
        if not todo:
            await update.message.reply_text("No plan yet. Use /start_day.")
        else:
            await update.message.reply_text("📅 Today's plan:\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in todo[:10]))
        return True
    if "streak" in low:
        s = memory["stats"]
        await update.message.reply_text(f"🔥 Streak: {s.get('streak',0)} days (best {s.get('longest_streak',0)})")
        return True
    if "weak" in low and "chapter" in low:
        w = get_weak_chapters()
        await update.message.reply_text(f"⚠️ Weak chapters: {', '.join(w[:5]) if w else 'None'}")
        return True
    if "remind me" in low:
        match = re.search(r"remind me at (\d{1,2}:\d{2}) (.+)", low)
        if match:
            time_str = match.group(1)
            reminder_msg = match.group(2)
            context.args = [time_str, reminder_msg]
            await remind_me_cmd(update, context)
            return True
    if "pomodoro" in low:
        if "start" in low:
            await pomodoro_cmd(update, context.with_args(["start"]))
        elif "stop" in low:
            await pomodoro_cmd(update, context.with_args(["stop"]))
        else:
            await pomodoro_cmd(update, context.with_args(["status"]))
        return True
    if "formula" in low:
        # extract topic
        match = re.search(r"formula for (.+)", low)
        if match:
            topic = match.group(1).strip()
            context.args = [topic]
            await formula_cmd(update, context)
            return True
    # AI for everything else
    await context.bot.send_chat_action(cid, action="typing")
    res = ask_ai_smart(msg, cid)
    reply = res.get("response", "Sorry, error.")
    updates = res.get("updates", [])
    if updates:
        upd_msg = apply_updates(updates)
        if upd_msg:
            reply += f"\n\n{upd_msg}"
    add_to_conversation(cid, "user", msg)
    add_to_conversation(cid, "assistant", reply)
    await update.message.reply_text(reply)
    return True

async def handle_set_test(update, context):
    txt = update.message.text
    parts = txt.split('|')
    if len(parts) >= 1:
        dstr = parts[0].strip()
        ch11 = [c.strip() for c in parts[1].split(',')] if len(parts)>1 else []
        try:
            date.fromisoformat(dstr)
            memory["tests"]["next_test_date"] = dstr
            memory["tests"]["next_test_11th_syllabus"] = ch11
            save_json("tests", memory["tests"])
            schedule_test_followups(context.application)
            await update.message.reply_text("✅ Test set. Daily 11th revision added.")
        except:
            await update.message.reply_text("Invalid date. Use YYYY-MM-DD.")
        context.user_data['mode'] = None
    else:
        await update.message.reply_text("Invalid format.")

# ---------- PDF handling and scheduling ----------
async def weekly_schedule_prompt(context):
    cid = context.job.chat_id
    if cid:
        await context.bot.send_message(cid, "📅 It's Saturday! Upload your class schedule PDF (must contain 'CETQAS').")
        context.bot_data.setdefault("expecting_pdf", {})[cid] = True

async def handle_document(update, context):
    cid = update.effective_chat.id
    if context.bot_data.get("expecting_pdf",{}).get(cid):
        doc = update.message.document
        if doc.mime_type == "application/pdf":
            file = await context.bot.get_file(doc.file_id)
            path = f"/tmp/{cid}_schedule.pdf"
            await file.download_to_drive(path)
            text = ""
            if PDF_SUPPORT:
                try:
                    with open(path, "rb") as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages:
                            text += page.extract_text() or ""
                except: pass
            if "CETQAS" in text:
                lines = text.split('\n')
                tt = [l.strip() for l in lines if any(day in l for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])]
                if tt:
                    memory["schedule"]["weekly_timetable"] = "\n".join(tt)
                    save_json("schedule", memory["schedule"])
                    await update.message.reply_text("✅ Timetable saved.")
                else:
                    await update.message.reply_text("CETQAS found but could not parse timetable. Use /week_update.")
            else:
                await update.message.reply_text("No 'CETQAS' found. Use /week_update to enter manually.")
            os.remove(path)
        else:
            await update.message.reply_text("Please send a PDF file.")
        context.bot_data["expecting_pdf"][cid] = False

def schedule_morning_checkin(job_queue, wake_up_str, chat_id):
    wake_time = datetime.strptime(wake_up_str, "%H:%M").time()
    job_queue.run_daily(morning_checkin_callback, time=wake_time, chat_id=chat_id, name="morning_checkin")

async def morning_checkin_callback(context: ContextTypes.DEFAULT_TYPE):
    cid = context.job.chat_id
    if cid:
        await start_daily_checkin(cid, context)

def schedule_weekly_pdf_prompt(job_queue, chat_id):
    job_queue.run_daily(weekly_schedule_prompt, time=datetime.strptime("08:00","%H:%M").time(), days=(5,), chat_id=chat_id, name="weekly_pdf_prompt")

def schedule_reminders(job_queue):
    for rem in memory["reminders"]:
        try:
            rem_time = datetime.strptime(rem["time"], "%H:%M").time()
            now = datetime.now()
            target = datetime.combine(now.date(), rem_time)
            if target <= now:
                target += timedelta(days=1)
            delay = (target - now).total_seconds()
            job_queue.run_once(send_reminder, delay, chat_id=rem["chat_id"], data=rem["message"], name=rem.get("job_name", f"remind_{rem['chat_id']}_{int(datetime.timestamp(now))}"))
        except:
            pass

# ---------- Health server ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()
# ========== EXTENSION: FLASHCARDS, NOTES, SPACED REPETITION, ADVANCED ANALYTICS ==========
# Paste this entire block at the end of your existing bot.py (before the main block)

# ---------- Additional imports (already present, but safe) ----------
import math
from collections import Counter

# ---------- Spaced repetition helpers (SM-2 algorithm) ----------
def calculate_next_review(quality: int, repetitions: int, ease_factor: float, interval: int) -> tuple:
    """
    quality: 0-5 (0=blackout, 5=perfect)
    returns (new_repetitions, new_ease_factor, new_interval)
    """
    if quality < 3:
        new_repetitions = 0
        new_interval = 1
    else:
        new_repetitions = repetitions + 1
        if new_repetitions == 1:
            new_interval = 1
        elif new_repetitions == 2:
            new_interval = 6
        else:
            new_interval = round(interval * ease_factor)
        new_ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        if new_ease_factor < 1.3:
            new_ease_factor = 1.3
    return new_repetitions, new_ease_factor, new_interval

# ---------- Flashcards data structure ----------
# memory["flashcards"] = {"decks": {deck_name: {"cards": [{"front": str, "back": str, "repetitions": int, "ease": float, "interval": int, "next_review": str}]}}}

async def create_deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /create_deck <deck_name>")
        return
    deck_name = " ".join(args).strip()
    if deck_name in memory["flashcards"]["decks"]:
        await update.message.reply_text(f"Deck '{deck_name}' already exists.")
        return
    memory["flashcards"]["decks"][deck_name] = {"cards": []}
    save_json("flashcards", memory["flashcards"])
    await update.message.reply_text(f"✅ Deck '{deck_name}' created. Add flashcards with /add_card {deck_name} <front> | <back>")

async def add_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /add_card <deck_name> <front> | <back>")
        return
    deck_name = args[0]
    rest = " ".join(args[1:])
    if "|" not in rest:
        await update.message.reply_text("Separate front and back with a pipe '|'")
        return
    front, back = rest.split("|", 1)
    front = front.strip()
    back = back.strip()
    if deck_name not in memory["flashcards"]["decks"]:
        await update.message.reply_text(f"Deck '{deck_name}' not found. Create it with /create_deck")
        return
    card = {
        "front": front,
        "back": back,
        "repetitions": 0,
        "ease": 2.5,
        "interval": 0,
        "next_review": datetime.now().isoformat()
    }
    memory["flashcards"]["decks"][deck_name]["cards"].append(card)
    save_json("flashcards", memory["flashcards"])
    await update.message.reply_text(f"✅ Card added to '{deck_name}':\nFront: {front}\nBack: {back}")

async def list_decks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    decks = memory["flashcards"]["decks"]
    if not decks:
        await update.message.reply_text("No flashcard decks. Create one with /create_deck")
        return
    msg = "📚 *Your Decks*\n"
    for name, deck in decks.items():
        msg += f"• {name} ({len(deck['cards'])} cards)\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def review_deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /review <deck_name>")
        return
    deck_name = " ".join(args).strip()
    if deck_name not in memory["flashcards"]["decks"]:
        await update.message.reply_text(f"Deck '{deck_name}' not found.")
        return
    now = datetime.now()
    due_cards = []
    for idx, card in enumerate(memory["flashcards"]["decks"][deck_name]["cards"]):
        next_review = datetime.fromisoformat(card["next_review"])
        if next_review <= now:
            due_cards.append((idx, card))
    if not due_cards:
        await update.message.reply_text(f"No cards due for review in '{deck_name}'. Great job!")
        return
    # Store review session in user_data
    context.user_data["review_session"] = {
        "deck": deck_name,
        "cards": due_cards,
        "current_index": 0,
        "awaiting_quality": False,
        "current_card": None
    }
    await send_next_card(update, context)

async def send_next_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = context.user_data.get("review_session")
    if not session:
        return
    if session["current_index"] >= len(session["cards"]):
        await update.message.reply_text("🎉 Review session finished! All cards reviewed. /list_decks to see progress.")
        context.user_data.pop("review_session")
        return
    idx, card = session["cards"][session["current_index"]]
    session["awaiting_quality"] = True
    session["current_card"] = (idx, card)
    await update.message.reply_text(f"📇 *Card {session['current_index']+1}/{len(session['cards'])}*\n\nFront: {card['front']}\n\nType your answer. Then rate your recall (0-5) when asked.", parse_mode='Markdown')

async def handle_review_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = context.user_data.get("review_session")
    if not session or not session.get("awaiting_quality"):
        return False
    # If we haven't shown the back yet, show it and ask for quality
    if not session.get("showed_back"):
        # Show the back
        idx, card = session["current_card"]
        await update.message.reply_text(f"*Answer:* {card['back']}\n\nHow well did you recall? (0=forgot, 1=wrong, 2=hard, 3=good, 4=easy, 5=perfect)", parse_mode='Markdown')
        session["showed_back"] = True
        return True
    else:
        # Expect quality rating
        try:
            quality = int(update.message.text.strip())
            if quality < 0 or quality > 5:
                raise ValueError
        except:
            await update.message.reply_text("Please enter a number between 0 and 5.")
            return True
        # Update card using SM-2
        idx, card = session["current_card"]
        new_reps, new_ease, new_interval = calculate_next_review(quality, card["repetitions"], card["ease"], card["interval"])
        card["repetitions"] = new_reps
        card["ease"] = new_ease
        card["interval"] = new_interval
        next_review = datetime.now() + timedelta(days=new_interval)
        card["next_review"] = next_review.isoformat()
        memory["flashcards"]["decks"][session["deck"]]["cards"][idx] = card
        save_json("flashcards", memory["flashcards"])
        # Move to next card
        session["current_index"] += 1
        session["awaiting_quality"] = False
        session["showed_back"] = False
        await send_next_card(update, context)
        return True

# ---------- Study notes ----------
async def add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /add_note <title> | <content> (use pipe to separate)")
        return
    text = " ".join(args)
    if "|" not in text:
        await update.message.reply_text("Separate title and content with a pipe '|'")
        return
    title, content = text.split("|", 1)
    title = title.strip()
    content = content.strip()
    note = {
        "id": str(int(datetime.timestamp(datetime.now()))),
        "title": title,
        "content": content,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat()
    }
    memory["study_notes"]["notes"].append(note)
    save_json("study_notes", memory["study_notes"])
    await update.message.reply_text(f"✅ Note saved: '{title}'")

async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = memory["study_notes"]["notes"]
    if not notes:
        await update.message.reply_text("No notes. Add one with /add_note")
        return
    msg = "📝 *Your Notes*\n"
    for note in notes[-10:]:
        msg += f"• {note['title']} (id: {note['id'][-6:]})\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /view_note <note_id>")
        return
    note_id = args[0]
    for note in memory["study_notes"]["notes"]:
        if note["id"].startswith(note_id) or note_id in note["title"]:
            await update.message.reply_text(f"*{note['title']}*\n\n{note['content']}\n\nCreated: {note['created'][:10]}", parse_mode='Markdown')
            return
    await update.message.reply_text("Note not found.")

async def delete_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete_note <note_id>")
        return
    note_id = args[0]
    for i, note in enumerate(memory["study_notes"]["notes"]):
        if note["id"].startswith(note_id) or note_id in note["title"]:
            deleted = memory["study_notes"]["notes"].pop(i)
            save_json("study_notes", memory["study_notes"])
            await update.message.reply_text(f"Deleted note: {deleted['title']}")
            return
    await update.message.reply_text("Note not found.")

# ---------- Advanced analytics: heatmap, consistency ----------
async def heatmap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show study activity heatmap (last 30 days)"""
    logs = memory["progress"]["logs"]
    day_counts = Counter()
    for log in logs:
        day = log["timestamp"][:10]
        day_counts[day] += 1
    today = date.today()
    days = []
    for i in range(30, -1, -1):
        d = today - timedelta(days=i)
        days.append((d.isoformat(), day_counts.get(d.isoformat(), 0)))
    msg = "📊 *Study Heatmap (last 31 days)*\n\n"
    # Simple text heatmap
    weekdays = ["M", "T", "W", "T", "F", "S", "S"]
    msg += "    " + " ".join(weekdays) + "\n"
    for i in range(0, len(days), 7):
        week = days[i:i+7]
        if not week:
            break
        week_str = f"{week[0][0][5:10]} "
        for d, count in week:
            if count == 0:
                week_str += "⬜"
            elif count < 3:
                week_str += "🟩"
            elif count < 6:
                week_str += "🟨"
            else:
                week_str += "🟥"
        msg += week_str + "\n"
    msg += "\n⬜=0 tasks  🟩=1-2  🟨=3-5  🟥=6+ tasks"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def consistency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculate consistency score"""
    stats = memory["stats"]
    total_days = stats.get("total_study_days", 0)
    if total_days == 0:
        await update.message.reply_text("No study days recorded yet.")
        return
    streak = stats.get("streak", 0)
    longest = stats.get("longest_streak", 0)
    # Consistency = (streak / longest) * 70 + (total_days / days_since_start) * 30
    first_log = None
    for log in memory["progress"]["logs"]:
        d = log["timestamp"][:10]
        if first_log is None or d < first_log:
            first_log = d
    if first_log:
        days_since_start = (date.today() - date.fromisoformat(first_log)).days + 1
        ratio_days = total_days / max(days_since_start, 1)
    else:
        ratio_days = 1
    ratio_streak = streak / max(longest, 1)
    consistency = (ratio_streak * 70) + (ratio_days * 30)
    consistency = min(100, int(consistency))
    msg = f"📈 *Consistency Score:* {consistency}/100\n"
    msg += f"• Current streak: {streak} days\n• Longest streak: {longest} days\n• Total study days: {total_days}\n"
    if consistency >= 80:
        msg += "🌟 Excellent consistency! Keep it up!"
    elif consistency >= 60:
        msg += "👍 Good consistency. Try to study every day."
    else:
        msg += "⚠️ Try to study daily to improve your score."
    await update.message.reply_text(msg, parse_mode='Markdown')

# ---------- AI integration: extend ask_ai_smart to handle flashcards and notes ----------
# We'll modify ask_ai_smart by adding a fallback in the updates list for flashcard/note actions.
# Since we cannot modify the original function directly in this extension, we'll add a new handler
# that intercepts natural language for flashcards/notes and overrides if needed.
# Alternatively, we can monkey-patch after the original definition. For simplicity, we'll add a
# separate command for AI to create flashcards via natural language: /ai_flashcard <front> | <back>
async def ai_flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Use AI to generate a flashcard from natural language"""
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /ai_flashcard <front> | <back> or just describe what you want to learn")
        return
    if "|" in text:
        front, back = text.split("|", 1)
        front = front.strip()
        back = back.strip()
        await update.message.reply_text(f"Proposed flashcard:\nFront: {front}\nBack: {back}\n\nReply with /add_card <deck> {front} | {back} to save.")
    else:
        # Use AI to generate a flashcard from a topic
        await context.bot.send_chat_action(update.effective_chat.id, action="typing")
        prompt = f"Generate a JEE flashcard (front/back) for the topic: {text}. Return only in format: FRONT: ... BACK: ..."
        response = ask_ai_smart(prompt, update.effective_chat.id)
        reply = response.get("response", "Could not generate.")
        await update.message.reply_text(reply)
    async def notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configure periodic notifications."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /notify on|off|interval <minutes>|status")
        return
    action = args[0].lower()
    chat_id = update.effective_chat.id
    notify_settings = memory["schedule"].get("notifications", {"enabled": True, "interval_minutes": 120})
    if action == "on":
        notify_settings["enabled"] = True
        memory["schedule"]["notifications"] = notify_settings
        save_json("schedule", memory["schedule"])
        schedule_notifications(context.application.job_queue, chat_id,
                              notify_settings.get("interval_minutes",120), True)
        await update.message.reply_text("✅ Notifications enabled.")
    elif action == "off":
        notify_settings["enabled"] = False
        memory["schedule"]["notifications"] = notify_settings
        save_json("schedule", memory["schedule"])
        schedule_notifications(context.application.job_queue, chat_id, 0, False)
        await update.message.reply_text("🔕 Notifications disabled.")
    elif action == "interval" and len(args) >= 2:
        try:
            interval = int(args[1])
            if interval < 15:
                await update.message.reply_text("Interval must be at least 15 minutes.")
                return
            notify_settings["interval_minutes"] = interval
            memory["schedule"]["notifications"] = notify_settings
            save_json("schedule", memory["schedule"])
            schedule_notifications(context.application.job_queue, chat_id,
                                  interval, notify_settings.get("enabled",True))
            await update.message.reply_text(f"⏲️ Notification interval set to {interval} minutes.")
        except ValueError:
            await update.message.reply_text("Please provide a number (minutes).")
    elif action == "status":
        enabled = notify_settings.get("enabled", True)
        interval = notify_settings.get("interval_minutes", 120)
        await update.message.reply_text(f"🔔 Notifications: {'ON' if enabled else 'OFF'}\n⏱️ Interval: {interval} minutes")
    else:
        await update.message.reply_text("Invalid. Use: /notify on|off|interval <minutes>|status")

# ---------- Register new command handlers ----------
# Add these lines to your main block (you will need to add them manually or we can provide the entire main block again)
# For now, we just provide the command definitions; the user must append to the handler registration section.

# The following function should be added to your main block's command registrations:
# app.add_handler(CommandHandler("create_deck", create_deck))
# app.add_handler(CommandHandler("add_card", add_card))
# app.add_handler(CommandHandler("list_decks", list_decks))
# app.add_handler(CommandHandler("review", review_deck))
# app.add_handler(CommandHandler("add_note", add_note))
# app.add_handler(CommandHandler("list_notes", list_notes))
# app.add_handler(CommandHandler("view_note", view_note))
# app.add_handler(CommandHandler("delete_note", delete_note))
# app.add_handler(CommandHandler("heatmap", heatmap_cmd))
# app.add_handler(CommandHandler("consistency", consistency_cmd))
# app.add_handler(CommandHandler("ai_flashcard", ai_flashcard))

# Also modify handle_message to intercept review answers during a review session.
# Add the following at the beginning of handle_message (or inside the natural language branch):
# if context.user_data.get("review_session") and context.user_data["review_session"].get("awaiting_quality"):
#     await handle_review_answer(update, context)
#     return

# Because this extension does not overwrite your existing code, you need to manually add the above lines to your main block and handle_message.
# I'll provide a patch section below that you can copy and paste to insert.


# ---------- Main ----------
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start_day", start_day_wrapper))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("weekly_report", week_report_cmd))
    app.add_handler(CommandHandler("set_test", set_test_cmd))
    app.add_handler(CommandHandler("view_tests", view_tests_cmd))
    app.add_handler(CommandHandler("set_schedule", set_schedule_cmd))
    app.add_handler(CommandHandler("add_backlog", add_backlog_cmd))
    app.add_handler(CommandHandler("view_backlog", view_backlog))
    app.add_handler(CommandHandler("complete_task", complete_task_cmd))
    app.add_handler(CommandHandler("update_syllabus", update_syllabus_cmd))
    app.add_handler(CommandHandler("view_syllabus", view_syllabus))
    app.add_handler(CommandHandler("custom_chapter", custom_chapter_cmd))
    app.add_handler(CommandHandler("notify", notify_cmd))
    app.add_handler(CommandHandler("remind_me", remind_me_cmd))
    app.add_handler(CommandHandler("pomodoro", pomodoro_cmd))
    app.add_handler(CommandHandler("break", break_cmd))
    app.add_handler(CommandHandler("focus", focus_cmd))
    app.add_handler(CommandHandler("set_goal", set_goal_cmd))
    app.add_handler(CommandHandler("goal_status", goal_status_cmd))
    app.add_handler(CommandHandler("points", points_cmd))
    app.add_handler(CommandHandler("challenge", challenge_cmd))
    app.add_handler(CommandHandler("reward", reward_cmd))
    app.add_handler(CommandHandler("create_deck", create_deck))
    app.add_handler(CommandHandler("add_card", add_card))
    app.add_handler(CommandHandler("list_decks", list_decks))
    app.add_handler(CommandHandler("review", review_deck))
    app.add_handler(CommandHandler("add_note", add_note))
    app.add_handler(CommandHandler("list_notes", list_notes))
    app.add_handler(CommandHandler("view_note", view_note))
    app.add_handler(CommandHandler("delete_note", delete_note))
    app.add_handler(CommandHandler("heatmap", heatmap_cmd))
    app.add_handler(CommandHandler("consistency", consistency_cmd))
    app.add_handler(CommandHandler("ai_flashcard", ai_flashcard))
    app.add_handler(CommandHandler("trends", trends_cmd))
    app.add_handler(CommandHandler("correlation", correlation_cmd))
    app.add_handler(CommandHandler("efficiency", efficiency_cmd))
    app.add_handler(CommandHandler("export_data", export_data_cmd))
    app.add_handler(CommandHandler("brain_dump", brain_dump_cmd))
    app.add_handler(CommandHandler("study_tips", study_tips_cmd))
    app.add_handler(CommandHandler("motivate", motivate_cmd))
    app.add_handler(CommandHandler("formula", formula_cmd))
    app.add_handler(CommandHandler("daily_quote", daily_quote_cmd))
    app.add_handler(CommandHandler("chat", chat_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("week_update", week_update_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(CallbackQueryHandler(callback_handler))
    if app.job_queue:
        schedule_reminders(app.job_queue)
        schedule_test_followups(app)
    logger.info("Ultra‑extensive JEE Study OS started.")
    app.run_polling()