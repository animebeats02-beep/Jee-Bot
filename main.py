import os
import json
import threading
import time
import random
import asyncio
import re
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

# ---------- Safe JSON load with repair ----------
def safe_load_json(name, default):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        save_json(name, default)
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Corrupted {name}.json: {e}. Restoring default.")
        save_json(name, default)
        return default

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
}

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

# ---------- Helper functions ----------
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
        print(f"Error in generate_todo_list: {e}")
        import traceback
        traceback.print_exc()
        raise

# ---------- AI smart engine with conversation memory ----------
conversation_memory: Dict[int, List[Dict]] = {}

def get_conversation_context(chat_id: int) -> List[Dict]:
    if chat_id not in conversation_memory:
        return []
    return conversation_memory[chat_id][-5:]

def add_to_conversation(chat_id: int, role: str, content: str):
    if chat_id not in conversation_memory:
        conversation_memory[chat_id] = []
    conversation_memory[chat_id].append({"role": role, "content": content})
    if len(conversation_memory[chat_id]) > 10:
        conversation_memory[chat_id] = conversation_memory[chat_id][-10:]

def ask_ai_smart(user_message: str, chat_id: int) -> Dict:
    if not GROQ_KEY:
        return {"response": "AI not available. Please set GROQ_API_KEY.", "updates": []}
    weak_topics = get_weak_chapters()[:5]
    backlog_count = len([t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"])
    upcoming_tests = memory["tests"].get("upcoming", [])
    next_test_date = memory["tests"].get("next_test_date", "None")
    syllabus_status = {k: v.get("status") for k, v in list(memory["syllabus"]["chapters"].items())[:20]}
    context_str = f"""
CURRENT MEMORY:
- Wake up: {memory['schedule']['wake_up']}
- Sleep: {memory['schedule']['sleep']}
- Target study hours/day: {memory['schedule']['study_hours']}
- Today's plan: {'Generated' if memory['today'].get('generated', False) else 'Not yet'}
- Backlog tasks pending: {backlog_count}
- Weak chapters: {weak_topics}
- Upcoming tests: {[t.get('name') for t in upcoming_tests]}
- Next monthly test date: {next_test_date}
- Sample syllabus status: {syllabus_status}
- Today's mood: {memory['stats'].get('mood_log', {}).get(date.today().isoformat(), {}).get('mood', 'Not recorded')}
"""
    conv = get_conversation_context(chat_id)
    conv_text = "\n".join([f"{m['role']}: {m['content']}" for m in conv]) if conv else "(No recent conversation)"
    system_prompt = f"""You are JEE Study OS, a highly intelligent and proactive JEE coach. You have access to the student's memory (above). Your job is to:
- Answer questions about the student's data (backlog, syllabus, schedule, stats, tests) using the memory.
- Answer general JEE academic questions (Physics, Chemistry, Maths) using your own knowledge.
- Update the student's data when they tell you to (e.g., "add backlog Physics Gravitation 45 min", "set wake-up to 7am", "my mood is 8").
- Give proactive advice (e.g., "You have a test in 3 days, revise these weak topics", "You slept only 5 hours, try to rest more").
- Be conversational, friendly, but strict like a coach.

You MUST return a valid JSON object with two fields:
- "response": the text you want to send back to the student.
- "updates": a list of update objects. Each update object has:
    * "action": one of ["add_backlog", "update_schedule", "update_syllabus_status", "record_mood", "add_homework", "set_test", "complete_task", "none"]
    * "data": a dictionary with the required fields for that action.

Example updates:
- Add backlog: {{"action": "add_backlog", "data": {{"subject": "Physics", "chapter": "Gravitation", "estimated_time": 45}}}}
- Update wake-up time: {{"action": "update_schedule", "data": {{"wake_up": "06:30"}}}}
- Update chapter status: {{"action": "update_syllabus_status", "data": {{"chapter_key": "Physics_Electrostatics", "status": "completed"}}}}
- Record mood: {{"action": "record_mood", "data": {{"mood": 8}}}}
- Add homework: {{"action": "add_homework", "data": {{"subject": "Chemistry", "chapter": "Chemical Kinetics", "exercises": {{"O1": 30, "O2": 20}}}}}}
- Set test: {{"action": "set_test", "data": {{"date": "2026-07-15", "chapters_11th": ["Physics_Units", "Chemistry_Some_Basic"]}}}}
- Complete a task: {{"action": "complete_task", "data": {{"task_id_or_chapter": "Physics_Electrostatics"}}}}

If no update is needed, send an empty list.

Now, respond ONLY with valid JSON. Do not add any other text.

Student message: {user_message}

Recent conversation:
{conv_text}

Memory context:
{context_str}
"""
    try:
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "system", "content": system_prompt}],
            "temperature": 0.5,
            "max_tokens": 800,
            "response_format": {"type": "json_object"}
        }
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            result = resp.json()["choices"][0]["message"]["content"]
            try:
                data = json.loads(result)
                return data
            except json.JSONDecodeError:
                print(f"Invalid JSON from AI: {result}")
                return {"response": "Sorry, I had trouble understanding. Please rephrase.", "updates": []}
        else:
            print(f"AI error: {resp.status_code}")
            return {"response": f"AI service error (status {resp.status_code}). Try again later.", "updates": []}
    except Exception as e:
        print(f"AI call exception: {e}")
        return {"response": "Network error. Please try again.", "updates": []}

def apply_updates(updates: List[Dict]) -> str:
    results = []
    for upd in updates:
        action = upd.get("action")
        data = upd.get("data", {})
        if action == "add_backlog":
            subject = data.get("subject")
            chapter = data.get("chapter")
            est_time = data.get("estimated_time", 45)
            if subject and chapter:
                task = {
                    "id": str(int(datetime.timestamp(datetime.now()))),
                    "subject": subject,
                    "chapter": chapter,
                    "type": "backlog",
                    "estimated_time": est_time,
                    "source": "AI",
                    "status": "pending",
                    "chapter_key": f"{subject}_{chapter.replace(' ','_')}"
                }
                memory["backlog"]["tasks"].append(task)
                save_json("backlog", memory["backlog"])
                results.append(f"Added backlog: {subject} - {chapter} ({est_time} min)")
            else:
                results.append("Could not add backlog: missing subject or chapter")
        elif action == "update_schedule":
            changed = []
            if "wake_up" in data:
                memory["schedule"]["wake_up"] = data["wake_up"]
                changed.append(f"wake-up to {data['wake_up']}")
            if "sleep" in data:
                memory["schedule"]["sleep"] = data["sleep"]
                changed.append(f"sleep to {data['sleep']}")
            if "study_hours" in data:
                memory["schedule"]["study_hours"] = data["study_hours"]
                changed.append(f"study hours to {data['study_hours']}")
            if changed:
                save_json("schedule", memory["schedule"])
                results.append(f"Schedule updated: {', '.join(changed)}")
            else:
                results.append("No schedule changes")
        elif action == "update_syllabus_status":
            key = data.get("chapter_key")
            status = data.get("status")
            if key and status in ("not_started","going_on","completed","weak","revision_needed"):
                if key in memory["syllabus"]["chapters"]:
                    memory["syllabus"]["chapters"][key]["status"] = status
                    save_json("syllabus", memory["syllabus"])
                    results.append(f"Chapter {key} marked as {status}")
                else:
                    results.append(f"Chapter key {key} not found")
            else:
                results.append("Invalid chapter update")
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
                results.append(f"Mood recorded: {mood}/10")
            else:
                results.append("Invalid mood value (must be 1-10)")
        elif action == "add_homework":
            subject = data.get("subject")
            chapter = data.get("chapter")
            exercises = data.get("exercises", {})
            if subject and chapter and exercises:
                chapter_key = f"{subject}_{chapter.replace(' ','_')}"
                if chapter_key not in memory["syllabus"]["chapters"]:
                    sub = subject if subject != "Chemistry" else "Physical"
                    memory["syllabus"]["chapters"][chapter_key] = {
                        "subject": subject, "chapter": chapter,
                        "class": 12, "status": "not_started", "sub_subject": sub
                    }
                    save_json("syllabus", memory["syllabus"])
                total_time = estimate_homework_time(exercises)
                task = {
                    "id": str(int(datetime.timestamp(datetime.now()))),
                    "subject": subject,
                    "chapter": chapter,
                    "type": "mixed",
                    "estimated_time": total_time,
                    "source": "AI",
                    "status": "pending",
                    "chapter_key": chapter_key,
                    "exercise_counts": exercises
                }
                memory["backlog"]["tasks"].append(task)
                save_json("backlog", memory["backlog"])
                results.append(f"Added homework for {subject} - {chapter} ({total_time} min)")
            else:
                results.append("Could not add homework: missing fields")
        elif action == "set_test":
            date_str = data.get("date")
            chapters_11th = data.get("chapters_11th", [])
            if date_str:
                try:
                    date.fromisoformat(date_str)
                    memory["tests"]["next_test_date"] = date_str
                    memory["tests"]["next_test_11th_syllabus"] = chapters_11th
                    save_json("tests", memory["tests"])
                    results.append(f"Test set for {date_str} with {len(chapters_11th)} 11th chapters")
                except:
                    results.append("Invalid test date format")
            else:
                results.append("Missing test date")
        elif action == "complete_task":
            task_ref = data.get("task_id_or_chapter")
            if task_ref:
                today_plan = memory["today"].get("todo", [])
                found = False
                for task in today_plan:
                    if task_ref.lower() in task.get("id","").lower() or task_ref.lower() in task.get("chapter","").lower():
                        task["status"] = "done"
                        found = True
                        memory["progress"]["logs"].append({
                            "task_id": task["id"],
                            "description": f"{task['subject']} - {task['chapter']}",
                            "timestamp": datetime.now().isoformat()
                        })
                        save_json("progress", memory["progress"])
                        results.append(f"Task '{task_ref}' marked done")
                        break
                if not found:
                    results.append(f"Task '{task_ref}' not found in today's plan")
            else:
                results.append("Missing task reference")
        else:
            results.append(f"Unknown action: {action}")
    return "\n".join(results) if results else ""

# ---------- Daily check-in state and handlers ----------
daily_states: Dict[int, Dict[str, Any]] = {}

async def start_daily_checkin(chat_id, context):
    daily_states[chat_id] = {
        "state": "waiting_sleep",
        "sleep": None,
        "wake_time": None,
        "mood": None,
        "study_hours": None,
        "homework": [],
        "skip_keywords": [],
        "current_chapter": None,
        "pending_exercise_types": [],
        "current_exercise_counts": {},
    }
    await context.bot.send_message(chat_id, "🌅 Good morning! How many hours did you sleep last night? (e.g., 6.5)")

async def handle_daily_checkin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in daily_states:
        return False
    state = daily_states[chat_id]
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
                await update.message.reply_text("Please enter a number between 1 and 10.")
        except ValueError:
            await update.message.reply_text("Please enter a number (1‑10).")
        return True
    elif state["state"] == "waiting_study_hours":
        try:
            hours = float(text)
            state["study_hours"] = hours
            state["state"] = "waiting_homework"
            await update.message.reply_text(
                "📝 Enter today's homework. You can use natural language like:\n"
                "`Physics Electrostatics O1 30, O2 25, JM 20`\n"
                "or just type `done` if you have no homework."
            )
        except ValueError:
            await update.message.reply_text("Please enter a number (e.g., 8).")
        return True
    elif state["state"] == "waiting_homework":
        if text.lower() == "done":
            state["state"] = "waiting_skip"
            if state["homework"]:
                task_list = "\n".join(f"{t['subject']} - {t['chapter']} ({t['type']})" for t in state["homework"])
                await update.message.reply_text(f"Your homework:\n{task_list}")
            else:
                await update.message.reply_text("No homework recorded.")
            await update.message.reply_text("🙅 Any homework tasks to skip? Send keywords/comma‑separated or type `none`.")
            return True
        # Simple parsing for "Subject Chapter O1 count" format (can be extended)
        match = re.match(r"(\w+)\s+(\w+)\s+O1\s+(\d+)", text, re.IGNORECASE)
        if match:
            subject = match.group(1)
            chapter = match.group(2)
            o1_count = int(match.group(3))
            exercises = {"O1": o1_count}
            chapter_key = f"{subject}_{chapter.replace(' ','_')}"
            if chapter_key not in memory["syllabus"]["chapters"]:
                sub = subject if subject != "Chemistry" else "Physical"
                memory["syllabus"]["chapters"][chapter_key] = {
                    "subject": subject, "chapter": chapter,
                    "class": 12, "status": "not_started", "sub_subject": sub
                }
                save_json("syllabus", memory["syllabus"])
            total_time = estimate_homework_time(exercises)
            task = {
                "id": str(int(datetime.timestamp(datetime.now()))),
                "subject": subject,
                "chapter": chapter,
                "type": "mixed",
                "estimated_time": total_time,
                "source": "coaching",
                "status": "pending",
                "chapter_key": chapter_key,
                "exercise_counts": exercises
            }
            state["homework"].append(task)
            await update.message.reply_text(f"✅ Added homework for {subject} - {chapter} (est. {total_time} min). Send more or `done`.")
        else:
            await update.message.reply_text("Could not understand. Use format: `Subject Chapter O1 count` or type `done`.")
        return True
    elif state["state"] == "waiting_skip":
        if text.lower() == "none":
            state["skip_keywords"] = []
        else:
            state["skip_keywords"] = [kw.strip() for kw in text.split(",") if kw.strip()]
        await finalize_daily_checkin(update, context)
        return True
    return False

async def finalize_daily_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = daily_states.pop(chat_id, None)
    if not state:
        await update.message.reply_text("❌ Session expired. Please use /start_day again.")
        return
    try:
        await context.bot.send_chat_action(chat_id, action="typing")
        sleep_msg = ""
        if state["sleep"] is not None:
            diff = state["sleep"] - RECOMMENDED_SLEEP
            if diff >= 1:
                sleep_msg = f"You slept {state['sleep']}h — well rested! (+{diff:.1f}h vs recommended)."
            elif diff <= -1:
                sleep_msg = f"You slept {state['sleep']}h — less than the recommended {RECOMMENDED_SLEEP}h."
            else:
                sleep_msg = f"You slept {state['sleep']}h — adequate."
        memory["homework"] = {"date": datetime.now().strftime("%Y-%m-%d"), "tasks": state["homework"]}
        save_json("homework", memory["homework"])
        study_hours = state["study_hours"] if state["study_hours"] else memory["schedule"]["study_hours"]
        todo, total_est, avail_mins, overflow = generate_todo_list(
            study_hours_override=study_hours,
            skip_keywords=state["skip_keywords"]
        )
        daily_hrs = study_hours
        days = estimate_backlog_days(daily_hrs)
        update_streak_and_hours(daily_hrs, mood=state["mood"], sleep_hours=state["sleep"])
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
        msg = f"{sleep_msg}\n\n📅 *Today's To‑Do List* (Classes: 12 PM – 8 PM)\n"
        msg += f"🕒 Free hours: ~{total_free}h (morning {morning_hours}h + evening {evening_hours}h)\n"
        msg += f"⏱️ Total task time: {total_est} min ({total_est/60:.1f}h)\n"
        if total_est > avail_mins:
            msg += "⚠️ Task time exceeds available study time.\n"
        if overflow:
            msg += "📦 The following tasks were moved to backlog to fit your day:\n"
            for t in overflow:
                msg += f"• {t['subject']} - {t['chapter']} ({t['type']})\n"
        msg += "\n"
        for task in todo:
            msg += f"{emoji(task.get('priority_score',50))} {task['subject']} - {task['chapter']} ({task['type']}) – {task['estimated_time']} min\n"
        msg += f"\n⏳ *Backlog estimate:* ~{days} day(s) at {daily_hrs}h/day."
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        print(f"Error finalizing check-in: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error generating plan: {str(e)[:100]}. Please try /start_day again.")

# ---------- Test management ----------
def schedule_test_followups(app):
    if not app.job_queue: return
    next_date_str = memory["tests"].get("next_test_date")
    if not next_date_str: return
    try:
        test_date = date.fromisoformat(next_date_str)
    except: return
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
    chat_id = context.job.chat_id or context.bot_data.get("user_chat_id")
    if chat_id:
        await context.bot.send_message(chat_id, "📝 How did your monthly test go? Any feedback?")

async def ask_next_test_info(context):
    chat_id = context.job.chat_id or context.bot_data.get("user_chat_id")
    if chat_id:
        await context.bot.send_message(chat_id,
            "📅 Please set your next monthly test.\nSend: `Test Date (YYYY-MM-DD) | 11th Chapter Keys (comma separated)`")

# ---------- Periodic notifications ----------
async def send_periodic_notification(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if chat_id is None:
        return
    now = datetime.now()
    current_hour = now.hour
    wake_hour = int(memory["schedule"]["wake_up"].split(":")[0])
    sleep_hour = int(memory["schedule"]["sleep"].split(":")[0])
    if not (wake_hour <= current_hour < sleep_hour):
        return
    msg_type = random.choice(["quote", "backlog", "progress", "checkin"])
    if msg_type == "quote":
        msg = random.choice(MOTIVATIONAL_QUOTES)
    elif msg_type == "backlog":
        pending = [t for t in memory["backlog"].get("tasks",[]) if t.get("status") != "done"]
        if pending:
            total_min = sum(t.get("estimated_time",45) for t in pending)
            hours = total_min / 60
            msg = f"📦 Backlog: {len(pending)} tasks, ~{hours:.1f} hours left. Keep chipping away!"
        else:
            msg = "🎉 No backlog! Great job. Want to review weak chapters?"
    elif msg_type == "progress":
        today_plan = memory["today"].get("todo", [])
        if today_plan:
            done_today = sum(1 for l in memory["progress"].get("logs",[]) if l.get("timestamp","").startswith(date.today().isoformat()))
            total_today = len(today_plan)
            msg = f"📊 Today's progress: {done_today}/{total_today} tasks done. Keep going!"
        else:
            msg = "⏰ Don't forget to start your day with /start_day or wait for morning check‑in."
    else:
        msg = "😊 How is your study session? Reply with /mood <1-10> or /progress <hours studied>."
    try:
        await context.bot.send_message(chat_id, msg)
    except Exception as e:
        print(f"Notification error: {e}")

def schedule_notifications(job_queue, chat_id, interval_minutes=120, enabled=True):
    for job in job_queue.jobs():
        if job.name == "periodic_notify" and job.chat_id == chat_id:
            job.schedule_removal()
    if enabled and interval_minutes > 0:
        job_queue.run_repeating(
            send_periodic_notification,
            interval=interval_minutes * 60,
            first=60,
            chat_id=chat_id,
            name="periodic_notify"
        )
        return True
    return False

# ---------- Command handlers ----------
async def start(update, context):
    chat_id = update.effective_chat.id
    context.bot_data["user_chat_id"] = chat_id
    if "notifications" not in memory["schedule"]:
        memory["schedule"]["notifications"] = {"enabled": True, "interval_minutes": 120}
        save_json("schedule", memory["schedule"])
    notify_settings = memory["schedule"]["notifications"]
    schedule_notifications(context.application.job_queue, chat_id,
                          notify_settings.get("interval_minutes",120),
                          notify_settings.get("enabled",True))
    wake_up = memory["schedule"].get("wake_up", "07:00")
    schedule_morning_checkin(context.application.job_queue, wake_up, chat_id)
    schedule_weekly_pdf_prompt(context.application.job_queue, chat_id)
    await update.message.reply_text(
        "🚀 **JEE Study OS – Super Smart AI Coach**\n\n"
        "I now understand natural conversation, remember our chat, and can update your data automatically.\n\n"
        "Try saying:\n"
        "• *'What's my backlog?'*\n"
        "• *'Add backlog Physics Gravitation 45 minutes'*\n"
        "• *'Mark Electrostatics as completed'*\n"
        "• *'My mood is 8'*\n"
        "• *'How to solve quadratic equations?'*\n\n"
        "I will also ask you every morning about your sleep, mood, and homework.\n"
        "Type /help for all commands.",
        parse_mode='Markdown'
    )

async def help_cmd(update, context):
    text = """
📚 **JEE Study OS – Super Smart AI Coach**

**💬 Natural Conversation** – Just talk to me like a human. I remember our chat and can:
- Answer questions about your data (backlog, syllabus, schedule, stats)
- Update your data (add backlog, change wake‑up time, mark chapters completed, record mood)
- Answer academic JEE questions (Physics, Chemistry, Maths)
- Give proactive advice based on your mood, backlog, and upcoming tests

**🌅 Daily Routine** – Every morning at your wake‑up time, I'll ask you:
- Sleep hours, wake time, mood, study hours, homework, tasks to skip

**🔧 Commands** (optional):
/start_day – Manually start morning check‑in
/view_plan – Today's to‑do list
/stats – Study streak and hours
/set_test – Schedule a monthly test
/view_syllabus – Full syllabus progress
/notify – Configure periodic reminders
/mood <1-10> – Quick mood log
/progress <hours> – Log study hours

**💡 Proactive advice**: I'll notify you if you're falling behind, have a test soon, or need rest.
"""
    await update.message.reply_text(text, parse_mode='Markdown')

async def chat_cmd(update, context):
    context.user_data['mode'] = 'chat'
    context.user_data['chat_history'] = []
    await update.message.reply_text("💬 Chat mode active. You can also just talk normally without /chat. /stop to end.")

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
    done_today = sum(1 for l in memory["progress"].get("logs",[]) if l.get("timestamp","").startswith(today_str))
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
    week_logs = [l for l in memory["progress"].get("logs",[]) if (today - date.fromisoformat(l["timestamp"][:10])).days < 7]
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

async def set_test_cmd(update, context):
    await update.message.reply_text("Send: `Test Date (YYYY-MM-DD) | 11th Chapter Keys (comma separated)`")
    context.user_data['mode'] = 'set_next_test'

async def set_schedule_cmd(update, context):
    await update.message.reply_text("Send: `wake_up|sleep|study_hours` (e.g., `07:00|22:00|8`)")
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
        f"{'🟢' if v.get('status') in ('completed','going_on') else '🔴' if v.get('status')=='weak' else '⚪'} {v.get('subject','')} ({v.get('sub_subject','')}) - {v.get('chapter','')} [{v.get('status','')}]"
        for k,v in list(chaps.items())[:30])
    if ongoing:
        msg += "\n📌 Current: " + ", ".join(ongoing[:5])
    if weak:
        msg += "\n⚠️ Weak: " + ", ".join(weak[:5])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status")!="done"]
    if not tasks:
        await update.message.reply_text("No pending backlog.")
        return
    msg = "📋 *Backlog*\n" + "\n".join(f"• {t.get('subject','')} {t.get('chapter','')} ({t.get('type','')}) est.{t.get('estimated_time',0)}min" for t in tasks)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def complete_task_cmd(update, context):
    await update.message.reply_text("Send task ID (from /view_plan) or description keyword.")
    context.user_data['mode'] = 'complete'

async def ask_cmd(update, context):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await context.bot.send_chat_action(update.effective_chat.id, action="typing")
    result = ask_ai_smart(question, update.effective_chat.id)
    await update.message.reply_text(result.get("response", "No response"))

async def week_update_cmd(update, context):
    await update.message.reply_text("Send your weekly class timetable (any format) or type `skip`.")
    context.user_data['mode'] = 'weekly'

async def view_plan(update, context):
    today_plan = memory["today"]
    if not today_plan.get("generated"):
        await update.message.reply_text("No to‑do list yet. Use /start_day or wait for morning check‑in.")
        return
    todo = today_plan.get("todo", [])
    if not todo:
        await update.message.reply_text("No tasks for today. Great! Relax or revise weak topics.")
        return
    msg = "📅 *Today's To‑Do*\n" + "\n".join(
        f"• {t.get('subject','')} - {t.get('chapter','')} ({t.get('type','')}) – {t.get('estimated_time',0)} min" for t in todo
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def start_day_cmd(update, context):
    chat_id = update.effective_chat.id
    daily_states.pop(chat_id, None)
    await start_daily_checkin(chat_id, context)

async def mood_cmd(update, context):
    if context.args:
        try:
            mood = int(context.args[0])
            if 1 <= mood <= 10:
                today_str = date.today().isoformat()
                mood_log = memory["stats"].get("mood_log", {})
                if today_str not in mood_log:
                    mood_log[today_str] = {}
                mood_log[today_str]["mood"] = mood
                memory["stats"]["mood_log"] = mood_log
                save_json("stats", memory["stats"])
                await update.message.reply_text(f"Mood recorded: {mood}/10. Stay strong!")
            else:
                await update.message.reply_text("Mood must be 1-10.")
        except ValueError:
            await update.message.reply_text("Send a number, e.g., /mood 8")
    else:
        await update.message.reply_text("Usage: /mood <1-10>")

async def progress_cmd(update, context):
    if context.args:
        try:
            hours = float(context.args[0])
            update_streak_and_hours(hours)
            await update.message.reply_text(f"✅ Logged {hours} study hour(s). Keep going!")
        except ValueError:
            await update.message.reply_text("Send a number, e.g., /progress 2.5")
    else:
        await update.message.reply_text("Usage: /progress <hours>")

async def notify_cmd(update, context):
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

async def handle_set_test(update, context):
    text = update.message.text
    parts = text.split('|')
    if len(parts) >= 1:
        date_str = parts[0].strip()
        chaps_11th = []
        if len(parts) > 1:
            chaps_11th = [c.strip() for c in parts[1].split(',') if c.strip()]
        try:
            date.fromisoformat(date_str)
        except:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD.")
            return
        memory["tests"]["next_test_date"] = date_str
        memory["tests"]["next_test_11th_syllabus"] = chaps_11th
        memory["tests"]["test_asked_today"] = False
        save_json("tests", memory["tests"])
        schedule_test_followups(context.application)
        await update.message.reply_text("✅ Test set. Daily 11th revision tasks will now appear.")
        context.user_data['mode'] = None
    else:
        await update.message.reply_text("Invalid format.")

# ---------- Main message router ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.text.startswith('/'):
        return
    if context.user_data.get('mode') == 'chat':
        await context.bot.send_chat_action(chat_id, action="typing")
        history = context.user_data.get('chat_history', [])
        user_msg = update.message.text
        history.append({"role":"user","content": user_msg})
        result = ask_ai_smart(user_msg, chat_id)
        reply = result.get("response", "I didn't understand.")
        history.append({"role":"assistant","content": reply})
        context.user_data['chat_history'] = history
        await update.message.reply_text(reply)
        return
    if context.user_data.get('mode') == 'set_next_test':
        await handle_set_test(update, context)
        return
    if chat_id in daily_states:
        await handle_daily_checkin_message(update, context)
        return
    if context.bot_data.get("expecting_pdf",{}).get(chat_id):
        await handle_document(update, context)
        return
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
            wake = parts[0].strip()
            sleep = parts[1].strip()
            hours = int(parts[2].strip())
            memory["schedule"]["wake_up"] = wake
            memory["schedule"]["sleep"] = sleep
            memory["schedule"]["study_hours"] = hours
            save_json("schedule", memory["schedule"])
            for job in context.application.job_queue.jobs():
                if job.name == "morning_checkin" and job.chat_id == chat_id:
                    job.schedule_removal()
            schedule_morning_checkin(context.application.job_queue, wake, chat_id)
            await update.message.reply_text("Schedule updated. Morning check‑in will now happen at the new wake‑up time.")
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
        today_plan = memory["today"].get("todo", [])
        found = False
        for task in today_plan:
            if text.lower() in task.get("id","").lower() or text.lower() in task.get("chapter","").lower():
                task["status"] = "done"
                found = True
                memory["progress"]["logs"].append({
                    "task_id": task["id"], "description": f"{task['subject']} - {task['chapter']}",
                    "timestamp": datetime.now().isoformat()
                })
                save_json("progress", memory["progress"])
                break
        if found:
            memory["today"]["todo"] = today_plan
            save_json("today", memory["today"])
            done_today = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(datetime.now().strftime("%Y-%m-%d")))
            await update.message.reply_text(f"✅ Task marked done. You've completed {done_today} task(s) today.")
        else:
            await update.message.reply_text("Task not found. Use /view_plan to see task IDs or chapters.")
        context.user_data['mode'] = None
        return

    # Natural language fallback
    handled = await handle_natural_language(update, context)
    if not handled:
        await update.message.reply_text("🤖 Use /help to see commands, or just ask me naturally (e.g., 'What's my backlog?', 'Set wake-up to 7am').")

# ---------- Smart natural language handler ----------
async def handle_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    user_message = update.message.text
    lower_msg = user_message.lower()
    if re.search(r"(what|show|tell).*backlog", lower_msg):
        tasks = [t for t in memory["backlog"].get("tasks",[]) if t.get("status") != "done"]
        if not tasks:
            await update.message.reply_text("No pending backlog tasks.")
        else:
            msg = "📋 Backlog:\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']} min" for t in tasks[:10])
            await update.message.reply_text(msg)
        return True
    if re.search(r"(what|show|tell).*(today'?s plan|todo|to-do)", lower_msg):
        todo = memory["today"].get("todo", [])
        if not todo:
            await update.message.reply_text("Today's plan not generated yet. Use /start_day or wait for morning check-in.")
        else:
            msg = "📅 Today's To‑Do:\n" + "\n".join(f"• {t['subject']} - {t['chapter']} ({t['type']}) – {t['estimated_time']} min" for t in todo[:10])
            await update.message.reply_text(msg)
        return True
    if re.search(r"(streak|how many days)", lower_msg):
        s = memory["stats"]
        await update.message.reply_text(f"🔥 Current streak: {s.get('streak',0)} days. Best: {s.get('longest_streak',0)} days.")
        return True
    if re.search(r"(weak|difficult).* chapters?", lower_msg):
        weak = get_weak_chapters()
        if not weak:
            await update.message.reply_text("No weak chapters reported.")
        else:
            await update.message.reply_text(f"⚠️ Weak chapters: {', '.join(weak[:5])}.")
        return True
    await context.bot.send_chat_action(chat_id, action="typing")
    result = ask_ai_smart(user_message, chat_id)
    response = result.get("response", "I didn't understand that.")
    updates = result.get("updates", [])
    if updates:
        update_msg = apply_updates(updates)
        if update_msg:
            response += f"\n\n{update_msg}"
    add_to_conversation(chat_id, "user", user_message)
    add_to_conversation(chat_id, "assistant", response)
    await update.message.reply_text(response)
    return True

# ---------- PDF handling and schedule ----------
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
    job_queue.run_daily(morning_checkin_callback, time=wake_time, chat_id=chat_id, name="morning_checkin")

async def morning_checkin_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if chat_id:
        await start_daily_checkin(chat_id, context)

def schedule_weekly_pdf_prompt(job_queue, chat_id):
    job_queue.run_daily(weekly_schedule_prompt, time=datetime.strptime("08:00","%H:%M").time(),
                        days=(5,), chat_id=chat_id, name="weekly_pdf_prompt")

# ---------- Health server for Render ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT",8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ---------- Main ----------
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
    app.add_handler(CommandHandler("start_day", start_day_cmd))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(CommandHandler("mood", mood_cmd))
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(CommandHandler("notify", notify_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    if app.job_queue:
        schedule_test_followups(app)
    print("Bot polling...")
    app.run_polling()