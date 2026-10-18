import { useRef, useState } from "react";
import {
  ArrowUpRight,
  CheckCircle2,
  CloudUpload,
  FileVideo,
  Loader2,
  Trash2,
  X,
} from "lucide-react";
import { Dialog, bytes } from "./components";
import { api, ApiError, errorMessage, uploadFile } from "./api";
type Item = {
  key: string;
  file: File;
  progress: number;
  state: "ready" | "uploading" | "done" | "error";
  jobId?: string;
  reservation?: {
    jobId: string;
    uploadUrl: string;
    headers: Record<string, string>;
  };
  uploaded?: boolean;
  error?: string;
};
export function UploadDialog({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const update = (key: string, patch: Partial<Item>) =>
    setItems((previous) =>
      previous.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    );
  function add(files: FileList | null) {
    if (!files || busy) return;
    const accepted = Array.from(files).filter(
      (file) =>
        /\.(mp4|mov|webm)$/i.test(file.name) &&
        file.size > 0 &&
        file.size <= 100 * 1024 * 1024,
    );
    setNotice(
      accepted.length !== files.length
        ? "Use MP4, MOV or WebM files, up to 100 MB each."
        : "",
    );
    setItems((previous) => [
      ...previous,
      ...accepted.map((file) => ({
        key: crypto.randomUUID(),
        file,
        progress: 0,
        state: "ready" as const,
      })),
    ]);
    if (input.current) input.current.value = "";
  }
  async function upload() {
    setBusy(true);
    for (const item of items.filter(
      (item) => item.state === "ready" || item.state === "error",
    )) {
      update(item.key, {
        state: "uploading",
        progress: item.uploaded ? 100 : 0,
        error: undefined,
      });
      try {
        if (item.reservation && !item.uploaded) {
          try {
            await api(
              `/api/jobs/${encodeURIComponent(item.reservation.jobId)}/confirm`,
              { method: "POST" },
            );
            update(item.key, { state: "done", uploaded: true, progress: 100 });
            onUploaded();
            continue;
          } catch (cause) {
            if (!(cause instanceof ApiError) || cause.status !== 409)
              throw cause;
          }
        }
        const contentType = /\.mov$/i.test(item.file.name)
          ? "video/quicktime"
          : /\.webm$/i.test(item.file.name)
            ? "video/webm"
            : "video/mp4";
        const prepared =
          item.reservation ??
          (await api<{
            jobId: string;
            uploadUrl: string;
            headers: Record<string, string>;
          }>("/api/uploads", {
            method: "POST",
            body: JSON.stringify({
              filename: item.file.name,
              contentType,
              sizeBytes: item.file.size,
            }),
          }));
        update(item.key, { jobId: prepared.jobId, reservation: prepared });
        if (!item.uploaded) {
          await uploadFile(
            prepared.uploadUrl,
            item.file,
            prepared.headers,
            (progress) => update(item.key, { progress }),
          );
          update(item.key, { uploaded: true, progress: 100 });
        }
        await api(`/api/jobs/${encodeURIComponent(prepared.jobId)}/confirm`, {
          method: "POST",
        });
        update(item.key, { state: "done", progress: 100 });
        onUploaded();
      } catch (error) {
        update(item.key, { state: "error", error: errorMessage(error) });
      }
    }
    setBusy(false);
  }
  const pending = items.filter(
    (item) => item.state === "ready" || item.state === "error",
  ).length;
  const done = items.filter((item) => item.state === "done").length;
  return (
    <Dialog
      label="Upload videos"
      onClose={() => {
        if (!busy) onClose();
      }}
      className="upload-dialog"
    >
      <div className="dialog-top">
        <div>
          <span className="eyebrow">INGEST A NEW SOURCE</span>
          <h2>Give your video a new format.</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close upload"
          onClick={onClose}
          disabled={busy}
        >
          <X size={20} />
        </button>
      </div>
      <p className="dialog-description">
        Files upload directly to object storage using a presigned URL. The
        pipeline takes it from there.
      </p>
      <input
        type="file"
        accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
        multiple
        ref={input}
        onChange={(event) => add(event.target.files)}
        className="file-input"
        aria-label="Choose video files"
        disabled={busy}
      />
      <button
        type="button"
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        disabled={busy}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          add(event.dataTransfer.files);
        }}
      >
        <span className="upload-illustration">
          <CloudUpload size={31} strokeWidth={1.5} />
        </span>
        <strong>Drop a video. We’ll handle the rest.</strong>
        <span>
          or <em>browse files</em> on your computer
        </span>
        <small>MP4, MOV or WebM · up to 100 MB per file</small>
      </button>
      {notice && (
        <div className="inline-warning" role="alert">
          {notice}
        </div>
      )}
      {items.length > 0 && (
        <div className="upload-items">
          {items.map((item) => (
            <div className={`upload-item ${item.state}`} key={item.key}>
              <FileVideo size={22} />
              <div className="upload-item-body">
                <strong>{item.file.name}</strong>
                <span>
                  {bytes(item.file.size)} ·{" "}
                  {item.state === "ready"
                    ? "Ready to upload"
                    : item.state === "done"
                      ? "Uploaded · processing asynchronously"
                      : item.state === "uploading"
                        ? `${item.progress}% uploaded`
                        : item.error}
                </span>
                {item.state === "uploading" && (
                  <progress
                    max={100}
                    value={item.progress}
                    aria-label={`${item.file.name} upload progress`}
                  />
                )}
              </div>
              {item.state === "done" ? (
                <CheckCircle2 size={19} />
              ) : item.state === "uploading" ? (
                <Loader2 size={19} className="spin" />
              ) : (
                <button
                  className="icon-button"
                  disabled={busy}
                  aria-label={`Remove ${item.file.name}`}
                  onClick={() =>
                    setItems((previous) =>
                      previous.filter((value) => value.key !== item.key),
                    )
                  }
                >
                  <Trash2 size={16} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="dialog-footer">
        <span>
          {busy
            ? "Keep this window open until your upload finishes."
            : done
              ? `${done} ${done === 1 ? "video" : "videos"} submitted to the pipeline.`
              : "Processing runs in the background after upload."}
        </span>
        {pending > 0 ? (
          <button className="button primary" disabled={busy} onClick={upload}>
            {busy ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <CloudUpload size={17} />
            )}{" "}
            {busy
              ? "Uploading…"
              : `Upload ${pending} ${pending === 1 ? "video" : "videos"}`}
          </button>
        ) : (
          <button
            className="button primary"
            onClick={items.length ? onClose : () => input.current?.click()}
          >
            {items.length ? "View jobs" : "Select a video"}
            <ArrowUpRight size={16} />
          </button>
        )}
      </div>
    </Dialog>
  );
}
