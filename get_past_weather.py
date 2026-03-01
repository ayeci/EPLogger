"""
get_past_weather.py - 気象庁の過去気象データをダウンロードし、CSV形式で保存するスクリプト

使い方:
    python get_past_weather.py

気象庁の「過去の気象データ・ダウンロード」ページ（https://www.data.jma.go.jp/risk/obsdl/）
からPOSTリクエストでCSVデータを取得し、pandasで整形したうえで
static/past_weather.csv に保存する。元データは backup/ にバックアップされる。
"""
import requests
import logging
import pandas as pd
import os
import shutil
from datetime import datetime
import calendar
from dotenv import load_dotenv

load_dotenv()

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def _build_ymd_list():
    """
    気象庁APIリクエスト用のymdListパラメータを生成する。

    現在日付を基準に、前月の同日（存在しない場合は前月末日）から
    当日までの期間を表すJSON文字列を返す。

    Returns:
        str: '["前年","当年","前月","当月","前月日","当日"]' 形式のJSON文字列。
    """
    now = datetime.now()

    # 前月の年・月を算出（1月の場合は前年12月）
    if now.month > 1:
        prev_year = now.year
        prev_month = now.month - 1
    else:
        prev_year = now.year - 1
        prev_month = 12

    # 前月の日数を超えないように調整（例: 3/31 → 2/28）
    prev_month_days = calendar.monthrange(prev_year, prev_month)[1]
    prev_day = min(now.day, prev_month_days)

    return f'["{prev_year}","{now.year}","{prev_month}","{now.month}","{prev_day}","{now.day}"]'


def download_jma_data():
    """
    気象庁の過去気象データダウンロードAPIにPOSTリクエストを送信し、
    CSVデータを取得して temp/ フォルダに保存する。

    セッション維持でCookieを取得した後、指定したペイロードで
    データをリクエストする。レスポンスのステータスコードが200の場合は
    CSVファイルとして、それ以外の場合はHTMLファイルとして保存する。

    Returns:
        list: [ステータスコード (int), ファイルパス (str)] のリスト。
              トップページへのアクセスに失敗した場合は None を返す。
    """

    url_menu = "https://www.data.jma.go.jp/risk/obsdl/"
    url_init = "https://www.data.jma.go.jp/risk/obsdl/index.php"
    url_download = "https://www.data.jma.go.jp/risk/obsdl/show/table"

    # セッションを維持する
    session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url_menu 
    }

    logger.info("気象庁のメニューページにアクセスしてセッション(Cookie)を取得します...")
    try:
        # リダイレクトを追跡せずにステータスコードを確認
        res = session.get(url_menu, headers=headers, timeout=10, allow_redirects=False)
        
        # 300系のステータスコード（リダイレクト）が返った場合は index.php にアクセス
        if 300 <= res.status_code < 400:
            logger.info(f"リダイレクト({res.status_code})を検出しました。index.php にアクセスします...")
            # index.phpへのアクセスのためにRefererとURLを更新
            headers["Referer"] = url_menu
            session.get(url_init, headers=headers, timeout=10)
            
            # データ取得POSTに向けたRefererの更新
            headers["Referer"] = url_init
        else:
            logger.info(f"ステータスコード {res.status_code} を受信。セッション取得完了。")
            headers["Referer"] = url_menu
            
    except requests.exceptions.RequestException as e:
        logger.error(f"セッション取得に失敗しました: {e}")
        return [False, ""]

    # payloadはサニタイズ不要！そのままのダブルクォートで渡します
    JMA_STATION_NUM = os.environ.get("JMA_STATION_NUM")
    payload = {
        "stationNumList": f'["{JMA_STATION_NUM}"]',
        "aggrgPeriod": "9",
        "elementNumList": '[["201",""],["101",""],["401",""],["501",""],["301",""],["503",""],["610",""],["703",""],["601",""]]',
        "interAnnualType": "2",
        "ymdList": _build_ymd_list(),
        "optionNumList": '[]',
        "downloadFlag": "true",
        "rmkFlag": "0",
        "disconnectFlag": "0",
        "youbiFlag": "0",
        "fukenFlag": "0",
        "kijiFlag": "0",
        "csvFlag": "1",
        "jikantaiFlag": "0",
        "jikantaiList": '[1,24]',
        "ymdLiteral": "1"
    }

    logger.info("CSVデータをリクエストしています...")
    try:
        response = session.post(url_download, data=payload, headers=headers, timeout=20)
    except requests.exceptions.RequestException as e:
        logger.error(f"データのダウンロードリクエストに失敗しました: {e}")
        return [False, ""]
    
    today = datetime.now().strftime('%Y%m%d%H%M%S')

    save_dir = "temp"
    os.makedirs(save_dir, exist_ok=True)

    # レスポンスのチェック
    status_code = response.status_code
    content_type = response.headers.get("Content-Type", "")
    # 成功失敗の判定はContent-Typeがtext/htmlかどうかで判定
    [is_success, ext] = [False, "html"] if "text/html" in content_type else [True, "csv"]
    
    if is_success:
        logger.info("リクエスト成功")
        filepath = os.path.join(save_dir, f"pw_{today}.{ext}")
    else:
        logger.error("リクエスト失敗")
        filepath = os.path.join(save_dir, f"pw_{today}_error.{ext}")
    
    logger.error(f"Status: {status_code}. Content-Type: {content_type}")

    with open(filepath, "wb") as f:
        f.write(response.content)
    logger.info(f"レスポンスを '{filepath}' に保存しました。")
    return [is_success, filepath]

def convert_response(filepath_raw):
    """
    ダウンロードした生CSVデータを整形し、static/past_weather.csv として保存する。

    気象庁のCSVは cp932 エンコーディングで、先頭数行にメタ情報が含まれるため、
    skiprows で不要行を除去し、重複する列名（風向）を修正してから
    UTF-8 で static/ に再保存する。処理後、元ファイルは backup/ に移動される。

    Args:
        filepath_raw (str): ダウンロードした生CSVファイルのパス。
    """

    logger.info("レスポンスを使いやすい形に加工します。")

    # CSV変換処理
    try:
        df = pd.read_csv(
            filepath_raw,
            encoding='cp932', 
            skiprows=[0, 1, 2, 4]
        )

        cols = list(df.columns)
        if len(cols) > 6:
            cols[6] = '風向'
            df.columns = cols
            
        save_dir = "static"
        os.makedirs(save_dir, exist_ok=True)

        filepath_new = os.path.join(save_dir, "past_weather.csv")
        df.to_csv(filepath_new, index=False, encoding='utf-8')
        
        logger.info(f"CSVを整形し '{filepath_new}' に保存しました。")
        
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

    backup(filepath)