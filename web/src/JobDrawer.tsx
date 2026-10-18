import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  Clock3,
  FileVideo,
  Film,
  Hash,
  Layers3,
  Loader2,
  RotateCcw,
  X,
} from "lucide-react";
import { api, errorMessage } from "./api";
import { Dialog, Status, bytes, date, number } from "./components";
import type { Job, JobEvent } from "./types";
export function JobDrawer({
  jobId,
  initial,
  onClose,
}: {
  jobId: string;
  initial?: Job;
  onClose: () => void;
}) {
  const [job, setJob] = useState<Job | null>(initial ?? null);
  const [events, setEvents] = useState<JobEvent[]>(initial?.events ?? []);
  const urls = useRef(
    new Map<string, { url: string; at: number }>(
      Object.values(initial?.outputs ?? {}).map((output) => [
        output.key,
        { url: output.url, at: Date.now() },
      ]),
    ),
  );
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"overview" | "events">("overview");
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState("");
  async function downloadOutput(
    name: string,
    output: { url: string; key: string },
  ) {
    setDownloading(name);
    setDownloadError("");
    try {
      const response = await fetch(output.url);
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = output.key.split("/").pop() ?? name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (cause) {
      setDownloadError(
        `${errorMessage(cause)}. You can also open the asset in a new tab.`,
      );
    } finally {
      setDownloading(null);
    }
  }
  useEffect(() => {
    let active = true;
    let fetching = false;
    async function load() {
      if (fetching) return;
      fetching = true;
      try {
        const details = await api<Job>(
          `/api/jobs/${encodeURIComponent(jobId)}`,
        );
        if (!active) return;
        for (const output of Object.values(details.outputs ?? {})) {
          const cached = urls.current.get(output.key);
          if (cached && Date.now() - cached.at < 12 * 60 * 1000)
            output.url = cached.url;
          else
            urls.current.set(output.key, { url: output.url, at: Date.now() });
        }
        setJob(details);
        setError("");
        try {
          const history = await api<{ events: JobEvent[] }>(
            `/api/jobs/${encodeURIComponent(jobId)}/history`,
          );
          if (active) setEvents(history.events ?? details.events ?? []);
        } catch {
          if (active) setEvents(details.events ?? []);
        }
      } catch (cause) {
        if (active) setError(errorMessage(cause));
      } finally {
        fetching = false;
      }
    }
    void load();
    const timer = setInterval(load, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobId]);
  const outputs = Object.entries(job?.outputs ?? {});
  const poster = outputs.find(
    ([name, output]) =>
      output.contentType?.startsWith("image/") || /thumbnail/i.test(name),
  )?.[1].url;
  return (
    <Dialog label="Job details" onClose={onClose} className="job-drawer">
      <div className="drawer-header">
        <div className="drawer-icon">
          <FileVideo size={23} />
        </div>
        <button
          className="icon-button"
          aria-label="Close job details"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <div className="drawer-title">
        <span className="eyebrow">PROCESSING JOB</span>
        <h2>{job?.filename ?? "Loading job…"}</h2>
        <div className="drawer-title-meta">
          {job && <Status status={job.status} />}
          <span className="monospace">{jobId}</span>
        </div>
      </div>
      {error && (
        <div className="inline-warning" role="alert">
          <AlertCircle size={16} />
          {error}
        </div>
      )}
      <div className="drawer-tabs">
        <button
          aria-pressed={tab === "overview"}
          onClick={() => setTab("overview")}
        >
          Overview
        </button>
        <button
          aria-pressed={tab === "events"}
          onClick={() => setTab("events")}
        >
          Event timeline <span>{events.length}</span>
        </button>
      </div>
      {!job ? (
        <div className="drawer-loading">
          <Loader2 className="spin" size={24} />
          Retrieving job details…
        </div>
      ) : tab === "overview" ? (
        <>
          <div className="job-facts">
            <div>
              <Clock3 size={15} />
              <span>
                Created
                <strong>{new Date(job.createdAt).toLocaleString()}</strong>
              </span>
            </div>
            <div>
              <Layers3 size={15} />
              <span>
                Source size<strong>{bytes(job.sizeBytes)}</strong>
              </span>
            </div>
            <div>
              <Film size={15} />
              <span>
                Processing duration
                <strong>
                  {job.durationSeconds == null
                    ? "In progress"
                    : `${number(job.durationSeconds, 2)} seconds`}
                </strong>
              </span>
            </div>
            <div>
              <RotateCcw size={15} />
              <span>
                Processing attempts<strong>{job.attempts ?? "—"}</strong>
              </span>
            </div>
          </div>
          {job.error && (
            <div className="job-error">
              <AlertCircle size={18} />
              <div>
                <strong>Processing stopped</strong>
                <p>
                  {typeof job.error === "string"
                    ? job.error
                    : JSON.stringify(job.error)}
                </p>
              </div>
            </div>
          )}
          <div className="output-heading">
            <h3>Processed outputs</h3>
            <span>{outputs.length} assets</span>
          </div>
          {downloadError && (
            <div className="inline-warning" role="alert">
              {downloadError}
            </div>
          )}
          {outputs.length > 0 ? (
            <div className="output-cards">
              {outputs.map(([name, output]) => {
                const isImage =
                  output.contentType?.startsWith("image/") ||
                  /thumb|poster/i.test(name);
                return (
                  <article className="output-card" key={name}>
                    <div className="output-preview">
                      {isImage ? (
                        <img
                          src={output.url}
                          alt={`${name} generated from ${job.filename}`}
                          loading="lazy"
                        />
                      ) : (
                        <video
                          poster={poster}
                          src={output.url}
                          controls
                          playsInline
                          preload="metadata"
                          aria-label={`${name} rendition`}
                        />
                      )}
                    </div>
                    <div className="output-card-info">
                      <div>
                        <strong>{name.replace(/[_-]/g, " ")}</strong>
                        <span>{output.contentType}</span>
                      </div>
                      <div className="output-actions">
                        <a
                          href={output.url}
                          target="_blank"
                          rel="noreferrer"
                          className="icon-button"
                          aria-label={`Open ${name} in a new tab`}
                        >
                          <ArrowUpRight size={16} />
                        </a>
                        <button
                          className="icon-button"
                          disabled={downloading !== null}
                          aria-label={`Download ${name}`}
                          onClick={() => void downloadOutput(name, output)}
                        >
                          {downloading === name ? (
                            <Loader2 size={16} className="spin" />
                          ) : (
                            <ArrowDownToLine size={16} />
                          )}
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="outputs-empty">
              <Film size={28} />
              <strong>
                {job.status === "FAILED"
                  ? "No outputs to deliver"
                  : "Outputs will appear here"}
              </strong>
              <p>
                {job.status === "FAILED"
                  ? "Review the event timeline for the failure details."
                  : "Each completed rendition becomes available as the job runs."}
              </p>
              {(job.status === "RUNNING" || job.status === "QUEUED") && (
                <span className="processing-hint">
                  <Loader2 size={13} className="spin" />
                  Checking for updates
                </span>
              )}
            </div>
          )}
          {job.executionArn && (
            <details className="execution-details">
              <summary>
                <Hash size={13} /> Execution reference
              </summary>
              <code>{job.executionArn}</code>
            </details>
          )}
          <button
            className="text-button timeline-link"
            onClick={() => setTab("events")}
          >
            Follow the event timeline
            <ArrowUpRight size={15} />
          </button>
        </>
      ) : (
        <div className="event-timeline">
          {events.length === 0 ? (
            <div className="outputs-empty">
              <Clock3 size={25} />
              <strong>Waiting for the first event</strong>
              <p>
                The timeline updates as your job moves through the pipeline.
              </p>
            </div>
          ) : (
            [...events]
              .sort(
                (a, b) =>
                  new Date(a.timestamp).valueOf() -
                  new Date(b.timestamp).valueOf(),
              )
              .map((event, index) => (
                <div
                  className={`event-row ${/fail|error/i.test(event.type) ? "event-error" : ""}`}
                  key={`${event.timestamp}-${index}`}
                >
                  <span className="event-marker">
                    {/fail|error/i.test(event.type) ? (
                      <X size={11} />
                    ) : (
                      <Check size={11} />
                    )}
                  </span>
                  <div>
                    <div className="event-row-heading">
                      <strong>{event.type.replace(/[_-]/g, " ")}</strong>
                      <time dateTime={event.timestamp}>
                        {date(event.timestamp, true)}
                      </time>
                    </div>
                    <p>{event.message}</p>
                    {event.rendition && (
                      <span className="event-rendition">{event.rendition}</span>
                    )}
                  </div>
                </div>
              ))
          )}
        </div>
      )}
      <div className="drawer-footer">
        <span className="live-dot" />
        Job state refreshes every 3 seconds
      </div>
    </Dialog>
  );
}
