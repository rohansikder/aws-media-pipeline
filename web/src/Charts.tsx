import { useState } from "react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Activity, ArrowUpRight, BarChart3 } from "lucide-react";
import { Empty, number, date } from "./components";
import type { Metrics } from "./types";
const tooltipStyle = {
  background: "#24303a",
  border: "1px solid #3d4a53",
  borderRadius: 10,
  color: "#eef1eb",
  fontSize: 12,
  boxShadow: "0 12px 30px #0004",
};
export function ActivityChart({ metrics }: { metrics: Metrics | null }) {
  const [mode, setMode] = useState<"jobs" | "duration">("jobs");
  const series = metrics?.series ?? [];
  const hasData = (metrics?.totalJobs ?? 0) > 0 && series.length > 0;
  return (
    <section className="panel activity-panel" aria-label="Processing activity">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">THE BIG PICTURE</span>
          <h2>
            Processing activity <ArrowUpRight size={17} />
          </h2>
        </div>
        <div className="segmented" aria-label="Chart metric">
          <button
            aria-pressed={mode === "jobs"}
            onClick={() => setMode("jobs")}
          >
            Jobs
          </button>
          <button
            aria-pressed={mode === "duration"}
            onClick={() => setMode("duration")}
          >
            Duration
          </button>
        </div>
      </div>
      <div className="chart-meta">
        <span>
          <i className="legend-dot mint" />
          {mode === "jobs" ? "Completed jobs" : "Average processing time"}
        </span>
        {mode === "jobs" && (
          <span>
            <i className="legend-dot amber" />
            Failed jobs
          </span>
        )}
        <span className="chart-period">
          {series.length
            ? `${date(series[0].timestamp)} — ${date(series[series.length - 1].timestamp)}`
            : "Awaiting first job"}
        </span>
      </div>
      <div className="activity-chart">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <AreaChart
              data={series}
              margin={{ top: 10, right: 7, left: -23, bottom: 0 }}
            >
              <defs>
                <linearGradient id="completedFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#9ce8bb" stopOpacity={0.22} />
                  <stop offset="100%" stopColor="#9ce8bb" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="failedFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#f2b66d" stopOpacity={0.14} />
                  <stop offset="100%" stopColor="#f2b66d" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#ffffff0b" vertical={false} />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(value) => date(String(value))}
                stroke="transparent"
                tick={{ fill: "#84919c", fontSize: 10 }}
                tickLine={false}
                minTickGap={44}
              />
              <YAxis
                tick={{ fill: "#84919c", fontSize: 10 }}
                stroke="transparent"
                tickLine={false}
                allowDecimals={mode === "duration"}
                tickFormatter={(value) =>
                  mode === "duration" ? `${number(value, 1)}s` : number(value)
                }
              />
              <Tooltip
                contentStyle={tooltipStyle}
                labelFormatter={(label) => date(String(label), true)}
                formatter={(value, name) => [
                  `${number(Number(value), 1)}${mode === "duration" ? "s" : ""}`,
                  name === "completed"
                    ? "Completed"
                    : name === "failed"
                      ? "Failed"
                      : "Avg. duration",
                ]}
              />
              <Area
                type="monotone"
                dataKey={mode === "jobs" ? "completed" : "avgDurationSeconds"}
                stroke="#9ce8bb"
                strokeWidth={2}
                fill="url(#completedFill)"
                dot={series.length < 2 ? { r: 4, fill: "#9ce8bb" } : false}
                activeDot={{ r: 4, stroke: "#14201f", strokeWidth: 3 }}
                isAnimationActive={false}
              />
              {mode === "jobs" && (
                <Area
                  type="monotone"
                  dataKey="failed"
                  stroke="#f2b66d"
                  strokeWidth={1.6}
                  fill="url(#failedFill)"
                  dot={false}
                  isAnimationActive={false}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <Empty
            icon={<Activity size={23} />}
            title="Your first frame starts here."
            description="Upload a video to see real processing activity. Charts populate as jobs complete."
          />
        )}
      </div>
      <div className="chart-footnote">
        <span className="live-dot" />
        Actual pipeline events · refreshes every 3 seconds
      </div>
    </section>
  );
}
export function RenditionChart({ metrics }: { metrics: Metrics | null }) {
  const data = metrics?.renditions ?? [];
  const hasData = data.some((item) => item.count > 0);
  return (
    <section className="panel rendition-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">READY TO DELIVER</span>
          <h2>Output renditions</h2>
        </div>
        <span className="quiet-icon">
          <BarChart3 size={18} />
        </span>
      </div>
      <p className="panel-description">Completed assets by output profile.</p>
      <div className="rendition-chart">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <BarChart
              data={data}
              layout="vertical"
              margin={{ left: 0, right: 28, top: 7, bottom: 0 }}
              barSize={16}
            >
              <CartesianGrid horizontal={false} stroke="#ffffff09" />
              <XAxis type="number" hide allowDecimals={false} />
              <YAxis
                type="category"
                dataKey="name"
                axisLine={false}
                tickLine={false}
                width={82}
                tick={{ fill: "#adb7be", fontSize: 11 }}
              />
              <Tooltip
                cursor={{ fill: "#ffffff03" }}
                contentStyle={tooltipStyle}
                formatter={(value) => [number(Number(value)), "Assets"]}
              />
              <Bar
                dataKey="count"
                fill="#9ce8bb"
                radius={[0, 4, 4, 0]}
                label={{ position: "right", fill: "#c5d0d4", fontSize: 11 }}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <Empty
            icon={<BarChart3 size={23} />}
            title="No outputs yet"
            description="Rendition counts appear when processing succeeds."
          />
        )}
      </div>
      <div className="output-note">
        <span className="mini-frame" />
        One source. Multiple possibilities.
      </div>
    </section>
  );
}
