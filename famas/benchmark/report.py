"""Benchmark outputs: CSV/JSON results, a Markdown summary and (optionally) charts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

SYSTEM_ORDER = [
    "single_agent",
    "fixed_workflow",
    "full_replan",
    "naive_incremental",
    "fa_mas",
    "fa_mas_verify",
]
# Validated categorical slots 1-5 (light mode); FA-MAS gets slot 1.
SYSTEM_COLORS = {
    "fa_mas": "#2a78d6",
    "full_replan": "#eb6834",
    "naive_incremental": "#1baf7a",
    "fixed_workflow": "#eda100",
    "single_agent": "#e87ba4",
    "fa_mas_verify": "#008300",
}
SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"


def write_rows(rows: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(out_dir / "results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    (out_dir / "results.json").write_text(json.dumps(rows, indent=1))


def _fmt(v: Any, kind: str = "num") -> str:
    if v is None:
        return "–"
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "usd":
        return f"${v:.4f}"
    if kind == "int":
        return f"{v:,.0f}"
    if kind == "s":
        return f"{v:.1f}s"
    return f"{v:.2f}"


def _systems(summary: dict) -> list[str]:
    present = set(summary["overall"])
    return [s for s in SYSTEM_ORDER if s in present] + sorted(present - set(SYSTEM_ORDER))


def summary_markdown(summary: dict[str, Any]) -> str:
    systems = _systems(summary)
    names = summary.get("systems", {})
    lines = [
        f"# FA-MAS benchmark summary\n\n{summary['n_runs']} runs (deterministic simulation, mock LLM).\n"
    ]

    def table(title: str, block: dict[str, dict], cols: list[tuple[str, str, str]]) -> None:
        lines.append(f"## {title}\n")
        lines.append("| System | " + " | ".join(c[1] for c in cols) + " |")
        lines.append("|---|" + "---:|" * len(cols))
        for s in systems:
            if s not in block:
                continue
            row = block[s]
            lines.append(
                f"| {names.get(s, s)} | " + " | ".join(_fmt(row.get(c[0]), c[2]) for c in cols) + " |"
            )
        lines.append("")

    core = [
        ("correct_rate", "Correct", "pct"),
        ("success_rate", "Completed", "pct"),
        ("latency_s", "Latency", "s"),
        ("tokens", "Tokens", "int"),
        ("cost_usd", "Cost", "usd"),
        ("agent_invocations", "Agent calls", "num"),
    ]
    recovery = [
        ("recovery_success_rate", "Recovery success", "pct"),
        ("reuse_rate", "Reuse rate", "pct"),
        ("invalid_reuse_rate", "Invalid reuse", "pct"),
        ("redundant_executions", "Redundant exec.", "num"),
        ("wasted_tokens", "Wasted tokens", "int"),
        ("overhead_tokens", "Replan overhead (tok)", "int"),
    ]
    table("Overall (all scenarios)", summary["overall"], core)
    table("Under failure (all scenarios except `none`)", summary["under_failure"], core)
    table("Recovery behaviour (scenarios with failures)", summary["under_failure"], recovery)
    for sc, block in summary["per_scenario"].items():
        title = summary.get("scenarios", {}).get(sc, sc)
        table(
            f"Scenario `{sc}` — {title}",
            block,
            core + [("reuse_rate", "Reuse", "pct"), ("redundant_executions", "Redundant", "num")],
        )
    for cx, block in summary.get("per_complexity", {}).items():
        table(f"Complexity: {cx}", block, core)
    return "\n".join(lines)


def write_summary(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    (out_dir / "summary.md").write_text(summary_markdown(summary))


def write_plots(summary: dict[str, Any], out_dir: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # optional dependency
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    systems = _systems(summary)
    names = summary.get("systems", {})
    scen = [s for s in summary["per_scenario"] if s != "none"] + (
        ["none"] if "none" in summary["per_scenario"] else []
    )
    paths: list[Path] = []

    def style(ax, title: str, ylabel: str) -> None:
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
        ax.set_title(title, color=TEXT, fontsize=12, loc="left", pad=14)
        ax.set_ylabel(ylabel, color=TEXT_2, fontsize=9)

    def grouped(metric: str, title: str, ylabel: str, fname: str, scale: float = 1.0) -> None:
        fig, ax = plt.subplots(figsize=(11, 4.4), facecolor=SURFACE)
        n = len(systems)
        group_w = 0.8
        w = group_w / n
        for i, s in enumerate(systems):
            xs, ys = [], []
            for j, sc in enumerate(scen):
                v = summary["per_scenario"].get(sc, {}).get(s, {}).get(metric)
                if v is not None:
                    xs.append(j - group_w / 2 + w * (i + 0.5))
                    ys.append(v * scale)
            ax.bar(
                xs, ys, width=w * 0.86, color=SYSTEM_COLORS.get(s, "#888"), label=names.get(s, s), zorder=2
            )
        ax.set_xticks(range(len(scen)))
        ax.set_xticklabels([sc.replace("_", " ") for sc in scen], color=TEXT_2)
        style(ax, title, ylabel)
        ax.set_title(title, color=TEXT, fontsize=12, loc="left", pad=30)
        leg = ax.legend(ncol=n, frameon=False, fontsize=9, loc="lower left", bbox_to_anchor=(0, 1.0))
        for t in leg.get_texts():
            t.set_color(TEXT_2)
        fig.tight_layout()
        p = out_dir / fname
        fig.savefig(p, dpi=160, facecolor=SURFACE)
        plt.close(fig)
        paths.append(p)

    grouped("tokens", "Mean tokens per run, by failure scenario", "tokens", "fig_tokens_by_scenario.png")
    grouped(
        "latency_s",
        "Mean end-to-end latency per run, by failure scenario",
        "seconds (simulated)",
        "fig_latency_by_scenario.png",
    )

    # Correctness heatmap (sequential single-hue ramp, annotated: doubles as a table view)
    import numpy as np

    ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("blue", ramp)
    all_scen = list(summary["per_scenario"])
    grid = np.array(
        [
            [summary["per_scenario"][sc].get(s, {}).get("correct_rate") or 0.0 for s in systems]
            for sc in all_scen
        ]
    )
    fig, ax = plt.subplots(figsize=(8.5, 0.45 * len(all_scen) + 1.6), facecolor=SURFACE)
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    for i in range(len(all_scen)):
        for j in range(len(systems)):
            v = grid[i, j]
            ax.text(
                j,
                i,
                f"{v * 100:.0f}%",
                ha="center",
                va="center",
                fontsize=9,
                color="#ffffff" if v > 0.6 else TEXT,
            )
    ax.set_xticks(range(len(systems)))
    ax.set_xticklabels([names.get(s, s) for s in systems], color=TEXT_2, fontsize=9)
    ax.set_yticks(range(len(all_scen)))
    ax.set_yticklabels([sc.replace("_", " ") for sc in all_scen], color=TEXT_2, fontsize=9)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    for i in range(1, len(all_scen)):  # 2px surface gaps between cells
        ax.axhline(i - 0.5, color=SURFACE, linewidth=2)
    for j in range(1, len(systems)):
        ax.axvline(j - 0.5, color=SURFACE, linewidth=2)
    ax.set_title(
        "Correct final output rate, by scenario and system", color=TEXT, fontsize=12, loc="left", pad=12
    )
    fig.tight_layout()
    p = out_dir / "fig_correctness_heatmap.png"
    fig.savefig(p, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    paths.append(p)
    return paths
