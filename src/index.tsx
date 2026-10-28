import { callable } from "@decky/api";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  definePlugin,
  staticClasses,
} from "@decky/ui";
import { FC, useCallback, useEffect, useRef, useState } from "react";

interface Settings {
  managed: boolean;
  enabled: boolean;
  normal_mode: string;
}

interface Status {
  success?: boolean;
  error?: string;
  go2: boolean;
  product: string;
  companion: boolean;
  ready: boolean;
  reason: string;
  enabled: boolean | null;
  capacity: number | null;
  charging_status: string | null;
  limit_percent: number;
  current_mode: string | null;
  backend: string;
  settings: Settings;
  last_error: string;
}

const getStatus = callable<[], Status>("get_status");
const setEnabled = callable<[boolean], Status>("set_enabled");
const backendResume = callable<[], Status>("resume");

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        window.clearTimeout(timer);
        reject(err);
      },
    );
  });
}

type SteamSub = { unregister: () => void } | null;

function registerResume(callback: () => void): SteamSub {
  const steam = (window as unknown as { SteamClient?: any }).SteamClient;
  if (steam?.System?.RegisterForOnResumeFromSuspend) {
    return steam.System.RegisterForOnResumeFromSuspend(callback);
  }
  if (steam?.User?.RegisterForResumeSuspendedGamesProgress) {
    return steam.User.RegisterForResumeSuspendedGamesProgress(() => callback());
  }
  return null;
}

const Icon: FC = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M16 4h-2V2h-4v2H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-1.5 12.5h-5v-1.5h5v1.5zm0-3.5h-5V12h5v1zm0-3.5h-5V8.5h5V9.5z" />
  </svg>
);

function batteryLine(status: Status): string {
  const parts: string[] = [];
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

const Content: FC = () => {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const pending = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (pending.current > 0) return;
    let lastError = "The plugin backend did not reply";
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        const next = await withTimeout(getStatus(), 8000, lastError);
        if (!mounted.current || pending.current > 0) return;
        setStatus(next);
        setError(next.last_error || next.error || "");
        return;
      } catch (err) {
        lastError = err instanceof Error ? err.message : "Could not read Charge Limit status";
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
    }
    if (mounted.current) setError(lastError);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), status?.ready ? 8000 : 1500);
    return () => clearInterval(timer);
  }, [refresh, status?.ready]);

  const run = useCallback(async (operation: () => Promise<Status>) => {
    pending.current += 1;
    try {
      const next = await operation();
      if (!mounted.current) return;
      setStatus(next);
      setError(next.success === false ? next.error || next.last_error || "The battery did not confirm" : "");
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "The change failed");
    } finally {
      pending.current -= 1;
    }
  }, []);

  if (!status) {
    return (
      <PanelSection title="Charge Limit">
        <PanelSectionRow>
          <div>{error || "Loading…"}</div>
        </PanelSectionRow>
        {error && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void refresh()}>
              Retry
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
    );
  }

  if (!status.go2) {
    return (
      <PanelSection title="Charge Limit">
        <PanelSectionRow>
          <div>
            {status.last_error || status.error
              ? status.last_error || status.error
              : `This plugin is for the Legion Go 2 (83N0 / 83N1). This device reports ${status.product || "an unknown model"}.`}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()}>
            Retry
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const checked = status.enabled === true;
  const line = batteryLine(status);

  return (
    <>
      {status.companion && (
        <PanelSection title="Conflict">
          <PanelSectionRow>
            <div>
              Legion Go 2 Companion is still installed. Uninstall it, or turn off its Battery module, so the two plugins do not fight over Charge Limit.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Battery">
        {line && (
          <PanelSectionRow>
            <div>{line}</div>
          </PanelSectionRow>
        )}
        {!status.ready && (
          <PanelSectionRow>
            <div>{status.reason || "Waiting for the battery…"}</div>
          </PanelSectionRow>
        )}
        {error && status.ready && (
          <PanelSectionRow>
            <div>{error}</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ToggleField
            label="Charge Limit"
            description={`Stops charging at about ${status.limit_percent}%. Same setting as Desktop Mode Power Management.`}
            checked={checked}
            disabled={!status.ready}
            onChange={(value: boolean) => {
              setStatus((current) => (current ? { ...current, enabled: value } : current));
              void run(() => setEnabled(value));
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <div>
            Turning this off charges to 100%. A battery already above the limit is not drained.
          </div>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};

export default definePlugin(() => {
  const resumeSub = registerResume(() => {
    void backendResume();
  });
  return {
    title: <div className={staticClasses.Title}>Legion Go 2 Charge Limit</div>,
    content: <Content />,
    icon: <Icon />,
    onDismount() {
      resumeSub?.unregister();
    },
  };
});
