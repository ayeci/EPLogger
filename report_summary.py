"""
EPLogger CSV を読んでローカルLLMで要約し、TTS（VoiceVox）で音声化、
Google Home（Cast）で再生するパイプライン。
キャラクター設定は OLLAMA_MODEL のカスタムモデル側に持たせる。
"""
import csv
import os
import wave
import time
import logging
from pathlib import Path
from threading import Thread

import ollama
import requests
import pychromecast
from dotenv import load_dotenv
from flask import Flask, send_file


# ===== 設定読込 =====
load_dotenv()

GOOGLE_HOME_NAME = os.getenv("GOOGLE_HOME_NAME")
PC_IP = os.getenv("PC_IP")
SERVE_PORT = int(os.getenv("SERVE_PORT", "8765"))
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://localhost:50021")
SPEAKER_ID = int(os.getenv("SPEAKER_ID", "3"))
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# 相対パス（プロジェクトルートから）
PROJECT_ROOT = Path(__file__).parent
CSV_PATH = PROJECT_ROOT / "static" / "data.csv"

# 一時ファイル置き場（実行時に自動作成）
TEMP_DIR = PROJECT_ROOT / "temp"
TEMP_DIR.mkdir(exist_ok=True)
AUDIO_PATH = TEMP_DIR / "announce.wav"

# Flask のログ抑制
logging.getLogger("werkzeug").setLevel(logging.ERROR)


def load_latest_record():
    """EPLogger の CSV から最新行を dict で返す"""
    with open(CSV_PATH, "r", encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
        return rows[-1]


def _to_float(val):
    """空文字 / None を 0.0 に、それ以外を float に変換"""
    return float(val) if val else 0.0


def build_prompt(record):
    """CSV 1行から LLM 用プロンプトを組み立てる"""
    # 30分あたりの電力量 (kWh) を平均出力 (kW) に換算
    gen_kw = _to_float(record["発電電力量[kWh]"]) * 2
    use_kw = _to_float(record["消費電力量[kWh]"]) * 2
    charge_kw = _to_float(record["充電電力量[kWh]"]) * 2
    discharge_kw = _to_float(record["放電電力量[kWh]"]) * 2
    soc = record["蓄電残量(SOC)[%]"]

    return f"""今の発電状況を30字以内で要約して。

時刻: {record['年月日']} {record['時刻']}
発電中: {gen_kw:.1f} kW
消費中: {use_kw:.1f} kW
蓄電池残量: {soc}%
充電中: {charge_kw:.1f} kW
放電中: {discharge_kw:.1f} kW

ポジティブに、キャラクター口調で。
"""


def generate_summary(record):
    """LLM にサマリ生成を依頼"""
    client = ollama.Client(host=OLLAMA_HOST)
    response = client.generate(
        model=OLLAMA_MODEL,
        prompt=build_prompt(record),
        keep_alive=0,  # 即アンロードで VRAM 解放
    )
    return response["response"].strip()


def synthesize_voice(text):
    """TTS で WAV 生成"""
    query = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": SPEAKER_ID},
    ).json()
    audio = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": SPEAKER_ID},
        json=query,
    ).content
    AUDIO_PATH.write_bytes(audio)
    return len(audio)


def get_wav_duration(path):
    """WAV ファイルから再生時間（秒）を取得"""
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def serve_audio():
    """音声ファイル配信用 Flask サーバ"""
    app = Flask(__name__)

    @app.route("/announce.wav")
    def serve():
        return send_file(str(AUDIO_PATH), mimetype="audio/wav")

    app.run(host="0.0.0.0", port=SERVE_PORT, debug=False, use_reloader=False)


def play_on_cast_device():
    """Cast デバイスで音声を再生（WAV 長で待機）"""
    duration = get_wav_duration(AUDIO_PATH)
    print(f"  [WAV] {duration:.2f}秒")

    chromecasts, browser = pychromecast.get_listed_chromecasts(
        friendly_names=[GOOGLE_HOME_NAME]
    )
    if not chromecasts:
        print(f"  [Cast] '{GOOGLE_HOME_NAME}' が見つからない")
        return
    cast = chromecasts[0]
    cast.wait()
    print(f"  [Cast] 接続: {cast.cast_info.host}")

    audio_url = f"http://{PC_IP}:{SERVE_PORT}/announce.wav"
    mc = cast.media_controller
    mc.play_media(audio_url, "audio/wav")
    mc.block_until_active()
    print(f"  [Cast] 再生指示送信")

    time.sleep(duration + 1.5)
    print(f"  [Cast] 完了")

    pychromecast.discovery.stop_discovery(browser)


def main():
    print("[1/4] HTTPサーバ起動")
    Thread(target=serve_audio, daemon=True).start()
    time.sleep(1)

    print("[2/4] CSV 読込")
    record = load_latest_record()
    print(f"  最新: {record['年月日']} {record['時刻']}")
    print(f"  発電: {record['発電電力量[kWh]']} kWh / SOC: {record['蓄電残量(SOC)[%]']}%")

    print("[3/4] LLM で要約")
    summary = generate_summary(record)
    print(f"  → {summary}")

    print("[4/4] 音声化 & Cast 再生")
    size = synthesize_voice(summary)
    print(f"  WAV: {size} bytes")
    play_on_cast_device()

    print("\n✓ 完了")


if __name__ == "__main__":
    main()