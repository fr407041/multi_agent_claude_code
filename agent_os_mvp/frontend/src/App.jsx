import { useEffect, useState } from 'react';

const API = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010';
const lanes = ['running', 'waiting', 'done', 'failed', 'idle'];

function Pill({ value }) {
  return <span className={`pill pill-${String(value || 'unknown').toLowerCase()}`}>{value || 'unknown'}</span>;
}

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value ?? 'n/a'}</strong></div>;
}

function AgentCard({ agent }) {
  return <article className="agent-card">
    <div className="agent-head"><strong>{agent.role}</strong><Pill value={agent.state} /></div>
    <p>Task: {agent.task_id || 'n/a'}</p>
    <p>Profile: {agent.agent_profile || 'n/a'}</p>
    <p>Policy: {agent.policy || 'n/a'}</p>
    {agent.note ? <p className="muted">{agent.note}</p> : null}
  </article>;
}

export default function App() {
  const [monitor, setMonitor] = useState(null);
  const [selectedRun, setSelectedRun] = useState(null);
  const [error, setError] = useState('');

  async function load(runId) {
    try {
      setError('');
      const res = await fetch(`${API}/api/ai-company-monitor`);
      if (!res.ok) throw new Error(`monitor ${res.status}`);
      const payload = await res.json();
      setMonitor(payload);
      const nextRunId = runId || payload?.latest_run?.run_id;
      if (nextRunId) {
        const detail = await fetch(`${API}/api/ai-company-monitor/runs/${nextRunId}`);
        if (!detail.ok) throw new Error(`run detail ${detail.status}`);
        setSelectedRun(await detail.json());
      } else {
        setSelectedRun(null);
      }
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => { load(); }, []);

  const summary = monitor?.all_runs_summary || { total_runs: 0, pass_count: 0, fail_count: 0, unknown_count: 0 };
  const counts = selectedRun?.agent_counts || {};
  const artifact = selectedRun?.artifact_verify?.parsed || selectedRun?.artifact_verify || {};
  const claims = selectedRun?.claim_ledger?.claims || [];
  const watchdog = selectedRun?.watchdog || {};

  return <main>
    <header className="topbar">
      <div><p className="eyebrow">AI Company</p><h1>Run Monitor</h1></div>
      <div className="actions">
        <select value={selectedRun?.run_id || ''} onChange={(e) => load(e.target.value)}>
          {(monitor?.recent_runs || []).map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id}</option>)}
        </select>
        <button onClick={() => load(selectedRun?.run_id)}>Refresh</button>
      </div>
    </header>

    {error ? <section className="alert">{error}</section> : null}

    <section className="grid two">
      <div className="card"><h2>All runs</h2><div className="metrics"><Metric label="Total" value={summary.total_runs} /><Metric label="Pass" value={summary.pass_count} /><Metric label="Fail" value={summary.fail_count} /><Metric label="Unknown" value={summary.unknown_count} /></div></div>
      <div className="card"><h2>Current run</h2>{selectedRun ? <><Pill value={selectedRun.overall_status} /><div className="metrics"><Metric label="Done" value={counts.done} /><Metric label="Running" value={counts.running} /><Metric label="Waiting" value={counts.waiting} /><Metric label="Failed" value={counts.failed} /><Metric label="Idle" value={counts.idle} /></div></> : <p>No runs yet.</p>}</div>
    </section>

    <section className="card"><h2>Agent board</h2><div className="lanes">{lanes.map((lane) => <div className="lane" key={lane}><h3>{lane}</h3>{(selectedRun?.agent_state_board?.[lane] || []).map((agent, idx) => <AgentCard key={`${lane}-${idx}`} agent={agent} />)}</div>)}</div></section>

    <section className="grid two">
      <div className="card"><h2>Trustworthiness</h2><p>Artifact score: {artifact.score ?? 'n/a'}</p><p>All passed: {String(artifact.all_passed ?? 'n/a')}</p><pre>{JSON.stringify(artifact.checks || {}, null, 2)}</pre></div>
      <div className="card"><h2>Watchdog / Memory</h2><p>Watchdog: {watchdog.watchdog_status || 'n/a'}</p><p>Last action: {watchdog.last_action || 'n/a'}</p><p>Memory checkpoints: {selectedRun?.memory_guard?.memory_checkpoint_count ?? 'n/a'}</p></div>
    </section>

    <section className="card"><h2>Claims / Evidence</h2>{claims.length ? claims.map((claim, idx) => <div className="claim" key={idx}><strong>{claim.agent_profile || claim.task_id}</strong><p>{claim.claim}</p><small>{(claim.evidence_refs || []).join(', ')}</small></div>) : <p>No claim ledger yet.</p>}</section>
  </main>;
}
