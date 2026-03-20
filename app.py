"""
app.py - 太陽光発電電力監視データをグラフとテーブルで表示するFlaskウェブサーバ

使い方:
    python app.py

LAN内の端末のブラウザから http://<IPアドレス>:5000/ でアクセス可能。
画面は30分毎に自動リロードされる。
"""

import os
import psutil
import json
import logging
import requests
from datetime import datetime, timedelta

import pandas as pd
from flask import Flask, render_template
from dotenv import load_dotenv

from api import view_data_bp

load_dotenv()

# Flaskアプリの初期化
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=PUBLIC_DIR)
app.register_blueprint(view_data_bp)

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# --- 定数 ---
CSV_ENCODING = 'utf_8_sig'  # BOM付UTF-8で統一

PUBLIC_CSV = os.path.join(PUBLIC_DIR, "data.csv")
STATUS_JSON = os.path.join(PUBLIC_DIR, "status.json")

AUTO_RELOAD_MS = 30 * 60 * 1000  # 30分（ミリ秒）

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

# --- 天気取得 ---
def get_yahoo_weather():
    """
    Yahoo!気象情報APIから直近の降水予測を取得する。

    .envファイルの COORDINATES（緯度経度）と APP_ID（Yahoo APIキー）を使用して
    Yahoo!天気・災害APIにリクエストを送信し、直近2時間分の降水量データを
    10分間隔で取得する。

    Returns:
        list[dict]: 降水予測リスト。各要素は以下のキーを持つ辞書:
            - time (str): 'HH:MM' 形式の時刻ラベル
            - rainfall (float): 降水量 (mm/h)
            - is_rain (bool): 降水ありの場合True
            - type (str): 'observation'（実測）または 'forecast'（予測）
            APIエラーまたは設定不備の場合は空リストを返す。
    """
    log_with_memory("--- Yahoo天気API取得開始 ---")

    coordinates = os.environ.get("COORDINATES")
    app_id = os.environ.get("APP_ID")
    
    if not coordinates or not app_id:
        logger.warning("COORDINATES または APP_ID が.envに設定されていません")
        return []

    url = f"https://map.yahooapis.jp/weather/V1/place?output=json&past=2&coordinates={coordinates}&appid={app_id}"
    
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        
        weather_list = []
        if "Feature" in data and len(data["Feature"]) > 0:
            weathers = data["Feature"][0].get("Property", {}).get("WeatherList", {}).get("Weather", [])
            for w in weathers:
                # Type が observation (実測) と forecast (予測) があるが、配列の順序通り表示する
                dt_str = w.get("Date", "")
                rain = w.get("Rainfall", 0.0)
                
                # '202602270830' -> '08:30' などのフォーマット変換
                if len(dt_str) >= 12:
                    time_label = f"{dt_str[8:10]}:{dt_str[10:12]}"
                else:
                    time_label = dt_str

                weather_list.append({
                    "time": time_label,
                    "rainfall": rain,
                    "is_rain": rain > 0,
                    "type": w.get("Type", "unknown")  # observation / forecast
                })

        log_with_memory("--- Yahoo天気API取得完了 ---")
        return weather_list

    except Exception as e:
        logger.error("Yahoo天気APIの取得に失敗: %s", e)
        return []

def get_jma_weather():
    """
    気象庁APIから指定地域の週間天気予報と気温を取得する。

    気象庁の防災情報データベースAPI（.envに定義した{JMA_AREA_CODE}）から週間予報データを取得し、
    天気コードに基づいたBootstrapアイコンクラスと配色、
    および最低気温・最高気温を含むリストを返す。

    Returns:
        list[dict]: 週間天気リスト。各要素は以下のキーを持つ辞書:
            - day (str): '今日 (MM/DD)' / '明日 (MM/DD)' / '曜日 (MM/DD)'
            - icon (str): Bootstrap Icons クラス名（例: 'bi-sun-fill'）
            - color (str): Bootstrap テキストカラークラス（例: 'text-warning'）
            - temp_min (str): 最低気温（℃）または '--'
            - temp_max (str): 最高気温（℃）または '--'
            APIエラー時は空リストを返す。
    """

    log_with_memory("--- 気象庁天気API取得開始 ---")

    # 気象庁の地域コード（.envから取得）
    JMA_AREA_CODE0 = os.environ.get('JMA_AREA_CODE0')
    JMA_AREA_CODE1 = os.environ.get('JMA_AREA_CODE1')
    JMA_AREA_CODE2 = os.environ.get('JMA_AREA_CODE2')

    url = f"https://www.jma.go.jp/bosai/forecast/data/forecast/{JMA_AREA_CODE0}.json"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        
        # 週間予報は data[1] の timeSeries に格納されている
        if len(data) < 2:
            return []
            
        weekly_data = data[1].get("timeSeries", [])
        if len(weekly_data) < 2:
            return []
            
        # timeSeries[0]: 天気情報
        weather_area = None
        for a in weekly_data[0].get("areas", []):
            if a.get("area", {}).get("code") == JMA_AREA_CODE1:
                weather_area = a
                break
                
        # timeSeries[1]: 気温情報
        temp_area = None
        for a in weekly_data[1].get("areas", []):
            if a.get("area", {}).get("code") == JMA_AREA_CODE2:
                temp_area = a
                break
                
        if not weather_area or not temp_area:
            return []
            
        time_defines = weekly_data[0].get("timeDefines", [])
        weather_codes = weather_area.get("weatherCodes", [])
        
        temps_min = temp_area.get("tempsMin", [])
        temps_max = temp_area.get("tempsMax", [])
        
        jma_list = []
        for i in range(len(time_defines)):
            dt = datetime.fromisoformat(time_defines[i])
            
            # 今日・明日の表記
            today = datetime.now()
            days_diff = (dt.date() - today.date()).days
            if days_diff == 0:
                day_str = "今日"
            elif days_diff == 1:
                day_str = "明日"
            else:
                weekdays = ["月", "火", "水", "木", "金", "土", "日"]
                day_str = weekdays[dt.weekday()]

            code = weather_codes[i] if i < len(weather_codes) else ""
            main_code = code[0] if code else "2"
            
            if main_code == "1":
                icon = "bi-sun-fill"
                color = "text-warning"
            elif main_code == "3":
                icon = "bi-cloud-rain-fill"
                color = "text-primary"
            elif main_code == "4":
                icon = "bi-snow"
                color = "text-info"
            else:
                icon = "bi-cloud-fill"
                color = "text-secondary"

            t_min = temps_min[i] if i < len(temps_min) and temps_min[i] else "--"
            t_max = temps_max[i] if i < len(temps_max) and temps_max[i] else "--"

            jma_list.append({
                "day": f"{day_str} ({dt.strftime('%m/%d')})",
                "icon": icon,
                "color": color,
                "temp_min": t_min,
                "temp_max": t_max
            })
            
        log_with_memory("--- 気象庁天気API取得完了 ---")
        return jma_list

    except Exception as e:
        logger.error("気象庁APIの取得に失敗: %s", e)
        return []

# ========================================
# Flask ルート: トップ画面（グラフ＋テーブル表示）
# ========================================
@app.route('/')
def index():
    """
    メインダッシュボード画面のHTMLをレンダリングする。
    
    `data.csv` を読み込んで本日の電力量累計を計算し、`index.html` に変数を渡す。
    グラフ描画用の詳細データセットは `api.py` のエンドポイントから
    非同期（JSONP）で取得されるため、ここでは最低限のステータス情報のみを計算する。
    
    戻り値:
        str: レンダリングされたHTML文字列
    """


    current_month = datetime.now().strftime('%Y-%m')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not os.path.exists(PUBLIC_CSV):
        return f"<html><body><h2>電力監視データ</h2><p>データがありません。先に scraper.py を実行してください。</p><p>最終表示: {now_str}</p></body></html>", 404

    df = pd.read_csv(PUBLIC_CSV, encoding=CSV_ENCODING)

    # --- 基本情報の取得 ---
    col_date = df.columns[0]  # "年月日"
    col_time = df.columns[1]  # "時刻"
    last_date = str(df[col_date].iloc[-1])  # "2026/02/26"

    # --- ステータス情報の取得 ---
    last_update_str = ""
    next_reload_str = ""
    battery_status = "不明"
    battery_charge = "--"

    # --- バッテリーアイコン表示用変数の初期化 ---
    bat_status_color = "text-secondary"
    bat_status_icon = ""
    bat_status_overlay = ""
    bat_charge_color = "text-primary"
    bat_charge_icon = "bi-battery"

    if os.path.exists(STATUS_JSON):
        try:
            with open(STATUS_JSON, 'r', encoding='utf-8') as f:
                status_data = json.load(f)
            upd_iso = status_data.get("updated", "")
            nxt_iso = status_data.get("next_update", "")
            battery_status = status_data.get("battery_status", "不明")
            battery_charge = status_data.get("battery_charge", "--")
            
            # 充放電のアイコン・色判定
            if "充電" in battery_status:
                bat_status_color = "text-char"
                bat_status_icon = "bolt"
            elif "放電" in battery_status:
                bat_status_color = "text-dis"
                bat_status_icon = "offline_bolt"
                
            # 電池残量のパーセントアイコン・色判定 (Material Symbols)
            if battery_charge.endswith("%"):
                try:
                    pct = int(battery_charge.replace("%", "").strip())
                    if pct > 90:
                        bat_charge_icon = "battery_full"
                    elif pct > 75:
                        bat_charge_icon = "battery_6_bar"
                    elif pct > 60:
                        bat_charge_icon = "battery_5_bar"
                    elif pct > 45:
                        bat_charge_icon = "battery_4_bar"
                    elif pct > 30:
                        bat_charge_icon = "battery_3_bar"
                    elif pct > 15:
                        bat_charge_icon = "battery_2_bar"
                    elif pct > 0:
                        bat_charge_icon = "battery_1_bar"
                    else:
                        bat_charge_icon = "battery_0_bar"
                        bat_charge_color = "text-danger"
                except ValueError:
                    pass
            
            if upd_iso:
                dt_upd = datetime.fromisoformat(upd_iso.replace('Z', ''))
                last_update_str = dt_upd.strftime('%Y-%m-%d %H:%M')
            if nxt_iso:
                dt_nxt = datetime.fromisoformat(nxt_iso.replace('Z', ''))
                next_reload_str = dt_nxt.strftime('%Y-%m-%d %H:%M')
        except Exception as e:
            logger.error("status.json の読み込みに失敗: %s", e)

    if not last_update_str:
        last_time = str(df[col_time].iloc[-1])
        last_dt = datetime.strptime(f"{last_date} {last_time}", '%Y/%m/%d %H:%M')
        next_reload_dt = last_dt + timedelta(minutes=61)
        last_update_str = last_dt.strftime('%Y-%m-%d %H:%M')
        next_reload_str = next_reload_dt.strftime('%Y-%m-%d %H:%M')

    # --- 天気予報の取得 ---
    weather_data = get_yahoo_weather()
    jma_weather = get_jma_weather()

    # --- 本日累計（発電・消費・売電・買電） ---
    power_columns = df.columns[2:8].tolist()
    daily_cols = [power_columns[0], power_columns[2], power_columns[1], power_columns[3]]
    df_today = df[df[col_date] == last_date]
    today_sums = df_today[daily_cols].sum()
    today_data = {
        "gen": round(today_sums[power_columns[0]], 3),   # 発電
        "con": round(today_sums[power_columns[1]], 3),   # 消費
        "sel": round(today_sums[power_columns[2]], 3),   # 売電
        "buy": round(today_sums[power_columns[3]], 3),   # 買電
    }

    html_table = df.to_html(classes='table table-sm table-striped', index=False)

    return render_template('index.html', 
         table=html_table, row_count=len(df),
         last_update=last_update_str, next_reload=next_reload_str,
         battery_status=battery_status, battery_charge=battery_charge,
         bat_status_color=bat_status_color, bat_status_icon=bat_status_icon,
         bat_charge_color=bat_charge_color, bat_charge_icon=bat_charge_icon,
         today=today_data, weather_data=weather_data, jma_weather=jma_weather)


# ========================================
# エントリポイント
# ========================================
if __name__ == '__main__':
    # LAN内の端末からアクセスできるように 0.0.0.0 でバインド
    app.run(host='0.0.0.0', port=5000)
