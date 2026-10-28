const manifest = {"name":"Legion Go 2 Charge Limit"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;

const getStatus = callable("get_status");
const setEnabled = callable("set_enabled");
const backendResume = callable("resume");
function withTimeout(promise, ms, message) {
    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error(message)), ms);
        promise.then((value) => {
            window.clearTimeout(timer);
            resolve(value);
        }, (err) => {
            window.clearTimeout(timer);
            reject(err);
        });
    });
}
function registerResume(callback) {
    const steam = window.SteamClient;
    if (steam?.System?.RegisterForOnResumeFromSuspend) {
        return steam.System.RegisterForOnResumeFromSuspend(callback);
    }
    if (steam?.User?.RegisterForResumeSuspendedGamesProgress) {
        return steam.User.RegisterForResumeSuspendedGamesProgress(() => callback());
    }
    return null;
}
const Icon = () => (window.SP_REACT.createElement("svg", { width: "20", height: "20", viewBox: "0 0 24 24", fill: "currentColor", "aria-hidden": "true" },
    window.SP_REACT.createElement("path", { d: "M16 4h-2V2h-4v2H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-1.5 12.5h-5v-1.5h5v1.5zm0-3.5h-5V12h5v1zm0-3.5h-5V8.5h5V9.5z" })));
function batteryLine(status) {
    const parts = [];
    if (status.capacity !== null) {
        parts.push(`Battery ${status.capacity}%`);
    }
    if (status.charging_status) {
        parts.push(status.charging_status);
    }
    if (status.enabled) {
        parts.push(`limit ${status.limit_percent}%`);
    }
    return parts.join(" · ");
}
const Content = () => {
    const [status, setStatus] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState("");
    const pending = SP_REACT.useRef(0);
    const mounted = SP_REACT.useRef(true);
    SP_REACT.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);
    const refresh = SP_REACT.useCallback(async () => {
        if (pending.current > 0)
            return;
        let lastError = "The plugin backend did not reply";
        for (let attempt = 0; attempt < 5; attempt += 1) {
            try {
                const next = await withTimeout(getStatus(), 8000, lastError);
                if (!mounted.current || pending.current > 0)
                    return;
                setStatus(next);
                setError(next.last_error || next.error || "");
                return;
            }
            catch (err) {
                lastError = err instanceof Error ? err.message : "Could not read Charge Limit status";
                await new Promise((resolve) => window.setTimeout(resolve, 1000));
            }
        }
        if (mounted.current)
            setError(lastError);
    }, []);
    SP_REACT.useEffect(() => {
        void refresh();
        const timer = setInterval(() => void refresh(), status?.ready ? 8000 : 1500);
        return () => clearInterval(timer);
    }, [refresh, status?.ready]);
    const run = SP_REACT.useCallback(async (operation) => {
        pending.current += 1;
        try {
            const next = await operation();
            if (!mounted.current)
                return;
            setStatus(next);
            setError(next.success === false ? next.error || next.last_error || "The battery did not confirm" : "");
        }
        catch (err) {
            if (mounted.current)
                setError(err instanceof Error ? err.message : "The change failed");
        }
        finally {
            pending.current -= 1;
        }
    }, []);
    if (!status) {
        return (window.SP_REACT.createElement(DFL.PanelSection, { title: "Charge Limit" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, error || "Loading…")),
            error && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ButtonItem, { layout: "below", onClick: () => void refresh() }, "Retry")))));
    }
    if (!status.go2) {
        return (window.SP_REACT.createElement(DFL.PanelSection, { title: "Charge Limit" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, status.last_error || status.error
                    ? status.last_error || status.error
                    : `This plugin is for the Legion Go 2 (83N0 / 83N1). This device reports ${status.product || "an unknown model"}.`)),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ButtonItem, { layout: "below", onClick: () => void refresh() }, "Retry"))));
    }
    const checked = status.enabled === true;
    const line = batteryLine(status);
    return (window.SP_REACT.createElement(window.SP_REACT.Fragment, null,
        status.companion && (window.SP_REACT.createElement(DFL.PanelSection, { title: "Conflict" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, "Legion Go 2 Companion is still installed. Uninstall it, or turn off its Battery module, so the two plugins do not fight over Charge Limit.")))),
        window.SP_REACT.createElement(DFL.PanelSection, { title: "Battery" },
            line && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, line))),
            !status.ready && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, status.reason || "Waiting for the battery…"))),
            error && status.ready && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, error))),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ToggleField, { label: "Charge Limit", description: `Stops charging at about ${status.limit_percent}%. Same setting as Desktop Mode Power Management.`, checked: checked, disabled: !status.ready, onChange: (value) => {
                        setStatus((current) => (current ? { ...current, enabled: value } : current));
                        void run(() => setEnabled(value));
                    } })),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, "Turning this off charges to 100%. A battery already above the limit is not drained.")))));
};
var index = DFL.definePlugin(() => {
    const resumeSub = registerResume(() => {
        void backendResume();
    });
    return {
        title: window.SP_REACT.createElement("div", { className: DFL.staticClasses.Title }, "Legion Go 2 Charge Limit"),
        content: window.SP_REACT.createElement(Content, null),
        icon: window.SP_REACT.createElement(Icon, null),
        onDismount() {
            resumeSub?.unregister();
        },
    };
});

export { index as default };
//# sourceMappingURL=index.js.map
