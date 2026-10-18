export type JobStatus =
  "UPLOADING" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
export interface JobEvent {
  timestamp: string;
  type: string;
  message: string;
  rendition?: string;
}
export interface Job {
  jobId: string;
  filename: string;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  sizeBytes: number;
  durationSeconds?: number;
  attempts?: number;
  outputs?: Record<string, { url: string; key: string; contentType: string }>;
  events?: JobEvent[];
  error?: string;
  executionArn?: string;
  scenario?: string;
}
export interface Metrics {
  sampleSize?: number;
  historyLimit?: number;
  processingFailureDepth?: number;
  totalJobs: number;
  succeeded: number;
  failed: number;
  active: number;
  successRate: number;
  throughputPerMinute: number;
  p50Seconds: number | null;
  p95Seconds: number | null;
  queueDepth: number;
  deadLetterDepth: number;
  retryCount: number;
  series: Array<{
    timestamp: string;
    completed: number;
    failed: number;
    avgDurationSeconds: number;
  }>;
  renditions: Array<{ name: string; count: number }>;
}
export interface Health {
  status: string;
  environment: string;
  region: string;
  scenariosEnabled?: boolean;
}
