/* Страница «Филиалы»: справочник филиалов (город, название, адрес, другие написания). */
(function () {
    "use strict";

    const KZ_CITIES = ["Алматы", "Астана", "Шымкент", "Караганда", "Актобе", "Тараз", "Павлодар", "Усть-Каменогорск",
        "Семей", "Атырау", "Костанай", "Кызылорда", "Уральск", "Петропавловск", "Актау", "Темиртау", "Туркестан",
        "Кокшетау", "Талдыкорган", "Экибастуз", "Рудный", "Жезказган", "Балхаш", "Конаев"];

    const $ = (id) => document.getElementById(id);
    const state = { branches: [], unmatched: [], skipped: 0, editingId: null, inited: false };

    function el(tag, props, children) {
        const node = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (k === "class") node.className = v;
            else if (k === "text") node.textContent = v;
            else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
            else node.setAttribute(k, v);
        });
        (children || []).forEach(c => node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
        return node;
    }

    function message(text, kind) {
        const m = $("brMessage");
        if (!m) return;
        m.textContent = text || "";
        m.className = "br-message" + (kind ? " " + kind : "");
    }

    async function api(method, url, body) {
        const res = await fetch(url, {
            method,
            headers: body ? { "Content-Type": "application/json" } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        let data = null;
        try { data = await res.json(); } catch (e) { /* пустой ответ */ }
        if (!res.ok) {
            const detail = data && data.detail;
            throw new Error(typeof detail === "string" ? detail : "Ошибка сервера (" + res.status + ")");
        }
        return data;
    }

    async function load() {
        try {
            const data = await api("GET", "/api/branches?t=" + Date.now());
            state.branches = data.branches || [];
            state.unmatched = data.unmatched || [];
            state.skipped = data.uninformativeCalls || 0;
            render();
        } catch (e) {
            const body = $("brTableBody");
            if (body) {
                body.innerHTML = "";
                const td = el("td", { colspan: "6", class: "br-empty", text: "Справочник недоступен: " + e.message + ". Страница работает только при запуске через сервер (python server.py)." });
                body.appendChild(el("tr", {}, [td]));
            }
        }
    }

    // Страница звонков после изменений справочника подтягивает новые названия филиалов
    function refreshCalls() {
        if (typeof window.loadCallsData === "function") window.loadCallsData(true);
    }

    async function afterChange() {
        await load();
        refreshCalls();
    }

    function render() {
        const badge = $("totalBranchesBadge");
        if (badge) badge.textContent = String(state.branches.length);

        const body = $("brTableBody");
        body.innerHTML = "";
        if (!state.branches.length) {
            body.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "br-empty", text: "Справочник пока пуст. Добавьте первый филиал формой выше или создайте его из названий звонков ниже." })]));
        }
        state.branches.forEach(b => {
            const aliases = el("td", {}, (b.aliases || []).length
                ? b.aliases.map(a => el("span", { class: "br-alias", text: a }))
                : [el("span", { class: "br-muted", text: "—" })]);
            const actions = el("td", { class: "text-right" }, [el("div", { class: "br-row-actions" }, [
                el("button", { class: "btn btn-secondary", type: "button", text: "Изменить", onclick: () => startEdit(b) }),
                el("button", { class: "btn btn-secondary", type: "button", text: "Удалить", onclick: () => removeBranch(b) }),
            ])]);
            body.appendChild(el("tr", {}, [
                el("td", { text: b.city }),
                el("td", {}, [el("strong", { text: b.name })]),
                el("td", { text: b.address || "—" }),
                aliases,
                el("td", { text: String(b.calls || 0) }),
                actions,
            ]));
        });

        const list = $("brUnmatchedList");
        list.innerHTML = "";
        $("brUnmatchedCount").textContent = String(state.unmatched.length);
        $("brUnmatchedCard").style.display = (state.unmatched.length || state.skipped) ? "" : "none";
        if (!state.unmatched.length) {
            list.appendChild(el("p", { class: "br-hint", text: "Все названия из звонков сопоставлены с филиалами." }));
        }
        state.unmatched.forEach(u => {
            const select = el("select", { class: "filter-select" }, [el("option", { value: "", text: "Добавить как написание к…" })]
                .concat(state.branches.map(b => el("option", { value: String(b.id), text: b.city + " — " + b.name }))));
            select.addEventListener("change", async () => {
                if (!select.value) return;
                try {
                    await api("POST", "/api/branches/" + select.value + "/aliases", { alias: u.raw });
                    await afterChange();
                } catch (e) { message(e.message, "error"); select.value = ""; }
            });
            list.appendChild(el("div", { class: "br-unmatched-row" }, [
                el("div", { class: "br-unmatched-name" }, [
                    u.raw,
                    el("small", { text: u.calls + " " + plural(u.calls) + (u.cityGuess ? " · похоже на г. " + u.cityGuess : "") }),
                ]),
                el("button", { class: "btn btn-secondary", type: "button", text: "Создать филиал", onclick: () => prefill(u) }),
                state.branches.length ? select : "",
            ]));
        });
        $("brSkippedNote").textContent = state.skipped
            ? "Ещё " + state.skipped + " " + plural(state.skipped) + " с общим названием («Филиал Aikos», «Не указан», только город): по ним филиал определить нельзя."
            : "";
    }

    function plural(n) {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return "звонок";
        if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "звонка";
        return "звонков";
    }

    function resetForm() {
        state.editingId = null;
        $("brForm").reset();
        $("brFormTitle").textContent = "Добавить филиал";
        $("brSaveBtn").textContent = "Добавить";
        $("brCancelBtn").style.display = "none";
    }

    function startEdit(b) {
        state.editingId = b.id;
        $("brCity").value = b.city;
        $("brName").value = b.name;
        $("brAddress").value = b.address || "";
        $("brAliases").value = (b.aliases || []).join(", ");
        $("brFormTitle").textContent = "Изменить филиал";
        $("brSaveBtn").textContent = "Сохранить";
        $("brCancelBtn").style.display = "";
        message("");
        $("brForm").scrollIntoView({ behavior: "smooth", block: "center" });
        $("brName").focus();
    }

    function prefill(u) {
        resetForm();
        $("brCity").value = u.cityGuess || "";
        $("brName").value = u.raw;
        message("");
        $("brForm").scrollIntoView({ behavior: "smooth", block: "center" });
        $(u.cityGuess ? "brAddress" : "brCity").focus();
    }

    async function removeBranch(b) {
        const note = b.calls ? "\n\nУ " + b.calls + " " + plural(b.calls) + " останется исходное название из разговора." : "";
        if (!window.confirm("Удалить филиал «" + b.name + "» (" + b.city + ")?" + note)) return;
        try {
            await api("DELETE", "/api/branches/" + b.id);
            if (state.editingId === b.id) resetForm();
            await afterChange();
        } catch (e) { message(e.message, "error"); }
    }

    async function submitForm(ev) {
        ev.preventDefault();
        const payload = {
            city: $("brCity").value.trim(),
            name: $("brName").value.trim(),
            address: $("brAddress").value.trim(),
            aliases: $("brAliases").value.split(",").map(s => s.trim()).filter(Boolean),
        };
        const btn = $("brSaveBtn");
        btn.disabled = true;
        try {
            if (state.editingId) await api("PUT", "/api/branches/" + state.editingId, payload);
            else await api("POST", "/api/branches", payload);
            const wasEditing = Boolean(state.editingId);
            resetForm();
            message(wasEditing ? "Сохранено." : "Филиал добавлен.", "ok");
            await afterChange();
        } catch (e) {
            message(e.message, "error");
        } finally {
            btn.disabled = false;
        }
    }

    function init() {
        if (!state.inited) {
            state.inited = true;
            const dl = $("brCityList");
            KZ_CITIES.forEach(c => dl.appendChild(el("option", { value: c })));
            $("brForm").addEventListener("submit", submitForm);
            $("brCancelBtn").addEventListener("click", () => { resetForm(); message(""); });
        }
        load();
    }

    window.initBranches = init;

    // Счётчик в меню показываем сразу после загрузки страницы
    document.addEventListener("DOMContentLoaded", () => {
        api("GET", "/api/branches?t=" + Date.now()).then(d => {
            const badge = $("totalBranchesBadge");
            if (badge) badge.textContent = String((d.branches || []).length);
        }).catch(() => {});
    });
})();
