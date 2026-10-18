import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Check, CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";
import type { JobStatus } from "./types";
export const number = (value: number | null | undefined, decimals = 0) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en", { maximumFractionDigits: decimals }).format(
        value,
      );
export const bytes = (value: number) =>
  value < 1024 * 1024
    ? `${number(value / 1024, 1)} KB`
    : `${number(value / 1024 / 1024, 1)} MB`;
export function date(value: string, withSeconds = false) {
  const time = new Date(value);
  return Number.isNaN(time.valueOf())
    ? "—"
    : time.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        ...(withSeconds ? { second: "2-digit" } : {}),
      });
}
export function relative(value: string) {
  const delta = Math.max(0, (Date.now() - new Date(value).valueOf()) / 1000);
  if (!Number.isFinite(delta)) return "—";
  if (delta < 60) return "Just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return new Date(value).toLocaleDateString();
}
const statuses = {
  UPLOADING: { label: "Uploading", icon: Loader2 },
  QUEUED: { label: "Queued", icon: Clock3 },
  RUNNING: { label: "Processing", icon: Loader2 },
  SUCCEEDED: { label: "Completed", icon: CheckCircle2 },
  FAILED: { label: "Failed", icon: XCircle },
};
export function Status({ status }: { status: JobStatus }) {
  const config = statuses[status] ?? { label: status, icon: Clock3 };
  const Icon = config.icon;
  return (
    <span className={`status status-${status.toLowerCase()}`}>
      <Icon
        size={12}
        className={status === "RUNNING" || status === "UPLOADING" ? "spin" : ""}
      />
      {config.label}
    </span>
  );
}
export function Dialog({
  children,
  onClose,
  label,
  className = "",
}: {
  children: ReactNode;
  onClose: () => void;
  label: string;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    el?.showModal();
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      el?.close();
      document.body.style.overflow = old;
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      className={className}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const rect = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            onClose();
        }
      }}
    >
      {children}
    </dialog>
  );
}
export function Empty({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Toast({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onClose, 6500);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  return (
    <div className="toast" role="status">
      <span>
        <Check size={16} />
      </span>
      {message}
      <button
        className="text-button"
        onClick={onClose}
        aria-label="Dismiss notification"
      >
        Dismiss
      </button>
    </div>
  );
}
