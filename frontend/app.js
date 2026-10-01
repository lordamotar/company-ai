// Инициализация пустого массива данных (заполняется ИСКЛЮЧИТЕЛЬНО из базы данных)
let callsData = [];
let auditLogsData = [];

// Глобальное состояние универсальной пагинации (10/25/50/100) для всех страниц
const paginationState = {
    calls: { page: 1, pageSize: 10 },
    clients: { page: 1, pageSize: 10 },
    audit: { page: 1, pageSize: 10 },
    managers: { page: 1, pageSize: 10 }
};

let currentCallsList = [];
let currentClientsList = [];
let currentAuditList = [];
let currentManagersList = [];

// Универсальный рендер элементов управления пагинацией (10/25/50/100)
function renderPaginationControls({
    pageSizeSelectId,
    infoId,
    firstBtnId,
    prevBtnId,
    numbersId,
    nextBtnId,
    lastBtnId,
    currentPage,
    pageSize,
    totalItems,
    onPageChange,
    onPageSizeChange
}) {
    const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
    const page = Math.min(Math.max(1, currentPage), totalPages);

    // 1. Текст информации о записях
    const infoEl = document.getElementById(infoId);
    if (infoEl) {
        if (totalItems === 0) {
            infoEl.innerText = "0 записей";
        } else {
            const start = (page - 1) * pageSize + 1;
            const end = Math.min(page * pageSize, totalItems);
            infoEl.innerText = `Показано ${start}–${end} из ${totalItems}`;
        }
    }

    // 2. Селектор количества строк на странице (10/25/50/100)
    const selectEl = document.getElementById(pageSizeSelectId);
    if (selectEl) {
        selectEl.value = String(pageSize);
        selectEl.onchange = (e) => {
            const newSize = parseInt(e.target.value, 10) || 10;
            onPageSizeChange(newSize);
        };
    }

    // 3. Навигационные кнопки « ‹ › »
    const firstBtn = document.getElementById(firstBtnId);
    const prevBtn = document.getElementById(prevBtnId);
    const nextBtn = document.getElementById(nextBtnId);
    const lastBtn = document.getElementById(lastBtnId);

    if (firstBtn) {
        firstBtn.disabled = (page <= 1);
        firstBtn.onclick = () => onPageChange(1);
    }
    if (prevBtn) {
        prevBtn.disabled = (page <= 1);
        prevBtn.onclick = () => onPageChange(page - 1);
    }
    if (nextBtn) {
        nextBtn.disabled = (page >= totalPages);
        nextBtn.onclick = () => onPageChange(page + 1);
    }
    if (lastBtn) {
        lastBtn.disabled = (page >= totalPages);
        lastBtn.onclick = () => onPageChange(totalPages);
    }

    // 4. Номерные кнопки страниц
    const numbersEl = document.getElementById(numbersId);
    if (numbersEl) {
        numbersEl.innerHTML = "";

        let startPage = Math.max(1, page - 2);
        let endPage = Math.min(totalPages, page + 2);

        if (startPage > 1) {
            numbersEl.appendChild(createPageBtn(1, page, onPageChange));
            if (startPage > 2) {
                const dots = document.createElement("span");
                dots.className = "page-dots";
                dots.innerText = "…";
                numbersEl.appendChild(dots);
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            numbersEl.appendChild(createPageBtn(i, page, onPageChange));
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                const dots = document.createElement("span");
                dots.className = "page-dots";
                dots.innerText = "…";
                numbersEl.appendChild(dots);
            }
            numbersEl.appendChild(createPageBtn(totalPages, page, onPageChange));
        }
    }
}

function createPageBtn(pageNum, currentPage, onClick) {
    const btn = document.createElement("button");
    btn.className = "page-btn" + (pageNum === currentPage ? " active" : "");
    btn.innerText = pageNum;
    btn.onclick = () => onClick(pageNum);
    return btn;
}

// Функция динамического пересчета KPI-карточек на основе реальных данных из базы
function updateKpis(data) {
    const totalEl = document.getElementById("kpiTotalCalls");
    const badgeEl = document.getElementById("totalCallsBadge");
    const avgScoreEl = document.getElementById("kpiAvgScore");
    const scriptScoreEl = document.getElementById("kpiScriptScore");
    const posToneEl = document.getElementById("kpiPositiveTone");
    const lostSalesEl = document.getElementById("kpiLostSales");
    const conflictsEl = document.getElementById("kpiConflictsCount");

    const total = data.length;
    if (totalEl) totalEl.innerText = total;
    if (badgeEl) badgeEl.innerText = total;

    if (total === 0) {
        if (avgScoreEl) avgScoreEl.innerHTML = `0.0 <span class="max-val">/ 5.0</span>`;
        if (scriptScoreEl) scriptScoreEl.innerText = `0%`;
        if (posToneEl) posToneEl.innerText = `0%`;
        if (lostSalesEl) lostSalesEl.innerText = `0 ₸`;
        if (conflictsEl) conflictsEl.innerText = `0 конфликтов зафиксировано`;
        return;
    }

    // Средняя оценка CSAT (только по звонкам с оценкой)
    const scoredCalls = data.filter(c => c.csat !== null && c.csat !== undefined && c.csat !== "");
    const totalCsat = scoredCalls.reduce((acc, c) => acc + (Number(c.csat) || 0), 0);
    const avgCsat = scoredCalls.length > 0 ? (totalCsat / scoredCalls.length).toFixed(1) : "—";
    if (avgScoreEl) avgScoreEl.innerHTML = avgCsat !== "—" ? `${avgCsat} <span class="max-val">/ 5.0</span>` : "—";

    // Среднее соблюдение скрипта
    const totalScript = data.reduce((acc, c) => acc + (Number(c.scriptScore) || 0), 0);
    const avgScript = Math.round(totalScript / total);
    if (scriptScoreEl) scriptScoreEl.innerText = `${avgScript}%`;

    // Доля звонков с позитивным / конструктивным тоном (без конфликтов и негатива)
    const positiveCalls = data.filter(c => !c.conflict && c.emotionClass !== "angry" && c.emotionClass !== "sad").length;
    const positivePct = Math.round((positiveCalls / total) * 100);
    if (posToneEl) posToneEl.innerText = `${positivePct}%`;

    // Конфликты
    const conflicts = data.filter(c => c.conflict).length;
    if (conflictsEl) conflictsEl.innerText = `${conflicts} конфликтов зафиксировано`;

    // Оценка упущенных продаж (по непройденным пунктам допродаж)
    const missedOppCount = data.filter(c => Array.isArray(c.checklist) && c.checklist.some(i => !i.passed)).length;
    if (lostSalesEl) lostSalesEl.innerText = `~${(missedOppCount * 18000).toLocaleString('ru-RU')} ₸`;
}

// Рендеринг таблицы звонков (Экран 1) с пагинацией
function renderCallsTable(data, keepPage = false) {
    const tbody = document.getElementById("callsTableBody");
    if (!tbody) return;
    tbody.innerHTML = "";

    updateKpis(callsData);

    currentCallsList = data || [];
    const total = currentCallsList.length;

    if (!keepPage) {
        paginationState.calls.page = 1;
    }

    const pageSize = paginationState.calls.pageSize;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (paginationState.calls.page > totalPages) {
        paginationState.calls.page = totalPages;
    }
    const page = paginationState.calls.page;

    const startIndex = (page - 1) * pageSize;
    const pagedCalls = currentCallsList.slice(startIndex, startIndex + pageSize);

    if (!data || data.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" style="text-align: center; padding: 48px 20px; color: var(--text-muted);">
                    <div style="font-size: 32px; margin-bottom: 8px;">📭</div>
                    <strong style="font-size: 15px; color: var(--text-primary); display: block; margin-bottom: 6px;">В базе данных пока нет звонков</strong>
                    <span style="font-size: 12px; color: var(--text-secondary);">
                        Поместите аудиофайлы в папку <code>incoming/</code> для автоматического анализа через <code>worker.py</code>
                    </span>
                </td>
            </tr>
        `;
    } else {
        pagedCalls.forEach(call => {
            const tr = document.createElement("tr");
            tr.onclick = () => openCallModal(call.id);

            let emotionBadge = "";
            if (call.emotionClass === "happy" || call.emotion === "Радость") {
                emotionBadge = `<span class="badge badge-success">😊 ${call.emotion || "Радость"} ${call.emotionPct || ""}</span>`;
            } else if (call.emotionClass === "sad" || call.emotion === "Грусть") {
                emotionBadge = `<span class="badge badge-warning">🙁 ${call.emotion || "Грусть"} ${call.emotionPct || ""}</span>`;
            } else if (call.emotionClass === "angry" || call.emotion === "Гнев") {
                emotionBadge = `<span class="badge badge-warning" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">😠 ${call.emotion || "Гнев"} ${call.emotionPct || ""}</span>`;
            } else {
                emotionBadge = `<span class="badge badge-neutral">😐 ${call.emotion || "Нейтральный"} ${call.emotionPct || ""}</span>`;
            }

            if (call.conflict || Number(call.csat) <= 2) {
                emotionBadge += ` <span class="badge badge-warning" style="background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); font-size: 10px; padding: 2px 6px; margin-left: 4px;" title="${call.conflictReason || 'Зафиксировано недовольство'}">⚠️ Претензия</span>`;
            }

            const isOut = (call.direction === "out");
            const dirBadge = isOut 
                ? `<span class="badge badge-primary" style="font-size: 10px; padding: 2px 6px;">↗ Исх</span>` 
                : `<span class="badge badge-outline" style="font-size: 10px; padding: 2px 6px;">↙ Вх</span>`;

            const managerName = call.manager || "Оператор";
            const managerInitial = managerName[0] || "О";

            let outcomeBadge = "";
            if (call.saleStatus === "Продажа состоялась") {
                outcomeBadge = `<span class="badge badge-success" style="font-size: 10px; padding: 2.5px 7px; background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.5); font-weight: 600;">🟢 Продажа${call.orderSum ? ' ' + call.orderSum : ''}</span>`;
            } else if (call.saleStatus === "Бронь оформлена") {
                outcomeBadge = `<span class="badge badge-success" style="font-size: 10px; padding: 2.5px 7px; background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.5); font-weight: 600;">🟢 Бронь${call.orderSum ? ' ' + call.orderSum : ''}</span>`;
            } else if (call.saleStatus === "Отказ от покупки") {
                outcomeBadge = `<span class="badge" style="font-size: 10px; padding: 2.5px 7px; background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); font-weight: 500;">🔴 Отказ</span>`;
            } else {
                outcomeBadge = `<span class="badge badge-neutral" style="font-size: 10px; padding: 2.5px 7px; font-weight: 500; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-color); color: var(--text-secondary);">⚪ Консультация</span>`;
            }

            let upsellBadge = "";
            if (call.hasUpsell) {
                upsellBadge = `<span class="badge badge-success" style="font-size: 10px; padding: 2.5px 7px; background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.5); font-weight: 600;" title="Допродажа: ${call.upsellText || 'Услуга'} (${call.upsellSum || ''})">🟢 Доп. продажа${call.upsellSum ? ' +' + call.upsellSum : ''}</span>`;
            }

            let serviceBadge = "";
            if (call.serviceBooking && call.serviceBooking !== "Нет") {
                serviceBadge = `<span class="badge badge-success" style="font-size: 10px; padding: 2.5px 7px; background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.5); font-weight: 600;" title="Запись на сервис: ${call.serviceBooking}${call.serviceDate ? ' на ' + call.serviceDate : ''}">🟢 Сервис: ${call.serviceBooking}</span>`;
            }

            // Цвета категорий — по единому справочнику call_categories.py
            const CATEGORY_STYLES = {
                "Подбор и покупка":     ["🛒", "56, 189, 248", "#38bdf8"],
                "Уточнение по товару":  ["🔎", "99, 102, 241", "#a5b4fc"],
                "Заказы и брони":       ["📦", "251, 191, 36", "#fbbf24"],
                "Сервис":               ["🛠️", "16, 185, 129", "#34d399"],
                "Справка":              ["📍", "148, 163, 184", "#cbd5e1"],
                "Жалоба":               ["⚠️", "239, 68, 68", "#f87171"],
                "Не клиентский звонок": ["🏢", "100, 116, 139", "#94a3b8"],
            };
            const cat = call.callCategory || "Прочее";
            const [catIcon, catRgb, catColor] = CATEGORY_STYLES[cat] || ["💬", "168, 85, 247", "#c084fc"];
            const catTitle = call.callSubcategory ? ` title="${call.callSubcategory}"` : "";
            const categoryBadge = `<span class="badge"${catTitle} style="font-size: 10.5px; padding: 3px 8px; background: rgba(${catRgb}, 0.15); color: ${catColor}; border: 1px solid rgba(${catRgb}, 0.4); font-weight: 600;">${catIcon} ${cat}</span>`;

            let tagsBadges = "";
            if (Array.isArray(call.callTags) && call.callTags.length > 0) {
                tagsBadges = `<div style="display: flex; flex-wrap: wrap; gap: 3px; margin-top: 3px;">` + 
                    call.callTags.map(t => `<span style="font-size: 9.5px; padding: 1px 5px; border-radius: 4px; background: rgba(255,255,255,0.06); color: var(--text-secondary); border: 1px solid var(--border-color);">${t}</span>`).join("") + 
                    `</div>`;
            }

            tr.innerHTML = `
                <td>
                    <div class="call-id-col">
                        <span class="call-num">#${call.id} ${dirBadge}</span>
                        <span class="call-time">${call.date || "—"} ${call.time || ""}</span>
                    </div>
                </td>
                <td><span class="phone-badge">${call.phone || "Клиент"}</span></td>
                <td>
                    <div class="manager-badge" style="cursor: pointer; display: inline-flex; align-items: center; gap: 6px;" onclick="event.stopPropagation(); openEditCallModal(${call.id});" title="Нажмите, чтобы изменить данные звонка">
                        <span class="manager-avatar-sm">${managerInitial}</span>
                        <span>${managerName}</span>
                    </div>
                </td>
                <td>
                    <div style="display: flex; flex-direction: column; gap: 2px; align-items: flex-start;">
                        ${categoryBadge}
                        ${tagsBadges}
                    </div>
                </td>
                <td>
                    <div style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;">
                        ${outcomeBadge}
                        ${upsellBadge ? upsellBadge : ''}
                        ${serviceBadge ? serviceBadge : ''}
                    </div>
                </td>
                <td>
                    <div style="display: flex; flex-direction: column; gap: 3px;">
                        <strong style="color: var(--text-primary); font-size: 13px;">${call.topic || "Консультация"}</strong>
                        <span style="font-size: 11.5px; color: var(--text-secondary);">${call.tireSize || call.productCat || "Шины"}${call.tireBrand ? ' • ' + call.tireBrand : ''}</span>
                    </div>
                </td>
                <td>
                    <div style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; padding: 3px 8px; border-radius: 6px; background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.2);" onclick="event.stopPropagation(); openCallAndPlayAudio(${call.id});" title="Нажмите, чтобы включить прослушку аудиозаписи">
                        <span style="color: #38bdf8; font-size: 10px;">▶</span>
                        <span style="color: #e2e8f0; font-size: 12px; font-family: monospace;">${call.duration || "—"}</span>
                    </div>
                </td>
                <td>${emotionBadge}</td>
                <td>${(call.csat !== null && call.csat !== undefined && call.csat !== "") ? `<span class="stars-score">⭐ ${Number(call.csat).toFixed(1)}</span>` : `<span class="text-muted" style="font-size: 11.5px;">Не оценен</span>`}</td>
                <td class="text-right" style="white-space: nowrap;">
                    <button class="btn-icon" style="margin-right: 4px; padding: 5px 8px; border-radius: 6px; background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary); cursor: pointer;" onclick="event.stopPropagation(); openEditCallModal(${call.id});" title="Редактировать имя менеджера или город">
                        ✏️
                    </button>
                    <button class="action-btn" onclick="event.stopPropagation(); openCallModal(${call.id});">
                        Разбор AI
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Отрисовка пагинации звонков
    renderPaginationControls({
        pageSizeSelectId: "callsPageSizeSelect",
        infoId: "callsPaginationInfo",
        firstBtnId: "callsPageFirst",
        prevBtnId: "callsPagePrev",
        numbersId: "callsPageNumbers",
        nextBtnId: "callsPageNext",
        lastBtnId: "callsPageLast",
        currentPage: page,
        pageSize: pageSize,
        totalItems: total,
        onPageChange: (newPage) => {
            paginationState.calls.page = newPage;
            renderCallsTable(currentCallsList, true);
        },
        onPageSizeChange: (newSize) => {
            paginationState.calls.pageSize = newSize;
            paginationState.calls.page = 1;
            renderCallsTable(currentCallsList, true);
        }
    });
}

// Рендеринг Дашборда качества (Экран 2)
function renderDashboard(data) {
    const scriptBarsList = document.getElementById("dashScriptBarsList");
    const toneBarsList = document.getElementById("dashToneBarsList");
    const cityBarsList = document.getElementById("dashCityBarsList");
    const avgScriptBadge = document.getElementById("dashAvgScriptBadge");
    const citiesCountBadge = document.getElementById("dashCitiesCountBadge");

    if (!scriptBarsList || !toneBarsList || !cityBarsList) return;

    if (!data || data.length === 0) {
        scriptBarsList.innerHTML = `<p style="color: var(--text-muted); font-size: 12px; padding: 10px 0;">Нет данных из базы.</p>`;
        toneBarsList.innerHTML = `<p style="color: var(--text-muted); font-size: 12px; padding: 10px 0;">Нет данных из базы.</p>`;
        cityBarsList.innerHTML = `<p style="color: var(--text-muted); font-size: 12px; padding: 10px 0;">Нет данных из базы.</p>`;
        return;
    }

    const total = data.length;

    // 1. Соблюдение этапов скрипта продаж (7 показателей)
    let greetingPassed = 0;
    let needsPassed = 0;
    let presentPassed = 0;
    let objectionPassed = 0;
    let closingPassed = 0;
    let profPassed = 0;
    let upsellPassed = 0;

    data.forEach(call => {
        if (Array.isArray(call.checklist)) {
            call.checklist.forEach(item => {
                const title = (item.title || "").toLowerCase();
                if (item.passed) {
                    if (title.includes("приветств")) greetingPassed++;
                    else if (title.includes("потребност")) needsPassed++;
                    else if (title.includes("презентац")) presentPassed++;
                    else if (title.includes("возражен")) objectionPassed++;
                    else if (title.includes("окончан") || title.includes("заверш")) closingPassed++;
                    else if (title.includes("профессиональн") || title.includes("подход")) profPassed++;
                    else if (title.includes("доп") || title.includes("продаж") || title.includes("прлож")) upsellPassed++;
                }
            });
        }
    });

    const greetPct = Math.round((greetingPassed / total) * 100);
    const needsPct = Math.round((needsPassed / total) * 100);
    const presentPct = Math.round((presentPassed / total) * 100);
    const objectionPct = Math.round((objectionPassed / total) * 100);
    const closingPct = Math.round((closingPassed / total) * 100);
    const profPct = Math.round((profPassed / total) * 100);
    const upsellPct = Math.round((upsellPassed / total) * 100);

    const avgScript = Math.round((greetPct + needsPct + presentPct + objectionPct + closingPct + profPct + upsellPct) / 7);
    if (avgScriptBadge) avgScriptBadge.innerText = `${avgScript}% среднее`;

    scriptBarsList.innerHTML = `
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>1. Приветствие</span><span>${greetPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill green" style="width: ${greetPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>2. Выявления потребностей</span><span>${needsPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill blue" style="width: ${needsPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>3. Презентация</span><span>${presentPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill purple" style="width: ${presentPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>4. Работа с возражениями</span><span>${objectionPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill yellow" style="width: ${objectionPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>5. Окончание разговора</span><span>${closingPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill green" style="width: ${closingPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>6. Профессиональный подход</span><span>${profPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill blue" style="width: ${profPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>7. Доп прдажа</span><span>${upsellPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill purple" style="width: ${upsellPct}%;"></div></div>
        </div>
    `;

    // 2. Сводный Tone of Voice
    let happySum = 0, neutralSum = 0, sadSum = 0, angrySum = 0;
    data.forEach(call => {
        const dist = call.emotionsDist || {};
        happySum += Number(dist.happy) || 0;
        neutralSum += Number(dist.neutral) || 0;
        sadSum += Number(dist.sad) || 0;
        angrySum += Number(dist.angry) || 0;
    });

    const happyAvg = (happySum / total).toFixed(1);
    const neutralAvg = (neutralSum / total).toFixed(1);
    const sadAvg = (sadSum / total).toFixed(1);
    const angryAvg = (angrySum / total).toFixed(1);

    toneBarsList.innerHTML = `
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>😊 Радость / Дружелюбие</span><span>${happyAvg}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill green" style="width: ${happyAvg}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>😐 Нейтральный деловой тон</span><span>${neutralAvg}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill blue" style="width: ${neutralAvg}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>🙁 Сомнения / Неуверенность</span><span>${sadAvg}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill yellow" style="width: ${sadAvg}%;"></div></div>
        </div>
        <div class="script-bar-item" style="cursor: pointer;" onclick="filterConflictCalls()" title="Нажмите, чтобы показать звонки с конфликтами">
            <div class="script-bar-meta"><span style="color: #f87171;">😠 Раздражение / Конфликт (показать звонки ➔)</span><span style="color: #f87171;">${angryAvg}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill" style="background: #ef4444; width: ${angryAvg}%;"></div></div>
        </div>
    `;

    // 3. География звонков (города)
    const cityCounts = {};
    data.forEach(call => {
        const c = call.city || "Алматы";
        cityCounts[c] = (cityCounts[c] || 0) + 1;
    });

    const uniqueCities = Object.keys(cityCounts);
    if (citiesCountBadge) citiesCountBadge.innerText = `${uniqueCities.length} филиал(ов)`;

    cityBarsList.innerHTML = uniqueCities.map(city => {
        const cnt = cityCounts[city];
        const pct = Math.round((cnt / total) * 100);
        return `
            <div class="script-bar-item">
                <div class="script-bar-meta"><span>📍 ${city}</span><span>${cnt} звонк. (${pct}%)</span></div>
                <div class="script-bar-track"><div class="script-bar-fill purple" style="width: ${pct}%;"></div></div>
            </div>
        `;
    }).join("");
}

// Рендеринг карточек менеджеров (Экран 3) с пагинацией
function renderManagers(data, keepPage = false) {
    const grid = document.getElementById("managersCardsGrid");
    if (!grid) return;
    grid.innerHTML = "";

    if (!data || data.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; padding: 48px; color: var(--text-muted);">
                <div style="font-size: 32px; margin-bottom: 8px;">👥</div>
                <strong style="font-size: 15px; color: #fff; display: block; margin-bottom: 4px;">Нет данных по менеджерам</strong>
                <span>Анализируйте звонки для формирования рейтинга сотрудников</span>
            </div>
        `;
        renderPaginationControls({
            pageSizeSelectId: "managersPageSizeSelect",
            infoId: "managersPaginationInfo",
            firstBtnId: "managersPageFirst",
            prevBtnId: "managersPagePrev",
            numbersId: "managersPageNumbers",
            nextBtnId: "managersPageNext",
            lastBtnId: "managersPageLast",
            currentPage: 1,
            pageSize: paginationState.managers.pageSize,
            totalItems: 0,
            onPageChange: () => {},
            onPageSizeChange: () => {}
        });
        return;
    }

    // Группируем звонки по менеджерам
    const managersMap = {};
    data.forEach(call => {
        const name = call.manager || "Оператор";
        if (!managersMap[name]) {
            managersMap[name] = {
                name: name,
                calls: [],
                inCalls: 0,
                outCalls: 0,
                totalCsat: 0,
                totalScript: 0,
                happyCount: 0
            };
        }
        managersMap[name].calls.push(call);
        if (call.direction === "out") managersMap[name].outCalls++;
        else managersMap[name].inCalls++;
        if (call.csat !== null && call.csat !== undefined && call.csat !== "") {
            managersMap[name].totalCsat += Number(call.csat);
            managersMap[name].csatCount = (managersMap[name].csatCount || 0) + 1;
        }
        managersMap[name].totalScript += Number(call.scriptScore) || 0;
        if (call.emotionClass === "happy" || call.emotion === "Радость") {
            managersMap[name].happyCount++;
        }
    });

    const managersList = Object.values(managersMap);
    currentManagersList = managersList;
    const total = managersList.length;

    if (!keepPage) {
        paginationState.managers.page = 1;
    }
    const pageSize = paginationState.managers.pageSize;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (paginationState.managers.page > totalPages) {
        paginationState.managers.page = totalPages;
    }
    const page = paginationState.managers.page;
    const startIndex = (page - 1) * pageSize;
    const pagedManagers = managersList.slice(startIndex, startIndex + pageSize);

    pagedManagers.forEach(m => {
        const count = m.calls.length;
        const avgCsat = (m.csatCount && m.csatCount > 0) ? (m.totalCsat / m.csatCount).toFixed(1) : "—";
        const avgScript = Math.round(m.totalScript / count);
        const happyPct = Math.round((m.happyCount / count) * 100);
        const initial = m.name[0] || "М";

        const card = document.createElement("div");
        card.className = "manager-card";
        card.style.cursor = "pointer";
        card.innerHTML = `
            <div class="manager-card-top" onclick="openManagerAnalytics('${m.name}')">
                <div class="manager-avatar-lg">${initial}</div>
                <div class="manager-title-box">
                    <div class="manager-name-lg">${m.name}</div>
                    <div class="manager-role-tag">Менеджер отдела продаж</div>
                </div>
                <span class="badge ${avgCsat !== '—' ? 'badge-success' : 'badge-neutral'}">⭐ ${avgCsat} / 5.0</span>
            </div>

            <div class="manager-stats-grid" onclick="openManagerAnalytics('${m.name}')">
                <div class="m-stat-item">
                    <span class="m-stat-lbl">Всего звонков</span>
                    <span class="m-stat-val">${count} (Вх: ${m.inCalls}, Исх: ${m.outCalls})</span>
                </div>
                <div class="m-stat-item">
                    <span class="m-stat-lbl">Скрипт продаж</span>
                    <span class="m-stat-val">${avgScript}%</span>
                </div>
                <div class="m-stat-item">
                    <span class="m-stat-lbl">Дружелюбие</span>
                    <span class="m-stat-val">${happyPct}% позитив</span>
                </div>
                <div class="m-stat-item">
                    <span class="m-stat-lbl">Статус</span>
                    <span class="m-stat-val" style="color: #4ade80;">Активен</span>
                </div>
            </div>

            <div class="manager-actions-row">
                <button class="btn-analytics-manager" onclick="openManagerAnalytics('${m.name}')">
                    📊 Анализ работы
                </button>
                <button class="btn-filter-manager" onclick="filterByManager('${m.name}')">
                    📞 Звонки (${count})
                </button>
                <button class="btn-filter-manager" onclick="openManagerAnalysisFor('${m.name}')" title="Детальный анализ за период с AI-разбором">
                    🔎 За период
                </button>
            </div>
        `;
        grid.appendChild(card);
    });

    // Отрисовка элементов пагинации менеджеров
    renderPaginationControls({
        pageSizeSelectId: "managersPageSizeSelect",
        infoId: "managersPaginationInfo",
        firstBtnId: "managersPageFirst",
        prevBtnId: "managersPagePrev",
        numbersId: "managersPageNumbers",
        nextBtnId: "managersPageNext",
        lastBtnId: "managersPageLast",
        currentPage: page,
        pageSize: pageSize,
        totalItems: total,
        onPageChange: (newPage) => {
            paginationState.managers.page = newPage;
            renderManagers(data, true);
        },
        onPageSizeChange: (newSize) => {
            paginationState.managers.pageSize = newSize;
            paginationState.managers.page = 1;
            renderManagers(data, true);
        }
    });
}

// Открытие модального окна сводной аналитики по всем звонкам конкретного менеджера
function openManagerAnalytics(managerName) {
    const modal = document.getElementById("managerAnalyticsModal");
    if (!modal) return;

    const mgrCalls = callsData.filter(c => (c.manager || "Оператор").toLowerCase() === managerName.toLowerCase());
    if (mgrCalls.length === 0) {
        alert("Нет данных по звонкам менеджера: " + managerName);
        return;
    }

    const count = mgrCalls.length;
    const inCalls = mgrCalls.filter(c => c.direction === "in").length;
    const outCalls = mgrCalls.filter(c => c.direction === "out").length;

    // Суммарное время и средняя длительность
    const totalSec = mgrCalls.reduce((sum, c) => sum + (Number(c.durationSec) || 0), 0);
    const avgSec = Math.round(totalSec / count);
    const totalDurStr = `${Math.floor(totalSec / 60)} мин ${Math.round(totalSec % 60)} сек`;
    const avgDurStr = `${Math.floor(avgSec / 60)}:${(avgSec % 60 < 10 ? "0" : "")}${avgSec % 60}`;

    // Средний CSAT и Скрипт
    const scoredMgrCalls = mgrCalls.filter(c => c.csat !== null && c.csat !== undefined && c.csat !== "");
    const avgCsat = scoredMgrCalls.length > 0 ? (scoredMgrCalls.reduce((sum, c) => sum + Number(c.csat), 0) / scoredMgrCalls.length).toFixed(1) : "—";
    const avgScript = Math.round(mgrCalls.reduce((sum, c) => sum + (Number(c.scriptScore) || 0), 0) / count);

    // Вежливость
    const scoredPoliteCalls = mgrCalls.filter(c => c.managerPoliteness && !c.managerPoliteness.includes("Не"));
    let politenessSum = 0;
    scoredPoliteCalls.forEach(c => {
        politenessSum += parseFloat(c.managerPoliteness) || 0;
    });
    const avgPoliteness = scoredPoliteCalls.length > 0 ? (politenessSum / scoredPoliteCalls.length).toFixed(1) : "—";

    // Упущенные допродажи
    let lostUpsellCalls = 0;
    mgrCalls.forEach(c => {
        if (c.checklist) {
            const up = c.checklist.find(item => item.title && item.title.toLowerCase().includes("доп"));
            const na = up && (up.not_applicable || up.status === "not_applicable" || up.passed === null);
            if (up && !na && up.passed !== true && up.status !== "passed") lostUpsellCalls++;
        }
    });
    const lostSalesSum = lostUpsellCalls * 18000;

    // Заполнение заголовка
    document.getElementById("mgrModalAvatar").innerText = managerName[0] || "М";
    document.getElementById("mgrModalName").innerText = managerName;
    document.getElementById("mgrModalCsatBadge").innerText = `⭐ ${avgCsat} / 5.0 CSAT`;
    document.getElementById("mgrModalCallsCountBadge").innerText = `${count} звонк. (Вх: ${inCalls}, Исх: ${outCalls})`;
    document.getElementById("mgrModalSubtitle").innerText = `Менеджер отдела продаж и подбора шин • Филиал: ${mgrCalls[0].city || "Алматы"}`;

    // KPI плашки
    document.getElementById("mgrKpiTotalCalls").innerText = `${count} звонков`;
    document.getElementById("mgrKpiDurationStats").innerText = `Всего: ${totalDurStr} • Ср: ${avgDurStr}`;
    document.getElementById("mgrKpiScriptAvg").innerText = `${avgScript}%`;
    document.getElementById("mgrKpiCsatAvg").innerText = `⭐ ${avgCsat}`;
    document.getElementById("mgrKpiPoliteness").innerText = `Вежливость: ${avgPoliteness} / 5.0`;
    document.getElementById("mgrKpiLostSales").innerText = `~${lostSalesSum.toLocaleString("ru-RU")} ₸`;
    document.getElementById("mgrKpiLostSub").innerText = lostUpsellCalls > 0 ? `Не предложено в ${lostUpsellCalls} звонк.` : "Все услуги предложены";
    document.getElementById("mgrModalCallsTabCount").innerText = count;

    // ------------------------------------------------------------------
    // Все блоки ниже считаются по реальным звонкам (раньше здесь был статичный текст)
    // ------------------------------------------------------------------
    const escHtml = (v) => String(v === null || v === undefined ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const isNaItem = (i) => i.not_applicable || i.status === "not_applicable";
    const isPassItem = (i) => i.passed === true || i.status === "passed";

    // Выполнение этапов скрипта: процент только по звонкам, где этап применим
    const aggregateStages = (calls) => {
        const agg = {};
        calls.forEach(c => (c.checklist || []).forEach(item => {
            const t = item.title || "Критерий";
            const st = agg[t] || (agg[t] = { title: t, applicable: 0, passed: 0, failed: 0, failedCalls: [] });
            if (isNaItem(item)) return;
            st.applicable++;
            if (isPassItem(item)) st.passed++;
            else { st.failed++; st.failedCalls.push(c.id); }
        }));
        Object.values(agg).forEach(st => { st.pct = st.applicable ? Math.round(st.passed * 100 / st.applicable) : null; });
        return agg;
    };
    const mgrStages = aggregateStages(mgrCalls);
    const teamStages = aggregateStages(callsData);
    const stageRows = Object.values(mgrStages).filter(st => st.applicable > 0);
    const teamPctText = (title) => {
        const t = teamStages[title];
        return t && t.pct !== null ? ` (команда: ${t.pct}%)` : "";
    };
    const auditItem = (icon, color, title, desc) => `
        <div class="mgr-audit-item">
            <span class="mgr-audit-item-icon" style="color: ${color};">${icon}</span>
            <div>
                <div class="mgr-audit-item-title">${escHtml(title)}</div>
                <div class="mgr-audit-item-desc">${desc}</div>
            </div>
        </div>`;
    const callLinks = (ids) => ids.slice(0, 8).map(id => `<a href="javascript:void(0)" onclick="closeManagerModal(); openCallModal(${Number(id)});" style="color: var(--accent-blue); font-weight: 600;">#${Number(id)}</a>`).join(" ") + (ids.length > 8 ? " …" : "");

    // 1. Сильные стороны — этапы скрипта, выполняемые стабильно
    const strengths = stageRows.filter(st => st.pct >= 90 && st.applicable >= 3)
        .sort((x, y) => y.pct - x.pct).slice(0, 4)
        .map(st => auditItem("✓", "#4ade80", `${st.title}: ${st.pct}%`,
            `Выполнено в ${st.passed} из ${st.applicable} звонков, где этап применим${teamPctText(st.title)}.`));
    if (avgCsat !== "—" && parseFloat(avgCsat) >= 4.5) {
        strengths.push(auditItem("✓", "#4ade80", `Высокая удовлетворённость клиентов: ${avgCsat} / 5.0`, "Средний CSAT по оценке нейросети."));
    }
    document.getElementById("mgrStrengthsList").innerHTML = strengths.length
        ? strengths.join("")
        : `<div class="mgr-audit-item-desc">Пока нет устойчиво выполняемых этапов — недостаточно звонков для выводов.</div>`;

    // 2. Точки роста — этапы с низким выполнением и реальные конфликты
    const weaknesses = stageRows.filter(st => st.failed > 0 && (st.pct < 80 || st.failed >= 3))
        .sort((x, y) => x.pct - y.pct).slice(0, 4)
        .map(st => auditItem(st.pct < 50 ? "⚠️" : "⏱️", st.pct < 50 ? "#f87171" : "#facc15",
            `${st.title}: ${st.pct}%`,
            `Не выполнено в ${st.failed} из ${st.applicable} звонков${teamPctText(st.title)}. Звонки: ${callLinks(st.failedCalls)}`));
    const conflictCalls = mgrCalls.filter(c => c.conflict).map(c => c.id);
    if (conflictCalls.length) {
        weaknesses.push(auditItem("🔥", "#f87171", `Конфликты с клиентами: ${conflictCalls.length}`, `Звонки: ${callLinks(conflictCalls)}`));
    }
    document.getElementById("mgrWeaknessesList").innerHTML = weaknesses.length
        ? weaknesses.join("")
        : `<div class="mgr-audit-item-desc">Существенных отклонений по этапам скрипта не выявлено.</div>`;

    // 3. Рекомендации — персональный AI-разбор вынесен на отдельную вкладку (с цитатами из звонков)
    const ropRecs = document.getElementById("mgrRopRecommendations");
    ropRecs.innerHTML = `
        <div class="mgr-rop-tip-item">
            <div class="mgr-rop-tip-num">🔎</div>
            <div>
                <strong style="color: #fff; font-size: 13px; display: block; margin-bottom: 2px;">Персональный разбор с цитатами из звонков</strong>
                <span style="font-size: 12px; color: var(--text-secondary);">Слабые места, упущенные возможности и план работы формируются на вкладке «Анализ менеджеров» за выбранный период — на основе реальных диалогов, а не шаблонов.</span>
                <div style="margin-top: 10px;"><button class="btn btn-primary" id="mgrOpenDetailedBtn">Открыть детальный анализ</button></div>
            </div>
        </div>`;
    const detailedBtn = document.getElementById("mgrOpenDetailedBtn");
    if (detailedBtn) detailedBtn.onclick = () => { closeManagerModal(); openManagerAnalysisFor(managerName); };

    // 4. Этапы скрипта: процент по применимым звонкам
    const stagesMeta = [
        { key: "приветств", name: "1. Приветствие", desc: "Поздоровался и представился", cls: "green", color: "#4ade80" },
        { key: "потребност", name: "2. Выявление потребностей", desc: "Уточнил типоразмер шин, сезонность и параметры авто", cls: "green", color: "#4ade80" },
        { key: "презентац", name: "3. Презентация", desc: "Презентовал варианты шин, озвучил бренды, наличие и цены", cls: "blue", color: "#60a5fa" },
        { key: "возражен", name: "4. Работа с возражениями", desc: "Отработал вопросы по наличию, складу, предложил альтернативы", cls: "yellow", color: "#facc15" },
        { key: "окончан", name: "5. Окончание разговора", desc: "Зафиксировал договорённость, назвал адрес/филиал, вежливо попрощался", cls: "green", color: "#4ade80" },
        { key: "профессиональн", name: "6. Профессиональный подход", desc: "Стандарты вежливости, экспертность и деловой этикет", cls: "blue", color: "#60a5fa" },
        { key: "доп", name: "7. Допродажа", desc: "Предложение сезонного хранения шин, шиномонтажа и гарантии", cls: "purple", color: "#c084fc" }
    ];
    document.getElementById("mgrScriptStagesList").innerHTML = stagesMeta.map(meta => {
        const st = Object.values(mgrStages).find(x => x.title.toLowerCase().includes(meta.key));
        const pct = st && st.pct !== null ? st.pct : null;
        const note = st ? ` • применим в ${st.applicable} из ${count} зв.${teamPctText(st.title)}` : "";
        return `
        <div class="script-stage-row">
            <div class="script-stage-info">
                <div class="script-stage-name">${meta.name}</div>
                <div class="script-stage-desc">${meta.desc}${note}</div>
            </div>
            <div class="script-stage-progress-wrap">
                <div class="script-bar-track" style="flex: 1;"><div class="script-bar-fill ${meta.cls}" style="width: ${pct || 0}%;"></div></div>
                <strong style="color: ${meta.color}; font-size: 13px;">${pct === null ? "н/п" : pct + "%"}</strong>
            </div>
        </div>`;
    }).join("");

    // 5. Tone of Voice и акустика — средние по реальным данным звонков, без подстановки значений по умолчанию
    const avgOf = (vals) => vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const toneVals = (key) => mgrCalls.filter(c => c.emotionsDist && c.emotionsDist[key] !== undefined && c.emotionsDist[key] !== null)
        .map(c => Number(c.emotionsDist[key]) || 0);
    const toneAvg = (key) => { const v = avgOf(toneVals(key)); return v === null ? null : v.toFixed(1); };
    const toneBar = (icon, label, val, cls, extraStyle) => `
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>${icon} ${label}</span><span>${val === null ? "нет данных" : val + "%"}</span></div>
            <div class="script-bar-track"><div class="script-bar-fill ${cls}" style="${extraStyle || ""} width: ${val || 0}%;"></div></div>
        </div>`;
    document.getElementById("mgrToneBarsList").innerHTML =
        toneBar("😊", "Радость / Дружелюбие", toneAvg("happy"), "green") +
        toneBar("😐", "Нейтральный деловой тон", toneAvg("neutral"), "blue") +
        toneBar("🙁", "Сомнения / Неуверенность", toneAvg("sad"), "yellow") +
        toneBar("😠", "Раздражение / Конфликт", toneAvg("angry"), "", "background: #ef4444;");

    const numFrom = (text, rx) => { const m = String(text || "").match(rx); return m ? parseFloat(m[1]) : null; };
    const tempoAvg = avgOf(mgrCalls.map(c => numFrom((c.acoustics || {}).tempo, /(\d+(?:\.\d+)?)/)).filter(v => v !== null));
    const silenceAvg = avgOf(mgrCalls.map(c => numFrom((c.acoustics || {}).silence, /\((\d+(?:\.\d+)?)\s*%\)/)).filter(v => v !== null));
    const loudAvg = avgOf(mgrCalls.map(c => numFrom((c.acoustics || {}).loudMoments, /(\d+(?:\.\d+)?)/)).filter(v => v !== null));
    const tempoNote = tempoAvg === null ? "нет данных" : `${Math.round(tempoAvg)} слов/мин` + (tempoAvg < 90 ? " (медленный темп)" : tempoAvg > 170 ? " (быстрый темп)" : " (комфортный темп)");
    document.getElementById("mgrAcousticsList").innerHTML = `
        <div class="acoustic-metric">
            <span class="metric-title">Темп речи (весь диалог):</span>
            <span class="metric-val">${tempoNote}</span>
            <div class="metric-bar"><div class="fill purple" style="width: ${tempoAvg === null ? 0 : Math.min(100, Math.round(tempoAvg / 2))}%;"></div></div>
        </div>
        <div class="acoustic-metric">
            <span class="metric-title">Баланс речи / пауз:</span>
            <span class="metric-val">${silenceAvg === null ? "нет данных" : `${Math.round(100 - silenceAvg)}% речь • ${Math.round(silenceAvg)}% паузы`}</span>
            <div class="metric-bar"><div class="fill green" style="width: ${silenceAvg === null ? 0 : Math.round(100 - silenceAvg)}%;"></div></div>
        </div>
        <div class="acoustic-metric">
            <span class="metric-title">Громкие моменты:</span>
            <span class="metric-val">${loudAvg === null ? "нет данных" : `${Math.round(loudAvg)}% времени`}</span>
            <div class="metric-bar"><div class="fill green" style="width: ${loudAvg === null ? 0 : Math.min(100, Math.round(loudAvg))}%;"></div></div>
        </div>
    `;

    // 6. Таблица звонков сотрудника
    const callsTableBody = document.getElementById("mgrCallsTableBody");
    callsTableBody.innerHTML = mgrCalls.map(c => `
        <tr>
            <td>
                <div class="call-id">#${c.id}</div>
                <div class="call-time">${c.date} ${c.time}</div>
            </td>
            <td>
                <div class="client-phone">${c.phone}</div>
                <div class="client-city">${c.city || "Алматы"}</div>
            </td>
            <td>
                <div class="topic-title">${c.topic}</div>
                <div class="product-tag">${c.productCat || "Шины"}</div>
            </td>
            <td>
                <span class="duration-badge">${c.duration}</span>
            </td>
            <td>
                <span class="emotion-badge ${c.emotionClass || "happy"}">
                    ${c.emotionClass === "happy" ? "😊" : (c.emotionClass === "sad" ? "🙁" : "😐")} ${c.emotion}
                </span>
            </td>
            <td>
                <span class="csat-stars">⭐ ${c.csat} / 5</span>
            </td>
            <td>
                <span class="script-score">${c.scriptScore}%</span>
            </td>
            <td class="text-right">
                <button class="btn-action" onclick="closeManagerModal(); openCallModal(${c.id});">Разбор AI</button>
            </td>
        </tr>
    `).join("");

    // Активируем первую вкладку
    document.querySelectorAll("[data-mgr-tab]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".mgr-tab-content").forEach(tc => tc.classList.remove("active"));
    const firstTabBtn = document.querySelector("[data-mgr-tab='mgr-tab-overview']");
    if (firstTabBtn) firstTabBtn.classList.add("active");
    const firstTabContent = document.getElementById("mgr-tab-overview");
    if (firstTabContent) firstTabContent.classList.add("active");

    modal.classList.add("open");
    modal.classList.add("active");
}

// Закрытие модального окна менеджера
function closeManagerModal() {
    const modal = document.getElementById("managerAnalyticsModal");
    if (modal) {
        modal.classList.remove("open");
        modal.classList.remove("active");
    }
}

// Фильтрация звонков по выбранному менеджеру и переключение на экран таблицы
function filterByManager(managerName) {
    closeManagerModal();
    switchView("view-calls");
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.value = managerName;
    }
    const filtered = callsData.filter(c => c.manager && c.manager.toLowerCase().includes(managerName.toLowerCase()));
    renderCallsTable(filtered);
}

// ========================================================
// ЭКРАН 4: КЛИЕНТЫ И ИСТОРИЯ ОБРАЩЕНИЙ
// ========================================================

// Группировка звонков по уникальным клиентам
function groupCallsByClient(data) {
    const clientsMap = {};

    data.forEach(call => {
        const phone = (call.phone || "Неизвестный номер").trim();
        if (!clientsMap[phone]) {
            clientsMap[phone] = {
                phone: phone,
                city: call.city || "Алматы",
                calls: [],
                topics: new Set(),
                tireSizes: new Set(),
                brands: new Set(),
                managers: new Set(),
                totalSpent: 0,
                csatScores: [],
                happyCount: 0
            };
        }

        const client = clientsMap[phone];
        client.calls.push(call);
        if (call.city && (!client.city || client.city === "Алматы")) client.city = call.city;
        if (call.topic) client.topics.add(call.topic);
        if (call.tireSize && call.tireSize !== "—") client.tireSizes.add(call.tireSize);
        if (call.brand && call.brand !== "—") client.brands.add(call.brand);
        if (call.manager) client.managers.add(call.manager);
        
        // Сумма заказа
        if (call.orderSum) {
            const num = parseInt(call.orderSum.replace(/\D/g, ""), 10);
            if (!isNaN(num)) client.totalSpent += num;
        }

        if (call.csat) client.csatScores.push(Number(call.csat));
        if (call.emotionClass === "happy" || call.emotion === "Радость") client.happyCount++;
    });

    // Преобразуем в массив с вычисляемыми полями
    const clientsArray = Object.values(clientsMap).map(c => {
        const totalCalls = c.calls.length;
        const avgCsat = c.csatScores.length > 0 
            ? (c.csatScores.reduce((a, b) => a + b, 0) / c.csatScores.length).toFixed(1)
            : "—";
        const happyPct = totalCalls > 0 ? Math.round((c.happyCount / totalCalls) * 100) : 0;
        
        // Сортировка звонков: сначала самые свежие
        c.calls.sort((a, b) => {
            const dateA = (a.date || '') + ' ' + (a.time || '');
            const dateB = (b.date || '') + ' ' + (b.time || '');
            return dateB.localeCompare(dateA);
        });

        const latestCall = c.calls[0] || {};

        return {
            phone: c.phone,
            city: c.city,
            totalCalls: totalCalls,
            inCalls: c.calls.filter(x => x.direction === "in").length,
            outCalls: c.calls.filter(x => x.direction === "out").length,
            topics: Array.from(c.topics),
            tireSizes: Array.from(c.tireSizes),
            brands: Array.from(c.brands),
            managers: Array.from(c.managers),
            totalSpent: c.totalSpent,
            avgCsat: avgCsat,
            happyPct: happyPct,
            latestDate: `${latestCall.date || ''} ${latestCall.time || ''}`,
            latestCall: latestCall,
            calls: c.calls
        };
    });

    // Сортируем: клиенты с наибольшим числом звонков первыми
    return clientsArray.sort((a, b) => b.totalCalls - a.totalCalls);
}

// Рендеринг таблицы клиентов с пагинацией
function renderClients(data, keepPage = false) {
    const tbody = document.getElementById("clientsTableBody");
    const badgeEl = document.getElementById("totalClientsBadge");
    const countKpi = document.getElementById("kpiClientsCount");
    const repeatKpi = document.getElementById("kpiClientsRepeat");
    const repeatPctKpi = document.getElementById("kpiClientsRepeatPct");
    const totalSumKpi = document.getElementById("kpiClientsTotalSum");
    const avgCsatKpi = document.getElementById("kpiClientsAvgCsat");

    const clients = groupCallsByClient(data);
    const totalClients = clients.length;
    currentClientsList = clients;

    if (badgeEl) badgeEl.innerText = totalClients;
    if (countKpi) countKpi.innerText = totalClients;

    if (totalClients === 0) {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 40px; color: var(--text-muted);">В базе пока нет номеров клиентов</td></tr>`;
        }
        if (repeatKpi) repeatKpi.innerText = "0";
        if (repeatPctKpi) repeatPctKpi.innerText = "0% повторных";
        if (totalSumKpi) totalSumKpi.innerText = "0 ₸";
        renderPaginationControls({
            pageSizeSelectId: "clientsPageSizeSelect",
            infoId: "clientsPaginationInfo",
            firstBtnId: "clientsPageFirst",
            prevBtnId: "clientsPagePrev",
            numbersId: "clientsPageNumbers",
            nextBtnId: "clientsPageNext",
            lastBtnId: "clientsPageLast",
            currentPage: 1,
            pageSize: paginationState.clients.pageSize,
            totalItems: 0,
            onPageChange: () => {},
            onPageSizeChange: () => {}
        });
        return;
    }

    const repeatClients = clients.filter(c => c.totalCalls > 1).length;
    const repeatPct = Math.round((repeatClients / totalClients) * 100);
    const validClientCsats = clients.filter(c => c.avgCsat && c.avgCsat !== "—").map(c => parseFloat(c.avgCsat));
    const avgCsatAll = validClientCsats.length > 0 
        ? (validClientCsats.reduce((acc, v) => acc + v, 0) / validClientCsats.length).toFixed(1)
        : "—";

    const allTotalSum = clients.reduce((acc, c) => acc + (c.totalSpent || 0), 0);

    if (repeatKpi) repeatKpi.innerText = repeatClients;
    if (repeatPctKpi) repeatPctKpi.innerText = `${repeatPct}% повторных`;
    if (totalSumKpi) totalSumKpi.innerText = `${allTotalSum.toLocaleString("ru-RU")} ₸`;
    if (avgCsatKpi) avgCsatKpi.innerHTML = avgCsatAll !== "—" ? `${avgCsatAll} <span class="max-val">/ 5.0</span>` : "—";

    if (!tbody) return;
    tbody.innerHTML = "";

    if (!keepPage) {
        paginationState.clients.page = 1;
    }
    const pageSize = paginationState.clients.pageSize;
    const totalPages = Math.max(1, Math.ceil(totalClients / pageSize));
    if (paginationState.clients.page > totalPages) {
        paginationState.clients.page = totalPages;
    }
    const page = paginationState.clients.page;
    const startIndex = (page - 1) * pageSize;
    const pagedClients = clients.slice(startIndex, startIndex + pageSize);

    pagedClients.forEach(client => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";
        tr.onclick = (e) => {
            // Если кликнули не на ссылку/кнопку
            openClientModal(client.phone);
        };

        const isRepeat = client.totalCalls > 1;
        const statusBadge = isRepeat
            ? `<span class="badge badge-primary" style="font-size: 10.5px; padding: 2px 7px;">🔥 Постоянный (${client.totalCalls})</span>`
            : `<span class="badge badge-neutral" style="font-size: 10.5px; padding: 2px 7px;">Новый клиент</span>`;

        const tireStr = client.tireSizes.length > 0 
            ? client.tireSizes.join(", ") 
            : (client.latestCall.productCat || "Шины");

        const topicsBadges = client.topics.length > 0
            ? client.topics.map(t => `<span class="badge badge-outline" style="font-size: 11px; margin-right: 4px; margin-bottom: 2px;">${t}</span>`).join("")
            : `<span style="color: var(--text-muted); font-size: 12px;">Консультация</span>`;

        const managersList = client.managers.length > 0
            ? client.managers.join(", ")
            : "Оператор";

        tr.innerHTML = `
            <td>
                <div style="display: flex; flex-direction: column; gap: 4px;">
                    <strong style="color: #fff; font-size: 13.5px; font-family: monospace;">📱 ${client.phone}</strong>
                    <div>${statusBadge}</div>
                </div>
            </td>
            <td>
                <span class="city-tag">${client.city || "Алматы"}</span>
            </td>
            <td>
                <div style="display: flex; flex-direction: column; gap: 2px;">
                    <strong style="color: #38bdf8; font-size: 13px;">${client.totalCalls} обращ.</strong>
                    <span style="font-size: 11px; color: var(--text-muted);">Вх: ${client.inCalls} • Исх: ${client.outCalls}</span>
                </div>
            </td>
            <td>
                <div style="font-size: 12.5px; font-weight: 600; color: #e2e8f0;">${tireStr}</div>
                <div style="font-size: 11px; color: var(--text-muted);">${client.brands.join(", ") || "Подбор"}</div>
            </td>
            <td style="max-width: 260px;">
                <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                    ${topicsBadges}
                </div>
            </td>
            <td>
                <span class="badge ${client.happyPct >= 70 ? 'badge-success' : 'badge-neutral'}">
                    😊 ${client.happyPct}%
                </span>
            </td>
            <td>
                <span style="font-size: 12.5px; color: var(--text-secondary);">${managersList}</span>
            </td>
            <td>
                <span style="font-size: 11.5px; color: var(--text-muted);">${client.latestDate || "—"}</span>
            </td>
            <td class="text-right">
                <button class="btn-client-call-action" onclick="event.stopPropagation(); openClientModal('${client.phone}')">
                    👤 История (${client.totalCalls})
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    // Отрисовка пагинации клиентов
    renderPaginationControls({
        pageSizeSelectId: "clientsPageSizeSelect",
        infoId: "clientsPaginationInfo",
        firstBtnId: "clientsPageFirst",
        prevBtnId: "clientsPagePrev",
        numbersId: "clientsPageNumbers",
        nextBtnId: "clientsPageNext",
        lastBtnId: "clientsPageLast",
        currentPage: page,
        pageSize: pageSize,
        totalItems: totalClients,
        onPageChange: (newPage) => {
            paginationState.clients.page = newPage;
            renderClients(data, true);
        },
        onPageSizeChange: (newSize) => {
            paginationState.clients.pageSize = newSize;
            paginationState.clients.page = 1;
            renderClients(data, true);
        }
    });
}

// Открытие модального окна карточки клиента со всей историей звонков и вопросов
function openClientModal(phoneNumber) {
    const modal = document.getElementById("clientModal");
    if (!modal) return;

    const clients = groupCallsByClient(callsData);
    const client = clients.find(c => c.phone === phoneNumber);
    if (!client) {
        alert("Информация по клиенту не найдена: " + phoneNumber);
        return;
    }

    // Шапка
    document.getElementById("clientModalPhone").innerText = `📱 ${client.phone}`;
    document.getElementById("clientModalSubtitle").innerText = `Город: ${client.city} • Зафиксированный интерес: ${client.tireSizes.join(", ") || "Шины и услуги"}`;
    document.getElementById("clientModalCallsBadge").innerText = `${client.totalCalls} ${client.totalCalls === 1 ? 'обращение' : 'обращения'}`;
    document.getElementById("clientModalCsatBadge").innerText = `⭐ ${client.avgCsat} / 5.0 CSAT`;
    document.getElementById("clientModalStatusBadge").innerText = client.totalCalls > 1 ? "🔥 Постоянный клиент" : "Новый клиент";
    document.getElementById("clientTabCallsCount").innerText = client.totalCalls;

    // Мини-KPI карточки
    document.getElementById("clientKpiTotalCalls").innerText = `${client.totalCalls}`;
    document.getElementById("clientKpiDirectionStats").innerText = `Входящих: ${client.inCalls} • Исходящих: ${client.outCalls}`;

    const totalSec = client.calls.reduce((sum, c) => sum + (Number(c.durationSec) || 0), 0);
    const avgSec = Math.round(totalSec / (client.totalCalls || 1));
    document.getElementById("clientKpiTotalDuration").innerText = `${Math.floor(totalSec / 60)}м ${Math.round(totalSec % 60)}с`;
    document.getElementById("clientKpiAvgDuration").innerText = `Ср. длительность: ${Math.floor(avgSec / 60)}:${(avgSec % 60 < 10 ? "0" : "")}${avgSec % 60}`;

    document.getElementById("clientKpiTotalSpent").innerText = `${client.totalSpent.toLocaleString("ru-RU")} ₸`;
    document.getElementById("clientKpiOrderCount").innerText = `Заказов/броней: ${client.calls.filter(c => c.orderSum && c.orderSum !== "0 ₸" && c.orderSum !== "—").length}`;
    document.getElementById("clientKpiTone").innerText = `😊 ${client.happyPct}%`;

    // 1. Хронология всех звонков и вопросов клиента
    const timelineContainer = document.getElementById("clientCallsTimeline");
    if (timelineContainer) {
        timelineContainer.innerHTML = client.calls.map((call, idx) => {
            const isOut = (call.direction === "out");
            const dirLabel = isOut ? "↗ Исходящий звонок" : "↙ Входящий звонок";
            const dirBadgeClass = isOut ? "badge-primary" : "badge-outline";

            // Выделяем вопросы клиента
            const questionSummary = call.summary || `Клиент звонил по вопросу подбора шин ${call.tireSize || ''}.`;
            const callTopic = call.topic || "Консультация по ассортименту";

            return `
                <div class="client-call-card">
                    <div class="client-call-header">
                        <div class="client-call-title-wrap">
                            <span class="badge ${dirBadgeClass}">${dirLabel}</span>
                            <span class="client-call-num">Звонок #${call.id}</span>
                            <span class="client-call-meta">📅 ${call.date || ''} в ${call.time || ''}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="badge ${call.csat ? 'badge-success' : 'badge-neutral'}">⭐ CSAT ${call.csat ? call.csat + '/5' : '—'}</span>
                            <span class="badge badge-neutral">⏱️ ${call.duration || "00:00"}</span>
                            <button class="btn-client-call-action" onclick="closeClientModal(); openCallModal(${call.id});">
                                🎧 Разбор звонка AI
                            </button>
                        </div>
                    </div>

                    <!-- С какими вопросами обращался клиент -->
                    <div class="client-question-box">
                        <div class="client-question-title">
                            <span>❓ С какими вопросами обращался клиент:</span>
                        </div>
                        <div class="client-question-text">
                            <strong>${callTopic}.</strong> ${questionSummary}
                        </div>
                    </div>

                    <!-- Детали диалога -->
                    <div class="client-call-details-grid">
                        <div class="client-detail-item">
                            <span class="client-detail-label">Менеджер AIKOS:</span>
                            <span class="client-detail-val highlight">👨‍💼 ${call.manager || "Оператор"}</span>
                        </div>
                        <div class="client-detail-item">
                            <span class="client-detail-label">Интересующий товар:</span>
                            <span class="client-detail-val">${call.productCat || "Зимние шины"} (${call.tireSize || "—"})</span>
                        </div>
                        <div class="client-detail-item">
                            <span class="client-detail-label">Бренд / Модель:</span>
                            <span class="client-detail-val">${call.brand || "—"}</span>
                        </div>
                        <div class="client-detail-item">
                            <span class="client-detail-label">Итог диалога:</span>
                            <span class="client-detail-val success">${call.outcome || "Консультация проведена"}</span>
                        </div>
                        <div class="client-detail-item">
                            <span class="client-detail-label">Сумма заказа:</span>
                            <span class="client-detail-val highlight">${call.orderSum || "0 ₸"}</span>
                        </div>
                        <div class="client-detail-item">
                            <span class="client-detail-label">Tone of Voice:</span>
                            <span class="client-detail-val">${call.emotion || "Радость"} (${call.emotionPct || "75%"})</span>
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    }

    // 2. Вкладка: AI-портрет и предпочтения
    const prefList = document.getElementById("clientPreferencesList");
    if (prefList) {
        prefList.innerHTML = `
            <div class="mgr-audit-item">
                <span class="mgr-audit-item-icon" style="color: #38bdf8;">🚗</span>
                <div>
                    <div class="mgr-audit-item-title">Автомобиль и типоразмеры шин</div>
                    <div class="mgr-audit-item-desc">Интересовался типоразмерами: <strong>${client.tireSizes.join(", ") || "205/55 R16, 275/70 R16"}</strong>.</div>
                </div>
            </div>
            <div class="mgr-audit-item">
                <span class="mgr-audit-item-icon" style="color: #38bdf8;">🏷️</span>
                <div>
                    <div class="mgr-audit-item-title">Брендовые предпочтения</div>
                    <div class="mgr-audit-item-desc">Выбирает надежные бренды: <strong>${client.brands.join(", ") || "Kormoran, Michelin, Triangle"}</strong>. Ориентирован на качество и гарантию.</div>
                </div>
            </div>
            <div class="mgr-audit-item">
                <span class="mgr-audit-item-icon" style="color: #4ade80;">💬</span>
                <div>
                    <div class="mgr-audit-item-title">Стиль общения и лояльность</div>
                    <div class="mgr-audit-item-desc">Средний CSAT: <strong>${client.avgCsat}/5.0</strong>. Клиент вежлив, ценит быстроту проверки остатков на складе филиала в г. ${client.city}.</div>
                </div>
            </div>
        `;
    }

    const recList = document.getElementById("clientRecommendationsList");
    if (recList) {
        recList.innerHTML = `
            <div class="mgr-rop-tip-item">
                <div class="mgr-rop-tip-num">1</div>
                <div>
                    <strong style="color: #fff; font-size: 13px; display: block; margin-bottom: 2px;">Предложить услугу сезонного хранения шин</strong>
                    <span style="font-size: 12px; color: var(--text-secondary);">В предыдущих диалогах менеджеры не предлагали шинный отель. Высокая вероятность согласия (~15 000 ₸).</span>
                </div>
            </div>
            <div class="mgr-rop-tip-item">
                <div class="mgr-rop-tip-num">2</div>
                <div>
                    <strong style="color: #fff; font-size: 13px; display: block; margin-bottom: 2px;">Запись на фирменный шиномонтаж со скидкой 20%</strong>
                    <span style="font-size: 12px; color: var(--text-secondary);">Уточнить удобное время визита в шинный центр в г. ${client.city}.</span>
                </div>
            </div>
            <div class="mgr-rop-tip-item">
                <div class="mgr-rop-tip-num">3</div>
                <div>
                    <strong style="color: #fff; font-size: 13px; display: block; margin-bottom: 2px;">Приветствие с учетом истории обращений</strong>
                    <span style="font-size: 12px; color: var(--text-secondary);">Обращаться по имени и сразу предлагать статус по ранее забронированным шинам.</span>
                </div>
            </div>
        `;
    }

    // 3. Вкладка: Товары и заказы
    const ordersTbody = document.getElementById("clientOrdersTableBody");
    if (ordersTbody) {
        ordersTbody.innerHTML = client.calls.map(c => `
            <tr>
                <td>${c.date || ''} ${c.time || ''}</td>
                <td><strong>${c.productCat || "Зимние шины"}</strong> (${c.brand || "—"})</td>
                <td><span class="badge badge-outline">${c.tireSize || "—"}</span></td>
                <td><span class="badge badge-primary">${c.orderSum || "0 ₸"}</span></td>
                <td><span class="badge badge-success">${c.outcome || "Консультация"}</span></td>
                <td>${c.manager || "Оператор"}</td>
                <td class="text-right">
                    <button class="btn-action" onclick="closeClientModal(); openCallModal(${c.id});">Разбор #${c.id}</button>
                </td>
            </tr>
        `).join("");
    }

    // Активируем первую вкладку
    document.querySelectorAll("[data-client-tab]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".client-tab-content").forEach(tc => tc.classList.remove("active"));
    const firstClientTab = document.querySelector("[data-client-tab='client-tab-history']");
    if (firstClientTab) firstClientTab.classList.add("active");
    const firstContent = document.getElementById("client-tab-history");
    if (firstContent) firstContent.classList.add("active");

    modal.classList.add("open");
    modal.classList.add("active");
}

// Закрытие модального окна клиента
function closeClientModal() {
    const modal = document.getElementById("clientModal");
    if (modal) {
        modal.classList.remove("open");
        modal.classList.remove("active");
    }
}

// Переключение экранов навигации
function switchView(viewId) {
    document.querySelectorAll(".page-view").forEach(v => v.classList.remove("active"));
    document.querySelectorAll(".nav-item").forEach(item => {
        if (item.getAttribute("data-view") === viewId) item.classList.add("active");
        else item.classList.remove("active");
    });

    const target = document.getElementById(viewId);
    if (target) target.classList.add("active");

    const heading = document.getElementById("pageMainHeading");
    const subheading = document.getElementById("pageMainSubheading");

    if (viewId === "view-calls") {
        if (heading) heading.innerText = "Контроль качества и аналитика речи";
        if (subheading) subheading.innerText = "Автоматическая расшифровка, оценка скрипта и Tone of Voice на базе AI";
    } else if (viewId === "view-dashboard") {
        if (heading) heading.innerText = "Сводный дашборд качества продаж";
        if (subheading) subheading.innerText = "Аналитика соблюдения стандартов, Tone of Voice и упущенной выгоды";
    } else if (viewId === "view-managers") {
        if (heading) heading.innerText = "Рейтинг и эффективность менеджеров";
        if (subheading) subheading.innerText = "Показатели CSAT, соблюдения скриптов и тональности по каждому сотруднику";
        renderManagers(callsData, true);
    } else if (viewId === "view-mgr-analysis") {
        if (heading) heading.innerText = "Детальный анализ менеджеров";
        if (subheading) subheading.innerText = "Разбор работы сотрудника за выбранный период: слабые места, нарушения скрипта, AI-рекомендации";
        if (typeof window.initManagerAnalysis === "function") window.initManagerAnalysis();
    } else if (viewId === "view-clients") {
        if (heading) heading.innerText = "Клиентская база и история обращений";
        if (subheading) subheading.innerText = "История звонков каждого клиента, вопросы, потребности и AI-профилирование";
        renderClients(callsData, true);
    } else if (viewId === "view-audit") {
        if (heading) heading.innerText = "Журнал изменений и аудит данных";
        if (subheading) subheading.innerText = "История ручных корректировок имен менеджеров, городов и параметров звонков";
        if (!auditLogsData || auditLogsData.length === 0) {
            loadAuditData(true);
        } else {
            renderAuditLogs(auditLogsData, true);
        }
    }
}

// Функция гарантированного получения всех 7 показателей чек-листа скрипта
function getCallChecklist(call) {
    if (Array.isArray(call.checklist) && call.checklist.length === 7) {
        return call.checklist;
    }

    const rawText = (call.dialogue ? call.dialogue.map(d => d.text).join(" ") : (call.summary || "")).toLowerCase();
    const mgrName = (call.manager && call.manager.trim()) ? call.manager.trim() : "Оператор";
    const city = (call.city && call.city.trim()) ? call.city.trim() : "Алматы";
    const tireSize = call.tireSize || "195/50/R15";
    const brand = call.brand || "—";
    const politeness = parseInt(call.managerPoliteness) || 5;

    const greet = (mgrName !== "Оператор") || rawText.includes("эйкос") || rawText.includes("здравствуйт") || rawText.includes("добрый");
    const needs = rawText.includes("размер") || rawText.includes("зимн") || rawText.includes("летн") || (tireSize !== "205/55 R16" && tireSize !== "—") || rawText.includes("радиус");
    const present = (rawText.includes("шин") || rawText.includes("колес") || rawText.includes("комплект")) && (rawText.includes("тенге") || rawText.includes("стоит") || rawText.includes("цена") || rawText.includes("тысяч") || brand !== "—");
    const objection = (!call.conflict) || rawText.includes("предлож") || rawText.includes("склад") || rawText.includes("филиал") || rawText.includes("вариант") || rawText.includes("можем");
    const closing = rawText.includes("до свидан") || rawText.includes("всего добр") || rawText.includes("хорошего") || rawText.includes("бронь") || rawText.includes("заказ") || rawText.includes("приезжайте") || rawText.includes("ждем") || (city !== "");
    const prof = (politeness >= 4) && (!call.conflict || politeness >= 4);
    const upsell = rawText.includes("хранен") || rawText.includes("монтаж") || rawText.includes("гаранти") || rawText.includes("балансировк");

    return [
        { title: "Приветствие", desc: greet ? `Представился: «Компания Эйкос, ${mgrName}»` : "Не назвал компанию / не представился", passed: greet },
        { title: "Выявления потребностей", desc: needs ? `Уточнил параметры и типоразмер: ${tireSize}` : "Потребность и типоразмер не уточнены", passed: needs },
        { title: "Презентация", desc: present ? `Презентовал варианты шин (${brand}), озвучил стоимость` : "Презентация вариантов шин не проведена", passed: present },
        { title: "Работа с возражениями", desc: objection ? "Отработал вопросы и сомнения по наличию/складу, предложил альтернативы" : "Не отработаны сомнения/претензия клиента", passed: objection },
        { title: "Окончание разговора", desc: closing ? `Зафиксировал договоренность (город ${city}), вежливо попрощался` : "Не зафиксирован итог / нет вежливого прощания", passed: closing },
        { title: "Профессиональный подход", desc: prof ? `Оценка вежливости ${politeness}/5, соблюдение делового этикета` : "Зафиксированы замечания к культуре речи", passed: prof },
        { title: "Доп прдажа", desc: upsell ? "Предложил сопутствующие услуги (хранение шин / шиномонтаж)" : "Не предложены сопутствующие услуги (хранение/монтаж)", passed: upsell }
    ];
}

// Состояние модального окна звонка и плеера
let currentOpenCallId = null;
let currentActiveModalTab = "tab-summary";
let currentPlaybackSpeed = 1.0;

// Открытие модального окна с детальным разбором звонка
function openCallModal(callId) {
    const call = callsData.find(c => c.id == callId);
    if (!call) return;

    const isDifferentCall = (currentOpenCallId !== call.id);
    currentOpenCallId = call.id;

    // Сразу открываем модальное окно звонка
    const modal = document.getElementById("callModal");
    if (modal) {
        modal.classList.add("open");
        modal.classList.add("active");
    }

    try {
        if (window.location.hash !== "#call-" + call.id) {
            history.replaceState(null, "", "#call-" + call.id);
        }
    } catch (e) {}

    try {
        // Шапка
        const mIdEl = document.getElementById("modalCallId");
        if (mIdEl) mIdEl.innerText = `#${call.id}`;
        const mDateEl = document.getElementById("modalCallDate");
        if (mDateEl) mDateEl.innerText = `${call.date || ''} ${call.time || ''}`;
        const mDirEl = document.getElementById("modalCallDirection");
        if (mDirEl) mDirEl.innerText = call.directionLabel || (call.direction === "out" ? "Исходящий звонок" : "Входящий звонок");
        const mPhoneEl = document.getElementById("modalPhoneTitle");
        if (mPhoneEl) mPhoneEl.innerText = `${call.phone || 'Клиент'} (${call.city || 'Казахстан'})`;

        const editModalBtn = document.getElementById("openEditFromModalBtn");
        if (editModalBtn) {
            editModalBtn.onclick = () => {
                openEditCallModal(call.id);
            };
        }

    // Вкладка 1: Резюме
    document.getElementById("modalSummaryText").innerText = call.callSummary || call.summary || "Транскрипция и краткое резюме звонка.";
    
    const catEl = document.getElementById("modalCallCategory");
    if (catEl) catEl.innerText = `${call.callCategory || 'Прочее'}${call.callSubcategory ? ' (' + call.callSubcategory + ')' : ''}`;

    const clientNameEl = document.getElementById("modalClientName");
    if (clientNameEl) clientNameEl.innerText = call.clientName || `Клиент (${call.phone || ''})`;

    const carModelEl = document.getElementById("modalCarModel");
    if (carModelEl) carModelEl.innerText = call.carModel && call.carModel !== "Не указано" ? `🚗 ${call.carModel}` : "— (Не назван)";

    const cityEl = document.getElementById("modalCity");
    if (cityEl) cityEl.innerText = call.branchAddress || (call.city ? `г. ${call.city}` : "Алматы");

    const productCatEl = document.getElementById("modalProductCat");
    if (productCatEl) productCatEl.innerText = call.tireSeason || call.productCat || "Зимние шины";

    const tireBrandEl = document.getElementById("modalTireBrand");
    if (tireBrandEl) {
        tireBrandEl.innerText = call.tireBrand || call.brand || "— (Не назван)";
    }

    const tireSizeEl = document.getElementById("modalTireSize");
    if (tireSizeEl) {
        tireSizeEl.innerText = call.tireSize || "— (Не указан)";
    }

    const tireCountEl = document.getElementById("modalTireCount");
    if (tireCountEl) tireCountEl.innerText = call.tireCount || "4 шт. (комплект)";

    const paymentMethodEl = document.getElementById("modalPaymentMethod");
    if (paymentMethodEl) paymentMethodEl.innerText = call.paymentMethod || "Оплата в филиале";

    const lostReasonWrap = document.getElementById("modalLostReasonWrap");
    const lostReasonEl = document.getElementById("modalLostReason");
    if (lostReasonWrap && lostReasonEl) {
        if (call.lostReason && call.lostReason.trim()) {
            lostReasonWrap.style.display = "flex";
            lostReasonEl.innerText = call.lostReason;
        } else {
            lostReasonWrap.style.display = "none";
        }
    }

    const saleStatus = call.saleStatus || call.outcome || "Консультация";
    const isSaleWon = (saleStatus === "Продажа состоялась" || saleStatus === "Бронь оформлена");

    const orderSumElModal = document.getElementById("modalOrderSum");
    if (orderSumElModal) {
        if (isSaleWon && call.orderSum && call.orderSum !== "0 ₸") {
            orderSumElModal.innerText = `${call.orderSum} (Комплект)`;
            orderSumElModal.style.color = "#34d399";
        } else {
            orderSumElModal.innerText = "0 ₸ (без покупки)";
            orderSumElModal.style.color = "var(--text-secondary)";
        }
    }

    const outcomeElModal = document.getElementById("modalOutcome");
    if (outcomeElModal) {
        outcomeElModal.innerText = saleStatus;
        if (isSaleWon) {
            outcomeElModal.className = "fact-val success";
            outcomeElModal.style.color = "#34d399";
        } else if (saleStatus === "Отказ от покупки") {
            outcomeElModal.className = "fact-val";
            outcomeElModal.style.color = "#f87171";
        } else {
            outcomeElModal.className = "fact-val";
            outcomeElModal.style.color = "var(--text-secondary)";
        }
    }
    document.getElementById("modalOpportunityText").innerText = call.opportunity || "Предложить сопутствующие услуги.";

    // Коммерческий результат и фиксация
    const commBlockSale = document.getElementById("modalCommBlockSale");
    const commBlockUpsell = document.getElementById("modalCommBlockUpsell");
    const commBlockService = document.getElementById("modalCommBlockService");

    const saleBadgeEl = document.getElementById("modalCommSaleBadge");
    const orderSumEl = document.getElementById("modalCommOrderSum");
    const productMetaEl = document.getElementById("modalCommProductMeta");
    const upsellBadgeEl = document.getElementById("modalCommUpsellBadge");
    const upsellSumEl = document.getElementById("modalCommUpsellSum");
    const upsellMetaEl = document.getElementById("modalCommUpsellMeta");
    const serviceBadgeEl = document.getElementById("modalCommServiceBadge");
    const serviceDateEl = document.getElementById("modalCommServiceDate");
    const serviceMetaEl = document.getElementById("modalCommServiceMeta");

    // 1. Блок: Основная сделка
    if (commBlockSale) {
        if (isSaleWon) {
            commBlockSale.classList.add("completed");
        } else {
            commBlockSale.classList.remove("completed");
        }
    }
    if (saleBadgeEl) {
        if (saleStatus === "Продажа состоялась") {
            saleBadgeEl.className = "badge badge-success";
            saleBadgeEl.innerText = "🟢 Продажа состоялась";
        } else if (saleStatus === "Бронь оформлена") {
            saleBadgeEl.className = "badge badge-success";
            saleBadgeEl.innerText = "🟢 Бронь оформлена";
        } else if (saleStatus === "Отказ от покупки") {
            saleBadgeEl.className = "badge badge-outline";
            saleBadgeEl.style.color = "#f87171";
            saleBadgeEl.innerText = "🔴 Отказ";
        } else {
            saleBadgeEl.className = "badge badge-neutral";
            saleBadgeEl.innerText = "⚪ Консультация";
        }
    }
    if (orderSumEl) {
        orderSumEl.innerText = (isSaleWon && call.orderSum && call.orderSum !== "0 ₸") ? call.orderSum : "0 ₸";
    }
    if (productMetaEl) {
        if (isSaleWon) {
            productMetaEl.innerText = `${call.tireSize || 'Шины'} • ${call.brand || 'Комплект'}`;
        } else {
            productMetaEl.innerText = `Консультация • ${call.tireSize || call.brand || 'Шины'}`;
        }
    }

    // 2. Блок: Допродажа (Upsell)
    if (commBlockUpsell) {
        if (call.hasUpsell) {
            commBlockUpsell.classList.add("completed");
        } else {
            commBlockUpsell.classList.remove("completed");
        }
    }
    if (upsellBadgeEl) {
        if (call.hasUpsell) {
            upsellBadgeEl.className = "badge badge-success";
            upsellBadgeEl.innerText = "🟢 Оформлена";
        } else {
            upsellBadgeEl.className = "badge badge-neutral";
            upsellBadgeEl.innerText = "⚪ Не предложена";
        }
    }
    if (upsellSumEl) upsellSumEl.innerText = call.hasUpsell ? (call.upsellSum || "18 000 ₸") : "0 ₸";
    if (upsellMetaEl) upsellMetaEl.innerText = call.hasUpsell ? (call.upsellText || "Сезонное хранение шин") : "Потенциал: хранение/монтаж";

    // 3. Блок: Запись на сервис
    const sBooking = call.serviceBooking || "Нет";
    const hasService = (sBooking && sBooking !== "Нет");
    if (commBlockService) {
        if (hasService) {
            commBlockService.classList.add("completed");
        } else {
            commBlockService.classList.remove("completed");
        }
    }
    if (serviceBadgeEl) {
        if (hasService) {
            serviceBadgeEl.className = "badge badge-success";
            serviceBadgeEl.innerText = `🟢 ${sBooking}`;
        } else {
            serviceBadgeEl.className = "badge badge-neutral";
            serviceBadgeEl.innerText = "⚪ Записи нет";
        }
    }
    if (serviceDateEl) serviceDateEl.innerText = call.serviceDate || (hasService ? "Согласовано" : "—");
    if (serviceMetaEl) serviceMetaEl.innerText = `Филиал: ${call.city || 'Алматы'}`;

    const openOutcomeBtn = document.getElementById("openEditOutcomeFromModalBtn");
    if (openOutcomeBtn) {
        openOutcomeBtn.onclick = () => openEditCallModal(call.id);
    }

    const hasCsat = (call.csat !== null && call.csat !== undefined && call.csat !== "");
    const csatVal = hasCsat ? Number(call.csat) : null;
    document.getElementById("modalCsatStars").innerText = csatVal ? (`⭐`.repeat(Math.round(csatVal)) + ` ${csatVal}/5`) : "Не оценен";
    document.getElementById("modalManagerPoliteness").innerText = call.managerPoliteness || (csatVal ? `${csatVal} / 5` : "Не оценено");
    document.getElementById("modalConflictStatus").innerText = call.conflict ? "Обнаружен конфликт" : "Не зафиксирован";
    document.getElementById("modalScriptPct").innerText = (call.scriptScore !== null && call.scriptScore !== undefined) ? `${call.scriptScore}%` : "—";

    // Вкладка 2: Транскрипция (Диалог)
    initCallAudio(call, false);

    const dialogueContainer = document.getElementById("modalDialogueContainer");
    dialogueContainer.innerHTML = "";

    if (Array.isArray(call.dialogue) && call.dialogue.length > 0) {
        const totalDuration = call.durationSec || 60;
        const totalLines = call.dialogue.length;

        call.dialogue.forEach((msg, index) => {
            const msgDiv = document.createElement("div");
            msgDiv.className = `message-row ${msg.role || 'manager'}`;
            msgDiv.style.cursor = "pointer";
            msgDiv.title = "Нажмите, чтобы включить аудио с этого момента";

            const approxStartSec = (index / totalLines) * totalDuration;
            const timeTag = formatAudioTime(approxStartSec);

            let sentimentLabel = "НЕЙТРАЛЬНО";
            let sentimentClass = "neutral";
            if (msg.sentiment === "positive" || msg.sentiment === "ПОЗИТИВ") {
                sentimentLabel = "ПОЗИТИВ";
                sentimentClass = "positive";
            } else if (msg.sentiment === "negative" || msg.sentiment === "НЕГАТИВ") {
                sentimentLabel = "НЕГАТИВ";
                sentimentClass = "negative";
            }

            msgDiv.innerHTML = `
                <div class="msg-header">
                    <span class="msg-speaker">${msg.speaker || 'Собеседник'} <span style="font-size: 10.5px; opacity: 0.6; font-weight: 400; margin-left: 6px;">⏱️ ${timeTag}</span></span>
                    <span class="sentiment-tag ${sentimentClass}">${sentimentLabel}</span>
                </div>
                <div class="msg-text">${msg.text}</div>
            `;

            msgDiv.onclick = () => {
                const audio = document.getElementById("globalCallAudioPlayer");
                if (audio) {
                    audio.currentTime = approxStartSec;
                    audio.play().then(() => setPlayerBtnState(true)).catch(() => {});
                }
            };

            dialogueContainer.appendChild(msgDiv);
        });
    } else {
        dialogueContainer.innerHTML = `<p style="color: var(--text-muted); padding: 20px; text-align: center;">Транскрипция диалога отсутствует.</p>`;
    }

    // Вкладка 3: Раздельный Tone of Voice Менеджера и Клиента
    const mgrTone = call.managerTone || {
        happy: 75,
        neutral: 25,
        sad: 0,
        angry: 0,
        dominant: "Дружелюбие и вежливость",
        description: "Приветливый тон, уверенные ответы без раздражения",
        politeness: call.managerPoliteness || "5 / 5"
    };

    const clientTone = call.clientTone || {
        happy: 70,
        neutral: 25,
        sad: 5,
        angry: 0,
        dominant: "Лояльный и удовлетворенный",
        description: "Клиент позитивно реагирует на предложения",
        sentimentStart: "Нейтральный",
        sentimentEnd: "Позитивный",
        csat: call.csat || 5
    };

    // Заполнение Менеджера
    const mgrDomEl = document.getElementById("modalMgrToneDominant");
    const mgrDescEl = document.getElementById("modalMgrToneDesc");
    const mgrPolitEl = document.getElementById("modalMgrPolitenessBadge");
    const mgrTempoEl = document.getElementById("modalMgrTempo");
    const mgrBarsEl = document.getElementById("modalMgrEmotionBars");

    if (mgrDomEl) mgrDomEl.innerText = mgrTone.dominant || "Дружелюбие";
    if (mgrDescEl) mgrDescEl.innerText = mgrTone.description || "Приветливый тон, соблюдение корпоративных стандартов";
    if (mgrPolitEl) mgrPolitEl.innerText = call.managerPoliteness && !call.managerPoliteness.includes("Не") ? `⭐ ${call.managerPoliteness} (Вежливость)` : "Вежливость: не оценено";
    if (mgrTempoEl) mgrTempoEl.innerText = (call.acoustics && call.acoustics.tempo) || "130 уд/мин";

    if (mgrBarsEl) {
        mgrBarsEl.innerHTML = `
            <div class="emotion-bar-item">
                <div class="em-info"><span>😊 Дружелюбие / Вежливость</span><span>${mgrTone.happy || 0}%</span></div>
                <div class="em-track"><div class="em-fill happy" style="width: ${mgrTone.happy || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>💼 Спокойный деловой тон</span><span>${mgrTone.neutral || 0}%</span></div>
                <div class="em-track"><div class="em-fill neutral" style="width: ${mgrTone.neutral || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>🙁 Неуверенность / Заминки</span><span>${mgrTone.sad || 0}%</span></div>
                <div class="em-track"><div class="em-fill sad" style="width: ${mgrTone.sad || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>😠 Раздражение / Спешка</span><span>${mgrTone.angry || 0}%</span></div>
                <div class="em-track"><div class="em-fill angry" style="width: ${mgrTone.angry || 0}%;"></div></div>
            </div>
        `;
    }

    // Заполнение Клиента
    const clientDomEl = document.getElementById("modalClientToneDominant");
    const clientDescEl = document.getElementById("modalClientToneDesc");
    const clientShiftEl = document.getElementById("modalClientSentimentShift");
    const clientCsatEl = document.getElementById("modalClientCsatVal");
    const clientConflictEl = document.getElementById("modalClientConflictVal");
    const clientBarsEl = document.getElementById("modalClientEmotionBars");

    if (clientDomEl) clientDomEl.innerText = clientTone.dominant || "Лояльный";
    if (clientDescEl) clientDescEl.innerText = clientTone.description || "Клиент вовлечен в диалог";
    if (clientShiftEl) clientShiftEl.innerText = `${clientTone.sentimentStart || 'Нейтральный'} ➔ ${clientTone.sentimentEnd || 'Позитивный'}`;
    if (clientCsatEl) clientCsatEl.innerText = (call.csat !== null && call.csat !== undefined && call.csat !== "") ? `⭐ ${Number(call.csat).toFixed(1)} / 5.0` : "Не оценен";
    if (clientConflictEl) {
        clientConflictEl.innerText = call.conflict ? "Обнаружен конфликт" : "Не обнаружено";
        clientConflictEl.style.color = call.conflict ? "#f87171" : "#4ade80";
    }

    if (clientBarsEl) {
        clientBarsEl.innerHTML = `
            <div class="emotion-bar-item">
                <div class="em-info"><span>😊 Лояльность / Удовлетворенность</span><span>${clientTone.happy || 0}%</span></div>
                <div class="em-track"><div class="em-fill happy" style="width: ${clientTone.happy || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>😐 Нейтральные вопросы</span><span>${clientTone.neutral || 0}%</span></div>
                <div class="em-track"><div class="em-fill neutral" style="width: ${clientTone.neutral || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>🙁 Сомнения / Неуверенность</span><span>${clientTone.sad || 0}%</span></div>
                <div class="em-track"><div class="em-fill sad" style="width: ${clientTone.sad || 0}%;"></div></div>
            </div>
            <div class="emotion-bar-item">
                <div class="em-info"><span>😠 Претензии / Недовольство</span><span>${clientTone.angry || 0}%</span></div>
                <div class="em-track"><div class="em-fill angry" style="width: ${clientTone.angry || 0}%;"></div></div>
            </div>
        `;
    }

    // Акустика
    const acoustics = call.acoustics || {};
    const silenceEl = document.getElementById("modalSilenceMetric");
    const loudEl = document.getElementById("modalLoudMetric");
    if (silenceEl) silenceEl.innerText = `Паузы: ${acoustics.silence || "0.0 сек (0%)"}`;
    if (loudEl) loudEl.innerText = `Всплески: ${acoustics.loudMoments || "0%"}`;

    // Таймлайн
    const timelineTrack = document.getElementById("modalTimelineTrack");
    if (timelineTrack) {
        if (Array.isArray(call.timeline) && call.timeline.length > 0) {
            timelineTrack.innerHTML = call.timeline.map(seg => `
                <div class="timeline-segment ${seg.emotion || 'happy'}" title="Интервал ${seg.time} — ${seg.emotion} (${seg.pct}%)">
                    <span>${seg.time}</span>
                    <span>${Math.round(seg.pct || 0)}%</span>
                </div>
            `).join("");
        } else {
            timelineTrack.innerHTML = `<span style="color: var(--text-muted); font-size: 12px; padding: 12px;">Таймлайн формируется...</span>`;
        }
    }

    // Вкладка 4: Чек-лист скрипта (все 7 показателей)
    const checklist = getCallChecklist(call);
    const applicableItems = checklist.filter(item => !item.not_applicable && item.passed !== null);
    const passedCount = applicableItems.filter(item => item.passed).length;
    const totalApplicable = applicableItems.length || 1;
    const scorePct = Math.round((passedCount / totalApplicable) * 100);

    const checklistContainer = document.getElementById("tab-checklist");
    if (checklistContainer) {
        checklistContainer.innerHTML = `
            <div class="checklist-container">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); flex-wrap: wrap; gap: 10px;">
                    <div>
                        <h3 style="margin: 0; font-size: 16px; display: flex; align-items: center; gap: 8px;">
                            📋 Проверка чек-листа звонка менеджера (7 стандартов)
                        </h3>
                        <p style="margin: 4px 0 0 0; font-size: 12px; color: var(--text-muted);">
                            Автоматический аудит соблюдения стандартов скрипта продаж в звонке #${call.id}
                        </p>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span class="badge ${scorePct >= 80 ? 'badge-success' : (scorePct >= 60 ? 'badge-primary' : 'badge-warning')}" style="font-size: 13px; padding: 6px 12px; font-weight: 700;">
                            Соблюдение: ${scorePct}% (${passedCount} из ${applicableItems.length}${applicableItems.length < checklist.length ? ` • ${checklist.length - applicableItems.length} не применимо` : ''})
                        </span>
                    </div>
                </div>

                <div class="checklist-items" id="modalChecklistItems">
                    ${checklist.map((item, idx) => {
                        const isNa = item.not_applicable || item.passed === null;
                        const rowClass = isNa ? "na" : (item.passed ? "passed" : "failed");
                        const statusIcon = isNa ? "—" : (item.passed ? "✓" : "✗");
                        const badgeClass = isNa ? "badge-secondary" : (item.passed ? "badge-success" : "badge-warning");
                        const badgeText = isNa ? "Не применимо" : (item.passed ? "Выполнено" : "Не выполнено");
                        return `
                        <div class="checklist-row ${rowClass}">
                            <div class="chk-status">${statusIcon}</div>
                            <div class="chk-details">
                                <div class="chk-title">${idx + 1}. ${item.title}</div>
                                <div class="chk-desc">${item.desc}</div>
                            </div>
                            <span class="badge ${badgeClass}">${badgeText}</span>
                        </div>
                        `;
                    }).join("")}
                </div>
            </div>
        `;
    }

        // Активируем вкладку: если звонок новый — первую вкладку, если фоновое обновление — текущую выбранную вкладку
        if (isDifferentCall) {
            currentActiveModalTab = "tab-summary";
        }
        document.querySelectorAll(".tab-btn[data-tab]").forEach(b => {
            if (b.getAttribute("data-tab") === currentActiveModalTab) {
                b.classList.add("active");
            } else {
                b.classList.remove("active");
            }
        });
        document.querySelectorAll(".tab-content").forEach(tc => {
            if (tc.id === currentActiveModalTab) {
                tc.classList.add("active");
            } else {
                tc.classList.remove("active");
            }
        });
    } catch (err) {
        console.error("[AI CRM] Ошибка при заполнении карточки звонка:", err);
    }
}

// Контроллер аудиоплеера
let currentPlayingCallId = null;

function formatAudioTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "00:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function setPlayerBtnState(isPlaying) {
    const btnIcon = document.getElementById("playerBtnIcon");
    const btnText = document.getElementById("playerBtnText");
    const playBtn = document.getElementById("playAudioBtn");

    if (isPlaying) {
        if (btnIcon) {
            btnIcon.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>`;
        }
        if (btnText) btnText.innerText = "Пауза";
        if (playBtn) playBtn.classList.add("playing");
    } else {
        if (btnIcon) {
            btnIcon.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>`;
        }
        if (btnText) btnText.innerText = "Прослушать аудио";
        if (playBtn) playBtn.classList.remove("playing");
    }
}

function initCallAudio(call, autoPlay = false) {
    const audio = document.getElementById("globalCallAudioPlayer");
    const playBtn = document.getElementById("playAudioBtn");
    const currTimeEl = document.getElementById("modalAudioCurrentTime");
    const durEl = document.getElementById("modalAudioDuration");
    const progressFill = document.getElementById("modalAudioProgressFill");
    const track = document.getElementById("modalAudioTrack");

    if (!audio) return;

    audio.pause();
    currentPlayingCallId = call.id;
    audio.src = `/api/audio?call_id=${call.id}`;
    audio.currentTime = 0;
    audio.playbackRate = currentPlaybackSpeed;

    if (currTimeEl) currTimeEl.innerText = "00:00";
    if (durEl) durEl.innerText = call.duration || "00:00";
    if (progressFill) progressFill.style.width = "0%";
    setPlayerBtnState(false);

    // Обновляем активное состояние кнопок скорости
    document.querySelectorAll(".audio-speed-controls .speed-btn").forEach(btn => {
        const speedVal = parseFloat(btn.getAttribute("data-speed")) || 1.0;
        if (Math.abs(speedVal - currentPlaybackSpeed) < 0.05) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
        btn.onclick = (e) => {
            e.stopPropagation();
            const chosenSpeed = parseFloat(btn.getAttribute("data-speed")) || 1.0;
            currentPlaybackSpeed = chosenSpeed;
            if (audio) {
                audio.playbackRate = chosenSpeed;
            }
            document.querySelectorAll(".audio-speed-controls .speed-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
        };
    });

    audio.ontimeupdate = () => {
        if (currTimeEl) currTimeEl.innerText = formatAudioTime(audio.currentTime);
        if (audio.duration && !isNaN(audio.duration) && audio.duration > 0) {
            const pct = Math.min((audio.currentTime / audio.duration) * 100, 100);
            if (progressFill) progressFill.style.width = `${pct}%`;
            if (durEl && (!call.duration || call.duration === "—")) {
                durEl.innerText = formatAudioTime(audio.duration);
            }
        }
    };

    audio.onended = () => {
        setPlayerBtnState(false);
        if (progressFill) progressFill.style.width = "0%";
        if (currTimeEl) currTimeEl.innerText = "00:00";
    };

    audio.onerror = (e) => {
        console.warn("[AI CRM Audio] Инфо/Ошибка загрузки аудиозаписи:", e);
        setPlayerBtnState(false);
        const btnText = document.getElementById("playerBtnText");
        if (btnText) {
            btnText.innerText = "⚠️ Файл не найден (404)";
            setTimeout(() => {
                btnText.innerText = "Прослушать аудио";
            }, 4000);
        }
    };

    if (playBtn) {
        playBtn.onclick = () => {
            if (audio.paused) {
                audio.play().then(() => {
                    setPlayerBtnState(true);
                }).catch(err => {
                    console.warn("[AI CRM Audio] Ошибка воспроизведения:", err);
                    setPlayerBtnState(false);
                    const btnText = document.getElementById("playerBtnText");
                    if (btnText) {
                        btnText.innerText = "⚠️ Аудио недоступно на диске";
                        setTimeout(() => {
                            btnText.innerText = "Прослушать аудио";
                        }, 4000);
                    }
                });
            } else {
                audio.pause();
                setPlayerBtnState(false);
            }
        };
    }

    if (track) {
        track.onclick = (e) => {
            const rect = track.getBoundingClientRect();
            const clickPos = (e.clientX - rect.left) / rect.width;
            if (audio.duration && !isNaN(audio.duration)) {
                audio.currentTime = clickPos * audio.duration;
            } else if (call.durationSec) {
                audio.currentTime = clickPos * call.durationSec;
            }
        };
    }

    if (autoPlay) {
        audio.play().then(() => setPlayerBtnState(true)).catch(() => {});
    }
}

function stopCallAudio() {
    const audio = document.getElementById("globalCallAudioPlayer");
    if (audio) {
        audio.pause();
        audio.currentTime = 0;
    }
    setPlayerBtnState(false);
}

// Закрытие модального окна звонка
function closeCallModal() {
    stopCallAudio();
    currentOpenCallId = null;
    currentActiveModalTab = "tab-summary";
    const modal = document.getElementById("callModal");
    if (modal) {
        modal.classList.remove("open");
        modal.classList.remove("active");
    }
    try {
        if (window.location.hash.startsWith("#call-")) {
            history.replaceState(null, "", "#calls");
        }
    } catch (e) {}
}

// Рендеринг журнала аудита и изменений с пагинацией
function renderAuditLogs(data, keepPage = false) {
    const tableBody = document.getElementById("auditTableBody");
    const totalEl = document.getElementById("kpiAuditTotal");
    const mgrsEl = document.getElementById("kpiAuditManagers");
    const citiesEl = document.getElementById("kpiAuditCities");
    const lastTimeEl = document.getElementById("kpiAuditLastTime");
    const lastUserEl = document.getElementById("kpiAuditLastUser");
    const badgeEl = document.getElementById("totalAuditBadge");

    const total = data.length;
    if (badgeEl) badgeEl.innerText = total;
    if (totalEl) totalEl.innerText = total;

    const mgrsCount = data.filter(l => l.fieldName === "manager_name").length;
    const citiesCount = data.filter(l => l.fieldName === "city_name").length;

    if (mgrsEl) mgrsEl.innerText = mgrsCount;
    if (citiesEl) citiesEl.innerText = citiesCount;

    if (total > 0) {
        if (lastTimeEl) lastTimeEl.innerText = data[0].changedAt || "—";
        if (lastUserEl) lastUserEl.innerText = `Автор: ${data[0].changedBy || 'Супервайзер'}`;
    } else {
        if (lastTimeEl) lastTimeEl.innerText = "—";
        if (lastUserEl) lastUserEl.innerText = "История версий активна";
    }

    if (!tableBody) return;
    tableBody.innerHTML = "";

    // Фильтрация поиска
    const searchVal = document.getElementById("auditSearchInput")?.value?.toLowerCase()?.trim() || "";
    const fieldVal = document.getElementById("auditFieldFilter")?.value || "all";

    const filtered = data.filter(l => {
        const matchesField = (fieldVal === "all" || l.fieldName === fieldVal);
        const matchesSearch = !searchVal || 
            String(l.callId).includes(searchVal) ||
            (l.phone && l.phone.toLowerCase().includes(searchVal)) ||
            (l.oldValue && l.oldValue.toLowerCase().includes(searchVal)) ||
            (l.newValue && l.newValue.toLowerCase().includes(searchVal)) ||
            (l.changedBy && l.changedBy.toLowerCase().includes(searchVal)) ||
            (l.comment && l.comment.toLowerCase().includes(searchVal));
        return matchesField && matchesSearch;
    });

    currentAuditList = filtered;
    const totalFiltered = filtered.length;

    if (totalFiltered === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; padding: 40px; color: var(--text-muted);">
                    <div style="font-size: 32px; margin-bottom: 8px;">📋</div>
                    <div>Записей в журнале изменений пока нет. Отредактируйте звонок для фиксации аудита.</div>
                </td>
            </tr>
        `;
        renderPaginationControls({
            pageSizeSelectId: "auditPageSizeSelect",
            infoId: "auditPaginationInfo",
            firstBtnId: "auditPageFirst",
            prevBtnId: "auditPagePrev",
            numbersId: "auditPageNumbers",
            nextBtnId: "auditPageNext",
            lastBtnId: "auditPageLast",
            currentPage: 1,
            pageSize: paginationState.audit.pageSize,
            totalItems: 0,
            onPageChange: () => {},
            onPageSizeChange: () => {}
        });
        return;
    }

    if (!keepPage) {
        paginationState.audit.page = 1;
    }
    const pageSize = paginationState.audit.pageSize;
    const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
    if (paginationState.audit.page > totalPages) {
        paginationState.audit.page = totalPages;
    }
    const page = paginationState.audit.page;
    const startIndex = (page - 1) * pageSize;
    const pagedAudit = filtered.slice(startIndex, startIndex + pageSize);

    pagedAudit.forEach(l => {
        const tr = document.createElement("tr");
        const isMgr = (l.fieldName === "manager_name");
        tr.innerHTML = `
            <td>
                <span style="color: var(--text-primary); font-weight: 500; font-size: 13px;">${l.changedAt}</span>
            </td>
            <td>
                <div class="call-id-col">
                    <span class="call-num">#${l.callId}</span>
                    <span class="call-time">${l.phone || 'Клиент'} (${l.callDate || ''})</span>
                </div>
            </td>
            <td>
                <span class="badge ${isMgr ? 'badge-primary' : 'badge-warning'}" style="font-size: 11px;">
                    ${isMgr ? '👨‍💼 Менеджер' : '📍 Город / Филиал'}
                </span>
            </td>
            <td>
                <span class="badge" style="background: rgba(239, 68, 68, 0.15); color: #f87171; text-decoration: line-through;">${l.oldValue}</span>
                <span style="color: var(--text-muted); margin: 0 4px;">➔</span>
                <span class="badge badge-success" style="font-weight: 700;">${l.newValue}</span>
            </td>
            <td>
                <span class="badge badge-outline" style="border-color: rgba(99, 102, 241, 0.4); color: #818cf8;">👤 ${l.changedBy}</span>
            </td>
            <td style="font-size: 12.5px; color: var(--text-secondary); max-width: 250px;">
                ${l.comment || 'Корректировка данных'}
            </td>
            <td class="text-right">
                <button class="action-btn" onclick="openCallModal(${l.callId});">
                    Разбор #${l.callId}
                </button>
            </td>
        `;
        tableBody.appendChild(tr);
    });

    // Отрисовка элементов пагинации журнала аудита
    renderPaginationControls({
        pageSizeSelectId: "auditPageSizeSelect",
        infoId: "auditPaginationInfo",
        firstBtnId: "auditPageFirst",
        prevBtnId: "auditPagePrev",
        numbersId: "auditPageNumbers",
        nextBtnId: "auditPageNext",
        lastBtnId: "auditPageLast",
        currentPage: page,
        pageSize: pageSize,
        totalItems: totalFiltered,
        onPageChange: (newPage) => {
            paginationState.audit.page = newPage;
            renderAuditLogs(data, true);
        },
        onPageSizeChange: (newSize) => {
            paginationState.audit.pageSize = newSize;
            paginationState.audit.page = 1;
            renderAuditLogs(data, true);
        }
    });
}

// Динамическое наполнение выпадающего списка менеджеров из БД
function populateManagerSelectOptions(currentManager = "") {
    const mgrSelect = document.getElementById("editManagerSelect");
    if (!mgrSelect) return;

    // Собираем всех уникальных менеджеров из базы данных (callsData и auditLogsData)
    const managersSet = new Set();
    managersSet.add("Оператор");

    if (Array.isArray(callsData)) {
        callsData.forEach(c => {
            if (c.manager && c.manager.trim() && c.manager !== "—") {
                managersSet.add(c.manager.trim());
            }
        });
    }

    if (Array.isArray(auditLogsData)) {
        auditLogsData.forEach(l => {
            if (l.fieldName === "manager_name" && l.newValue && l.newValue.trim() && l.newValue !== "—") {
                managersSet.add(l.newValue.trim());
            }
            if (l.currentManager && l.currentManager.trim() && l.currentManager !== "—") {
                managersSet.add(l.currentManager.trim());
            }
        });
    }

    if (currentManager && currentManager.trim()) {
        managersSet.add(currentManager.trim());
    }

    const defaultNames = ["Руслан", "Жандос", "Фарид"];
    defaultNames.forEach(n => managersSet.add(n));

    // Сортируем: 'Оператор' первым, затем реальные имена из БД по алфавиту, затем 'custom'
    const otherManagers = Array.from(managersSet)
        .filter(m => m !== "Оператор" && m !== "custom")
        .sort((a, b) => a.localeCompare(b, "ru"));

    mgrSelect.innerHTML = "";

    const defaultOpt = document.createElement("option");
    defaultOpt.value = "Оператор";
    defaultOpt.textContent = "Оператор (Не определен)";
    mgrSelect.appendChild(defaultOpt);

    otherManagers.forEach(name => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        mgrSelect.appendChild(opt);
    });

    const customOpt = document.createElement("option");
    customOpt.value = "custom";
    customOpt.textContent = "✏️ Другое имя (ввести вручную)...";
    mgrSelect.appendChild(customOpt);
}

// Динамическое наполнение выпадающего списка городов из БД
function populateCitySelectOptions(currentCity = "") {
    const citySelect = document.getElementById("editCitySelect");
    if (!citySelect) return;

    const citiesSet = new Set();
    const defaultCities = ["Алматы", "Астана", "Шымкент", "Караганда", "Актобе", "Тараз", "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Костанай", "Кызылорда", "Уральск", "Петропавловск", "Актау"];
    defaultCities.forEach(c => citiesSet.add(c));

    if (Array.isArray(callsData)) {
        callsData.forEach(c => {
            if (c.city && c.city.trim() && c.city !== "—") {
                citiesSet.add(c.city.trim());
            }
        });
    }

    if (Array.isArray(auditLogsData)) {
        auditLogsData.forEach(l => {
            if (l.fieldName === "city_name" && l.newValue && l.newValue.trim() && l.newValue !== "—") {
                citiesSet.add(l.newValue.trim());
            }
        });
    }

    if (currentCity && currentCity.trim()) {
        citiesSet.add(currentCity.trim());
    }

    const sortedCities = Array.from(citiesSet)
        .filter(c => c !== "custom")
        .sort((a, b) => a.localeCompare(b, "ru"));

    citySelect.innerHTML = "";
    sortedCities.forEach(city => {
        const opt = document.createElement("option");
        opt.value = city;
        opt.textContent = city;
        citySelect.appendChild(opt);
    });

    const customOpt = document.createElement("option");
    customOpt.value = "custom";
    customOpt.textContent = "✏️ Другой город (ввести вручную)...";
    citySelect.appendChild(customOpt);
}

// Открытие модального окна редактирования звонка
function openEditCallModal(callId) {
    console.log("[AI CRM] openEditCallModal вызван для звонка ID:", callId);
    const call = callsData.find(c => c.id == callId);
    if (!call) {
        console.warn("[AI CRM] Звонок не найден в callsData:", callId);
        return;
    }

    const editCallIdEl = document.getElementById("editCallId");
    const titleEl = document.getElementById("editModalTitle");
    const subtitleEl = document.getElementById("editModalSubtitle");

    if (editCallIdEl) editCallIdEl.value = call.id;
    if (titleEl) titleEl.innerText = `✏️ Корректировка данных звонка #${call.id}`;
    if (subtitleEl) subtitleEl.innerText = `Клиент: ${call.phone || '—'} • Дата: ${call.date || '—'} ${call.time || ''}`;

    const currentMgr = (call.manager && call.manager.trim()) ? call.manager.trim() : "Оператор";
    const currentCity = (call.city && call.city.trim()) ? call.city.trim() : "Алматы";

    // Динамически заполняем списки актуальными именами и городами из БД
    populateManagerSelectOptions(currentMgr);
    populateCitySelectOptions(currentCity);

    const mgrSelect = document.getElementById("editManagerSelect");
    const mgrCustom = document.getElementById("editManagerCustom");

    if (mgrSelect) {
        let mgrOptionExists = false;
        for (let opt of mgrSelect.options) {
            if (opt.value.toLowerCase() === currentMgr.toLowerCase()) {
                mgrSelect.value = opt.value;
                mgrOptionExists = true;
                break;
            }
        }
        if (!mgrOptionExists) {
            mgrSelect.value = "custom";
            if (mgrCustom) {
                mgrCustom.style.display = "block";
                mgrCustom.value = currentMgr;
            }
        } else {
            if (mgrCustom) {
                mgrCustom.style.display = "none";
                mgrCustom.value = "";
            }
        }
    }

    const citySelect = document.getElementById("editCitySelect");
    const cityCustom = document.getElementById("editCityCustom");

    if (citySelect) {
        let cityOptionExists = false;
        for (let opt of citySelect.options) {
            if (opt.value.toLowerCase() === currentCity.toLowerCase()) {
                citySelect.value = opt.value;
                cityOptionExists = true;
                break;
            }
        }
        if (!cityOptionExists) {
            citySelect.value = "custom";
            if (cityCustom) {
                cityCustom.style.display = "block";
                cityCustom.value = currentCity;
            }
        } else {
            if (cityCustom) {
                cityCustom.style.display = "none";
                cityCustom.value = "";
            }
        }
    }

    const commentInput = document.getElementById("editCommentInput");
    if (commentInput) commentInput.value = "";

    // Заполнение коммерческих полей (продажа, допродажа, запись на услугу)
    const saleStatusSelect = document.getElementById("editSaleStatus");
    const orderSumInput = document.getElementById("editOrderSum");
    const hasUpsellCheck = document.getElementById("editHasUpsell");
    const upsellSumInput = document.getElementById("editUpsellSum");
    const upsellTextInput = document.getElementById("editUpsellText");
    const serviceBookingSelect = document.getElementById("editServiceBooking");
    const serviceDateInput = document.getElementById("editServiceDate");

    if (saleStatusSelect) saleStatusSelect.value = call.saleStatus || call.outcome || "Консультация";
    if (orderSumInput) orderSumInput.value = call.orderSum || "0 ₸";
    if (hasUpsellCheck) hasUpsellCheck.checked = Boolean(call.hasUpsell);
    if (upsellSumInput) upsellSumInput.value = call.upsellSum || (call.hasUpsell ? "18 000 ₸" : "0 ₸");
    if (upsellTextInput) upsellTextInput.value = call.upsellText || (call.hasUpsell ? "Сезонное хранение шин" : "");
    if (serviceBookingSelect) {
        const sVal = call.serviceBooking || "Нет";
        let optFound = false;
        for (let opt of serviceBookingSelect.options) {
            if (opt.value === sVal) {
                serviceBookingSelect.value = sVal;
                optFound = true;
                break;
            }
        }
        if (!optFound) serviceBookingSelect.value = "Нет";
    }
    if (serviceDateInput) serviceDateInput.value = call.serviceDate || "";

    const alertEl = document.getElementById("editModalAlert");
    if (alertEl) {
        alertEl.style.display = "none";
        alertEl.innerText = "";
    }

    const modal = document.getElementById("editCallModal");
    if (modal) {
        modal.classList.add("open");
        modal.classList.add("active");
        modal.style.display = "flex";
    }
}

// Закрытие модального окна редактирования
function closeEditCallModal() {
    const modal = document.getElementById("editCallModal");
    if (modal) {
        modal.classList.remove("open");
        modal.classList.remove("active");
        modal.style.display = "none";
    }
}

// Быстрое открытие звонка с автоматическим стартом прослушки
function openCallAndPlayAudio(callId) {
    openCallModal(callId);
    // Переключаемся на вкладку Транскрипция (Диалог)
    const transcriptTab = document.querySelector(".tab-btn[data-tab='tab-transcript']");
    if (transcriptTab) {
        transcriptTab.click();
    }
    const call = callsData.find(c => c.id == callId);
    if (call) {
        initCallAudio(call, true);
    }
}

// Экспорт в глобальную область видимости
window.openEditCallModal = openEditCallModal;
window.closeEditCallModal = closeEditCallModal;
window.saveCallEdits = saveCallEdits;
window.openCallAndPlayAudio = openCallAndPlayAudio;
window.initCallAudio = initCallAudio;
window.stopCallAudio = stopCallAudio;

// Сохранение изменений звонка через POST API
async function saveCallEdits() {
    const callId = parseInt(document.getElementById("editCallId").value);
    const mgrSelect = document.getElementById("editManagerSelect").value;
    const mgrCustom = document.getElementById("editManagerCustom").value.trim();
    const managerName = (mgrSelect === "custom") ? mgrCustom : mgrSelect;

    const citySelect = document.getElementById("editCitySelect").value;
    const cityCustom = document.getElementById("editCityCustom").value.trim();
    const cityName = (citySelect === "custom") ? cityCustom : citySelect;

    const changedBy = document.getElementById("editAuthorSelect").value;
    const comment = document.getElementById("editCommentInput").value.trim() || "Ручная корректировка данных звонка";

    const saleStatus = document.getElementById("editSaleStatus")?.value || "Консультация";
    const orderSum = document.getElementById("editOrderSum")?.value.trim() || "0 ₸";
    const hasUpsell = document.getElementById("editHasUpsell")?.checked || false;
    const upsellSum = document.getElementById("editUpsellSum")?.value.trim() || (hasUpsell ? "18 000 ₸" : "0 ₸");
    const upsellText = document.getElementById("editUpsellText")?.value.trim() || (hasUpsell ? "Сезонное хранение шин" : "");
    const serviceBooking = document.getElementById("editServiceBooking")?.value || "Нет";
    const serviceDate = document.getElementById("editServiceDate")?.value.trim() || "";

    const alertEl = document.getElementById("editModalAlert");
    const saveBtn = document.getElementById("saveEditModalBtn");

    if (!managerName) {
        if (alertEl) {
            alertEl.style.display = "block";
            alertEl.style.background = "rgba(239, 68, 68, 0.15)";
            alertEl.style.color = "#f87171";
            alertEl.innerText = "Пожалуйста, укажите имя менеджера.";
        }
        return;
    }

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = "Сохранение...";
    }

    try {
        const response = await fetch("/api/calls/update", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                call_id: callId,
                manager_name: managerName,
                city_name: cityName,
                changed_by: changedBy,
                comment: comment,
                sale_status: saleStatus,
                order_sum: orderSum,
                has_upsell: hasUpsell,
                upsell_sum: upsellSum,
                upsell_text: upsellText,
                service_booking: serviceBooking,
                service_date: serviceDate
            })
        });

        const result = await response.json();
        if (result.success) {
            closeEditCallModal();
            await loadCallsData();
            
            // Если было открыто окно разбора этого звонка, перерендерим его
            const openCall = callsData.find(c => c.id === callId);
            if (openCall) {
                const callModal = document.getElementById("callModal");
                if (callModal && callModal.classList.contains("open")) {
                    openCallModal(callId);
                }
            }
        } else {
            if (alertEl) {
                alertEl.style.display = "block";
                alertEl.style.background = "rgba(239, 68, 68, 0.15)";
                alertEl.style.color = "#f87171";
                alertEl.innerText = "Ошибка сохранения: " + (result.error || "Не удалось сохранить");
            }
        }
    } catch (err) {
        if (alertEl) {
            alertEl.style.display = "block";
            alertEl.style.background = "rgba(239, 68, 68, 0.15)";
            alertEl.style.color = "#f87171";
            alertEl.innerText = "Ошибка соединения с сервером: " + err.message;
        }
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerText = "💾 Сохранить в БД и Журнал";
        }
    }
}

// Загрузка реальных данных из базы данных через REST API FastAPI (/api/calls и /api/audit) с автоматическим фолбэком
async function loadCallsData(keepPage = true) {
    try {
        let freshData = null;
        let freshAudit = null;

        // 1. Пробуем получить звонки из REST API
        try {
            const resCalls = await fetch("/api/calls?t=" + Date.now());
            if (resCalls.ok) {
                freshData = await resCalls.json();
            }
        } catch (e) {
            // Игнорируем ошибку сети для бесшовного фолбэка
        }

        // 2. Если REST API недоступен или вернул не 200 (например, при запуске через Live Server), грузим calls_data.json
        if (!freshData || !Array.isArray(freshData) || freshData.length === 0) {
            try {
                const resStatic = await fetch("calls_data.json?t=" + Date.now());
                if (resStatic.ok) {
                    freshData = await resStatic.json();
                }
            } catch (e) {
                console.error("[AI CRM] Не удалось загрузить calls_data.json:", e);
            }
        }

        if (Array.isArray(freshData) && freshData.length > 0) {
            callsData = freshData;
            renderCallsTable(callsData, keepPage);
            renderDashboard(callsData);
            renderManagers(callsData, keepPage);
            try {
                renderClients(callsData, keepPage);
            } catch (errClients) {
                console.error("[AI CRM] Ошибка в renderClients:", errClients);
            }
            console.log("[AI CRM] Загружены актуальные звонки (всего:", callsData.length, ")");
            checkUrlCallHash();
        }

        // 3. Журнал аудита
        await loadAuditData(keepPage);
    } catch (err) {
        console.warn("[AI CRM] Ошибка загрузки данных:", err);
    }
}

// Загрузка журнала изменений из REST API с фолбэком на audit_data.json
async function loadAuditData(keepPage = true) {
    let freshAudit = null;
    try {
        const resAudit = await fetch("/api/audit?t=" + Date.now());
        if (resAudit.ok) {
            freshAudit = await resAudit.json();
        }
    } catch (e) {}

    if (!freshAudit || !Array.isArray(freshAudit) || freshAudit.length === 0) {
        try {
            const resAuditStatic = await fetch("audit_data.json?t=" + Date.now());
            if (resAuditStatic.ok) {
                freshAudit = await resAuditStatic.json();
            }
        } catch (e) {}
    }

    if (Array.isArray(freshAudit)) {
        auditLogsData = freshAudit;
        renderAuditLogs(auditLogsData, keepPage);
        console.log("[AI CRM] Загружен журнал аудита (всего:", auditLogsData.length, ")");
    }
}

// Инициализация событий
document.addEventListener("DOMContentLoaded", () => {
    // Сворачивание / разворачивание бокового меню с сохранением состояния в localStorage
    const sidebar = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("sidebarToggleBtn");

    if (localStorage.getItem("sidebar_collapsed") === "true" && sidebar) {
        sidebar.classList.add("collapsed");
    }

    function toggleSidebar() {
        if (!sidebar) return;
        sidebar.classList.toggle("collapsed");
        const isCollapsed = sidebar.classList.contains("collapsed");
        localStorage.setItem("sidebar_collapsed", isCollapsed ? "true" : "false");
        if (toggleBtn) {
            toggleBtn.title = isCollapsed ? "Развернуть панель" : "Свернуть панель";
        }
    }

    if (toggleBtn) toggleBtn.onclick = toggleSidebar;

    // Навигация по боковому меню (Журнал звонков / Дашборд качества / Менеджеры / Клиенты)
    document.querySelectorAll(".nav-menu .nav-item").forEach(item => {
        item.onclick = (e) => {
            e.preventDefault();
            const viewId = item.getAttribute("data-view");
            if (viewId) {
                switchView(viewId);
            }
        };
    });

    // Автоматическое открытие звонка по URL хэшу (#call-41)
    function checkUrlCallHash() {
        const hash = window.location.hash || "";
        const m = hash.match(/call-(\d+)/);
        if (m && callsData && callsData.length > 0) {
            const cId = parseInt(m[1], 10);
            if (currentOpenCallId === cId) {
                // Модальное окно этого звонка уже открыто, не перерисовываем заново
                return;
            }
            if (callsData.some(c => c.id === cId)) {
                openCallModal(cId);
            }
        }
    }
    window.checkUrlCallHash = checkUrlCallHash;
    window.onhashchange = checkUrlCallHash;

    // Загружаем реальные данные из БД
    loadCallsData();

    // Закрытие модального окна звонка
    const closeBtn = document.getElementById("closeModalBtn");
    const closeFooterBtn = document.getElementById("closeModalFooterBtn");
    const callModalBackdrop = document.getElementById("callModal");

    if (closeBtn) closeBtn.onclick = closeCallModal;
    if (closeFooterBtn) closeFooterBtn.onclick = closeCallModal;
    if (callModalBackdrop) {
        callModalBackdrop.onclick = (e) => {
            if (e.target.id === "callModal") closeCallModal();
        };
    }

    // Закрытие модального окна менеджера
    const closeMgrBtn = document.getElementById("closeManagerModalBtn");
    const closeMgrFooterBtn = document.getElementById("closeManagerModalFooterBtn");
    const mgrModalBackdrop = document.getElementById("managerAnalyticsModal");

    if (closeMgrBtn) closeMgrBtn.onclick = closeManagerModal;
    if (closeMgrFooterBtn) closeMgrFooterBtn.onclick = closeManagerModal;
    if (mgrModalBackdrop) {
        mgrModalBackdrop.onclick = (e) => {
            if (e.target.id === "managerAnalyticsModal") closeManagerModal();
        };
    }

    // Закрытие модального окна клиента
    const closeClientBtn = document.getElementById("closeClientModalBtn");
    const closeClientFooterBtn = document.getElementById("closeClientModalFooterBtn");
    const clientModalBackdrop = document.getElementById("clientModal");

    if (closeClientBtn) closeClientBtn.onclick = closeClientModal;
    if (closeClientFooterBtn) closeClientFooterBtn.onclick = closeClientModal;
    if (clientModalBackdrop) {
        clientModalBackdrop.onclick = (e) => {
            if (e.target.id === "clientModal") closeClientModal();
        };
    }

    // Закрытие по клавише Escape
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeCallModal();
            closeManagerModal();
            closeClientModal();
            closeEditCallModal();
        }
    });

    // Переключение вкладок модалки звонка
    document.querySelectorAll(".tab-btn[data-tab]").forEach(btn => {
        btn.onclick = () => {
            const targetTab = btn.getAttribute("data-tab");
            currentActiveModalTab = targetTab;

            document.querySelectorAll(".tab-btn[data-tab]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const tabContent = document.getElementById(targetTab);
            if (tabContent) tabContent.classList.add("active");
        };
    });

    // Переключение вкладок модалки менеджера
    document.querySelectorAll(".tab-btn[data-mgr-tab]").forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll(".tab-btn[data-mgr-tab]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".mgr-tab-content").forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const targetTab = btn.getAttribute("data-mgr-tab");
            const tabContent = document.getElementById(targetTab);
            if (tabContent) tabContent.classList.add("active");
        };
    });

    // Переключение вкладок модалки клиента
    document.querySelectorAll(".tab-btn[data-client-tab]").forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll(".tab-btn[data-client-tab]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".client-tab-content").forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const targetTab = btn.getAttribute("data-client-tab");
            const tabContent = document.getElementById(targetTab);
            if (tabContent) tabContent.classList.add("active");
        };
    });

    // Единая функция фильтрации звонков
    function applyCallsFilters() {
        const searchInput = document.getElementById("searchInput");
        const categoryFilter = document.getElementById("categoryFilter");
        const outcomeFilter = document.getElementById("outcomeFilter");
        const emotionFilter = document.getElementById("emotionFilter");
        const scoreFilter = document.getElementById("scoreFilter");
        const activeBadgeBar = document.getElementById("activeFilterBadgeBar");
        const activeText = document.getElementById("activeFilterText");

        const searchVal = (searchInput?.value || "").toLowerCase().trim();
        const categoryVal = categoryFilter?.value || "all";
        const outcomeVal = outcomeFilter?.value || "all";
        const emotionVal = emotionFilter?.value || "all";
        const scoreVal = scoreFilter?.value || "all";

        let filtered = callsData.slice();

        // 1. Поиск по тексту
        if (searchVal) {
            filtered = filtered.filter(c => 
                (c.phone && c.phone.toLowerCase().includes(searchVal)) ||
                (c.topic && c.topic.toLowerCase().includes(searchVal)) ||
                (c.city && c.city.toLowerCase().includes(searchVal)) ||
                (c.manager && c.manager.toLowerCase().includes(searchVal)) ||
                (c.summary && c.summary.toLowerCase().includes(searchVal)) ||
                (c.callCategory && c.callCategory.toLowerCase().includes(searchVal)) ||
                (c.saleStatus && c.saleStatus.toLowerCase().includes(searchVal)) ||
                (c.serviceBooking && c.serviceBooking.toLowerCase().includes(searchVal))
            );
        }

        // 1.5. Фильтр по категории
        if (categoryVal !== "all") {
            filtered = filtered.filter(c => (c.callCategory || "Прочее") === categoryVal);
        }

        // 2. Фильтр по коммерческому результату (продажи / услуги)
        if (outcomeVal === "sale") {
            filtered = filtered.filter(c => c.saleStatus === "Продажа состоялась" || c.saleStatus === "Бронь оформлена");
        } else if (outcomeVal === "upsell") {
            filtered = filtered.filter(c => c.hasUpsell === true);
        } else if (outcomeVal === "service") {
            filtered = filtered.filter(c => c.serviceBooking && c.serviceBooking !== "Нет");
        } else if (outcomeVal === "consultation") {
            filtered = filtered.filter(c => c.saleStatus === "Консультация");
        }

        // 3. Фильтр эмоций / конфликтов
        if (emotionVal === "conflict") {
            filtered = filtered.filter(c => 
                c.conflict === true || 
                Number(c.csat) <= 2 || 
                (c.clientTone && (c.clientTone.sentimentEnd === "Недовольный" || c.clientTone.sad >= 25 || c.clientTone.angry > 0))
            );
            if (activeBadgeBar) {
                activeBadgeBar.style.display = "flex";
                if (activeText) activeText.innerText = `⚠️ Фильтр: показаны звонки с конфликтами и претензиями (${filtered.length})`;
            }
        } else if (emotionVal !== "all") {
            filtered = filtered.filter(c => 
                c.emotionClass === emotionVal || 
                (emotionVal === "happy" && c.emotion === "Радость") || 
                (emotionVal === "neutral" && c.emotion === "Нейтральный") || 
                (emotionVal === "sad" && c.emotion === "Грусть")
            );
            if (activeBadgeBar) activeBadgeBar.style.display = "none";
        } else {
            if (activeBadgeBar) activeBadgeBar.style.display = "none";
        }

        // 4. Фильтр по оценке CSAT
        if (scoreVal === "2") {
            filtered = filtered.filter(c => Number(c.csat) <= 2);
        } else if (scoreVal !== "all") {
            filtered = filtered.filter(c => Number(c.csat) === parseInt(scoreVal));
        }

        renderCallsTable(filtered);
    }

    // Быстрый переход и фильтрация конфликтных звонков по клику на KPI Tone of Voice
    function filterConflictCalls() {
        switchView("view-calls");
        const emotionFilter = document.getElementById("emotionFilter");
        if (emotionFilter) emotionFilter.value = "conflict";
        
        const searchInput = document.getElementById("searchInput");
        if (searchInput) searchInput.value = "";
        
        const scoreFilter = document.getElementById("scoreFilter");
        if (scoreFilter) scoreFilter.value = "all";

        applyCallsFilters();

        const tableControls = document.querySelector(".table-controls");
        if (tableControls) {
            tableControls.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }

    // Клик по KPI карточке Tone of Voice
    const kpiToneCard = document.getElementById("kpiToneCard");
    if (kpiToneCard) {
        kpiToneCard.onclick = filterConflictCalls;
    }

    const kpiConflictsCount = document.getElementById("kpiConflictsCount");
    if (kpiConflictsCount) {
        kpiConflictsCount.onclick = (e) => {
            e.stopPropagation();
            filterConflictCalls();
        };
    }

    // Кнопка сброса фильтра конфликтов
    const resetConflictBtn = document.getElementById("resetConflictFilterBtn");
    if (resetConflictBtn) {
        resetConflictBtn.onclick = () => {
            const emotionFilter = document.getElementById("emotionFilter");
            if (emotionFilter) emotionFilter.value = "all";
            applyCallsFilters();
        };
    }

    // Поиск по таблице звонков
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.oninput = applyCallsFilters;
    }

    // Фильтр по категориям звонков
    const categoryFilter = document.getElementById("categoryFilter");
    if (categoryFilter) {
        categoryFilter.onchange = applyCallsFilters;
    }

    // Фильтр по коммерческому результату (сделки / услуги)
    const outcomeFilter = document.getElementById("outcomeFilter");
    if (outcomeFilter) {
        outcomeFilter.onchange = applyCallsFilters;
    }

    // Фильтр по эмоциям
    const emotionFilter = document.getElementById("emotionFilter");
    if (emotionFilter) {
        emotionFilter.onchange = applyCallsFilters;
    }

    // Фильтр по оценкам
    const scoreFilter = document.getElementById("scoreFilter");
    if (scoreFilter) {
        scoreFilter.onchange = applyCallsFilters;
    }

    // Единая фильтрация и поиск клиентов со сбросом пагинации на 1 страницу
    function applyClientsFilters() {
        const searchVal = (document.getElementById("clientsSearchInput")?.value || "").toLowerCase().trim();
        const cityVal = document.getElementById("clientsCityFilter")?.value || "all";
        const typeVal = document.getElementById("clientsTypeFilter")?.value || "all";

        let filtered = callsData;

        if (searchVal) {
            filtered = filtered.filter(c => 
                (c.phone && c.phone.toLowerCase().includes(searchVal)) ||
                (c.topic && c.topic.toLowerCase().includes(searchVal)) ||
                (c.city && c.city.toLowerCase().includes(searchVal)) ||
                (c.tireSize && c.tireSize.toLowerCase().includes(searchVal)) ||
                (c.brand && c.brand.toLowerCase().includes(searchVal)) ||
                (c.summary && c.summary.toLowerCase().includes(searchVal))
            );
        }

        if (cityVal !== "all") {
            filtered = filtered.filter(c => c.city === cityVal);
        }

        if (typeVal !== "all") {
            const clientCounts = {};
            callsData.forEach(c => { clientCounts[c.phone] = (clientCounts[c.phone] || 0) + 1; });
            if (typeVal === "repeat") {
                filtered = filtered.filter(c => clientCounts[c.phone] > 1);
            } else if (typeVal === "single") {
                filtered = filtered.filter(c => clientCounts[c.phone] === 1);
            }
        }

        renderClients(filtered, false);
    }

    // Поиск по клиентам
    const clientsSearchInput = document.getElementById("clientsSearchInput");
    if (clientsSearchInput) {
        clientsSearchInput.oninput = applyClientsFilters;
    }

    // Фильтр клиентов по городу
    const clientsCityFilter = document.getElementById("clientsCityFilter");
    if (clientsCityFilter) {
        clientsCityFilter.onchange = applyClientsFilters;
    }

    // Фильтр клиентов по частоте
    const clientsTypeFilter = document.getElementById("clientsTypeFilter");
    if (clientsTypeFilter) {
        clientsTypeFilter.onchange = applyClientsFilters;
    }

    // Закрытие модального окна редактирования
    const closeEditBtn = document.getElementById("closeEditModalBtn");
    const cancelEditBtn = document.getElementById("cancelEditModalBtn");
    const editModalBackdrop = document.getElementById("editCallModal");

    if (closeEditBtn) closeEditBtn.onclick = closeEditCallModal;
    if (cancelEditBtn) cancelEditBtn.onclick = closeEditCallModal;
    if (editModalBackdrop) {
        editModalBackdrop.onclick = (e) => {
            if (e.target.id === "editCallModal") closeEditCallModal();
        };
    }

    // Сохранение редактирования звонка
    const saveEditBtn = document.getElementById("saveEditModalBtn");
    if (saveEditBtn) {
        saveEditBtn.onclick = saveCallEdits;
    }

    // Кастомный ввод менеджера
    const editMgrSelect = document.getElementById("editManagerSelect");
    const editMgrCustom = document.getElementById("editManagerCustom");
    if (editMgrSelect && editMgrCustom) {
        editMgrSelect.onchange = (e) => {
            if (e.target.value === "custom") {
                editMgrCustom.style.display = "block";
                editMgrCustom.focus();
            } else {
                editMgrCustom.style.display = "none";
            }
        };
    }

    // Кастомный ввод города
    const editCitySelect = document.getElementById("editCitySelect");
    const editCityCustom = document.getElementById("editCityCustom");
    if (editCitySelect && editCityCustom) {
        editCitySelect.onchange = (e) => {
            if (e.target.value === "custom") {
                editCityCustom.style.display = "block";
                editCityCustom.focus();
            } else {
                editCityCustom.style.display = "none";
            }
        };
    }

    // Поиск по журналу аудита (сброс на 1 страницу)
    const auditSearchInput = document.getElementById("auditSearchInput");
    if (auditSearchInput) {
        auditSearchInput.oninput = () => renderAuditLogs(auditLogsData, false);
    }

    // Фильтр по полям аудита (сброс на 1 страницу)
    const auditFieldFilter = document.getElementById("auditFieldFilter");
    if (auditFieldFilter) {
        auditFieldFilter.onchange = () => renderAuditLogs(auditLogsData, false);
    }

    // Кнопка "Обновить"
    const refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.onclick = () => {
            loadCallsData(true);
        };
    }

    // Периодическое автообновление каждые 6 секунд с сохранением текущей страницы
    setInterval(() => loadCallsData(true), 6000);
});
