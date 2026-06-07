import os
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"  # free, fast, excellent for coaching

DATA_DIR = "/tmp/data" if os.getenv("RENDER") else "data"
os.makedirs(DATA_DIR, exist_ok=True)
lock = threading.Lock()

# ---------- memory functions (same as before) ----------
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
}

def get_weak_chapters():
    return [k for k,v in memory["syllabus"]["chapters"].items() if v["status"] in ("weak","revision_needed")]

def get_test_chapters():
    chaps = []
    for t in memory["tests"]["upcoming"]:
        chaps.extend(t.get("syllabus", []))
    return list(set(chaps))

def compute_priority(task):
    score = 50
    weak = get_weak_chapters()
    test_chaps = get_test_chapters()
    if task.get("test_link") in [t["name"] for t in memory["tests"]["upcoming"]]:
        score += 30
    if task.get("chapter_key") in weak:
        score += 25
    if task.get("source") in ("test", "AI"):
        score += 15
    return score

def generate_plan():
    schedule = memory["schedule"]
    wake = schedule["wake_up"]
    study_mins = schedule["study_hours"] * 60
    hw = memory["homework"]["tasks"] if memory["homework"]["date"] == datetime.now().strftime("%Y-%m-%d") else []
    backlog = [t for t in memory["backlog"]["tasks"] if t["status"] != "done"]
    all_tasks = []
    for t in hw:
        t["priority_score"] = compute_priority(t)
        all_tasks.append(t)
    for t in backlog:
        t["priority_score"] = compute_priority(t)
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

# ---------- AI (Groq) ----------
def ask_ai(prompt):
    if not GROQ_KEY:
        return "AI not available. Set GROQ_API_KEY."
    time.sleep(1)  # gentle rate limit
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
    system_msg = "You are JEE Study OS, a strict Kota JEE coach + general assistant. Use the memory context. Be motivational, data-driven. Never overwrite user data. Reply in same language as user."
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

# ---------- Telegram Handlers (all included, help_cmd fixed) ----------
async def start(update, context):
    await update.message.reply_text("🚀 JEE Study OS ready! Send /help to see commands.")

async def help_cmd(update, context):
    text = """
📚 *JEE Study OS Commands*

📌 Daily
/start_day – Generate today's plan
/view_plan – Show today's schedule
/add_homework – Add coaching homework
/view_homework – View today's homework
/complete_task – Mark a task as done

📌 Backlog & Syllabus
/add_backlog – Add backlog tasks
/view_backlog – Show pending backlog
/update_syllabus – Change chapter status
/view_syllabus – See your progress

📌 Tests & Stats
/add_test – Add upcoming test
/view_tests – List tests
/stats – Your productivity stats

📌 Schedule
/set_schedule – Set wake-up, sleep, study hours
/week_update – Weekly timetable update

🧠 General chat – Ask me anything!
"""
    await update.message.reply_text(text)

async def start_day(update, context):
    plan = generate_plan()
    if not plan:
        await update.message.reply_text("No tasks to plan. Add homework or backlog first.")
        return
    msg = "✅ Today's plan generated! Here it is:\n" + "\n".join(
        f"`{b['start']}-{b['end']}` {b['description']} ({b['status']})" for b in plan
    )
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
        await update.message.reply_text("No plan yet. Use /start_day.")
        return
    msg = "📅 *Today's Plan*\n" + "\n".join(
        f"`{b['start']}-{b['end']}` {b['description']} [{b['status']}]" for b in today["plan"]
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def add_homework_cmd(update, context):
    await update.message.reply_text("Send homework tasks one by one like:\n`Physics|Rotation|O2|60`\n(subject|topic|type|minutes)\nType `done` when finished.")
    context.user_data['mode'] = 'homework'
    context.user_data['temp'] = []

async def add_backlog_cmd(update, context):
    await update.message.reply_text("Send backlog tasks like:\n`Physics|Gravitation|Theory|90|test_name`\nType `done`.")
    context.user_data['mode'] = 'backlog'
    context.user_data['temp'] = []

async def add_test_cmd(update, context):
    await update.message.reply_text("Send test info like:\n`Test Name|YYYY-MM-DD|Physics_Kinematics,Chemistry_Bonding|importance(1-10)`\nType `done`.")
    context.user_data['mode'] = 'test'

async def set_schedule_cmd(update, context):
    await update.message.reply_text("Send: `wake_up|sleep|study_hours`\nExample: `07:00|22:00|8`")
    context.user_data['mode'] = 'schedule'

async def week_update_cmd(update, context):
    await update.message.reply_text("Send your weekly class timetable (any format) or type `skip`.")
    context.user_data['mode'] = 'weekly'

async def update_syllabus_cmd(update, context):
    await update.message.reply_text("Send chapter key and new status:\n`Physics_Kinematics|completed`\n(Keys shown in /view_syllabus)")
    context.user_data['mode'] = 'syllabus'

async def view_syllabus(update, context):
    chaps = memory["syllabus"]["chapters"]
    weak = get_weak_chapters()
    msg = "📖 *Syllabus Status:*\n"
    for k, v in list(chaps.items())[:20]:
        emoji = "🟢" if v["status"] in ("completed","going_on") else "🔴" if v["status"]=="weak" else "⚪"
        msg += f"{emoji} {v['subject']} - {v['chapter']} [{v['status']}]\n"
    if weak:
        msg += "\n⚠️ Weak topics: " + ", ".join(weak[:5])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def view_backlog(update, context):
    tasks = [t for t in memory["backlog"]["tasks"] if t["status"] != "done"]
    if not tasks:
        await update.message.reply_text("No pending backlog.")
        return
    msg = "📋 *Backlog*\n" + "\n".join(f"• {t['subject']} {t['chapter']} ({t['type']}) est.{t['estimated_time']}min" for t in tasks)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def stats(update, context):
    prod = memory["stats"]["productivity"]
    avg = sum(prod)/len(prod) if prod else 0
    await update.message.reply_text(f"📊 Avg daily tasks completed: {avg:.1f}\nConsistency: {memory['stats']['consistency']}%")

async def complete_task_cmd(update, context):
    await update.message.reply_text("Send task ID (from /view_plan) or a part of the description.")
    context.user_data['mode'] = 'complete'

async def handle_message(update, context):
    text = update.message.text
    mode = context.user_data.get('mode')
    if mode == 'homework':
        if text.lower() == 'done':
            memory["homework"] = {"date": datetime.now().strftime("%Y-%m-%d"), "tasks": context.user_data['temp']}
            save_json("homework", memory["homework"])
            context.user_data['mode'] = None
            await update.message.reply_text("✅ Homework saved.")
        else:
            parts = text.split('|')
            if len(parts) >= 4:
                task = {
                    "id": str(int(datetime.timestamp(datetime.now()))),
                    "subject": parts[0],
                    "chapter": parts[1],
                    "type": parts[2],
                    "estimated_time": int(parts[3]),
                    "source": "coaching",
                    "status": "pending"
                }
                context.user_data['temp'].append(task)
                await update.message.reply_text(f"Added {task['subject']} - {task['chapter']}. Send next or 'done'.")
        return
    elif mode == 'backlog':
        if text.lower() == 'done':
            for t in context.user_data['temp']:
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
                    "source": "self"
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
            context.user_data['mode'] = None
            await update.message.reply_text("Schedule updated.")
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
    # AI chat
    reply = ask_ai(text)
    await update.message.reply_text(reply)

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
    app.add_handler(CommandHandler("start_day", start_day))
    app.add_handler(CommandHandler("view_plan", view_plan))
    app.add_handler(CommandHandler("add_homework", add_homework_cmd))
    app.add_handler(CommandHandler("add_backlog", add_backlog_cmd))
    app.add_handler(CommandHandler("add_test", add_test_cmd))
    app.add_handler(CommandHandler("set_schedule", set_schedule_cmd))
    app.add_handler(CommandHandler("week_update", week_update_cmd))
    app.add_handler(CommandHandler("update_syllabus", update_syllabus_cmd))
    app.add_handler(CommandHandler("view_syllabus", view_syllabus))
    app.add_handler(CommandHandler("view_backlog", view_backlog))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("complete_task", complete_task_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    if app.job_queue:
        app.job_queue.run_repeating(autonomous_check, interval=600, first=10)
    print("Bot polling...")
    app.run_polling()