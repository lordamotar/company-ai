// Отчёт контроля качества переписки по менеджеру — как отчёт РОПа по звонкам, но без ИИ:
// слабые места выводятся из чек-листа, скорости и пропущенных ответов, примеры — реальные цитаты из переписки.
// Встраивается в dialog/dashboard.html (dialog_processor.py), работает без интернета.

const REPORT_TIPS = {
    greeting: {
        title: "Не здоровается с клиентом",
        pattern: "Первое сообщение менеджера без приветствия — сразу ответ или вопрос.",
        impact: "Ответ выглядит сухим и формальным, клиент меньше доверяет.",
        fix: "Начинать каждый диалог с приветствия и своего имени.",
        phrase: "Добрый день! Меня зовут {name}, менеджер Aikos. Чем могу помочь?",
        exercise: "Добавить приветствие в шаблон быстрых ответов и проверить 10 следующих диалогов.",
    },
    needs: {
        title: "Не уточняет потребность",
        pattern: "Менеджер не задаёт вопросов о городе, размере, автомобиле, сезоне и количестве — отвечает «в лоб» или ждёт, пока клиент напишет сам.",
        impact: "Без параметров нельзя подобрать товар: диалог затягивается, клиент уходит к конкурентам.",
        fix: "Задавать 2–3 уточняющих вопроса в первом же ответе.",
        phrase: "Подскажите, пожалуйста, в каком городе и на какой автомобиль подбираем? Нужен комплект из 4 шин?",
        exercise: "Разобрать примеры из отчёта: для каждого записать, какие вопросы нужно было задать клиенту.",
    },
    offer: {
        title: "Не предлагает конкретный товар",
        pattern: "Нет конкретной позиции: размера с моделью, ссылки на каталог или варианта «есть в наличии».",
        impact: "Клиенту нечего выбрать — решение откладывается, продажа не происходит.",
        fix: "Предлагать 1–3 конкретных варианта с наличием в городе клиента и ссылкой на каталог.",
        phrase: "В наличии в вашем городе есть 2 варианта: … Какой вам ближе?",
        exercise: "Для 5 диалогов из отчёта подобрать по каталогу 2–3 варианта и написать ответ с ними.",
    },
    price: {
        title: "Не называет цену",
        pattern: "В ответах менеджера нет стоимости товара или услуги.",
        impact: "Цена — главный вопрос клиента в переписке; без неё клиент идёт сравнивать в другие магазины.",
        fix: "В каждом предложении указывать цену за штуку и за комплект, акции и рассрочку.",
        phrase: "Стоимость — 52 150 ₸ за шину, комплект из 4 шт. — 208 600 ₸. Есть рассрочка Kaspi.",
        exercise: "Переписать ответы из примеров отчёта так, чтобы в каждом была цена за шт. и за комплект.",
    },
    cta: {
        title: "Не ведёт к покупке или записи",
        pattern: "Менеджер отвечает на вопрос, но не предлагает следующий шаг: бронь, заказ, оплату, доставку, запись.",
        impact: "Клиент получил информацию и ушёл «подумать» — сделка не закрыта.",
        fix: "Заканчивать каждый ответ предложением следующего шага.",
        phrase: "Оформить бронь на ваше имя? Могу сразу записать на шиномонтаж на удобное время.",
        exercise: "Составить 3 фразы-призыва (бронь, запись, доставка) и использовать их в каждом диалоге неделю.",
    },
    farewell: {
        title: "Не завершает диалог",
        pattern: "Диалог обрывается без благодарности и приглашения обращаться снова.",
        impact: "Клиент не получает ощущения заботы, ниже шанс повторного обращения.",
        fix: "Завершать диалог благодарностью и приглашением обращаться.",
        phrase: "Спасибо за обращение! Если появятся вопросы — пишите, будем рады помочь.",
        exercise: "Добавить фразу завершения в шаблоны быстрых ответов.",
    },
    slow: {
        title: "Долго отвечает",
        pattern: "Клиент ждёт первого ответа менеджера после передачи от ИИ-ассистента слишком долго (в рабочее время).",
        impact: "За время ожидания клиент пишет в другие магазины и покупает там.",
        fix: "Отвечать в течение 5–10 минут — хотя бы коротко, что вопрос взят в работу.",
        phrase: "Добрый день! Вижу ваш вопрос, проверяю наличие — отвечу в течение 5 минут.",
        exercise: "Включить уведомления о новых диалогах; РОПу — проверять очередь неотвеченных дважды в день.",
    },
    unanswered: {
        title: "Оставляет клиента без ответа",
        pattern: "Последний вопрос клиента остался без ответа менеджера.",
        impact: "Клиент с конкретным вопросом не получил ответ — почти гарантированно потерянная продажа.",
        fix: "Перед закрытием дня просматривать диалоги, где последнее сообщение от клиента.",
        phrase: "Извините за задержку! По вашему вопросу: …",
        exercise: "Пройти по всем диалогам «без ответа» из отчёта и ответить клиентам повторно.",
    },
    noReply: {
        title: "Не подключается после передачи от ИИ",
        pattern: "ИИ-ассистент передал диалог менеджеру, но менеджер так и не написал клиенту.",
        impact: "Клиент, которому пообещали менеджера, остаётся без ответа.",
        fix: "Отслеживать диалоги, переданные ботом, и отвечать на них в первую очередь.",
        phrase: "Добрый день! Я менеджер Aikos, подключился к вашему вопросу.",
        exercise: "Настроить уведомление о передаче диалога от ИИ-ассистента.",
    },
};

const SEVERITY_LABEL = { critical: "Критично", major: "Существенно", minor: "Незначительно" };
const SEVERITY_ORDER = { critical: 0, major: 1, minor: 2 };
const PRICE_QUESTION_RE = /сколько|цен[аыу]|стоим|почем|почём|прайс/i;

function maskPersonal(text) {
    return String(text || "")
        .replace(/(\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}/g, "[телефон]")
        .replace(/\b\d{12}\b/g, "[ИИН]");
}

function clip(text, max = 220) {
    const t = maskPersonal(text).replace(/https?:\/\/\S+/g, "[ссылка на каталог]").replace(/\s+/g, " ").trim();
    return t.length > max ? t.slice(0, max) + "…" : t;
}

function severityByRate(p) {
    return p < 20 ? "critical" : p < 50 ? "major" : p < 67 ? "minor" : null;
}

function managerReplies(d, name) {
    return (d.messages || []).filter(m => m.role === "operator" && m.sender === name);
}

// Пример: вопрос клиента перед ответом менеджера и сам ответ.
// Для приветствия показывается первый ответ, для остальных пунктов — самый содержательный (длинный).
function exchangeQuote(d, name, key) {
    const msgs = (d.messages || []).filter(m => m.role !== "bot" && !SYSTEM_TEXT_RE.test(m.text || ""));
    const own = msgs.map((m, i) => [m, i]).filter(([m]) => m.role === "operator" && m.sender === name);
    if (!own.length) return "";
    const at = key === "greeting" ? own[0][1]
        : own.reduce((best, cur) => (cur[0].text || "").length > (best[0].text || "").length ? cur : best)[1];
    const client = [...msgs.slice(0, at)].reverse().find(m => m.role === "client" && (m.text || "").trim());
    return (client ? `Клиент: «${clip(client.text, 140)}» → ` : "") + `Менеджер: «${clip(msgs[at].text)}»`;
}

function buildManagerReport(name) {
    const dialogs = currentFilteredDialogs.filter(d =>
        (d.messages || []).some(m => m.role === "operator" && m.sender === name)
        || (d.mgr && d.mgr.noReply && d.mgr.name === name));
    const rated = dialogs.filter(d => d.mgr && !d.mgr.noReply && d.mgr.name === name);
    const noReply = dialogs.filter(d => d.mgr && d.mgr.noReply);
    const n = rated.length;
    const pct = (k, total) => total ? Math.round(k * 100 / total) : 0;
    const dates = dialogs.map(d => (d.createdAt || "").slice(0, 10)).filter(Boolean).sort();
    const first = median(rated.map(d => d.mgr.firstResponseMin).filter(v => v !== null && v !== undefined));
    const unanswered = rated.filter(d => d.mgr.unanswered);
    const metrics = {
        dialogs: dialogs.length,
        rated: n,
        replies: dialogs.reduce((s, d) => s + managerReplies(d, name).length, 0),
        score: n ? Math.round(rated.reduce((s, d) => s + d.mgr.score, 0) / n) : 0,
        firstResponse: first,
        unanswered: unanswered.length,
        unansweredPct: pct(unanswered.length, n),
        noReply: noReply.length,
        conversions: rated.filter(d => d.isSale || d.isService).length,
    };
    metrics.conversionPct = pct(metrics.conversions, n);

    const weak = [], strengths = [];
    (rawData.managerChecklist || []).forEach(([key, label]) => {
        const passed = rated.filter(d => d.mgr.checks[key]).length;
        const p = pct(passed, n);
        const severity = severityByRate(p);
        if (!severity) {
            strengths.push({ title: label, detail: `Выполняется в ${p}% диалогов (${passed} из ${n}).` });
            return;
        }
        const failed = rated.filter(d => !d.mgr.checks[key])
            .sort((a, b) => managerReplies(b, name).length - managerReplies(a, name).length);
        weak.push({
            key, severity, rate: p,
            frequency: `Не выполнено в ${n - passed} из ${n} диалогов (выполняется только в ${p}%)`,
            evidence: failed.slice(0, 3).map(d => ({ sid: d.sid, quote: exchangeQuote(d, name, key) })),
        });
    });

    if (first !== null && first > MGR_SLOW_MIN) {
        const slow = rated.filter(d => d.mgr.firstResponseMin > MGR_SLOW_MIN)
            .sort((a, b) => b.mgr.firstResponseMin - a.mgr.firstResponseMin);
        weak.push({
            key: "slow", severity: first > 60 ? "critical" : "major",
            frequency: `Медиана первого ответа — ${fmtMin(first)}; дольше ${MGR_SLOW_MIN} мин — в ${slow.length} диалогах`,
            evidence: slow.slice(0, 3).map(d => ({ sid: d.sid, quote: `Клиент ждал первого ответа ${fmtMin(d.mgr.firstResponseMin)} рабочего времени` })),
        });
    } else if (first !== null) {
        strengths.push({ title: "Быстрый первый ответ", detail: `Медиана — ${fmtMin(first)} рабочего времени.` });
    }

    if (unanswered.length >= 3) {
        weak.push({
            key: "unanswered",
            severity: metrics.unansweredPct > 15 ? "critical" : metrics.unansweredPct > 7 ? "major" : "minor",
            frequency: `${unanswered.length} диалогов (${metrics.unansweredPct}%) — последний вопрос клиента без ответа`,
            evidence: unanswered.slice(0, 3).map(d => {
                const last = [...(d.messages || [])].reverse().find(m => m.role === "client" && (m.text || "").trim() && !SYSTEM_TEXT_RE.test(m.text));
                return { sid: d.sid, quote: last ? `Последнее сообщение клиента: «${clip(last.text, 160)}»` : "" };
            }),
        });
    }

    if (noReply.length) {
        weak.push({
            key: "noReply", severity: noReply.length >= 5 ? "major" : "minor",
            frequency: `${noReply.length} диалогов передано ИИ-ассистентом, ответа менеджера не было`,
            evidence: noReply.slice(0, 3).map(d => ({ sid: d.sid, quote: `Передан менеджеру ${d.mgr.handoffAt}` })),
        });
    }

    weak.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || (a.rate ?? 0) - (b.rate ?? 0));

    // Упущенные возможности: клиент спросил цену, менеджер её не назвал, покупки нет
    const missed = rated.filter(d => !d.mgr.checks.price && !d.isSale
        && (d.messages || []).some(m => m.role === "client" && PRICE_QUESTION_RE.test(m.text || "")))
        .slice(0, 5).map(d => {
            const q = (d.messages || []).find(m => m.role === "client" && PRICE_QUESTION_RE.test(m.text || ""));
            return { sid: d.sid, what: `Клиент спросил о цене («${clip(q.text, 120)}»), менеджер цену не назвал. Итог: ${d.outcome}.` };
        });

    const level = metrics.score >= 60 ? "Высокий" : metrics.score >= 35 ? "Средний" : "Низкий";
    const verdict = `${name}: ${metrics.dialogs} диалогов за период, средняя оценка по чек-листу ${metrics.score}/100, `
        + `медиана первого ответа ${fmtMin(first)}, продажи и записи — ${metrics.conversionPct}% диалогов. `
        + (strengths.length ? `Сильные стороны: ${strengths.map(s => s.title.toLowerCase()).join(", ")}. ` : "")
        + (weak.length ? `Главные зоны роста: ${weak.slice(0, 3).map(w => REPORT_TIPS[w.key].title.toLowerCase()).join("; ")}.` : "Устойчивых слабых мест не выявлено.");

    return {
        name, metrics, level, verdict, weak, strengths, missed,
        coaching: weak.slice(0, 3).map(w => ({ focus: REPORT_TIPS[w.key].title, exercise: REPORT_TIPS[w.key].exercise })),
        period: dates.length ? [dates[0], dates[dates.length - 1]] : ["", ""],
        generatedAt: new Date().toLocaleString("ru-RU"),
    };
}

function ruDate(iso) {
    return iso ? iso.split("-").reverse().join(".") : "—";
}

function reportPhrase(key, name) {
    return REPORT_TIPS[key].phrase.replace("{name}", name);
}

function downloadManagerReport() {
    const name = document.getElementById("mgrDetailSelect").value;
    if (!name) return;
    const r = buildManagerReport(name);
    const m = r.metrics;
    const esc = escapeHtml;

    const weakHtml = r.weak.map((w, i) => {
        const t = REPORT_TIPS[w.key];
        return `<div class="card ${w.severity}">
            <span class="badge ${w.severity}">${SEVERITY_LABEL[w.severity]}</span>
            <h3 style="display:inline; margin-left:8px;">${i + 1}. ${esc(t.title)}</h3>
            <div class="freq">${esc(w.frequency)}</div>
            <p><b>Суть проблемы:</b> ${esc(t.pattern)}</p>
            ${w.evidence.length ? `<div class="evidence"><b>Примеры из переписки:</b><ul>
                ${w.evidence.map(e => `<li><b>Диалог #${e.sid}:</b> <i>${esc(e.quote)}</i></li>`).join("")}</ul></div>` : ""}
            <p><b>К чему приводит:</b> ${esc(t.impact)}</p>
            <p><b>Решение:</b> ${esc(t.fix)}</p>
            <p class="sample">💬 <b>Пример правильной фразы:</b> «${esc(reportPhrase(w.key, name))}»</p>
        </div>`;
    }).join("");

    const doc = `<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>Отчёт контроля качества переписки: ${esc(name)}</title>
<style>
    body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background:#f8fafc; color:#1e293b; margin:0; padding:24px; line-height:1.5; }
    .container { max-width:900px; margin:0 auto; background:#fff; border-radius:12px; padding:32px; box-shadow:0 4px 20px rgba(0,0,0,0.06); }
    .header { border-bottom:2px solid #e2e8f0; padding-bottom:20px; margin-bottom:24px; }
    .header h1 { margin:0 0 6px; font-size:24px; color:#0f172a; }
    .sub { color:#64748b; font-size:14px; }
    .btn { background:#2563eb; color:#fff; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600; margin-bottom:24px; }
    .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:12px; margin-bottom:24px; }
    .kpi { background:#f1f5f9; border-radius:8px; padding:12px 14px; border:1px solid #e2e8f0; }
    .kpi .num { font-size:20px; font-weight:700; color:#0f172a; margin-top:4px; }
    .kpi .lbl { font-size:11px; text-transform:uppercase; color:#64748b; font-weight:600; }
    .verdict { background:#f0fdf4; border-left:4px solid #16a34a; padding:16px; border-radius:0 8px 8px 0; margin-bottom:24px; }
    .verdict.middle { background:#fffbeb; border-left-color:#d97706; }
    .verdict.low { background:#fef2f2; border-left-color:#dc2626; }
    .card { border:1px solid #e2e8f0; border-radius:8px; padding:16px; margin-bottom:16px; }
    .card.critical { border-left:4px solid #ef4444; } .card.major { border-left:4px solid #f59e0b; } .card.minor { border-left:4px solid #3b82f6; }
    .badge { display:inline-block; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700; text-transform:uppercase; }
    .badge.critical { background:#fee2e2; color:#991b1b; } .badge.major { background:#fef3c7; color:#92400e; } .badge.minor { background:#dbeafe; color:#1e40af; }
    .freq { font-size:13px; color:#64748b; margin-top:6px; }
    .card p { font-size:14px; margin:8px 0; }
    .evidence { background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px; margin:12px 0; font-size:13.5px; }
    .evidence ul { margin:6px 0 0 18px; padding:0; }
    .sample { background:#f1f5f9; padding:8px 12px; border-radius:6px; font-style:italic; }
    .grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:20px; }
    .mini { background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px 12px; margin-bottom:8px; font-size:13.5px; }
    .mini.good { border-left:3px solid #16a34a; } .mini.warn { border-left:3px solid #f59e0b; }
    .meta { font-size:12px; color:#94a3b8; border-top:1px solid #e2e8f0; padding-top:14px; margin-top:24px; }
    @media print { body { background:#fff; padding:0; } .container { box-shadow:none; padding:0; max-width:100%; } .no-print { display:none !important; } .card { page-break-inside:avoid; } }
</style></head>
<body><div class="container">
    <button class="btn no-print" onclick="window.print()">🖨️ Распечатать / Сохранить в PDF</button>
    <div class="header">
        <h1>Аудит качества переписки: ${esc(name)}</h1>
        <div class="sub">Период: ${ruDate(r.period[0])} — ${ruDate(r.period[1])} • WhatsApp / онлайн-чат • Сеть шинных центров Aikos</div>
    </div>
    <div class="kpis">
        <div class="kpi"><div class="lbl">Оценка</div><div class="num">${m.score} / 100</div></div>
        <div class="kpi"><div class="lbl">Диалогов</div><div class="num">${m.dialogs}</div></div>
        <div class="kpi"><div class="lbl">Ответов</div><div class="num">${m.replies}</div></div>
        <div class="kpi"><div class="lbl">1-й ответ</div><div class="num">${fmtMin(m.firstResponse)}</div></div>
        <div class="kpi"><div class="lbl">Без ответа</div><div class="num">${m.unanswered} (${m.unansweredPct}%)</div></div>
        <div class="kpi"><div class="lbl">Продажи + сервис</div><div class="num">${m.conversions} (${m.conversionPct}%)</div></div>
    </div>
    <div class="verdict ${r.level === "Высокий" ? "" : r.level === "Средний" ? "middle" : "low"}">
        <div style="font-weight:700; margin-bottom:4px;">Уровень сотрудника: ${r.level}</div>
        <div>${esc(r.verdict)}</div>
    </div>
    <h2 style="font-size:18px; margin:24px 0 12px;">Слабые места и зоны роста</h2>
    ${weakHtml || "<p>Устойчивых слабых мест не выявлено.</p>"}
    <div class="grid-2">
        <div><h2 style="font-size:16px; margin:16px 0 10px;">Сильные стороны</h2>
            ${r.strengths.map(s => `<div class="mini good"><b>✓ ${esc(s.title)}</b><div>${esc(s.detail)}</div></div>`).join("") || "<p>—</p>"}</div>
        <div><h2 style="font-size:16px; margin:16px 0 10px;">План работы с менеджером</h2>
            ${r.coaching.map((c, i) => `<div class="mini"><b>${i + 1}. ${esc(c.focus)}</b><div>${esc(c.exercise)}</div></div>`).join("") || "<p>—</p>"}</div>
    </div>
    ${r.missed.length ? `<h2 style="font-size:16px; margin:20px 0 10px;">Упущенные возможности</h2>
        ${r.missed.map(o => `<div class="mini warn"><b>Диалог #${o.sid}:</b> ${esc(o.what)}</div>`).join("")}` : ""}
    <div class="meta">Сформировано: ${r.generatedAt} • Оценка по правилам (чек-лист из 6 пунктов, время ответа в рабочие часы 9:00–20:00), без ИИ.
        Учтены только ответы менеджера, ответы ИИ-ассистента исключены. Телефоны в цитатах скрыты.</div>
</div></body></html>`;

    const safe = s => String(s).replace(/[/\\?%*:|"<>]/g, "_").trim();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([doc], { type: "text/html;charset=utf-8" }));
    link.download = `Отчет_переписка_${safe(name)}_${r.period[0]}_${r.period[1]}.html`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
}

function copyManagerReport() {
    const name = document.getElementById("mgrDetailSelect").value;
    if (!name) return;
    const r = buildManagerReport(name);
    const m = r.metrics;
    const line = "========================================================";
    let text = `${line}\nОТЧЁТ КОНТРОЛЯ КАЧЕСТВА ПЕРЕПИСКИ: ${name}\n`
        + `Период: ${ruDate(r.period[0])} — ${ruDate(r.period[1])}\n`
        + `Диалогов: ${m.dialogs} | Ответов менеджера: ${m.replies} | Оценка: ${m.score}/100\n`
        + `1-й ответ (медиана): ${fmtMin(m.firstResponse)} | Без ответа: ${m.unanswered} (${m.unansweredPct}%) | Продажи + сервис: ${m.conversions} (${m.conversionPct}%)\n${line}\n\n`
        + `УРОВЕНЬ СОТРУДНИКА: ${r.level}\n\nВЕРДИКТ:\n${r.verdict}\n\n`;
    if (r.weak.length) {
        text += "СЛАБЫЕ МЕСТА И ЗОНЫ РОСТА:\n";
        r.weak.forEach((w, i) => {
            const t = REPORT_TIPS[w.key];
            text += `${i + 1}. [${SEVERITY_LABEL[w.severity].toUpperCase()}] ${t.title}\n   ${w.frequency}\n   Суть: ${t.pattern}\n`;
            w.evidence.forEach(e => { text += `   Пример (диалог #${e.sid}): ${e.quote}\n`; });
            text += `   Последствия: ${t.impact}\n   Решение: ${t.fix}\n   Пример фразы: «${reportPhrase(w.key, name)}»\n\n`;
        });
    }
    if (r.strengths.length) text += "СИЛЬНЫЕ СТОРОНЫ:\n" + r.strengths.map((s, i) => `${i + 1}. ${s.title}: ${s.detail}`).join("\n") + "\n\n";
    if (r.coaching.length) text += "ПЛАН РАБОТЫ РОПА С МЕНЕДЖЕРОМ:\n" + r.coaching.map((c, i) => `${i + 1}. Фокус: ${c.focus}\n   Упражнение: ${c.exercise}`).join("\n") + "\n\n";
    if (r.missed.length) text += "УПУЩЕННЫЕ ВОЗМОЖНОСТИ:\n" + r.missed.map(o => `- Диалог #${o.sid}: ${o.what}`).join("\n") + "\n\n";
    text += `Сформировано: ${r.generatedAt}. Оценка по правилам, без ИИ.\n`;

    navigator.clipboard.writeText(text)
        .then(() => alert("Отчёт скопирован в буфер обмена"))
        .catch(() => prompt("Скопируйте текст отчёта:", text));
}
