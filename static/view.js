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

    // --- 分析（経済効果・蓄電池運用）データの取得と描画 ---

    /**
     * 数値を「12,345」形式のカンマ区切り文字列に整形する。
     * @param {number} n
     * @returns {string}
     */
    function formatYen(n) {
        return (n < 0 ? '−' : '') + Math.abs(n).toLocaleString('ja-JP');
    }

    /**
     * api_analysis.py から分析データを JSONP で取得する。
     * @param {Function} onComplete - レスポンスデータを受け取るコールバック
     */
    function fetchAnalysisData(onComplete) {
        const cbName = 'analysisCallback_' + Date.now();
        window[cbName] = (data) => {
            delete window[cbName];
            onComplete(data);
        };
        const script = document.createElement('script');
        script.src = `/api_analysis.py?callback=${cbName}&t=${Date.now()}`;
        document.body.appendChild(script);
    }

    /**
     * 分析セクション（サマリー統計・経済効果グラフ・蓄電池グラフ）を描画する。
     * @param {Object} data - api_analysis.py から取得したデータ
     */
    function initAnalysisCharts(data) {
        // --- サマリー統計の反映 ---
        const s = data.summary || {};
        const setStat = (id, valueHtml) => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = valueHtml;
        };
        setStat('sumSellIncome', `${formatYen(s.sell_income)}<span class="stat-unit">円</span>`);
        setStat('sumBuyCost', `${formatYen(s.buy_cost)}<span class="stat-unit">円</span>`);
        setStat('sumMerit', `${formatYen(s.merit_total)}<span class="stat-unit">円</span>`);
        setStat('sumBattery',
            `${s.full_days} / ${s.empty_days}<span class="stat-unit">日（満充電 / 完全放電・全${s.total_days}日）</span>`);

        // 料金条件の注記
        const r = data.rate || {};
        const note = document.getElementById('rateNote');
        if (note && r.sell !== undefined) {
            note.textContent =
                `※ 試算条件: 買電 従量電灯B相当（基本料金${r.basic}円/月＋段階単価＋再エネ賦課金${r.levy}円/kWh）、`
                + `売電${r.sell}円/kWh。実収支＝売電収入−買電支出、導入メリット＝全量買電した場合との差額。`;
        }

        // --- 経済効果グラフ（棒：実収支 / 棒：導入メリット） ---
        const econColors = {
            actual: getCss('--color-sel') || '#c687d9',
            merit: getCss('--color-gen') || '#97c305',
        };
        new Chart(document.getElementById('econChart'), {
            type: 'bar',
            data: {
                labels: data.monthly_labels,
                datasets: [
                    {
                        label: '実収支（売電−買電）', data: data.econ_actual,
                        backgroundColor: econColors.actual + '99', borderColor: econColors.actual,
                        borderWidth: 1,
                    },
                    {
                        label: '導入メリット（全量買電との差額）', data: data.econ_merit,
                        backgroundColor: econColors.merit + '99', borderColor: econColors.merit,
                        borderWidth: 1,
                    },
                ]
            },
            options: {
                ...commonOpts,
                plugins: {
                    ...commonOpts.plugins,
                    datalabels: {
                        display: true, anchor: 'end', align: 'end',
                        font: { family: 'Outfit', size: 9, weight: 'bold' },
                        formatter: (v) => (v || v === 0) ? v.toLocaleString('ja-JP') : ''
                    },
                    tooltip: {
                        ...commonOpts.plugins.tooltip,
                        callbacks: {
                            label: (ctx) => ctx.dataset.label + ': '
                                + Math.round(ctx.parsed.y).toLocaleString('ja-JP') + ' 円'
                        }
                    }
                },
                clip: false,
                scales: {
                    ...commonOpts.scales,
                    y: {
                        ...commonOpts.scales.y,
                        ticks: {
                            ...commonOpts.scales.y.ticks,
                            callback: (v) => v.toLocaleString('ja-JP')
                        }
                    },
                    'y-axis-soc': { display: false }
                }
            }
        });

        // --- 蓄電池グラフ（棒：充放電平均 / 折れ線：SOC平均、SOCは右軸） ---
        const batChargeColor = getCss('--color-char') || '#0a87c9';
        const batDisColor = getCss('--color-dis') || '#a77a00';
        const batSocColor = getCss('--color-bat') || '#34aa55';
        new Chart(document.getElementById('batteryChart'), {
            type: 'bar',
            data: {
                labels: data.hourly_labels,
                datasets: [
                    {
                        type: 'bar', label: '充電（平均）', data: data.hourly_charge,
                        backgroundColor: batChargeColor + '99', borderColor: batChargeColor,
                        borderWidth: 1, order: 2,
                    },
                    {
                        type: 'bar', label: '放電（平均）', data: data.hourly_discharge,
                        backgroundColor: batDisColor + '99', borderColor: batDisColor,
                        borderWidth: 1, order: 2,
                    },
                    {
                        type: 'line', label: 'SOC（平均）', data: data.hourly_soc,
                        borderColor: batSocColor, backgroundColor: 'rgba(52,170,85,0.15)',
                        borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true,
                        yAxisID: 'y-axis-soc', order: 1, spanGaps: true,
                    },
                ]
            },
            options: {
                ...commonOpts,
                plugins: {
                    ...commonOpts.plugins,
                    datalabels: { display: false },
                    tooltip: {
                        ...commonOpts.plugins.tooltip,
                        callbacks: {
                            label: (ctx) => {
                                if (ctx.dataset.label.includes('SOC')) {
                                    return ctx.dataset.label + ': ' + ctx.parsed.y + ' %';
                                }
                                return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(3) + ' kWh';
                            }
                        }
                    }
                },
                scales: {
                    ...commonOpts.scales,
                    x: { ...commonOpts.scales.x, grid: { display: false } },
                    y: {
                        ...commonOpts.scales.y, min: 0,
                        ticks: { ...commonOpts.scales.y.ticks, callback: (v) => v.toFixed(2) }
                    },
                    'y-axis-soc': {
                        ...commonOpts.scales['y-axis-soc'],
                        min: 0, max: 100,
                    }
                }
            }
        });
    }

    /**
     * CSS変数の値を取得する（系統色をJSと共有するため）。
     * @param {string} varName - 例: '--color-gen'
     * @returns {string} トリム済みの値。未定義なら空文字。
     */
    function getCss(varName) {
        return getComputedStyle(document.documentElement)
            .getPropertyValue(varName).trim();
    }

    // 分析データの取得・描画（初回のみ）
    fetchAnalysisData(initAnalysisCharts);

    // 初回フェッチ: 30日間
    fetchChartData(rangeToParams('30d'), window.initAllCharts);
});
