/**
 * Chart.js の初期化およびデータフェッチを行うスクリプト
 *
 * HTML読み込み後に実行され、Chart.jsの共通オプション定義、
 * since/until パラメータ付きJSONPによるグラフデータ取得、
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

    // --- ユーティリティ ---

    /**
     * Date オブジェクトを 'yyyyMMdd' 形式の文字列に変換する
     * @param {Date} date
     * @returns {string}
     */
    function toYYYYMMDD(date) {
        return date.toISOString().slice(0, 10).replace(/-/g, '');
    }

    /**
     * data-range 属性値を since/until パラメータオブジェクトに変換する
     * @param {string} range - '30d' / '90d' / 'all' など
     * @returns {Object} - { since?: string, until?: string }
     */
    function rangeToParams(range) {
        if (range === 'all') return {};
        const days = parseInt(range, 10);
        if (!isNaN(days) && days > 0) {
            const since = new Date();
            since.setDate(since.getDate() - days);
            return { since: toYYYYMMDD(since) };
        }
        return {};
    }

    // --- JSONP フェッチ・キャッシュ ---

    /** since/until 文字列をキーにしたレスポンスキャッシュ */
    const chartDataCache = {};

    /**
     * since/until パラメータ付きで JSONP リクエストを発行し、結果をキャッシュする。
     * 同じパラメータのキャッシュが存在する場合は再フェッチしない。
     *
     * @param {Object} params - { since?: string, until?: string }
     * @param {Function} onComplete - レスポンスデータを受け取るコールバック
     */
    function fetchChartData(params, onComplete) {
        const qs = new URLSearchParams(params).toString();  // '' / 'since=yyyyMMdd' / etc.
        if (chartDataCache[qs]) {
            onComplete(chartDataCache[qs]);
            return;
        }
        const cbName = 'chartCallback_' + Date.now();
        window[cbName] = (data) => {
            chartDataCache[qs] = data;
            delete window[cbName];
            onComplete(data);
        };
        const sep = qs ? '&' + qs : '';
        const script = document.createElement('script');
        script.src = `/api.py?callback=${cbName}&t=${Date.now()}${sep}`;
        document.body.appendChild(script);
    }

    // --- Y 軸ヘルパー ---

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

    // --- powerChart の生成・破棄 ---

    let powerChartInstance = null;

    /**
     * powerChart を生成して返す。既存インスタンスがあれば先に破棄する。
     *
     * @param {Array} labels
     * @param {Array} datasets
     * @returns {Chart}
     */
    function createPowerChart(labels, datasets) {
        if (powerChartInstance) {
            powerChartInstance.destroy();
            powerChartInstance = null;
        }

        const opts = getCommonOptionsWithMaxY(datasets);
        opts.scales.x = {
            ...opts.scales.x,
            ticks: {
                ...opts.scales.x.ticks,
                callback: function (val, index) {
                    let labelStr = '';
                    if (labels && labels[index]) {
                        labelStr = String(labels[index]);
                    } else if (this.getLabelForValue) {
                        labelStr = String(this.getLabelForValue(val));
                    }
                    if (labelStr.endsWith(' 00:00') || labelStr.includes(' 00:00')) {
                        return null;
                    } else if (labelStr.endsWith(' 12:00') || labelStr.includes(' 12:00')) {
                        const parts = labelStr.split(' ');
                        const dateKey = parts[0];
                        if (window._dailyWeatherSummary && window._dailyWeatherSummary[dateKey]) {
                            const weather = window._dailyWeatherSummary[dateKey];
                            return [dateKey, `${weather.weather_icon} ${weather.sunshine}h`];
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

        powerChartInstance = new Chart(document.getElementById('powerChart'), {
            type: 'line',
            data: { labels, datasets },
            options: opts,
            plugins: [verticalLinePlugin]
        });
        return powerChartInstance;
    }

    // --- タブ切り替え ---

    document.querySelectorAll('#monitoringTabs .nav-link').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#monitoringTabs .nav-link')
                    .forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const params = rangeToParams(btn.dataset.range);
            fetchChartData(params, (data) => {
                // 取得したデータの daily_weather_summary をグローバルに上書きして
                // X軸ラベルコールバックから参照できるようにする
                window._dailyWeatherSummary = data.daily_weather_summary || {};
                createPowerChart(data.labels, data.datasets);
            });
        });
    });

    // --- 初期ロード ---

    /**
     * JSONP 取得後に全グラフを初期化するコールバック
     *
     * @param {Object} data - api.py から取得した描画用データ
     */
    window.initAllCharts = function (data) {
        // daily_weather_summary をグローバルに保持（powerChart の X 軸ラベルから参照）
        window._dailyWeatherSummary = data.daily_weather_summary || {};

        // --- 24時間グラフ ---
        const chart24hOptions = getCommonOptionsWithMaxY(data.datasets_24h);
        chart24hOptions.scales.x = {
            ...chart24hOptions.scales.x,
            ticks: {
                ...chart24hOptions.scales.x.ticks,
                callback: function (val, index) {
                    let timeLabel = data.labels_24h && data.labels_24h[index] !== undefined
                        ? String(data.labels_24h[index]) : null;
                    if (!timeLabel) return null;
                    if (data.hourly_weather_summary) {
                        const matchingKeys = Object.keys(data.hourly_weather_summary)
                            .filter(k => k.endsWith(' ' + timeLabel.replace(/30$/, '00')));
                        if (matchingKeys.length > 0) {
                            const icon = data.hourly_weather_summary[matchingKeys[matchingKeys.length - 1]];
                            return [timeLabel + ' ' + icon];
                        }
                    }
                    return timeLabel;
                }
            }
        };
        new Chart(document.getElementById('chart24h'), {
            type: 'line',
            data: { labels: data.labels_24h, datasets: data.datasets_24h },
            options: chart24hOptions
        });

        // --- 週間累計グラフ ---
        const dailyVerticalLinePlugin = {
            id: 'dailyVerticalLinePlugin',
            beforeDatasetsDraw: chart => {
                const ctx = chart.ctx;
                const xAxis = chart.scales.x;
                const yAxis = chart.scales.y;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.3)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]); // 点線の間隔

                // 棒グラフ（日付）の間に線を引く
                for (let i = 0; i < chart.data.labels.length - 1; i++) {
                    const xMid = (xAxis.getPixelForTick(i) + xAxis.getPixelForTick(i + 1)) / 2; // アイテムの中間点
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

        // --- powerChart（デフォルト: 30日間） ---
        createPowerChart(data.labels, data.datasets);

        // --- 自動リロード ---
        if (data.reload_ms) {
            setTimeout(() => location.reload(), data.reload_ms);
        }
    };

    // 初回フェッチ: 30日間
    fetchChartData(rangeToParams('30d'), window.initAllCharts);
});
