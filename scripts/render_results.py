"""Render publication-ready charts from a completed, real integration report.

Install the optional reporting dependencies with ``pip install -e '.[reports]'``.
The default input is build/integration-results.json; no sample data is generated.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BG, PANEL, LINE = "#111a22", "#18242e", "#2b3842"
TEXT, MUTED, MINT, AMBER, BLUE = "#e9ece7", "#9aaab7", "#a5ebbc", "#edb979", "#8bb8dc"
RENDITIONS = ("360p", "720p", "thumbnail")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Report timestamps must include their UTC offset")
    return parsed.astimezone(UTC)


def number(value, label):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return result


def emulator_label(report):
    version = report.get("environment", {}).get("emulatorVersion")
    if version:
        return f"LOCAL FLOCI {version}"
    # This is configuration provenance, not an inferred runtime measurement.
    compose = ROOT / "compose.yaml"
    match = re.search(r"image:\s*[\"']?floci/floci:([^\s\"']+)", compose.read_text())
    return f"LOCAL FLOCI · CONFIGURED IMAGE {match.group(1)}" if match else "LOCAL FLOCI"


def observations(report):
    if report.get("status") not in {"passed", "failed"} or not report.get("finishedAt"):
        raise ValueError("Wait for the integration run to finish before rendering its report")
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The integration report contains no completed cases")
    if any(case.get("status") not in {"passed", "failed"} for case in cases):
        raise ValueError("The integration report still contains an incomplete case")
    successful, jobs, seen, corrupt_rejections = [], [], set(), 0
    for case in cases:
        records = case.get("jobs", [])
        for index, job in enumerate(records, start=1):
            if job["jobId"] in seen:
                raise ValueError("A job appears more than once in the integration evidence")
            seen.add(job["jobId"])
            jobs.append(job)
            if (
                case["name"] == "corrupt"
                and case["status"] == "passed"
                and job["status"] == "FAILED"
            ):
                corrupt_rejections += 1
            if job["status"] != "SUCCEEDED":
                continue
            elapsed = (timestamp(job["completedAt"]) - timestamp(job["createdAt"])).total_seconds()
            duration = number(elapsed, "Reservation-to-completion duration")
            attempts = {}
            for rendition in RENDITIONS:
                count = number(job["attemptCounts"][rendition], "Worker attempt count")
                if not count.is_integer() or count < 1:
                    raise ValueError(
                        "A successful rendition must have a positive integer attempt count"
                    )
                attempts[rendition] = int(count)
            if set(job.get("outputs", {})) != set(RENDITIONS):
                raise ValueError("A successful job is missing verified output renditions")
            if any(
                output.get("signatureVerified") is not True for output in job["outputs"].values()
            ):
                raise ValueError("Only explicitly verified S3 output objects can be plotted")
            name = case["name"].replace("-", " ").title()
            label = f"{name} {index}" if len(records) > 1 else name
            successful.append(
                {
                    "label": f"{label} · {job['jobId'][:7]}",
                    "duration": duration,
                    "attempts": attempts,
                    "outputs": list(job["outputs"].values()),
                }
            )
    counts = Counter(job["status"] for job in jobs)
    return {
        "cases": cases,
        "successful": successful,
        "counts": counts,
        "outputs": sum(
            output.get("signatureVerified") is True
            for job in jobs
            for output in job.get("outputs", {}).values()
        ),
        "corruptRejections": corrupt_rejections,
    }


def render(report, source, output_directory):
    # Keep --help and report validation usable without optional plotting packages.
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Patch
        from matplotlib.ticker import MaxNLocator
    except ImportError as error:
        raise RuntimeError("Install reporting dependencies: pip install -e '.[reports]'") from error

    data = observations(report)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "text.color": TEXT,
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": TEXT,
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
            "savefig.facecolor": BG,
            "figure.facecolor": BG,
        }
    )
    figure = plt.figure(figsize=(15.6, 9.2), layout=None)
    completed = timestamp(report["finishedAt"]).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(case["status"] == "passed" for case in data["cases"])
    total = len(data["cases"])
    good = report["status"] == "passed"
    figure.text(0.055, 0.95, "MEDIA PIPELINE / VALIDATION", size=10, color=MINT, weight="bold")
    figure.text(0.945, 0.95, emulator_label(report), size=9, color=MUTED, ha="right")
    figure.text(0.055, 0.883, "Real events. Verified outputs.", size=28, weight="bold")
    figure.text(
        0.055,
        0.847,
        "Functional validation of the local pipeline · not an AWS performance benchmark",
        size=11.5,
        color=MUTED,
    )
    figure.text(
        0.945,
        0.885,
        "RUN PASSED" if good else "RUN HAS FAILURES",
        size=11,
        color=MINT if good else AMBER,
        weight="bold",
        ha="right",
    )
    figure.text(0.945, 0.855, completed, size=9, color=MUTED, ha="right")

    cards = [
        (f"{passed} / {total}", "CASES PASSED", MINT if passed == total else AMBER),
        (str(data["counts"]["SUCCEEDED"]), "SUCCESSFUL JOBS VERIFIED", TEXT),
        (str(data["counts"]["FAILED"]), "FAILED JOBS OBSERVED", AMBER),
        (str(data["outputs"]), "S3 OUTPUT OBJECTS VERIFIED", MINT),
    ]
    for index, (value, label, color) in enumerate(cards):
        x, width = 0.055 + index * 0.227, 0.209
        figure.add_artist(
            FancyBboxPatch(
                (x, 0.706),
                width,
                0.105,
                boxstyle="round,pad=0.008,rounding_size=0.012",
                facecolor=PANEL,
                edgecolor=LINE,
                linewidth=0.9,
                transform=figure.transFigure,
                zorder=0,
            )
        )
        figure.text(x + 0.014, 0.772, label, size=8.8, color=MUTED, weight="bold")
        figure.text(x + 0.014, 0.724, value, size=25, color=color, weight="bold")

    left = figure.add_axes((0.195, 0.24, 0.325, 0.355), facecolor=PANEL)
    right = figure.add_axes((0.625, 0.24, 0.32, 0.355), facecolor=PANEL, sharey=left)
    for axis in (left, right):
        for spine in axis.spines.values():
            spine.set_visible(False)
        axis.grid(axis="x", color=LINE, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.tick_params(axis="both", which="both", length=0, pad=9, labelsize=10)
        axis.xaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=3))
    figure.text(0.195, 0.665, "01 / SUCCESSFUL JOBS", size=9, color=MINT, weight="bold")
    figure.text(0.195, 0.628, "Reservation → completion", size=16, weight="bold")
    figure.text(0.625, 0.665, "02 / SUCCESSFUL JOBS", size=9, color=MINT, weight="bold")
    figure.text(0.625, 0.628, "Worker attempts by rendition", size=16, weight="bold")
    rows = data["successful"]
    if rows:
        y = list(range(len(rows)))
        durations = [row["duration"] for row in rows]
        maximum = max(max(durations), 0.1)
        left.barh(y, durations, height=0.56, color=MINT, zorder=3)
        left.set_yticks(y, [row["label"] for row in rows])
        left.set_ylim(len(rows) - 0.4, -0.6)
        left.set_xlim(0, maximum * 1.27)
        for index, duration in enumerate(durations):
            left.text(duration + maximum * 0.025, index, f"{duration:.2f} s", va="center", size=10)
        offsets = [0] * len(rows)
        for rendition, color in zip(RENDITIONS, (MINT, BLUE, AMBER), strict=True):
            values = [row["attempts"][rendition] for row in rows]
            right.barh(
                y,
                values,
                left=offsets,
                height=0.56,
                color=color,
                edgecolor=PANEL,
                linewidth=1.3,
                zorder=3,
            )
            for index, value in enumerate(values):
                right.text(
                    offsets[index] + value / 2,
                    index,
                    str(value),
                    ha="center",
                    va="center",
                    color=BG,
                    weight="bold",
                    size=10,
                )
            offsets = [offset + value for offset, value in zip(offsets, values, strict=True)]
        right.set_xlim(0, max(offsets) + 0.7)
        right.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
        for index, count in enumerate(offsets):
            right.text(count + 0.1, index, str(count), va="center", color=TEXT, size=10)
    else:
        for axis in (left, right):
            axis.set_yticks([])
            axis.text(
                0.5,
                0.5,
                "No successful jobs verified in this run",
                ha="center",
                va="center",
                color=MUTED,
                transform=axis.transAxes,
                wrap=True,
            )
    right.tick_params(axis="y", labelleft=False)
    left.set_xlabel("Seconds · includes upload and queue wait", labelpad=14, size=10)
    right.set_xlabel("Attempts · total shown at the end of each bar", labelpad=14, size=10)
    right.legend(
        handles=[
            Patch(facecolor=color, label=rendition)
            for rendition, color in zip(RENDITIONS, (MINT, BLUE, AMBER), strict=True)
        ],
        loc="lower left",
        bbox_to_anchor=(-0.01, -0.29),
        ncol=3,
        frameon=False,
        fontsize=10,
        handlelength=1,
        columnspacing=1.4,
        labelcolor=MUTED,
    )

    note = "Charts show successful jobs only. Counts above include observed failure scenarios."
    if data["corruptRejections"]:
        count = data["corruptRejections"]
        note = f"Corrupt-input validation rejected {count} job{'s' if count != 1 else ''} as expected; successful jobs are plotted above."
    figure.text(0.055, 0.112, note, size=10, color=MUTED)
    figure.add_artist(
        plt.Line2D(
            (0.055, 0.945), (0.085, 0.085), color=LINE, linewidth=1, transform=figure.transFigure
        )
    )
    try:
        source_label = str(source.relative_to(ROOT))
    except ValueError:
        source_label = source.name
    elapsed = number(report["elapsedSeconds"], "Suite elapsed time")
    figure.text(
        0.055,
        0.052,
        f"Source: {source_label}  ·  Actual suite elapsed: {elapsed:.2f} s",
        size=9,
        color=MUTED,
    )
    figure.text(
        0.945,
        0.052,
        "Job IDs abbreviated; measurements retain their original units.",
        size=8.7,
        color=MUTED,
        ha="right",
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "Title": "Media pipeline local functional validation",
        "Description": f"Observed integration run finished {completed}. Not an AWS performance benchmark.",
        "Date": timestamp(report["finishedAt"]).isoformat(),
    }
    figure.savefig(output_directory / "validation.svg", metadata=metadata)
    figure.savefig(
        output_directory / "validation.png",
        dpi=200,
        metadata={"Title": metadata["Title"], "Description": metadata["Description"]},
    )
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "build" / "integration-results.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "results")
    args = parser.parse_args()
    try:
        source = args.input.resolve()
        report = json.loads(source.read_text())
        observations(report)
        render(report, source, args.output_dir.resolve())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"Cannot render integration results: {error}\n")
    print(f"Wrote {args.output_dir / 'validation.svg'} and {args.output_dir / 'validation.png'}")


if __name__ == "__main__":
    main()
