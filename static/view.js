/**
 * Chart.js の初期化およびデータフェッチを行うスクリプト
 * 
 * HTML読み込み後に実行され、Chart.jsの共通オプション定義、
 * JSONPによるグラフデータ取得、取得後コールバックによる
 * グラフ描画およびページ自動リロードのタイマー設定を行います。
 */
document.addEventListener("DOMContentLoaded", () => {
    // datalabels プラグインの登録
    Chart.register(ChartDataLabels);

    /**
     * 各種グラフ（線グラフ・棒グラフ）で共有する Chart.js の共通オプション
     */
    const commonOpts = {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
            padding: { left: 20, right: 30, top: 10, bottom: 10 }
        },
        plugins: {
            legend: {
                position: 'top',
                labels: { usePointStyle: true, font: { family: 'Outfit', size: 12, weight: 'bold' } }
            },
            tooltip: {
                backgroundColor: 'white', titleColor: '#333', bodyColor: '#333',
                borderColor: '#ddd', borderWidth: 1, padding: 10,
                callbacks: {
                    label: (ctx) => {
                        const label = ctx.dataset.label;
                        if (label && label.includes('SOC')) {
                            return label + ': ' + ctx.parsed.y + ' %';
                        }
                        return label + ': ' + Math.abs(ctx.parsed.y).toFixed(3) + ' kWh';
                    }
                }
            },
            datalabels: { display: false }
        },
        scales: {
            x: { offset: true, grid: { display: false }, ticks: { font: { family: 'Outfit', size: 10 } } },
            y: { grid: { color: '#f0f0f0' }, ticks: { font: { family: 'Outfit' }, callback: (v) => Math.abs(v).toFixed(1) } },
            'y-axis-soc': {
                position: 'right',
                min: 0,
                max: 100,
                grid: { display: false },
                ticks: {
                    font: { family: 'Outfit' },
                    callback: (v) => v < 0 ? '' : v + '%'
                }
            }
        }
    };

    /**
     * JSONPリクエスト成功時に実行されるグローバルコールバック関数
     * 
     * @param {Object} data - api.py から取得した描画用データ
     * @param {Array} data.labels_24h - 直近24時間グラフのX軸ラベル
     * @param {Array} data.datasets_24h - 直近24時間グラフのデータセット
     * @param {Array} data.labels - 全期間グラフのX軸ラベル
     * @param {Array} data.datasets - 全期間グラフのデータセット
     * @param {Array} data.daily_labels - 週間グラフのX軸ラベル
     * @param {Array} data.daily_datasets - 週間グラフのデータセット
     * @param {number} data.reload_ms - 自動リロードまでの待機時間（ミリ秒）
     */
    window.loadChartData = function (data) {
        // --- Y軸の最大値を計算して左右の軸の0〜最大値の高さを揃えるヘルパー ---
        function getCommonOptionsWithMaxY(datasets) {
            let maxY = 0;
            let minY = 0;
            datasets.forEach(ds => {
                if (ds.yAxisID !== 'y-axis-soc' && ds.data) {
                    const validData = ds.data.filter(v => v !== null);
                    if (validData.length > 0) {
                        const dsMax = Math.max(...validData);
                        const dsMin = Math.min(...validData);
                        if (dsMax > maxY) maxY = dsMax;
                        if (dsMin < minY) minY = dsMin;
                    }
                }
            });
            // 余裕を持たせる（例: 上下10%）
            const suggestedMax = maxY > 0 ? maxY * 1.1 : 5;
            const suggestedMin = minY < 0 ? minY * 1.1 : 0;

            // 左側のY軸と右側(SOC)のY軸で「0」のライン高さを一致させる
            // SOCのmaxを100に固定する場合、SOC側のminは左軸の(min/max)*100になる
            const socMin = suggestedMax > 0 ? 100 * (suggestedMin / suggestedMax) : 0;

            // commonOptsをディープコピーして一部上書き
            return {
                ...commonOpts,
                scales: {
                    ...commonOpts.scales,
                    y: {
                        ...commonOpts.scales.y,
                        min: suggestedMin,
                        max: suggestedMax
                    },
                    'y-axis-soc': {
                        ...commonOpts.scales['y-axis-soc'],
                        min: socMin,
                        max: 100
                    }
                }
            };
        }

        // 24時間グラフ用のオプション
        const chart24hOptions = getCommonOptionsWithMaxY(data.datasets_24h);
        chart24hOptions.scales.x = {
            ...chart24hOptions.scales.x,
            ticks: {
                ...chart24hOptions.scales.x.ticks,
                callback: function (val, index) {
                    let timeLabel = data.labels_24h && data.labels_24h[index] !== undefined
                        ? String(data.labels_24h[index])
                        : null;
                    if (!timeLabel) return null;

                    // 時間が 00 分の場合のみアイコンを表示するか検討
                    if (!!data.hourly_weather_summary) {
                        const matchingKeys = Object.keys(data.hourly_weather_summary).filter(k => k.endsWith(' ' + timeLabel.replace(/30$/, "00")));
                        if (matchingKeys.length > 0) {
                            // 最新のもの（配列の最後）を取得
                            const icon = data.hourly_weather_summary[matchingKeys[matchingKeys.length - 1]];
                            return [timeLabel + ' ' + icon];
                        }
                    }
                    return timeLabel;
                }
            }
        };

        // --- 直近24時間グラフの描画 ---
        new Chart(document.getElementById('chart24h'), {
            type: 'line',
            data: { labels: data.labels_24h, datasets: data.datasets_24h },
            options: chart24hOptions
        });

        // --- 全期間グラフの描画（X軸のラベルを間引く） ---
        const powerChartOptions = getCommonOptionsWithMaxY(data.datasets);
        powerChartOptions.scales.x = {
            ...powerChartOptions.scales.x,
            ticks: {
                ...powerChartOptions.scales.x.ticks,
                callback: function (val, index) {
                    // val は内部のピクセルや数値メタデータ、indexが純粋な配列のインデックスになることが多いです
                    let labelStr = "";
                    if (data.labels && data.labels[index]) {
                        labelStr = String(data.labels[index]);
                    } else if (this.getLabelForValue) {
                        labelStr = String(this.getLabelForValue(val));
                    }

                    if (labelStr && (labelStr.endsWith(' 00:00') || labelStr.includes(' 00:00'))) {
                        // 00:00 の時は縦線があるのでラベルは非表示
                        return null;
                    } else if (labelStr && (labelStr.endsWith(' 12:00') || labelStr.includes(' 12:00'))) {
                        // 12:00 の時は "MM/DD" に加えて気象情報を複数行で表示
                        const parts = labelStr.split(' ');
                        const dateKey = parts[0];
                        if (data.daily_weather_summary && data.daily_weather_summary[dateKey]) {
                            const weather = data.daily_weather_summary[dateKey];
                            return [
                                dateKey,
                                `${weather.weather_icon} ${weather.sunshine}h`
                            ];
                        }
                        return dateKey || labelStr;
                    }
                    return null;
                },
                maxRotation: 45,
                minRotation: 45
            }
        };

        // --- "00:00" の位置に縦線を引くためのカスタムプラグイン ---
        const verticalLinePlugin = {
            id: 'verticalLinePlugin',
            beforeDatasetsDraw: chart => {
                const ctx = chart.ctx;
                const xAxis = chart.scales.x;
                const yAxis = chart.scales.y;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.3)'; // 少し濃い目の半透明グレー
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]); // 点線の間隔

                chart.data.labels.forEach((label, index) => {
                    const labelStr = String(label);
                    // " 00:00" で終わるか、"00:00" を含むかチェック
                    if (labelStr.endsWith(' 00:00') || labelStr.includes(' 00:00')) {
                        // getPixelForTickはラベルが間引かれていると無効なため、getPixelForValue(インデックス)を使う
                        const x = xAxis.getPixelForValue(index);
                        ctx.moveTo(x, yAxis.top);
                        ctx.lineTo(x, yAxis.bottom);
                    }
                });
                ctx.stroke();
                ctx.restore();
            }
        };

        new Chart(document.getElementById('powerChart'), {
            type: 'line',
            data: { labels: data.labels, datasets: data.datasets },
            options: powerChartOptions,
            plugins: [verticalLinePlugin]
        });

        // --- 週間累計グラフの日付の間に縦線を引くプラグイン ---
        const dailyVerticalLinePlugin = {
            id: 'dailyVerticalLinePlugin',
            beforeDatasetsDraw: chart => {
                const ctx = chart.ctx;
                const xAxis = chart.scales.x;
                const yAxis = chart.scales.y;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.3)'; // 少し濃い目の半透明グレー
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]); // 点線の間隔

                // 棒グラフ（日付）の間に線を引く
                for (let i = 0; i < chart.data.labels.length - 1; i++) {
                    const xCurrent = xAxis.getPixelForTick(i);
                    const xNext = xAxis.getPixelForTick(i + 1);
                    const xMid = (xCurrent + xNext) / 2; // アイテムの中間点

                    ctx.moveTo(xMid, yAxis.top);
                    ctx.lineTo(xMid, yAxis.bottom);
                }
                ctx.stroke();
                ctx.restore();
            }
        };

        // --- 週間累計グラフの描画 ---
        new Chart(document.getElementById('dailyChart'), {
            type: 'bar',
            data: { labels: data.daily_labels, datasets: data.daily_datasets },
            options: {
                ...commonOpts,
                plugins: {
                    ...commonOpts.plugins,
                    datalabels: {
                        display: true, anchor: 'end', align: 'end',
                        font: { family: 'Outfit', size: 10, weight: 'bold' },
                        formatter: (v) => v ? v.toFixed(1) : ''
                    }
                },
                clip: false,
                scales: {
                    ...commonOpts.scales,
                    x: {
                        ...commonOpts.scales.x,
                        offset: true,
                        ticks: {
                            callback: function (val, index) {
                                // data.daily_labels には "MM/DD" 形式で入っている
                                const dateKey = data.daily_labels[index];
                                if (data.daily_weather_summary && data.daily_weather_summary[dateKey]) {
                                    const weather = data.daily_weather_summary[dateKey];
                                    return [
                                        dateKey,
                                        `${weather.weather_icon} ${weather.sunshine}h`
                                    ];
                                }
                                return dateKey;
                            }
                        }
                    }
                }
            },
            plugins: [dailyVerticalLinePlugin]
        });

        // --- 自動リロードのタイマー設定 ---
        if (data.reload_ms) {
            setTimeout(() => location.reload(), data.reload_ms);
        }
    };

    // --- JSONP リクエストの発行 ---
    // scriptタグを動的に生成し、APIエンドポイントへアクセスする
    const script = document.createElement('script');
    // キャッシュを回避するためにタイムスタンプを付与（クエリストリング t）
    script.src = `/api.py?callback=loadChartData&t=${new Date().getTime()}`;
    document.body.appendChild(script);
});
