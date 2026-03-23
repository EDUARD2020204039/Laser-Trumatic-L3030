const signalLabels = {
    machine_on: { on: "Opreste masina", off: "Porneste masina" },
    cutting_active: { on: "Opreste taierea", off: "Porneste taierea" },
    table_change: { on: "Opreste schimbul", off: "Porneste schimbul" }
};

const state = {
    dashboard: null,
    isSubmitting: false
};

document.addEventListener("DOMContentLoaded", () => {
    bindActions();
    loadDashboard();
    window.setInterval(loadDashboard, 15000);
});

function bindActions() {
    document.querySelectorAll("[data-signal]").forEach((button) => {
        button.addEventListener("click", async () => {
            const signalName = button.dataset.signal;
            const currentValue = Boolean(state.dashboard?.current_signals?.[signalName]?.active);
            await sendEvent(signalName, !currentValue);
        });
    });

    document.getElementById("refresh-operator").addEventListener("click", loadDashboard);
    document.getElementById("delete-latest-tests").addEventListener("click", () => deleteEvents("manual_latest", 10));
    document.getElementById("delete-all-tests").addEventListener("click", () => deleteEvents("manual_all"));
}

async function loadDashboard() {
    try {
        const response = await fetch(window.appConfig.dashboardUrl);
        const payload = await response.json();
        state.dashboard = payload;
        renderDashboard(payload);
    } catch (error) {
        console.error(error);
    }
}

async function sendEvent(signalName, value) {
    if (state.isSubmitting) {
        return;
    }

    state.isSubmitting = true;
    const noteInput = document.getElementById("event-note");

    try {
        const response = await fetch(window.appConfig.eventsUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
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
    }
}

async function deleteEvents(mode, limit = null) {
    if (state.isSubmitting) {
        return;
    }

    const confirmMessage = mode === "manual_all"
        ? "Stergi toate evenimentele manuale de test?"
        : `Stergi ultimele ${limit} evenimente manuale de test?`;

    if (!window.confirm(confirmMessage)) {
        return;
    }

    state.isSubmitting = true;

    try {
        const response = await fetch(window.appConfig.eventsUrl, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode, limit })
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
    }
}

function renderDashboard(payload) {
    renderHeader(payload);
    renderMachineState(payload.current_state);
    renderSignals(payload.current_signals);
    renderButtons(payload.current_signals);
    renderOperator(payload.operator_snapshot);
    renderSource(payload.real_data_source);
    renderStats(payload.stats_today);
    renderTimeline(payload.recent_events);
}

function renderHeader(payload) {
    const updatedAt = payload.updated_at ? formatDateTime(payload.updated_at) : "necunoscut";
    document.getElementById("updated-at").textContent = `Ultima actualizare: ${updatedAt}`;
}

function renderMachineState(machineState) {
    document.getElementById("machine-state-label").textContent = machineState.label;
    document.getElementById("machine-state-badge").textContent = machineState.key;
    document.getElementById("machine-state-badge").className = `state-badge tone-${machineState.tone}`;
    document.getElementById("machine-state-description").textContent = machineState.description;
}

function renderSignals(signals) {
    const signalGrid = document.getElementById("signal-grid");
    signalGrid.innerHTML = "";

    Object.entries(signals).forEach(([signalName, signal]) => {
        const tile = document.createElement("article");
        tile.className = "signal-tile";
        tile.innerHTML = `
            <div class="signal-topline">
                <span class="signal-indicator accent-${signal.accent}"></span>
                <small>${signal.active ? "Activ" : "Inactiv"}</small>
            </div>
            <h3>${signal.label}</h3>
            <small>${signal.description}</small>
            <strong>${signal.changed_at ? formatDateTime(signal.changed_at) : "Fara evenimente"}</strong>
        `;
        signalGrid.appendChild(tile);
    });
}

function renderButtons(signals) {
    Object.entries(signals).forEach(([signalName, signal]) => {
        const button = document.getElementById(`button-${signalName}`);
        button.classList.toggle("is-active", signal.active);
        button.textContent = signalLabels[signalName][signal.active ? "on" : "off"];
    });
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
    }

    if (operatorSnapshot.status === "error") {
        dot.classList.add("error");
    }

    text.textContent = `${operatorSnapshot.message} WorkCenter ID ${operatorSnapshot.workcenter_id}.`;

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
        primaryContainer.innerHTML = `<p class="empty-state">Nu exista operator activ pe workcenterul monitorizat.</p>`;
    }

    if (!operatorSnapshot.operators.length) {
        return;
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
    document.getElementById("metric-machine-on").textContent = stats.machine_on_label;
    document.getElementById("metric-cutting").textContent = stats.cutting_label;
    document.getElementById("metric-table-change").textContent = stats.table_change_label;
    document.getElementById("metric-idle").textContent = stats.idle_label;
    document.getElementById("utilization-value").textContent = `${stats.utilization_percent}%`;
    document.getElementById("utilization-fill").style.width = `${Math.min(stats.utilization_percent, 100)}%`;
}

function renderSource(realDataSource) {
    const dot = document.getElementById("source-dot");
    const text = document.getElementById("source-status-text");
    const panel = document.getElementById("source-panel");

    dot.className = "dot";
    if (realDataSource.status === "configured") {
        dot.classList.add("connected");
    }

    text.textContent = realDataSource.message;
    panel.innerHTML = `
        <div class="source-panel-item">
            <small>Sursa reala</small>
            <strong>${realDataSource.name}</strong>
            <p>${realDataSource.status === "configured" ? "Configurata" : "Neconfigurata"}</p>
            <small>${realDataSource.endpoint || "Completeaza LASER_REAL_DATA_ENDPOINT cand aflam cum expune laserul datele."}</small>
        </div>
    `;
}

function renderTimeline(events) {
    const timeline = document.getElementById("timeline");
    timeline.innerHTML = "";

    if (!events.length) {
        timeline.innerHTML = `<p class="empty-state">Nu exista inca evenimente.</p>`;
        return;
    }

    events.forEach((event) => {
        const item = document.createElement("article");
        item.className = "timeline-item";
        item.innerHTML = `
            <div class="timeline-meta">
                <small>${formatDateTime(event.created_at)}</small>
                <small>${event.source}</small>
            </div>
            <strong>${event.signal_label}: ${event.value ? "ON" : "OFF"}</strong>
            <p>${event.operator_name || "Fara operator"}</p>
            <small>${event.note || "Fara observatii"}${event.is_manual ? " · test manual" : ""}</small>
        `;
        timeline.appendChild(item);
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
