const signalLabels = {
    machine_on: { on: "Opreste masina", off: "Porneste masina" },
    cutting_active: { on: "Opreste productia", off: "Porneste productia" },
    table_change: { on: "Opreste schimbul", off: "Porneste schimbul" }
};

const state = {
    dashboard: null,
    isSubmitting: false,
    selectedMachineKey: window.appConfig.defaultMachineKey || "laser1",
    workcenterFeedback: null
};

document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();

    const savedMachineKey = window.localStorage.getItem("selectedMachineKey");
    if (savedMachineKey) {
        state.selectedMachineKey = savedMachineKey;
    }

    bindActions();
    loadDashboard(state.selectedMachineKey);
    window.setInterval(() => loadDashboard(state.selectedMachineKey), 15000);
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
            const button = event.target.closest("[data-machine-key]");
            if (!button) {
                return;
            }

            const nextMachineKey = button.dataset.machineKey;
            if (!nextMachineKey || nextMachineKey === state.selectedMachineKey) {
                return;
            }

            state.workcenterFeedback = null;
            await loadDashboard(nextMachineKey);
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
        renderDashboard(payload);
    } catch (error) {
        console.error(error);
        setWorkcenterFeedback(error.message, "error");
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
    renderHeader(payload);
    renderMachineSelector(payload.machines);
    renderMachineState(payload.machine, payload.current_state);
    renderSignals(payload.current_signals);
    renderButtons(payload.current_signals);
    renderWorkcenter(payload.machine);
    renderOperator(payload.operator_snapshot);
    renderSource(payload.real_data_source);
    renderStats(payload.stats_today);
    renderTimeline(payload.recent_events);
}

function renderHeader(payload) {
    document.getElementById("dashboard-title").textContent = `${payload.dashboard_title} / ${payload.machine.label}`;
    document.getElementById("dashboard-subtitle").textContent = payload.machine.description;
    document.getElementById("active-machine-label").textContent = payload.machine.label;
    document.getElementById("active-workcenter-label").textContent = payload.machine.workcenter_id
        ? `WC ${payload.machine.workcenter_id}`
        : "WC neconfigurat";
    document.getElementById("updated-at").textContent = payload.updated_at
        ? formatDateTime(payload.updated_at)
        : "necunoscut";
}

function renderMachineSelector(machines) {
    const selector = document.getElementById("machine-selector");
    selector.innerHTML = machines
        .map((machine) => `
            <button
                class="machine-tab ${machine.is_selected ? "is-selected" : ""}"
                data-machine-key="${machine.key}"
                type="button"
            >
                <small>${machine.label}</small>
                <strong>${machine.workcenter_id ? `WC ${machine.workcenter_id}` : "Fara WC"}</strong>
                <span>${machine.description}</span>
            </button>
        `)
        .join("");
}

function renderMachineState(machine, machineState) {
    document.getElementById("machine-context-title").textContent = machine.label;
    document.getElementById("machine-state-label").textContent = machineState.label;
    document.getElementById("machine-state-description").textContent = machineState.description;

    const badge = document.getElementById("machine-state-badge");
    badge.textContent = machineState.key.replace("_", " ").toUpperCase();
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
        button.textContent = signalLabels[signalName][signal.active ? "on" : "off"];
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
    document.getElementById("metric-randament").textContent = `${stats.randament_percent}%`;
    document.getElementById("metric-availability").textContent = `Disponibilitate ${stats.availability_percent}%`;
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
