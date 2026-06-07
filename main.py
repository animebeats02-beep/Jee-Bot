import os
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests

# PDF handling
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

# ---------- memory (JSON) ----------
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
    subjects = {
        "Physics": ["Units & Measurements","Motion in Straight Line","Motion in Plane","Laws of Motion","Work Energy Power","Rotational Motion","Gravitation","Mechanical Properties Solids","Mechanical Properties Fluids","Thermal Properties","Thermodynamics","Kinetic Theory","Oscillations","Waves","Electric Charges Fields","Electrostatic Potential","Current Electricity","Moving Charges Magnetism","Magnetism Matter","Electromagnetic Induction","Alternating Current","Electromagnetic Waves","Ray Optics","Wave Optics","Dual Nature Radiation","Atoms","Nuclei","Semiconductor Electronics","Communication Systems"],
        "Chemistry": ["Some Basic Concepts","Structure Atom","Classification Periodicity","Chemical Bonding","States Matter","Thermodynamics","Equilibrium","Redox Reactions","Hydrogen","s-Block","p-Block 11","Organic Basic Principles","Hydrocarbons","Environmental","Solid State","Solutions","Electrochemistry","Chemical Kinetics","Surface Chemistry","Metallurgy","p-Block 12","d & f Block","Coordination Compounds","Haloalkanes","Alcohols Phenols Ethers","Aldehydes Ketones","Amines","Biomolecules","Polymers","Chemistry Everyday"],
        "Maths": ["Sets","Relations Functions","Trigonometric Functions","Mathematical Induction","Complex Numbers","Linear Inequalities","Permutations Combinations","Binomial Theorem","Sequences Series","Straight Lines","Conic Sections","3D Geometry","Limits Derivatives","Mathematical Reasoning","Statistics","Probability 11","Relations Functions 12","Inverse Trig","Matrices","Determinants","Continuity Differentiability","Application Derivatives","Integrals","Application Integrals","Differential Equations","Vector Algebra","3D Geometry 12","Linear Programming","Probability 12"]
    }
    chapters = {}
    for cls in [11,12]:
        for sub, chaps in subjects.items():
            for ch in chaps:
                key = f"{sub}_{ch.replace(' ','_')}"
                chapters[key] = {"subject": sub, "chapter": ch, "class": cls, "status": "not_started", "priority": 5}
    return chapters

memory = {
    "backlog": load_json("backlog", {"tasks": []}),
    "today": load_json("today", {"date": "", "plan": [], "generated": False}),
    "schedule": load_json("schedule", {"wake_up": "07:00", "sleep": "22:00", "study_hours": 8, "weekly_timetable": "", "last_updated": ""}),
    "progress": load_json("progress", {"logs": []}),
    "stats": load_json("stats", {"productivity": [], "consistency": 0, "fatigue_flags": 0}),
    "syllabus": load_json("syllabus", {"chapters": init_syllabus()}),
    "tests": load_json("tests", {"upcoming": [], "past": []}),
    "homework": load_json("homework", {"date": "", "tasks": []}),
    "chapter_exercises": load_json("chapter_exercises", {"exercises": {}}),  # key -> {O1: count, ...}
}

# ---------- default per-question times (minutes) ----------
EXERCISE_TIMES = {
    "O1": 5,
    "O2": 7,
    "O3": 7,
    "O4": 5,
    "JM": 5,
    "JA": 8,
    "Gyanoday": 10,   # per section/page
}

# fixed maths counts
MATHS_DEFAULTS = {"O1": 30, "O2": 20, "O3": 20, "O4": 10, "JM": 30, "JA": 20}

# ---------- helpers ----------
def get_ongoing_chapters() -> List[str]:
    return [k for k,v in memory["syllabus"]["chapters"].items() if v["status"] == "going_on"]

def get_weak_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v["status"] in ("weak","revision_needed")]

def get_chapter_exercise_counts(chapter_key: str) -> Dict[str, int]:
    """Return stored exercise counts for a chapter, or empty dict."""
    return memory["chapter_exercises"]["exercises"].get(chapter_key, {})

def save_chapter_exercise_counts(chapter_key: str, counts: Dict[str, int]):
    memory["chapter_exercises"]["exercises"][chapter_key] = counts
    save_json("chapter_exercises", memory["chapter_exercises"])

def compute_priority(task, test_chapters: List[str], ongoing_chapters: List[str]) -> int:
    score = 50
    if task.get("test_link") in [t["name"] for t in memory["tests"]["upcoming"]]:
        score += 30
    chapter_key = task.get("chapter_key", "")
    if chapter_key in test_chapters:
        score += 30
    if chapter_key in ongoing_chapters:
        score += 20
    if chapter_key in get_weak_chapters():
        score += 25
    if task.get("source") in ("test", "AI"):
        score += 15
    return score

def should_skip_task(task, skip_keywords):
    if not skip_keywords:
        return False
    text = (task.get("id", "") + " " + task.get("description", "")).lower()
    for kw in skip_keywords:
        if kw.strip().lower() in text:
            return True
    return False

def estimate_backlog_days(daily_hours):
    pending = [t for t in memory["backlog"]["tasks"] if t["status"] != "done"]
    if not pending:
        return 0
    total_min = sum(t.get("estimated_time", 45) for t in pending)
    if daily_hours <= 0:
        daily_hours = 1
    days = total_min / (daily_hours * 60)
    return round(days, 1)

def estimate_homework_time(task_description, exercise_counts: Dict[str, int]) -> int:
    """Calculate total minutes from exercise counts using default per‑question times."""
    total = 0
    for ex_type, count in exercise_counts.items():
        time_per = EXERCISE_TIMES.get(ex_type, 3)
        total += count * time_per
    return max(total, 10)   # minimum 10 min

def generate_plan(study_hours_override=None, skip_keywords=None,
                  test_chapters: List[str] = None, ongoing_chapters: List[str] = None):
    schedule = memory["schedule"]
    wake = schedule["wake_up"]
    study_mins = (study_hours_override if study_hours_override else schedule["study_hours"]) * 60
    hw = memory["homework"]["tasks"] if memory["homework"]["date"] == datetime.now().strftime("%Y-%m-%d") else []
    backlog = [t for t in memory["backlog"]["tasks"] if t["status"] != "done"]
    if skip_keywords:
        backlog = [t for t in backlog if not should_skip_task(t, skip_keywords)]
    if test_chapters is None:
        test_chapters = []
    if ongoing_chapters is None:
        ongoing_chapters = get_ongoing_chapters()
    all_tasks = []
    for t in hw:
        t["priority_score"] = compute_priority(t, test_chapters, ongoing_chapters)
        all_tasks.append(t)
    for t in backlog:
        t["priority_score"] = compute_priority(t, test_chapters, ongoing_chapters)
        all_tasks.append(t)
    all_tasks.sort(key=lambda x: x["priority_score"], reverse=True)
    wake_time = datetime.strptime(wake, "%H:%M")
    current = wake_time
    plan = []
    remaining = study_mins
    task_count = 0
    for task in all_tasks:
        if remaining <= 0:
            break
        effort = task.get("estimated_time", 45)
        block = min(effort, remaining)
        end = current + timedelta(minutes=block)
        plan.append({
            "task_id": task.get("id", ""),
            "description": f"{task.get('subject','')} - {task.get('chapter','')} ({task.get('type','')})",
            "start": current.strftime("%H:%M"),
            "end": end.strftime("%H:%M"),
            "subject": task.get("subject"),
            "type": task.get("type"),
            "status": "pending"
        })
        current = end
        remaining -= block
        task_count += 1
        if task_count % 3 == 0 and remaining > 0:
            break_end = current + timedelta(minutes=15)
            plan.append({
                "task_id": "break",
                "description": "Break ☕",
                "start": current.strftime("%H:%M"),
                "end": break_end.strftime("%H:%M"),
                "subject": "Break",
                "type": "break",
                "status": "pending"
            })
            current = break_end
            remaining -= 15
    memory["today"] = {"date": datetime.now().strftime("%Y-%m-%d"), "plan": plan, "generated": True}
    save_json("today", memory["today"])
    return plan

# ---------- AI (Groq, /ask only) ----------
def ask_ai(prompt):
    if not GROQ_KEY:
        return "AI not available."
    time.sleep(1)
    weak = get_weak_chapters()[:5]
    backlog_count = len([t for t in memory["backlog"]["tasks"] if t["status"] != "done"])
    tests = [t["name"] for t in memory["tests"]["upcoming"]]
    context = f"""
[SYSTEM MEMORY]
Syllabus progress: {sum(1 for v in memory['syllabus']['chapters'].values() if v['status'] in ('completed','going_on'))} chapters completed/ongoing.
Weak topics: {weak}.
Backlog tasks: {backlog_count}.
Upcoming tests: {tests}.
Today's plan: {'Generated' if memory['today']['generated'] else 'Not yet'}.
"""
    system_msg = "You are JEE Study OS, a strict Kota JEE coach. Answer the user question using the memory context."
    messages = [
        {"role": "system", "content": system_msg + "\n\n" + context},
        {"role": "user", "content": prompt}
    ]
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500
    }
    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            return f"AI error: {resp.status_code} {resp.text}"
    except Exception as e:
        return f"AI error: {str(e)}"

# ---------- morning check‑in state machine (extended) ----------
checkin_states: Dict[int, Dict[str, Any]] = {}

async def start_morning_checkin(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    checkin_states[chat_id] = {
        "state": "waiting_sleep",
        "sleep": None,
        "study_hours": None,
        "homework": [],          # list of task dicts
        "skip_keywords": [],
        "test_info": None,
        "test_11th_chapters": [],
        "current_hw_chapter": None,  # when entering exercises
        "pending_exercise_types": [],
        "current_exercise_counts": {}
    }
    await context.bot.send_message(chat_id, "🌅 Good morning! How many hours did you sleep last night? (reply with a number, e.g. 6.5)")

async def handle_checkin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in checkin_states:
        return False
    state = checkin_states[chat_id]
    text = update.message.text.strip()

    # ---- basic info ----
    if state["state"] == "waiting_sleep":
        try:
            hours = float(text)
            state["sleep"] = hours
            state["state"] = "waiting_study_hours"
            await update.message.reply_text("📘 How many hours can you study today? (e.g. 8)")
        except ValueError:
            await update.message.reply_text("Please enter a number (e.g. 7).")
        return True

    elif state["state"] == "waiting_study_hours":
        try:
            hours = float(text)
            state["study_hours"] = hours
            state["state"] = "waiting_test_info"
            await update.message.reply_text(
                "📅 Any test today? If yes, send:\n`Test Name|YYYY-MM-DD`\nIf no, type `none`."
            )
        except ValueError:
            await update.message.reply_text("Please enter a number (e.g. 8).")
        return True

    elif state["state"] == "waiting_test_info":
        if text.lower() == "none":
            state["test_info"] = None
            state["state"] = "waiting_homework_start"
            await update.message.reply_text(
                "📝 Let's add today's homework. For each chapter, send the chapter name like:\n`Physics|Rotation`\nOr type `done` when finished."
            )
        else:
            parts = text.split('|')
            if len(parts) >= 2:
                state["test_info"] = {"name": parts[0], "date": parts[1]}
                state["state"] = "waiting_test_11th"
                await update.message.reply_text(
                    "📚 Which **11th class chapters** are included in this test?\n"
                    "Send chapter keys separated by commas (e.g., `Physics_Kinematics,Chemistry_Structure_Atom`).\n"
                    "If none, type `none`."
                )
            else:
                await update.message.reply_text("❌ Format: `Test Name|YYYY-MM-DD` or `none`.")
        return True

    elif state["state"] == "waiting_test_11th":
        if text.lower() == "none":
            state["test_11th_chapters"] = []
        else:
            state["test_11th_chapters"] = [ch.strip() for ch in text.split(",") if ch.strip()]
        state["state"] = "waiting_homework_start"
        await update.message.reply_text(
            "📝 Now enter today's homework. For each chapter, send the chapter key like:\n`Physics_Rotation`\nOr type `done` when all chapters are entered."
        )
        return True

    # ---- homework entry ----
    elif state["state"] == "waiting_homework_start":
        if text.lower() == "done":
            state["state"] = "waiting_skip_homework"
            if state["homework"]:
                task_list = "\n".join(f"{t['subject']} - {t['chapter']} ({t['type']})" for t in state["homework"])
                await update.message.reply_text(f"Your homework:\n{task_list}")
            else:
                await update.message.reply_text("No homework recorded.")
            await update.message.reply_text(
                "🙅 Are there any homework tasks you want to SKIP today?\n"
                "Send one or more keywords / task IDs (comma separated) or type `none`."
            )
        else:
            # expecting chapter key like Physics_Rotation
            chapter_key = text.strip()
            if chapter_key in memory["syllabus"]["chapters"]:
                subject = memory["syllabus"]["chapters"][chapter_key]["subject"]
                state["current_hw_chapter"] = chapter_key
                # prepare exercise type question
                state["state"] = "waiting_exercise_types"
                await update.message.reply_text(
                    f"📋 For **{subject} - {memory['syllabus']['chapters'][chapter_key]['chapter']}**, which exercise types do you have?\n"
                    "Send them as a list: e.g., `O1,O2,JM`\n"
                    "Available: O1, O2, O3, O4, JM, JA, Gyanoday"
                )
            else:
                await update.message.reply_text("❌ Chapter key not found. Check /view_syllabus for keys. Try again or type `done`.")
        return True

    elif state["state"] == "waiting_exercise_types":
        if text.lower() == "done":
            # skip this chapter
            state["state"] = "waiting_homework_start"
            await update.message.reply_text("Chapter skipped. Send next chapter key or `done`.")
            return True
        types = [t.strip() for t in text.split(",") if t.strip()]
        valid_types = [t for t in types if t in EXERCISE_TIMES]
        if not valid_types:
            await update.message.reply_text("No valid types. Please use O1,O2,O3,O4,JM,JA,Gyanoday (comma separated).")
            return True
        state["pending_exercise_types"] = valid_types
        state["current_exercise_counts"] = {}
        # check if subject is Maths and use defaults if available
        chapter_key = state["current_hw_chapter"]
        subject = memory["syllabus"]["chapters"][chapter_key]["subject"]
        if subject == "Maths":
            # auto-fill from MATHS_DEFAULTS for the types that match
            for ex in valid_types:
                if ex in MATHS_DEFAULTS:
                    state["current_exercise_counts"][ex] = MATHS_DEFAULTS[ex]
                else:
                    state["current_exercise_counts"][ex] = 0
            # ask if we should adjust any
            await update.message.reply_text(
                f"🔢 For Maths, default counts are:\n" +
                "\n".join(f"{k}: {v}" for k,v in state["current_exercise_counts"].items()) +
                "\n\nIf these are correct, reply `ok`. Otherwise, send a new count for one type, e.g., `O2=25`."
            )
            state["state"] = "waiting_exercise_counts"
        else:
            # ask for counts one by one
            current_type = valid_types[0]
            state["state"] = "waiting_exercise_counts"
            state["pending_exercise_types"] = valid_types
            await update.message.reply_text(f"How many questions in **{current_type}**? (send a number)")
        return True

    elif state["state"] == "waiting_exercise_counts":
        if text.lower() == "ok" or text.lower() == "done":
            # finalize this chapter
            chapter_key = state["current_hw_chapter"]
            save_chapter_exercise_counts(chapter_key, state["current_exercise_counts"])
            total_time = estimate_homework_time("", state["current_exercise_counts"])
            # add a homework task for this chapter with combined effort
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
            # back to entering next chapter
            state["state"] = "waiting_homework_start"
            await update.message.reply_text(f"✅ Added homework for {task['subject']} - {task['chapter']} (est. {total_time} min). Next chapter key or `done`.")
            return True

        # if we are in Maths default mode, user may send adjustments like O2=25
        if "=" in text:
            parts = text.split("=")
            ex_type = parts[0].strip()
            try:
                count = int(parts[1])
                if ex_type in state["current_exercise_counts"]:
                    state["current_exercise_counts"][ex_type] = count
                    await update.message.reply_text(f"Updated {ex_type} to {count}. Send another adjustment or `ok`.")
                else:
                    await update.message.reply_text("Type not in list. Send `ok` to finish or another adjustment.")
            except ValueError:
                await update.message.reply_text("Invalid number. Use format `O2=25`.")
            return True

        # regular count entry (non-maths)
        try:
            count = int(text)
            # find the next type that hasn't been filled
            remaining_types = [t for t in state["pending_exercise_types"] if t not in state["current_exercise_counts"]]
            if not remaining_types:
                await update.message.reply_text("All types have counts. Reply `ok` to finalize.")
                return True
            current_type = remaining_types[0]
            state["current_exercise_counts"][current_type] = count
            # ask for next
            next_types = [t for t in state["pending_exercise_types"] if t not in state["current_exercise_counts"]]
            if next_types:
                await update.message.reply_text(f"How many questions in **{next_types[0]}**? (send a number)")
            else:
                await update.message.reply_text("All types entered. Reply `ok` to finalize this chapter.")
            return True
        except ValueError:
            await update.message.reply_text("Please enter a number, or `ok` to finish.")
            return True

    # ---- skip homework ----
    elif state["state"] == "waiting_skip_homework":
        if text.lower() == "none":
            state["skip_keywords"] = []
        else:
            state["skip_keywords"] = [kw.strip() for kw in text.split(",") if kw.strip()]
        # finalize
        state["state"] = "finalize"
        return await handle_checkin_message(update, context)   # jump to finalize

    # ---- finalize (plan generation) ----
    if state["state"] == "finalize":
        await update.message.reply_text("✅ Check‑in complete! Generating your personalised plan...")
        # 1. Save homework (all entered)
        memory["homework"] = {"date": datetime.now().strftime("%Y-%m-%d"), "tasks": state["homework"]}
        save_json("homework", memory["homework"])

        # 2. Test handling
        test_chapters_for_plan = []
        ongoing_now = get_ongoing_chapters()
        if state["test_info"]:
            test_syllabus = list(ongoing_now)
            test_syllabus.extend(state["test_11th_chapters"])
            test_syllabus = list(set(test_syllabus))
            test_entry = {
                "name": state["test_info"]["name"],
                "date": state["test_info"]["date"],
                "syllabus": test_syllabus,
                "importance": 8
            }
            memory["tests"]["upcoming"].append(test_entry)
            save_json("tests", memory["tests"])
            test_chapters_for_plan = test_syllabus

        # 3. Generate plan
        plan = generate_plan(
            study_hours_override=state["study_hours"],
            skip_keywords=state["skip_keywords"],
            test_chapters=test_chapters_for_plan,
            ongoing_chapters=ongoing_now
        )

        # 4. Backlog estimate
        daily_hrs = state["study_hours"] if state["study_hours"] else memory["schedule"]["study_hours"]
        days = estimate_backlog_days(daily_hrs)

        # 5. Send plan
        plan_msg = "📅 *Your Daily Plan*\n" + "\n".join(
            f"`{b['start']}-{b['end']}` {b['description']} ({b['status']})" for b in plan
        )
        plan_msg += f"\n\n⏳ *Backlog estimate:* At {daily_hrs}h/day, you'll clear all pending backlog in ~{days} day(s)."
        await update.message.reply_text(plan_msg, parse_mode='Markdown')

        # 6. Reminders
        for b in plan:
            if b["type"] != "break":
                try:
                    start_time = datetime.strptime(b["start"], "%H:%M").time()
                    context.job_queue.run_daily(
                        lambda ctx, txt=f"⏰ Start: {b['description']} now!": ctx.bot.send_message(ctx.job.chat_id, text=txt),
                        time=start_time,
                        chat_id=chat_id,
                        name=b["task_id"]
                    )
                except: pass

        del checkin_states[chat_id]
        return True

    return False

# ---------- PDF upload for weekly schedule ----------
async def weekly_schedule_prompt(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id, "📅 It's Saturday! Please upload your class schedule PDF for batch CETQAS. I'll try to extract your weekly timetable.")
    # set a flag to expect PDF
    context.bot_data.setdefault("expecting_pdf", {})
    context.bot_data["expecting_pdf"][chat_id] = True

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.bot_data.get("expecting_pdf", {}).get(chat_id):
        doc = update.message.document
        if doc.mime_type == "application/pdf":
            file = await context.bot.get_file(doc.file_id)
            file_path = f"/tmp/{chat_id}_schedule.pdf"
            await file.download_to_drive(file_path)
            # extract text
            text = ""
            if PDF_SUPPORT:
                try:
                    with open(file_path, "rb") as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages:
                            text += page.extract_text() or ""
                except:
                    text = ""
            if "CETQAS" in text:
                # Try to parse days and times (simplified)
                lines = text.split('\n')
                timetable = []
                for line in lines:
                    if any(day in line for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]):
                        timetable.append(line.strip())
                if timetable:
                    memory["schedule"]["weekly_timetable"] = "\n".join(timetable)
                    save_json("schedule", memory["schedule"])
                    await update.message.reply_text("✅ Timetable extracted and saved.")
                else:
                    await update.message.reply_text("Found CETQAS but couldn't parse timetable. Please use /week_update to enter it manually.")
            else:
                await update.message.reply_text("Could not find CETQAS in the PDF. Please use /week_update to enter manually.")
            # clean up
            os.remove(file_path)
        else:
            await update.message.reply_text("Please send a PDF file.")
        # reset flag
        context.bot_data["expecting_pdf"][chat_id] = False

# ---------- Telegram commands (most unchanged) ----------
async def start(update, context):
    await update.message.reply_text("🚀 JEE Study OS ready! Use /help for commands. I'll wake you up at your scheduled time.")

async def help_cmd(update, context):
    text = """
📚 *JEE Study OS – Commands*

🌅 *Morning Check‑in*
Automatically at wake‑up time. I'll ask about sleep, study hours, tests, homework with exercise counts (O1..JA), and skippable tasks.

📌 Manual
/set_schedule – wake_up|sleep|study_hours
/add_backlog – Add backlog tasks
/view_backlog – Show backlog
/update_syllabus – Change chapter status (use `going_on` for current)
/view_syllabus – Progress
/add_test – Add test
/view_tests – List tests
/start_day – Generate today's plan manually
/view_plan – Show plan
/complete_task – Mark done
/stats – Productivity
/ask <q> – AI coach
/week_update – Manual timetable entry

📎 PDF upload: Every Saturday I'll ask for your schedule PDF.
"""
    await update.message.reply_text(text, parse_mode='Markdown')

async def set_schedule_cmd(update, context):
    await update.message.reply_text("Send: `wake_up|sleep|study_hours`\nExample: `07:00|22:00|8`")
    context.user_data['mode'] = 'schedule'

async def add_backlog_cmd(update, context):
    await update.message.reply_text("Send backlog tasks like:\n`Physics|Gravitation|Theory|90|test_name`\nType `done`.")
    context.user_data['mode'] = 'backlog'
    context.user_data['temp'] = []

async def add_test_cmd(update, context):
    await update.message.reply_text("Send test info like:\n`Test Name|YYYY-MM-DD|Physics_Kinematics,Chemistry_Bonding|importance(1-10)`\nType `done`.")
    context.user_data['mode'] = 'test'

async def update_syllabus_cmd(update, context):
    await update.message.reply_text("Send chapter key and new status:\n`Physics_Kinematics|going_on`")
    context.user_data['mode'] = 'syllabus'

async def view_syllabus(update, context):
    chaps = memory["syllabus"]["chapters"]
    weak = get_weak_chapters()
    ongoing = get_ongoing_chapters()
    msg = "📖 *Syllabus Status:*\n"
    for k, v in list(chaps.items())[:20]:
        emoji = "🟢" if v["status"] in ("completed","going_on") else "🔴" if v["status"]=="weak" else "⚪"
        msg += f"{emoji} {v['subject']} - {v['chapter']} [{v['status']}]\n"
    if ongoing:
        msg += "\n📌 *Currently studying:* " + ", ".join(ongoing[:5])
    if weak:
        msg += "\n⚠️ Weak topics: " + ", ".join(weak[:5])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"]["tasks"] if t["status"] != "done"]
    if not tasks:
        await update.message.reply_text("No pending backlog.")
        return
    msg = "📋 *Backlog*\n" + "\n".join(f"• {t['subject']} {t['chapter']} ({t['type']}) est.{t['estimated_time']}min  (id: {t['id'][:8]}...)" for t in tasks)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def stats(update, context):
    prod = memory["stats"]["productivity"]
    avg = sum(prod)/len(prod) if prod else 0
    await update.message.reply_text(f"📊 Avg daily tasks completed: {avg:.1f}\nConsistency: {memory['stats']['consistency']}%")

async def complete_task_cmd(update, context):
    await update.message.reply_text("Send task ID (from /view_plan) or a part of the description.")
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
    plan = generate_plan()
    if not plan:
        await update.message.reply_text("No tasks to plan. Add homework or backlog first.")
        return
    daily_hrs = memory["schedule"]["study_hours"]
    days = estimate_backlog_days(daily_hrs)
    msg = "✅ Today's plan generated! Here it is:\n" + "\n".join(
        f"`{b['start']}-{b['end']}` {b['description']} ({b['status']})" for b in plan
    )
    msg += f"\n\n⏳ *Backlog estimate:* At {daily_hrs}h/day, you'll clear all pending backlog in ~{days} day(s)."
    await update.message.reply_text(msg, parse_mode='Markdown')
    for b in plan:
        if b["type"] != "break":
            try:
                start_time = datetime.strptime(b["start"], "%H:%M").time()
                context.job_queue.run_daily(
                    lambda ctx, txt=f"⏰ Start: {b['description']} now!": ctx.bot.send_message(ctx.job.chat_id, text=txt),
                    time=start_time,
                    chat_id=update.effective_chat.id,
                    name=b["task_id"]
                )
            except: pass

async def view_plan(update, context):
    today = memory["today"]
    if not today.get("generated"):
        await update.message.reply_text("No plan yet. Use /start_day or wait for morning check‑in.")
        return
    msg = "📅 *Today's Plan*\n" + "\n".join(
        f"`{b['start']}-{b['end']}` {b['description']} [{b['status']}]" for b in today["plan"]
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ---------- message router ----------
async def handle_message(update, context):
    # check-in flow first
    if await handle_checkin_message(update, context):
        return
    # then PDF mode
    if context.bot_data.get("expecting_pdf", {}).get(update.effective_chat.id):
        await handle_document(update, context)
        return
    # then input modes
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
                    "subject": parts[0],
                    "chapter": parts[1],
                    "type": parts[2],
                    "estimated_time": int(parts[3]),
                    "test_link": parts[4] if len(parts)>4 else "",
                    "status": "pending",
                    "source": "self",
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
                test = {
                    "name": parts[0],
                    "date": parts[1],
                    "syllabus": [s.strip() for s in parts[2].split(',')] if len(parts)>2 else [],
                    "importance": int(parts[3]) if len(parts)>3 else 5
                }
                memory["tests"]["upcoming"].append(test)
                save_json("tests", memory["tests"])
                context.user_data['mode'] = None
                await update.message.reply_text("Test added.")
        return
    elif mode == 'schedule':
        parts = text.split('|')
        if len(parts) == 3:
            memory["schedule"]["wake_up"] = parts[0]
            memory["schedule"]["sleep"] = parts[1]
            memory["schedule"]["study_hours"] = int(parts[2])
            save_json("schedule", memory["schedule"])
            for job in context.job_queue.jobs():
                if job.name == "morning_checkin":
                    job.schedule_removal()
            schedule_morning_checkin(context.job_queue, parts[0], update.effective_chat.id)
            await update.message.reply_text("Schedule updated and morning check‑in reset.")
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
                await update.message.reply_text("Invalid chapter key. Check /view_syllabus.")
            context.user_data['mode'] = None
        return
    elif mode == 'complete':
        today = memory["today"]
        found = False
        for block in today.get("plan", []):
            if text.lower() in block["task_id"].lower() or text.lower() in block["description"].lower():
                block["status"] = "done"
                found = True
                memory["progress"]["logs"].append({
                    "task_id": block["task_id"],
                    "description": block["description"],
                    "timestamp": datetime.now().isoformat()
                })
                save_json("progress", memory["progress"])
                break
        if found:
            memory["today"] = today
            save_json("today", memory["today"])
            today_str = datetime.now().strftime("%Y-%m-%d")
            done_today = sum(1 for l in memory["progress"]["logs"] if l["timestamp"].startswith(today_str))
            memory["stats"]["productivity"].append(done_today)
            save_json("stats", memory["stats"])
            await update.message.reply_text("✅ Task marked done. Great job!")
        else:
            await update.message.reply_text("Task not found. Check ID or description.")
        context.user_data['mode'] = None
        return
    # No active mode → reject casual chat
    await update.message.reply_text("I'm in coach mode. Use /help to see commands, or wait for your morning check‑in. If you need AI assistance, try /ask <question>.")

# ---------- autonomous missed task checker ----------
async def autonomous_check(context):
    today = memory["today"]
    if not today.get("generated"):
        return
    now = datetime.now().strftime("%H:%M")
    plan = today["plan"]
    updated = False
    for block in plan:
        if block["status"] == "pending" and now > block["end"]:
            block["status"] = "missed"
            updated = True
            await context.bot.send_message(chat_id=context.job.chat_id, text=f"⚠️ Missed: {block['description']} ({block['start']}-{block['end']})")
    if updated:
        memory["today"] = today
        save_json("today", memory["today"])

def schedule_morning_checkin(job_queue, wake_up_str, chat_id):
    wake_time = datetime.strptime(wake_up_str, "%H:%M").time()
    job_queue.run_daily(
        start_morning_checkin,
        time=wake_time,
        chat_id=chat_id,
        name="morning_checkin"
    )

def schedule_weekly_pdf_prompt(job_queue, chat_id):
    """Ask for PDF every Saturday 8 AM."""
    job_queue.run_daily(
        weekly_schedule_prompt,
        time=datetime.strptime("08:00", "%H:%M").time(),
        days=(5,),  # Saturday (Monday=0)
        chat_id=chat_id,
        name="weekly_pdf_prompt"
    )

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("set_schedule", set_schedule_cmd))
    app.add_handler(CommandHandler("add_backlog", add_backlog_cmd))
    app.add_handler(CommandHandler("add_test", add_test_cmd))
    app.add_handler(CommandHandler("update_syllabus", update_syllabus_cmd))
    app.add_handler(CommandHandler("view_syllabus", view_syllabus))
    app.add_handler(CommandHandler("view_backlog", view_backlog))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("complete_task", complete_task_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    app.add_handler(CommandHandler("week_update", week_update_cmd))
    app.add_handler(CommandHandler("start_day", start_day))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    if app.job_queue:
        app.job_queue.run_repeating(autonomous_check, interval=600, first=10)
        schedule = memory["schedule"]
        schedule_morning_checkin(app.job_queue, schedule["wake_up"], None)
        schedule_weekly_pdf_prompt(app.job_queue, None)  # will need a chat_id; we use None for now and rely on job context
    print("Bot polling...")
    app.run_polling()