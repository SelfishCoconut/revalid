import { useEffect, useMemo, useRef, useState } from "react";

import type { ProbeResult, ProviderKind, Settings as SettingsData } from "../api/types";
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
 * A selectable LLM provider. Each carries the model-string prefix its ids need
 * (Ollama/OpenAI list bare ids, so a `provider:` prefix makes a valid Pydantic
 * AI `provider:model` string), the default base URL, whether it uses a base URL
 * at all (Anthropic is a native provider — no base URL), and whether a key is
 * required for discovery. Discovery dispatches on `id` server-side (Anthropic
 * authenticates differently), so the three are handled uniformly here.
 */
interface ProviderConfig {
  id: ProviderKind;
  label: string;
  prefix: string;
  defaultBaseUrl: string;
  usesBaseUrl: boolean;
  keyRequired: boolean;
  blurb: string;
}

const PROVIDERS: readonly ProviderConfig[] = [
  {
    id: "ollama",
    label: "Ollama (local)",
    prefix: "ollama:",
    defaultBaseUrl: "http://localhost:11434/v1",
    usesBaseUrl: true,
    keyRequired: false,
    blurb: "Detects a running local Ollama server and lists its models — no API key needed.",
  },
  {
    id: "anthropic",
    label: "Claude (Anthropic)",
    prefix: "anthropic:",
    defaultBaseUrl: "",
    usesBaseUrl: false,
    keyRequired: true,
    blurb: "Lists Claude models from the Anthropic API. Requires an API key.",
  },
  {
    id: "openai",
    label: "OpenAI",
    prefix: "openai:",
    defaultBaseUrl: "https://api.openai.com/v1",
    usesBaseUrl: true,
    keyRequired: true,
    blurb: "Lists OpenAI models. Requires an API key.",
  },
];

/** Infer the provider from a saved `provider:model` string (defaults to Ollama). */
function providerOf(model: string): ProviderKind {
  if (model.startsWith("anthropic:")) return "anthropic";
  if (model.startsWith("openai:")) return "openai";
  return "ollama";
}

function configFor(id: ProviderKind): ProviderConfig {
  return PROVIDERS.find((provider) => provider.id === id) ?? PROVIDERS[0];
}

/**
 * The editable form. Split out from {@link Settings} so its state can be lazily
 * initialized from the loaded setting — the parent only mounts this once
 * `useSettings` has resolved, so the initial values are always the real ones.
 */
function SettingsForm({ initial }: { initial: SettingsData }) {
  const update = useUpdateSettings();
  const probe = useProbeProvider();

  const initialProvider = providerOf(initial.model);
  const [provider, setProvider] = useState<ProviderKind>(initialProvider);
  const [model, setModel] = useState(initial.model);
  const [custom, setCustom] = useState(false);
  const [baseUrl, setBaseUrl] = useState(
    initial.base_url ?? configFor(initialProvider).defaultBaseUrl,
  );
  const [apiKey, setApiKey] = useState("");
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);

  const cfg = configFor(provider);

  // Discovered ids get the provider prefix so they land as valid model strings.
  const discoveredModels = useMemo(
    () => (probeResult?.reachable ? probeResult.models.map((id) => `${cfg.prefix}${id}`) : []),
    [probeResult, cfg.prefix],
  );

  // No hardcoded model list — the choices are exactly what the selected provider
  // exposes (discovered), plus the saved model when it belongs to this provider
  // so a saved/offline selection never silently disappears from the group.
  const modelOptions = useMemo(
    () => [
      ...new Set([
        ...(providerOf(initial.model) === provider ? [initial.model] : []),
        ...discoveredModels,
      ]),
    ],
    [discoveredModels, initial.model, provider],
  );

  function chooseModel(value: string) {
    setCustom(false);
    setModel(value);
  }

  function runProbe() {
    probe.mutate(
      { provider, base_url: cfg.usesBaseUrl ? baseUrl || null : null, api_key: apiKey || null },
      { onSuccess: setProbeResult },
    );
  }

  function chooseProvider(next: ProviderKind) {
    if (next === provider) return;
    const nextCfg = configFor(next);
    setProvider(next);
    setBaseUrl(nextCfg.usesBaseUrl ? nextCfg.defaultBaseUrl : "");
    setApiKey("");
    setProbeResult(null);
    setCustom(false);
    // Keep the saved model only if it belongs to the newly chosen provider.
    setModel(providerOf(initial.model) === next ? initial.model : "");
  }

  // Auto-discover on mount only for a keyless provider (Ollama): a keyed
  // provider's stored key is write-only, so its discovery waits for the operator
  // to (re-)enter the key and press Discover. The ref guard keeps it to one probe.
  const didAutoProbe = useRef(false);
  useEffect(() => {
    if (didAutoProbe.current) return;
    didAutoProbe.current = true;
    if (!cfg.keyRequired) runProbe();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only, ref-guarded
  }, []);

  function handleSave() {
    update.mutate({
      model,
      base_url: cfg.usesBaseUrl ? baseUrl || null : null,
      api_key: apiKey || null,
    });
  }

  return (
    <section aria-labelledby="model-settings-heading">
      <Eyebrow>Model &amp; provider</Eyebrow>
      <h2 id="model-settings-heading" className="mt-1.5 font-mono text-lg font-semibold text-fg">
        LLM backend
      </h2>
      <p className="mt-1 text-[13px] text-dim">
        The provider and model used for extraction and planning (FR-13). A saved change
        applies on the next run — no restart needed.
      </p>

      <div className="mt-5 space-y-4">
        <fieldset className="m-0 border-0 p-0">
          <legend className={fieldLabel}>Provider</legend>
          <div className="mt-1.5 grid grid-cols-3 gap-1.5">
            {PROVIDERS.map((option) => (
              <label
                key={option.id}
                className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-[12px] transition-colors ${
                  provider === option.id
                    ? "border-iris/60 bg-iris/10 text-fg"
                    : "border-line bg-panel-2 text-dim hover:bg-panel"
                }`}
              >
                <input
                  type="radio"
                  name="provider"
                  value={option.id}
                  checked={provider === option.id}
                  onChange={() => {
                    chooseProvider(option.id);
                  }}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
          </div>
          <p className="mt-1.5 text-[12px] text-faint">{cfg.blurb}</p>
        </fieldset>

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
          {modelOptions.length === 0 && !custom && (
            <p className="mt-1.5 text-[12px] text-faint">
              No models yet — {cfg.keyRequired ? "enter the API key and " : ""}press Discover.
            </p>
          )}
          {custom && (
            <input
              aria-label="Custom model id"
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
              }}
              placeholder={`${cfg.prefix}model`}
              className={`${inputClass} mt-2 font-mono`}
            />
          )}
        </fieldset>

        {cfg.usesBaseUrl && (
          <label className={fieldLabel}>
            Base URL
            <input
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
              }}
              placeholder={cfg.defaultBaseUrl}
              className={`${inputClass} font-mono`}
            />
          </label>
        )}

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
                : cfg.keyRequired
                  ? "required — provider API key"
                  : "optional — provider bearer token"
            }
            autoComplete="off"
            className={`${inputClass} font-mono`}
          />
        </label>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={runProbe} disabled={probe.isPending}>
          {probe.isPending ? "Discovering…" : "Discover models"}
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
    </section>
  );
}

/** Client-side display preferences (stored per browser), starting with date format. */
function DisplaySettings() {
  const dateFormat = useDateFormat();

  return (
    <section aria-labelledby="display-settings-heading">
      <Eyebrow>Display</Eyebrow>
      <h2 id="display-settings-heading" className="mt-1.5 font-mono text-lg font-semibold text-fg">
        Date format
      </h2>
      <p className="mt-1 text-[13px] text-dim">
        How timestamps render across the app. Saved in this browser.
      </p>
      <fieldset className="m-0 mt-4 border-0 p-0">
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
    </section>
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
    <div className="rev-rise">
      <Panel className="p-5 sm:p-6">
        <Eyebrow>Configuration</Eyebrow>
        <h1 className="mt-1.5 font-mono text-xl font-semibold text-fg">Settings</h1>
        <p className="mt-1 max-w-2xl text-[13px] text-dim">
          The active LLM backend and how the console renders for you.
        </p>
        <div className="mt-6 grid gap-x-10 gap-y-8 border-t border-line pt-6 lg:grid-cols-2">
          <SettingsForm initial={settings.data} />
          <DisplaySettings />
        </div>
      </Panel>
    </div>
  );
}
