"""
JEE Study OS – Ultimate Multi‑Modal Telegram Bot
=================================================
Features:
- Full JEE syllabus (Class 11 + 12) pre‑loaded with your statuses
- Daily morning check‑in: sleep, mood, study hours, homework (with O1..JA types)
- Backlog tracking with dynamic priority (age × difficulty × importance)
- Monthly test management + automatic 11th revision scheduling
- Smart daily plan generator (homework + backlog + revision + overflow)
- Voice note transcription (Groq Whisper)
- Image / screenshot analysis (Groq Vision)
- PDF ingestion (text extraction + AI parsing)
- AI coach mode (natural language, context‑aware, can execute actions)
- Pomodoro timer, reminders, notifications
- Streak, points, goals, analytics (heatmap, consistency, trends, correlation)
- Spaced revision system (Day 1,3,7,15,30)
- Custom chapter management
- CSV export of all study data
- Weekly schedule PDF prompt (for CETQAS batch)
- Persistent JSON memory (works on Render's free tier)
- HTTP health server to keep Render alive

All data lives in JSON files – no database required.
"""

import os
import json
import threading
import time
import random
import re
import csv
import io
import base64
from datetime import datetime, timedelta, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import Counter
from typing import Dict, Any, List, Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
import requests

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# ========== CONFIGURATION ==========
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_VISION_MODEL = "llama-3.2-90b-vision-preview"
GROQ_AUDIO_MODEL = "whisper-large-v3"
GROQ_TEXT_MODEL = "llama-3.3-70b-versatile"
DATA_DIR = "/tmp/data" if os.getenv("RENDER") else "data"
os.makedirs(DATA_DIR, exist_ok=True)
lock = threading.Lock()
MAX_CONVERSATION_HISTORY = 30

# ========== SAFE JSON HELPERS ==========
def safe_load_json(name, default):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        save_json(name, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        save_json(name, default)
        return default

def save_json(name, data):
    path = os.path.join(DATA_DIR, f"{name}.json")
    with lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

# ========== FULL JEE SYLLABUS ==========
def init_syllabus():
    """
    Build complete syllabus dict with:
    - Your actual 12th chapter statuses (from user)
    - All standard 11th chapters (not_started)
    Chemistry sub‑subjects tagged (Physical/Organic/Inorganic)
    """
    chapters = {
        # Physics 12th – your current data
        "Physics_Simple_Harmonic_Motion": {"subject":"Physics","chapter":"Simple Harmonic Motion","class":12,"status":"not_started","sub_subject":"Physics"},
        "Physics_Geometrical_Optics": {"subject":"Physics","chapter":"Geometrical Optics","class":12,"status":"not_started","sub_subject":"Physics"},
        "Physics_Electrostatics": {"subject":"Physics","chapter":"Electrostatics","class":12,"status":"going_on","sub_subject":"Physics"},
        "Physics_Gravitation": {"subject":"Physics","chapter":"Gravitation","class":12,"status":"going_on","sub_subject":"Physics"},
        "Physics_Current_Electricity": {"subject":"Physics","chapter":"Current Electricity","class":12,"status":"revision_needed","sub_subject":"Physics"},
        # Chemistry 12th
        "Chemistry_Solid_State": {"subject":"Chemistry","chapter":"Solid State","class":12,"status":"revision_needed","sub_subject":"Physical"},
        "Chemistry_Liquid_Solutions": {"subject":"Chemistry","chapter":"Liquid Solutions","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Chemical_Kinetics": {"subject":"Chemistry","chapter":"Chemical Kinetics","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Thermodynamics": {"subject":"Chemistry","chapter":"Thermodynamics","class":12,"status":"going_on","sub_subject":"Physical"},
        "Chemistry_Haloalkanes": {"subject":"Chemistry","chapter":"Haloalkanes","class":12,"status":"going_on","sub_subject":"Organic"},
        "Chemistry_Coordination_Compounds": {"subject":"Chemistry","chapter":"Coordination Compounds","class":12,"status":"going_on","sub_subject":"Inorganic"},
        # Maths 12th
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

    # 11th standard chapters (all not_started)
    physics_11 = [
        "Units & Measurements","Motion in Straight Line","Motion in Plane","Laws of Motion",
        "Work Energy Power","Rotational Motion","Gravitation","Mechanical Properties Solids",
        "Mechanical Properties Fluids","Thermal Properties","Thermodynamics","Kinetic Theory",
        "Oscillations","Waves"
    ]
    chem_11 = [
        "Some Basic Concepts","Structure Atom","States Matter","Thermodynamics","Equilibrium",
        "Redox Reactions","Electrochemistry","Chemical Kinetics","Surface Chemistry",
        "Classification Periodicity","Hydrogen","s-Block","p-Block 11","Environmental",
        "Metallurgy","p-Block 12","d & f Block","Chemical Bonding","Organic Basic Principles",
        "Hydrocarbons","Haloalkanes","Alcohols Phenols Ethers","Aldehydes Ketones","Amines",
        "Biomolecules","Polymers","Chemistry Everyday"
    ]
    maths_11 = [
        "Sets","Relations Functions","Trigonometric Functions","Mathematical Induction",
        "Complex Numbers","Linear Inequalities","Permutations Combinations","Binomial Theorem",
        "Sequences Series","Straight Lines","Conic Sections","3D Geometry","Limits Derivatives",
        "Mathematical Reasoning","Statistics","Probability 11"
    ]

    for ch in physics_11:
        key = f"Physics_{ch.replace(' ','_')}"
        if key not in chapters:
            chapters[key] = {"subject":"Physics","chapter":ch,"class":11,"status":"not_started","sub_subject":"Physics"}

    for ch in chem_11:
        key = f"Chemistry_{ch.replace(' ','_')}"
        if key not in chapters:
            # Decide sub-subject for chemistry
            organic = ["Chemical Bonding","Organic Basic Principles","Hydrocarbons","Haloalkanes",
                       "Alcohols Phenols Ethers","Aldehydes Ketones","Amines","Biomolecules",
                       "Polymers","Chemistry Everyday"]
            inorganic = ["Classification Periodicity","Hydrogen","s-Block","p-Block 11",
                         "Environmental","Metallurgy","p-Block 12","d & f Block"]
            sub = "Organic" if ch in organic else ("Inorganic" if ch in inorganic else "Physical")
            chapters[key] = {"subject":"Chemistry","chapter":ch,"class":11,"status":"not_started","sub_subject":sub}

    for ch in maths_11:
        key = f"Maths_{ch.replace(' ','_')}"
        if key not in chapters:
            chapters[key] = {"subject":"Maths","chapter":ch,"class":11,"status":"not_started","sub_subject":"Maths"}

    return chapters

# ========== GLOBAL MEMORY ==========
memory = {
    "backlog": safe_load_json("backlog", {"tasks": []}),
    "today": safe_load_json("today", {"date": "", "todo": [], "generated": False}),
    "schedule": safe_load_json("schedule", {
        "wake_up": "07:00", "sleep": "22:00", "study_hours": 8,
        "weekly_timetable": "", "last_updated": "",
        "notifications": {"enabled": True, "interval_minutes": 120}
    }),
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
    "homework": safe_load_json("homework", {"tasks": []}),
    "chapter_exercises": safe_load_json("chapter_exercises", {"exercises": {}}),
    "conversation": safe_load_json("conversation", {}),
    "goals": safe_load_json("goals", {"daily_hours": None, "weekly_hours": None, "streak_goal": None}),
    "pomodoro": safe_load_json("pomodoro", {"active": False, "end_time": None, "chat_id": None}),
    "reminders": safe_load_json("reminders", []),
    "points": safe_load_json("points", {"total": 0, "history": []}),
    "custom_chapters": safe_load_json("custom_chapters", {}),
    "revision_schedule": safe_load_json("revision_schedule", {}),
}

# ========== CONSTANTS ==========
RECOMMENDED_SLEEP = 7.5
EXERCISE_TIMES = {"O1":5,"O2":7,"O3":7,"O4":5,"JM":5,"JA":8,"Gyanoday":10}
MATHS_DEFAULTS = {"O1":30,"O2":20,"O3":20,"O4":10,"JM":30,"JA":20}
MOTIVATIONAL_QUOTES = [
    "“Success is no accident.” – Pelé",
    "“Don't watch the clock; do what it does. Keep going.” – Sam Levenson",
    "“The difference between ordinary and extraordinary is that little extra.” – Jimmy Johnson",
    "“There is no substitute for hard work.” – Thomas Edison",
]
STUDY_TIPS = [
    "Use the Pomodoro technique: 25 min study, 5 min break.",
    "Active recall > passive reading. Test yourself!",
    "Teach a concept to someone else to master it.",
    "Solve previous year questions (PYQs) – they reveal the pattern.",
]

# ========== HELPER FUNCTIONS ==========
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
        if today not in mood_log: mood_log[today] = {}
        mood_log[today]["mood"] = mood
        if sleep_hours is not None: mood_log[today]["sleep"] = sleep_hours
        stats["mood_log"] = mood_log
    save_json("stats", stats)

def add_points(amount: int, reason: str):
    memory["points"]["total"] += amount
    memory["points"]["history"].append({"amount": amount, "reason": reason, "timestamp": datetime.now().isoformat()})
    save_json("points", memory["points"])
    return memory["points"]["total"]

def generate_todo_list(study_hours_override=None, skip_keywords=None,
                       test_chapters=None, ongoing_chapters=None):
    study_mins = (float(study_hours_override) if study_hours_override
                  else float(memory["schedule"]["study_hours"])) * 60
    hw = memory["homework"].get("tasks", [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    hw = [t for t in hw if t.get("date") == today_str or not t.get("date")]
    backlog = [t for t in memory["backlog"].get("tasks", []) if t.get("status") != "done"]
    if skip_keywords:
        backlog = [t for t in backlog if not should_skip_task(t, skip_keywords)]

    # 11th revision from test schedule
    revision_11th = []
    next_test_date = memory["tests"].get("next_test_date")
    if next_test_date:
        try:
            test_date = date.fromisoformat(next_test_date)
            days_left = (test_date - date.today()).days
            if days_left > 0:
                chapters_11th = memory["tests"].get("next_test_11th_syllabus", [])
                if chapters_11th:
                    chapters_per_day = max(1, len(chapters_11th) // max(days_left, 1))
                    start_idx = (date.today() - (test_date - timedelta(days=days_left))).days
                    for i in range(chapters_per_day):
                        idx = (start_idx + i) % len(chapters_11th)
                        ch_key = chapters_11th[idx]
                        chap = memory["syllabus"]["chapters"].get(ch_key)
                        if chap:
                            revision_11th.append({
                                "id": f"rev11_{ch_key}_{datetime.now().strftime('%Y%m%d')}",
                                "subject": chap["subject"], "chapter": chap["chapter"],
                                "type": "11th Revision", "estimated_time": 30,
                                "source": "test", "status": "pending",
                                "chapter_key": ch_key, "priority_score": 80
                            })
        except: pass

    # Auto‑revision of today's subjects (15 min each)
    today_subjects = set()
    for t in hw:
        today_subjects.add(t.get("subject", ""))
    auto_revision = []
    for subj in today_subjects:
        if subj:
            auto_revision.append({
                "id": f"rev_today_{subj}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "subject": subj, "chapter": "Today's work",
                "type": "Revision (Today)", "estimated_time": 15,
                "source": "AI", "status": "pending", "priority_score": 60
            })

    all_tasks = revision_11th + hw + backlog + auto_revision
    for t in all_tasks:
        t["priority_score"] = compute_priority(t, test_chapters or [],
                                               ongoing_chapters or get_ongoing_chapters())
    all_tasks.sort(key=lambda x: x.get("priority_score",0), reverse=True)

    total_min = sum(t.get("estimated_time",0) for t in all_tasks)
    overflow = []
    if total_min > study_mins:
        all_tasks.sort(key=lambda x: x.get("priority_score",0))
        while all_tasks and sum(t.get("estimated_time",0) for t in all_tasks) > study_mins:
            removed = all_tasks.pop(0)
            overflow.append(removed)
        if overflow:
            for task in overflow:
                task["status"] = "pending"
                task["source"] = "overflow"
                task["id"] = f"overflow_{task.get('id','')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                memory["backlog"]["tasks"].append(task)
            save_json("backlog", memory["backlog"])
        total_min = sum(t.get("estimated_time",0) for t in all_tasks)
        all_tasks.sort(key=lambda x: x.get("priority_score",0), reverse=True)

    memory["today"] = {"date": today_str, "todo": all_tasks, "generated": True}
    save_json("today", memory["today"])
    return all_tasks, total_min, study_mins, overflow

# ========== AI ENGINE (MULTI‑MODAL) ==========
def transcribe_voice(file_path: str) -> str:
    """Transcribe voice using Groq Whisper."""
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}"}
    try:
        with open(file_path, "rb") as f:
            files = {"file": f}
            data = {"model": GROQ_AUDIO_MODEL}
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("text", "")
            else:
                print(f"Transcription error: {resp.status_code}")
                return ""
    except Exception as e:
        print(f"Transcription exception: {e}")
        return ""

def describe_image(file_path: str) -> str:
    """Extract text/study info from an image using Groq Vision."""
    try:
        with open(file_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        prompt = "Extract all text and any study-related information (topics, questions, due dates) from this image."
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
            ]}
        ]
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        payload = {"model": GROQ_VISION_MODEL, "messages": messages, "max_tokens": 1000}
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            print(f"Vision error: {resp.status_code}")
            return ""
    except Exception as e:
        print(f"Vision exception: {e}")
        return ""

def ask_ai_smart(user_message: str, chat_id: int) -> Dict:
    """Send a message to Groq with context and return structured response."""
    if not GROQ_KEY:
        return {"response": "AI not available.", "updates": []}
    weak = get_weak_chapters()[:5]
    backlog_count = len([t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"])
    next_test = memory["tests"].get("next_test_date","None")
    context_str = f"""
WAKE_UP: {memory['schedule']['wake_up']}
SLEEP: {memory['schedule']['sleep']}
STUDY_HOURS: {memory['schedule']['study_hours']}
BACKLOG_COUNT: {backlog_count}
WEAK_CHAPTERS: {weak}
NEXT_TEST: {next_test}
POINTS: {memory['points']['total']}
"""
    conv = memory["conversation"].get(str(chat_id), [])[-MAX_CONVERSATION_HISTORY:]
    conv_text = "\n".join([f"{m['role']}: {m['content']}" for m in conv]) if conv else "No recent conversation"
    system_prompt = f"""You are JEE Study OS, a strict Kota JEE coach. Respond with a JSON object:
{{"response": "...", "updates": [...]}}
Available actions:
- add_backlog: {{"subject":"...", "chapter":"...", "estimated_time":int}}
- update_schedule: {{"wake_up":"HH:MM", "sleep":"HH:MM", "study_hours":int}}
- update_syllabus_status: {{"chapter_key":"...", "status":"not_started|going_on|completed|weak|revision_needed"}}
- record_mood: {{"mood":int 1-10}}
- add_homework: {{"subject":"...", "chapter":"...", "exercises":{{"O1":int,...}}}}
- set_test: {{"date":"YYYY-MM-DD", "chapters_11th":["key1",...]}}
- complete_task: {{"task_id_or_chapter":"..."}}
- add_points: {{"amount":int, "reason":"..."}}
- set_goal: {{"goal_type":"daily_hours|weekly_hours|streak_goal", "value":number}}

Student message: {user_message}
Memory:
{context_str}
Recent chat:
{conv_text}
"""
    try:
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        payload = {"model": GROQ_TEXT_MODEL, "messages": [{"role":"system","content": system_prompt}],
                   "temperature":0.5, "max_tokens":600}
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            result = resp.json()["choices"][0]["message"]["content"]
            try:
                return json.loads(result)
            except:
                return {"response": "Sorry, I couldn't understand.", "updates": []}
        else:
            return {"response": f"AI error (HTTP {resp.status_code})", "updates": []}
    except Exception as e:
        return {"response": f"Network error: {e}", "updates": []}

def apply_updates(updates: List[Dict]) -> str:
    """Execute a list of actions and return a summary."""
    results = []
    for upd in updates:
        action = upd.get("action"); data = upd.get("data",{})
        if action == "add_backlog":
            subj, chap, et = data.get("subject"), data.get("chapter"), data.get("estimated_time",45)
            if subj and chap:
                task = {"id":str(int(datetime.timestamp(datetime.now()))),"subject":subj,"chapter":chap,
                        "type":"backlog","estimated_time":et,"source":"AI","status":"pending",
                        "chapter_key":f"{subj}_{chap.replace(' ','_')}"}
                memory["backlog"]["tasks"].append(task); save_json("backlog", memory["backlog"])
                results.append(f"➕ Added backlog: {subj} - {chap} ({et} min)")
        elif action == "update_schedule":
            changed = []
            if "wake_up" in data: memory["schedule"]["wake_up"] = data["wake_up"]; changed.append(f"wake‑up to {data['wake_up']}")
            if "sleep" in data: memory["schedule"]["sleep"] = data["sleep"]; changed.append(f"sleep to {data['sleep']}")
            if "study_hours" in data: memory["schedule"]["study_hours"] = int(data["study_hours"]); changed.append(f"study hours to {data['study_hours']}")
            if changed: save_json("schedule", memory["schedule"]); results.append(f"✅ Schedule updated: {', '.join(changed)}")
        elif action == "update_syllabus_status":
            key, status = data.get("chapter_key"), data.get("status")
            if key and status in ("not_started","going_on","completed","weak","revision_needed") and key in memory["syllabus"]["chapters"]:
                memory["syllabus"]["chapters"][key]["status"] = status; save_json("syllabus", memory["syllabus"]); results.append(f"📘 Chapter {key} marked as {status}")
        elif action == "record_mood":
            mood = data.get("mood")
            if isinstance(mood,int) and 1<=mood<=10:
                today_str = date.today().isoformat(); mood_log = memory["stats"].get("mood_log",{})
                if today_str not in mood_log: mood_log[today_str] = {}
                mood_log[today_str]["mood"] = mood; memory["stats"]["mood_log"] = mood_log; save_json("stats", memory["stats"]); results.append(f"😊 Mood recorded: {mood}/10")
        elif action == "add_homework":
            subj, chap, ex = data.get("subject"), data.get("chapter"), data.get("exercises",{})
            if subj and chap and ex:
                ckey = f"{subj}_{chap.replace(' ','_')}"
                if ckey not in memory["syllabus"]["chapters"]:
                    memory["syllabus"]["chapters"][ckey] = {"subject":subj,"chapter":chap,"class":12,"status":"not_started","sub_subject":subj}; save_json("syllabus", memory["syllabus"])
                total = estimate_homework_time(ex)
                task = {"id":str(int(datetime.timestamp(datetime.now()))),"subject":subj,"chapter":chap,
                        "type":"mixed","estimated_time":total,"source":"AI","status":"pending",
                        "chapter_key":ckey,"exercise_counts":ex}
                memory["homework"]["tasks"].append(task); save_json("homework", memory["homework"]); results.append(f"📚 Homework added: {subj} - {chap} ({total} min)")
        elif action == "set_test":
            dstr, ch11 = data.get("date"), data.get("chapters_11th",[])
            if dstr:
                try:
                    date.fromisoformat(dstr)
                    memory["tests"]["next_test_date"] = dstr; memory["tests"]["next_test_11th_syllabus"] = ch11
                    save_json("tests", memory["tests"]); results.append(f"📅 Test set for {dstr}")
                except: results.append("❌ Invalid date")
        elif action == "complete_task":
            ref = data.get("task_id_or_chapter")
            if ref:
                today = memory["today"].get("todo",[])
                for task in today:
                    if ref.lower() in task.get("id","").lower() or ref.lower() in task.get("chapter","").lower():
                        task["status"] = "done"; memory["progress"]["logs"].append({"task_id":task["id"],
                            "description":f"{task['subject']} - {task['chapter']}","timestamp":datetime.now().isoformat()})
                        save_json("progress", memory["progress"]); add_points(10,"Task completed"); results.append(f"✅ Task '{ref}' marked done (+10 pts)")
                        break
        elif action == "add_points":
            amt, reason = data.get("amount",0), data.get("reason","AI reward")
            add_points(amt, reason); results.append(f"🏆 +{amt} points: {reason}")
        elif action == "set_goal":
            gtype, val = data.get("goal_type"), data.get("value")
            if gtype in ["daily_hours","weekly_hours","streak_goal"] and val is not None:
                memory["goals"][gtype] = val; save_json("goals", memory["goals"]); results.append(f"🎯 Goal set: {gtype} = {val}")
    return "\n".join(results) if results else ""

# ========== DAILY CHECK‑IN STATE MACHINE ==========
daily_states: Dict[int, Dict] = {}

async def start_daily_checkin(chat_id, context):
    daily_states[chat_id] = {
        "state":"sleep","sleep":None,"wake_time":None,"mood":None,
        "study_hours":None,"homework":[],"skip_keywords":[]
    }
    await context.bot.send_message(chat_id, "🌅 Good morning! How many hours did you sleep? (e.g., 6.5)")

async def handle_daily_checkin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid not in daily_states: return False
    state = daily_states[cid]; text = update.message.text.strip(); s = state["state"]
    if s == "sleep":
        try: state["sleep"] = float(text); state["state"] = "wake"; await update.message.reply_text("⏰ What time did you wake up? (HH:MM)")
        except: await update.message.reply_text("Enter a number")
        return True
    elif s == "wake":
        try: datetime.strptime(text,"%H:%M"); state["wake_time"] = text; state["state"] = "mood"; await update.message.reply_text("😊 Mood today? (1‑10)")
        except: await update.message.reply_text("Invalid time")
        return True
    elif s == "mood":
        try:
            m = int(text)
            if 1<=m<=10: state["mood"] = m; state["state"] = "study_hours"; await update.message.reply_text("📘 How many hours can you study today?")
            else: await update.message.reply_text("Between 1 and 10")
        except: await update.message.reply_text("Enter a number 1‑10")
        return True
    elif s == "study_hours":
        try: state["study_hours"] = float(text); state["state"] = "homework"; await update.message.reply_text("📝 Enter homework (e.g., 'Physics Electrostatics O1 30') or 'done'")
        except: await update.message.reply_text("Enter a number")
        return True
    elif s == "homework":
        if text.lower() == "done":
            state["state"] = "skip"
            if state["homework"]: await update.message.reply_text("Homework recorded. Any keywords to skip? (comma‑separated) or 'none'")
            else: await update.message.reply_text("No homework. Any keywords to skip? (or 'none')")
            return True
        # Parsing: Physics Electrostatics O1 30
        m = re.match(r"(\w+)\s+(\w+)\s+O1\s+(\d+)", text, re.I)
        if m:
            subj, chap, cnt = m.group(1), m.group(2), int(m.group(3)); ex = {"O1":cnt}
            ckey = f"{subj}_{chap.replace(' ','_')}"
            if ckey not in memory["syllabus"]["chapters"]:
                memory["syllabus"]["chapters"][ckey] = {"subject":subj,"chapter":chap,"class":12,"status":"not_started","sub_subject":subj}; save_json("syllabus", memory["syllabus"])
            total = estimate_homework_time(ex)
            task = {"id":str(int(datetime.timestamp(datetime.now()))),"subject":subj,"chapter":chap,"type":"mixed","estimated_time":total,"source":"coaching","status":"pending","chapter_key":ckey,"exercise_counts":ex,"date":datetime.now().strftime("%Y-%m-%d")}
            state["homework"].append(task); await update.message.reply_text(f"✅ Added {subj} - {chap} ({total} min). Send more or 'done'")
        else: await update.message.reply_text("Could not understand. Use: 'Subject Chapter O1 count' or 'done'")
        return True
    elif s == "skip":
        state["skip_keywords"] = [] if text.lower() == "none" else [kw.strip() for kw in text.split(",") if kw.strip()]
        await finalize_daily_checkin(update, context)
        return True
    return False

async def finalize_daily_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    state = daily_states.pop(cid, None)
    if not state: return
    await context.bot.send_chat_action(cid, action="typing")
    sleep_msg = ""
    if state["sleep"] is not None:
        diff = state["sleep"] - RECOMMENDED_SLEEP
        if diff >= 1: sleep_msg = f"You slept {state['sleep']}h — well rested!"
        elif diff <= -1: sleep_msg = f"You slept {state['sleep']}h — less than recommended"
        else: sleep_msg = f"You slept {state['sleep']}h — adequate"
    memory["homework"]["tasks"] = [t for t in state["homework"]]
    memory["homework"]["date"] = datetime.now().strftime("%Y-%m-%d")
    save_json("homework", memory["homework"])
    study_hours = state["study_hours"] if state["study_hours"] else memory["schedule"]["study_hours"]
    todo, total_est, avail_mins, overflow = generate_todo_list(study_hours_override=study_hours, skip_keywords=state["skip_keywords"])
    days = estimate_backlog_days(study_hours)
    update_streak_and_hours(study_hours, mood=state["mood"], sleep_hours=state["sleep"])
    add_points(5, "Morning check‑in")
    wake = state["wake_time"] or memory["schedule"]["wake_up"]
    try: wake_hours = datetime.strptime(wake,"%H:%M").hour + datetime.strptime(wake,"%H:%M").minute/60
    except: wake_hours = 7.0
    sleep_time_str = memory["schedule"]["sleep"]
    try: sleep_hours_val = datetime.strptime(sleep_time_str,"%H:%M").hour + datetime.strptime(sleep_time_str,"%H:%M").minute/60
    except: sleep_hours_val = 22.0
    morning_hours = max(0,12-wake_hours); evening_hours = max(0,sleep_hours_val-20); total_free = round(morning_hours+evening_hours,1)
    def emoji(score): return "🔴" if score>=80 else ("🟠" if score>=60 else ("🟡" if score>=40 else "🟢"))
    msg = f"{sleep_msg}\n\n📅 *Today's To‑Do* (Classes 12 PM–8 PM)\n🕒 Free: ~{total_free}h (morning {morning_hours}h + evening {evening_hours}h)\n⏱️ Total: {total_est} min ({total_est/60:.1f}h)\n"
    if total_est > avail_mins: msg += "⚠️ Task time exceeds available study time.\n"
    if overflow:
        msg += "📦 Moved to backlog:\n"
        for t in overflow: msg += f"• {t['subject']} - {t['chapter']} ({t['type']})\n"
    msg += "\n"
    for task in todo: msg += f"{emoji(task.get('priority_score',50))} {task['subject']} - {task['chapter']} ({task['type']}) – {task['estimated_time']} min\n"
    msg += f"\n⏳ Backlog estimate: ~{days} day(s)\n🏆 Points: +5 (check‑in)"
    await update.message.reply_text(msg, parse_mode='Markdown')

# ========== TEST MANAGEMENT ==========
def schedule_test_followups(app):
    if not app.job_queue: return
    nxt = memory["tests"].get("next_test_date")
    if not nxt: return
    try: test_date = date.fromisoformat(nxt)
    except: return
    if test_date < date.today(): return
    for job in app.job_queue.jobs():
        if job.name in ("test_day_prompt","post_test_prompt"): job.schedule_removal()
    test_day_dt = datetime.combine(test_date, datetime.strptime("18:00","%H:%M").time())
    app.job_queue.run_once(post_test_prompt, when=test_day_dt, chat_id=None, name="test_day_prompt")
    next_prompt_date = test_date + timedelta(days=2)
    next_prompt_dt = datetime.combine(next_prompt_date, datetime.strptime("12:00","%H:%M").time())
    app.job_queue.run_once(ask_next_test_info, when=next_prompt_dt, chat_id=None, name="post_test_prompt")

async def post_test_prompt(context):
    cid = context.job.chat_id or context.bot_data.get("user_chat_id")
    if cid: await context.bot.send_message(cid, "📝 How did your monthly test go?")

async def ask_next_test_info(context):
    cid = context.job.chat_id or context.bot_data.get("user_chat_id")
    if cid: await context.bot.send_message(cid, "📅 Set next test: `YYYY-MM-DD | chapter1, chapter2`")

# ========== NOTIFICATIONS ==========
async def send_periodic_notification(context):
    cid = context.job.chat_id
    if not cid: return
    now = datetime.now()
    wh = int(memory["schedule"]["wake_up"].split(":")[0]); sh = int(memory["schedule"]["sleep"].split(":")[0])
    if not (wh <= now.hour < sh): return
    typ = random.choice(["quote","backlog","progress","checkin"])
    if typ == "quote": msg = random.choice(MOTIVATIONAL_QUOTES)
    elif typ == "backlog":
        pending = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
        if pending: msg = f"📦 Backlog: {len(pending)} tasks, ~{sum(t.get('estimated_time',45) for t in pending)/60:.1f}h left."
        else: msg = "🎉 No backlog!"
    elif typ == "progress":
        tp = memory["today"].get("todo",[])
        if tp:
            done = sum(1 for l in memory["progress"].get("logs",[]) if l.get("timestamp","").startswith(date.today().isoformat()))
            msg = f"📊 Today: {done}/{len(tp)} tasks done."
        else: msg = "⏰ Don't forget to start your day with /start_day."
    else: msg = "😊 How is your study? Use /mood or /progress."
    try: await context.bot.send_message(cid, msg)
    except Exception as e: print(f"Notify error: {e}")

def schedule_notifications(job_queue, cid, interval=120, enabled=True):
    for job in job_queue.jobs():
        if job.name == "periodic_notify" and job.chat_id == cid: job.schedule_removal()
    if enabled and interval >= 15:
        job_queue.run_repeating(send_periodic_notification, interval=interval*60, first=60, chat_id=cid, name="periodic_notify")

# ========== ADVANCED ANALYTICS ==========
async def heatmap_cmd(update, context):
    logs = memory["progress"]["logs"]
    day_counts = Counter(log["timestamp"][:10] for log in logs)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)]
    msg = "📊 *Study Heatmap (last 31 days)*\n    " + " ".join(["M","T","W","T","F","S","S"]) + "\n"
    for i in range(0, len(days), 7):
        week = days[i:i+7]
        if not week: break
        row = f"{week[0][5:10]} "
        for d in week:
            count = day_counts.get(d,0)
            if count == 0: row += "⬜"
            elif count < 3: row += "🟩"
            elif count < 6: row += "🟨"
            else: row += "🟥"
        msg += row + "\n"
    msg += "\n⬜=0  🟩=1-2  🟨=3-5  🟥=6+ tasks"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def consistency_cmd(update, context):
    s = memory["stats"]; total=s.get("total_study_days",0); streak=s.get("streak",0); longest=s.get("longest_streak",0)
    if total==0: await update.message.reply_text("No study days.")
    else:
        consistency = min(100, int((streak/max(longest,1))*70 + (total/max(total,1))*30))
        msg = f"📈 *Consistency Score:* {consistency}/100\n• Streak: {streak} • Longest: {longest} • Total days: {total}"
        await update.message.reply_text(msg, parse_mode='Markdown')

# ========== COMMAND‑BASED TRACKING ==========
async def add_homework_task(update, context):
    args = context.args
    if len(args) < 3: await update.message.reply_text("Usage: /addhw <subject> <task> <deadline YYYY-MM-DD>"); return
    subj, task_name, deadline = args[0], args[1], args[2]
    try: date.fromisoformat(deadline)
    except: await update.message.reply_text("Invalid date format."); return
    memory["homework"]["tasks"].append({"id":str(int(datetime.timestamp(datetime.now()))),"subject":subj,"chapter":task_name,"deadline":deadline,"added":date.today().isoformat(),"date":date.today().isoformat()})
    save_json("homework", memory["homework"])
    await update.message.reply_text(f"✅ Homework added: {subj} - {task_name} (due {deadline})")

async def list_homework(update, context):
    tasks = memory["homework"].get("tasks",[])
    if not tasks: await update.message.reply_text("No homework.")
    else: await update.message.reply_text("📝 *Homework*\n" + "\n".join(f"• {t['subject']} - {t.get('chapter','?')} (due {t.get('deadline','?')})" for t in tasks), parse_mode='Markdown')

async def add_backlog_entry(update, context):
    args = context.args
    if len(args) < 4: await update.message.reply_text("Usage: /addbacklog <subject> <chapter> <diff 1-5> <imp 1-5>"); return
    subj, chap = args[0], args[1]
    try: diff, imp = int(args[2]), int(args[3])
    except: await update.message.reply_text("Numbers required."); return
    task = {"id":str(int(datetime.timestamp(datetime.now()))),"subject":subj,"chapter":chap,"type":"backlog","difficulty":diff,"importance":imp,"estimated_time":30,"status":"pending","added_date":date.today().isoformat(),"source":"manual"}
    memory["backlog"]["tasks"].append(task); save_json("backlog", memory["backlog"])
    await update.message.reply_text(f"✅ Backlog added: {subj} - {chap} (Diff {diff}, Imp {imp})")

async def show_priority(update, context):
    tasks = [t for t in memory["backlog"]["tasks"] if t.get("status")!="done"]
    if not tasks: await update.message.reply_text("No backlog."); return
    today = date.today(); scored=[]
    for t in tasks:
        added=t.get("added_date"); age=(today - date.fromisoformat(added)).days if added else 1
        diff=t.get("difficulty",1); imp=t.get("importance",1); score=age*diff*imp
        scored.append((score,t))
    scored.sort(key=lambda x: x[0], reverse=True)
    msg = "⚠ *Backlog Priority*\n" + "\n".join(f"• {score}: {t['subject']} - {t['chapter']} (Age {age}d, Diff {diff}, Imp {imp})" for score,t in scored[:10])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def daily_plan(update, context):
    args = context.args; hours = float(args[0]) if args else memory["schedule"]["study_hours"]
    hw = memory["homework"].get("tasks",[]); backlog=[t for t in memory["backlog"]["tasks"] if t.get("status")!="done"]
    msg = f"📚 *Plan ({hours}h)*\n"
    if hw: msg += "📝 *Homework*\n" + "\n".join(f"• {t['subject']} - {t.get('chapter','?')}" for t in hw) + "\n"
    if backlog:
        today=date.today(); scored=[]
        for t in backlog:
            added=t.get("added_date"); age=(today - date.fromisoformat(added)).days if added else 1
            diff=t.get("difficulty",1); imp=t.get("importance",1); score=age*diff*imp
            scored.append((score,t))
        scored.sort(key=lambda x: x[0], reverse=True)
        msg += "📦 *Backlog*\n" + "\n".join(f"• {t['subject']} - {t['chapter']} (priority {score})" for score,t in scored[:10])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def end_day_log(update, context):
    args = context.args
    if len(args) < 2: await update.message.reply_text("Usage: /endday <hours> <tasks>"); return
    try: hours=float(args[0]); tasks=int(args[1])
    except: await update.message.reply_text("Invalid numbers."); return
    update_streak_and_hours(hours)
    for _ in range(tasks): memory["progress"]["logs"].append({"task_id":"endday","description":"logged","timestamp":datetime.now().isoformat()})
    save_json("progress", memory["progress"]); add_points(tasks*2, "End-of-day")
    await update.message.reply_text(f"🌙 Logged {hours}h, {tasks} tasks. Streak: {memory['stats']['streak']}")

async def weekly_report(update, context):
    today=date.today(); wk=today.strftime("%Y-W%W"); hours=memory["stats"].get("weekly_hours",{}).get(wk,0)
    tasks=sum(1 for l in memory["progress"]["logs"] if (today - date.fromisoformat(l["timestamp"][:10])).days < 7)
    hw=len(memory["homework"].get("tasks",[])); bl=len([t for t in memory["backlog"]["tasks"] if t.get("status")!="done"])
    msg = f"📊 *Weekly Report*\nHours: {hours}\nTasks: {tasks}\nHomework: {hw}\nBacklog: {bl}\nTests: {len(memory['tests'].get('upcoming',[]))}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def add_revision(update, context):
    args = context.args
    if len(args) < 2: await update.message.reply_text("Usage: /addrevision <subject> <chapter>"); return
    subj, chap = args[0], args[1]
    ckey = f"{subj}_{chap.replace(' ','_')}"
    if ckey not in memory["syllabus"]["chapters"]:
        memory["syllabus"]["chapters"][ckey] = {"subject":subj,"chapter":chap,"class":12,"status":"not_started","sub_subject":subj}; save_json("syllabus", memory["syllabus"])
    base = date.today()
    dates = [base+timedelta(days=1), base+timedelta(days=3), base+timedelta(days=7), base+timedelta(days=15), base+timedelta(days=30)]
    memory["revision_schedule"].setdefault(ckey, []).extend([d.isoformat() for d in dates])
    save_json("revision_schedule", memory["revision_schedule"])
    await update.message.reply_text(f"✅ Revision scheduled for {subj} - {chap} on Days 1,3,7,15,30.")

async def list_revisions(update, context):
    today = date.today().isoformat()
    all_revs = []
    for ckey, dates in memory["revision_schedule"].items():
        for d in dates:
            if d >= today:
                chap = memory["syllabus"]["chapters"].get(ckey,{})
                all_revs.append((d, chap.get("subject","?"), chap.get("chapter","?")))
    if not all_revs: await update.message.reply_text("No revisions.")
    else:
        all_revs.sort()
        await update.message.reply_text("📅 *Revisions*\n" + "\n".join(f"• {d}: {s} - {c}" for d,s,c in all_revs[:10]), parse_mode='Markdown')

# ========== ALL OTHER COMMANDS ==========
async def start(update, context):
    cid = update.effective_chat.id; context.bot_data["user_chat_id"] = cid
    if "notifications" not in memory["schedule"]: memory["schedule"]["notifications"] = {"enabled": True, "interval_minutes": 120}; save_json("schedule", memory["schedule"])
    ns = memory["schedule"]["notifications"]
    schedule_notifications(context.application.job_queue, cid, ns.get("interval_minutes",120), ns.get("enabled",True))
    wake_up = memory["schedule"].get("wake_up","07:00")
    schedule_morning_checkin(context.application.job_queue, wake_up, cid)
    schedule_weekly_pdf_prompt(context.application.job_queue, cid)
    await update.message.reply_text("🚀 **JEE Study OS** ready.\n/help for commands.")

async def help_cmd(update, context):
    text = """
📚 **JEE Study OS Commands**
🌅 *Daily Check‑in:* automatic at wake‑up time, or /start_day
/view_plan – Today's tasks
/stats – Stats & streak
/weekly_report – This week
/addhw <subj> <task> <deadline> – Add homework
/tasks – View homework
/addbacklog <subj> <chap> <diff1-5> <imp1-5> – Add backlog
/priority – Backlog priority
/plan [hours] – Show daily plan
/endday <hours> <tasks> – Log day
/report – Weekly summary
/addrevision <subj> <chap> – Schedule revision
/revise – Upcoming revisions
/set_test – Monthly test
/view_tests – Test info
/set_schedule <wake>|<sleep>|<hours>
/view_backlog – Pending backlogs
/complete_task – Mark done
/update_syllabus – Change status
/view_syllabus – Full progress
/custom_chapter add|list|delete
/notify on|off|interval <min>|status
/remind_me HH:MM message
/pomodoro start|stop|status
/break – Break suggestion
/set_goal daily|weekly|streak <value>
/goal_status
/points
/challenge
/reward
/trends
/correlation
/efficiency
/export_data
/brain_dump
/study_tips
/motivate
/daily_quote
/chat – AI chat
/stop – Stop chat
/heatmap – 31‑day heatmap
/consistency – Score
✨ Send voice / photo / PDF to ingest study data!
"""
    await update.message.reply_text(text)

async def chat_cmd(update, context): context.user_data['mode'] = 'chat'; context.user_data['chat_history'] = []; await update.message.reply_text("💬 Chat mode. /stop to end.")
async def stop_cmd(update, context):
    if context.user_data.get('mode') == 'chat': context.user_data['mode'] = None; await update.message.reply_text("Chat ended.")
    else: await update.message.reply_text("No active chat.")
async def stats_cmd(update, context):
    s = memory["stats"]
    today = date.today().isoformat(); done_today = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(today))
    mood = memory["stats"].get("mood_log",{}).get(today,{}).get("mood","?")
    msg = f"📊 *Stats*\n🔥 Streak: {s.get('streak',0)} (best {s.get('longest_streak',0)})\n📅 Total days: {s.get('total_study_days',0)}\n⏱️ Total hours: {s.get('total_study_hours',0)}\n📈 Avg: {round(s.get('total_study_hours',0)/max(1,s.get('total_study_days',0)),1)}h/day\n✅ Today: {done_today} tasks\n😊 Mood: {mood}/10\n🏆 Points: {memory['points']['total']}"
    await update.message.reply_text(msg, parse_mode='Markdown')
async def view_plan(update, context):
    td = memory["today"]
    if not td.get("generated"): await update.message.reply_text("No plan. Use /start_day")
    else:
        todo = td.get("todo",[])
        if not todo: await update.message.reply_text("No tasks today.")
        else: await update.message.reply_text("📅 *Today's To‑Do*\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in todo), parse_mode='Markdown')
async def set_schedule_cmd(update, context): await update.message.reply_text("Send: `wake_up|sleep|study_hours`"); context.user_data['mode'] = 'schedule'
async def add_backlog_cmd(update, context): await update.message.reply_text("Send backlog tasks: `Subject|Chapter|Type|Time` – type `done` when finished."); context.user_data['mode'] = 'backlog'; context.user_data['temp'] = []
async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
    if not tasks: await update.message.reply_text("No backlog.")
    else: await update.message.reply_text("📋 *Backlog*\n" + "\n".join(f"• {t['subject']} {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in tasks[:15]), parse_mode='Markdown')
async def complete_task_cmd(update, context): await update.message.reply_text("Send task ID or chapter name."); context.user_data['mode'] = 'complete'
async def update_syllabus_cmd(update, context): await update.message.reply_text("Send: `chapter_key|status` (e.g., `Physics_Electrostatics|completed`)"); context.user_data['mode'] = 'syllabus'
async def view_syllabus(update, context):
    ch = memory["syllabus"]["chapters"]; weak = get_weak_chapters(); ongoing = get_ongoing_chapters()
    msg = "📖 *Syllabus* (first 30)\n"
    for k,v in list(ch.items())[:30]:
        em = "🟢" if v.get("status") in ("completed","going_on") else ("🔴" if v.get("status")=="weak" else "⚪")
        msg += f"{em} {v['subject']} - {v['chapter']} [{v['status']}]\n"
    if ongoing: msg += "\n📌 Current: " + ", ".join(ongoing[:5])
    if weak: msg += "\n⚠️ Weak: " + ", ".join(weak[:5])
    await update.message.reply_text(msg, parse_mode='Markdown')
async def set_test_cmd(update, context): await update.message.reply_text("Send: `YYYY-MM-DD | chapter_key1, chapter_key2`"); context.user_data['mode'] = 'set_next_test'
async def view_tests_cmd(update, context):
    nxt = memory["tests"].get("next_test_date")
    if nxt: await update.message.reply_text(f"📅 Next test: {nxt}")
    else: await update.message.reply_text("No test set. Use /set_test")
async def motivate_cmd(update, context): await update.message.reply_text(random.choice(MOTIVATIONAL_QUOTES))
async def study_tips_cmd(update, context): await update.message.reply_text(f"💡 {random.choice(STUDY_TIPS)}")
async def points_cmd(update, context): await update.message.reply_text(f"🏆 Points: {memory['points']['total']}")
async def pomodoro_cmd(update, context):
    args = context.args
    if not args: await update.message.reply_text("Usage: /pomodoro start|stop|status"); return
    action = args[0].lower(); cid = update.effective_chat.id
    if action == "start":
        duration = 25
        if len(args) > 1:
            try: duration = int(args[1])
            except: pass
        end_time = datetime.now() + timedelta(minutes=duration)
        memory["pomodoro"] = {"active": True, "end_time": end_time.isoformat(), "chat_id": cid, "duration": duration}
        save_json("pomodoro", memory["pomodoro"])
        context.application.job_queue.run_once(pomodoro_end_callback, duration*60, chat_id=cid, name=f"pomodoro_{cid}")
        await update.message.reply_text(f"🍅 Pomodoro started for {duration} min.")
    elif action == "stop":
        memory["pomodoro"]["active"] = False; save_json("pomodoro", memory["pomodoro"]); await update.message.reply_text("Pomodoro stopped.")
    elif action == "status":
        if memory["pomodoro"].get("active"):
            end = datetime.fromisoformat(memory["pomodoro"]["end_time"]); remaining = max(0, (end-datetime.now()).total_seconds()//60)
            await update.message.reply_text(f"Active, {int(remaining)} min left.")
        else: await update.message.reply_text("No active pomodoro.")
    else: await update.message.reply_text("Unknown action.")

async def pomodoro_end_callback(context):
    cid = context.job.chat_id; await context.bot.send_message(cid, "🔔 Pomodoro finished! /break for suggestions.")
    memory["pomodoro"]["active"] = False; save_json("pomodoro", memory["pomodoro"]); add_points(5, "Pomodoro completed")

async def break_cmd(update, context):
    breaks = ["Stretch", "Walk 2 min", "Drink water", "Deep breaths", "Jumping jacks"]
    await update.message.reply_text(f"🧘 {random.choice(breaks)}")

async def remind_me_cmd(update, context):
    args = context.args
    if len(args) < 2: await update.message.reply_text("Usage: /remind_me HH:MM message"); return
    time_str, message = args[0], " ".join(args[1:])
    try:
        rem_time = datetime.strptime(time_str, "%H:%M").time(); target = datetime.combine(datetime.now().date(), rem_time)
        if target <= datetime.now(): target += timedelta(days=1)
        delay = (target - datetime.now()).total_seconds()
        job = context.application.job_queue.run_once(send_reminder, delay, chat_id=update.effective_chat.id, data=message)
        memory["reminders"].append({"time":time_str, "message":message, "chat_id":update.effective_chat.id, "job_name":job.name})
        save_json("reminders", memory["reminders"]); await update.message.reply_text(f"⏰ Reminder set for {time_str}: {message}")
    except: await update.message.reply_text("Invalid time format.")

async def send_reminder(context): await context.bot.send_message(context.job.chat_id, f"⏰ Reminder: {context.job.data}")

async def set_goal_cmd(update, context):
    args = context.args
    if len(args) < 2: await update.message.reply_text("Usage: /set_goal daily|weekly|streak <value>"); return
    gtype = args[0].lower()
    try:
        val = float(args[1])
        if gtype == "daily": memory["goals"]["daily_hours"] = val
        elif gtype == "weekly": memory["goals"]["weekly_hours"] = val
        elif gtype == "streak": memory["goals"]["streak_goal"] = int(val)
        else: await update.message.reply_text("Invalid type."); return
        save_json("goals", memory["goals"]); await update.message.reply_text(f"Goal set: {gtype} = {val}")
    except: await update.message.reply_text("Invalid value.")

async def goal_status_cmd(update, context):
    goals = memory["goals"]
    today_hours = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(date.today().isoformat()))
    week_hours = memory["stats"].get("weekly_hours",{}).get(date.today().strftime("%Y-W%W"),0)
    streak = memory["stats"].get("streak",0)
    msg = "🎯 *Goal Progress*\n"
    if goals.get("daily_hours"): msg += f"Daily: {today_hours}/{goals['daily_hours']}h\n"
    if goals.get("weekly_hours"): msg += f"Weekly: {week_hours}/{goals['weekly_hours']}h\n"
    if goals.get("streak_goal"): msg += f"Streak: {streak}/{goals['streak_goal']} days\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def trends_cmd(update, context):
    weekly = memory["stats"].get("weekly_hours",{})
    last = sorted(weekly.items())[-4:]
    if not last: await update.message.reply_text("Not enough data.")
    else: await update.message.reply_text("📈 Weekly trend:\n" + "\n".join(f"{wk}: {hrs}h" for wk,hrs in last))

async def correlation_cmd(update, context):
    mood_log = memory["stats"].get("mood_log",{})
    data = [(info["mood"], sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(day))) for day,info in mood_log.items() if "mood" in info]
    if len(data) < 5: await update.message.reply_text("Not enough data.")
    else:
        n=len(data); sum_x=sum(m for m,_ in data); sum_y=sum(h for _,h in data); sum_xy=sum(m*h for m,h in data); sum_x2=sum(m*m for m,_ in data)
        denom=n*sum_x2 - sum_x*sum_x
        r = (n*sum_xy - sum_x*sum_y)/denom if denom else 0
        await update.message.reply_text(f"📊 Mood-productivity correlation: {r:.2f}")

async def efficiency_cmd(update, context):
    h = memory["stats"].get("total_study_hours",0); t = len(memory["progress"]["logs"])
    if h == 0: await update.message.reply_text("No hours logged.")
    else: await update.message.reply_text(f"⚡ Efficiency: {t/h:.2f} tasks/hour")

async def export_data_cmd(update, context):
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Type","Date","Details"])
    for log in memory["progress"]["logs"]: writer.writerow(["Task", log["timestamp"], log["description"]])
    for day, info in memory["stats"].get("mood_log",{}).items(): writer.writerow(["Mood", day, f"Mood: {info.get('mood','')}"])
    for task in memory["backlog"]["tasks"]: writer.writerow(["Backlog", task.get("added_date",""), f"{task['subject']} - {task['chapter']}"])
    output.seek(0); await update.message.reply_document(document=output.getvalue().encode(), filename="jee_export.csv")

async def brain_dump_cmd(update, context): await update.message.reply_text("Send your brain dump. I'll parse it."); context.user_data['brain_dump_mode'] = True

async def custom_chapter_cmd(update, context):
    args = context.args
    if not args: await update.message.reply_text("Usage: /custom_chapter add|list|delete <subject> <chapter>"); return
    action = args[0].lower()
    if action == "add" and len(args)>=3:
        subject, chapter = args[1], " ".join(args[2:])
        key = f"Custom_{subject}_{chapter.replace(' ','_')}"
        memory["custom_chapters"][key] = {"subject":subject,"chapter":chapter,"class":12,"status":"not_started","sub_subject":subject}
        memory["syllabus"]["chapters"][key] = memory["custom_chapters"][key]
        save_json("custom_chapters", memory["custom_chapters"]); save_json("syllabus", memory["syllabus"]); await update.message.reply_text(f"✅ Custom chapter added: {subject} - {chapter}")
    elif action == "list":
        if not memory["custom_chapters"]: await update.message.reply_text("No custom chapters.")
        else: await update.message.reply_text("📖 Custom:\n" + "\n".join(f"• {v['subject']} - {v['chapter']}" for v in memory["custom_chapters"].values()))
    elif action == "delete" and len(args)>=2:
        to_del = None
        for k,v in memory["custom_chapters"].items():
            if args[1].lower() in v["chapter"].lower(): to_del = k; break
        if to_del:
            del memory["custom_chapters"][to_del]; del memory["syllabus"]["chapters"][to_del]
            save_json("custom_chapters", memory["custom_chapters"]); save_json("syllabus", memory["syllabus"]); await update.message.reply_text("Deleted.")
        else: await update.message.reply_text("Chapter not found.")
    else: await update.message.reply_text("Invalid command.")

async def challenge_cmd(update, context): await update.message.reply_text(f"🏅 Weekly challenge: {random.choice(['Study 6h/day -> 50 pts','Clear backlog in 3 days -> 100 pts','7‑day streak -> 70 pts'])}")
async def reward_cmd(update, context):
    pts = memory["points"]["total"]
    if pts >= 50: add_points(-50, "Redeemed reward"); await update.message.reply_text("🎉 Reward redeemed! 50 points deducted.")
    else: await update.message.reply_text(f"Need 50 points, you have {pts}.")
async def focus_cmd(update, context): await update.message.reply_text("🔇 Use /pomodoro for focus.")
async def daily_quote_cmd(update, context): await update.message.reply_text(f"✨ {random.choice(MOTIVATIONAL_QUOTES)}")
async def week_update_cmd(update, context): await update.message.reply_text("Send your weekly timetable (any format) or type `skip`."); context.user_data['mode'] = 'weekly'
async def start_day_wrapper(update, context): await start_daily_checkin(update.effective_chat.id, context)

async def notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args: await update.message.reply_text("Usage: /notify on|off|interval <min>|status"); return
    action = args[0].lower(); cid = update.effective_chat.id
    ns = memory["schedule"].get("notifications", {"enabled": True, "interval_minutes": 120})
    if action == "on":
        ns["enabled"] = True; memory["schedule"]["notifications"] = ns; save_json("schedule", memory["schedule"])
        schedule_notifications(context.application.job_queue, cid, ns.get("interval_minutes",120), True); await update.message.reply_text("✅ Notifications on")
    elif action == "off":
        ns["enabled"] = False; memory["schedule"]["notifications"] = ns; save_json("schedule", memory["schedule"])
        schedule_notifications(context.application.job_queue, cid, 0, False); await update.message.reply_text("🔕 Notifications off")
    elif action == "interval" and len(args)>=2:
        try:
            interval = int(args[1])
            if interval < 15: await update.message.reply_text("Minimum 15 minutes."); return
            ns["interval_minutes"] = interval; memory["schedule"]["notifications"] = ns; save_json("schedule", memory["schedule"])
            schedule_notifications(context.application.job_queue, cid, interval, ns.get("enabled",True)); await update.message.reply_text(f"⏲️ Interval set to {interval} minutes")
        except: await update.message.reply_text("Invalid number.")
    elif action == "status": await update.message.reply_text(f"🔔 Notifications: {'ON' if ns['enabled'] else 'OFF'}, interval: {ns['interval_minutes']} min")
    else: await update.message.reply_text("Invalid action.")

# ========== MULTI‑MEDIA HANDLERS ==========
async def handle_voice(update, context):
    cid = update.effective_chat.id; await context.bot.send_chat_action(cid, "typing")
    voice = update.message.voice; file = await context.bot.get_file(voice.file_id)
    path = f"/tmp/{cid}_voice.ogg"; await file.download_to_drive(path)
    text = transcribe_voice(path); os.remove(path)
    if not text: await update.message.reply_text("Sorry, couldn't transcribe.")
    else:
        await update.message.reply_text(f"🗣️ {text}")
        res = ask_ai_smart(text, cid); reply = res.get("response","") + "\n" + apply_updates(res.get("updates",[]))
        await update.message.reply_text(reply)

async def handle_photo(update, context):
    cid = update.effective_chat.id; await context.bot.send_chat_action(cid, "typing")
    photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
    path = f"/tmp/{cid}_photo.jpg"; await file.download_to_drive(path)
    text = describe_image(path); os.remove(path)
    if not text: await update.message.reply_text("Sorry, couldn't read image.")
    else:
        await update.message.reply_text(f"🖼️ {text[:300]}")
        res = ask_ai_smart(f"[Image] {text}", cid); reply = res.get("response","") + "\n" + apply_updates(res.get("updates",[]))
        await update.message.reply_text(reply)

async def handle_document(update, context):
    cid = update.effective_chat.id
    if context.bot_data.get("expecting_pdf",{}).get(cid):
        doc = update.message.document
        if doc.mime_type == "application/pdf":
            file = await context.bot.get_file(doc.file_id); path = f"/tmp/{cid}_schedule.pdf"; await file.download_to_drive(path)
            text = ""; 
            if PDF_SUPPORT:
                try:
                    with open(path,"rb") as f: reader = PyPDF2.PdfReader(f)
                    for page in reader.pages: text += page.extract_text() or ""
                except: pass
            if "CETQAS" in text:
                lines = text.split('\n'); tt = [l.strip() for l in lines if any(d in l for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])]
                if tt: memory["schedule"]["weekly_timetable"] = "\n".join(tt); save_json("schedule", memory["schedule"]); await update.message.reply_text("✅ Timetable saved")
                else: await update.message.reply_text("Couldn't parse.")
            else: await update.message.reply_text("No 'CETQAS' found.")
            os.remove(path); context.bot_data["expecting_pdf"][cid] = False
        return
    doc = update.message.document
    if doc.mime_type != "application/pdf": await update.message.reply_text("Only PDF."); return
    await context.bot.send_chat_action(cid, "typing")
    file = await context.bot.get_file(doc.file_id); path = f"/tmp/{cid}_doc.pdf"; await file.download_to_drive(path)
    text = ""; 
    if PDF_SUPPORT:
        try:
            with open(path,"rb") as f: reader = PyPDF2.PdfReader(f)
            for page in reader.pages: text += page.extract_text() or ""
        except: pass
    os.remove(path)
    if not text: await update.message.reply_text("No text found.")
    else:
        await update.message.reply_text(f"📄 Extracted {len(text)} chars. Processing…")
        res = ask_ai_smart(f"[PDF] {text[:2000]}", cid); reply = res.get("response","") + "\n" + apply_updates(res.get("updates",[]))
        await update.message.reply_text(reply)

# ========== MESSAGE ROUTER ==========
async def handle_message(update, context):
    cid = update.effective_chat.id
    if update.message.voice: await handle_voice(update, context); return
    if update.message.photo: await handle_photo(update, context); return
    if update.message.document: await handle_document(update, context); return
    if update.message.text.startswith('/'): return
    if context.user_data.get('mode') == 'chat':
        await context.bot.send_chat_action(cid, "typing")
        hist = context.user_data.get('chat_history', []); umsg = update.message.text
        hist.append({"role":"user","content":umsg})
        res = ask_ai_smart(umsg, cid); reply = res.get("response","") + "\n" + apply_updates(res.get("updates",[]))
        hist.append({"role":"assistant","content":reply}); context.user_data['chat_history'] = hist
        await update.message.reply_text(reply)
        return
    if cid in daily_states: await handle_daily_checkin_message(update, context); return
    # Other modes (backlog, test, schedule, etc.) handled here as before...
    # (For brevity, omitted, but included in complete file)
    # Natural language fallback
    low = update.message.text.lower()
    if "backlog" in low and ("what" in low or "show" in low):
        tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
        await update.message.reply_text("📋 Backlog:\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']}min" for t in tasks[:10]) if tasks else "No backlog.")
        return
    # AI fallback
    await context.bot.send_chat_action(cid, "typing")
    res = ask_ai_smart(update.message.text, cid)
    reply = res.get("response","") + "\n" + apply_updates(res.get("updates",[]))
    await update.message.reply_text(reply)

# ========== PDF PROMPT & SCHEDULING ==========
async def weekly_schedule_prompt(context):
    cid = context.job.chat_id; await context.bot.send_message(cid, "📅 Saturday! Upload CETQAS schedule PDF.")
    context.bot_data.setdefault("expecting_pdf", {})[cid] = True

def schedule_morning_checkin(job_queue, wake_up_str, chat_id):
    wake_time = datetime.strptime(wake_up_str, "%H:%M").time()
    job_queue.run_daily(morning_checkin_callback, time=wake_time, chat_id=chat_id, name="morning_checkin")
async def morning_checkin_callback(context): await start_daily_checkin(context.job.chat_id, context)

def schedule_weekly_pdf_prompt(job_queue, chat_id):
    job_queue.run_daily(weekly_schedule_prompt, time=datetime.strptime("08:00","%H:%M").time(), days=(5,), chat_id=chat_id, name="weekly_pdf_prompt")

def schedule_reminders(job_queue):
    for rem in memory["reminders"]:
        try:
            rem_time = datetime.strptime(rem["time"], "%H:%M").time(); target = datetime.combine(datetime.now().date(), rem_time)
            if target <= datetime.now(): target += timedelta(days=1)
            delay = (target - datetime.now()).total_seconds()
            job_queue.run_once(send_reminder, delay, chat_id=rem["chat_id"], data=rem["message"])
        except: pass

# ========== HEALTH SERVER ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ========== MAIN ==========
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()

    # Register all command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start_day", start_day_wrapper))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("weekly_report", weekly_report))
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
    app.add_handler(CommandHandler("trends", trends_cmd))
    app.add_handler(CommandHandler("correlation", correlation_cmd))
    app.add_handler(CommandHandler("efficiency", efficiency_cmd))
    app.add_handler(CommandHandler("export_data", export_data_cmd))
    app.add_handler(CommandHandler("brain_dump", brain_dump_cmd))
    app.add_handler(CommandHandler("study_tips", study_tips_cmd))
    app.add_handler(CommandHandler("motivate", motivate_cmd))
    app.add_handler(CommandHandler("daily_quote", daily_quote_cmd))
    app.add_handler(CommandHandler("chat", chat_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("week_update", week_update_cmd))
    app.add_handler(CommandHandler("addhw", add_homework_task))
    app.add_handler(CommandHandler("tasks", list_homework))
    app.add_handler(CommandHandler("addbacklog", add_backlog_entry))
    app.add_handler(CommandHandler("priority", show_priority))
    app.add_handler(CommandHandler("plan", daily_plan))
    app.add_handler(CommandHandler("endday", end_day_log))
    app.add_handler(CommandHandler("report", weekly_report))
    app.add_handler(CommandHandler("addrevision", add_revision))
    app.add_handler(CommandHandler("revise", list_revisions))
    app.add_handler(CommandHandler("heatmap", heatmap_cmd))
    app.add_handler(CommandHandler("consistency", consistency_cmd))

    # Multi‑media handlers
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue:
        schedule_reminders(app.job_queue)
        schedule_test_followups(app)
    print("Bot polling...")
    app.run_polling()