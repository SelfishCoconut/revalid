import { useEffect, useMemo, useRef, useState } from "react";

import type { ProbeResult, Settings as SettingsData } from "../api/types";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel } from "../components/ui/Panel";
import { Spinner } from "../components/Spinner";
import { useProbeProvider, useSettings, useUpdateSettings } from "../hooks/useSettings";
import { DATE_FORMATS, setDateFormat, useDateFormat } from "../lib/dateFormat";
import { errorMessage } from "../lib/format";

const inputClass =
  "mt-1 w-full rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55";
const fieldLabel =
  "block font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint";

/**
 * True when a base URL looks like a local/self-hosted Ollama endpoint. Ollama
 * lists raw model ids (e.g. `qwen3.6:27b`, where the colon is its own tag
 * separator, not a provider prefix) — those need `ollama:` prepended to become
 * a valid Pydantic AI `provider:model` string. Other OpenAI-compatible hosts
 * already return ids in the shape the backend expects.
 */
function looksLikeOllama(baseUrl: string): boolean {
  return /ollama/i.test(baseUrl) || baseUrl.includes(":11434");
}

/**
 * The editable form. Split out from {@link Settings} so its `model`/`baseUrl`
 * state can be lazily initialized from the loaded setting (`useState(() =>
 * initial.model)`) instead of synced in via a `useEffect` — the parent only
 * mounts this once `useSettings` has resolved, so the initial value is always
 * the real one and no effect-driven re-sync is needed.
 */
function SettingsForm({ initial }: { initial: SettingsData }) {
  const update = useUpdateSettings();
  const probe = useProbeProvider();

  const [model, setModel] = useState(initial.model);
  const [custom, setCustom] = useState(false);
  const [baseUrl, setBaseUrl] = useState(initial.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);

  const discoveredModels = useMemo(() => {
    if (!probeResult?.reachable) return [];
    const prefixed = looksLikeOllama(baseUrl);
    return probeResult.models.map((id) => (prefixed ? `ollama:${id}` : id));
  }, [probeResult, baseUrl]);

  // No hardcoded model list — the choices are exactly what the configured host
  // exposes (discovered below), plus the current model so a saved/offline
  // selection never silently disappears from the radio group.
  const modelOptions = useMemo(
    () => [...new Set([initial.model, ...discoveredModels])],
    [discoveredModels, initial.model],
  );

  function chooseModel(value: string) {
    setCustom(false);
    setModel(value);
  }

  function handleRefresh() {
    probe.mutate(
      { base_url: baseUrl || null, api_key: apiKey || null },
      { onSuccess: setProbeResult },
    );
  }

  // Discover models from the configured host on first mount, so the group
  // reflects what this backend actually offers without the operator having to
  // click Refresh. The ref guard keeps it to a single probe.
  const didAutoProbe = useRef(false);
  useEffect(() => {
    if (didAutoProbe.current || !baseUrl) return;
    didAutoProbe.current = true;
    probe.mutate({ base_url: baseUrl, api_key: apiKey || null }, { onSuccess: setProbeResult });
  }, [baseUrl, apiKey, probe]);

  function handleSave() {
    update.mutate({
      model,
      base_url: baseUrl || null,
      api_key: apiKey || null,
    });
  }

  return (
    <Panel className="p-5">
      <Eyebrow>Model &amp; provider</Eyebrow>
      <h1 className="mt-1.5 font-mono text-xl font-semibold text-fg">Settings</h1>
      <p className="mt-1 max-w-xl text-[13px] text-dim">
        The active LLM backend (FR-13). A saved change applies on the next extraction or
        plan — no restart needed.
      </p>

      <div className="mt-5 max-w-md space-y-4">
        <fieldset className="m-0 border-0 p-0">
          <legend className={fieldLabel}>Model</legend>
          <div className="mt-1.5 max-h-56 space-y-0.5 overflow-y-auto rounded-lg border border-line bg-panel-2 p-2">
            {modelOptions.map((option) => (
              <label
                key={option}
                className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 font-mono text-[13px] text-fg hover:bg-panel"
              >
                <input
                  type="radio"
                  name="model"
                  value={option}
                  checked={!custom && model === option}
                  onChange={() => {
                    chooseModel(option);
                  }}
                  className="accent-iris"
                />
                {option}
              </label>
            ))}
            <label className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-[13px] text-dim hover:bg-panel">
              <input
                type="radio"
                name="model"
                checked={custom}
                onChange={() => {
                  setCustom(true);
                }}
                className="accent-iris"
              />
              Custom…
            </label>
          </div>
          {custom && (
            <input
              aria-label="Custom model id"
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
              }}
              placeholder="provider:model — e.g. anthropic:claude-opus-4-8"
              className={`${inputClass} mt-2 font-mono`}
            />
          )}
        </fieldset>

        <label className={fieldLabel}>
          Base URL
          <input
            value={baseUrl}
            onChange={(event) => {
              setBaseUrl(event.target.value);
            }}
            placeholder="http://localhost:11434/v1"
            className={`${inputClass} font-mono`}
          />
        </label>

        <label className={fieldLabel}>
          API key
          <input
            type="password"
            value={apiKey}
            onChange={(event) => {
              setApiKey(event.target.value);
            }}
            placeholder={
              initial.api_key_set
                ? `set ··${initial.api_key_hint ?? ""} — leave blank to keep`
                : "optional — provider bearer token"
            }
            autoComplete="off"
            className={`${inputClass} font-mono`}
          />
        </label>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={handleRefresh} disabled={probe.isPending}>
          {probe.isPending ? "Refreshing…" : "Refresh models"}
        </Button>
        <Button onClick={handleSave} disabled={update.isPending || !model.trim()}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>

      <div className="mt-4 space-y-2">
        {probeResult && (
          <p
            role="status"
            className={`text-[13px] ${probeResult.reachable ? "text-ok-fg" : "text-danger-fg"}`}
          >
            {probeResult.reachable
              ? `Reachable — ${String(probeResult.models.length)} model${probeResult.models.length === 1 ? "" : "s"} discovered.`
              : `Unreachable — ${probeResult.error ?? "unknown error"}`}
          </p>
        )}
        {probe.isError && (
          <p role="alert" className="text-[13px] text-danger-fg">
            {errorMessage(probe.error)}
          </p>
        )}
        {update.isSuccess && (
          <p role="status" className="text-[13px] text-ok-fg">
            Saved.
          </p>
        )}
        {update.isError && (
          <p role="alert" className="text-[13px] text-danger-fg">
            {errorMessage(update.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** Client-side display preferences (stored per browser), starting with date format. */
function DisplaySettings() {
  const dateFormat = useDateFormat();

  return (
    <Panel className="p-5">
      <Eyebrow>Display</Eyebrow>
      <h2 className="mt-1.5 font-mono text-lg font-semibold text-fg">Date format</h2>
      <p className="mt-1 max-w-xl text-[13px] text-dim">
        How timestamps render across the app. Saved in this browser.
      </p>
      <fieldset className="m-0 mt-4 max-w-md border-0 p-0">
        <legend className="sr-only">Date format</legend>
        <div className="space-y-0.5 rounded-lg border border-line bg-panel-2 p-2">
          {DATE_FORMATS.map((option) => (
            <label
              key={option.id}
              className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-2 py-1.5 text-[13px] hover:bg-panel"
            >
              <span className="flex items-center gap-2 text-fg">
                <input
                  type="radio"
                  name="date-format"
                  value={option.id}
                  checked={dateFormat === option.id}
                  onChange={() => {
                    setDateFormat(option.id);
                  }}
                  className="accent-iris"
                />
                {option.label}
              </span>
              <span className="font-mono text-[12px] text-faint">{option.example}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </Panel>
  );
}

/** `/settings` route: edit the active LLM backend (FR-13, ADR-0021). */
export default function Settings() {
  const settings = useSettings();

  if (settings.isPending) {
    return (
      <Panel className="p-5">
        <Spinner label="Loading settings" />
      </Panel>
    );
  }

  if (settings.isError) {
    return (
      <Panel className="p-5">
        <p role="alert" className="text-sm text-danger-fg">
          {errorMessage(settings.error)}
        </p>
      </Panel>
    );
  }

  return (
    <div className="rev-rise space-y-6">
      <SettingsForm initial={settings.data} />
      <DisplaySettings />
    </div>
  );
}
