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
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

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


def clear_repo_source():
    """يحذف الملفات القديمة من المستودع (باستثناء .github) قبل رفع مشروع جديد."""
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return
    for item in resp.json():
        if item["name"] == ".github":
            continue
        delete_path(item["path"])


def delete_path(path):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    resp = requests.get(url, headers=HEADERS, params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        return
    data = resp.json()
    if isinstance(data, list):
        for entry in data:
            delete_path(entry["path"])
    else:
        requests.delete(
            url,
            headers=HEADERS,
            json={
                "message": f"remove {path}",
                "sha": data["sha"],
                "branch": GITHUB_BRANCH,
            },
        )


def upload_file_to_github(local_path, repo_path):
    """يرفع ملف واحد إلى GitHub عبر Contents API (يُنشئ أو يُحدّث)."""
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{repo_path}"
    with open(local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    existing = requests.get(url, headers=HEADERS, params={"ref": GITHUB_BRANCH})
    sha = existing.json().get("sha") if existing.status_code == 200 else None

    payload = {
        "message": f"upload {repo_path}",
        "content": content,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=HEADERS, json=payload)
    return resp.status_code in (200, 201)


def upload_project_folder(extracted_dir):
    """يرفع كل ملفات مشروع الأندرويد المفكوك بالحفاظ على بنية المجلدات."""
    for root, _, files in os.walk(extracted_dir):
        for name in files:
            local_path = os.path.join(root, name)
            rel_path = os.path.relpath(local_path, extracted_dir).replace("\\", "/")
            upload_file_to_github(local_path, rel_path)


def trigger_workflow():
    """يشغّل GitHub Actions يدويًا (workflow_dispatch)."""
    url = (
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/{WORKFLOW_FILE}/dispatches"
    )
    resp = requests.post(url, headers=HEADERS, json={"ref": GITHUB_BRANCH})
    return resp.status_code == 204


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

    clear_repo_source()
    upload_project_folder(extract_dir)

    await update.message.reply_text("🚀 تم الرفع، بدأ البناء الآن... (قد يستغرق 2-5 دقائق)")

    trigger_workflow()
    time.sleep(10)  # انتظار قصير قبل قراءة رقم التشغيل
    run_id = get_latest_run_id()

    if not run_id:
        await update.message.reply_text("⚠️ تعذر بدء عملية البناء. تحقق من إعدادات المصنع.")
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    conclusion = wait_for_run_completion(run_id)

    if conclusion != "success":
        run_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}"
        await update.message.reply_text(
            f"❌ فشل البناء (النتيجة: {conclusion}).\nراجع السجل هنا:\n{run_url}"
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
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.Document.ALL, handle_zip))
    print("Obad APK Factory Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
