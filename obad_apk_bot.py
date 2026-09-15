"""
Obad APK Factory Bot
---------------------
بوت تليجرام يستقبل ملف ZIP (مشروع أندرويد) من المستخدم،
يرفعه تلقائيًا إلى GitHub، يشغّل بناء APK عبر GitHub Actions،
ثم يرسل ملف APK النهائي رجوعًا للمستخدم في تليجرام.

قبل التشغيل، عبّئ القيم في قسم "الإعدادات" أسفل هذا الملف.
"""

import os
import time
import base64
import zipfile
import shutil
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters


class _HealthCheckHandler(BaseHTTPRequestHandler):
    """معالج بسيط يرد بـ 200 فقط ليقتنع Render أن الخدمة حية."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Obad APK Factory Bot is alive.")

    def log_message(self, format, *args):
        pass  # لتجنب إغراق السجل برسائل كل فحص صحي


def _start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    server.serve_forever()

# ========================= الإعدادات =========================
# القيم الحساسة تُقرأ من متغيرات البيئة (Environment Variables) فقط
# ولا تُكتب أبدًا مباشرة هنا داخل الكود.
BOT_TOKEN = os.environ["BOT_TOKEN"]           # يُقرأ من Environment Variable باسم BOT_TOKEN
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]     # يُقرأ من Environment Variable باسم GITHUB_TOKEN

GITHUB_OWNER = "moh636888-alt"                       # اسم حسابك في GitHub
GITHUB_REPO = "obad-apk-factory"                     # اسم المستودع
GITHUB_BRANCH = "main"
WORKFLOW_FILE = "build.yml"                          # اسم ملف الأتمتة داخل .github/workflows/
# ==============================================================

GITHUB_API = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def push_project_via_git(extracted_dir, work_dir):
    """
    يستنسخ المستودع عبر git، يستبدل محتواه بمشروع الأندرويد الجديد
    (مع الحفاظ على .github/workflows)، ثم يدفعه دفعة واحدة.
    هذه الطريقة أوثق بكثير من رفع الملفات واحدًا تلو الآخر عبر API،
    لأنها لا تفشل جزئيًا لملف دون آخر - إما تنجح كاملة أو تفشل برسالة واضحة.
    """
    repo_url = (
        f"https://{GITHUB_TOKEN}@github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
    )
    clone_dir = os.path.join(work_dir, "repo_clone")

    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", GITHUB_BRANCH, repo_url, clone_dir],
        capture_output=True, text=True,
    )
    if clone.returncode != 0:
        return False, f"فشل الاستنساخ (clone):\n{clone.stderr[-800:]}"

    # حذف كل شيء عدا .github (لتفادي بقايا ملفات مشروع سابق)
    for name in os.listdir(clone_dir):
        if name in (".git", ".github"):
            continue
        full_path = os.path.join(clone_dir, name)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)

    # نسخ ملفات المشروع الجديد كاملة
    for item in os.listdir(extracted_dir):
        src = os.path.join(extracted_dir, item)
        dst = os.path.join(clone_dir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    subprocess.run(["git", "config", "user.email", "bot@obad-factory.local"], cwd=clone_dir)
    subprocess.run(["git", "config", "user.name", "Obad APK Factory Bot"], cwd=clone_dir)
    subprocess.run(["git", "add", "-A"], cwd=clone_dir)

    commit = subprocess.run(
        ["git", "commit", "-m", "تحديث مشروع الأندرويد عبر البوت"],
        cwd=clone_dir, capture_output=True, text=True,
    )
    # لو ما فيه تغييرات فعلية، commit يرجع كود خطأ - نتجاهله بدون توقف
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        pass

    push = subprocess.run(
        ["git", "push", "origin", GITHUB_BRANCH],
        cwd=clone_dir, capture_output=True, text=True,
    )
    if push.returncode != 0:
        return False, f"فشل الدفع (push):\n{push.stderr[-800:]}"

    return True, "تم الرفع بنجاح"


def trigger_workflow():
    """يشغّل GitHub Actions يدويًا (workflow_dispatch). يرجع (نجح؟, رسالة التفاصيل)."""
    url = (
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/{WORKFLOW_FILE}/dispatches"
    )
    resp = requests.post(url, headers=HEADERS, json={"ref": GITHUB_BRANCH})
    if resp.status_code == 204:
        return True, "تم التشغيل بنجاح"
    return False, f"HTTP {resp.status_code}: {resp.text}"


def get_latest_run_id():
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs"
    resp = requests.get(url, headers=HEADERS, params={"branch": GITHUB_BRANCH, "per_page": 1})
    runs = resp.json().get("workflow_runs", [])
    return runs[0]["id"] if runs else None


def wait_for_run_completion(run_id, timeout=600, interval=15):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}"
    waited = 0
    while waited < timeout:
        resp = requests.get(url, headers=HEADERS)
        data = resp.json()
        if data.get("status") == "completed":
            return data.get("conclusion")  # "success" أو "failure"
        time.sleep(interval)
        waited += interval
    return "timeout"


def download_apk_artifact(run_id, dest_dir):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}/artifacts"
    resp = requests.get(url, headers=HEADERS)
    artifacts = resp.json().get("artifacts", [])
    if not artifacts:
        return None

    artifact = artifacts[0]
    download_url = artifact["archive_download_url"]
    zip_resp = requests.get(download_url, headers=HEADERS)

    zip_path = os.path.join(dest_dir, "artifact.zip")
    with open(zip_path, "wb") as f:
        f.write(zip_resp.content)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(dest_dir)

    for root, _, files in os.walk(dest_dir):
        for name in files:
            if name.endswith(".apk"):
                return os.path.join(root, name)
    return None


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document or not document.file_name.lower().endswith(".zip"):
        await update.message.reply_text("يرجى إرسال ملف ZIP لمشروع أندرويد فقط.")
        return

    chat_id = update.message.chat_id
    work_dir = f"/tmp/obad_{chat_id}_{int(time.time())}"
    os.makedirs(work_dir, exist_ok=True)

    await update.message.reply_text("📦 استلمت الملف، جاري رفعه إلى المصنع...")

    zip_path = os.path.join(work_dir, "project.zip")
    tg_file = await document.get_file()
    await tg_file.download_to_drive(zip_path)

    extract_dir = os.path.join(work_dir, "extracted")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)

    ok, detail = push_project_via_git(extract_dir, work_dir)
    if not ok:
        await update.message.reply_text(f"⚠️ تعذر رفع المشروع إلى GitHub.\nالتفاصيل:\n{detail}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    await update.message.reply_text("🚀 تم الرفع، بدأ البناء الآن... (قد يستغرق 2-5 دقائق)")

    ok, detail = trigger_workflow()
    if not ok:
        await update.message.reply_text(
            f"⚠️ تعذر بدء عملية البناء.\nسبب الفشل الحقيقي من GitHub:\n{detail}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    time.sleep(10)  # انتظار قصير قبل قراءة رقم التشغيل
    run_id = get_latest_run_id()

    if not run_id:
        await update.message.reply_text("⚠️ تعذر بدء عملية البناء. تحقق من إعدادات المصنع.")
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    conclusion = wait_for_run_completion(run_id)

    if conclusion != "success":
        run_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}/job"
        await update.message.reply_text(
            f"❌ فشل البناء (النتيجة: {conclusion}).\n"
            f"هذا يعني الآن أن الرفع نجح، والمشكلة داخل الكود نفسه (مثلاً: مكتبة ناقصة أو خطأ Gradle).\n"
            f"راجع تفاصيل السجل هنا:\n{run_url}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    apk_path = download_apk_artifact(run_id, work_dir)
    if apk_path and os.path.exists(apk_path):
        await update.message.reply_document(document=open(apk_path, "rb"))
        await update.message.reply_text("✅ تفضل، هذا ملف APK الجاهز.")
    else:
        await update.message.reply_text("⚠️ نجح البناء لكن تعذر العثور على ملف APK الناتج.")

    shutil.rmtree(work_dir, ignore_errors=True)


def main():
    # نشغّل سيرفر الفحص الصحي في خيط منفصل بالتوازي مع البوت
    threading.Thread(target=_start_health_check_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.Document.ALL, handle_zip))
    print("Obad APK Factory Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
