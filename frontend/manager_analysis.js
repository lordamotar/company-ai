// Вкладка «Анализ менеджеров»: фильтр периода + менеджер → метрики и AI-разбор слабых мест.
// Данные считает бэкенд (/api/managers/*), здесь — только отображение.
(function () {
    "use strict";

    const state = {
        inited: false,
        managers: [],
        period: { date_min: null, date_max: null },
        analysis: null,
        ai: null,
        loading: false,
        reqSeq: 0
    };

    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v === null || v === undefined ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const fmt = (v, suffix = "") => (v === null || v === undefined) ? "—" : `${v}${suffix}`;

    function isoToRu(iso) {
        if (!iso) return "—";
        const [y, m, d] = iso.split("-");
        return `${d}.${m}.${y}`;
    }
    function toIso(d) {
        const p = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }

    async function api(url, options) {
        const res = await fetch(url, options);
        let data = null;
        try { data = await res.json(); } catch (e) { /* не JSON */ }
        if (!res.ok) {
            const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`;
            throw new Error(detail);
        }
        return data;
    }

    // ---------------------------------------------------------------- фильтры
    function fillManagerSelect() {
        const sel = $("maManagerSelect");
        if (!sel) return;
        const prev = sel.value;
        sel.innerHTML = state.managers.map(m =>
            `<option value="${esc(m.name)}">${esc(m.name)} (${m.calls})</option>`).join("");
        if (prev && state.managers.some(m => m.name === prev)) sel.value = prev;
    }

    function setDatesToManagerRange() {
        const name = $("maManagerSelect").value;
        const m = state.managers.find(x => x.name === name);
        if (m) {
            $("maDateFrom").value = m.date_min || "";
            $("maDateTo").value = m.date_max || "";
        }
    }

    function applyPreset(kind) {
        const today = new Date();
        let from = null, to = null;
        if (kind === "7d") { from = new Date(today); from.setDate(from.getDate() - 6); to = today; }
        else if (kind === "30d") { from = new Date(today); from.setDate(from.getDate() - 29); to = today; }
        else if (kind === "month") { from = new Date(today.getFullYear(), today.getMonth(), 1); to = today; }
        else if (kind === "prev-month") {
            from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
            to = new Date(today.getFullYear(), today.getMonth(), 0);
        } else if (kind === "all") {
            $("maDateFrom").value = state.period.date_min || "";
            $("maDateTo").value = state.period.date_max || "";
            return runAnalysis();
        }
        $("maDateFrom").value = toIso(from);
        $("maDateTo").value = toIso(to);
        runAnalysis();
    }

    // ---------------------------------------------------------------- загрузка
    // Список менеджеров из уже загруженных звонков — запасной вариант, если API недоступно
    function managersFromCalls() {
        const groups = {};
        (typeof callsData !== "undefined" && Array.isArray(callsData) ? callsData : []).forEach(c => {
            const name = (c.manager || "Оператор").trim() || "Оператор";
            const m = String(c.date || "").match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
            const iso = m ? `${m[3]}-${m[2]}-${m[1]}` : null;
            const g = groups[name] || (groups[name] = { name, calls: 0, date_min: null, date_max: null });
            g.calls++;
            if (iso && (!g.date_min || iso < g.date_min)) g.date_min = iso;
            if (iso && (!g.date_max || iso > g.date_max)) g.date_max = iso;
        });
        return Object.values(groups).sort((a, b) => b.calls - a.calls || a.name.localeCompare(b.name, "ru"));
    }

    function periodFromManagers(list) {
        const mins = list.map(m => m.date_min).filter(Boolean).sort();
        const maxs = list.map(m => m.date_max).filter(Boolean).sort();
        return { date_min: mins[0] || null, date_max: maxs[maxs.length - 1] || null };
    }

    async function loadManagers() {
        let apiError = null;
        try {
            const data = await api("/api/managers?t=" + Date.now());
            state.managers = data.managers || [];
            state.period = data.period || state.period;
        } catch (e) {
            apiError = e;
        }
        if (!state.managers.length) {
            state.managers = managersFromCalls();
            state.period = periodFromManagers(state.managers);
        }
        fillManagerSelect();
        if (!state.managers.length && (state.retries = (state.retries || 0) + 1) <= 8) {
            // звонки в интерфейсе могли ещё не загрузиться — пробуем снова
            $("maResult").innerHTML = `<div class="ma-loading"><div class="ma-spinner"></div>Загружаю менеджеров…</div>`;
            setTimeout(loadManagers, 1000);
            return;
        }
        if (!state.managers.length) {
            $("maResult").innerHTML = emptyBox("Нет данных по менеджерам",
                apiError ? esc(apiError.message) : "Сначала обработайте звонки.");
            return;
        }
        if (state.pendingManager && state.managers.some(m => m.name === state.pendingManager)) {
            $("maManagerSelect").value = state.pendingManager;
        }
        state.pendingManager = null;
        setDatesToManagerRange();
        runAnalysis();
    }

    async function runAnalysis() {
        const manager = $("maManagerSelect").value;
        const dateFrom = $("maDateFrom").value;
        const dateTo = $("maDateTo").value;
        if (!manager) return;
        if (dateFrom && dateTo && dateFrom > dateTo) {
            $("maResult").innerHTML = emptyBox("Неверный период", "Начало периода позже его конца.");
            return;
        }
        const seq = ++state.reqSeq;
        state.ai = null;
        $("maResult").innerHTML = `<div class="ma-loading"><div class="ma-spinner"></div>Считаю метрики…</div>`;
        try {
            const qs = new URLSearchParams({ manager });
            if (dateFrom) qs.set("date_from", dateFrom);
            if (dateTo) qs.set("date_to", dateTo);
            const data = await api("/api/managers/analysis?" + qs.toString());
            if (seq !== state.reqSeq) return; // пришёл устаревший ответ
            state.analysis = data;
            renderAnalysis();
        } catch (e) {
            if (seq !== state.reqSeq) return;
            const notFound = /not found|HTTP 404/i.test(e.message);
            $("maResult").innerHTML = emptyBox("Ошибка расчёта", notFound
                ? "API анализа менеджеров не найдено на сервере. Перезапустите веб-сервер (docker compose up -d --build web или server.py), чтобы подхватились новые файлы."
                : esc(e.message));
        }
    }

    async function runAi(refresh) {
        const a = state.analysis;
        if (!a) return;
        const box = $("maAiBox");
        box.innerHTML = `<div class="ma-loading"><div class="ma-spinner"></div>
            ИИ разбирает звонки менеджера — это занимает 20–60 секунд…</div>`;
        const seq = state.reqSeq;
        try {
            const data = await api("/api/managers/ai-analysis", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ manager: a.manager, date_from: a.date_from, date_to: a.date_to, refresh: !!refresh })
            });
            if (seq !== state.reqSeq) return;
            state.ai = data;
            renderAi();
        } catch (e) {
            if (seq !== state.reqSeq) return;
            box.innerHTML = aiIntro(`<div class="ma-error">⚠️ ${esc(e.message)}</div>`);
            bindAiButtons();
        }
    }

    // ---------------------------------------------------------------- отрисовка
    function emptyBox(title, text) {
        return `<div class="ma-empty"><div style="font-size:32px;margin-bottom:8px;">🗂️</div>
            <strong>${esc(title)}</strong><span>${text}</span></div>`;
    }

    function delta(value, team, unit = "", higherIsBetter = true) {
        if (value === null || value === undefined || team === null || team === undefined) return "";
        const diff = Math.round((value - team) * 10) / 10;
        if (diff === 0) return `<span class="ma-delta neutral">= как в команде (${team}${unit})</span>`;
        const good = higherIsBetter ? diff > 0 : diff < 0;
        return `<span class="ma-delta ${good ? "good" : "bad"}">${diff > 0 ? "▲" : "▼"} ${Math.abs(diff)}${unit} к команде (${team}${unit})</span>`;
    }

    function callLink(id) {
        return `<a href="javascript:void(0)" class="ma-call-link" onclick="openCallModal(${Number(id)})">#${Number(id)}</a>`;
    }

    function renderAnalysis() {
        const a = state.analysis;
        const m = a.metrics;
        const root = $("maResult");
        if (!m || !m.calls) {
            root.innerHTML = emptyBox("За этот период звонков нет",
                `У менеджера «${esc(a.manager)}» нет звонков за ${isoToRu(a.date_from)} — ${isoToRu(a.date_to)}. Измените период.`);
            return;
        }
        const t = m.team || {};
        const few = m.calls < 5
            ? `<div class="ma-warning">⚠️ Всего ${m.calls} зв. в выборке — выводы носят ориентировочный характер, расширьте период для надёжного анализа.</div>` : "";

        root.innerHTML = `
            <div class="ma-head">
                <div class="ma-avatar">${esc((a.manager || "М")[0])}</div>
                <div>
                    <div class="ma-head-name">${esc(a.manager)}</div>
                    <div class="ma-head-sub">Период: ${isoToRu(a.date_from)} — ${isoToRu(a.date_to)} • звонков: ${m.calls}
                        (вх. ${m.in_calls}, исх. ${m.out_calls}) • общее время ${esc(m.total_duration)} • средний ${esc(m.avg_duration || "—")}</div>
                </div>
            </div>
            ${few}
            <div class="ma-kpis">
                ${kpi("📋 Соблюдение скрипта", fmt(m.avg_script, "%"), delta(m.avg_script, t.avg_script, "%"))}
                ${kpi("⭐ CSAT", fmt(m.avg_csat), delta(m.avg_csat, t.avg_csat))}
                ${kpi("🤝 Вежливость", fmt(m.avg_politeness, " / 5"), delta(m.avg_politeness, t.avg_politeness))}
                ${kpi("🛒 Результативных звонков", fmt(m.won_rate, "%"), `<span class="ma-delta neutral">бронь/продажа: ${m.won_calls} из ${m.calls}` +
                    (t.won_rate !== undefined && t.won_rate !== null ? `; команда ${t.won_rate}%` : "") + `</span>`)}
                ${kpi("🔥 Конфликты", m.conflicts, m.conflicts ? `<span class="ma-delta bad">звонки: ${m.conflict_calls.map(callLink).join(", ")}</span>` : `<span class="ma-delta good">не выявлено</span>`)}
                ${kpi("🔇 Тишина в разговоре", fmt(m.avg_silence_pct, "%"), delta(m.avg_silence_pct, t.avg_silence_pct, "%", false) +
                    (m.long_silence_calls.length ? `<span class="ma-sub">тишина >30%: ${m.long_silence_calls.length} зв.</span>` : ""))}
            </div>

            <div class="ma-grid-2">
                <div class="card ma-card">
                    <h3>Этапы скрипта</h3>
                    <p class="ma-hint">Процент считается только по звонкам, где этап применим. Метка команды — средний показатель всех менеджеров за тот же период. Нажмите на «не выполнен», чтобы увидеть звонки.</p>
                    ${renderStages(m)}
                </div>
                <div class="card ma-card">
                    <h3>Динамика за период</h3>
                    ${renderTrend(m.trend)}
                    <h3 style="margin-top:22px;">Исходы звонков</h3>
                    ${renderOutcomes(m)}
                </div>
            </div>

            <div class="card ma-card" id="maAiBox"></div>

            <div class="card ma-card">
                <h3>Проблемные звонки периода</h3>
                <p class="ma-hint">Рейтинг по числу невыполненных этапов, CSAT, конфликтам и оценке скрипта. Допродажа учитывается с малым весом — она не предлагается почти везде.</p>
                ${renderProblemCalls(m)}
            </div>
        `;
        renderAi();
        bindStageToggles();
    }

    function kpi(label, value, sub) {
        return `<div class="ma-kpi"><div class="ma-kpi-label">${label}</div><div class="ma-kpi-value">${value}</div><div class="ma-kpi-sub">${sub || ""}</div></div>`;
    }

    function renderStages(m) {
        const team = (m.team && m.team.stage_pass) || {};
        return m.stages.slice().sort((x, y) => (x.pass_rate ?? 101) - (y.pass_rate ?? 101)).map((s, i) => {
            const rate = s.pass_rate;
            const cls = rate === null ? "na" : rate >= 90 ? "green" : rate >= 70 ? "yellow" : "red";
            const tm = team[s.title];
            const teamMark = (tm !== undefined && tm !== null) ? `<div class="ma-team-mark" style="left:${tm}%" title="Команда: ${tm}%"></div>` : "";
            const failedBtn = s.failed
                ? `<button class="ma-failed-btn" data-stage="${i}">не выполнен: ${s.failed} зв. ▾</button>` : `<span class="ma-ok">без нарушений</span>`;
            return `
            <div class="ma-stage">
                <div class="ma-stage-top">
                    <div class="ma-stage-name">${esc(s.title)}</div>
                    <div class="ma-stage-val ${cls}">${rate === null ? "н/п" : rate + "%"}</div>
                </div>
                <div class="ma-bar"><div class="ma-bar-fill ${cls}" style="width:${rate || 0}%"></div>${teamMark}</div>
                <div class="ma-stage-meta">применим в ${s.applicable} из ${m.calls} зв.${s.na ? ` • неприменим: ${s.na}` : ""} • ${failedBtn}</div>
                <div class="ma-stage-calls" id="maStageCalls${i}" style="display:none;">${(s.failed_calls || []).map(callLink).join(" ")}</div>
            </div>`;
        }).join("");
    }

    function bindStageToggles() {
        document.querySelectorAll(".ma-failed-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const el = $("maStageCalls" + btn.dataset.stage);
                if (el) el.style.display = el.style.display === "none" ? "block" : "none";
            });
        });
    }

    function renderTrend(trend) {
        const pts = (trend && trend.points) || [];
        if (pts.length < 2) {
            return `<div class="ma-hint">Для динамики нужно минимум два периода (${trend && trend.granularity === "day" ? "дня" : "недели"}) со звонками.</div>`;
        }
        const W = 520, H = 190, pl = 34, pr = 26, pt = 14, pb = 34;
        const iw = W - pl - pr, ih = H - pt - pb;
        const x = (i) => pl + (pts.length === 1 ? iw / 2 : (iw * i) / (pts.length - 1));
        const yScript = (v) => pt + ih - (Math.max(0, Math.min(100, v)) / 100) * ih;
        const yCsat = (v) => pt + ih - (Math.max(0, Math.min(5, v)) / 5) * ih;
        const line = (fn, key, color) => {
            const d = pts.map((p, i) => p[key] === null ? null : `${x(i)},${fn(p[key])}`).filter(Boolean);
            if (!d.length) return "";
            return `<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${d.join(" ")}"/>` +
                pts.map((p, i) => p[key] === null ? "" :
                    `<circle cx="${x(i)}" cy="${fn(p[key])}" r="4" fill="${color}"><title>${esc(p.label)}: ${p[key]}</title></circle>`).join("");
        };
        const grid = [0, 25, 50, 75, 100].map(v =>
            `<line x1="${pl}" x2="${W - pr}" y1="${yScript(v)}" y2="${yScript(v)}" stroke="rgba(255,255,255,.07)"/>
             <text x="${pl - 6}" y="${yScript(v) + 4}" text-anchor="end" fill="#64748b" font-size="10">${v}</text>`).join("");
        const labels = pts.map((p, i) =>
            `<text x="${x(i)}" y="${H - 12}" text-anchor="${i === pts.length - 1 ? "end" : i === 0 ? "start" : "middle"}" fill="#94a3b8" font-size="10">${esc(p.label)}</text>
             <text x="${x(i)}" y="${H - 1}" text-anchor="${i === pts.length - 1 ? "end" : i === 0 ? "start" : "middle"}" fill="#64748b" font-size="9">${p.calls} зв.</text>`).join("");
        return `
        <svg viewBox="0 0 ${W} ${H}" class="ma-chart" role="img" aria-label="Динамика скрипта и CSAT">${grid}
            ${line(yScript, "script", "#a78bfa")}${line(yCsat, "csat", "#38bdf8")}${labels}</svg>
        <div class="ma-legend"><span><i style="background:#a78bfa"></i>Скрипт, % (шкала слева)</span>
            <span><i style="background:#38bdf8"></i>CSAT, 0–5 (масштабирован на ту же высоту)</span></div>`;
    }

    function renderOutcomes(m) {
        const total = m.calls;
        const colors = { "Продажа состоялась": "green", "Бронь оформлена": "green", "Консультация": "blue", "Отказ от покупки": "red" };
        const rows = Object.entries(m.outcomes).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
            `<div class="ma-outcome"><span>${esc(k)}</span><div class="ma-bar small"><div class="ma-bar-fill ${colors[k] || "blue"}" style="width:${Math.round(v * 100 / total)}%"></div></div><b>${v}</b></div>`).join("");
        const lost = Object.entries(m.lost_reasons || {});
        const lostHtml = lost.length ? `<div class="ma-lost"><div class="ma-hint" style="margin-bottom:6px;">Причины отказов / несостоявшихся сделок:</div>
            <ul>${lost.map(([k, v]) => `<li>${esc(k)} — ${v}</li>`).join("")}</ul></div>` : "";
        return rows + lostHtml;
    }

    function renderProblemCalls(m) {
        if (!m.problem_calls.length) return `<div class="ma-hint">Проблемных звонков не найдено.</div>`;
        return `<div class="ma-table-wrap"><table class="ma-table"><thead><tr>
            <th>Звонок</th><th>Тема</th><th>Скрипт</th><th>CSAT</th><th>Нарушения</th><th class="text-right"></th></tr></thead><tbody>` +
            m.problem_calls.map(p => `<tr>
                <td><div class="call-id">#${p.id}</div><div class="call-time">${esc(p.date)} ${esc(p.time)}</div></td>
                <td><div class="topic-title">${esc(p.topic)}</div><div class="ma-sub">${esc(p.sale_status || "")} • ${esc(p.duration || "")}</div></td>
                <td><span class="script-score">${fmt(p.script_score, "%")}</span></td>
                <td>${p.csat === null ? "—" : "⭐ " + p.csat}</td>
                <td class="ma-viol">${p.failed_stages.map(s => `<span class="ma-tag red">${esc(s)}</span>`).join("")}
                    ${p.upsell_missed ? `<span class="ma-tag yellow">без допродажи</span>` : ""}
                    ${p.conflict ? `<span class="ma-tag red">конфликт</span>` : ""}
                    ${(p.silence_pct || 0) > 40 ? `<span class="ma-tag yellow">тишина ${p.silence_pct}%</span>` : ""}</td>
                <td class="text-right"><button class="btn btn-secondary ma-open-btn" onclick="openCallModal(${p.id})">Разбор AI</button></td>
            </tr>`).join("") + `</tbody></table></div>`;
    }

    // ---------------------------------------------------------------- AI
    function aiIntro(inner) {
        return `<div class="ma-ai-head"><div><h3 style="margin:0;">🤖 AI-разбор слабых мест</h3>
            <div class="ma-hint" style="margin-top:4px;">ИИ анализирует цифры периода и полные диалоги самых проблемных звонков, находит повторяющиеся ошибки и подтверждает их цитатами.</div></div>
            <button class="btn btn-primary" id="maAiRunBtn">✨ Сгенерировать AI-разбор</button></div>${inner || ""}`;
    }

    function copyAiReport() {
        const a = state.analysis;
        const ai = state.ai;
        if (!a || !ai) return;
        const m = a.metrics || {};
        const r = ai.report || {};

        let text = `========================================================\n`;
        text += `ОТЧЕТ КОНТРОЛЯ КАЧЕСТВА: ${a.manager}\n`;
        text += `Период: ${isoToRu(a.date_from)} — ${isoToRu(a.date_to)}\n`;
        text += `Проанализировано звонков: ${m.calls || 0} (вх: ${m.in_calls || 0}, исх: ${m.out_calls || 0})\n`;
        text += `Соблюдение скрипта: ${fmt(m.avg_script, "%")} | CSAT: ${fmt(m.avg_csat)} / 5 | Вежливость: ${fmt(m.avg_politeness)} / 5\n`;
        text += `Результативность: ${fmt(m.won_rate, "%")} (${m.won_calls || 0} сделок/броней)\n`;
        text += `Конфликтов: ${m.conflicts || 0}\n`;
        text += `========================================================\n\n`;

        if (r.level) text += `УРОВЕНЬ СОТРУДНИКА: ${r.level}\n\n`;
        if (r.verdict) text += `ВЕРДИКТ ИИ:\n${r.verdict}\n\n`;

        if (r.weak_points && r.weak_points.length) {
            text += `СЛАБЫЕ МЕСТА И ЗОНЫ РОСТА:\n`;
            r.weak_points.forEach((w, i) => {
                text += `${i + 1}. [${(w.severity || "важно").toUpperCase()}] ${w.title}\n`;
                if (w.pattern) text += `   Суть: ${w.pattern}\n`;
                if (w.impact) text += `   Последствия: ${w.impact}\n`;
                if (w.fix) text += `   Решение: ${w.fix}\n`;
                if (w.sample_phrase) text += `   Пример фразы: «${w.sample_phrase}»\n`;
                text += `\n`;
            });
        }

        if (r.strengths && r.strengths.length) {
            text += `СИЛЬНЫЕ СТОРОНЫ:\n`;
            r.strengths.forEach((s, i) => {
                text += `${i + 1}. ${s.title}: ${s.detail || ""}\n`;
            });
            text += `\n`;
        }

        if (r.coaching_plan && r.coaching_plan.length) {
            text += `ПЛАН РАБОТЫ РОПА С МЕНЕДЖЕРОМ:\n`;
            r.coaching_plan.forEach((c, i) => {
                text += `${i + 1}. Фокус: ${c.focus}\n   Упражнение: ${c.exercise}\n`;
            });
            text += `\n`;
        }

        text += `Отчет сформирован моделью: ${ai.model || "AI"} (${(ai.generated_at || "").replace("T", " ")})\n`;

        navigator.clipboard.writeText(text).then(() => {
            showToast("✓ Отчет успешно скопирован в буфер обмена!");
        }).catch(() => {
            prompt("Скопируйте текст отчета:", text);
        });
    }

    function showToast(msg) {
        const old = document.querySelector(".ma-copy-toast");
        if (old) old.remove();
        const t = document.createElement("div");
        t.className = "ma-copy-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    function downloadAiReport() {
        const a = state.analysis;
        const ai = state.ai;
        if (!a || !ai) return;
        const m = a.metrics || {};
        const r = ai.report || {};
        const safeMgr = (a.manager || "manager").replace(/[/\\?%*:|"<>]/g, "_").trim();
        const safePeriod = `${a.date_from || ""}_${a.date_to || ""}`.replace(/[/\\?%*:|"<>]/g, "_");
        const filename = `Отчет_контроля_качества_${safeMgr}_${safePeriod}.html`;

        const weakHtml = (r.weak_points || []).map((w, idx) => `
            <div class="card weak-card ${w.severity || 'minor'}">
                <div class="badge ${w.severity || 'minor'}">${(w.severity === 'critical' ? 'Критично' : w.severity === 'major' ? 'Существенно' : 'Незначительно')}</div>
                <h3 style="display:inline; margin-left: 8px;">${idx + 1}. ${esc(w.title || "")}</h3>
                ${w.frequency ? `<div class="freq">${esc(w.frequency)}</div>` : ""}
                ${w.pattern ? `<p class="pattern"><b>Суть проблемы:</b> ${esc(w.pattern)}</p>` : ""}
                ${(w.evidence && w.evidence.length) ? `
                    <div class="evidence-box">
                        <b>Примеры из звонков:</b>
                        <ul>
                            ${w.evidence.map(e => `<li><b>Звонок #${e.call_id}:</b> ${e.quote ? `<i>«${esc(e.quote)}»</i>` : ''} ${e.comment ? `— ${esc(e.comment)}` : ''}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}
                ${w.impact ? `<p class="impact"><b>К чему приводит:</b> ${esc(w.impact)}</p>` : ""}
                ${w.fix ? `<p class="fix"><b>Решение:</b> ${esc(w.fix)}</p>` : ""}
                ${w.sample_phrase ? `<p class="sample">💬 <b>Пример правильной фразы:</b> «${esc(w.sample_phrase)}»</p>` : ""}
            </div>
        `).join('');

        const strengthsHtml = (r.strengths || []).map(s => `
            <div class="mini-card good"><b>✓ ${esc(s.title || "")}</b><div>${esc(s.detail || "")}</div></div>
        `).join('');

        const missedHtml = (r.missed_opportunities || []).map(o => `
            <div class="mini-card warn"><b>Звонок #${o.call_id}:</b> ${esc(o.what_missed || "")}</div>
        `).join('');

        const coachingHtml = (r.coaching_plan || []).map((c, i) => `
            <div class="mini-card"><b>${i + 1}. ${esc(c.focus || "")}</b><div>${esc(c.exercise || "")}</div></div>
        `).join('');

        const docHtml = `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Отчет контроля качества: ${esc(a.manager)}</title>
<style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 24px; line-height: 1.5; }
    .container { max-width: 900px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 4px 20px rgba(0,0,0,0.06); }
    .header { border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 24px; }
    .header h1 { margin: 0 0 6px 0; font-size: 24px; color: #0f172a; }
    .header .sub { color: #64748b; font-size: 14px; }
    .actions { margin-bottom: 24px; }
    .btn { background: #2563eb; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; }
    .btn:hover { background: #1d4ed8; }
    .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 24px; }
    .kpi { background: #f1f5f9; border-radius: 8px; padding: 12px 14px; border: 1px solid #e2e8f0; }
    .kpi .num { font-size: 20px; font-weight: 700; color: #0f172a; margin-top: 4px; }
    .kpi .lbl { font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; }
    .verdict { background: #f0fdf4; border-left: 4px solid #16a34a; padding: 16px; border-radius: 0 8px 8px 0; margin-bottom: 24px; }
    .verdict.middle { background: #fffbeb; border-left-color: #d97706; }
    .verdict.low { background: #fef2f2; border-left-color: #dc2626; }
    .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    .card.critical { border-left: 4px solid #ef4444; }
    .card.major { border-left: 4px solid #f59e0b; }
    .card.minor { border-left: 4px solid #3b82f6; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .badge.critical { background: #fee2e2; color: #991b1b; }
    .badge.major { background: #fef3c7; color: #92400e; }
    .badge.minor { background: #dbeafe; color: #1e40af; }
    .evidence-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; margin: 12px 0; font-size: 13.5px; }
    .evidence-box ul { margin: 6px 0 0 18px; padding: 0; }
    .pattern, .impact, .fix, .sample { font-size: 14px; margin: 8px 0; }
    .sample { background: #f1f5f9; padding: 8px 12px; border-radius: 6px; font-style: italic; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
    .mini-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; font-size: 13.5px; }
    .mini-card.good { border-left: 3px solid #16a34a; }
    .mini-card.warn { border-left: 3px solid #f59e0b; }
    .meta { font-size: 12px; color: #94a3b8; border-top: 1px solid #e2e8f0; padding-top: 14px; margin-top: 24px; }
    @media print {
        body { background: #fff; padding: 0; }
        .container { box-shadow: none; padding: 0; max-width: 100%; }
        .no-print { display: none !important; }
        .card { page-break-inside: avoid; }
    }
</style>
</head>
<body>
<div class="container">
    <div class="actions no-print">
        <button class="btn" onclick="window.print()">🖨️ Распечатать / Сохранить в PDF</button>
    </div>
    <div class="header">
        <h1>Аудит качества: ${esc(a.manager)}</h1>
        <div class="sub">Период: ${isoToRu(a.date_from)} — ${isoToRu(a.date_to)} • Сеть шинных центров Aikos</div>
    </div>
    <div class="kpis">
        <div class="kpi"><div class="lbl">Скрипт</div><div class="num">${fmt(m.avg_script, "%")}</div></div>
        <div class="kpi"><div class="lbl">CSAT</div><div class="num">${fmt(m.avg_csat)}</div></div>
        <div class="kpi"><div class="lbl">Вежливость</div><div class="num">${fmt(m.avg_politeness)} / 5</div></div>
        <div class="kpi"><div class="lbl">Конверсия</div><div class="num">${fmt(m.won_rate, "%")}</div></div>
        <div class="kpi"><div class="lbl">Звонков</div><div class="num">${m.calls || 0}</div></div>
        <div class="kpi"><div class="lbl">Конфликтов</div><div class="num">${m.conflicts || 0}</div></div>
    </div>
    <div class="verdict ${/выс/i.test(r.level || '') ? '' : /сред/i.test(r.level || '') ? 'middle' : 'low'}">
        <div style="font-weight:700; margin-bottom: 4px;">Уровень сотрудника: ${esc(r.level || "—")}</div>
        <div>${esc(r.verdict || "")}</div>
    </div>
    <h2 style="font-size:18px; margin: 24px 0 12px 0;">Слабые места и зоны роста</h2>
    ${weakHtml || "<p>Устойчивых слабых мест не выявлено.</p>"}
    <div class="grid-2">
        <div>
            <h2 style="font-size:16px; margin: 16px 0 10px 0;">Сильные стороны</h2>
            ${strengthsHtml || "<p>—</p>"}
        </div>
        <div>
            <h2 style="font-size:16px; margin: 16px 0 10px 0;">План работы с менеджером</h2>
            ${coachingHtml || "<p>—</p>"}
        </div>
    </div>
    ${missedHtml ? `<h2 style="font-size:16px; margin: 20px 0 10px 0;">Упущенные возможности</h2>${missedHtml}` : ""}
    ${r.dynamics ? `<p style="margin-top:16px; font-size:13.5px;"><b>Динамика:</b> ${esc(r.dynamics)}</p>` : ""}
    <div class="meta">
        Сформировано: ${(ai.generated_at || "").replace("T", " ")} | Модель: ${esc(ai.model || "AI")} | Выборка: ${ai.calls_analyzed || 0} звонков
    </div>
</div>
</body>
</html>`;

        const blob = new Blob([docHtml], { type: "text/html;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        showToast("✓ Отчет успешно скачан!");
    }

    function bindAiButtons() {
        const run = $("maAiRunBtn");
        if (run) run.addEventListener("click", () => runAi(false));
        const re = $("maAiRefreshBtn");
        if (re) re.addEventListener("click", () => runAi(true));
        const downloadBtn = $("maAiDownloadBtn");
        if (downloadBtn) downloadBtn.addEventListener("click", downloadAiReport);
        const copyBtn = $("maAiCopyBtn");
        if (copyBtn) copyBtn.addEventListener("click", copyAiReport);
    }

    const SEV = {
        critical: { label: "Критично", cls: "red" },
        major: { label: "Существенно", cls: "yellow" },
        minor: { label: "Незначительно", cls: "blue" }
    };

    function renderAi() {
        const box = $("maAiBox");
        if (!box) return;
        const ai = state.ai;
        if (!ai) { box.innerHTML = aiIntro(""); bindAiButtons(); return; }
        const r = ai.report || {};
        const level = r.level ? `<span class="ma-tag ${/выс/i.test(r.level) ? "green" : /сред/i.test(r.level) ? "yellow" : "red"}">уровень: ${esc(r.level)}</span>` : "";

        const weak = (r.weak_points || []).map(w => {
            const sev = SEV[w.severity] || SEV.minor;
            const ev = (w.evidence || []).map(e => `
                <div class="ma-evidence">
                    <div>${callLink(e.call_id)} ${e.quote ? `<span class="ma-quote">«${esc(e.quote)}»</span>` : ""}
                        ${e.quote ? (e.verified ? `<span class="ma-verified ok" title="Цитата найдена в расшифровке звонка">✓ цитата подтверждена</span>` :
                        `<span class="ma-verified warn" title="Точной цитаты нет в расшифровке — проверьте звонок вручную">⚠ цитата не найдена в записи</span>`) : ""}</div>
                    ${e.comment ? `<div class="ma-ev-comment">${esc(e.comment)}</div>` : ""}
                </div>`).join("");
            return `<div class="ma-weak ${sev.cls}">
                <div class="ma-weak-top"><span class="ma-tag ${sev.cls}">${sev.label}</span><strong>${esc(w.title)}</strong>
                    ${w.frequency ? `<span class="ma-freq">${esc(w.frequency)}</span>` : ""}</div>
                ${w.pattern ? `<p>${esc(w.pattern)}</p>` : ""}
                ${ev ? `<div class="ma-ev-list">${ev}</div>` : ""}
                ${w.impact ? `<p class="ma-impact"><b>К чему приводит:</b> ${esc(w.impact)}</p>` : ""}
                ${w.fix ? `<p class="ma-fix"><b>Что делать иначе:</b> ${esc(w.fix)}</p>` : ""}
                ${w.sample_phrase ? `<p class="ma-sample">💬 «${esc(w.sample_phrase)}»</p>` : ""}
            </div>`;
        }).join("");

        const strengths = (r.strengths || []).map(s =>
            `<div class="ma-mini good"><b>${esc(s.title)}</b>${s.detail ? `<div>${esc(s.detail)}</div>` : ""}</div>`).join("");
        const missed = (r.missed_opportunities || []).map(o =>
            `<div class="ma-mini warn">${callLink(o.call_id)} ${esc(o.what_missed)}</div>`).join("");
        const coaching = (r.coaching_plan || []).map((c, i) =>
            `<div class="ma-mini"><b>${i + 1}. ${esc(c.focus)}</b><div>${esc(c.exercise)}</div></div>`).join("");

        box.innerHTML = aiIntro(`
            <div class="ma-verdict">${level}<p>${esc(r.verdict || "")}</p></div>
            ${weak ? `<h4 class="ma-h4">Слабые места</h4>${weak}` : `<div class="ma-hint">ИИ не выявил устойчивых слабых мест.</div>`}
            <div class="ma-cols">
                ${strengths ? `<div><h4 class="ma-h4">Сильные стороны</h4>${strengths}</div>` : ""}
                ${missed ? `<div><h4 class="ma-h4">Упущенные возможности</h4>${missed}</div>` : ""}
                ${coaching ? `<div><h4 class="ma-h4">План работы с менеджером</h4>${coaching}</div>` : ""}
            </div>
            ${r.dynamics ? `<p class="ma-dyn"><b>Динамика:</b> ${esc(r.dynamics)}</p>` : ""}
            ${r.data_limitations ? `<p class="ma-hint">ℹ️ ${esc(r.data_limitations)}</p>` : ""}
            <div class="ma-ai-actions">
                <button class="btn btn-primary" id="maAiDownloadBtn">📥 Скачать отчет</button>
                <button class="btn btn-secondary" id="maAiCopyBtn">📋 Скопировать отчет</button>
                <button class="ma-link-btn" id="maAiRefreshBtn" style="margin-left:auto;">↻ Пересчитать</button>
            </div>
            <div class="ma-ai-meta">Модель: ${esc(ai.model)} • проанализировано звонков: ${ai.calls_analyzed}, из них полный диалог отдан ИИ по ${ai.calls_with_full_dialogue} наиболее проблемным •
                ${esc((ai.generated_at || "").replace("T", " "))}${ai.cached ? " • из кэша" : ""}</div>
        `);
        // кнопку «Сгенерировать» после готового отчёта не показываем — есть «Пересчитать»
        const runBtn = $("maAiRunBtn");
        if (runBtn) runBtn.style.display = "none";
        bindAiButtons();
    }

    // ---------------------------------------------------------------- init
    function init() {
        if (!state.inited) {
            state.inited = true;
            $("maManagerSelect").addEventListener("change", () => { setDatesToManagerRange(); runAnalysis(); });
            $("maDateFrom").addEventListener("change", runAnalysis);
            $("maDateTo").addEventListener("change", runAnalysis);
            document.querySelectorAll("[data-ma-preset]").forEach(b =>
                b.addEventListener("click", () => applyPreset(b.dataset.maPreset)));
            loadManagers();
        }
    }

    window.initManagerAnalysis = init;

    // Самостоятельный запуск: не зависит от того, какая версия app.js загружена (кэш браузера).
    // Как только экран становится активным — обновляем заголовок и инициализируем вкладку.
    function onViewShown() {
        const h = $("pageMainHeading"), sh = $("pageMainSubheading");
        if (h) h.innerText = "Детальный анализ менеджеров";
        if (sh) sh.innerText = "Разбор работы сотрудника за выбранный период: слабые места, нарушения скрипта, AI-рекомендации";
        init();
    }
    function watchView() {
        const view = $("view-mgr-analysis");
        if (!view) return;
        if (view.classList.contains("active")) onViewShown();
        new MutationObserver(() => {
            if (view.classList.contains("active")) onViewShown();
        }).observe(view, { attributes: true, attributeFilter: ["class"] });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchView);
    else watchView();
    window.openManagerAnalysisFor = function (name) {
        const wasInited = state.inited;
        state.pendingManager = name;
        if (typeof switchView === "function") switchView("view-mgr-analysis");
        const sel = $("maManagerSelect");
        if (wasInited && sel && name) { sel.value = name; state.pendingManager = null; setDatesToManagerRange(); runAnalysis(); }
    };

    // Привязываем кнопку «Экспортировать отчет РОПа» из модального окна менеджера
    document.addEventListener("click", (e) => {
        if (e.target && e.target.id === "mgrExportReportBtn") {
            const modal = $("managerModal");
            const nameEl = $("mgrModalTitle");
            const name = nameEl ? nameEl.textContent.trim() : null;
            if (modal) modal.classList.remove("active");
            if (name && window.openManagerAnalysisFor) {
                window.openManagerAnalysisFor(name);
            }
        }
    });
})();
