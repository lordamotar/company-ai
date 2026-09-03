// Инициализация пустого массива данных (заполняется ИСКЛЮЧИТЕЛЬНО из базы данных)
let callsData = [];

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

    // Средняя оценка CSAT
    const totalCsat = data.reduce((acc, c) => acc + (Number(c.csat) || 0), 0);
    const avgCsat = (totalCsat / total).toFixed(1);
    if (avgScoreEl) avgScoreEl.innerHTML = `${avgCsat} <span class="max-val">/ 5.0</span>`;

    // Среднее соблюдение скрипта
    const totalScript = data.reduce((acc, c) => acc + (Number(c.scriptScore) || 0), 0);
    const avgScript = Math.round(totalScript / total);
    if (scriptScoreEl) scriptScoreEl.innerText = `${avgScript}%`;

    // Доля позитивных эмоций
    const happyCalls = data.filter(c => c.emotionClass === "happy" || c.emotion === "Радость").length;
    const happyPct = Math.round((happyCalls / total) * 100);
    if (posToneEl) posToneEl.innerText = `${happyPct}%`;

    // Конфликты
    const conflicts = data.filter(c => c.conflict).length;
    if (conflictsEl) conflictsEl.innerText = `${conflicts} конфликтов зафиксировано`;

    // Оценка упущенных продаж (по непройденным пунктам допродаж)
    const missedOppCount = data.filter(c => Array.isArray(c.checklist) && c.checklist.some(i => !i.passed)).length;
    if (lostSalesEl) lostSalesEl.innerText = `~${(missedOppCount * 18000).toLocaleString('ru-RU')} ₸`;
}

// Рендеринг таблицы звонков (Экран 1)
function renderCallsTable(data) {
    const tbody = document.getElementById("callsTableBody");
    if (!tbody) return;
    tbody.innerHTML = "";

    updateKpis(callsData);

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
        return;
    }

    data.forEach(call => {
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
            emotionBadge = `<span class="badge badge-neutral">😐 ${call.emotion || "Нейтрально"} ${call.emotionPct || ""}</span>`;
        }

        const isOut = (call.direction === "out");
        const dirBadge = isOut 
            ? `<span class="badge badge-primary" style="font-size: 10px; padding: 2px 6px;">↗ Исх</span>` 
            : `<span class="badge badge-outline" style="font-size: 10px; padding: 2px 6px;">↙ Вх</span>`;

        const managerName = call.manager || "Оператор";
        const managerInitial = managerName[0] || "О";

        tr.innerHTML = `
            <td>
                <div class="call-id-col">
                    <span class="call-num">#${call.id} ${dirBadge}</span>
                    <span class="call-time">${call.date || "—"} ${call.time || ""}</span>
                </div>
            </td>
            <td><span class="phone-badge">${call.phone || "Клиент"}</span></td>
            <td>
                <div class="manager-badge">
                    <span class="manager-avatar-sm">${managerInitial}</span>
                    <span>${managerName}</span>
                </div>
            </td>
            <td><strong>${call.topic || "Консультация"}</strong></td>
            <td>${call.duration || "—"}</td>
            <td>${emotionBadge}</td>
            <td><span class="stars-score">⭐ ${call.csat || 5}.0 / 5</span></td>
            <td><span class="badge badge-primary">${call.scriptScore || 80}%</span></td>
            <td><span class="badge ${call.statusClass || 'badge-success'}">${call.status || 'Обработан'}</span></td>
            <td class="text-right">
                <button class="action-btn" onclick="event.stopPropagation(); openCallModal(${call.id});">
                    Разбор AI
                </button>
            </td>
        `;
        tbody.appendChild(tr);
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

    // 1. Этапы скрипта продаж
    let greetingPassed = 0;
    let needsPassed = 0;
    let cityPassed = 0;
    let upsellPassed = 0;

    data.forEach(call => {
        if (Array.isArray(call.checklist)) {
            call.checklist.forEach(item => {
                const title = (item.title || "").toLowerCase();
                if (item.passed) {
                    if (title.includes("приветств") || title.includes("имя")) greetingPassed++;
                    else if (title.includes("потребност") || title.includes("размер")) needsPassed++;
                    else if (title.includes("город") || title.includes("наличи") || title.includes("акци")) cityPassed++;
                    else if (title.includes("допродаж") || title.includes("хранен") || title.includes("монтаж")) upsellPassed++;
                }
            });
        }
    });

    const greetPct = Math.round((greetingPassed / total) * 100);
    const needsPct = Math.round((needsPassed / total) * 100);
    const cityPct = Math.round((cityPassed / total) * 100);
    const upsellPct = Math.round((upsellPassed / total) * 100);

    const avgScript = Math.round((greetPct + needsPct + cityPct + upsellPct) / 4);
    if (avgScriptBadge) avgScriptBadge.innerText = `${avgScript}% среднее`;

    scriptBarsList.innerHTML = `
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>1. Приветствие и стандарт бренда</span><span>${greetPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill green" style="width: ${greetPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>2. Выявление потребности и параметров шин</span><span>${needsPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill blue" style="width: ${needsPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>3. Уточнение города и проверка наличия</span><span>${cityPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill purple" style="width: ${cityPct}%;"></div></div>
        </div>
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>4. Допродажа сопутствующих услуг (хранение/монтаж)</span><span>${upsellPct}%</span></div>
            <div class="script-bar-track"><div class="script-bar-fill yellow" style="width: ${upsellPct}%;"></div></div>
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
        <div class="script-bar-item">
            <div class="script-bar-meta"><span>😠 Раздражение / Конфликт</span><span>${angryAvg}%</span></div>
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

// Рендеринг карточек менеджеров (Экран 3)
function renderManagers(data) {
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
        managersMap[name].totalCsat += Number(call.csat) || 0;
        managersMap[name].totalScript += Number(call.scriptScore) || 0;
        if (call.emotionClass === "happy" || call.emotion === "Радость") {
            managersMap[name].happyCount++;
        }
    });

    const managersList = Object.values(managersMap);

    managersList.forEach(m => {
        const count = m.calls.length;
        const avgCsat = (m.totalCsat / count).toFixed(1);
        const avgScript = Math.round(m.totalScript / count);
        const happyPct = Math.round((m.happyCount / count) * 100);
        const initial = m.name[0] || "М";

        const card = document.createElement("div");
        card.className = "manager-card";
        card.innerHTML = `
            <div class="manager-card-top">
                <div class="manager-avatar-lg">${initial}</div>
                <div class="manager-title-box">
                    <div class="manager-name-lg">${m.name}</div>
                    <div class="manager-role-tag">Менеджер отдела продаж</div>
                </div>
                <span class="badge badge-success">⭐ ${avgCsat} / 5.0</span>
            </div>

            <div class="manager-stats-grid">
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
                <button class="btn-filter-manager" onclick="filterByManager('${m.name}')">
                    Посмотреть звонки (${count})
                </button>
            </div>
        `;
        grid.appendChild(card);
    });
}

// Фильтрация звонков по выбранному менеджеру и переключение на экран таблицы
function filterByManager(managerName) {
    switchView("view-calls");
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.value = managerName;
    }
    const filtered = callsData.filter(c => c.manager && c.manager.toLowerCase().includes(managerName.toLowerCase()));
    renderCallsTable(filtered);
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
    }
}

// Открытие модального окна с детальным разбором звонка
function openCallModal(callId) {
    const call = callsData.find(c => c.id === callId);
    if (!call) return;

    // Шапка
    document.getElementById("modalCallId").innerText = `#${call.id}`;
    document.getElementById("modalCallDate").innerText = `${call.date || ''} ${call.time || ''}`;
    document.getElementById("modalCallDirection").innerText = call.directionLabel || (call.direction === "out" ? "Исходящий звонок" : "Входящий звонок");
    document.getElementById("modalPhoneTitle").innerText = `${call.phone || 'Клиент'} (${call.city || 'Казахстан'})`;

    // Вкладка 1: Резюме
    document.getElementById("modalSummaryText").innerText = call.summary || "Транскрипция и краткое резюме звонка.";
    document.getElementById("modalCity").innerText = call.city || "Казахстан";
    document.getElementById("modalProductCat").innerText = call.productCat || "Шины / Сервис";
    document.getElementById("modalTireSize").innerText = call.tireSize || "—";
    document.getElementById("modalSelectedBrand").innerText = call.brand || "—";
    document.getElementById("modalOrderSum").innerText = call.orderSum || "—";
    document.getElementById("modalOutcome").innerText = call.outcome || "Консультация проведена";
    document.getElementById("modalOpportunityText").innerText = call.opportunity || "Предложить сопутствующие услуги.";

    const csatVal = call.csat || 5;
    document.getElementById("modalCsatStars").innerText = `⭐`.repeat(csatVal) + ` ${csatVal}/5`;
    document.getElementById("modalManagerPoliteness").innerText = call.managerPoliteness || `${csatVal} / 5`;
    document.getElementById("modalConflictStatus").innerText = call.conflict ? "Обнаружен конфликт" : "Не зафиксирован";
    document.getElementById("modalScriptPct").innerText = `${call.scriptScore || 85}%`;

    // Вкладка 2: Транскрипция (Диалог)
    document.getElementById("modalAudioDuration").innerText = call.duration || "—";
    const dialogueContainer = document.getElementById("modalDialogueContainer");
    dialogueContainer.innerHTML = "";

    if (Array.isArray(call.dialogue) && call.dialogue.length > 0) {
        call.dialogue.forEach(msg => {
            const msgDiv = document.createElement("div");
            msgDiv.className = `message-row ${msg.role || 'manager'}`;

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
                    <span class="msg-speaker">${msg.speaker || 'Собеседник'}</span>
                    <span class="sentiment-tag ${sentimentClass}">${sentimentLabel}</span>
                </div>
                <div class="msg-text">${msg.text}</div>
            `;
            dialogueContainer.appendChild(msgDiv);
        });
    } else {
        dialogueContainer.innerHTML = `<p style="color: var(--text-muted); padding: 20px; text-align: center;">Транскрипция диалога отсутствует.</p>`;
    }

    // Вкладка 3: Тон-войс и Акустика
    const emotionBarsContainer = document.getElementById("modalEmotionBars");
    const emDist = call.emotionsDist || { happy: 50, neutral: 30, sad: 15, angry: 5 };
    emotionBarsContainer.innerHTML = `
        <div class="emotion-bar-item">
            <div class="em-info"><span>😊 Радость / Дружелюбие</span><span>${emDist.happy || 0}%</span></div>
            <div class="em-track"><div class="em-fill happy" style="width: ${emDist.happy || 0}%;"></div></div>
        </div>
        <div class="emotion-bar-item">
            <div class="em-info"><span>😐 Нейтральный тон</span><span>${emDist.neutral || 0}%</span></div>
            <div class="em-track"><div class="em-fill neutral" style="width: ${emDist.neutral || 0}%;"></div></div>
        </div>
        <div class="emotion-bar-item">
            <div class="em-info"><span>🙁 Грусть / Сомнения</span><span>${emDist.sad || 0}%</span></div>
            <div class="em-track"><div class="em-fill sad" style="width: ${emDist.sad || 0}%;"></div></div>
        </div>
        <div class="emotion-bar-item">
            <div class="em-info"><span>😠 Гнев / Раздражение</span><span>${emDist.angry || 0}%</span></div>
            <div class="em-track"><div class="em-fill angry" style="width: ${emDist.angry || 0}%;"></div></div>
        </div>
    `;

    const acoustics = call.acoustics || {};
    document.getElementById("modalSilenceMetric").innerText = acoustics.silence || "—";
    document.getElementById("modalLoudMetric").innerText = acoustics.loudMoments || "0%";
    document.getElementById("modalTempoMetric").innerText = acoustics.tempo || "130 уд/мин";

    // Посегментный таймлайн
    const timelineContainer = document.getElementById("modalTimelineTrack");
    timelineContainer.innerHTML = "";
    if (Array.isArray(call.timeline) && call.timeline.length > 0) {
        call.timeline.forEach(seg => {
            const segDiv = document.createElement("div");
            segDiv.className = `timeline-segment ${seg.emotion || 'happy'}`;
            segDiv.title = `Интервал ${seg.time} — ${seg.emotion} (${seg.pct}%)`;
            segDiv.innerHTML = `<span>${seg.time}</span><span>${Math.round(seg.pct || 0)}%</span>`;
            timelineContainer.appendChild(segDiv);
        });
    } else {
        timelineContainer.innerHTML = `<span style="color: var(--text-muted); font-size: 12px;">Таймлайн формируется...</span>`;
    }

    // Вкладка 4: Чек-лист скрипта
    const checklistContainer = document.getElementById("modalChecklistItems");
    checklistContainer.innerHTML = "";
    const checklist = Array.isArray(call.checklist) && call.checklist.length > 0 ? call.checklist : [
        { title: "Приветствие и имя компании", desc: "Соблюдение корпоративного стандарта", passed: true },
        { title: "Выявление потребности", desc: "Уточнение параметров заказа", passed: true },
        { title: "Допродажа услуг", desc: "Предложение дополнительных сервисов", passed: false }
    ];

    checklist.forEach(item => {
        const clDiv = document.createElement("div");
        clDiv.className = "checklist-item";
        clDiv.innerHTML = `
            <div>
                <div class="cl-title">${item.title}</div>
                <div class="cl-desc">${item.desc}</div>
            </div>
            <div>
                ${item.passed ? '<span class="badge badge-success">✓ Выполнено</span>' : '<span class="badge badge-warning">✗ Пропущено</span>'}
            </div>
        `;
        checklistContainer.appendChild(clDiv);
    });

    // Открытие окна
    document.getElementById("callModal").classList.add("open");
}

// Закрытие модального окна
function closeCallModal() {
    const modal = document.getElementById("callModal");
    if (modal) modal.classList.remove("open");
}

// Загрузка реальных данных из базы данных через calls_data.json
async function loadCallsData() {
    try {
        const res = await fetch("calls_data.json?t=" + new Date().getTime());
        if (res.ok) {
            const freshData = await res.json();
            if (Array.isArray(freshData)) {
                callsData = freshData;
                renderCallsTable(callsData);
                renderDashboard(callsData);
                renderManagers(callsData);
                console.log("[AI CRM] Загружены реальные звонки из PostgreSQL:", callsData.length);
                return;
            }
        }
    } catch (err) {
        console.warn("[AI CRM] Ошибка загрузки calls_data.json:", err);
    }
    
    // Если файл пуст или не найден, отображаем пустое состояние (БЕЗ ТЕСТОВЫХ ДАННЫХ)
    callsData = [];
    renderCallsTable([]);
    renderDashboard([]);
    renderManagers([]);
}

// Инициализация событий
document.addEventListener("DOMContentLoaded", () => {
    // Навигация по боковому меню (Журнал звонков / Дашборд качества / Менеджеры)
    document.querySelectorAll(".nav-menu .nav-item").forEach(item => {
        item.onclick = (e) => {
            e.preventDefault();
            const viewId = item.getAttribute("data-view");
            if (viewId) {
                switchView(viewId);
            }
        };
    });

    // Загружаем реальные данные из БД
    loadCallsData();

    // Закрытие модального окна
    const closeBtn = document.getElementById("closeModalBtn");
    const closeFooterBtn = document.getElementById("closeModalFooterBtn");
    const modalBackdrop = document.getElementById("callModal");

    if (closeBtn) closeBtn.onclick = closeCallModal;
    if (closeFooterBtn) closeFooterBtn.onclick = closeCallModal;
    if (modalBackdrop) {
        modalBackdrop.onclick = (e) => {
            if (e.target.id === "callModal") closeCallModal();
        };
    }

    // Переключение вкладок модалки
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const targetTab = btn.getAttribute("data-tab");
            const tabContent = document.getElementById(targetTab);
            if (tabContent) tabContent.classList.add("active");
        };
    });

    // Поиск по таблице
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.oninput = (e) => {
            const val = e.target.value.toLowerCase().trim();
            const filtered = callsData.filter(c => 
                (c.phone && c.phone.toLowerCase().includes(val)) ||
                (c.topic && c.topic.toLowerCase().includes(val)) ||
                (c.city && c.city.toLowerCase().includes(val)) ||
                (c.manager && c.manager.toLowerCase().includes(val))
            );
            renderCallsTable(filtered);
        };
    }

    // Фильтр по эмоциям
    const emotionFilter = document.getElementById("emotionFilter");
    if (emotionFilter) {
        emotionFilter.onchange = (e) => {
            const val = e.target.value;
            if (val === "all") {
                renderCallsTable(callsData);
            } else {
                const filtered = callsData.filter(c => c.emotionClass === val || (val === "happy" && c.emotion === "Радость") || (val === "neutral" && c.emotion === "Нейтральный") || (val === "sad" && c.emotion === "Грусть"));
                renderCallsTable(filtered);
            }
        };
    }

    // Фильтр по оценкам
    const scoreFilter = document.getElementById("scoreFilter");
    if (scoreFilter) {
        scoreFilter.onchange = (e) => {
            const val = e.target.value;
            if (val === "all") {
                renderCallsTable(callsData);
            } else {
                const filtered = callsData.filter(c => Number(c.csat) === parseInt(val));
                renderCallsTable(filtered);
            }
        };
    }

    // Кнопка "Обновить"
    const refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.onclick = () => {
            loadCallsData();
        };
    }

    // Периодическое автообновление каждые 6 секунд
    setInterval(loadCallsData, 6000);
});
