import { useBuilder } from '../context';
import { RUN_CAPS } from '../model';
import { TextField } from '../fields';
import { fromIsoInput, toIsoInput } from '../time';

export function LimitsSection() {
  const { draft, updateForm } = useBuilder();
  const f = draft.form;
  const shown = toIsoInput(f.startAt);
  return (
    <>
      <p className="hint">
        Runs are deterministic: the same seed and clock start give the same ids and timings every time. Time in a run is
        virtual and only moves when the run’s clock is advanced. Limits stop a run that goes on too long.
      </p>
      <TextField
        path="scenario.environment.seed"
        label="Random seed"
        type="number"
        min={0}
        required
        value={f.seed}
        help="Any whole number. Change it to get different (but still reproducible) ids."
        onChange={(v) => updateForm((d) => void (d.seed = v))}
      />
      <TextField
        path="scenario.environment.clock.start_at"
        label="Clock start (UTC)"
        type="datetime-local"
        required
        value={shown}
        help={f.startAt && !shown ? `Current value ${f.startAt} cannot be shown here; pick a time or edit it in YAML.` : 'The virtual date and time when a run begins.'}
        onChange={(v) => updateForm((d) => void (d.startAt = fromIsoInput(v)))}
      />
      <TextField
        path="scenario.evaluation.limits.max_agent_actions"
        label="Maximum agent actions"
        type="number"
        min={1}
        required
        value={f.maxActions}
        help="The run stops accepting agent operations after this many."
        onChange={(v) => updateForm((d) => void (d.maxActions = v))}
      />
      <TextField
        path="scenario.evaluation.limits.max_virtual_duration_seconds"
        label="Maximum virtual duration (seconds)"
        type="number"
        min={1}
        required
        value={f.maxDuration}
        help="86400 is one virtual day."
        onChange={(v) => updateForm((d) => void (d.maxDuration = v))}
      />
      {RUN_CAPS.map((cap) => (
        <TextField
          key={cap.key}
          path={`scenario.evaluation.limits.${cap.key}`}
          label={cap.label}
          type="number"
          min={1}
          value={f.caps?.[cap.key] ?? ''}
          placeholder={String(cap.fallback)}
          help={`${cap.help} Leave empty for the default (${cap.fallback}).`}
          onChange={(v) => updateForm((d) => void (d.caps = { ...d.caps, [cap.key]: v }))}
        />
      ))}
    </>
  );
}
