import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8010";
const BOARD_STATES = ["running", "waiting", "done", "failed", "idle"];

const emptyMonitor = {
  overview: {
    total_runs: 0,
    pass_count: 0,
    fail_count: 0,
    unknown_count: 0,
    specs: [],
    latest_status: "unknown",
  },
  all_runs_summary: {
    total_runs: 0,
    pass_count: 0,
    fail_count: 0,
    unknown_count: 0,
    status_breakdown: { pass: 0, fail: 0, unknown: 0 },
    unknown_runs: [],
  },
  selected_run_preview: null,
  latest_run: null,
  recent_runs: [],
};

function normalizeStatus(value) {
  return String(value || "unknown").toLowerCase().replace(/\s+/g, "-");
}

function StatusPill({ value }) {
  return <span className={`status-pill status-${normalizeStatus(value)}`}>{value || "unknown"}</span>;
}

function formatDate(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderList(items) {
  if (!items || items.length === 0) return "n/a";
  return items.join(", ");
}

function stateTitle(state) {
  const map = {
    running: "Running",
    waiting: "Waiting",
    done: "Done",
    failed: "Failed",
    idle: "Idle",
  };
  return map[state] || state;
}

function fallbackText(value) {
  return value || "No explicit fallback plan recorded.";
}

function renderCount(value) {
  return value ?? "n/a";
}

function formatTokens(value) {
  if (value === null || value === undefined || value === "") return "n/a";
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return value;
  if (numberValue >= 1000000) return `${(numberValue / 1000000).toFixed(1)}M`;
  if (numberValue >= 1000) return `${(numberValue / 1000).toFixed(1)}K`;
  return String(numberValue);
}

function formatEvidenceRef(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return "";
  const path = item.path || item.file || item.source || "";
  return path ? `${item.type || "file"}:${path}` : "";
}

function MetricTile({ label, value }) {
  return (
    <article className="metric-tile">
      <span>{label}</span>
      <strong>{renderCount(value)}</strong>
    </article>
  );
}

function SectionHeader({ title, meta, status }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      <div className="section-meta">
        {meta ? <span>{meta}</span> : null}
        {status ? <StatusPill value={status} /> : null}
      </div>
    </div>
  );
}

function AgentCard({ item }) {
  return (
    <article className={`agent-card agent-card-${normalizeStatus(item.state)}`}>
      <div className="agent-card-topline">
        <strong>{item.role}</strong>
        <StatusPill value={item.state} />
      </div>
      <dl className="compact-dl">
        <div>
          <dt>Task</dt>
          <dd>{item.task_id || "n/a"}</dd>
        </div>
        <div>
          <dt>Profile</dt>
          <dd>{item.agent_profile || "n/a"} / {item.profile_mode || "n/a"}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>{item.effective_policy_level || "n/a"}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>{formatTokens(item.token_usage_summary?.estimated_total_tokens)}</dd>
        </div>
        <div>
          <dt>Scope</dt>
          <dd>{renderList(item.scope)}</dd>
        </div>
      </dl>
      {item.verification_note ? <p className="agent-note">{item.verification_note}</p> : null}
      {item.fallback_plan ? <p className="agent-note">Fallback: {item.fallback_plan}</p> : null}
    </article>
  );
}

function DetailDisclosure({ title, open, children }) {
  return (
    <details className="detail-panel" open={open}>
      <summary>{title}</summary>
      <div className="detail-body">{children}</div>
    </details>
  );
}

function TrustChecks({ checks }) {
  const entries = Object.entries(checks || {});
  if (entries.length === 0) return <p className="muted">No artifact checks have been recorded.</p>;
  return (
    <div className="check-list">
      {entries.map(([key, value]) => (
        <div key={key} className={`check-row ${value ? "check-pass" : "check-fail"}`}>
          <span>{key}</span>
          <strong>{value ? "pass" : "fail"}</strong>
        </div>
      ))}
    </div>
  );
}

function toRunVerdict(run, selectedRunSummary, finalResult, watchdog) {
  if (!run && !selectedRunSummary) return "UNKNOWN";
  const canonicalStatus = normalizeStatus(finalResult.final_run_verdict?.overall_status);
  if (canonicalStatus === "pass") return "PASS";
  if (canonicalStatus === "fail") return "FAIL";
  if (canonicalStatus === "partial") return "NEEDS REPAIR";
  const status = normalizeStatus(run?.overall_status || selectedRunSummary?.overall_status);
  const hasFailedAgent = (selectedRunSummary?.failed_agent_count || run?.failed_agent_count || 0) > 0;
  const hasSemanticFailure = (finalResult.semantic_expectations || []).some((item) => item.status === "failed");
  const domainFailed = finalResult.domain_verdict?.enabled && finalResult.domain_verdict?.status !== "pass";
  const watchdogStatus = normalizeStatus(watchdog?.watchdog_status);
  if (domainFailed) return "FAIL";
  if (status === "fail") return "FAIL";
  if (hasFailedAgent || hasSemanticFailure || watchdogStatus === "escalated") return "NEEDS REPAIR";
  if (status === "pass") return "PASS";
  if ((selectedRunSummary?.running_agent_count || run?.running_agent_count || 0) > 0) return "RUNNING";
  return "UNKNOWN";
}

function firstSemanticFailure(finalResult) {
  return (finalResult.semantic_expectations || []).find((item) => item.status === "failed") || null;
}

function buildTrustGates(finalResult, watchdog, packagePreflight, providerDiagnostic) {
  const artifactChecks = finalResult.artifact_checks || {};
  const artifactValues = Object.values(artifactChecks);
  const artifactPassed =
    typeof finalResult.all_passed === "boolean"
      ? finalResult.all_passed
      : artifactValues.length > 0
        ? artifactValues.every(Boolean)
        : finalResult.artifact_score === 1 || finalResult.overall_status === "pass";
  const semanticItems = finalResult.semantic_expectations || [];
  const semanticFailed = semanticItems.filter((item) => item.status === "failed");
  const schemaContext = finalResult.schema_context || {};
  const watchdogStatus = normalizeStatus(watchdog?.watchdog_status || "not-run");
  const domainVerdict = finalResult.domain_verdict || {};
  const providerStatus = providerDiagnostic?.classification
    ? providerDiagnostic.classification === "DIRECT_COMPLETION_PASSED"
      ? "pass"
      : providerDiagnostic.classification === "MODEL_VISIBLE_BUT_COMPLETION_FAILED"
        ? "fail"
        : "warning"
    : "unknown";
  return [
    {
      key: "package",
      label: "Package",
      status: packagePreflight.package_integrity ? (packagePreflight.package_integrity === "pass" ? "pass" : "fail") : "unknown",
      detail: packagePreflight.package_integrity === "pass"
        ? packagePreflight.release_id || "verified"
        : packagePreflight.missing_files?.[0] || packagePreflight.hash_mismatches?.[0]?.path || "integrity unavailable",
    },
    {
      key: "artifact",
      label: "Artifact",
      status: artifactPassed ? "pass" : "fail",
      detail: artifactValues.length ? `${artifactValues.filter(Boolean).length}/${artifactValues.length} checks` : `score ${finalResult.artifact_score ?? "n/a"}`,
    },
    {
      key: "semantic",
      label: "Semantic",
      status: semanticFailed.length === 0 ? "pass" : "fail",
      detail: semanticItems.length ? `${semanticItems.length - semanticFailed.length}/${semanticItems.length} expectations` : "no semantic checks",
    },
    {
      key: "schema",
      label: "Schema",
      status: schemaContext.schema_context_available ? "pass" : "unknown",
      detail: schemaContext.schema_context_available ? `${schemaContext.source_count || 0} source(s)` : "not provided",
    },
    {
      key: "domain",
      label: "Domain",
      status: !domainVerdict.enabled ? "unknown" : domainVerdict.status === "pass" ? "pass" : "fail",
      detail: !domainVerdict.enabled
        ? "not configured"
        : `${domainVerdict.checks?.length - domainVerdict.defects?.length || 0}/${domainVerdict.checks?.length || 0} runner checks`,
    },
    {
      key: "watchdog",
      label: "Watchdog",
      status: watchdogStatus === "healthy" ? "pass" : watchdogStatus === "escalated" ? "fail" : "unknown",
      detail: watchdog?.last_action || watchdog?.watchdog_status || "not-run",
    },
    {
      key: "provider",
      label: "Provider",
      status: providerStatus,
      detail: providerDiagnostic?.classification || "not probed",
    },
  ];
}

function buildNextAction(verdict, finalResult, watchdog, packagePreflight, providerDiagnostic) {
  if (packagePreflight.package_integrity && packagePreflight.package_integrity !== "pass") {
    const target = packagePreflight.missing_files?.[0] || packagePreflight.hash_mismatches?.[0]?.path;
    return target
      ? `Repair package integrity first: ${target}. Do not rerun model agents yet.`
      : "Run strict package verification before rerunning model agents.";
  }
  const canonical = finalResult.final_run_verdict || {};
  if (canonical.next_action) return canonical.next_action;
  const domainVerdict = finalResult.domain_verdict || {};
  if (domainVerdict.enabled && domainVerdict.status !== "pass") {
    const defect = domainVerdict.defects?.[0] || {};
    const target = [defect.artifact, defect.path].filter(Boolean).join(":");
    const reason = defect.reason || defect.kind || "runner-owned domain validation failed";
    return `Fix domain result${target ? ` at ${target}` : ""}: ${reason}. Do not trust model-reported KPI values.`;
  }
  const semanticFailure = firstSemanticFailure(finalResult);
  if (semanticFailure) {
    return `Fix ${semanticFailure.artifact}:${semanticFailure.path}. Expected ${JSON.stringify(semanticFailure.expected)}, got ${JSON.stringify(semanticFailure.actual)}.`;
  }
  if (normalizeStatus(watchdog?.watchdog_status) === "escalated") {
    return watchdog.last_action || "Review watchdog escalation and rerun the failed task with a narrower scope.";
  }
  if (providerDiagnostic?.classification === "MODEL_VISIBLE_BUT_COMPLETION_TIMEOUT") {
    return providerDiagnostic.next_action || "Direct provider completion timed out; validate the router/Claude live path before changing model settings.";
  }
  const artifactChecks = finalResult.artifact_checks || {};
  const failedArtifactCheck = Object.entries(artifactChecks).find(([, value]) => !value);
  if (failedArtifactCheck) {
    return `Fix artifact gate: ${failedArtifactCheck[0]} failed.`;
  }
  const schemaContext = finalResult.schema_context || {};
  if ((finalResult.semantic_expectations || []).length > 0 && !schemaContext.schema_context_available) {
    return "Rerun with seed/schema context enabled so repair feedback can reference real input fields.";
  }
  if (verdict === "PASS") return "Runner-owned gates passed. Review Evidence before publishing.";
  if (verdict === "RUNNING") return "Wait for the current agent step to finish, then refresh.";
  return "Select a run or inspect Debug for raw artifacts.";
}

function buildAgentFlow(run, finalResult, watchdog) {
  const board = run?.agent_state_board || {};
  const allAgents = Object.values(board).flat();
  const roleState = (roles) => {
    const found = allAgents.find((item) => roles.includes(item.role) || roles.includes(item.owner_role) || roles.includes(item.agent_profile));
    return found?.state || "idle";
  };
  const semanticFailure = firstSemanticFailure(finalResult);
  return [
    { label: "Planner", status: run?.meeting_status ? "done" : "idle", note: run?.meeting_status || "no meeting" },
    {
      label: "Model",
      status: run?.live_degraded ? "failed" : run?.live_meeting_used ? "done" : run?.execution_jobs_run ? "done" : roleState(["research_agent", "synthesis_agent", "local_action_executor"]),
      note: run?.live_meeting_used
        ? `${run?.meeting?.live_turn_count || 0} live turn(s) via ${run?.live_transport || run?.meeting?.live_transport || "unknown"}`
        : run?.live_degraded
          ? run?.degrade_category || run?.meeting?.degrade_category || "live degraded"
          : `${run?.execution_jobs_run || 0} job(s)`,
    },
    { label: "Executor", status: semanticFailure ? "failed" : finalResult.all_passed === false ? "failed" : "done", note: semanticFailure ? "semantic mismatch" : "artifact checks" },
    { label: "Reviewer", status: run?.review_verdicts?.length ? "done" : roleState(["reviewer_worker", "risk_reviewer"]), note: `${run?.review_verdicts?.length || 0} verdict(s)` },
    { label: "Dashboard", status: normalizeStatus(watchdog?.watchdog_status) === "escalated" ? "failed" : "done", note: watchdog?.watchdog_status || "monitoring" },
  ];
}

function GateCard({ gate }) {
  return (
    <article className={`gate-card gate-${normalizeStatus(gate.status)}`}>
      <span>{gate.label}</span>
      <strong>{gate.status}</strong>
      <p>{gate.detail}</p>
    </article>
  );
}

function FlowStep({ step, isLast }) {
  return (
    <div className="flow-step-wrap">
      <article className={`flow-step flow-${normalizeStatus(step.status)}`}>
        <strong>{step.label}</strong>
        <StatusPill value={step.status} />
        <p>{step.note}</p>
      </article>
      {!isLast ? <span className="flow-connector" aria-hidden="true" /> : null}
    </div>
  );
}

export default function App() {
  const [monitor, setMonitor] = useState(emptyMonitor);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedRun, setSelectedRun] = useState(null);
  const [monitorLoading, setMonitorLoading] = useState(true);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [monitorError, setMonitorError] = useState("");
  const [runDetailError, setRunDetailError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");
  const [activeTab, setActiveTab] = useState("agents");
  const [chatPrompt, setChatPrompt] = useState("");
  const [chatSessionId, setChatSessionId] = useState("");
  const [chatMessages, setChatMessages] = useState([]);
  const [chatEvents, setChatEvents] = useState([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState("");
  const [uiConfig, setUiConfig] = useState({ show_progress_bar: true, show_agent_logs: true, show_artifacts: true, show_chat: true });

  async function loadSession(sessionId) {
    if (!sessionId) return;
    const response = await fetch(`${API_BASE}/api/v1/dashboard/${sessionId}`);
    if (!response.ok) throw new Error(`Session load failed: ${response.status}`);
    const data = await response.json();
    setChatMessages(data.messages || []);
    setChatEvents(data.events || []);
  }

  async function sendChat(event) {
    event.preventDefault();
    if (!chatPrompt.trim() || chatLoading) return;
    setChatLoading(true);
    setChatError("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: chatPrompt.trim(), session_id: chatSessionId || null }),
      });
      if (!response.ok) throw new Error(`Chat failed: ${response.status}`);
      const data = await response.json();
      setChatSessionId(data.session_id);
      setChatPrompt("");
      await loadSession(data.session_id);
    } catch (err) {
      setChatError(err.message || "Chat failed");
    } finally {
      setChatLoading(false);
    }
  }

  async function loadMonitor() {
    try {
      const response = await fetch(`${API_BASE}/api/ai-company-monitor`);
      if (!response.ok) throw new Error(`Monitor load failed: ${response.status}`);
      const data = await response.json();
      setMonitor(data);
      setSelectedRunId((current) => current || data.selected_run_preview?.run_id || data.latest_run?.run_id || "");
      setMonitorError("");
      setLastUpdated(new Date().toISOString());
    } catch (err) {
      setMonitorError(err.message || "Failed to load monitor");
    } finally {
      setMonitorLoading(false);
    }
  }

  async function loadRunDetail(runId) {
    if (!runId) return;
    setRunDetailLoading(true);
    setRunDetailError("");
    setSelectedRun(null);
    try {
      const response = await fetch(`${API_BASE}/api/ai-company-monitor/runs/${runId}`);
      if (!response.ok) throw new Error(`Run detail load failed: ${response.status}`);
      const data = await response.json();
      setSelectedRun(data);
    } catch (err) {
      setRunDetailError(err.message || "Failed to load run detail");
    } finally {
      setRunDetailLoading(false);
    }
  }

  useEffect(() => {
    loadMonitor();
    fetch(`${API_BASE}/api/v1/dashboard/config`).then((response) => response.ok ? response.json() : null).then((data) => data && setUiConfig(data)).catch(() => {});
    const timer = window.setInterval(loadMonitor, 15000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (selectedRunId) loadRunDetail(selectedRunId);
  }, [selectedRunId]);

  const hasExplicitSelection = Boolean(selectedRunId);
  const run = hasExplicitSelection ? selectedRun : monitor.latest_run;
  const selectedRunSummary = hasExplicitSelection ? selectedRun?.selected_run_summary || null : monitor.selected_run_preview;
  const allRunsSummary = monitor.all_runs_summary || emptyMonitor.all_runs_summary;
  const overview = monitor.overview || emptyMonitor.overview;
  const finalResult = run?.final_result || {};
  const alerts = run?.alerts || [];
  const board = run?.agent_state_board || {};
  const failuresByAgent = run?.failures_by_agent || [];
  const failureSummary = run?.failure_summary || {};
  const watchdog = run?.watchdog || {};
  const providerDiagnostic = run?.provider_diagnostic || finalResult.provider_diagnostic || {};
  const packagePreflight = run?.package_preflight || {};
  const tokenSummary = run?.token_summary || {};
  const claimLedger = run?.claim_ledger || {};
  const claimMetrics = run?.claim_ledger_metrics || claimLedger.metrics || {};
  const claims = claimLedger.claims || [];
  const agentProfiles = run?.agent_profiles || [];
  const policyViolations = run?.policy_violations || [];
  const downgradeSummary = run?.downgrade_summary || [];
  const recentFailureTrend = run?.recent_agent_failure_trend || [];
  const reliabilitySnapshot = run?.agent_reliability_snapshot || [];
  const unknownRuns = allRunsSummary.unknown_runs || [];

  const allRunsMetrics = useMemo(
    () => [
      { label: "Total", value: allRunsSummary.total_runs },
      { label: "Pass", value: allRunsSummary.pass_count },
      { label: "Fail", value: allRunsSummary.fail_count },
      { label: "Unknown", value: allRunsSummary.unknown_count },
    ],
    [allRunsSummary],
  );

  const currentRunMetrics = useMemo(
    () => [
      { label: "Roster", value: selectedRunSummary?.roster_count },
      { label: "Running", value: selectedRunSummary?.running_agent_count },
      { label: "Waiting", value: selectedRunSummary?.waiting_agent_count },
      { label: "Done", value: selectedRunSummary?.done_agent_count },
      { label: "Failed", value: selectedRunSummary?.failed_agent_count },
      { label: "Idle", value: selectedRunSummary?.idle_agent_count },
    ],
    [selectedRunSummary],
  );

  const boardSummary = useMemo(
    () =>
      BOARD_STATES.map((state) => ({
        state,
        title: stateTitle(state),
        items: board[state] || [],
      })),
    [board],
  );

  const trustCheckCount = Object.keys(finalResult.artifact_checks || {}).length;
  const trustPassCount = Object.values(finalResult.artifact_checks || {}).filter(Boolean).length;
  const currentRunStatusText = selectedRunSummary
    ? `${selectedRunSummary.running_agent_count} running / ${selectedRunSummary.waiting_agent_count} waiting / ${selectedRunSummary.done_agent_count} done / ${selectedRunSummary.failed_agent_count} failed`
    : "Run detail unavailable";
  const noRuns = !monitorLoading && (monitor.recent_runs || []).length === 0;
  const runVerdict = toRunVerdict(run, selectedRunSummary, finalResult, watchdog);
  const trustGates = buildTrustGates(finalResult, watchdog, packagePreflight, providerDiagnostic);
  const nextAction = buildNextAction(runVerdict, finalResult, watchdog, packagePreflight, providerDiagnostic);
  const agentFlow = buildAgentFlow(run, finalResult, watchdog);

  return (
    <main
      className="dashboard-shell"
      data-show-progress={String(uiConfig.show_progress_bar)}
      data-show-agent-logs={String(uiConfig.show_agent_logs)}
      data-show-artifacts={String(uiConfig.show_artifacts)}
    >
      <header className="topbar">
        <div className="topbar-title">
          <p className="eyebrow">AI Company</p>
          <h1>{run?.spec_id || "Mission Control"}</h1>
          <p className="subcopy">{run?.goal || "Monitor bounded multi-agent runs, trust checks, profile governance, and recovery signals."}</p>
        </div>

        <div className="topbar-actions">
          <label className="run-picker">
            <span>Run</span>
            <select value={selectedRunId} onChange={(event) => setSelectedRunId(event.target.value)} disabled={noRuns}>
              {noRuns ? <option>No runs yet</option> : null}
              {(monitor.recent_runs || []).map((item) => (
                <option key={item.run_id} value={item.run_id}>
                  {item.run_id}
                </option>
              ))}
            </select>
          </label>

          <button type="button" className="refresh-button" onClick={loadMonitor}>
            {monitorLoading ? "Loading" : "Refresh"}
          </button>
          <span className="last-updated">Updated {lastUpdated ? formatDate(lastUpdated) : "n/a"}</span>
        </div>
      </header>

      {monitorError ? <p className="error-banner">{monitorError}</p> : null}
      {runDetailError ? <p className="error-banner">{runDetailError}</p> : null}

      {uiConfig.show_chat ? <details className="surface session-console">
        <summary>Main Agent Chat &amp; Session Events</summary>
        <div className="session-toolbar">
          <label>
            Session ID
            <input value={chatSessionId} onChange={(event) => setChatSessionId(event.target.value)} placeholder="New session if blank" />
          </label>
          <button type="button" onClick={() => loadSession(chatSessionId).catch((err) => setChatError(err.message))} disabled={!chatSessionId}>Load session</button>
        </div>
        <div className="session-grid">
          <section>
            <h3>Explicit messages</h3>
            <div className="session-feed">
              {chatMessages.length ? chatMessages.map((message, index) => <article key={`${message.created_at}-${index}`}><strong>{message.role}</strong><p>{message.content}</p></article>) : <p>No messages yet.</p>}
            </div>
            <form className="chat-form" onSubmit={sendChat}>
              <textarea value={chatPrompt} onChange={(event) => setChatPrompt(event.target.value)} placeholder="Send a task to the main Claude agent" rows="4" />
              <button type="submit" disabled={chatLoading || !chatPrompt.trim()}>{chatLoading ? "Running" : "Send"}</button>
            </form>
            {chatError ? <p className="error-banner">{chatError}</p> : null}
          </section>
          <section>
            <h3>Framework events</h3>
            <div className="session-feed">
              {chatEvents.length ? chatEvents.map((item, index) => <article key={`${item.created_at}-${index}`}><strong>{item.event_type} · {item.agent_role}</strong><p>{JSON.stringify(item.payload)}</p></article>) : <p>No explicit events yet.</p>}
            </div>
            <p className="subcopy">Only explicit messages, status, tools and artifacts are recorded. Hidden reasoning is never collected.</p>
          </section>
        </div>
      </details> : null}

      {noRuns ? (
        <section className="empty-state surface">
          <SectionHeader title="No runs yet" meta="Generate a compatible ai-company run to populate this dashboard." />
        </section>
      ) : null}

      <section className={`operator-overview verdict-${normalizeStatus(runVerdict)}`}>
        <article className="surface verdict-card">
          <p className="eyebrow">Run Verdict</p>
          <div className="verdict-word">{runVerdict}</div>
          <p className="verdict-run">{run?.run_id || selectedRunSummary?.run_id || "No run selected"}</p>
          <div className="verdict-kpis">
            <MetricTile label="Semantic" value={`${finalResult.semantic_expectations?.filter((item) => item.status === "passed").length || 0}/${finalResult.semantic_expectations?.length || 0}`} />
            <MetricTile label="Repairs" value={run?.kpis?.repair_rounds_used ?? "n/a"} />
            <MetricTile label="Profile" value={run?.run_profile_mode || "n/a"} />
          </div>
        </article>

        <article className="surface next-action-card">
          <SectionHeader title="Next Action" status={runVerdict} />
          <p>{nextAction}</p>
          <div className="mini-kpi-strip">
            <span>Total {allRunsSummary.total_runs}</span>
            <span>Pass {allRunsSummary.pass_count}</span>
            <span>Fail {allRunsSummary.fail_count}</span>
            <span>Unknown {allRunsSummary.unknown_count}</span>
          </div>
        </article>

        <article className="surface trust-gates-card">
          <SectionHeader title="Trust Gates" meta="Package / Artifact / Semantic / Schema / Domain / Watchdog" />
          <div className="gate-grid">
            {trustGates.map((gate) => (
              <GateCard key={gate.key} gate={gate} />
            ))}
          </div>
        </article>

        <article className="surface agent-flow-card">
          <SectionHeader title="Agent Flow" meta="Planner -> Model -> Executor -> Reviewer -> Dashboard" />
          <div className="agent-flow">
            {agentFlow.map((step, index) => (
              <FlowStep key={step.label} step={step} isLast={index === agentFlow.length - 1} />
            ))}
          </div>
        </article>
      </section>

      {run?.goal_dag?.plan?.jobs?.length ? (
        <section className="surface details-section">
          <SectionHeader title="Goal DAG" meta="Canonical dependency and recovery state" status={runVerdict} />
          <div className="agent-flow">
            {run.goal_dag.plan.jobs.map((job, index) => {
              const state = run.goal_dag.dependency_state?.states?.[job.id]?.state || "waiting";
              return <FlowStep key={job.id} step={{ label: `${job.id} / ${job.capability}`, status: state, note: `${(job.depends_on || []).join(", ") || "root"} -> ${(job.outputs || []).join(", ")}` }} isLast={index === run.goal_dag.plan.jobs.length - 1} />;
            })}
          </div>
          <div className="debug-grid">
            <article><h3>Root cause</h3><p>{run.final_run_verdict?.root_failed_job || "none"} {run.final_run_verdict?.failure_category || ""}</p></article>
            <article><h3>Blocked descendants</h3><p>{(run.final_run_verdict?.blocked_descendants || []).join(", ") || "none"}</p></article>
            <article><h3>Recovery</h3><p>{run.final_run_verdict?.recovery_action || "No recovery required"}</p></article>
          </div>
        </section>
      ) : null}

      <section className="summary-grid">
        <article className="surface summary-panel">
          <SectionHeader title="All runs" meta={`${allRunsSummary.pass_count} pass / ${allRunsSummary.fail_count} fail / ${allRunsSummary.unknown_count} unknown`} />
          <div className="metric-grid">
            {allRunsMetrics.map((item) => (
              <MetricTile key={item.label} label={item.label} value={item.value} />
            ))}
          </div>
          {allRunsSummary.unknown_count > 0 ? (
            <p className="inline-warning">{allRunsSummary.unknown_count} run has unresolved status. Check Unknown runs below.</p>
          ) : (
            <p className="muted">Historical totals use one consistent summary source.</p>
          )}
        </article>

        <article className="surface summary-panel">
          <SectionHeader title="Current run" meta={currentRunStatusText} status={selectedRunSummary?.overall_status} />
          {runDetailLoading ? <p className="unavailable-state">Loading selected run...</p> : null}
          {!runDetailLoading && runDetailError ? <p className="unavailable-state">Run detail unavailable.</p> : null}
          {!runDetailLoading && !runDetailError && !selectedRunSummary ? <p className="unavailable-state">No selected run is available yet.</p> : null}
          {!runDetailLoading && !runDetailError && selectedRunSummary ? (
            <>
              <div className="metric-grid metric-grid-six">
                {currentRunMetrics.map((item) => (
                  <MetricTile key={item.label} label={item.label} value={item.value} />
                ))}
              </div>
              <p className="muted">Mode: {run?.run_profile_mode || "unknown"}</p>
            </>
          ) : null}
        </article>
      </section>

      <section className="health-grid">
        <article className="surface run-health">
          <SectionHeader title="Run health" status={run?.overall_status || overview.latest_status} />
          {run ? (
            <>
              <p className="decision-text">{run.decision_summary || "No decision summary has been recorded yet."}</p>
              <div className="info-grid">
                <MetricTile label="Started" value={formatDate(run.started_at)} />
                <MetricTile label="Meeting" value={run.meeting_status || "n/a"} />
                <MetricTile label="Package" value={packagePreflight.package_integrity || run.package_integrity || "n/a"} />
                <MetricTile label="Release" value={packagePreflight.release_id || run.release_id || "n/a"} />
                <MetricTile label="Meeting Mode" value={run.meeting_mode || run.meeting?.meeting_mode || "deterministic"} />
                <MetricTile label="Meeting Transport" value={run.live_transport || run.meeting?.live_transport || "n/a"} />
                <MetricTile label="Meeting Fallback" value={(run.live_degraded || run.meeting?.live_degraded) ? "degraded" : "normal"} />
                <MetricTile label="Profile" value={run.run_profile_mode || "n/a"} />
                <MetricTile label="Artifact" value={finalResult.artifact_score ?? "n/a"} />
                <MetricTile label="Watchdog" value={watchdog.watchdog_status || "not-run"} />
                <MetricTile label="Provider Diagnostic" value={providerDiagnostic.classification || "not-run"} />
                <MetricTile label="Token Total" value={formatTokens(tokenSummary.total_estimated_agent_tokens ?? run.total_estimated_agent_tokens)} />
                <MetricTile label="Provider Tokens" value={formatTokens(tokenSummary.total_provider_agent_tokens)} />
                <MetricTile label="Top Token Agent" value={tokenSummary.top_token_agent || run.top_token_agent || "n/a"} />
                <MetricTile label="Token Risk" value={tokenSummary.overflow_risk_agent_count ?? run.overflow_risk_agent_count ?? "n/a"} />
              </div>
            </>
          ) : (
            <p className="unavailable-state">Loading selected run details before showing run health.</p>
          )}
        </article>

        <article className="surface alerts-panel">
          <SectionHeader title="Alerts" meta={`${alerts.length} signal${alerts.length === 1 ? "" : "s"}`} />
          <div className="alert-stack">
            {alerts.length === 0 ? <p className="muted">No alerts at the moment.</p> : null}
            {alerts.map((alert) => (
              <article key={`${alert.type}-${alert.title}`} className={`alert-item alert-${alert.severity}`}>
                <strong>{alert.title}</strong>
                <p>{alert.detail}</p>
              </article>
            ))}
          </div>
        </article>
      </section>

      <section className="surface board-section">
        <SectionHeader title="Agent board" meta={currentRunStatusText} />
        {runDetailLoading ? <p className="unavailable-state">Loading selected run agent states...</p> : null}
        {!runDetailLoading && !run ? <p className="unavailable-state">Run detail unavailable.</p> : null}
        {!runDetailLoading && run ? (
          <div className="board-grid">
            {boardSummary.map((lane) => (
              <section key={lane.state} className={`board-lane lane-${lane.state}`}>
                <div className="lane-header">
                  <h3>{lane.title}</h3>
                  <span>{lane.items.length}</span>
                </div>
                {lane.items.length === 0 ? <p className="lane-empty">None</p> : null}
                <div className="lane-items">
                  {lane.items.map((item) => (
                    <AgentCard key={`${lane.state}-${item.role}-${item.task_id || "none"}`} item={item} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : null}
      </section>

      <section className="work-grid">
        <article className="surface trust-panel">
          <SectionHeader title="Result trustworthiness" meta={`${trustPassCount}/${trustCheckCount} checks`} />
          <pre className="summary-box">{finalResult.summary_markdown || "No summary has been generated yet."}</pre>
          <TrustChecks checks={finalResult.artifact_checks} />
        </article>

        <article className="surface">
          <SectionHeader title="Failure snapshot" meta="Current run by family" />
          <div className="failure-summary-grid">
            {Object.entries(failureSummary).map(([family, count]) => (
              <div key={family} className={`failure-family-card ${count > 0 ? "failure-family-hot" : ""}`}>
                <span>{family}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
        </article>

        <article className="surface">
          <SectionHeader title="Watchdog" status={watchdog.watchdog_status || "not-run"} />
          <div className="info-grid info-grid-compact">
            <MetricTile label="Last check" value={watchdog.last_check_at ? formatDate(watchdog.last_check_at) : "n/a"} />
            <MetricTile label="Last action" value={watchdog.last_action || "n/a"} />
            <MetricTile label="Stale tasks" value={watchdog.stale_task_count ?? "n/a"} />
            <MetricTile label="Repairs" value={watchdog.repair_attempts_used ?? "n/a"} />
          </div>
        </article>
      </section>

      <section className="surface sensemaking-tabs">
        <div className="tab-header">
          <SectionHeader title="Run Sensemaking" meta="Agents / Evidence / Debug" />
          <div className="tab-buttons" role="tablist" aria-label="Run sensemaking tabs">
            {["agents", "evidence", "debug"].map((tab) => (
              <button
                key={tab}
                type="button"
                className={activeTab === tab ? "tab-button tab-button-active" : "tab-button"}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {activeTab === "agents" ? (
          <div className="tab-panel">
            {boardSummary.flatMap((lane) => lane.items).length === 0 ? <p className="muted">No agent activity has been recorded.</p> : null}
            <div className="agent-table-grid">
              {boardSummary.flatMap((lane) =>
                lane.items.map((item) => (
                  <article key={`tab-${lane.state}-${item.role}-${item.task_id || "none"}`} className="agent-row-card">
                    <div>
                      <strong>{item.role}</strong>
                      <p>{item.verification_note || item.fallback_plan || "No note recorded."}</p>
                    </div>
                    <StatusPill value={item.state} />
                    <span>{item.agent_profile || "no profile"}</span>
                    <span>{item.task_id || "n/a"}</span>
                  </article>
                )),
              )}
            </div>
          </div>
        ) : null}

        {activeTab === "evidence" ? (
          <div className="tab-panel evidence-panel">
            <p className="muted">
              Claims: {claimMetrics.claim_count ?? 0} / coverage: {claimMetrics.claim_coverage_rate ?? 0} / uncertainty gaps:{" "}
              {claimMetrics.uncertainty_gap_count ?? 0}
            </p>
            {claims.length === 0 ? <p className="muted">No claim/evidence ledger has been generated.</p> : null}
            {claims.slice(0, 8).map((claim, index) => (
              <article key={claim.claim_id || `${claim.task_id}-${index}`} className="evidence-card">
                <strong>{claim.task_id || "claim"} / {claim.confidence || "unknown"} confidence</strong>
                <p>{claim.claim}</p>
                <p>Evidence: {(claim.evidence_refs || []).map(formatEvidenceRef).filter(Boolean).join(", ") || "missing"}</p>
              </article>
            ))}
          </div>
        ) : null}

        {activeTab === "debug" ? (
          <div className="tab-panel debug-panel">
            <div className="debug-grid">
              <article>
                <h3>Artifact / Semantic</h3>
                <pre>{JSON.stringify({
                  artifact_score: finalResult.artifact_score,
                  artifact_checks: finalResult.artifact_checks,
                  semantic_expectations: finalResult.semantic_expectations,
                  schema_context: finalResult.schema_context,
                  provider_diagnostic: providerDiagnostic,
                }, null, 2)}</pre>
              </article>
              <article>
                <h3>Watchdog / Failures</h3>
                <pre>{JSON.stringify({ watchdog, failureSummary, failuresByAgent }, null, 2)}</pre>
              </article>
                  <article>
                    <h3>Meeting</h3>
                <pre>{JSON.stringify({
                  meeting_mode: run?.meeting_mode || run?.meeting?.meeting_mode,
                  live_meeting_used: run?.live_meeting_used || run?.meeting?.live_meeting_used,
                  live_turn_count: run?.meeting?.live_turn_count,
                  live_transport: run?.live_transport || run?.meeting?.live_transport,
                  live_transport_reason: run?.live_transport_reason || run?.meeting?.live_transport_reason,
                  live_degraded: run?.live_degraded || run?.meeting?.live_degraded,
                  degrade_category: run?.degrade_category || run?.meeting?.degrade_category,
                  degrade_reason: run?.degrade_reason || run?.meeting?.degrade_reason,
                  transcript_file: run?.meeting?.transcript_file,
                }, null, 2)}</pre>
              </article>
                  <article>
                    <h3>Token Ledger</h3>
                    <pre>{JSON.stringify(run?.token_ledger || {}, null, 2)}</pre>
                  </article>
                  <article>
                    <h3>KPI</h3>
                    <pre>{JSON.stringify(run?.kpis || {}, null, 2)}</pre>
                  </article>
            </div>
          </div>
        ) : null}
      </section>

      <section className="details-section">
        <DetailDisclosure title="Claims / Evidence / Confidence" open={claims.length > 0}>
          <p className="muted">
            Claims: {claimMetrics.claim_count ?? 0} / coverage: {claimMetrics.claim_coverage_rate ?? 0} / uncertainty gaps:{" "}
            {claimMetrics.uncertainty_gap_count ?? 0}
          </p>
          {claims.length === 0 ? <p className="muted">No subagent claim ledger has been generated for this run.</p> : null}
          {claims.map((claim) => (
            <article key={claim.claim_id} className="detail-item">
              <strong>{claim.task_id} / {claim.confidence || "unknown"} confidence</strong>
              <p>{claim.claim}</p>
              <p>Evidence refs: {(claim.evidence_refs || []).map(formatEvidenceRef).filter(Boolean).join(", ") || "missing"}</p>
              {claim.limitations?.length ? <p>Limitations: {claim.limitations.join("; ")}</p> : null}
              {claim.handoff_next ? <p>Handoff: {claim.handoff_next}</p> : null}
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Agent profile governance" open={policyViolations.length > 0}>
          <p className="muted">
            Run mode: {run?.run_profile_mode || "unknown"} / violations: {policyViolations.length} / downgrades: {downgradeSummary.length}
          </p>
          {agentProfiles.length === 0 ? <p className="muted">No agent profile metadata has been recorded for this run.</p> : null}
          {agentProfiles.map((profile) => (
            <article key={`${profile.task_id}-${profile.agent_profile}`} className="detail-item">
              <strong>{profile.task_id} / {profile.owner_role} / {profile.agent_profile || "missing"}</strong>
              <p>Mode: {profile.profile_mode || "n/a"}</p>
              <p>Policy: {profile.effective_policy_level || "n/a"}</p>
              <p>Tool policy: {profile.effective_tool_policy || "n/a"}</p>
              <p>Resolved from: {profile.resolved_from || "n/a"}</p>
              {profile.policy_issues?.length ? <p>Issues: {profile.policy_issues.join(", ")}</p> : null}
            </article>
          ))}
          {policyViolations.map((item) => (
            <article key={`${item.task_id}-${item.issue}`} className="detail-item">
              <strong>Policy violation: {item.issue}</strong>
              <p>Task: {item.task_id || "n/a"}</p>
              <p>Role: {item.owner_role || "n/a"}</p>
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Failures by agent" open={failuresByAgent.length > 0}>
          {failuresByAgent.length === 0 ? <p className="muted">No failed tasks in this run.</p> : null}
          {failuresByAgent.map((agent) => (
            <article key={agent.role} className="detail-item">
              <strong>{agent.role} / {agent.failure_count} failure{agent.failure_count > 1 ? "s" : ""}</strong>
              {agent.failures.map((failure) => (
                <div key={failure.task_id} className="sub-detail-block">
                  <p>Task: {failure.task_id}</p>
                  <p>Family: {failure.failure_family}</p>
                  <p>Status: {failure.status || "n/a"}</p>
                  <p>Verdict: {failure.verdict || "n/a"}</p>
                  <p>Verification note: {failure.verification_note || "n/a"}</p>
                  {failure.detected_by ? <p>Detected by: {failure.detected_by}</p> : null}
                  {failure.failure_reason ? <p>Failure reason: {failure.failure_reason}</p> : null}
                  {failure.recommended_next_action ? <p>Next action: {failure.recommended_next_action}</p> : null}
                  <p>Fallback: {fallbackText(failure.fallback_plan)}</p>
                </div>
              ))}
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Unknown runs" open={unknownRuns.length > 0}>
          {unknownRuns.length === 0 ? <p className="muted">No unresolved historical runs were found.</p> : null}
          {unknownRuns.map((item) => (
            <article key={item.run_id} className="detail-item">
              <strong>{item.run_id}</strong>
              <p>Started: {formatDate(item.started_at)}</p>
              <p>Stored status: {item.stored_status || "missing"}</p>
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Recent agent failure trend">
          {recentFailureTrend.length === 0 ? <p className="muted">No recent agent failure trend is available.</p> : null}
          {recentFailureTrend.length > 0 ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Failed runs</th>
                    <th>Total failures</th>
                    <th>Most common family</th>
                    <th>Last failed run</th>
                  </tr>
                </thead>
                <tbody>
                  {recentFailureTrend.map((row) => (
                    <tr key={row.role}>
                      <td>{row.role}</td>
                      <td>{row.failed_runs}</td>
                      <td>{row.failure_count}</td>
                      <td>{row.most_common_failure_family}</td>
                      <td>{row.last_failed_run_id || "n/a"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {reliabilitySnapshot.length > 0 ? (
            <>
              <h3>Agent reliability snapshot</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>Total assignments</th>
                      <th>Success</th>
                      <th>Fail</th>
                      <th>Most common family</th>
                      <th>Last failed run</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reliabilitySnapshot.map((row) => (
                      <tr key={row.role}>
                        <td>{row.role}</td>
                        <td>{row.total_assignments}</td>
                        <td>{row.success_count}</td>
                        <td>{row.fail_count}</td>
                        <td>{row.most_common_failure_family}</td>
                        <td>{row.last_failed_run_id || "n/a"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </DetailDisclosure>

        <DetailDisclosure title="Meeting log and task assignment">
          <h3>Discussion</h3>
          {(run?.meeting?.discussion_log || []).map((item, index) => (
            <article key={`${item.role}-${index}`} className="detail-item">
              <strong>Round {item.round} / {item.role}</strong>
              <p>{item.summary}</p>
              <ul>
                {(item.proposed_actions || []).map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </article>
          ))}

          <h3>Assigned tasks</h3>
          {(run?.meeting?.task_assignments || []).map((task) => (
            <article key={task.task_id} className="detail-item">
              <strong>{task.task_id}</strong>
              <p>Owner: {task.owner_role}</p>
              <p>Profile: {task.agent_profile || "n/a"} / {task.profile_mode || "n/a"}</p>
              <p>Scope: {renderList(task.scope)}</p>
              <p>Depends on: {renderList(task.depends_on)}</p>
              <p>Fallback: {fallbackText(task.fallback_plan)}</p>
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Prompt, raw output, and execution log">
          {(run?.status_details || []).map((item) => (
            <article key={item.id} className="detail-item">
              <strong>{item.id} / {item.owner_role} / {item.status}</strong>
              <details>
                <summary>Prompt</summary>
                <pre>{item.prompt_excerpt || "No prompt artifact."}</pre>
              </details>
              <details>
                <summary>Raw output</summary>
                <pre>{item.raw_excerpt || "No raw output artifact."}</pre>
              </details>
              <details>
                <summary>Execution log</summary>
                <pre>{item.exec_log_excerpt || "No execution log artifact."}</pre>
              </details>
            </article>
          ))}
        </DetailDisclosure>

        <DetailDisclosure title="Recent runs">
          <div className="run-list">
            {(monitor.recent_runs || []).map((item) => (
              <button
                key={item.run_id}
                type="button"
                className={`run-row ${item.run_id === selectedRunId ? "run-row-active" : ""}`}
                onClick={() => setSelectedRunId(item.run_id)}
              >
                <div>
                  <strong>{item.run_id}</strong>
                  <p>{item.goal}</p>
                </div>
                <div className="run-row-meta">
                  <StatusPill value={item.overall_status} />
                  <span>{item.roster_count} roster</span>
                  <span>{item.running_agent_count} running</span>
                  <span>{item.failed_agent_count} failed</span>
                </div>
              </button>
            ))}
          </div>
        </DetailDisclosure>
      </section>
    </main>
  );
}
