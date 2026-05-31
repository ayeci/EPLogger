"""
api.py - JSONP形式で電力データをフロントエンドに提供するFlask Blueprint

このモジュールは `data.csv` と `status.json` の情報を読み込み、
Chart.js などのフロントエンドグラフ描画ライブラリが必要とする
成形済みJSONPデータを返却します。
"""

import os
import json
import logging
import math
from datetime import datetime, timedelta

import pandas as pd
from flask import Blueprint, request, current_app, Response

view_data_bp = Blueprint('view_data', __name__)

CSV_ENCODING = 'utf_8_sig'

# app.py と同じ定数を参照するか、ここで定義
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "static")
PUBLIC_CSV = os.path.join(PUBLIC_DIR, "data.csv")

AUTO_RELOAD_MS = 30 * 60 * 1000  # 30分（ミリ秒）

logger = logging.getLogger(__name__)

@view_data_bp.route('/api.py')
def view_data():
    """
    CSVから電力データを取得・成形し、JSONP形式（application/javascript）でレスポンスを返す。
    
    URLパラメータ:
        callback (str): JSONPのコールバック関数名。デフォルトは 'callback'。
        
    戻り値:
        Response: `callbackName({ ...データ... });` 形式のJavaScript文字列。
    """
    callback = request.args.get('callback', 'callback')

    if not os.path.exists(PUBLIC_CSV):
        # データがない場合は空のデータを返す
        data = {
            "labels": [], "datasets": [],
            "labels_24h": [], "datasets_24h": [],
            "daily_labels": [], "daily_datasets": [],
            "reload_ms": AUTO_RELOAD_MS
        }
        json_data = json.dumps(data)
        return Response(f"{callback}({json_data});", mimetype='application/javascript')

    df = pd.read_csv(PUBLIC_CSV, encoding=CSV_ENCODING)

    col_date = df.columns[0]
    col_time = df.columns[1]

    # --- since / until による日付絞り込み ---
    # yyyyMMdd 形式で受け取り、未指定の場合はその端を開放
    since_str = request.args.get('since')  # e.g. '20260101'
    until_str = request.args.get('until')  # e.g. '20260524'
    if since_str or until_str:
        df_dates = pd.to_datetime(df[col_date], format='%Y/%m/%d')
        mask = pd.Series(True, index=df.index)
        if since_str:
            mask &= df_dates >= pd.to_datetime(since_str, format='%Y%m%d')
        if until_str:
            mask &= df_dates <= pd.to_datetime(until_str, format='%Y%m%d')
        df = df[mask].reset_index(drop=True)

    def _fmt_label(date_str, time_str):
        """
        日付と時刻の文字列を 'MM/DD HH:MM' 形式のX軸ラベルに整形する。

        Args:
            date_str (str): 'YYYY/MM/DD' 形式の日付文字列。
            time_str (str): 'HH:MM' 形式の時刻文字列。

        Returns:
            str: 'MM/DD HH:MM' 形式のラベル文字列。
        """
        parts = str(date_str).split('/')
        mm_dd = '/'.join(parts[1:]) if len(parts) >= 3 else str(date_str)
        return f"{mm_dd} {time_str}"
    labels = [_fmt_label(d, t) for d, t in zip(df[col_date], df[col_time])]

    power_columns = df.columns[2:8].tolist()
    display_order = [0, 2, 4, 1, 3, 5]
    colors = {
        0: '#97c305', 2: '#c687d9', 4: '#0a87c9',
        1: '#f4b200', 3: '#ff6c00', 5: '#a77a00',
    }
    negate_indices = {1, 3, 5}

    def _to_values(series, negate=False):
        """
        pandas Series をChart.js用のリストに変換する。

        NaN値はNone（JSONのnull）に変換し、negate=Trueの場合は
        符号を反転させる（消費・買電・放電など負方向に描画する系列用）。

        Args:
            series (pd.Series): 変換対象のpandas Series。
            negate (bool): Trueの場合、数値の符号を反転する。

        Returns:
            list: float または None のリスト。
        """
        result = []
        for v in series:
            if pd.isna(v):
                result.append(None)
            else:
                result.append(-float(v) if negate else float(v))
        return result

    datasets = []
    for idx in display_order:
        col_name = power_columns[idx]
        negate = idx in negate_indices
        color = colors[idx]
        values = _to_values(df[col_name], negate)
        datasets.append({
            'label': col_name, 'data': values,
            'borderColor': color, 'backgroundColor': color + '33',
            'borderWidth': 1.5, 'pointRadius': 0, 'pointHitRadius': 10,
            'tension': 0.3, 'fill': False, 'spanGaps': True,
        })
        
    soc_col_name = "蓄電残量(SOC)[%]"
    if soc_col_name in df.columns:
        soc_values = _to_values(df[soc_col_name], False)
        datasets.append({
            'label': soc_col_name, 'data': soc_values,
            'borderColor': '#2ea851', 'backgroundColor': 'rgba(46, 168, 81, 0.2)',
            'borderWidth': 1.5, 'pointRadius': 0, 'pointHitRadius': 10,
            'tension': 0.3, 'fill': True, 'spanGaps': True,
            'yAxisID': 'y-axis-soc'
        })

    ROWS_24H = 48
    df_24h = df.tail(ROWS_24H)
    labels_24h = df_24h[col_time].astype(str).tolist()
    datasets_24h = []
    for idx in display_order:
        col_name = power_columns[idx]
        negate = idx in negate_indices
        color = colors[idx]
        values_24h = _to_values(df_24h[col_name], negate)
        datasets_24h.append({
            'label': col_name, 'data': values_24h,
            'borderColor': color, 'backgroundColor': color + '33',
            'borderWidth': 2, 'pointRadius': 2, 'pointHitRadius': 10,
            'tension': 0.3, 'fill': False, 'spanGaps': True,
        })
        
    if soc_col_name in df.columns:
        soc_values_24h = _to_values(df_24h[soc_col_name], False)
        datasets_24h.append({
            'label': soc_col_name, 'data': soc_values_24h,
            'borderColor': '#2ea851', 'backgroundColor': 'rgba(46, 168, 81, 0.2)',
            'borderWidth': 2, 'pointRadius': 2, 'pointHitRadius': 10,
            'tension': 0.3, 'fill': True, 'spanGaps': True,
            'yAxisID': 'y-axis-soc'
        })

    daily_cols = [power_columns[0], power_columns[2], power_columns[1], power_columns[3]]
    df_daily = df.groupby(col_date)[daily_cols].sum().tail(7)
    
    daily_labels = []
    for d in df_daily.index:
        parts = str(d).split('/')
        daily_labels.append('/'.join(parts[1:]) if len(parts) >= 3 else str(d))
        
    daily_datasets = [
        {'label': power_columns[0], 'data': df_daily[power_columns[0]].round(3).tolist(), 'backgroundColor': colors[0] + '80', 'borderColor': colors[0], 'borderWidth': 1},
        {'label': power_columns[1], 'data': df_daily[power_columns[1]].round(3).tolist(), 'backgroundColor': colors[1] + '80', 'borderColor': colors[1], 'borderWidth': 1},
        {'label': power_columns[2], 'data': df_daily[power_columns[2]].round(3).tolist(), 'backgroundColor': colors[2] + '80', 'borderColor': colors[2], 'borderWidth': 1},
        {'label': power_columns[3], 'data': df_daily[power_columns[3]].round(3).tolist(), 'backgroundColor': colors[3] + '80', 'borderColor': colors[3], 'borderWidth': 1},
    ]

    # --------------------------------------------------------------------------
    # 過去の天候データの集計 (past_weather.csv)
    # --------------------------------------------------------------------------
    daily_weather_summary = {}
    from flask import current_app
    weather_csv_path = os.path.join(current_app.root_path, 'static', 'past_weather.csv')
    logger.info(f"Looking for weather CSV at: {weather_csv_path}")
    if os.path.exists(weather_csv_path):
        try:
            df_w = pd.read_csv(weather_csv_path)
            logger.info(f"CSV Columns: {df_w.columns.tolist()}")
            if '年月日時' in df_w.columns:
                df_w['年月日時'] = pd.to_datetime(df_w['年月日時'])
                df_w['DateKey'] = df_w['年月日時'].dt.strftime('%m/%d')
                logger.info(f"Processed DateKeys: {df_w['DateKey'].unique()[:5]}")
                
                for col in ['気温(℃)', '降水量(mm)', '日照時間(時間)', '降雪(cm)', '積雪(cm)']:
                    if col not in df_w.columns:
                        df_w[col] = 0.0
                    else:
                        df_w[col] = pd.to_numeric(df_w[col], errors='coerce')
                
                df_w[['降水量(mm)', '日照時間(時間)', '降雪(cm)', '積雪(cm)', '気温(℃)']] = df_w[['降水量(mm)', '日照時間(時間)', '降雪(cm)', '積雪(cm)', '気温(℃)']].fillna(0)
                
                grouped = df_w.groupby('DateKey')
                
                for date_key, group in grouped:
                    sunshine_sum = group['日照時間(時間)'].sum()
                    rain_sum = group['降水量(mm)'].sum()
                    snow_sum = group['降雪(cm)'].sum() + group['積雪(cm)'].sum()
                    temp_max = group['気温(℃)'].max()
                    temp_min = group['気温(℃)'].min()
                    temp_avg = group['気温(℃)'].mean()
                    
                    weather_label = "気象データなし"
                    weather_icon = ""
                    if sunshine_sum >= 0.8:
                        weather_label = "快晴"
                        weather_icon = "☀️"
                    elif 0.6 <= sunshine_sum < 0.8:
                        weather_label = "晴れ"
                        weather_icon = "🌤️"
                    elif 0.1 <= sunshine_sum < 0.6:
                        weather_label = "曇り"
                        weather_icon = "☁️"
                    elif sunshine_sum < 0.1:
                        if snow_sum > 0:
                            weather_label = "雪"
                            weather_icon = "❄️"
                        elif rain_sum > 0:
                            weather_label = "雨"
                            weather_icon = "☔"
                        else:
                            weather_label = "曇り"
                            weather_icon = "☁️"
                            
                    t_max_str = f"{temp_max:.1f}" if not math.isnan(temp_max) else "--"
                    t_min_str = f"{temp_min:.1f}" if not math.isnan(temp_min) else "--"
                    t_avg_str = f"{temp_avg:.1f}" if not math.isnan(temp_avg) else "--"
                    
                    daily_weather_summary[date_key] = {
                        "sunshine": round(sunshine_sum, 1),
                        "temp_max": t_max_str,
                        "temp_min": t_min_str,
                        "temp_avg": t_avg_str,
                        "weather_label": weather_label,
                        "weather_icon": weather_icon
                    }
        except Exception as e:
            import traceback
            error_msg = f"Failed to process past_weather.csv: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            with open("past_weather_error.txt", "w", encoding="utf-8") as f:
                f.write(error_msg)
                
    # --------------------------------------------------------------------------
    # 過去の天候データの時間別集計 (24hグラフ用)
    # --------------------------------------------------------------------------
    hourly_weather_summary = {}
    if os.path.exists(weather_csv_path) and 'df_w' in locals():
        try:
            # df_w は既に日時パースやNaN埋め・数値変換などが適用済み
            for _, row in df_w.iterrows():
                dt = row['年月日時']
                if pd.isna(dt): continue
                
                # "MM/DD HH:00" 形式のキーを作成
                hour_key = dt.strftime('%m/%d %H:00')
                
                sunshine = row.get('日照時間(時間)', 0.0)
                rain = row.get('降水量(mm)', 0.0)
                snow = row.get('降雪(cm)', 0.0) + row.get('積雪(cm)', 0.0)
                
                weather_icon = ""
                # 1時間ごとの天候判定
                if sunshine >= 0.8:
                    weather_icon = "☀️"
                elif 0.6 <= sunshine < 0.8:
                    weather_icon = "🌤️"
                elif 0.1 <= sunshine < 0.6:
                    weather_icon = "☁️"
                elif sunshine < 0.1:
                    if snow > 0:
                        weather_icon = "❄️"
                    elif rain > 0:
                        weather_icon = "☔"
                    else:
                        weather_icon = "☁️"
                        
                hourly_weather_summary[hour_key] = weather_icon
                
        except Exception as e:
            import traceback
            logger.error(f"Failed to create hourly_weather_summary: {e}\n{traceback.format_exc()}")

    data = {
        "labels": labels, "datasets": datasets,
        "labels_24h": labels_24h, "datasets_24h": datasets_24h,
        "daily_labels": daily_labels, "daily_datasets": daily_datasets,
        "daily_weather_summary": daily_weather_summary,
        "hourly_weather_summary": hourly_weather_summary,
        "reload_ms": AUTO_RELOAD_MS,
        "debug_weather_path": weather_csv_path,
        "debug_weather_exists": os.path.exists(weather_csv_path)
    }

    # JSONシリアライズして JSONP 文字列として返す
    json_data = json.dumps(data)
    return Response(f"{callback}({json_data});", mimetype='application/javascript')


# --- 料金設定（東京電力 従量電灯B 50A 相当の標準値。実際の契約に合わせて調整可） ---
# 基本料金・段階単価・再エネ賦課金は変更があり得るため、ここで一元管理する
RATE_BASIC_MONTHLY = 1558.75      # 基本料金（円/月、50A）
RATE_TIER1_PRICE, RATE_TIER1_MAX = 29.80, 120   # 第1段階（〜120kWh/月）
RATE_TIER2_PRICE, RATE_TIER2_MAX = 36.40, 300   # 第2段階（120〜300kWh/月）
RATE_TIER3_PRICE = 40.49                          # 第3段階（300kWh/月超）
RATE_RENEWABLE_LEVY = 3.49        # 再エネ発電促進賦課金（円/kWh）

# 売電単価スケジュール（適用開始日つき）。
# 売電は受給開始日以降に発生し、単価は時期で変わり得るため、
# 「全期間一律」ではなく「適用開始日つきのテーブル」で管理する。
#
# 形式    : (適用開始日 'YYYY-MM-DD', 単価[円/kWh]) のリスト。最初の適用開始日より前は単価0（売電対象外）。
SELL_RATE_SCHEDULE = [
    ("2026-02-21", 24.00),
]

import bisect

# SELL_RATE_SCHEDULE を適用開始日昇順にソートし、二分探索用の配列を用意する
_SELL_SCHED = sorted(SELL_RATE_SCHEDULE, key=lambda x: x[0])
_SELL_SCHED_DATES = [d for d, _ in _SELL_SCHED]
_SELL_SCHED_RATES = [r for _, r in _SELL_SCHED]


def _sell_rate_for(date_iso):
    """
    指定日に適用される売電単価[円/kWh]を返す。

    Args:
        date_iso (str): 'YYYY-MM-DD' 形式の日付文字列。

    Returns:
        float: 適用単価。最初の適用開始日より前の日付は 0.0（売電対象外）。
    """
    i = bisect.bisect_right(_SELL_SCHED_DATES, date_iso) - 1
    if i < 0:
        return 0.0
    return _SELL_SCHED_RATES[i]


def _buy_energy_charge(kwh):
    """
    買電量[kWh]から従量料金（三段階）＋再エネ賦課金を計算する（基本料金は含まない）。

    Args:
        kwh (float): 月間の買電電力量[kWh]。

    Returns:
        float: 電力量料金＋再エネ賦課金の合計[円]。
    """
    if kwh <= RATE_TIER1_MAX:
        energy = kwh * RATE_TIER1_PRICE
    elif kwh <= RATE_TIER2_MAX:
        energy = (RATE_TIER1_MAX * RATE_TIER1_PRICE
                  + (kwh - RATE_TIER1_MAX) * RATE_TIER2_PRICE)
    else:
        energy = (RATE_TIER1_MAX * RATE_TIER1_PRICE
                  + (RATE_TIER2_MAX - RATE_TIER1_MAX) * RATE_TIER2_PRICE
                  + (kwh - RATE_TIER2_MAX) * RATE_TIER3_PRICE)
    return energy + kwh * RATE_RENEWABLE_LEVY


@view_data_bp.route('/api_analysis.py')
def view_analysis():
    """
    経済効果と蓄電池運用分析のデータを集計し、JSONP形式で返す。

    月次の電力収支・経済効果（実収支とシステム導入メリットの両建て）、
    および時間帯別の充放電・SOC推移を成形して返却する。

    URLパラメータ:
        callback (str): JSONPのコールバック関数名。デフォルトは 'callback'。

    戻り値:
        Response: `callbackName({ ...データ... });` 形式のJavaScript文字列。
    """
    callback = request.args.get('callback', 'callback')

    empty = {
        "monthly_labels": [], "econ_actual": [], "econ_merit": [],
        "self_sufficiency": [], "self_consumption": [],
        "hourly_labels": [], "hourly_charge": [], "hourly_discharge": [], "hourly_soc": [],
        "summary": {}, "rate": {}
    }

    if not os.path.exists(PUBLIC_CSV):
        return Response(f"{callback}({json.dumps(empty)});", mimetype='application/javascript')

    df = pd.read_csv(PUBLIC_CSV, encoding=CSV_ENCODING)
    col_date = df.columns[0]
    col_time = df.columns[1]
    power_columns = df.columns[2:8].tolist()  # 発電・消費・売電・買電・充電・放電

    # 数値化（カンマ区切り等の混入に備えてcoerce）
    for c in power_columns + ["蓄電残量(SOC)[%]"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 先頭行などの欠損は0で補完（売電・充電に初期欠損があるため）
    df[power_columns] = df[power_columns].fillna(0)

    col_gen, col_con, col_sel, col_buy, col_cha, col_dis = power_columns

    # --- 月次集計 ---
    dt = pd.to_datetime(df[col_date] + " " + df[col_time], format="%Y/%m/%d %H:%M")
    df["_month"] = dt.dt.strftime("%Y-%m")
    df["_hour"] = dt.dt.hour

    # 行ごとの日付（ISO形式）を用意し、その日に適用される単価で売電収入を算出
    iso_dates = dt.dt.strftime("%Y-%m-%d")
    df["_sell_income_row"] = [
        float(s) * _sell_rate_for(d)
        for s, d in zip(df[col_sel], iso_dates)
    ]

    grp = df.groupby("_month")
    monthly_labels, econ_actual, econ_merit = [], [], []
    self_sufficiency, self_consumption = [], []

    total_sell_income = 0.0
    total_buy_cost = 0.0
    total_merit = 0.0

    for month in sorted(grp.groups.keys()):
        g = grp.get_group(month)
        gen = float(g[col_gen].sum())
        con = float(g[col_con].sum())
        sel = float(g[col_sel].sum())
        buy = float(g[col_buy].sum())

        # 実際の収支：売電収入 − 買電支出（基本料金込み）
        # 売電収入は行ごとに適用単価を当てた _sell_income_row 列の月次合計を使う
        sell_income = float(g["_sell_income_row"].sum())
        buy_cost = _buy_energy_charge(buy) + RATE_BASIC_MONTHLY
        actual_balance = sell_income - buy_cost  # マイナス＝持ち出し

        # 導入メリット：システムが無く消費全量を買電した場合の料金 − 実質支出
        virtual_cost = _buy_energy_charge(con) + RATE_BASIC_MONTHLY
        actual_net_cost = buy_cost - sell_income   # 実質的な持ち出し額
        merit = virtual_cost - actual_net_cost

        monthly_labels.append(month)
        econ_actual.append(round(actual_balance))
        econ_merit.append(round(merit))
        self_sufficiency.append(round(gen / con * 100, 1) if con > 0 else 0)
        self_consumption.append(round((gen - sel) / gen * 100, 1) if gen > 0 else 0)

        total_sell_income += sell_income
        total_buy_cost += buy_cost
        total_merit += merit

    # --- 時間帯別の充放電・SOC ---
    hour_grp = df.groupby("_hour")
    hourly_labels = [f"{h}時" for h in range(24)]
    hourly_charge, hourly_discharge, hourly_soc = [], [], []
    for h in range(24):
        if h in hour_grp.groups:
            gh = hour_grp.get_group(h)
            hourly_charge.append(round(float(gh[col_cha].mean()), 3))
            hourly_discharge.append(round(float(gh[col_dis].mean()), 3))
            hourly_soc.append(round(float(gh["蓄電残量(SOC)[%]"].mean()), 1))
        else:
            hourly_charge.append(0)
            hourly_discharge.append(0)
            hourly_soc.append(None)

    # --- 蓄電池サマリー ---
    soc_daily = df.groupby(col_date)["蓄電残量(SOC)[%]"].agg(["min", "max"])
    full_days = int((soc_daily["max"] >= 95).sum())
    empty_days = int((soc_daily["min"] <= 5).sum())
    total_days = int(len(soc_daily))

    summary = {
        "sell_income": round(total_sell_income),
        "buy_cost": round(total_buy_cost),
        "actual_balance": round(total_sell_income - total_buy_cost),
        "merit_total": round(total_merit),
        "total_days": total_days,
        "full_days": full_days,
        "empty_days": empty_days,
        "soc_max_avg": round(float(soc_daily["max"].mean()), 1),
        "soc_min_avg": round(float(soc_daily["min"].mean()), 1),
    }

    rate = {
        "basic": RATE_BASIC_MONTHLY, "tier1": RATE_TIER1_PRICE,
        "tier2": RATE_TIER2_PRICE, "tier3": RATE_TIER3_PRICE,
        "levy": RATE_RENEWABLE_LEVY,
        "sell": _SELL_SCHED_RATES[-1] if _SELL_SCHED_RATES else 0,  # 代表値（最新単価）
        "sell_schedule": _SELL_SCHED,  # 参考: 適用開始日つき全単価
    }

    data = {
        "monthly_labels": monthly_labels,
        "econ_actual": econ_actual,
        "econ_merit": econ_merit,
        "self_sufficiency": self_sufficiency,
        "self_consumption": self_consumption,
        "hourly_labels": hourly_labels,
        "hourly_charge": hourly_charge,
        "hourly_discharge": hourly_discharge,
        "hourly_soc": hourly_soc,
        "summary": summary,
        "rate": rate,
    }
    return Response(f"{callback}({json.dumps(data)});", mimetype='application/javascript')
