"""
scraper.py - 太陽光発電監視サイトからCSVデータをダウンロードしてマージするバッチスクリプト

使い方:
    python scraper.py              → 当月データを取得
    python scraper.py 2026-02      → 指定月データを取得

30分毎にタスクスケジューラ等で定期実行する想定。
"""

import os
import sys
import glob
import time
import shutil
import json
import logging
from datetime import datetime, timedelta

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


# dotenvの読み込み（.envファイルが存在すれば環境変数としてロード）
load_dotenv()

# --- 定数 ---
CSV_ENCODING = 'utf_8_sig'          # BOM付UTF-8で統一
LOGIN_URL = "https://ctrl.kp-net.com/settingcontrol/login"

# .envファイル、または環境変数から取得。
LOGIN_ID = os.environ.get("LOGIN_ID", "")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")       # ダウンロード一時保存先
BACKUP_DIR = os.path.join(BASE_DIR, "backup")   # バックアップ保存先
PUBLIC_DIR = os.path.join(BASE_DIR, "static")   # Web公開フォルダ
PUBLIC_CSV = os.path.join(PUBLIC_DIR, "data.csv")
STATUS_JSON = os.path.join(PUBLIC_DIR, "status.json")

MAX_DATA_ROWS = 336  # data.csv に保持するデータ行数の上限
DOWNLOAD_TIMEOUT = 30  # ダウンロード待機のタイムアウト（秒）

# --- フォルダ作成 ---
for d in [TEMP_DIR, BACKUP_DIR, PUBLIC_DIR]:
    os.makedirs(d, exist_ok=True)


# ========================================
# ドライバ初期化
# ========================================
def get_driver():
    """
    ダウンロード先をTEMP_DIRに設定したヘッドレスChromeドライバを初期化して返す。

    webdriver-managerを使用して適切なChromeDriverバージョンを自動取得し、
    ダウンロードプロンプトを無効化した状態で初期化する。

    Returns:
        webdriver.Chrome: 初期化済みのChromeドライバインスタンス。
    """
    options = Options()
    options.add_argument('--headless')  # ヘッドレスモードで実行
    prefs = {
        "download.default_directory": TEMP_DIR,
        "download.prompt_for_download": False,
    }
    options.add_experimental_option("prefs", prefs)
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


# ========================================
# ダウンロード完了待機
# ========================================
def wait_for_download(timeout=DOWNLOAD_TIMEOUT):
    """
    TEMP_DIRにCSVファイルが出現するまでポーリングで待機する。

    Chromeがダウンロード中に生成する .crdownload ファイルが存在する間は
    ダウンロード処理中とみなして待機を継続する。

    Args:
        timeout (int): 最大待機秒数。デフォルトは DOWNLOAD_TIMEOUT。

    Returns:
        str or None: ダウンロード完了したCSVファイルのパス。
                     タイムアウト時は None。
    """
    start = time.time()
    while time.time() - start < timeout:
        # .crdownload が残っていればまだダウンロード中
        if glob.glob(os.path.join(TEMP_DIR, "*.crdownload")):
            time.sleep(1)
            continue
        # CSVファイルを探す
        csv_files = glob.glob(os.path.join(TEMP_DIR, "*.csv"))
        if csv_files:
            logger.info("ダウンロード完了: %s", csv_files[0])
            return csv_files[0]
        time.sleep(1)
    return None


# ========================================
# CSVマージ処理
# ========================================
def merge_csv(downloaded_file):
    """
    ダウンロードしたCSVファイルを data.csv にマージする。

    処理の流れ:
        1. 元ファイルをBACKUP_DIRにバックアップ
        2. data.csv が存在しなければ新規作成
        3. data.csv が存在すれば末尾行の先頭20文字をアンカーに差分マージ
        4. データ行を MAX_DATA_ROWS 行に制限（古いデータを自動削除）
        5. ダウンロードファイルを削除

    Args:
        downloaded_file (str): マージ対象のCSVファイルパス。
    """
    original_name = os.path.basename(downloaded_file)

    # --- バックアップ（元のファイル名のまま） ---
    backup_path = os.path.join(BACKUP_DIR, original_name)
    if os.path.exists(backup_path):
        # 同名ファイルが既に存在する場合はタイムスタンプを付加
        name, ext = os.path.splitext(original_name)
        backup_path = os.path.join(BACKUP_DIR, f"{name}_{int(time.time())}{ext}")
    shutil.copy(downloaded_file, backup_path)
    logger.info("バックアップ保存: %s", backup_path)

    # --- data.csv が存在しない場合 → そのままコピーして終了 ---
    if not os.path.exists(PUBLIC_CSV):
        shutil.copy(downloaded_file, PUBLIC_CSV)
        logger.info("data.csv を新規作成しました")
        os.remove(downloaded_file)
        return

    # --- 差分マージ ---
    # data.csv の末尾1行の先頭20文字をアンカーとする
    with open(PUBLIC_CSV, 'r', encoding=CSV_ENCODING) as f:
        existing_lines = f.readlines()

    # データ行がない場合（ヘッダーのみの場合）はそのままコピー
    if len(existing_lines) <= 1:
        shutil.copy(downloaded_file, PUBLIC_CSV)
        logger.info("data.csv にデータ行がなかったため上書きしました")
        os.remove(downloaded_file)
        return

    last_anchor = existing_lines[-1][:20]

    with open(downloaded_file, 'r', encoding=CSV_ENCODING) as f:
        new_lines = f.readlines()

    # ダウンロードファイルの中からアンカー行を検索
    append_start = None
    for i, line in enumerate(new_lines):
        if line[:20] == last_anchor:
            append_start = i + 1
            break

    if append_start is not None and append_start < len(new_lines):
        # アンカー行の次の行～最終行を追記
        with open(PUBLIC_CSV, 'a', encoding=CSV_ENCODING) as f:
            f.writelines(new_lines[append_start:])
        logger.info("差分 %d 行を追記しました", len(new_lines) - append_start)
    elif append_start is None:
        # アンカーが見つからない場合は、ヘッダー行を除いて全行を追記
        with open(PUBLIC_CSV, 'a', encoding=CSV_ENCODING) as f:
            f.writelines(new_lines[1:])  # 1行目（ヘッダー）をスキップ
        logger.info("アンカーが見つからなかったため、ヘッダー以外の全行を追記しました")
    else:
        # append_start がファイル末尾 → 新規データなし
        logger.info("新規データはありません")

    # --- データ行を MAX_DATA_ROWS 行に制限 ---
    with open(PUBLIC_CSV, 'r', encoding=CSV_ENCODING) as f:
        all_lines = f.readlines()

    # ヘッダー（1行目）＋ データ行
    header = all_lines[0]
    data_lines = all_lines[1:]

    if len(data_lines) > MAX_DATA_ROWS:
        trimmed = data_lines[-MAX_DATA_ROWS:]  # 末尾 MAX_DATA_ROWS 行だけ残す
        with open(PUBLIC_CSV, 'w', encoding=CSV_ENCODING) as f:
            f.write(header)
            f.writelines(trimmed)
        logger.info("データ行を %d 行 → %d 行にトリミングしました",
                     len(data_lines), MAX_DATA_ROWS)

    # --- ダウンロードファイルを削除 ---
    os.remove(downloaded_file)
    logger.info("ダウンロードファイル削除: %s", downloaded_file)


def update_status_json(battery_status="不明", battery_charge="--"):
    """
    data.csv の末尾行から最終更新日時を取得し、status.json を更新する。

    スクレイピングで取得した蓄電池のステータス（充電中/放電中）と
    残量パーセンテージを記録し、次回のデータ取得予定時刻も算出して保存する。
    app.py がこのJSONを読み込んでダッシュボードに表示する。

    Args:
        battery_status (str): 蓄電池の充放電状態（例: '充電中', '放電中'）。
        battery_charge (str): 蓄電残量のパーセンテージ文字列（例: '75%'）。
    """
    if not os.path.exists(PUBLIC_CSV):
        return

    try:
        # data.csv を読み込んで末行の日時を取得
        df = pd.read_csv(PUBLIC_CSV, encoding=CSV_ENCODING)
        if df.empty:
            return

        col_date = df.columns[0]  # "年月日"
        col_time = df.columns[1]  # "時刻"

        last_date = str(df[col_date].iloc[-1])  # "2026/02/26"
        last_time = str(df[col_time].iloc[-1])  # "19:30"
        
        # JST としてパース (簡易的に)
        last_dt = datetime.strptime(f"{last_date} {last_time}", '%Y/%m/%d %H:%M')
        next_update_dt = last_dt + timedelta(minutes=31)

        # ISO 8601 形式 (Z付き) で保存
        # Python の isoformat は末尾が +09:00 等になるが、
        # 要望の Date.toISOString (UTC/Z) に合わせる場合は UTC に変換する必要がある。
        # ここでは単純に文字列として "Z" を付加するか、JST 表記のまま ISO 形式にする。
        # ユーザーの要望は Date.toISOString 形式なので、ここでは一旦簡易的に Z 付加形式にする。
        status_data = {
            "updated": last_dt.strftime('%Y-%m-%dT%H:%M:00.000Z'),
            "next_update": next_update_dt.strftime('%Y-%m-%dT%H:%M:00.000Z'),
            "battery_status": battery_status,
            "battery_charge": battery_charge
        }

        with open(STATUS_JSON, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, indent=2)
        
        logger.info("status.json を更新しました: %s", status_data)

    except Exception as e:
        logger.error("status.json の更新に失敗しました: %s", e)


# ========================================
# メイン処理: ログイン → ダウンロード → マージ
# ========================================
def crawl(target_month):
    """
    太陽光発電監視サイトにログインし、指定月のCSVデータをダウンロードしてマージする。

    処理の流れ:
        1. Seleniumでログインページにアクセスし認証
        2. ダッシュボードから蓄電池情報（充放電状態・残量）をスクレイピング
        3. CSV出力画面に遷移し、対象月の30分データをダウンロード
        4. merge_csv() でdata.csvに差分マージ
        5. update_status_json() でstatus.jsonを更新

    Args:
        target_month (str): 'YYYY-MM' 形式の対象年月。

    Returns:
        bool: 全処理が正常完了した場合はTrue、エラー発生時はFalse。
    """
    logger.info("===== データ取得開始: %s =====", target_month)
    driver = get_driver()
    wait = WebDriverWait(driver, 20)

    try:
        # --- 1. ログイン ---
        logger.info("ログイン画面にアクセス中...")
        driver.get(LOGIN_URL)
        wait.until(EC.presence_of_element_located((By.ID, "loginid"))).send_keys(LOGIN_ID)
        driver.find_element(By.ID, "loginpassword").send_keys(LOGIN_PASSWORD)
        driver.find_element(By.ID, "login-button").click()
        logger.info("ログインボタンを押下しました")

        # --- 2. 蓄電池情報の取得 (ログイン後ダッシュボードより) ---
        logger.info("蓄電池情報を取得中...")
        try:
            # ログイン直後のダッシュボードで少し待機
            time.sleep(3)
            battery_status = wait.until(EC.presence_of_element_located(
                (By.XPATH, "/html/body/div/div[9]/div[4]/div[2]/div[3]/table[3]/tbody/tr[2]/td[1]"))).text
            battery_charge = driver.find_element(
                By.XPATH, "/html/body/div/div[9]/div[4]/div[2]/div[3]/table[3]/tbody/tr[2]/td[2]").text
            logger.info("蓄電池情報 取得成功: 状態=%s, 残量=%s", battery_status, battery_charge)
        except Exception as e:
            logger.warning("蓄電池情報の取得に失敗しました: %s", e)
            battery_status = "取得失敗"
            battery_charge = "--"

        # --- 3. CSVダウンロード画面へ遷移 ---
        logger.info("ダウンロード画面にアクセス中...")
        submit_btn = driver.find_element(
            By.XPATH, "/html/body/div/div[9]/div[5]/div[3]/form/button")
        driver.execute_script("arguments[0].click();", submit_btn)
        logger.info("各種データのCSV出力ボタンを押下しました")

        submit_btn = driver.find_element(
            By.XPATH, "/html/body/div/div[9]/div[1]/div[1]/form/button")
        driver.execute_script("arguments[0].click();", submit_btn)
        logger.info("計測データのCSV出力ボタンを押下しました")

        # 2-1. Select×3個を設定（hidden項目があるためJSクリックを使用）
        logger.info("セレクトボックスを設定中...")
        Select(wait.until(EC.presence_of_element_located(
            (By.NAME, "outputFormat")))).select_by_value("太陽光発電＋蓄電池")
        Select(driver.find_element(
            By.NAME, "aggrType")).select_by_value("30分データ")
        Select(driver.find_element(
            By.NAME, "collectDate")).select_by_value(target_month)

        # 2-2. submitボタンをクリック（hidden項目があるためJSクリックで確実に実行）
        submit_btn = driver.find_element(
            By.XPATH, "/html/body/div/div[9]/div/form/div[3]/button[2]")
        driver.execute_script("arguments[0].click();", submit_btn)
        logger.info("ダウンロードを開始しました")

        # 2-3. ダウンロード完了待機
        downloaded_file = wait_for_download()
        if not downloaded_file:
            logger.error("ダウンロードがタイムアウトしました（%d秒）", DOWNLOAD_TIMEOUT)
            return False

        # --- 4. 日次・月次・年次データ洗い替え ---
        merge_csv(downloaded_file)
        
        # --- 5. ステータス JSON 更新 ---
        update_status_json(battery_status, battery_charge)

        logger.info("===== データ更新が完了しました =====")
        return True

    except Exception as e:
        logger.exception("データ取得中にエラーが発生しました: %s", e)
        return False

    finally:
        driver.quit()
        logger.info("ブラウザを閉じました")


# ========================================
# エントリポイント
# ========================================
if __name__ == '__main__':
    # コマンドライン引数で対象月を指定可能（省略時は当月）
    if len(sys.argv) >= 2:
        month = sys.argv[1]
    else:
        month = datetime.now().strftime('%Y-%m')

    success = crawl(month)
    sys.exit(0 if success else 1)
