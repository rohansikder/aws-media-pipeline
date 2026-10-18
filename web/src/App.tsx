import { useCallback, useEffect, useRef, useState } from "react";
import type { ComponentType } from "react";
import {
  AlertCircle,
  ArrowRight,
  ArrowUpRight,
  Box,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Clock3,
  CloudUpload,
  Code2,
  Database,
  ExternalLink,
  FileVideo,
  Film,
  Gauge,
  GitBranch,
  Inbox,
  Info,
  Layers3,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { ActivityChart, RenditionChart } from "./Charts";
import { api, errorMessage } from "./api";
import {
  Dialog,
  Empty,
  Status,
  Toast,
  bytes,
  number,
  relative,
} from "./components";
import { JobDrawer } from "./JobDrawer";
import { UploadDialog } from "./UploadDialog";
import type { Health, Job, JobStatus, Metrics } from "./types";
const statusOptions: Array<{ value: "all" | JobStatus; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "UPLOADING", label: "Uploading" },
  { value: "QUEUED", label: "Queued" },
  { value: "RUNNING", label: "Processing" },
  { value: "SUCCEEDED", label: "Completed" },
  { value: "FAILED", label: "Failed" },
];
const scenarios = [
  {
    id: "duplicate",
    name: "Duplicate event",
    description:
      "Send the same source event twice. Check that one source produces one set of outputs.",
    icon: CheckCheck,
  },
  {
    id: "corrupt",
    name: "Invalid video",
    description:
      "Submit a corrupt file and follow its failure through the workflow.",
    icon: FileVideo,
  },
  {
    id: "retry",
    name: "Transient failure",
    description:
      "Trigger a recoverable processing error and observe the retry behavior.",
    icon: RotateCcw,
  },
  {
    id: "burst",
    name: "Burst of uploads",
    description:
      "Queue several videos together and watch the pipeline work through the load.",
    icon: Zap,
  },
  {
    id: "dispatch-failure",
    name: "Dispatch failure",
    description: "Force a dispatch error and inspect dead-letter recovery.",
    icon: GitBranch,
  },
];
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsLoaded, setJobsLoaded] = useState(false);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [reliabilityOpen, setReliabilityOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | JobStatus>("all");
  const [busyScenario, setBusyScenario] = useState<string | null>(null);
  const [scenarioError, setScenarioError] = useState("");
  const [toast, setToast] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const pending = useRef(false);
  const alive = useRef(true);
  const clearToast = useCallback(() => setToast(""), []);
  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true;
    setRefreshing(true);
    try {
      const results = await Promise.allSettled([
        api<Health>("/api/health"),
        api<{ jobs: Job[] }>("/api/jobs"),
        api<Metrics>("/api/metrics"),
      ]);
      if (!alive.current) return;
      const messages: string[] = [];
      if (results[0].status === "fulfilled") setHealth(results[0].value);
      else messages.push(errorMessage(results[0].reason));
      if (results[1].status === "fulfilled") {
        setJobs(results[1].value.jobs ?? []);
        setJobsLoaded(true);
      } else messages.push(errorMessage(results[1].reason));
      if (results[2].status === "fulfilled") setMetrics(results[2].value);
      else messages.push(errorMessage(results[2].reason));
      setError(messages[0] ?? "");
      if (messages.length === 0) setUpdated(new Date());
      setLoaded(true);
    } finally {
      pending.current = false;
      if (alive.current) setRefreshing(false);
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [refresh]);
  async function runScenario(id: string) {
    setBusyScenario(id);
    setScenarioError("");
    try {
      const result = await api<{ message: string; jobIds: string[] }>(
        "/api/scenarios",
        { method: "POST", body: JSON.stringify({ scenario: id }) },
      );
      setToast(result.message);
      void refresh();
    } catch (cause) {
      setScenarioError(errorMessage(cause));
    } finally {
      setBusyScenario(null);
    }
  }
  async function redrive() {
    setBusyScenario("redrive");
    setScenarioError("");
    try {
      const result = await api<{ redriven: number; message?: string }>(
        "/api/dlq/redrive",
        { method: "POST" },
      );
      setToast(
        `${result.redriven} ${result.redriven === 1 ? "message" : "messages"} redriven. ${result.message ?? ""}`,
      );
      void refresh();
    } catch (cause) {
      setScenarioError(errorMessage(cause));
    } finally {
      setBusyScenario(null);
    }
  }
  const local = health
    ? /local|emulat|docker|development/i.test(health.environment)
    : true;
  const filtered = [...jobs]
    .filter(
      (job) =>
        (status === "all" || job.status === status) &&
        `${job.filename} ${job.jobId}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .sort(
      (a, b) =>
        new Date(b.createdAt).valueOf() - new Date(a.createdAt).valueOf(),
    );
  const counts = {
    uploading: jobs.filter((job) => job.status === "UPLOADING").length,
    queued: jobs.filter((job) => job.status === "QUEUED").length,
    running: jobs.filter((job) => job.status === "RUNNING").length,
    completed: jobs.filter((job) => job.status === "SUCCEEDED").length,
  };
  const isOnline = !!health && !error;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="wordmark" href="#" aria-label="Framework home">
            <span className="logo-mark">
              <i />
              <i />
            </span>
            FRAMEWORK
            <span className="wordmark-divider" />
            <span className="wordmark-sub">Media Pipeline</span>
          </a>
          <nav className="main-nav" aria-label="Main navigation">
            <a href="#overview" className="active">
              Overview
            </a>
            <a href="#jobs">
              Jobs<span>{jobs.length}</span>
            </a>
            <button onClick={() => setHelpOpen(true)}>
              Architecture
              <ArrowUpRight size={12} />
            </button>
          </nav>
          <div className="header-right">
            <span className={`environment ${local ? "local" : ""}`}>
              <Box size={12} />
              {health
                ? local
                  ? "Local emulation"
                  : health.environment
                : "Connecting"}
            </span>
            <span className="avatar" title="Operations console">
              MP
            </span>
          </div>
        </div>
      </header>
      <main id="overview">
        <div className="breadcrumb">
          <span>Workspace</span>
          <ChevronRight size={12} />
          <span>Media pipeline</span>
          <span className="region">
            <span className={`live-dot ${isOnline ? "" : "offline"}`} />
            {health?.region ?? "Awaiting API"}
          </span>
        </div>
        <section className="page-heading">
          <div>
            <span className="eyebrow">EVENT-DRIVEN. FORMAT-READY.</span>
            <h1>
              Media, in motion<span>.</span>
            </h1>
            <p>One upload. Parallel processing. Every frame accounted for.</p>
          </div>
          <div className="heading-actions">
            <button
              className="button secondary"
              onClick={() => setReliabilityOpen(true)}
            >
              <ShieldCheck size={16} />
              Test resilience
            </button>
            <button
              className="button primary"
              onClick={() => setUploadOpen(true)}
            >
              <CloudUpload size={17} />
              Upload video
              <ArrowUpRight size={15} />
            </button>
          </div>
        </section>
        {error && (
          <div className="connection-notice" role="alert">
            <AlertCircle size={17} />
            <div>
              <strong>Unable to refresh the pipeline</strong>
              <span>
                {error}{" "}
                {updated
                  ? "Previously loaded results remain visible."
                  : "Start the API and AWS services, then try again."}
              </span>
            </div>
            <button
              className="text-button"
              onClick={() => void refresh()}
              disabled={refreshing}
            >
              {refreshing ? "Checking…" : "Retry"}
              <RefreshCw size={13} className={refreshing ? "spin" : ""} />
            </button>
          </div>
        )}
        <div className="overview-topline">
          <div className="section-label">
            <span className={`live-dot ${isOnline ? "" : "offline"}`} />
            {isOnline
              ? "Pipeline connected"
              : loaded
                ? "Waiting for connection"
                : "Connecting to pipeline"}
            <span className="subtle-divider">/</span>
            {local
              ? "AWS services, running locally"
              : "AWS processing environment"}
          </div>
          <button
            className="refresh-button"
            onClick={() => void refresh()}
            aria-label="Refresh pipeline data"
            disabled={refreshing}
          >
            <RefreshCw size={12} className={refreshing ? "spin" : ""} />
            {updated
              ? `Updated ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
              : "Waiting for first refresh"}
          </button>
        </div>
        <section className="metrics-grid" aria-label="Pipeline metrics">
          <Metric
            label="Total jobs"
            value={number(metrics?.totalJobs)}
            icon={Layers3}
            detail={
              metrics
                ? `${number(metrics.active)} active across the pipeline`
                : "Waiting for pipeline data"
            }
            tone="mint"
          />
          <Metric
            label="Success rate"
            value={
              metrics && metrics.succeeded + metrics.failed > 0
                ? `${number(metrics.successRate, 1)}%`
                : "—"
            }
            icon={CheckCheck}
            detail={
              metrics
                ? `${number(metrics.succeeded)} completed · ${number(metrics.failed)} failed`
                : "Completed jobs / terminal jobs"
            }
            tone="mint"
          />
          <Metric
            label="Processing throughput"
            value={number(metrics?.throughputPerMinute, 1)}
            unit="jobs/min"
            icon={Gauge}
            detail="Measured job completions per minute"
            tone="cream"
          />
          <Metric
            label="Processing latency"
            value={number(metrics?.p95Seconds, 1)}
            unit="sec"
            icon={Clock3}
            detail={
              metrics?.p50Seconds != null
                ? `p95 · p50 ${number(metrics.p50Seconds, 1)} sec · n=${number(metrics.sampleSize ?? metrics.succeeded)}`
                : "p95 duration · awaiting completed jobs"
            }
            tone="amber"
          />
        </section>
        <div className="chart-grid">
          <ActivityChart metrics={metrics} />
          <RenditionChart metrics={metrics} />
        </div>
        <section className="panel workflow-panel" aria-label="Workflow">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">UNDER THE HOOD</span>
              <h2>A clear path from source to screen.</h2>
            </div>
            <button className="text-button" onClick={() => setHelpOpen(true)}>
              Explore architecture
              <ArrowUpRight size={14} />
            </button>
          </div>
          <div className="workflow-track">
            <WorkflowNode
              icon={CloudUpload}
              title="Upload"
              service="S3 · source bucket"
              count={jobsLoaded ? counts.uploading : null}
              caption="uploading"
            />
            <WorkflowConnector />
            <WorkflowNode
              icon={Inbox}
              title="Queue"
              service="SQS · event delivery"
              count={metrics?.queueDepth ?? (jobsLoaded ? counts.queued : null)}
              caption="messages"
            />
            <WorkflowConnector />
            <WorkflowNode
              icon={Workflow}
              title="Orchestrate"
              service="Step Functions"
              count={jobsLoaded ? counts.running : null}
              caption="running"
            />
            <WorkflowConnector parallel />
            <WorkflowNode
              icon={Film}
              title="Transcode"
              service="Parallel FFmpeg workers"
              count={jobsLoaded ? counts.running : null}
              caption="active jobs"
            />
            <WorkflowConnector />
            <WorkflowNode
              icon={CheckCheck}
              title="Deliver"
              service="S3 · output bucket"
              count={jobsLoaded ? counts.completed : null}
              caption="completed"
              done
            />
          </div>
          <div className="workflow-footer">
            <span>
              <GitBranch size={12} />
              Parallel renditions · durable job state · event history
            </span>
            <button className="text-button" onClick={() => setHelpOpen(true)}>
              <Info size={12} />
              {local ? "Locally emulated AWS services" : "AWS architecture"}
            </button>
          </div>
        </section>
        <section className="jobs-section" id="jobs">
          <div className="jobs-heading">
            <div>
              <span className="eyebrow">THE WORK, IN DETAIL</span>
              <h2>
                Recent jobs <span className="count-badge">{jobs.length}</span>
              </h2>
            </div>
            <div className="job-controls">
              <label className="search-input">
                <Search size={14} />
                <input
                  type="search"
                  placeholder="Search jobs…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="Search jobs by filename or ID"
                />
                <span>⌕</span>
              </label>
              <label className="status-filter">
                <SlidersHorizontal size={13} />
                <select
                  value={status}
                  aria-label="Filter jobs by status"
                  onChange={(event) =>
                    setStatus(event.target.value as typeof status)
                  }
                >
                  {statusOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={12} />
              </label>
            </div>
          </div>
          <div className="panel jobs-table-panel">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Source file</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Outputs</th>
                    <th>Created</th>
                    <th aria-label="Details" />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((job) => (
                    <tr key={job.jobId} onClick={() => setSelected(job.jobId)}>
                      <td>
                        <button
                          className="job-source"
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelected(job.jobId);
                          }}
                        >
                          <span className="file-icon">
                            <FileVideo size={17} />
                          </span>
                          <span>
                            <strong>{job.filename}</strong>
                            <small>
                              {bytes(job.sizeBytes)}
                              <span>·</span>
                              {job.jobId.slice(0, 12)}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <Status status={job.status} />
                      </td>
                      <td className="duration-cell">
                        {job.durationSeconds != null ? (
                          `${number(job.durationSeconds, 1)}s`
                        ) : job.status === "FAILED" ? (
                          "—"
                        ) : (
                          <span className="muted">Pending</span>
                        )}
                      </td>
                      <td>
                        <span className="output-count">
                          {Object.keys(job.outputs ?? {}).length
                            ? `${Object.keys(job.outputs ?? {}).length} assets`
                            : "—"}
                        </span>
                      </td>
                      <td className="created-cell">
                        <time
                          dateTime={job.createdAt}
                          title={new Date(job.createdAt).toLocaleString()}
                        >
                          {relative(job.createdAt)}
                        </time>
                      </td>
                      <td>
                        <button
                          className="row-arrow icon-button"
                          aria-label={`View ${job.filename}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelected(job.jobId);
                          }}
                        >
                          <ArrowUpRight size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {filtered.length === 0 && (
              <Empty
                icon={
                  query || status !== "all" ? (
                    <Search size={24} />
                  ) : (
                    <FileVideo size={26} />
                  )
                }
                title={
                  query || status !== "all"
                    ? "No jobs match your filters"
                    : loaded
                      ? "A little quiet in the queue."
                      : "Connecting to your pipeline…"
                }
                description={
                  query || status !== "all"
                    ? "Try another filename or choose a different status."
                    : "Upload your first video to watch the full pipeline come to life."
                }
                action={
                  query || status !== "all" ? (
                    <button
                      className="text-button"
                      onClick={() => {
                        setQuery("");
                        setStatus("all");
                      }}
                    >
                      Clear filters
                      <ArrowRight size={14} />
                    </button>
                  ) : (
                    <button
                      className="button secondary small"
                      onClick={() => setUploadOpen(true)}
                    >
                      <CloudUpload size={14} />
                      Upload a video
                    </button>
                  )
                }
              />
            )}
            <div className="table-footer">
              <span>
                {filtered.length} of {jobs.length} jobs
              </span>
              <span>
                <span className="live-dot" />
                Automatic refresh enabled
              </span>
            </div>
          </div>
        </section>
        <section className="reliability-strip">
          <div className="shield-mark">
            <ShieldCheck size={21} />
          </div>
          <div>
            <strong>Built for the unexpected.</strong>
            <p>
              Duplicate events, transient errors, invalid sources. Put the
              failure paths to work.
            </p>
          </div>
          <div className="reliability-numbers">
            <span>
              <strong>{number(metrics?.retryCount)}</strong>retries observed
            </span>
            <span>
              <strong
                className={
                  (metrics?.deadLetterDepth ?? 0) > 0 ? "amber-text" : ""
                }
              >
                {number(metrics?.deadLetterDepth)}
              </strong>
              dead-letter messages
            </span>
          </div>
          <button
            className="button secondary small"
            onClick={() => setReliabilityOpen(true)}
          >
            Run a scenario
            <ArrowUpRight size={14} />
          </button>
        </section>
        <footer className="app-footer">
          <span>
            <span className="logo-mark tiny">
              <i />
              <i />
            </span>
            FRAMEWORK <span className="footer-divider">/</span> An event-driven
            media pipeline
          </span>
          <button onClick={() => setHelpOpen(true)}>
            S3 · SQS · Step Functions · DynamoDB
            <ExternalLink size={11} />
          </button>
        </footer>
      </main>
      {uploadOpen && (
        <UploadDialog
          onClose={() => setUploadOpen(false)}
          onUploaded={() => void refresh()}
        />
      )}{" "}
      {selected && (
        <JobDrawer
          key={selected}
          jobId={selected}
          initial={jobs.find((job) => job.jobId === selected)}
          onClose={() => setSelected(null)}
        />
      )}{" "}
      {helpOpen && (
        <ArchitectureDialog
          local={local}
          region={health?.region}
          onClose={() => setHelpOpen(false)}
        />
      )}{" "}
      {reliabilityOpen && (
        <Dialog
          label="Test pipeline resilience"
          onClose={() => {
            if (!busyScenario) setReliabilityOpen(false);
          }}
          className="reliability-dialog"
        >
          <div className="dialog-top">
            <div>
              <span className="eyebrow">EXPECT THE UNEXPECTED</span>
              <h2>Test the recovery paths.</h2>
            </div>
            <button
              className="icon-button"
              aria-label="Close resilience scenarios"
              disabled={!!busyScenario}
              onClick={() => setReliabilityOpen(false)}
            >
              <X size={20} />
            </button>
          </div>
          <p className="dialog-description">
            Each scenario submits real work to this environment. Watch its jobs
            and events to see how the system responds.
          </p>
          {health?.scenariosEnabled === false && (
            <div className="inline-warning">
              <Info size={15} />
              Scenario controls are disabled by the API in this environment.
            </div>
          )}
          {scenarioError && (
            <div className="inline-warning" role="alert">
              <AlertCircle size={15} />
              {scenarioError}
            </div>
          )}
          <div className="scenario-list">
            {scenarios.map((scenario) => (
              <button
                key={scenario.id}
                className="scenario"
                disabled={!!busyScenario || health?.scenariosEnabled === false}
                onClick={() => void runScenario(scenario.id)}
              >
                <span className="scenario-icon">
                  <scenario.icon size={19} />
                </span>
                <span>
                  <strong>{scenario.name}</strong>
                  <small>{scenario.description}</small>
                </span>
                {busyScenario === scenario.id ? (
                  <Loader2 size={17} className="spin" />
                ) : (
                  <ArrowUpRight size={17} />
                )}
              </button>
            ))}
          </div>
          <div className="redrive-box">
            <div>
              <strong>
                <Inbox size={16} />
                Dead-letter queue{" "}
                <span>{number(metrics?.deadLetterDepth)}</span>
              </strong>
              <p>
                Replay valid notifications after resolving their cause.
                Malformed messages remain for inspection.
              </p>
            </div>
            <button
              className="button secondary small"
              disabled={
                !!busyScenario ||
                health?.scenariosEnabled === false ||
                !metrics?.deadLetterDepth
              }
              onClick={() => void redrive()}
            >
              {busyScenario === "redrive" ? (
                <Loader2 size={13} className="spin" />
              ) : (
                <RotateCcw size={13} />
              )}
              Redrive
            </button>
          </div>
        </Dialog>
      )}
      {toast && <Toast message={toast} onClose={clearToast} />}
    </>
  );
}
function Metric({
  label,
  value,
  unit,
  icon: Icon,
  detail,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  icon: ComponentType<{ size?: number }>;
  detail: string;
  tone: string;
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-top">
        <span>{label}</span>
        <Icon size={16} />
      </div>
      <div className="metric-value">
        {value}
        {unit && <span>{unit}</span>}
      </div>
      <p>{detail}</p>
    </article>
  );
}
function WorkflowNode({
  icon: Icon,
  title,
  service,
  count,
  caption,
  done = false,
}: {
  icon: ComponentType<{ size?: number }>;
  title: string;
  service: string;
  count: number | null;
  caption: string;
  done?: boolean;
}) {
  return (
    <div
      className={`workflow-node ${(count ?? 0) > 0 ? "has-work" : ""} ${done ? "workflow-done" : ""}`}
    >
      <span className="workflow-icon">
        <Icon size={21} />
        {(count ?? 0) > 0 && !done && <i />}
      </span>
      <strong>{title}</strong>
      <small>{service}</small>
      <span className="workflow-count">
        <b>{number(count)}</b>
        {caption}
      </span>
    </div>
  );
}
function WorkflowConnector({ parallel = false }: { parallel?: boolean }) {
  return (
    <div
      className={`workflow-connector ${parallel ? "parallel" : ""}`}
      aria-hidden="true"
    >
      <span />
      <ChevronRight size={12} />
      {parallel && <small>fan out</small>}
    </div>
  );
}
function ArchitectureDialog({
  local,
  region,
  onClose,
}: {
  local: boolean;
  region?: string;
  onClose: () => void;
}) {
  return (
    <Dialog
      label="Pipeline architecture"
      onClose={onClose}
      className="architecture-dialog"
    >
      <div className="dialog-top">
        <div>
          <span className="eyebrow">FROM EVENT TO OUTPUT</span>
          <h2>The pipeline, unpacked.</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close architecture"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <div className="architecture-environment">
        <Box size={17} />
        <div>
          <strong>
            {local ? "Local AWS emulation" : "AWS environment"}
            <span>{region ?? "Region pending"}</span>
          </strong>
          <p>
            {local
              ? "AWS-compatible services run locally. This console shows actual jobs, artifacts and events from that environment."
              : "Jobs run against the configured AWS services. This console reads the environment’s actual metrics and artifacts."}
          </p>
        </div>
      </div>
      <ol className="architecture-steps">
        <li>
          <span>
            <CloudUpload size={18} />
          </span>
          <div>
            <h3>Direct-to-storage ingestion</h3>
            <p>
              The API creates a job and issues a presigned S3 URL. The browser
              sends the source bytes directly to storage.
            </p>
          </div>
        </li>
        <li>
          <span>
            <Inbox size={18} />
          </span>
          <div>
            <h3>Durable event delivery</h3>
            <p>
              S3 upload events reach SQS. A dispatcher starts orchestration and
              routes unrecoverable delivery failures to a dead-letter queue.
            </p>
          </div>
        </li>
        <li>
          <span>
            <Workflow size={18} />
          </span>
          <div>
            <h3>Parallel processing</h3>
            <p>
              Step Functions coordinates FFmpeg rendition workers. Processing
              creates multiple video outputs and a thumbnail.
            </p>
          </div>
        </li>
        <li>
          <span>
            <Database size={18} />
          </span>
          <div>
            <h3>Traceable job state</h3>
            <p>
              DynamoDB stores job state and event history. Outputs live in S3
              and remain available from each job’s detail view.
            </p>
          </div>
        </li>
      </ol>
      <div className="architecture-note">
        <Code2 size={17} />
        <p>
          This is an operations view of the configured environment. Local
          timings describe this machine and workload; they are not a claim about
          production AWS performance.
        </p>
      </div>
    </Dialog>
  );
}
