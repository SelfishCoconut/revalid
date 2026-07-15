import { useMemo, useState } from "react";

import type { ProbeResult, Settings as SettingsData } from "../api/types";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel } from "../components/ui/Panel";
import { Spinner } from "../components/Spinner";
import { useProbeProvider, useSettings, useUpdateSettings } from "../hooks/useSettings";
import { errorMessage } from "../lib/format";

/**
 * Curated backends known to work with this tool: the ADR-0021 local-first
 * default, a lighter local variant, and the native Anthropic fallback. The
 * probe below adds whatever a live host actually discovers, so this list is a
 * starting point, not a restriction — the field accepts any string.
 */
const KNOWN_MODELS = ["ollama:qwen3.6:27b", "ollama:qwen3:14b", "anthropic:claude-sonnet-5"];

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
  const [baseUrl, setBaseUrl] = useState(initial.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);

  const discoveredModels = useMemo(() => {
    if (!probeResult?.reachable) return [];
    const prefixed = looksLikeOllama(baseUrl);
    return probeResult.models.map((id) => (prefixed ? `ollama:${id}` : id));
  }, [probeResult, baseUrl]);

  const modelOptions = useMemo(
    () => [...new Set([...KNOWN_MODELS, ...discoveredModels])],
    [discoveredModels],
  );

  function handleProbe() {
    probe.mutate(
      { base_url: baseUrl || null, api_key: apiKey || null },
      { onSuccess: setProbeResult },
    );
  }

  function handleSave() {
    update.mutate({ model, base_url: baseUrl || null, api_key: apiKey || null });
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
        <label className={fieldLabel}>
          Model
          <input
            list="settings-model-options"
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
            }}
            placeholder="ollama:qwen3.6:27b"
            className={`${inputClass} font-mono`}
          />
          <datalist id="settings-model-options">
            {modelOptions.map((option) => (
              <option key={option} value={option} />
            ))}
          </datalist>
        </label>

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
        <Button variant="ghost" onClick={handleProbe} disabled={probe.isPending}>
          {probe.isPending ? "Testing…" : "Test connection"}
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
    </div>
  );
}
