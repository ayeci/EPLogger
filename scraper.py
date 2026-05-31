"""
scraper.py - 太陽光発電監視サイトからCSVデータをダウンロードしてマージするバッチスクリプト

使い方:
    python scraper.py              → 当月データを取得
    python scraper.py 2026-02      → 指定月データを取得

30分毎にタスクスケジューラ等で定期実行する想定。
"""

import os
import psutil
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


def log_with_memory(message):
    try:
        # 自分自身（Pythonプロセス）を取得
        parent = psutil.Process(os.getpid())
        # 自分のメモリ
        total_mem = parent.memory_info().rss
        
        # すべての子プロセス（Chrome、ChromeDriverなど）を再帰的に取得して加算
        for child in parent.children(recursive=True):
            try:
                total_mem += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # 計測途中でプロセスが終了した場合などはスキップ
                pass
        
        mem_mb = total_mem / 1024 / 1024
        logging.info(f"{message} (Total Memory: {mem_mb:.2f} MB)")
    except Exception as e:
        logging.error(f"メモリ計測エラー: {e}")

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
        4. ダウンロードファイルを削除

    Args:
        downloaded_file (str): マージ対象のCSVファイルパス。
    """

    log_with_memory("--- CSVマージ開始 ---")
    
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

    # --- ダウンロードファイルを削除 ---
    os.remove(downloaded_file)
    logger.info("ダウンロードファイル削除: %s", downloaded_file)
    
    log_with_memory("--- CSVマージ完了 ---")


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
# 取得月リストの算出
# ========================================
def _month_range(start_ym, end_ym):
    """'YYYY-MM' の start から end まで（両端含む）の月リストを昇順で返す"""
    result = []
    y, m = map(int, start_ym.split('-'))
    ey, em = map(int, end_ym.split('-'))
    while (y, m) <= (ey, em):
        result.append(f'{y:04d}-{m:02d}')
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


def get_months_to_fetch(current_month_str):
    """
    data.csv の末尾を確認し、取得が必要な月のリストを返す。

    判定ルール:
      - data.csv 未存在 / 空 / 最終取得が当月  → [current_month_str]
      - 月またぎ かつ 最終取得時刻が '23:30'    → 最終取得の翌月〜当月
      - 月またぎ かつ 最終取得時刻が '23:30' 以外 → 最終取得月〜当月（途中から補完）

    Args:
        current_month_str (str): 今回取得対象の 'YYYY-MM' 文字列。

    Returns:
        list[str]: 取得すべき月の 'YYYY-MM' リスト（昇順）。
    """
    if not os.path.exists(PUBLIC_CSV):
        return [current_month_str]
    try:
        df = pd.read_csv(PUBLIC_CSV, encoding=CSV_ENCODING)
        if df.empty:
            return [current_month_str]

        col_date = df.columns[0]
        col_time = df.columns[1]

        last_date = str(df[col_date].iloc[-1])  # 'YYYY/MM/DD'
        last_time = str(df[col_time].iloc[-1])  # 'HH:MM'

        last_month = datetime.strptime(last_date, '%Y/%m/%d').strftime('%Y-%m')

        if last_month == current_month_str:
            return [current_month_str]

        # 月をまたいでいる
        logger.info("月またぎを検出: 最終取得=%s %s, 取得対象=%s",
                    last_date, last_time, current_month_str)

        if last_time == '23:30':
            # 最終取得月は末尾まで取得済み → 翌月から
            y, m = map(int, last_month.split('-'))
            m += 1
            if m > 12:
                m, y = 1, y + 1
            start_month = f'{y:04d}-{m:02d}'
        else:
            # 最終取得月が途中まで → その月から再取得して補完
            start_month = last_month

        months = _month_range(start_month, current_month_str)
        logger.info("取得月リスト: %s", months)
        return months

    except Exception as e:
        logger.warning("取得月リスト算出中にエラーが発生しました: %s", e)
        return [current_month_str]


# ========================================
# メイン処理: ログイン → ダウンロード → マージ
# ========================================
def _login(driver, wait):
    """
    ログインページにアクセスして認証し、蓄電池情報を取得する。

    Returns:
        tuple[str, str]: (battery_status, battery_charge)
    """
    log_with_memory("ログイン画面にアクセス中...")
    driver.get(LOGIN_URL)
    wait.until(EC.presence_of_element_located((By.ID, "loginid"))).send_keys(LOGIN_ID)
    driver.find_element(By.ID, "loginpassword").send_keys(LOGIN_PASSWORD)
    driver.find_element(By.ID, "login-button").click()
    log_with_memory("ログインボタンを押下しました")

    log_with_memory("蓄電池情報を取得中...")
    try:
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

    return battery_status, battery_charge


def _navigate_to_download_form(driver):
    """ダッシュボードから計測データCSVのダウンロードフォームへ遷移する"""
    log_with_memory("ダウンロード画面にアクセス中...")
    submit_btn = driver.find_element(
        By.XPATH, "/html/body/div/div[9]/div[5]/div[3]/form/button")
    driver.execute_script("arguments[0].click();", submit_btn)
    log_with_memory("各種データのCSV出力ボタンを押下しました")

    submit_btn = driver.find_element(
        By.XPATH, "/html/body/div/div[9]/div[1]/div[1]/form/button")
    driver.execute_script("arguments[0].click();", submit_btn)
    log_with_memory("計測データのCSV出力ボタンを押下しました")


def _download_and_merge(driver, wait, target_month):
    """
    ダウンロードフォーム表示済みの状態で、指定月を選択してCSVを取得・マージする。
    ダウンロード後もフォームページに留まるため、連続して呼び出すことができる。

    Args:
        driver: WebDriverインスタンス（ダウンロードフォーム表示済みであること）
        wait: WebDriverWaitインスタンス
        target_month (str): 'YYYY-MM' 形式の対象年月

    Returns:
        bool: 成功した場合True
    """
    log_with_memory(f"セレクトボックスを設定中... ({target_month})")
    Select(wait.until(EC.presence_of_element_located(
        (By.NAME, "outputFormat")))).select_by_value("太陽光発電＋蓄電池")
    Select(driver.find_element(
        By.NAME, "aggrType")).select_by_value("30分データ")
    Select(driver.find_element(
        By.NAME, "collectDate")).select_by_value(target_month)

    submit_btn = driver.find_element(
        By.XPATH, "/html/body/div/div[9]/div/form/div[3]/button[2]")
    driver.execute_script("arguments[0].click();", submit_btn)
    logger.info("ダウンロードを開始しました: %s", target_month)

    downloaded_file = wait_for_download()
    if not downloaded_file:
        logger.error("ダウンロードがタイムアウトしました（%d秒）: %s", DOWNLOAD_TIMEOUT, target_month)
        return False

    merge_csv(downloaded_file)
    return True


def crawl(months):
    """
    1回のログインで複数月のCSVデータをダウンロード・マージする。

    処理の流れ:
        1. ログイン + 蓄電池情報取得（1回のみ）
        2. CSVダウンロードフォームへ遷移（1回のみ）
        3. 指定月ぶんだけ「月選択 → ダウンロード → マージ」をループ
           （ダウンロード後もフォームに留まるため再遷移不要）
        4. status.json を更新

    Args:
        months (list[str]): 取得対象の 'YYYY-MM' リスト（昇順）。

    Returns:
        bool: 全月の処理が正常完了した場合はTrue。
    """
    if not months:
        logger.warning("取得対象月が空です")
        return True

    log_with_memory("--- データ取得開始: " + ', '.join(months) + " ---")
    driver = get_driver()
    wait = WebDriverWait(driver, 20)

    try:
        battery_status, battery_charge = _login(driver, wait)
        _navigate_to_download_form(driver)

        success = True
        for i, month in enumerate(months):
            log_with_memory(f"--- {month} のダウンロード開始 ({i + 1}/{len(months)}) ---")
            if not _download_and_merge(driver, wait, month):
                logger.error("%s のダウンロードに失敗しました", month)
                success = False

        update_status_json(battery_status, battery_charge)
        logger.info("===== データ更新が完了しました =====")
        return success

    except Exception as e:
        logger.exception("データ取得中にエラーが発生しました: %s", e)
        return False

    finally:
        driver.quit()
        log_with_memory("ブラウザを閉じました")
        log_with_memory("--- データ取得完了 ---")


# ========================================
# エントリポイント
# ========================================
if __name__ == '__main__':
    # コマンドライン引数で対象月を指定可能（省略時は当月）
    if len(sys.argv) >= 2:
        month = sys.argv[1]
    else:
        month = datetime.now().strftime('%Y-%m')

    months = get_months_to_fetch(month)
    success = crawl(months)
    sys.exit(0 if success else 1)
