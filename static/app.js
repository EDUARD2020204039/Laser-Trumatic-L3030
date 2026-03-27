const signalLabels = {
    machine_on: { on: "Opreste masina", off: "Porneste masina" },
    cutting_active: { on: "Opreste productia", off: "Porneste productia" },
    table_change: { on: "Opreste schimbul", off: "Porneste schimbul" }
};

const state = {
    dashboard: null,
    savedRecords: null,
    isSubmitting: false,
    selectedMachineKey: window.appConfig.defaultMachineKey || "laser1",
    currentView: window.localStorage.getItem("currentView") || "dashboard",
    savedPeriod: window.localStorage.getItem("savedPeriod") || "all",
    savedOperatorId: window.localStorage.getItem("savedOperatorId") || "",
    workcenterFeedback: null,
    lastStatsSnapshot: null,
    lastStatsSyncMs: 0,
    renderedFeedsSignature: "",
    liveExtractionLayoutKey: "",
    feedRefreshTimers: []
};

const savedPeriodReportLabelMap = {
    day: "Zilnic",
    week: "Saptamanal",
    month: "Lunar"
};

function getSignalButtonLabel(signalName, signal) {
    if (signal?.active && signal?.button_on_label) {
        return signal.button_on_label;
    }
    if (!signal?.active && signal?.button_off_label) {
        return signal.button_off_label;
    }
    return signalLabels[signalName][signal.active ? "on" : "off"];
}

function formatSavedPeriodDate(value, options = { dateStyle: "medium" }) {
    return new Intl.DateTimeFormat("ro-RO", options).format(value);
}

function getStartOfCurrentWeek(now) {
    const weekStart = new Date(now);
    weekStart.setHours(0, 0, 0, 0);
    const dayOffset = (weekStart.getDay() + 6) % 7;
    weekStart.setDate(weekStart.getDate() - dayOffset);
    return weekStart;
}

function getSavedPeriodMeta(period) {
    const normalizedPeriod = ["all", "day", "week", "month"].includes(period) ? period : "all";
    const now = new Date();
    const todayLabel = formatSavedPeriodDate(now, { dateStyle: "full" });
    const weekStart = getStartOfCurrentWeek(now);
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);

    if (normalizedPeriod === "day") {
        return {
            key: "day",
            sectionTitle: "Operatori si foi finalizate azi",
            subtitle: "Fiecare foaie se salveaza la final de table change, iar randamentul zilei este media foilor finalizate azi.",
            hint: `Filtrul ia foile finalizate in ${todayLabel}. Machine OFF nu intra in calcule, iar la fiecare foaie noua se adauga media cu cele deja salvate.`,
            countLabel: "Foi azi",
            reportCardText: "Media randamentelor pe foile finalizate azi",
            machineReportTitle: "Detaliu operator azi",
            emptySummary: "Nu exista inca foi salvate pentru ziua curenta.",
            emptyReports: "Rezumatul operatorului apare aici dupa prima foaie finalizata.",
            emptyMachineReports: "Explicatia de calcul apare aici dupa primele foi salvate azi.",
            emptyRecords: "Nu exista inca foi salvate pentru ziua curenta."
        };
    }

    if (normalizedPeriod === "week") {
        return {
            key: "week",
            sectionTitle: "Operatori si foi finalizate saptamana aceasta",
            subtitle: "Media saptamanala se calculeaza din toate foile finalizate din saptamana curenta.",
            hint: `Filtrul ia intervalul ${formatSavedPeriodDate(weekStart)} - ${formatSavedPeriodDate(now)}. Fiecare foaie noua intra in media saptamanii doar dupa ce se termina table change.`,
            countLabel: "Foi saptamana",
            reportCardText: "Media randamentelor pe foile finalizate saptamana aceasta",
            machineReportTitle: "Detaliu operator saptamanal",
            emptySummary: "Nu exista inca foi salvate pentru saptamana curenta.",
            emptyReports: "Rezumatul operatorului apare aici dupa primele foi salvate din saptamana curenta.",
            emptyMachineReports: "Explicatia de calcul apare aici dupa primele foi salvate din saptamana curenta.",
            emptyRecords: "Nu exista inca foi salvate pentru saptamana curenta."
        };
    }

    if (normalizedPeriod === "month") {
        return {
            key: "month",
            sectionTitle: "Operatori si foi finalizate luna aceasta",
            subtitle: "Media lunii vine din toate foile finalizate in luna curenta, nu dintr-un singur total frumos.",
            hint: `Filtrul ia intervalul ${formatSavedPeriodDate(monthStart)} - ${formatSavedPeriodDate(now)}. Machine OFF ramane in afara calculelor, iar fiecare foaie adauga un nou randament in media lunii.`,
            countLabel: "Foi luna",
            reportCardText: "Media randamentelor pe foile finalizate luna aceasta",
            machineReportTitle: "Detaliu operator lunar",
            emptySummary: "Nu exista inca foi salvate pentru luna curenta.",
            emptyReports: "Rezumatul operatorului apare aici dupa primele foi salvate din luna curenta.",
            emptyMachineReports: "Explicatia de calcul apare aici dupa primele foi salvate din luna curenta.",
            emptyRecords: "Nu exista inca foi salvate pentru luna curenta."
        };
    }

    return {
        key: "all",
        sectionTitle: "Tot istoricul foilor salvate",
        subtitle: "Aici vezi operatorii si foile finalizate din tot istoricul disponibil in sursa de date.",
        hint: "Fiecare foaie este salvata la final de table change. Randamentul unei foi este Cutting sau Bending impartit la Machine ON pentru acea foaie.",
        countLabel: "Total foi",
        reportCardText: "Media randamentelor din toate foile salvate",
        machineReportTitle: "Detaliu operator",
        emptySummary: "Nu exista inca date salvate in istoric.",
        emptyReports: "Rezumatul operatorului se va afisa aici dupa primele foi salvate.",
        emptyMachineReports: "Explicatia de calcul se va afisa aici dupa primele foi salvate.",
        emptyRecords: "Cand se termina table change, foaia se salveaza automat aici."
    };
}

function filterSavedReportsByPeriod(reports, period) {
    if (period === "all") {
        return reports;
    }

    const targetLabel = savedPeriodReportLabelMap[period];
    return reports.filter((item) => item.label === targetLabel);
}

document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();

    const savedMachineKey = window.localStorage.getItem("selectedMachineKey");
    if (savedMachineKey) {
        state.selectedMachineKey = savedMachineKey;
    }

    bindActions();
    if (state.currentView === "saved") {
        loadSavedRecords();
    } else {
        loadDashboard(state.selectedMachineKey);
    }
    window.setInterval(() => {
        if (state.currentView === "saved") {
            loadSavedRecords();
            return;
        }
        loadDashboard(state.selectedMachineKey);
    }, 10000);
});

function initThemeToggle() {
    const toggle = document.getElementById("themeToggle");
    const body = document.body;
    const currentTheme = window.localStorage.getItem("theme");

    if (!toggle) {
        return;
    }

    if (currentTheme === "dark") {
        body.classList.add("dark-mode");
        body.classList.remove("light-mode");
        toggle.checked = false;
    } else {
        body.classList.add("light-mode");
        body.classList.remove("dark-mode");
        toggle.checked = true;
    }

    toggle.addEventListener("change", () => {
        if (toggle.checked) {
            body.classList.add("light-mode");
            body.classList.remove("dark-mode");
            window.localStorage.setItem("theme", "light");
        } else {
            body.classList.add("dark-mode");
            body.classList.remove("light-mode");
            window.localStorage.setItem("theme", "dark");
        }
    });
}

function bindActions() {
    const machineSelector = document.getElementById("machine-selector");
    if (machineSelector) {
        machineSelector.addEventListener("click", async (event) => {
            const button = event.target.closest("[data-machine-key], [data-view]");
            if (!button) {
                return;
            }

            const nextMachineKey = button.dataset.machineKey;
            const nextView = button.dataset.view || "dashboard";

            if (nextView === "saved") {
                if (state.currentView === "saved") {
                    await loadSavedRecords();
                    return;
                }

                state.currentView = "saved";
                window.localStorage.setItem("currentView", state.currentView);
                await loadSavedRecords();
                return;
            }

            if (!nextMachineKey) {
                return;
            }

            if (nextMachineKey === state.selectedMachineKey && state.currentView === "dashboard") {
                await loadDashboard(nextMachineKey);
                return;
            }

            state.currentView = "dashboard";
            window.localStorage.setItem("currentView", state.currentView);
            state.workcenterFeedback = null;
            await loadDashboard(nextMachineKey);
        });
    }

    const savedFilterRow = document.getElementById("saved-filter-row");
    if (savedFilterRow) {
        savedFilterRow.addEventListener("click", async (event) => {
            const button = event.target.closest("[data-saved-period]");
            if (!button) {
                return;
            }

            state.savedPeriod = button.dataset.savedPeriod || "all";
            window.localStorage.setItem("savedPeriod", state.savedPeriod);
            await loadSavedRecords();
        });
    }

    const savedSummary = document.getElementById("saved-summary");
    if (savedSummary) {
        savedSummary.addEventListener("click", async (event) => {
            const card = event.target.closest("[data-operator-id]");
            if (!card) {
                return;
            }

            state.savedOperatorId = card.dataset.operatorId || "";
            window.localStorage.setItem("savedOperatorId", state.savedOperatorId);
            await loadSavedRecords();
        });
    }

    document.querySelectorAll("[data-signal]").forEach((button) => {
        button.addEventListener("click", async () => {
            const signalName = button.dataset.signal;
            const currentValue = Boolean(state.dashboard?.current_signals?.[signalName]?.active);
            await sendEvent(signalName, !currentValue);
        });
    });

    const refreshButton = document.getElementById("refresh-operator");
    if (refreshButton) {
        refreshButton.addEventListener("click", () => loadDashboard(state.selectedMachineKey));
    }

    const deleteLatestButton = document.getElementById("delete-latest-tests");
    if (deleteLatestButton) {
        deleteLatestButton.addEventListener("click", () => deleteEvents("manual_latest", 10));
    }

    const deleteAllButton = document.getElementById("delete-all-tests");
    if (deleteAllButton) {
        deleteAllButton.addEventListener("click", () => deleteEvents("manual_all"));
    }

    const saveWorkcenterButton = document.getElementById("save-workcenter");
    if (saveWorkcenterButton) {
        saveWorkcenterButton.addEventListener("click", updateWorkcenter);
    }

    const workcenterInput = document.getElementById("workcenter-id-input");
    if (workcenterInput) {
        workcenterInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                updateWorkcenter();
            }
        });
    }
}

async function loadDashboard(machineKey = state.selectedMachineKey) {
    const targetMachineKey = machineKey || state.selectedMachineKey;

    try {
        const response = await fetch(
            `${window.appConfig.dashboardUrl}?machine=${encodeURIComponent(targetMachineKey)}`
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Nu am putut incarca dashboard-ul.");
        }

        state.dashboard = payload;
        state.selectedMachineKey = payload.selected_machine_key;
        window.localStorage.setItem("selectedMachineKey", payload.selected_machine_key);
        window.localStorage.setItem("currentView", "dashboard");
        state.currentView = "dashboard";
        renderDashboard(payload);
    } catch (error) {
        console.error(error);
        setWorkcenterFeedback(error.message, "error");
    }
}

async function loadSavedRecords() {
    try {
        const query = new URLSearchParams({ period: state.savedPeriod });
        if (state.savedOperatorId) {
            query.set("operator_id", state.savedOperatorId);
        }
        const response = await fetch(`${window.appConfig.savedRecordsUrl}?${query.toString()}`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Nu am putut incarca datele salvate.");
        }

        state.savedRecords = payload;
        state.savedPeriod = payload.period || state.savedPeriod;
        state.savedOperatorId = payload.selected_operator_id || state.savedOperatorId || "";
        state.currentView = "saved";
        window.localStorage.setItem("currentView", "saved");
        window.localStorage.setItem("savedPeriod", state.savedPeriod);
        window.localStorage.setItem("savedOperatorId", state.savedOperatorId);
        renderSavedView(payload);
    } catch (error) {
        console.error(error);
        window.alert(error.message);
    }
}

async function sendEvent(signalName, value) {
    if (state.isSubmitting) {
        return;
    }

    state.isSubmitting = true;
    syncBusyState();
    const noteInput = document.getElementById("event-note");

    try {
        const response = await fetch(window.appConfig.eventsUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                machine_key: state.selectedMachineKey,
                signal_name: signalName,
                value,
                note: noteInput.value.trim(),
                source: "manual-dashboard"
            })
        });

        const payload = await response.json();
        if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Nu am putut salva evenimentul.");
        }

        state.dashboard = payload.dashboard;
        noteInput.value = "";
        renderDashboard(payload.dashboard);
    } catch (error) {
        window.alert(error.message);
    } finally {
        state.isSubmitting = false;
        syncBusyState();
    }
}

async function deleteEvents(mode, limit = null) {
    if (state.isSubmitting) {
        return;
    }

    const machineLabel = state.dashboard?.machine?.label || state.selectedMachineKey;
    const confirmMessage = mode === "manual_all"
        ? `Stergi toate evenimentele manuale de test pentru ${machineLabel}?`
        : `Stergi ultimele ${limit} evenimente manuale de test pentru ${machineLabel}?`;

    if (!window.confirm(confirmMessage)) {
        return;
    }

    state.isSubmitting = true;
    syncBusyState();

    try {
        const response = await fetch(window.appConfig.eventsUrl, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                machine_key: state.selectedMachineKey,
                mode,
                limit
            })
        });

        const payload = await response.json();
        if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Nu am putut sterge evenimentele.");
        }

        state.dashboard = payload.dashboard;
        renderDashboard(payload.dashboard);
    } catch (error) {
        window.alert(error.message);
    } finally {
        state.isSubmitting = false;
        syncBusyState();
    }
}

async function updateWorkcenter() {
    if (state.isSubmitting) {
        return;
    }

    const input = document.getElementById("workcenter-id-input");
    const rawValue = input.value.trim();

    state.isSubmitting = true;
    syncBusyState();

    try {
        const response = await fetch(
            `${window.appConfig.machinesUrl}/${encodeURIComponent(state.selectedMachineKey)}`,
            {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    workcenter_id: rawValue
                })
            }
        );

        const payload = await response.json();
        if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Nu am putut actualiza workcenterul.");
        }

        state.workcenterFeedback = {
            machineKey: state.selectedMachineKey,
            tone: "success",
            message: `WorkCenter salvat pentru ${payload.machine.label}.`
        };
        state.dashboard = payload.dashboard;
        renderDashboard(payload.dashboard);
    } catch (error) {
        setWorkcenterFeedback(error.message, "error");
    } finally {
        state.isSubmitting = false;
        syncBusyState();
    }
}

function renderDashboard(payload) {
    syncSectionVisibility("dashboard");
    renderHeader(payload);
    renderMachineSelector(payload.machines);
    renderMachineState(payload.machine, payload.current_state);
    renderSignals(payload.current_signals);
    renderButtons(payload.current_signals);
    renderWorkcenter(payload.machine);
    renderOperator(payload.operator_snapshot);
    renderSource(payload.real_data_source);
    renderLiveExtraction(payload.live_extraction);
    renderMachineFeeds(payload.machine_feeds || []);
    renderStats(payload.stats_today);
    renderTimeline(payload.recent_events);
}

function renderSavedView(payload) {
    const currentPeriod = payload.period || state.savedPeriod;
    const periodMeta = getSavedPeriodMeta(currentPeriod);
    syncSectionVisibility("saved");
    renderSavedHeader(payload, periodMeta);
    renderMachineSelector(state.dashboard?.machines || window.appConfig.initialMachines || []);
    renderSavedSummary(payload, periodMeta);
    renderSavedFilters(currentPeriod);
    renderSavedReports(payload, currentPeriod, periodMeta);
    renderSavedMachineReports(payload, currentPeriod, periodMeta);
    renderSavedRecords(payload.records || [], periodMeta);
}

function renderHeader(payload) {
    document.getElementById("dashboard-title").textContent = `${payload.dashboard_title} / ${payload.machine.label}`;
    document.getElementById("dashboard-subtitle").textContent = payload.machine.description;
}

function renderSavedHeader(payload, periodMeta) {
    document.getElementById("dashboard-title").textContent = "Date salvate / operatori";
    document.getElementById("dashboard-subtitle").textContent = periodMeta.subtitle;
    document.getElementById("saved-section-title").textContent = periodMeta.sectionTitle;
    document.getElementById("saved-period-hint").textContent = periodMeta.hint;
    document.getElementById("saved-records-label").textContent = periodMeta.countLabel;
}

function renderMachineSelector(machines) {
    const selector = document.getElementById("machine-selector");
    const machineButtons = machines
        .map((machine) => `
            <button
                class="machine-tab ${machine.is_selected ? "is-selected" : ""}"
                data-machine-key="${machine.key}"
                data-view="dashboard"
                type="button"
            >
                <small>${machine.label}</small>
                <strong>${machine.workcenter_id ? `WC ${machine.workcenter_id}` : "Fara WC"}</strong>
                <span>${machine.description}</span>
            </button>
        `)
        .join("");

    const savedButton = `
        <button
            class="machine-tab saved-tab ${state.currentView === "saved" ? "is-selected" : ""}"
            data-view="saved"
            type="button"
        >
            <small>Arhiva</small>
            <strong>DATE SALVATE</strong>
            <span>Operatori, programe si cicluri salvate automat azi.</span>
        </button>
    `;

    selector.innerHTML = `${machineButtons}${savedButton}`;
}

function renderMachineState(machine, machineState) {
    document.getElementById("machine-context-title").textContent = machine.label;
    document.getElementById("machine-state-label").textContent = machineState.label;
    document.getElementById("machine-state-description").textContent = machineState.description;

    const badge = document.getElementById("machine-state-badge");
    badge.textContent = (machineState.badge_label || machineState.label || machineState.key).toUpperCase();
    badge.className = `state-badge tone-${machineState.tone}`;
}

function renderSignals(signals) {
    const signalGrid = document.getElementById("signal-grid");
    signalGrid.innerHTML = "";

    Object.entries(signals).forEach(([signalName, signal]) => {
        const tile = document.createElement("article");
        tile.className = `signal-tile accent-${signal.accent}`;
        tile.innerHTML = `
            <div class="signal-heading">
                <strong>${signal.label}</strong>
                <span>${signal.active ? "Activ" : "Inactiv"}</span>
            </div>
            <p>${signal.description}</p>
            <small>${signal.changed_at ? formatDateTime(signal.changed_at) : "Fara evenimente"}</small>
        `;
        signalGrid.appendChild(tile);
    });
}

function renderButtons(signals) {
    Object.entries(signals).forEach(([signalName, signal]) => {
        const button = document.getElementById(`button-${signalName}`);
        if (!button) {
            return;
        }

        button.classList.toggle("is-active", signal.active);
        button.textContent = getSignalButtonLabel(signalName, signal);
    });
}

function renderWorkcenter(machine) {
    const input = document.getElementById("workcenter-id-input");
    if (document.activeElement !== input) {
        input.value = machine.workcenter_id ?? "";
    }

    const feedback = state.workcenterFeedback?.machineKey === machine.key
        ? state.workcenterFeedback
        : {
            tone: "muted",
            message: "Cand schimbi ID-ul, operatorul activ se reincarca imediat pentru utilajul selectat."
        };

    setFeedbackText(feedback.message, feedback.tone);
}

function renderOperator(operatorSnapshot) {
    const dot = document.getElementById("operator-dot");
    const text = document.getElementById("operator-status-text");
    const primaryContainer = document.getElementById("operator-primary");
    const listContainer = document.getElementById("operator-list");

    dot.className = "dot";
    listContainer.innerHTML = "";

    if (operatorSnapshot.status === "connected") {
        dot.classList.add("connected");
    } else if (operatorSnapshot.status === "error") {
        dot.classList.add("error");
    } else {
        dot.classList.add("pending");
    }

    text.textContent = operatorSnapshot.workcenter_id
        ? `${operatorSnapshot.message} WorkCenter ID ${operatorSnapshot.workcenter_id}.`
        : operatorSnapshot.message;

    if (operatorSnapshot.primary_operator) {
        const operator = operatorSnapshot.primary_operator;
        primaryContainer.innerHTML = `
            <div class="operator-primary-item">
                <small>Operator activ</small>
                <strong>${operator.full_name}</strong>
                <p>ID angajat: ${operator.employee_id}</p>
                <small>${operator.check_in ? `Check-in: ${operator.check_in}` : "Check-in necunoscut"}</small>
            </div>
        `;
    } else {
        primaryContainer.innerHTML = `
            <p class="empty-state">Nu exista operator activ pentru workcenterul configurat.</p>
        `;
    }

    operatorSnapshot.operators.slice(1).forEach((operator) => {
        const pill = document.createElement("div");
        pill.className = "operator-pill";
        pill.innerHTML = `
            <p>${operator.full_name}</p>
            <small>ID ${operator.employee_id}</small>
        `;
        listContainer.appendChild(pill);
    });
}

function renderStats(stats) {
    document.getElementById("metric-label-machine-on").textContent = "Machine ON";
    document.getElementById("metric-label-cutting").textContent = stats.cutting_metric_label || "Cutting";
    document.getElementById("metric-label-table-change").textContent = stats.table_change_metric_label || "Table change";
    document.getElementById("metric-label-idle").textContent = "Idle";
    document.getElementById("metric-randament").textContent = `${stats.randament_percent}%`;
    document.getElementById("metric-availability").textContent =
        stats.availability_label || `Disponibilitate taiere/masina_pornita ${stats.availability_percent}%`;
    document.getElementById("metric-window").textContent = stats.production_window_label;
    document.getElementById("metric-machine-on").textContent = stats.machine_on_label;
    document.getElementById("metric-cutting").textContent = stats.cutting_label;
    document.getElementById("metric-table-change").textContent = stats.table_change_label;
    document.getElementById("metric-idle").textContent = stats.idle_label;
    document.getElementById("utilization-fill").style.width = `${Math.min(stats.randament_percent, 100)}%`;
}

function renderSource(realDataSource) {
    const dot = document.getElementById("source-dot");
    const text = document.getElementById("source-status-text");
    const panel = document.getElementById("source-panel");

    dot.className = "dot";
    if (realDataSource.status === "configured") {
        dot.classList.add("connected");
    } else {
        dot.classList.add("pending");
    }

    text.textContent = realDataSource.message;
    const details = Array.isArray(realDataSource.details)
        ? realDataSource.details.map((detail) => `<li>${detail}</li>`).join("")
        : "";
    panel.innerHTML = `
        <div class="source-panel-item">
            <small>Sursa reala</small>
            <strong>${realDataSource.name}</strong>
            <p>${realDataSource.status === "configured" ? "Identificata in proiect" : "In asteptare"}</p>
            <small>${realDataSource.transport || "Transport necunoscut"}</small>
            <small>${realDataSource.endpoint || "Nu exista inca endpoint clar pentru acest utilaj."}</small>
            ${details ? `<ul class="source-detail-list">${details}</ul>` : ""}
        </div>
    `;
}

function renderLiveExtraction(snapshot) {
    const container = document.getElementById("live-extraction");
    if (!container) {
        return;
    }

    const currentMachineKey = state.dashboard?.machine?.key || state.selectedMachineKey;

    if (!snapshot || !snapshot.available) {
        state.liveExtractionLayoutKey = `${currentMachineKey}:empty`;
        container.innerHTML = `<p class="empty-state">${snapshot?.message || "Nu exista inca date extrase live din ecranul utilajului."}</p>`;
        return;
    }

    const signals = snapshot.derived_signals || {};
    const cells = currentMachineKey === "abkant"
        ? [
            { slot: "program", label: "Program curent", value: snapshot.active_program || "Necitit" },
            { slot: "total", label: "Piese de indoit", value: snapshot.total_pieces ?? "Necunoscut" },
            { slot: "produced", label: "Piese indoite", value: snapshot.produced_pieces ?? 0 },
            { slot: "progress", label: "Progres", value: snapshot.pieces_label || "n/a" },
            { slot: "machine_on", label: "Machine ON", value: signals.machine_on ? "DA" : "NU" },
            { slot: "bending", label: "Bending", value: signals.cutting_active ? "DA" : "NU" },
            { slot: "bend_change", label: "Bend change", value: signals.table_change ? "DA" : "NU" },
            { slot: "status", label: "Status program", value: snapshot.program_status || "Necitit" }
        ]
        : [
            { slot: "selected_program", label: "Selected program", value: snapshot.selected_program || "Necitit" },
            { slot: "active_program", label: "Active program", value: snapshot.active_program || "Necitit" },
            { slot: "material", label: "Material", value: snapshot.material || "Necitit" },
            { slot: "program_status", label: "Program status", value: snapshot.program_status || "Necitit" },
            { slot: "machine_on", label: "Machine ON", value: signals.machine_on ? "DA" : "NU" },
            { slot: "cutting", label: "Cutting", value: signals.cutting_active ? "DA" : "NU" },
            { slot: "table_change", label: "Table change", value: signals.table_change ? "DA" : "NU" },
            { slot: "idle", label: "Idle", value: signals.idle ? "DA" : "NU" }
        ];

    const layoutKey = `${currentMachineKey}:live`;
    if (state.liveExtractionLayoutKey !== layoutKey) {
        const rows = [];
        for (let index = 0; index < cells.length; index += 2) {
            rows.push(cells.slice(index, index + 2));
        }

        container.innerHTML = `
            <div class="live-screen live-screen-static">
                ${rows.map((row) => `
                    <div class="live-screen-row">
                        ${row.map((cell) => `
                            <div class="live-cell">
                                <span>${cell.label}</span>
                                <strong data-live-slot="${cell.slot}">--</strong>
                            </div>
                        `).join("")}
                    </div>
                `).join("")}
            </div>
            <p class="feedback-text live-extraction-feedback"></p>
        `;
        state.liveExtractionLayoutKey = layoutKey;
    }

    cells.forEach((cell) => {
        const valueNode = container.querySelector(`[data-live-slot="${cell.slot}"]`);
        if (valueNode) {
            valueNode.textContent = String(cell.value);
        }
    });

    const feedbackNode = container.querySelector(".live-extraction-feedback");
    if (feedbackNode) {
        feedbackNode.textContent = snapshot.message || "";
    }
}

function renderMachineFeeds(feeds) {
    const container = document.getElementById("machine-feeds");
    if (!container) {
        return;
    }

    const renderedFeeds = feeds
        .filter((feed) => feed.url)
        .map((feed) => ({
            key: feed.key,
            mode: feed.mode,
            url: feed.url,
            open_url: feed.open_url || feed.url,
            display_url: feed.display_url || feed.url,
            refresh_ms: feed.refresh_ms || null
        }));
    const signature = JSON.stringify(renderedFeeds);

    if (!renderedFeeds.length) {
        state.renderedFeedsSignature = signature;
        state.feedRefreshTimers.forEach((timerId) => window.clearInterval(timerId));
        state.feedRefreshTimers = [];
        container.classList.remove("is-single-feed");
        container.innerHTML = `<p class="empty-state">Nu exista inca feeduri configurate pentru utilajul selectat.</p>`;
        return;
    }

    if (state.renderedFeedsSignature === signature) {
        return;
    }

    state.renderedFeedsSignature = signature;
    state.feedRefreshTimers.forEach((timerId) => window.clearInterval(timerId));
    state.feedRefreshTimers = [];
    container.classList.toggle("is-single-feed", renderedFeeds.length === 1);
    container.innerHTML = renderedFeeds
        .map((feed) => {
            const isFitPage = feed.mode === "page" && feed.key === "hmi";
            const initialImageSrc = feed.refresh_ms
                ? `${feed.url}${feed.url.includes("?") ? "&" : "?"}ts=${Date.now()}`
                : feed.url;
            const body = feed.mode === "page"
                ? (
                    isFitPage
                        ? `<div class="feed-fit-shell"><iframe class="feed-frame feed-frame-fit" src="${feed.url}" loading="eager" referrerpolicy="no-referrer" scrolling="no"></iframe></div>`
                        : `<iframe class="feed-frame" src="${feed.url}" loading="eager" referrerpolicy="no-referrer"></iframe>`
                )
                : `<img class="feed-image" src="${initialImageSrc}" alt="${feed.display_url}" loading="eager" ${feed.refresh_ms ? `data-base-src="${feed.url}" data-refresh-ms="${feed.refresh_ms}"` : ""}>`;

            return `
                <article class="feed-card">
                    <div class="feed-card-head">
                        <a class="feed-link" href="${feed.open_url}" target="_blank" rel="noopener noreferrer">${feed.display_url}</a>
                    </div>
                    <div class="feed-viewport ${isFitPage ? "is-fit-page" : ""}">
                        ${body}
                    </div>
                </article>
            `;
        })
        .join("");

    container.querySelectorAll(".feed-image[data-refresh-ms][data-base-src]").forEach((image) => {
        const baseSrc = image.getAttribute("data-base-src");
        const refreshMs = Number(image.getAttribute("data-refresh-ms") || 0);
        if (!baseSrc || refreshMs < 250) {
            return;
        }

        const timerId = window.setInterval(() => {
            image.src = `${baseSrc}${baseSrc.includes("?") ? "&" : "?"}ts=${Date.now()}`;
        }, refreshMs);
        state.feedRefreshTimers.push(timerId);
    });
}

function renderTimeline(events) {
    const timeline = document.getElementById("timeline");
    timeline.innerHTML = "";

    if (!events.length) {
        timeline.innerHTML = `<p class="empty-state">Nu exista inca evenimente pentru utilajul selectat.</p>`;
        return;
    }

    events.forEach((eventItem) => {
        const item = document.createElement("article");
        item.className = "timeline-item";
        item.innerHTML = `
            <div class="timeline-meta">
                <small>${formatDateTime(eventItem.created_at)}</small>
                <small>${eventItem.source}</small>
            </div>
            <strong>${eventItem.signal_label}: ${eventItem.value ? "ON" : "OFF"}</strong>
            <p>${eventItem.operator_name || "Fara operator activ"}</p>
            <small>${eventItem.note || "Fara observatii"}${eventItem.is_manual ? " | test manual" : ""}</small>
        `;
        timeline.appendChild(item);
    });
}

function getSavedOperatorPeriod(operatorEntry, periodKey) {
    return operatorEntry?.[periodKey] || {
        records_count: 0,
        efficiency_percent: 0,
        machine_on_label: "00:00:00",
        cutting_label: "00:00:00",
        idle_label: "00:00:00",
        table_change_label: "00:00:00"
    };
}

function getSelectedSavedOperator(payload) {
    const operators = payload?.operators || [];
    if (!operators.length) {
        return null;
    }

    return operators.find((operatorEntry) => operatorEntry.operator_id === payload.selected_operator_id) || operators[0];
}

function renderSavedSummary(payload, periodMeta) {
    const container = document.getElementById("saved-summary");
    const count = document.getElementById("saved-records-count");
    const recordsCount = Number(state.savedRecords?.records_count || 0);
    count.textContent = String(recordsCount);

    if (payload.data_source === "prometheus") {
        const operators = payload.operators || [];
        if (!operators.length) {
            container.innerHTML = `<p class="empty-state">${periodMeta.emptySummary}</p>`;
            return;
        }

        container.innerHTML = operators
            .map((item) => {
                const day = getSavedOperatorPeriod(item, "day");
                const week = getSavedOperatorPeriod(item, "week");
                const month = getSavedOperatorPeriod(item, "month");
                const selectedClass = item.operator_id === payload.selected_operator_id ? "is-selected" : "";
                return `
                    <article
                        class="saved-summary-card saved-operator-card ${selectedClass}"
                        data-operator-id="${item.operator_id}"
                        role="button"
                        tabindex="0"
                    >
                        <small>Operator</small>
                        <strong>${item.operator_name}</strong>
                        <small>${item.employee_id ? `ID angajat ${item.employee_id}` : "ID angajat indisponibil"}</small>
                        <p>${day.records_count} foi azi, ${week.records_count} saptamana, ${month.records_count} luna</p>
                        <div class="saved-operator-periods">
                            <div class="saved-operator-period">
                                <span>Zi</span>
                                <strong>${roundToOneDecimal(day.efficiency_percent)}%</strong>
                                <small>${day.records_count} foi</small>
                            </div>
                            <div class="saved-operator-period">
                                <span>Sapt.</span>
                                <strong>${roundToOneDecimal(week.efficiency_percent)}%</strong>
                                <small>${week.records_count} foi</small>
                            </div>
                            <div class="saved-operator-period">
                                <span>Luna</span>
                                <strong>${roundToOneDecimal(month.efficiency_percent)}%</strong>
                                <small>${month.records_count} foi</small>
                            </div>
                        </div>
                    </article>
                `;
            })
            .join("");
        return;
    }

    const summary = payload.summary || [];
    if (!summary.length) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptySummary}</p>`;
        return;
    }

    container.innerHTML = summary
        .map((item) => `
            <article class="saved-summary-card">
                <small>Operator</small>
                <strong>${item.operator_name}</strong>
                <p>${item.records_count} cicluri salvate</p>
                <small>${item.total_cycle_label} timp cumulat</small>
                <small>${item.machines.join(", ")}</small>
            </article>
        `)
        .join("");
}

function renderSavedFilters(period) {
    document.querySelectorAll("[data-saved-period]").forEach((button) => {
        button.classList.toggle("is-selected", button.dataset.savedPeriod === period);
    });
}

function renderSavedReports(payload, period, periodMeta) {
    const container = document.getElementById("saved-reports");
    if (payload.data_source === "prometheus") {
        const selectedOperator = getSelectedSavedOperator(payload);
        if (!selectedOperator) {
            container.innerHTML = `<p class="empty-state">${periodMeta.emptyReports}</p>`;
            return;
        }

        const periodStats = getSavedOperatorPeriod(selectedOperator, period);
        const activityLabel = payload.records?.[0]?.activity_label || "Cutting";
        const changeLabel = payload.records?.[0]?.change_label || "Table change";
        container.innerHTML = `
            <article class="saved-report-card">
                <small>Operator selectat</small>
                <strong>${selectedOperator.operator_name}</strong>
                <small>${selectedOperator.employee_id ? `ID angajat ${selectedOperator.employee_id}` : "ID angajat indisponibil"}</small>
                <p>${periodMeta.reportCardText}</p>
                <div class="saved-report-metrics">
                    <span>${periodStats.records_count} foi finalizate</span>
                    <span>Media randamentelor: ${roundToOneDecimal(periodStats.efficiency_percent)}%</span>
                    <span>Machine ON ${periodStats.machine_on_label}</span>
                    <span>${activityLabel} ${periodStats.cutting_label}</span>
                    <span>Idle ${periodStats.idle_label}</span>
                    <span>${changeLabel} ${periodStats.table_change_label}</span>
                </div>
            </article>
            <article class="saved-report-card">
                <small>Sursa</small>
                <strong>${payload.data_source === "prometheus" ? "Prometheus" : "SQLite"}</strong>
                <p>Datele salvate se citesc din seria istorica de foi finalizate, nu dintr-un calcul live din browser.</p>
                <div class="saved-report-metrics">
                    <span>Fiecare foaie se inchide dupa terminarea table change.</span>
                    <span>Machine OFF nu intra in randamentul foii.</span>
                    <span>Zi, saptamana si luna folosesc media foilor finalizate in perioada respectiva.</span>
                </div>
            </article>
        `;
        return;
    }

    const reports = payload.reports || [];
    const visibleReports = filterSavedReportsByPeriod(reports, period);

    if (!visibleReports.length) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptyReports}</p>`;
        return;
    }

    container.innerHTML = visibleReports
        .map((item) => `
            <article class="saved-report-card">
                <small>${item.label}</small>
                <strong>${item.efficiency_percent}%</strong>
                <p>${periodMeta.reportCardText}</p>
                <div class="saved-report-metrics">
                    <span>${item.records_count} cicluri</span>
                    <span>${item.cutting_display_label || "Taiere"} ${item.cutting_label}</span>
                    <span>${item.table_change_display_label || "Schimb masa"} ${item.table_change_label}</span>
                </div>
            </article>
        `)
        .join("");
}

function renderSavedMachineReports(payload, period, periodMeta) {
    const container = document.getElementById("saved-machine-reports");
    if (payload.data_source === "prometheus") {
        const records = payload.records || [];
        if (!records.length) {
            container.innerHTML = `<p class="empty-state">${periodMeta.emptyMachineReports}</p>`;
            return;
        }

        const activityLabel = records[0]?.activity_label || "Cutting";
        const changeLabel = records[0]?.change_label || "Table change";
        const machineMap = new Map();
        records.forEach((record) => {
            const existing = machineMap.get(record.machine_key) || {
                machine_label: record.machine_label,
                records_count: 0,
                machine_on_seconds: 0,
                cutting_seconds: 0,
                idle_seconds: 0,
                table_change_seconds: 0
            };
            existing.records_count += 1;
            existing.machine_on_seconds += Number(record.machine_on_duration_seconds || 0);
            existing.cutting_seconds += Number(record.cycle_duration_seconds || 0);
            existing.idle_seconds += Number(record.idle_duration_seconds || 0);
            existing.table_change_seconds += Number(record.table_change_duration_seconds || 0);
            machineMap.set(record.machine_key, existing);
        });

        container.innerHTML = Array.from(machineMap.values())
            .map((item) => `
                <article class="saved-machine-report-card">
                    <small>${item.machine_label}</small>
                    <strong>${periodMeta.machineReportTitle}</strong>
                    <div class="saved-machine-period-list">
                        <div class="saved-machine-period-item">
                            <span>Foi finalizate</span>
                            <strong>${item.records_count}</strong>
                            <small>Machine ON ${formatSeconds(item.machine_on_seconds)}</small>
                            <small>${activityLabel} ${formatSeconds(item.cutting_seconds)}</small>
                            <small>Idle ${formatSeconds(item.idle_seconds)}</small>
                            <small>${changeLabel} ${formatSeconds(item.table_change_seconds)}</small>
                        </div>
                    </div>
                </article>
            `)
            .join("");
        return;
    }

    const reportsByMachine = payload.reports_by_machine || [];
    const visibleMachineReports = reportsByMachine
        .map((machineReport) => ({
            ...machineReport,
            periods: filterSavedReportsByPeriod(machineReport.periods || [], period)
        }))
        .filter((machineReport) => machineReport.periods.length);

    if (!visibleMachineReports.length) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptyMachineReports}</p>`;
        return;
    }

    container.innerHTML = visibleMachineReports
        .map((machineReport) => `
            <article class="saved-machine-report-card">
                <small>${machineReport.machine_label}</small>
                <strong>${periodMeta.machineReportTitle}</strong>
                <div class="saved-machine-period-list">
                    ${machineReport.periods.map((period) => `
                        <div class="saved-machine-period-item">
                            <span>${period.label}</span>
                            <strong>${period.efficiency_percent}%</strong>
                            <small>${period.records_count} cicluri</small>
                            <small>${period.cutting_display_label || "Taiere"} ${period.cutting_label}</small>
                            <small>${period.table_change_display_label || "Schimb masa"} ${period.table_change_label}</small>
                        </div>
                    `).join("")}
                </div>
            </article>
        `)
        .join("");
}

function renderSavedRecords(records, periodMeta) {
    const container = document.getElementById("saved-record-list");
    if (!records.length) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptyRecords}</p>`;
        return;
    }

    container.innerHTML = records
        .map((record) => `
            <article class="saved-record-card">
                <div class="saved-record-top">
                    <div>
                        <small>${record.machine_label}</small>
                        <strong>${record.selected_program}</strong>
                        <small>${record.table_change_ended_at ? `Finalizat: ${formatDateTime(record.table_change_ended_at)}` : ""}</small>
                    </div>
                    <div class="saved-record-meta">
                        <small>${record.operator_name}</small>
                        <strong>${roundToOneDecimal(record.efficiency_percent || 0)}%</strong>
                        <small>Randament foaie</small>
                    </div>
                </div>
                <div class="saved-record-grid">
                    <div>
                        <span>Program activ</span>
                        <strong>${record.active_program}</strong>
                    </div>
                    <div>
                        <span>Material</span>
                        <strong>${record.material}</strong>
                    </div>
                    <div>
                        <span>Inceput ${record.activity_label?.toLowerCase() || "taiere"}</span>
                        <strong>${record.cutting_started_at ? formatDateTime(record.cutting_started_at) : "Necunoscut"}</strong>
                    </div>
                    <div>
                        <span>Machine ON</span>
                        <strong>${record.machine_on_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>${record.activity_label || "Cutting"}</span>
                        <strong>${record.cycle_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>Idle</span>
                        <strong>${record.idle_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>${record.change_label || "Schimb masa"}</span>
                        <strong>${formatDateTime(record.table_change_started_at)}</strong>
                    </div>
                    <div>
                        <span>Final ${record.change_label?.toLowerCase() || "schimb masa"}</span>
                        <strong>${record.table_change_ended_at ? formatDateTime(record.table_change_ended_at) : "In lucru"}</strong>
                    </div>
                    <div>
                        <span>Durata ${record.change_label?.toLowerCase() || "schimb masa"}</span>
                        <strong>${record.table_change_duration_label || "00:00:00"}</strong>
                    </div>
                </div>
                <p class="saved-record-note">
                    Status la salvare: ${record.program_status}. Operator: ${record.operator_name}. Foaia a fost inchisa la final de ${record.change_label?.toLowerCase() || "table change"}.
                </p>
            </article>
        `)
        .join("");
}

function syncSectionVisibility(view) {
    document.getElementById("dashboard-overview-section").classList.toggle("is-hidden", view !== "dashboard");
    document.getElementById("dashboard-integrated-section").classList.toggle("is-hidden", view !== "dashboard");
    document.getElementById("saved-section").classList.toggle("is-hidden", view !== "saved");
}

function setWorkcenterFeedback(message, tone = "muted") {
    state.workcenterFeedback = {
        machineKey: state.selectedMachineKey,
        tone,
        message
    };
    setFeedbackText(message, tone);
}

function setFeedbackText(message, tone = "muted") {
    const feedback = document.getElementById("workcenter-feedback");
    feedback.textContent = message;
    feedback.className = `feedback-text ${tone ? `is-${tone}` : ""}`.trim();
}

function syncBusyState() {
    document.querySelectorAll("button").forEach((button) => {
        button.disabled = state.isSubmitting;
    });
}

function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return new Intl.DateTimeFormat("ro-RO", {
        dateStyle: "short",
        timeStyle: "medium"
    }).format(date);
}

function formatSeconds(totalSeconds) {
    const safeTotal = Math.max(Number(totalSeconds || 0), 0);
    const hours = Math.floor(safeTotal / 3600);
    const minutes = Math.floor((safeTotal % 3600) / 60);
    const seconds = safeTotal % 60;
    return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function parseDurationLabel(label) {
    const match = String(label || "").match(/^(\d{1,}):(\d{2}):(\d{2})$/);
    if (!match) {
        return 0;
    }

    const [, hours, minutes, seconds] = match;
    return Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds);
}

function roundToOneDecimal(value) {
    return Math.round(Number(value || 0) * 10) / 10;
}
