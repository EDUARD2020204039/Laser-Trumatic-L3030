const signalLabels = {
    machine_on: { on: "Opreste masina", off: "Porneste masina" },
    cutting_active: { on: "Opreste productia", off: "Porneste productia" },
    table_change: { on: "Opreste schimbul", off: "Porneste schimbul" },
    idle_abort: { on: "Opreste idle/abort", off: "Porneste idle/abort" }
};

const state = {
    dashboard: null,
    savedRecords: null,
    savedModbusRecords: null,
    isSubmitting: false,
    selectedMachineKey: window.appConfig.defaultMachineKey || "laser1",
    currentView: window.localStorage.getItem("currentView") || "dashboard",
    savedPeriod: window.localStorage.getItem("savedPeriod") || "all",
    savedModbusPeriod: window.localStorage.getItem("savedModbusPeriod") || "day",
    savedOperatorId: window.localStorage.getItem("savedOperatorId") || "",
    savedModbusOperatorId: window.localStorage.getItem("savedModbusOperatorId") || "",
    workcenterFeedback: null,
    modbusFeedback: null,
    modbusDraft: null,
    modbusDraftDirty: false,
    modbusDraftMachineKey: null,
    lastSavedRefreshMs: 0,
    lastStatsSnapshot: null,
    lastStatsSyncMs: 0,
    dashboardRequestId: 0,
    savedRequestId: 0,
    dashboardFetchInFlight: false,
    savedFetchInFlight: false,
    dashboardAbortController: null,
    savedAbortController: null,
    renderedFeedsSignature: "",
    liveExtractionLayoutKey: "",
    feedRefreshTimers: []
};

const savedPeriodReportLabelMap = {
    day: "Zilnic",
    week: "Saptamanal",
    month: "Lunar"
};

if (!["dashboard", "saved", "saved_modbus"].includes(state.currentView)) {
    state.currentView = "dashboard";
}
if (!["day", "week", "month"].includes(state.savedModbusPeriod)) {
    state.savedModbusPeriod = "day";
}

function getMachineOnMetricLabel(machineKey) {
    return "Timp activ pe program";
}

function getAvailabilityPrefix(machineKey) {
    return machineKey === "abkant"
        ? "Disponibilitate indoire/feed_activ"
        : "Disponibilitate taiere/feed_activ";
}

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
            subtitle: "Fiecare foaie se salveaza la final de table change, iar randamentul zilei este calculat cumulat din Cutting sau Bending raportat la timpul activ acumulat azi.",
            hint: `Filtrul ia foile finalizate in ${todayLabel}. Intervalul este strict 00:00 -> acum pentru ziua curenta.`,
            countLabel: "Foi azi",
            reportCardText: "Randament real cumulat pe foile finalizate azi",
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
            subtitle: "Randamentul saptamanii se calculeaza cumulat din toate foile finalizate in saptamana curenta.",
            hint: `Filtrul ia intervalul ${formatSavedPeriodDate(weekStart)} - ${formatSavedPeriodDate(now)}. Fiecare foaie noua adauga timpii ei la totalul real al saptamanii.`,
            countLabel: "Foi saptamana",
            reportCardText: "Randament real cumulat pe foile finalizate saptamana aceasta",
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
            subtitle: "Randamentul lunii vine din totalul timpilor acumulati pe toate foile finalizate in luna curenta.",
            hint: `Filtrul ia intervalul ${formatSavedPeriodDate(monthStart)} - ${formatSavedPeriodDate(now)}. Randamentul este calculat din total Cutting sau Bending raportat la totalul timpilor activi.`,
            countLabel: "Foi luna",
            reportCardText: "Randament real cumulat pe foile finalizate luna aceasta",
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
        hint: "Fiecare foaie este salvata la final de table change. Randamentul afisat aici este calculat cumulat din total Cutting sau Bending impartit la totalul timpilor activi.",
        countLabel: "Total foi",
        reportCardText: "Randament real cumulat din toate foile salvate",
        machineReportTitle: "Detaliu operator",
        emptySummary: "Nu exista inca date salvate in istoric.",
        emptyReports: "Rezumatul operatorului se va afisa aici dupa primele foi salvate.",
        emptyMachineReports: "Explicatia de calcul se va afisa aici dupa primele foi salvate.",
        emptyRecords: "Cand se termina table change, foaia se salveaza automat aici."
    };
}

function getSavedModbusPeriodMeta(period, payload) {
    const windowLabel = payload?.window_label || "";
    if (period === "day") {
        return {
            key: "day",
            sectionTitle: "Date Salvate MODBUS - Zilnic",
            subtitle: "Se folosesc doar ciclurile LASER1MODBUS din ziua curenta si se actualizeaza dupa fiecare program finalizat.",
            hint: `Randamentul zilnic este media tuturor randamentelor salvate azi. Regula provizorie: daca durata de Table change depaseste 01:30, timpul care depaseste pragul se penalizeaza in randament ca timp de tip idle, fara sa modifice indicatorul Idle afisat separat. Urmeaza seturi de reguli pe grosimea tablei. ${windowLabel}`.trim(),
            countLabel: "Cicluri MODBUS azi",
            emptySummary: "Nu exista inca cicluri MODBUS salvate azi.",
            emptyRecords: "Nu exista inca randamente MODBUS salvate azi."
        };
    }

    if (period === "week") {
        return {
            key: "week",
            sectionTitle: "Date Salvate MODBUS - Saptamanal",
            subtitle: "Media saptamanala se calculeaza doar dupa o saptamana completa.",
            hint: `Se afiseaza ultima saptamana completa incheiata. ${windowLabel}`.trim(),
            countLabel: "Cicluri MODBUS saptamana",
            emptySummary: "Nu exista cicluri MODBUS salvate in ultima saptamana completa.",
            emptyRecords: "Nu exista randamente MODBUS in ultima saptamana completa."
        };
    }

    return {
        key: "month",
        sectionTitle: "Date Salvate MODBUS - Lunar",
        subtitle: "Media lunara se calculeaza doar dupa o luna completa.",
        hint: `Se afiseaza ultima luna completa incheiata. ${windowLabel}`.trim(),
        countLabel: "Cicluri MODBUS luna",
        emptySummary: "Nu exista cicluri MODBUS salvate in ultima luna completa.",
        emptyRecords: "Nu exista randamente MODBUS in ultima luna completa."
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
    renderMachineSelector(window.appConfig.initialMachines || []);
    window.setInterval(() => {
        tickLiveStats();
    }, 1000);
    if (state.currentView === "saved") {
        prepareSavedViewLoadingState();
        loadSavedRecords();
    } else if (state.currentView === "saved_modbus") {
        prepareSavedModbusViewLoadingState();
        loadSavedModbusRecords();
    } else {
        syncSectionVisibility("dashboard");
        loadDashboard(state.selectedMachineKey);
    }
    window.setInterval(() => {
        if (state.currentView === "saved") {
            const now = Date.now();
            if (state.savedFetchInFlight || now - state.lastSavedRefreshMs < 15000) {
                return;
            }
            loadSavedRecords();
            return;
        }
        if (state.currentView === "saved_modbus") {
            const now = Date.now();
            if (state.savedFetchInFlight || now - state.lastSavedRefreshMs < 15000) {
                return;
            }
            loadSavedModbusRecords();
            return;
        }
        if (state.dashboardFetchInFlight) {
            return;
        }
        loadDashboard(state.selectedMachineKey);
    }, 3000);
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

function prepareSavedViewLoadingState() {
    const periodMeta = getSavedPeriodMeta(state.savedPeriod);
    syncSectionVisibility("saved");
    renderSavedHeader(
        {
            period: state.savedPeriod,
            records_count: 0,
            data_source: "loading"
        },
        periodMeta
    );
    renderSavedFilters(state.savedPeriod);
    renderSavedModbusOperatorFilter(null);
    document.getElementById("saved-summary").innerHTML = `<p class="empty-state">Se incarca operatorii si istoricul salvat...</p>`;
    document.getElementById("saved-reports").innerHTML = `<p class="empty-state">Se pregateste raportul pentru perioada selectata...</p>`;
    document.getElementById("saved-machine-reports").innerHTML = `<p class="empty-state">Se pregateste detaliul pe utilaj...</p>`;
    document.getElementById("saved-record-list").innerHTML = `<p class="empty-state">Se incarca istoricul ${periodMeta.countLabel.toLowerCase()}...</p>`;
}

function prepareSavedModbusViewLoadingState() {
    const periodMeta = getSavedModbusPeriodMeta(state.savedModbusPeriod, null);
    syncSectionVisibility("saved_modbus");
    renderSavedModbusHeader(periodMeta);
    renderSavedFilters(state.savedModbusPeriod, { includeAll: false });
    renderSavedModbusOperatorFilter({
        operators: [],
        selected_operator_id: state.savedModbusOperatorId
    });
    document.getElementById("saved-summary").innerHTML = `<p class="empty-state">Se incarca sumarul MODBUS...</p>`;
    document.getElementById("saved-reports").innerHTML = `<p class="empty-state">Se calculeaza media randamentelor MODBUS...</p>`;
    document.getElementById("saved-machine-reports").innerHTML = `<p class="empty-state">Se pregateste intervalul complet pentru perioada selectata...</p>`;
    document.getElementById("saved-record-list").innerHTML = `<p class="empty-state">Se incarca randamentele MODBUS salvate...</p>`;
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

            if (nextView === "saved_modbus") {
                if (state.currentView === "saved_modbus") {
                    await loadSavedModbusRecords();
                    return;
                }

                state.currentView = "saved_modbus";
                window.localStorage.setItem("currentView", state.currentView);
                await loadSavedModbusRecords();
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
            state.modbusFeedback = null;
            clearModbusDraft();
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

            const nextPeriod = button.dataset.savedPeriod || "all";
            if (state.currentView === "saved_modbus") {
                state.savedModbusPeriod = ["day", "week", "month"].includes(nextPeriod) ? nextPeriod : "day";
                window.localStorage.setItem("savedModbusPeriod", state.savedModbusPeriod);
                await loadSavedModbusRecords();
                return;
            }

            state.savedPeriod = nextPeriod;
            window.localStorage.setItem("savedPeriod", state.savedPeriod);
            await loadSavedRecords();
        });
    }

    const savedSummary = document.getElementById("saved-summary");
    if (savedSummary) {
        savedSummary.addEventListener("click", (event) => {
            if (state.currentView !== "saved") {
                return;
            }
            const card = event.target.closest("[data-operator-id]");
            if (!card) {
                return;
            }

            state.savedOperatorId = card.dataset.operatorId || "";
            window.localStorage.setItem("savedOperatorId", state.savedOperatorId);
            if (state.savedRecords) {
                state.savedRecords.selected_operator_id = state.savedOperatorId;
                renderSavedView(state.savedRecords);
                return;
            }
            loadSavedRecords();
        });
    }

    const savedOperatorSelect = document.getElementById("saved-operator-select");
    if (savedOperatorSelect) {
        savedOperatorSelect.addEventListener("change", async () => {
            if (state.currentView !== "saved_modbus") {
                return;
            }
            state.savedModbusOperatorId = savedOperatorSelect.value || "";
            if (state.savedModbusOperatorId) {
                window.localStorage.setItem("savedModbusOperatorId", state.savedModbusOperatorId);
            } else {
                window.localStorage.removeItem("savedModbusOperatorId");
            }
            await loadSavedModbusRecords();
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

    const saveModbusButton = document.getElementById("save-modbus-config");
    if (saveModbusButton) {
        saveModbusButton.addEventListener("click", updateModbusConfig);
    }

    const modbusTransportInput = document.getElementById("modbus-transport-input");
    if (modbusTransportInput) {
        modbusTransportInput.addEventListener("change", () => {
            markModbusDraftDirty();
            syncModbusTransportFields();
        });
    }

    const modbusConfigSection = document.getElementById("modbus-config-section");
    if (modbusConfigSection) {
        const handleDraftChange = (event) => {
            if (!event.target?.id?.startsWith("modbus-")) {
                return;
            }
            if (event.target.id === "modbus-feedback") {
                return;
            }
            markModbusDraftDirty();
        };
        modbusConfigSection.addEventListener("input", handleDraftChange);
        modbusConfigSection.addEventListener("change", handleDraftChange);
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
    const requestId = state.dashboardRequestId + 1;
    state.dashboardRequestId = requestId;

    if (state.dashboardAbortController) {
        state.dashboardAbortController.abort();
    }
    state.dashboardAbortController = new AbortController();
    state.dashboardFetchInFlight = true;

    try {
        const response = await fetch(
            `${window.appConfig.dashboardUrl}?machine=${encodeURIComponent(targetMachineKey)}`,
            { signal: state.dashboardAbortController.signal }
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Nu am putut incarca dashboard-ul.");
        }
        if (requestId !== state.dashboardRequestId) {
            return;
        }

        state.dashboard = payload;
        state.selectedMachineKey = payload.selected_machine_key;
        if (state.modbusDraftMachineKey && state.modbusDraftMachineKey !== payload.selected_machine_key) {
            clearModbusDraft();
        }
        window.localStorage.setItem("selectedMachineKey", payload.selected_machine_key);
        window.localStorage.setItem("currentView", "dashboard");
        state.currentView = "dashboard";
        renderDashboard(payload);
    } catch (error) {
        if (error.name === "AbortError") {
            return;
        }
        console.error(error);
        setWorkcenterFeedback(error.message, "error");
    } finally {
        if (requestId === state.dashboardRequestId) {
            state.dashboardFetchInFlight = false;
            state.dashboardAbortController = null;
        }
    }
}

async function loadSavedRecords() {
    const requestId = state.savedRequestId + 1;
    state.savedRequestId = requestId;

    if (state.savedAbortController) {
        state.savedAbortController.abort();
    }
    state.savedAbortController = new AbortController();
    state.savedFetchInFlight = true;

    try {
        const query = new URLSearchParams({ period: state.savedPeriod });
        if (state.savedOperatorId) {
            query.set("operator_id", state.savedOperatorId);
        }
        const response = await fetch(
            `${window.appConfig.savedRecordsUrl}?${query.toString()}`,
            { signal: state.savedAbortController.signal }
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Nu am putut incarca datele salvate.");
        }
        if (requestId !== state.savedRequestId) {
            return;
        }

        state.savedRecords = payload;
        state.savedPeriod = payload.period || state.savedPeriod;
        state.savedOperatorId = payload.selected_operator_id || "";
        state.lastSavedRefreshMs = Date.now();
        state.currentView = "saved";
        window.localStorage.setItem("currentView", "saved");
        window.localStorage.setItem("savedPeriod", state.savedPeriod);
        if (state.savedOperatorId) {
            window.localStorage.setItem("savedOperatorId", state.savedOperatorId);
        } else {
            window.localStorage.removeItem("savedOperatorId");
        }
        renderSavedView(payload);
    } catch (error) {
        if (error.name === "AbortError") {
            return;
        }
        console.error(error);
        window.alert(error.message);
    } finally {
        if (requestId === state.savedRequestId) {
            state.savedFetchInFlight = false;
            state.savedAbortController = null;
        }
    }
}

async function loadSavedModbusRecords() {
    const requestId = state.savedRequestId + 1;
    state.savedRequestId = requestId;

    if (state.savedAbortController) {
        state.savedAbortController.abort();
    }
    state.savedAbortController = new AbortController();
    state.savedFetchInFlight = true;

    try {
        const query = new URLSearchParams({ period: state.savedModbusPeriod });
        if (state.savedModbusOperatorId) {
            query.set("operator_id", state.savedModbusOperatorId);
        }
        const response = await fetch(
            `${window.appConfig.savedModbusRecordsUrl}?${query.toString()}`,
            { signal: state.savedAbortController.signal }
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Nu am putut incarca datele salvate MODBUS.");
        }
        if (requestId !== state.savedRequestId) {
            return;
        }

        state.savedModbusRecords = payload;
        state.savedModbusPeriod = payload.period || state.savedModbusPeriod;
        state.savedModbusOperatorId = payload.selected_operator_id || "";
        state.lastSavedRefreshMs = Date.now();
        state.currentView = "saved_modbus";
        window.localStorage.setItem("currentView", "saved_modbus");
        window.localStorage.setItem("savedModbusPeriod", state.savedModbusPeriod);
        if (state.savedModbusOperatorId) {
            window.localStorage.setItem("savedModbusOperatorId", state.savedModbusOperatorId);
        } else {
            window.localStorage.removeItem("savedModbusOperatorId");
        }
        renderSavedModbusView(payload);
    } catch (error) {
        if (error.name === "AbortError") {
            return;
        }
        console.error(error);
        window.alert(error.message);
    } finally {
        if (requestId === state.savedRequestId) {
            state.savedFetchInFlight = false;
            state.savedAbortController = null;
        }
    }
}

function getFilteredSavedRecords(payload) {
    const records = payload?.records || [];
    const selectedOperatorId = payload?.selected_operator_id || "";
    if (!selectedOperatorId) {
        return records;
    }

    return records.filter((record) => {
        const recordOperatorId = String(record.operator_id || "").trim();
        const recordOperatorName = record.operator_name || "Fara operator la salvare";
        const resolvedOperatorId = recordOperatorId || `name:${recordOperatorName}`;
        return resolvedOperatorId === selectedOperatorId;
    });
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

async function updateModbusConfig() {
    if (state.isSubmitting) {
        return;
    }

    const machineKey = state.selectedMachineKey;
    const hostInput = document.getElementById("modbus-host-input");
    if (!hostInput) {
        return;
    }

    state.isSubmitting = true;
    syncBusyState();

    try {
        const response = await fetch(
            `${window.appConfig.machinesUrl}/${encodeURIComponent(machineKey)}`,
            {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    modbus_config: {
                        transport: document.getElementById("modbus-transport-input").value,
                        host: hostInput.value.trim(),
                        port: document.getElementById("modbus-port-input").value.trim(),
                        serial_port: document.getElementById("modbus-serial-port-input").value.trim(),
                        serial_baudrate: document.getElementById("modbus-serial-baudrate-input").value.trim(),
                        serial_parity: document.getElementById("modbus-serial-parity-input").value,
                        serial_stopbits: document.getElementById("modbus-serial-stopbits-input").value,
                        unit_id: document.getElementById("modbus-unit-id-input").value.trim(),
                        bit_source: document.getElementById("modbus-bit-source-input").value,
                        start_address: document.getElementById("modbus-start-address-input").value.trim(),
                        poll_timeout_seconds: document.getElementById("modbus-timeout-input").value.trim(),
                        in1_signal: document.getElementById("modbus-in1-signal").value,
                        in2_signal: document.getElementById("modbus-in2-signal").value,
                        in3_signal: document.getElementById("modbus-in3-signal").value,
                        in4_signal: document.getElementById("modbus-in4-signal").value
                    }
                })
            }
        );

        const payload = await response.json();
        if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Nu am putut salva configuratia Modbus.");
        }

        state.modbusFeedback = {
            machineKey,
            tone: "success",
            message: `Configuratia Modbus a fost salvata pentru ${payload.machine.label}.`
        };
        clearModbusDraft();
        state.dashboard = payload.dashboard;
        renderDashboard(payload.dashboard);
    } catch (error) {
        setModbusFeedback(error.message, "error");
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
    renderModbusConfig(payload.machine);
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
    const filteredRecords = getFilteredSavedRecords(payload);
    syncSectionVisibility("saved");
    renderSavedHeader(payload, periodMeta);
    renderMachineSelector(state.dashboard?.machines || window.appConfig.initialMachines || []);
    renderSavedSummary(payload, periodMeta);
    renderSavedFilters(currentPeriod, { includeAll: true });
    renderSavedModbusOperatorFilter(null);
    renderSavedReports(payload, currentPeriod, periodMeta);
    renderSavedMachineReports(payload, currentPeriod, periodMeta);
    renderSavedRecords(filteredRecords, periodMeta);
}

function renderSavedModbusView(payload) {
    const currentPeriod = payload.period || state.savedModbusPeriod;
    const periodMeta = getSavedModbusPeriodMeta(currentPeriod, payload);
    syncSectionVisibility("saved_modbus");
    renderSavedModbusHeader(periodMeta, payload);
    renderMachineSelector(state.dashboard?.machines || window.appConfig.initialMachines || []);
    renderSavedFilters(currentPeriod, { includeAll: false });
    renderSavedModbusOperatorFilter(payload);
    renderSavedModbusSummary(payload, periodMeta);
    renderSavedModbusReports(payload, periodMeta);
    renderSavedModbusRecords(payload, periodMeta);
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

function renderSavedModbusHeader(periodMeta, payload = null) {
    document.getElementById("dashboard-title").textContent = "Date Salvate MODBUS";
    document.getElementById("dashboard-subtitle").textContent = periodMeta.subtitle;
    document.getElementById("saved-section-title").textContent = periodMeta.sectionTitle;
    document.getElementById("saved-period-hint").textContent = periodMeta.hint;
    document.getElementById("saved-records-label").textContent = periodMeta.countLabel;
    document.getElementById("saved-records-count").textContent = String(payload?.records_count || 0);
}

function renderMachineSelector(machines) {
    const selector = document.getElementById("machine-selector");
    const machineButtons = machines
        .map((machine) => `
            <button
                class="machine-tab ${state.currentView === "dashboard" && machine.key === state.selectedMachineKey ? "is-selected" : ""}"
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

    const savedModbusButton = `
        <button
            class="machine-tab saved-tab ${state.currentView === "saved_modbus" ? "is-selected" : ""}"
            data-view="saved_modbus"
            type="button"
        >
            <small>Arhiva</small>
            <strong>DATE SALVATE MODBUS</strong>
            <span>Doar ciclurile LASER1MODBUS, cu medie pe zi, saptamana completa si luna completa.</span>
        </button>
    `;

    selector.innerHTML = `${machineButtons}${savedButton}${savedModbusButton}`;
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
        if (signal.visible === false) {
            return;
        }
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

function renderModbusInputValue(modbusInputs, inputKey) {
    const input = (modbusInputs || []).find((item) => item.key === inputKey);
    if (!input) {
        return "Necitit";
    }
    return `${input.active ? "1" : "0"} / ${input.signal || "unused"}`;
}

function collectModbusDraftFromDom() {
    return {
        transport: document.getElementById("modbus-transport-input")?.value || "tcp",
        host: document.getElementById("modbus-host-input")?.value.trim() || "",
        port: document.getElementById("modbus-port-input")?.value.trim() || "",
        serial_port: document.getElementById("modbus-serial-port-input")?.value.trim() || "",
        serial_baudrate: document.getElementById("modbus-serial-baudrate-input")?.value.trim() || "",
        serial_parity: document.getElementById("modbus-serial-parity-input")?.value || "N",
        serial_stopbits: document.getElementById("modbus-serial-stopbits-input")?.value || "1",
        unit_id: document.getElementById("modbus-unit-id-input")?.value.trim() || "",
        bit_source: document.getElementById("modbus-bit-source-input")?.value || "discrete_input",
        start_address: document.getElementById("modbus-start-address-input")?.value.trim() || "",
        poll_timeout_seconds: document.getElementById("modbus-timeout-input")?.value.trim() || "",
        signal_map: {
            in1: document.getElementById("modbus-in1-signal")?.value || "unused",
            in2: document.getElementById("modbus-in2-signal")?.value || "unused",
            in3: document.getElementById("modbus-in3-signal")?.value || "unused",
            in4: document.getElementById("modbus-in4-signal")?.value || "unused"
        }
    };
}

function markModbusDraftDirty() {
    state.modbusDraft = collectModbusDraftFromDom();
    state.modbusDraftDirty = true;
    state.modbusDraftMachineKey = state.selectedMachineKey;
}

function clearModbusDraft() {
    state.modbusDraft = null;
    state.modbusDraftDirty = false;
    state.modbusDraftMachineKey = null;
}

function syncSelectOptions(select, options, desiredValue) {
    if (!select) {
        return;
    }

    const optionMarkup = (options || [])
        .map((option) => `<option value="${option.value}">${option.label}</option>`)
        .join("");

    if (select.dataset.optionsMarkup !== optionMarkup) {
        select.innerHTML = optionMarkup;
        select.dataset.optionsMarkup = optionMarkup;
    }

    const safeDesiredValue = desiredValue == null ? "" : String(desiredValue);
    const activeValue = document.activeElement === select ? select.value : safeDesiredValue;
    const hasOption = Array.from(select.options).some((option) => option.value === activeValue);
    if (hasOption) {
        select.value = activeValue;
    }
}

function syncModbusTransportFields() {
    const transportSelect = document.getElementById("modbus-transport-input");
    const transport = transportSelect?.value || "tcp";

    document.querySelectorAll("[data-modbus-transport-scope]").forEach((node) => {
        node.classList.toggle("is-hidden", node.dataset.modbusTransportScope !== transport);
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

function renderModbusConfig(machine) {
    const section = document.getElementById("modbus-config-section");
    if (!section) {
        return;
    }

    const config = machine?.modbus_config;
    const isVisible = Boolean(config);
    section.classList.toggle("is-hidden", !isVisible);
    if (!isVisible) {
        return;
    }

    const displayConfig = state.modbusDraftDirty && state.modbusDraftMachineKey === machine.key
        ? {
            ...config,
            ...state.modbusDraft,
            signal_map: {
                ...(config.signal_map || {}),
                ...(state.modbusDraft?.signal_map || {})
            }
        }
        : config;

    const fieldIds = {
        transport: "modbus-transport-input",
        host: "modbus-host-input",
        port: "modbus-port-input",
        serial_port: "modbus-serial-port-input",
        serial_baudrate: "modbus-serial-baudrate-input",
        unit_id: "modbus-unit-id-input",
        bit_source: "modbus-bit-source-input",
        start_address: "modbus-start-address-input",
        poll_timeout_seconds: "modbus-timeout-input"
    };

    Object.entries(fieldIds).forEach(([fieldName, fieldId]) => {
        const input = document.getElementById(fieldId);
        if (!input || document.activeElement === input) {
            return;
        }
        input.value = displayConfig[fieldName] ?? "";
    });

    const transportSelect = document.getElementById("modbus-transport-input");
    syncSelectOptions(transportSelect, config.transport_options || [], displayConfig.transport || "tcp");

    const serialParitySelect = document.getElementById("modbus-serial-parity-input");
    syncSelectOptions(serialParitySelect, config.serial_parity_options || [], displayConfig.serial_parity || "N");

    const serialStopbitsSelect = document.getElementById("modbus-serial-stopbits-input");
    syncSelectOptions(
        serialStopbitsSelect,
        config.serial_stopbits_options || [],
        String(displayConfig.serial_stopbits || 1)
    );

    ["in1", "in2", "in3", "in4"].forEach((inputKey) => {
        const select = document.getElementById(`modbus-${inputKey}-signal`);
        if (!select) {
            return;
        }
        const currentValue = displayConfig.signal_map?.[inputKey] || "unused";
        const options = config.signal_options || [];
        syncSelectOptions(select, options, currentValue);
    });

    syncModbusTransportFields();

    const feedback = state.modbusFeedback?.machineKey === machine.key
        ? state.modbusFeedback
        : {
            tone: state.modbusDraftDirty && state.modbusDraftMachineKey === machine.key
                ? "muted"
                : (config.enabled ? "success" : "muted"),
            message: state.modbusDraftDirty && state.modbusDraftMachineKey === machine.key
                ? "Ai modificari Modbus nesalvate. Ele raman in formular pana apesi Salveaza Modbus."
                : (
                    config.enabled
                        ? `Containerul citeste ${config.transport === "rtu" ? "Modbus RTU" : "Modbus TCP"} din ${config.endpoint}. Maparea intrarilor poate fi schimbata oricand.`
                        : "Alege transportul Modbus, completeaza endpointul si salveaza maparea IN1..IN4."
                )
        };
    setModbusFeedback(feedback.message, feedback.tone);
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
    } else if (operatorSnapshot.operators?.length) {
        const operator = operatorSnapshot.operators[0];
        primaryContainer.innerHTML = `
            <div class="operator-primary-item">
                <small>Ultimul operator cunoscut pe workcenter</small>
                <strong>${operator.full_name}</strong>
                <p>ID angajat: ${operator.employee_id}</p>
                <small>${operator.last_seen ? `Ultimul pontaj: ${operator.last_seen}` : "Ultimul pontaj necunoscut"}</small>
            </div>
        `;
    } else {
        primaryContainer.innerHTML = `
            <p class="empty-state">Nu exista operator activ pentru workcenterul configurat.</p>
        `;
    }

    operatorSnapshot.operators.slice(operatorSnapshot.primary_operator ? 1 : 0).forEach((operator) => {
        const pill = document.createElement("div");
        pill.className = "operator-pill";
        pill.innerHTML = `
            <p>${operator.full_name}</p>
            <small>ID ${operator.employee_id}${operator.is_active ? " | activ acum" : operator.last_seen ? ` | ultim pontaj ${operator.last_seen}` : ""}</small>
        `;
        listContainer.appendChild(pill);
    });
}

function renderStats(stats) {
    state.lastStatsSnapshot = {
        machine_on_seconds: Number(stats.machine_on_seconds || 0),
        cutting_seconds: Number(stats.cutting_seconds || 0),
        table_change_seconds: Number(stats.table_change_seconds || 0),
        idle_seconds: Number(stats.idle_seconds || 0),
        machine_on_changed_at: state.dashboard?.current_signals?.machine_on?.changed_at || null,
        production_window_started_at: stats.production_window_started_at || null,
        randament_percent: Number(stats.randament_percent || 0),
        availability_percent: Number(stats.availability_percent || 0),
        cutting_metric_label: stats.cutting_metric_label || "Cutting",
        table_change_metric_label: stats.table_change_metric_label || "Table change",
        signals: {
            machine_on: Boolean(state.dashboard?.current_signals?.machine_on?.active),
            cutting_active: Boolean(state.dashboard?.current_signals?.cutting_active?.active),
            table_change: Boolean(state.dashboard?.current_signals?.table_change?.active),
            idle: Boolean(state.dashboard?.current_signals?.idle_abort?.active),
        },
        machine_key: state.dashboard?.machine?.key || state.selectedMachineKey
    };
    state.lastStatsSyncMs = Date.now();
    updateStatsDisplay(computeDisplayedStats());
}

function computeDisplayedStats() {
    if (!state.lastStatsSnapshot) {
        return null;
    }

    const snapshot = state.lastStatsSnapshot;
    const elapsedSeconds = Math.max(Math.floor((Date.now() - state.lastStatsSyncMs) / 1000), 0);
    const machineOnSeconds = snapshot.machine_on_seconds + (snapshot.signals.machine_on ? elapsedSeconds : 0);
    const cuttingSeconds = snapshot.cutting_seconds + (snapshot.signals.cutting_active ? elapsedSeconds : 0);
    const tableChangeSeconds = snapshot.table_change_seconds + (snapshot.signals.table_change ? elapsedSeconds : 0);
    const idleSeconds = snapshot.idle_seconds + (snapshot.signals.idle ? elapsedSeconds : 0);
    const sessionWindowSeconds = snapshot.signals.machine_on && snapshot.machine_on_changed_at
        ? Math.max(Math.floor((Date.now() - new Date(snapshot.machine_on_changed_at).getTime()) / 1000), 0)
        : 0;
    const productiveSeconds = cuttingSeconds + tableChangeSeconds;
    const randamentPercent = machineOnSeconds > 0
        ? roundToOneDecimal((Math.min(productiveSeconds, machineOnSeconds) / machineOnSeconds) * 100)
        : 0;
    const availabilityPrefix = getAvailabilityPrefix(snapshot.machine_key);

    return {
        machineOnSeconds,
        cuttingSeconds,
        tableChangeSeconds,
        idleSeconds,
        sessionWindowSeconds,
        randamentPercent,
        availabilityLabel: `${availabilityPrefix} ${randamentPercent}%`,
        cuttingMetricLabel: snapshot.cutting_metric_label,
        tableChangeMetricLabel: snapshot.table_change_metric_label
    };
}

function updateStatsDisplay(displayedStats) {
    if (!displayedStats) {
        return;
    }

    document.getElementById("metric-label-machine-on").textContent = getMachineOnMetricLabel(state.lastStatsSnapshot?.machine_key);
    document.getElementById("metric-window-label").textContent = "Machine ON";
    document.getElementById("metric-label-cutting").textContent = displayedStats.cuttingMetricLabel || "Cutting";
    document.getElementById("metric-label-table-change").textContent = displayedStats.tableChangeMetricLabel || "Table change";
    document.getElementById("metric-label-idle").textContent = "Idle";
    document.getElementById("metric-randament").textContent = `${displayedStats.randamentPercent}%`;
    document.getElementById("metric-availability").textContent = displayedStats.availabilityLabel;
    document.getElementById("metric-window").textContent = formatSeconds(displayedStats.sessionWindowSeconds);
    document.getElementById("metric-machine-on").textContent = formatSeconds(displayedStats.machineOnSeconds);
    document.getElementById("metric-cutting").textContent = formatSeconds(displayedStats.cuttingSeconds);
    document.getElementById("metric-table-change").textContent = formatSeconds(displayedStats.tableChangeSeconds);
    document.getElementById("metric-idle").textContent = formatSeconds(displayedStats.idleSeconds);
}

function tickLiveStats() {
    if (state.currentView !== "dashboard" || !state.lastStatsSnapshot) {
        return;
    }

    updateStatsDisplay(computeDisplayedStats());
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
    let cells;
    if (currentMachineKey === "abkant") {
        cells = [
            { slot: "program", label: "Program curent", value: snapshot.active_program || "Necitit" },
            { slot: "upper_tool", label: "Upper", value: snapshot.upper_tool || "n/a" },
            { slot: "lower_tool", label: "Lower", value: snapshot.lower_tool || "n/a" },
            { slot: "total", label: "Piese de indoit", value: snapshot.total_pieces ?? "Necunoscut" },
            { slot: "produced", label: "Piese indoite", value: snapshot.produced_pieces ?? 0 },
            { slot: "progress", label: "Progres", value: snapshot.pieces_label || "n/a" },
            { slot: "machine_on", label: "Feed activ", value: signals.machine_on ? "DA" : "NU" },
            { slot: "bending", label: "Bending", value: signals.cutting_active ? "DA" : "NU" },
            { slot: "setup_change", label: "Setup change", value: signals.table_change ? "DA" : "NU" },
            { slot: "status", label: "Status program", value: snapshot.program_status || "Necitit" }
        ];
    } else if (currentMachineKey === "laser1modbus") {
        cells = [
            { slot: "selected_program", label: "Selected program", value: snapshot.selected_program || "Necitit" },
            { slot: "active_program", label: "Active program", value: snapshot.active_program || "Necitit" },
            { slot: "material", label: "Material", value: snapshot.material || "Necitit" },
            { slot: "program_status", label: "Program status", value: snapshot.program_status || "Necitit" },
            { slot: "in1", label: "IN1", value: renderModbusInputValue(snapshot.modbus_inputs, "in1") },
            { slot: "in2", label: "IN2", value: renderModbusInputValue(snapshot.modbus_inputs, "in2") },
            { slot: "in3", label: "IN3", value: renderModbusInputValue(snapshot.modbus_inputs, "in3") },
            { slot: "in4", label: "IN4", value: renderModbusInputValue(snapshot.modbus_inputs, "in4") },
            { slot: "machine_on", label: "Machine ON", value: signals.machine_on ? "DA" : "NU" },
            { slot: "cutting", label: "Cutting", value: signals.cutting_active ? "DA" : "NU" },
            { slot: "table_change", label: "Table change", value: signals.table_change ? "DA" : "NU" },
            { slot: "idle", label: "Idle / Aborted", value: signals.idle_abort ? "DA" : (signals.idle ? "IDLE" : "NU") }
        ];
    } else {
        cells = [
            { slot: "selected_program", label: "Selected program", value: snapshot.selected_program || "Necitit" },
            { slot: "active_program", label: "Active program", value: snapshot.active_program || "Necitit" },
            { slot: "material", label: "Material", value: snapshot.material || "Necitit" },
            { slot: "program_status", label: "Program status", value: snapshot.program_status || "Necitit" },
            { slot: "machine_on", label: "Feed activ", value: signals.machine_on ? "DA" : "NU" },
            { slot: "cutting", label: "Cutting", value: signals.cutting_active ? "DA" : "NU" },
            { slot: "table_change", label: "Table change", value: signals.table_change ? "DA" : "NU" },
            { slot: "idle", label: "Idle", value: signals.idle ? "DA" : "NU" }
        ];
    }

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
            <strong>${eventItem.signal_label}: ${eventItem.value ? "Activ" : "Inactiv"}</strong>
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

function isAbkantOnlyOperator(operatorEntry) {
    const machines = operatorEntry?.machines || [];
    return machines.length > 0 && machines.every((machineLabel) => machineLabel === "Abkant");
}

function getSavedUnitLabels(operatorEntry) {
    if (isAbkantOnlyOperator(operatorEntry)) {
        return {
            plural: "piese",
            singular: "piesa"
        };
    }

    return {
        plural: "foi",
        singular: "foaie"
    };
}

function getSavedUnitLabelsForMachine(machineKey) {
    if (machineKey === "abkant") {
        return {
            plural: "piese",
            singular: "piesa"
        };
    }

    return {
        plural: "foi",
        singular: "foaie"
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
    const recordsCount = getFilteredSavedRecords(payload).length;
    count.textContent = String(recordsCount);

    const operators = payload.operators || [];
    if (operators.length) {

        container.innerHTML = operators
            .map((item) => {
                const day = getSavedOperatorPeriod(item, "day");
                const week = getSavedOperatorPeriod(item, "week");
                const month = getSavedOperatorPeriod(item, "month");
                const unitLabels = getSavedUnitLabels(item);
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
                        <small>${item.machines?.length ? `Utilaj: ${item.machines.join(", ")}` : "Utilaj: necunoscut"}</small>
                        <p>${day.records_count} ${unitLabels.plural} azi, ${week.records_count} saptamana, ${month.records_count} luna</p>
                        <div class="saved-operator-periods">
                            <div class="saved-operator-period">
                                <span>Zi</span>
                                <strong>${roundToOneDecimal(day.efficiency_percent)}%</strong>
                                <small>${day.records_count} ${unitLabels.plural}</small>
                            </div>
                            <div class="saved-operator-period">
                                <span>Sapt.</span>
                                <strong>${roundToOneDecimal(week.efficiency_percent)}%</strong>
                                <small>${week.records_count} ${unitLabels.plural}</small>
                            </div>
                            <div class="saved-operator-period">
                                <span>Luna</span>
                                <strong>${roundToOneDecimal(month.efficiency_percent)}%</strong>
                                <small>${month.records_count} ${unitLabels.plural}</small>
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

function renderSavedFilters(period, options = { includeAll: true }) {
    const includeAll = options.includeAll !== false;
    document.querySelectorAll("[data-saved-period]").forEach((button) => {
        const isAllButton = button.dataset.savedPeriod === "all";
        button.classList.toggle("is-hidden", isAllButton && !includeAll);
        button.classList.toggle("is-selected", button.dataset.savedPeriod === period);
    });
}

function renderSavedModbusOperatorFilter(payload = null) {
    const wrapper = document.getElementById("saved-operator-filter");
    const select = document.getElementById("saved-operator-select");
    if (!wrapper || !select) {
        return;
    }

    wrapper.classList.toggle("is-hidden", state.currentView !== "saved_modbus");
    if (state.currentView !== "saved_modbus") {
        return;
    }

    const operators = payload?.operators || [];
    const selectedOperatorId = payload?.selected_operator_id || state.savedModbusOperatorId || "";
    const options = [
        `<option value="">Toti operatorii</option>`,
        ...operators.map((operator) => `<option value="${operator.operator_id}">${operator.operator_name}${operator.employee_id ? ` (ID ${operator.employee_id})` : ""}</option>`)
    ];
    select.innerHTML = options.join("");
    select.value = selectedOperatorId;
}

function renderSavedReports(payload, period, periodMeta) {
    const container = document.getElementById("saved-reports");
    if ((payload.operators || []).length) {
        const selectedOperator = getSelectedSavedOperator(payload);
        if (!selectedOperator) {
            container.innerHTML = `<p class="empty-state">${periodMeta.emptyReports}</p>`;
            return;
        }

        const periodStats = getSavedOperatorPeriod(selectedOperator, period);
        const unitLabels = getSavedUnitLabels(selectedOperator);
        const filteredRecords = getFilteredSavedRecords(payload);
        const activityLabel = filteredRecords[0]?.activity_label || "Cutting";
        const changeLabel = filteredRecords[0]?.change_label || "Table change";
        container.innerHTML = `
            <article class="saved-report-card">
                <small>Operator selectat</small>
                <strong>${selectedOperator.operator_name}</strong>
                <small>${selectedOperator.employee_id ? `ID angajat ${selectedOperator.employee_id}` : "ID angajat indisponibil"}</small>
                <small>${selectedOperator.machines?.length ? `Utilaj: ${selectedOperator.machines.join(", ")}` : "Utilaj: necunoscut"}</small>
                <p>${periodMeta.reportCardText}</p>
                <div class="saved-report-metrics">
                    <span>${periodStats.records_count} ${unitLabels.plural} finalizate</span>
                    <span>Randament real cumulat: ${roundToOneDecimal(periodStats.efficiency_percent)}%</span>
                    <span>Timp activ ${periodStats.machine_on_label}</span>
                    <span>${activityLabel} ${periodStats.cutting_label}</span>
                    <span>Idle ${periodStats.idle_label}</span>
                    <span>${changeLabel} ${periodStats.table_change_label}</span>
                </div>
            </article>
            <article class="saved-report-card">
                <small>Sursa</small>
                <strong>${payload.data_source === "prometheus" ? "Prometheus" : "Istoric salvat"}</strong>
                <p>Datele salvate se citesc din seria istorica de ${unitLabels.plural} finalizate, nu dintr-un calcul live din browser.</p>
                <div class="saved-report-metrics">
                    <span>Fiecare ${unitLabels.singular} se inchide dupa terminarea table change.</span>
                    <span>Timpul in afara feedului activ nu intra in randamentul ${unitLabels.singular}.</span>
                    <span>${payload.data_source === "prometheus" ? "Prometheus raspunde direct pentru istoric." : "Exportul Prometheus ramane activ; istoricul afisat vine din cache-ul salvat al aplicatiei."}</span>
                    <span>Zi, saptamana si luna folosesc media ${unitLabels.plural} finalizate in perioada respectiva.</span>
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
    if ((payload.operators || []).length) {
        const records = getFilteredSavedRecords(payload);
        if (!records.length) {
            container.innerHTML = `<p class="empty-state">${periodMeta.emptyMachineReports}</p>`;
            return;
        }

        const selectedOperator = getSelectedSavedOperator(payload);
        const unitLabels = getSavedUnitLabels(selectedOperator);
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
                            <span>${unitLabels.plural.charAt(0).toUpperCase() + unitLabels.plural.slice(1)} finalizate</span>
                            <strong>${item.records_count}</strong>
                            <small>Timp activ ${formatSeconds(item.machine_on_seconds)}</small>
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

    const groupedRecords = new Map();
    records.forEach((record) => {
        const groupKey = `${record.machine_key}::${record.selected_program}`;
        const unitLabels = getSavedUnitLabelsForMachine(record.machine_key);
        const existing = groupedRecords.get(groupKey) || {
            id: record.id,
            machine_key: record.machine_key,
            machine_label: record.machine_label,
            selected_program: record.selected_program,
            active_programs: new Set(),
            materials: new Set(),
            operators: new Set(),
            activity_label: record.activity_label,
            change_label: record.change_label,
            records_count: 0,
            machine_on_duration_seconds: 0,
            cycle_duration_seconds: 0,
            idle_duration_seconds: 0,
            table_change_duration_seconds: 0,
            cutting_started_at: null,
            table_change_started_at: null,
            table_change_ended_at: null,
            program_statuses: new Set(),
            unitLabels
        };

        existing.records_count += 1;
        existing.machine_on_duration_seconds += Number(record.machine_on_duration_seconds || 0);
        existing.cycle_duration_seconds += Number(record.cycle_duration_seconds || 0);
        existing.idle_duration_seconds += Number(record.idle_duration_seconds || 0);
        existing.table_change_duration_seconds += Number(record.table_change_duration_seconds || 0);
        existing.active_programs.add(record.active_program || "Necitit");
        existing.materials.add(record.material || "Necitit");
        existing.operators.add(record.operator_name || "Fara operator la salvare");
        existing.program_statuses.add(record.program_status || "Necitit");

        if (record.cutting_started_at && (!existing.cutting_started_at || new Date(record.cutting_started_at) < new Date(existing.cutting_started_at))) {
            existing.cutting_started_at = record.cutting_started_at;
        }

        if (record.table_change_started_at && (!existing.table_change_started_at || new Date(record.table_change_started_at) < new Date(existing.table_change_started_at))) {
            existing.table_change_started_at = record.table_change_started_at;
        }

        const currentEnd = record.table_change_ended_at || record.created_at;
        if (currentEnd && (!existing.table_change_ended_at || new Date(currentEnd) > new Date(existing.table_change_ended_at))) {
            existing.table_change_ended_at = currentEnd;
        }

        groupedRecords.set(groupKey, existing);
    });

    const aggregatedRecords = Array.from(groupedRecords.values())
        .map((record) => {
            const efficiencyPercent = record.machine_on_duration_seconds > 0
                ? roundToOneDecimal((record.cycle_duration_seconds / record.machine_on_duration_seconds) * 100)
                : 0;
            return {
                ...record,
                operator_name: record.operators.size === 1 ? Array.from(record.operators)[0] : "Mai multi operatori",
                active_program: record.active_programs.size === 1 ? Array.from(record.active_programs)[0] : "Mixt",
                material: record.materials.size === 1 ? Array.from(record.materials)[0] : "Mixt",
                program_status: record.program_statuses.size === 1 ? Array.from(record.program_statuses)[0] : "Status mixt",
                efficiency_percent: efficiencyPercent,
                machine_on_duration_label: formatSeconds(record.machine_on_duration_seconds),
                cycle_duration_label: formatSeconds(record.cycle_duration_seconds),
                idle_duration_label: formatSeconds(record.idle_duration_seconds),
                table_change_duration_label: formatSeconds(record.table_change_duration_seconds)
            };
        })
        .sort((left, right) => new Date(right.table_change_ended_at || 0) - new Date(left.table_change_ended_at || 0));

    container.innerHTML = aggregatedRecords
        .map((record) => `
            <article class="saved-record-card">
                <div class="saved-record-top">
                    <div>
                        <small>${record.machine_label}</small>
                        <strong>${record.selected_program}</strong>
                        <small>${record.records_count} ${record.unitLabels.plural} pe acelasi program</small>
                    </div>
                    <div class="saved-record-meta">
                        <small>${record.operator_name}</small>
                        <strong>${roundToOneDecimal(record.efficiency_percent || 0)}%</strong>
                        <small>Randament program</small>
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
                        <span>Timp activ</span>
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
                        <span>Primul ${record.change_label?.toLowerCase() || "schimb masa"}</span>
                        <strong>${record.table_change_started_at ? formatDateTime(record.table_change_started_at) : "Necunoscut"}</strong>
                    </div>
                    <div>
                        <span>Ultimul ${record.change_label?.toLowerCase() || "schimb masa"}</span>
                        <strong>${record.table_change_ended_at ? formatDateTime(record.table_change_ended_at) : "In lucru"}</strong>
                    </div>
                    <div>
                        <span>Durata ${record.change_label?.toLowerCase() || "schimb masa"}</span>
                        <strong>${record.table_change_duration_label || "00:00:00"}</strong>
                    </div>
                </div>
                <p class="saved-record-note">
                    Status la salvare: ${record.program_status}. Operator: ${record.operator_name}. Agregat din ${record.records_count} ${record.unitLabels.plural} pentru programul ${record.selected_program}.
                </p>
            </article>
        `)
        .join("");
}

function renderSavedModbusSummary(payload, periodMeta) {
    const container = document.getElementById("saved-summary");
    const count = document.getElementById("saved-records-count");
    count.textContent = String(payload.records_count || 0);
    const selectedOperator = (payload.operators || []).find((operator) => operator.operator_id === payload.selected_operator_id) || null;
    const operatorLabel = selectedOperator
        ? `${selectedOperator.operator_name}${selectedOperator.employee_id ? ` (ID ${selectedOperator.employee_id})` : ""}`
        : "Toti operatorii";

    if (!payload.records_count) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptySummary}</p>`;
        return;
    }

    container.innerHTML = `
        <article class="saved-summary-card">
            <small>${payload.machine_label || "LASER1MODBUS"}</small>
            <strong>${roundToOneDecimal(payload.average_efficiency_percent || 0)}%</strong>
            <p>Media randamentelor salvate in perioada selectata.</p>
            <small>Operator selectat: ${operatorLabel}</small>
            <small>${payload.window_label || ""}</small>
            <small>Interval: ${formatDateTime(payload.window_started_at)} - ${formatDateTime(payload.window_ended_at)}</small>
        </article>
    `;
}

function renderSavedModbusReports(payload, periodMeta) {
    const reports = document.getElementById("saved-reports");
    const machineReports = document.getElementById("saved-machine-reports");
    const records = payload.records || [];
    if (!records.length) {
        reports.innerHTML = `<p class="empty-state">${periodMeta.emptyRecords}</p>`;
        machineReports.innerHTML = `<p class="empty-state">${periodMeta.emptyRecords}</p>`;
        return;
    }

    reports.innerHTML = `
        <article class="saved-report-card">
            <small>Formula</small>
            <strong>${roundToOneDecimal(payload.average_efficiency_percent || 0)}%</strong>
            <p>Media randamentelor individuale salvate pentru ciclurile MODBUS din interval.</p>
            <div class="saved-report-metrics">
                <span>Formula ciclu: (Cutting + Table change) / Timp activ pe program</span>
                <span>Cicluri incluse: ${records.length}</span>
                <span>${payload.is_closed_period ? "Perioada este completa (inchisa)." : "Perioada este in curs (zi curenta)."}</span>
            </div>
        </article>
    `;

    const byProgram = new Map();
    records.forEach((record) => {
        const key = record.selected_program || "Necitit";
        const item = byProgram.get(key) || { program: key, efficiencies: [] };
        item.efficiencies.push(Number(record.efficiency_percent || 0));
        byProgram.set(key, item);
    });

    const programRows = Array.from(byProgram.values())
        .map((item) => ({
            program: item.program,
            average: item.efficiencies.length
                ? roundToOneDecimal(item.efficiencies.reduce((sum, value) => sum + value, 0) / item.efficiencies.length)
                : 0,
            count: item.efficiencies.length
        }))
        .sort((left, right) => right.average - left.average);

    machineReports.innerHTML = `
        <article class="saved-machine-report-card">
            <small>Randament pe program</small>
            <strong>${payload.machine_label || "LASER1MODBUS"}</strong>
            <div class="saved-machine-period-list">
                ${programRows.map((row) => `
                    <div class="saved-machine-period-item">
                        <span>${row.program}</span>
                        <strong>${row.average}%</strong>
                        <small>${row.count} cicluri</small>
                    </div>
                `).join("")}
            </div>
        </article>
    `;
}

function renderSavedModbusRecords(payload, periodMeta) {
    const container = document.getElementById("saved-record-list");
    const records = payload.records || [];
    if (!records.length) {
        container.innerHTML = `<p class="empty-state">${periodMeta.emptyRecords}</p>`;
        return;
    }

    const sortedRecords = [...records].sort(
        (left, right) => new Date(right.table_change_ended_at || right.created_at || 0) - new Date(left.table_change_ended_at || left.created_at || 0)
    );
    container.innerHTML = sortedRecords
        .map((record) => {
            const closeReasonLabel = record.close_reason_label || "Ciclu incheiat";
            const resetMoment = record.table_change_ended_at || record.created_at;
            const resetSummary = record.close_reason === "program_change"
                ? `Reset timp activ pe program: ${closeReasonLabel}${record.next_program ? ` -> program nou ${record.next_program}` : ""}.`
                : `Reset timp activ pe program: ${closeReasonLabel}.`;
            return `
            <article class="saved-record-card">
                <div class="saved-record-top">
                    <div>
                        <small>${record.machine_label || "LASER1MODBUS"}</small>
                        <strong>${record.selected_program || "Necitit"}</strong>
                        <small>${record.operator_name || "Fara operator la salvare"}</small>
                    </div>
                    <div class="saved-record-meta">
                        <small>${record.table_change_ended_at ? formatDateTime(record.table_change_ended_at) : formatDateTime(record.created_at)}</small>
                        <strong>${roundToOneDecimal(record.efficiency_percent || 0)}%</strong>
                        <small>Randament ciclu</small>
                    </div>
                </div>
                <div class="saved-record-grid">
                    <div>
                        <span>Timp activ pe program</span>
                        <strong>${record.machine_on_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>${record.activity_label || "Cutting"}</span>
                        <strong>${record.cycle_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>${record.change_label || "Table change"}</span>
                        <strong>${record.table_change_duration_label || "00:00:00"}</strong>
                    </div>
                    <div>
                        <span>Idle</span>
                        <strong>${record.idle_duration_label || "00:00:00"}</strong>
                    </div>
                </div>
                <p class="saved-record-note">
                    ${resetSummary} Salvare: ${resetMoment ? formatDateTime(resetMoment) : "Necunoscut"}.
                </p>
            </article>
        `;
        })
        .join("");
}

function syncSectionVisibility(view) {
    document.getElementById("dashboard-overview-section").classList.toggle("is-hidden", view !== "dashboard");
    document.getElementById("dashboard-integrated-section").classList.toggle("is-hidden", view !== "dashboard");
    document.getElementById("saved-section").classList.toggle("is-hidden", !["saved", "saved_modbus"].includes(view));
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

function setModbusFeedback(message, tone = "muted") {
    state.modbusFeedback = {
        machineKey: state.selectedMachineKey,
        tone,
        message
    };
    const feedback = document.getElementById("modbus-feedback");
    if (!feedback) {
        return;
    }
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
