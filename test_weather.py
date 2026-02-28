import pandas as pd
import math
import os

weather_csv_path = 'static/past_weather.csv'
daily_weather_summary = {}

try:
    df_w = pd.read_csv(weather_csv_path)
    if '年月日時' in df_w.columns:
        df_w['年月日時'] = pd.to_datetime(df_w['年月日時'])
        # M/D 形式にする必要あり (データcsvの表示に合わせて 02/22 形式になっているか確認)
        # view_data 側では %m/%d にしている。
        df_w['DateKey'] = df_w['年月日時'].dt.strftime('%m/%d')
        
        for col in ['気温(℃)', '降水量(mm)', '日照時間(時間)', '降雪(cm)', '積雪(cm)']:
            if col not in df_w.columns:
                df_w[col] = 0.0
        
        # '降雪(cm)' などに '--' や空白があるかもしれないので to_numeric で変換
        for col in ['降水量(mm)', '日照時間(時間)', '降雪(cm)', '積雪(cm)', '気温(℃)']:
            df_w[col] = pd.to_numeric(df_w[col], errors='coerce').fillna(0)
            
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
            elif 0.4 <= sunshine_sum < 0.8:
                weather_label = "晴れ"
                weather_icon = "🌤️"
            elif 0.1 <= sunshine_sum < 0.4:
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
    print(f"Failed to process past_weather.csv: {e}")

print("---")
print(daily_weather_summary.keys())
