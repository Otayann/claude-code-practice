#!/usr/bin/env python3
"""旅の動画自動整理アプリ

Google Drive上の「撮影素材」フォルダ内の動画ファイルを
AIリネーム → 縦横振り分け → 日付フォルダ振り分け の順で自動整理する。

使い方:
    python organize.py           # 全ての動画を処理
    python organize.py 5         # 先頭5本だけ処理（テスト用）
"""

import calendar
import io
import os
import re
import sys
import tempfile
import time
from datetime import datetime

import cv2
import google.generativeai as genai
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from mutagen.mp4 import MP4

# ── 設定 ──────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_ID = "1w1r3rwrp5MCQXJEKdKoo7yCdvLW1Q6U6"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v")
GEMINI_MODEL = "gemini-2.0-flash"

# API制限対策: Gemini API呼び出しごとの待機秒数（無料枠: 15 req/min → 4秒以上）
GEMINI_SLEEP_SECONDS = 5
# 429エラー時の最大リトライ回数
MAX_RETRIES = 3

# 日付範囲ルール
DATE_RANGES = [
    (1, 5),
    (6, 10),
    (11, 15),
    (16, 20),
    (21, 25),
    # 26〜末日は動的に決定
]

# リネーム済みファイル名のパターン: 場所_内容_連番_MM-DD-YYYY.拡張子
RENAMED_PATTERN = re.compile(
    r"^(.+?)_(.+?)_(\d{3})_(\d{2})-(\d{2})-(\d{4})\.[^.]+$"
)


def authenticate_drive():
    """Google Drive API の OAuth2.0 認証を行い、サービスを返す。"""
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), "token.json")
    creds_path = os.path.join(os.path.dirname(__file__), "credentials.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                print("エラー: credentials.json が見つかりません。")
                print("Google Cloud Console から OAuth2.0 認証情報をダウンロードしてください。")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def configure_gemini():
    """Gemini API を設定する。"""
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "xxxxx":
        print("エラー: GEMINI_API_KEY が設定されていません。")
        print(".env ファイルに有効な Gemini API キーを設定してください。")
        sys.exit(1)
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL)


def list_video_files(service):
    """指定フォルダ内の動画ファイル一覧を取得する。"""
    ext_conditions = " or ".join(
        [f"name contains '{ext}'" for ext in VIDEO_EXTENSIONS]
    )
    query = f"'{FOLDER_ID}' in parents and ({ext_conditions}) and trashed = false"

    results = []
    page_token = None
    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, createdTime, mimeType)",
                pageToken=page_token,
            )
            .execute()
        )
        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # 拡張子で再フィルタ（name contains はあいまい一致のため）
    filtered = []
    for f in results:
        name_lower = f["name"].lower()
        if any(name_lower.endswith(ext) for ext in VIDEO_EXTENSIONS):
            filtered.append(f)

    return filtered


def parse_renamed_filename(name):
    """既にリネーム済みのファイル名を解析する。

    Returns:
        (location, content, serial, date) のタプル、または None
    """
    match = RENAMED_PATTERN.match(name)
    if not match:
        return None
    location = match.group(1)
    content = match.group(2)
    serial = match.group(3)
    month = int(match.group(4))
    day = int(match.group(5))
    year = int(match.group(6))
    try:
        date = datetime(year, month, day)
    except ValueError:
        return None
    return location, content, serial, date


def download_partial(service, file_id, byte_count=2 * 1024 * 1024):
    """動画ファイルの先頭部分をダウンロードする（サムネイル抽出用）。"""
    request = service.files().get_media(fileId=file_id)
    request.headers["Range"] = f"bytes=0-{byte_count - 1}"
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    try:
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except Exception:
        pass
    buffer.seek(0)
    return buffer.read()


def extract_thumbnail(video_data):
    """動画データから先頭フレームをサムネイル画像として抽出する。"""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_data)
        tmp_path = tmp.name

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            return None, None, None

        ret, frame = cap.read()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        if not ret or frame is None:
            return None, width, height

        _, img_encoded = cv2.imencode(".jpg", frame)
        return img_encoded.tobytes(), width, height
    finally:
        os.unlink(tmp_path)


def get_video_date(video_data, drive_created_time):
    """動画の撮影日時を取得する。MP4メタデータ → Google Drive作成日の優先順。"""
    # MP4メタデータから取得を試みる
    if video_data:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_data)
            tmp_path = tmp.name
        try:
            mp4 = MP4(tmp_path)
            if "©day" in mp4.tags:
                date_str = mp4.tags["©day"][0]
                for fmt in [
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d",
                    "%Y",
                ]:
                    try:
                        return datetime.strptime(
                            date_str[: len(fmt.replace("%", "X"))], fmt
                        )
                    except (ValueError, IndexError):
                        continue
                match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_str)
                if match:
                    return datetime(
                        int(match.group(1)),
                        int(match.group(2)),
                        int(match.group(3)),
                    )
        except Exception:
            pass
        finally:
            os.unlink(tmp_path)

    # Google Driveの作成日にフォールバック
    if drive_created_time:
        try:
            return datetime.strptime(drive_created_time, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            try:
                return datetime.strptime(drive_created_time, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                pass

    return datetime.now()


def analyze_with_gemini(model, thumbnail_data):
    """Gemini AIにサムネイル画像を送り、場所と内容を判定する。

    429エラー（クォータ超過）が出たら待機して自動リトライする。
    """
    if thumbnail_data is None:
        return "不明", "不明"

    prompt = (
        "この画像は旅行中に撮影された動画の1フレームです。\n"
        "以下の2つを日本語で簡潔に答えてください。\n"
        "1. 撮影場所（具体的な名所・地名がわかればそれを、わからなければ「不明」）\n"
        "2. 撮影内容（何をしているか、何が映っているかを短く）\n\n"
        "回答形式（必ずこの形式で）:\n"
        "場所: ○○○\n"
        "内容: ○○○\n\n"
        "注意:\n"
        "- 場所・内容はファイル名に使えるよう短く（各10文字以内推奨）\n"
        "- スペースやスラッシュなどファイル名に使えない文字は使わない\n"
        "- 確信がない場合は「不明」と答えてください"
    )

    image_part = {
        "mime_type": "image/jpeg",
        "data": thumbnail_data,
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content([prompt, image_part])
            text = response.text.strip()

            location = "不明"
            content = "不明"
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("場所:") or line.startswith("場所："):
                    location = line.split(":", 1)[-1].split("：", 1)[-1].strip()
                elif line.startswith("内容:") or line.startswith("内容："):
                    content = line.split(":", 1)[-1].split("：", 1)[-1].strip()

            location = sanitize_filename_part(location)
            content = sanitize_filename_part(content)

            return location or "不明", content or "不明"

        except Exception as e:
            error_str = str(e)
            # 429クォータエラーかチェック
            is_quota_error = "429" in error_str or "quota" in error_str.lower()

            if is_quota_error and attempt < MAX_RETRIES - 1:
                # retry_delayがレスポンスに含まれている場合はそれを使う
                retry_seconds = 60
                match = re.search(r"seconds:\s*(\d+)", error_str)
                if match:
                    retry_seconds = int(match.group(1)) + 5
                print(
                    f"  ⚠ APIクォータ超過。{retry_seconds}秒待機してリトライします... "
                    f"(試行 {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(retry_seconds)
                continue
            else:
                print(f"  ⚠ Gemini API エラー: {error_str[:200]}")
                return "不明", "不明"

    return "不明", "不明"


def sanitize_filename_part(name):
    """ファイル名に使えない文字を除去する。"""
    name = re.sub(r'[\\/:*?"<>|\s]', "", name)
    return name


def get_date_range_folder(dt):
    """日付から日付範囲フォルダ名を生成する。"""
    day = dt.day
    month = dt.month
    year = dt.year

    for start, end in DATE_RANGES:
        if start <= day <= end:
            return f"{start}日〜{end}日"

    last_day = calendar.monthrange(year, month)[1]
    return f"26日〜{last_day}日"


def get_month_folder(dt):
    """日付から月フォルダ名を生成する。"""
    return f"{dt.month}月"


def find_or_create_folder(service, parent_id, folder_name):
    """指定フォルダ内にサブフォルダを検索し、なければ作成する。"""
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{folder_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]


def rename_file(service, file_id, new_name):
    """Google Drive上のファイル名を変更する。"""
    service.files().update(fileId=file_id, body={"name": new_name}).execute()


def move_file(service, file_id, new_parent_id, current_parent_id):
    """Google Drive上のファイルを別フォルダへ移動する。"""
    service.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=current_parent_id,
        fields="id, parents",
    ).execute()


def process_videos(limit=None):
    """メイン処理: 動画ファイルの整理を実行する。

    Args:
        limit: 処理する最大本数（Noneなら全件）
    """
    print("=" * 50)
    print("  旅の動画自動整理アプリ")
    print("=" * 50)
    print()
    print(f"対象フォルダ: 撮影素材 (ID: {FOLDER_ID})")
    if limit:
        print(f"⚠ テストモード: 先頭 {limit} 本だけ処理します")
    print()
    print("処理内容:")
    print("  ① AIリネーム（Gemini で場所・内容を判定）")
    print("  ② 縦横フォルダへ振り分け")
    print("  ③ 日付フォルダへ振り分け")
    print()
    print(f"※ API制限対策として、各動画の処理後に {GEMINI_SLEEP_SECONDS} 秒待機します")
    print()

    confirm = input("実行しますか？ (y/N): ").strip().lower()
    if confirm != "y":
        print("キャンセルしました。")
        return

    print()
    print("認証中...")
    service = authenticate_drive()
    model = configure_gemini()

    print("動画ファイルを取得中...")
    videos = list_video_files(service)

    if not videos:
        print("動画ファイルが見つかりませんでした。")
        return

    # limit指定時は先頭N本だけに絞る
    if limit and limit > 0:
        videos = videos[:limit]

    total = len(videos)
    print(f"{total} 本の動画ファイルを処理します。")
    print()

    success_count = 0

    for idx, video in enumerate(videos, 1):
        file_id = video["id"]
        original_name = video["name"]
        created_time = video.get("createdTime", "")
        ext = os.path.splitext(original_name)[1].lower()

        print(f"[{idx}/{total}] {original_name} を処理中...")

        try:
            # ── 既にリネーム済みかチェック（不明_不明_... の再処理対応） ──
            parsed = parse_renamed_filename(original_name)
            existing_serial = None
            existing_date = None
            if parsed:
                prev_location, prev_content, existing_serial, existing_date = parsed
                print(
                    f"  既にリネーム済み (場所={prev_location}, 内容={prev_content}, "
                    f"連番={existing_serial}, 日付={existing_date.strftime('%Y-%m-%d')})"
                )

            # ── 動画データの部分ダウンロード ──
            print("  ダウンロード中...")
            video_data = download_partial(service, file_id)

            # ── サムネイル抽出 & 解像度取得 ──
            print("  サムネイル抽出中...")
            thumbnail, width, height = extract_thumbnail(video_data)

            # ── Gemini AI で場所・内容を判定 ──
            print("  AI分析中...")
            location, content = analyze_with_gemini(model, thumbnail)

            # ── 撮影日時を取得（既存の日付があればそれを使う） ──
            if existing_date:
                video_date = existing_date
            else:
                video_date = get_video_date(video_data, created_time)

            # ── ① リネーム ──
            date_str = video_date.strftime("%m-%d-%Y")
            # 既存の連番があればそれを使う、なければ今回の連番を使う
            serial = existing_serial if existing_serial else f"{idx:03d}"
            new_name = f"{location}_{content}_{serial}_{date_str}{ext}"

            # 既にリネーム済みで、場所・内容も同じならリネームをスキップ
            if new_name != original_name:
                print(f"  リネーム: {new_name}")
                rename_file(service, file_id, new_name)
            else:
                print(f"  リネーム不要")

            # ── ② 縦横振り分け ──
            if width is not None and height is not None and width > 0 and height > 0:
                orientation_folder = "縦動画" if height > width else "横動画"
            else:
                orientation_folder = "横動画"
                print("  ⚠ 解像度を取得できませんでした。横動画として振り分けます。")

            orientation_id = find_or_create_folder(
                service, FOLDER_ID, orientation_folder
            )

            # ── ③ 日付フォルダ振り分け ──
            month_folder = get_month_folder(video_date)
            month_id = find_or_create_folder(service, orientation_id, month_folder)

            date_range_folder = get_date_range_folder(video_date)
            date_range_id = find_or_create_folder(service, month_id, date_range_folder)

            # ── ファイルを移動 ──
            move_file(service, file_id, date_range_id, FOLDER_ID)

            dest_path = f"{orientation_folder}/{month_folder}/{date_range_folder}/"
            print(f"[{idx}/{total}] {new_name} → {dest_path} ✅")
            success_count += 1

        except Exception as e:
            print(f"[{idx}/{total}] {original_name} → エラー: {e} ❌")

        # API制限対策: 次の動画処理まで少し待つ（最後の動画のあとは待たない）
        if idx < total:
            time.sleep(GEMINI_SLEEP_SECONDS)

        print()

    print("=" * 50)
    print(f"=== 完了！{total}本中{success_count}本を整理しました ===")
    print("=" * 50)


def parse_args():
    """コマンドライン引数を解析する。"""
    limit = None
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
            if limit <= 0:
                print("エラー: 処理本数は1以上の整数を指定してください。")
                sys.exit(1)
        except ValueError:
            print(f"エラー: 引数は整数で指定してください（例: python organize.py 5）")
            sys.exit(1)
    return limit


if __name__ == "__main__":
    limit = parse_args()
    process_videos(limit=limit)
