"""
get_past_weather.py - 気象庁の過去気象データをダウンロードし、CSV形式で保存するスクリプト

使い方:
    python get_past_weather.py

気象庁の「過去の気象データ・ダウンロード」ページ（https://www.data.jma.go.jp/risk/obsdl/）
からPOSTリクエストでCSVデータを取得し、pandasで整形したうえで
static/past_weather.csv に保存する。元データは backup/ にバックアップされる。

WAF対策: JMAサイトはCloudFront WAFで sec-fetch-user ヘッダーを検証する。
requests で sec-fetch-user: ?1 を明示的に送信することで回避する。
"""
import psutil
import requests
import logging
import pandas as pd
import os
import shutil
from datetime import datetime, timedelta
import calendar
from dotenv import load_dotenv

load_dotenv()

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def log_with_memory(message):
    try:
        parent = psutil.Process(os.getpid())
        total_mem = parent.memory_info().rss
        for child in parent.children(recursive=True):
            try:
                total_mem += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        mem_mb = total_mem / 1024 / 1024
        logging.info(f"{message} (Total Memory: {mem_mb:.2f} MB)")
    except Exception as e:
        logging.error(f"メモリ計測エラー: {e}")

def _build_ymd_list():
    """
    気象庁APIリクエスト用のymdListパラメータを生成する。

    取得開始日は以下の優先順位で決定する:
    1. past_weather.csv が存在する場合: その最終日の翌日
    2. data.csv が存在する場合: その最初の日
    3. いずれも存在しない場合: 前月の同日（従来動作）

    終了日は昨日（JMAは当日データが不完全なため）。

    Returns:
        list: [開始年, 終了年, 開始月, 終了月, 開始日, 終了日] の文字列リスト。
        None: 既にデータが最新状態でダウンロード不要の場合。
    """
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    end_date = yesterday.date()

    start_date = None

    weather_csv = os.path.join("static", "past_weather.csv")
    data_csv = os.path.join("static", "data.csv")

    if os.path.exists(weather_csv):
        try:
            df_w = pd.read_csv(weather_csv, usecols=[0])
            if len(df_w) > 0:
                latest_dt = pd.to_datetime(df_w.iloc[:, 0]).max()
                candidate = (latest_dt + timedelta(days=1)).date()
                if candidate > end_date:
                    logger.info("past_weather.csv は既に最新状態です。スキップします。")
                    return None
                start_date = candidate
                logger.info(f"past_weather.csv の続きから取得: {start_date} 〜 {end_date}")
        except Exception as e:
            logger.warning(f"past_weather.csv の最終日取得に失敗: {e}")

    if start_date is None and os.path.exists(data_csv):
        try:
            df_d = pd.read_csv(data_csv, encoding='utf_8_sig', usecols=[0], nrows=1)
            if len(df_d) > 0:
                first_date_str = str(df_d.iloc[0, 0])  # 'YYYY/MM/DD'
                start_date = datetime.strptime(first_date_str, '%Y/%m/%d').date()
                logger.info(f"data.csv の開始日から全件取得: {start_date} 〜 {end_date}")
        except Exception as e:
            logger.warning(f"data.csv の開始日取得に失敗: {e}")

    if start_date is None:
        # フォールバック: 前月同日〜昨日（従来動作）
        if now.month > 1:
            prev_year = now.year
            prev_month = now.month - 1
        else:
            prev_year = now.year - 1
            prev_month = 12
        prev_month_days = calendar.monthrange(prev_year, prev_month)[1]
        prev_day = min(now.day, prev_month_days)
        start_date = datetime(prev_year, prev_month, prev_day).date()

    start_dt = datetime(start_date.year, start_date.month, start_date.day)

    return [
        str(start_dt.year), str(end_date.year),
        str(start_dt.month), str(end_date.month),
        str(start_dt.day), str(end_date.day),
    ]


# 取得する気象要素コードリスト
# JMAのtop/elementで確認した有効コード
_ELEMENT_NUM_LIST = '[["201",""],["101",""],["401",""],["301",""],["503",""],["610",""],["601",""],["501",""],["604",""],["607",""],["703",""]]'


def download_jma_data():
    """
    気象庁の過去気象データダウンロードAPIにPOSTリクエストを送信し、
    CSVデータを取得して temp/ フォルダに保存する。

    JMAサイトのCloudFront WAFは sec-fetch-user: ?1 ヘッダーを検証する。
    このヘッダーを明示的に含めることでWAFを通過し、CSVを取得できる。

    Returns:
        list: [is_success (bool), ファイルパス (str)] のリスト。
    """
    log_with_memory("--- 気象庁天気API取得開始 ---")

    url_init = "https://www.data.jma.go.jp/risk/obsdl/index.php"
    url_download = "https://www.data.jma.go.jp/risk/obsdl/show/table"

    session = requests.Session()

    # ブラウザと同じヘッダーセット
    browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    sec_ch_ua = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'

    headers_init = {
        "User-Agent": browser_ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "dnt": "1",
        "upgrade-insecure-requests": "1",
    }

    logger.info("気象庁 index.php にアクセスしてAWSALBクッキーを取得します...")
    try:
        session.get(url_init, headers=headers_init, timeout=15)
        logger.info(f"クッキー取得完了: AWSALB={session.cookies.get('AWSALB', 'N/A')[:20]}...")
    except requests.exceptions.RequestException as e:
        logger.error(f"index.phpアクセスに失敗しました: {e}")
        return [False, ""]

    JMA_STATION_NUM = os.environ.get("JMA_STATION_NUM")
    if not JMA_STATION_NUM:
        logger.error("JMA_STATION_NUM が .env に設定されていません")
        return [False, ""]

    ymd_list = _build_ymd_list()
    if ymd_list is None:
        log_with_memory("--- 気象庁天気API取得スキップ（データ最新） ---")
        return [False, ""]
    logger.info(f"取得期間: {ymd_list[0]}年{ymd_list[2]}月{ymd_list[4]}日 〜 {ymd_list[1]}年{ymd_list[3]}月{ymd_list[5]}日")

    ymd_json = f'["{ymd_list[0]}","{ymd_list[1]}","{ymd_list[2]}","{ymd_list[3]}","{ymd_list[4]}","{ymd_list[5]}"]'

    payload = {
        "stationNumList": f'["{JMA_STATION_NUM}"]',
        "aggrgPeriod": "9",
        "elementNumList": _ELEMENT_NUM_LIST,
        "interAnnualType": "1",
        "ymdList": ymd_json,
        "optionNumList": "[]",
        "downloadFlag": "true",
        "rmkFlag": "0",
        "disconnectFlag": "0",
        "youbiFlag": "0",
        "fukenFlag": "0",
        "kijiFlag": "0",
        "csvFlag": "1",
        "jikantaiFlag": "0",
        "jikantaiList": "[1,24]",
        "ymdLiteral": "1"
    }

    # sec-fetch-user: ?1 が必須。これがないとCloudFront WAFにブロックされる。
    headers_post = {
        "User-Agent": browser_ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.data.jma.go.jp",
        "Referer": url_init,
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "dnt": "1",
        "priority": "u=0, i",
        "upgrade-insecure-requests": "1",
    }

    logger.info("CSVデータをリクエストしています...")
    try:
        response = session.post(url_download, data=payload, headers=headers_post, timeout=30)
    except requests.exceptions.RequestException as e:
        logger.error(f"ダウンロードリクエストに失敗しました: {e}")
        return [False, ""]

    save_dir = "temp"
    os.makedirs(save_dir, exist_ok=True)
    today = datetime.now().strftime('%Y%m%d%H%M%S')

    status_code = response.status_code
    content_type = response.headers.get("Content-Type", "")
    is_success = "text/html" not in content_type

    if is_success:
        logger.info(f"リクエスト成功 (Status: {status_code}, CT: {content_type})")
        filepath = os.path.join(save_dir, f"pw_{today}.csv")
    else:
        logger.error(f"リクエスト失敗 (Status: {status_code}, CT: {content_type})")
        filepath = os.path.join(save_dir, f"pw_{today}_error.html")

    with open(filepath, "wb") as f:
        f.write(response.content)
    logger.info(f"レスポンスを '{filepath}' に保存しました ({len(response.content)} bytes)")

    log_with_memory("--- 気象庁天気API取得完了 ---")
    return [is_success, filepath]


def convert_response(filepath_raw):
    """
    ダウンロードした生CSVデータを整形し、static/past_weather.csv に追記マージする。

    気象庁のCSVは cp932 エンコーディングで、先頭数行にメタ情報が含まれるため、
    skiprows で不要行を除去し、重複する列名（風向）を修正してから既存データと
    マージして UTF-8 で static/ に保存する。処理後、元ファイルは backup/ に移動される。

    Args:
        filepath_raw (str): ダウンロードした生CSVファイルのパス。
    """
    log_with_memory("--- CSV整形開始 ---")
    logger.info("レスポンスを使いやすい形に加工します。")

    try:
        df_new = pd.read_csv(
            filepath_raw,
            encoding='cp932',
            skiprows=[0, 1, 2, 4]
        )

        # JMAのCSVは風速と風向を同じ列名（風速(m/s)）で出力するため、
        # pandasが2つ目の重複列に「.1」を付ける（例: 風速(m/s).1）。
        # この列は実際には風向データ（北北東 等）なので「風向」に修正する。
        cols = list(df_new.columns)
        for i, col in enumerate(cols):
            if col.endswith('.1') and ('風速' in col or '風向' in col):
                cols[i] = '風向'
                break
        df_new.columns = cols

        save_dir = "static"
        os.makedirs(save_dir, exist_ok=True)
        filepath_new = os.path.join(save_dir, "past_weather.csv")

        date_col = df_new.columns[0]  # '年月日時'

        if os.path.exists(filepath_new):
            try:
                df_existing = pd.read_csv(filepath_new, encoding='utf-8')
                df_merged = pd.concat([df_existing, df_new], ignore_index=True)
                df_merged = df_merged.drop_duplicates(subset=[date_col], keep='last')
                df_merged = df_merged.sort_values(date_col).reset_index(drop=True)
                df_merged.to_csv(filepath_new, index=False, encoding='utf-8')
                logger.info(f"既存データとマージし '{filepath_new}' に保存しました（{len(df_merged)}行）")
            except Exception as e:
                logger.warning(f"既存データのマージに失敗、新規データのみ保存します: {e}")
                df_new.to_csv(filepath_new, index=False, encoding='utf-8')
                logger.info(f"CSVを整形し '{filepath_new}' に保存しました（{len(df_new)}行）")
        else:
            df_new.to_csv(filepath_new, index=False, encoding='utf-8')
            logger.info(f"CSVを整形し '{filepath_new}' に新規保存しました（{len(df_new)}行）")

        log_with_memory("--- CSV整形完了 ---")

    except Exception as e:
        logger.error(f"PandasでのCSVパース中にエラーが発生しました: {e}")


def backup(filepath_raw):
    try:
        backup_dir = "backup"
        os.makedirs(backup_dir, exist_ok=True)

        filename_only = os.path.basename(filepath_raw)
        filepath_bk = os.path.join(backup_dir, filename_only)
        shutil.move(filepath_raw, filepath_bk)

        logger.info(f"{filepath_raw}を{filepath_bk}にバックアップしました。")

    except Exception as e:
        logger.error(f"バックアップ中にエラーが発生しました: {e}")


if __name__ == "__main__":
    [is_success, filepath] = download_jma_data()

    if is_success:
        convert_response(filepath)

    if filepath:
        backup(filepath)
