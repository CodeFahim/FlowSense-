"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { money, moneyShort, shortDate } from "@/lib/format";
import { useReducedMotion } from "@/lib/motion";
import type { ForecastSeries } from "@/lib/types";

type Row = { date: string; settled: number; expected: number; scenario?: number; reserve: number };

// Colours come from theme variables so the chart follows light/dark and the accent.
const C = { settled: "var(--chart-settled)", expected: "var(--accent)", scenario: "var(--warn)", reserve: "var(--warn)", zero: "var(--danger)", grid: "var(--chart-grid)", axis: "var(--muted)" };

function Key({ color, dashed, label }: { color: string; dashed?: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg width="22" height="8" aria-hidden><line x1="0" y1="4" x2="22" y2="4" stroke={color} strokeWidth="2.5" strokeDasharray={dashed ? "5 3" : undefined} /></svg>
      {label}
    </span>
  );
}

export function ForecastChart({ baseline, scenario, currency, height = 260 }: { baseline: ForecastSeries; scenario?: ForecastSeries; currency: string; height?: number }) {
  const [asTable, setAsTable] = useState(false);
  // Lines ease to new values after an update (short, and off when the user prefers less motion).
  const still = useReducedMotion();
  const anim = { isAnimationActive: !still, animationDuration: 300, animationEasing: "ease-out" as const };
  const rows: Row[] = baseline.points.map((p, i) => ({
    date: p.date,
    settled: p.settled_path_minor,
    expected: p.expected_path_minor,
    scenario: scenario?.points[i]?.expected_path_minor,
    reserve: p.reserve_minor,
  }));
  const reserve = baseline.points[0]?.reserve_minor ?? 0;
  // Only draw the what-if line when it differs; otherwise it would hide the baseline lines.
  const showScenario = !!scenario && rows.some((r) => r.scenario !== r.expected);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-muted">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <Key color={C.settled} label="Settled cash only (used for safety)" />
          <Key color={C.expected} dashed label="With expected payments (not guaranteed)" />
          {showScenario && <Key color={C.scenario} label="What-if" />}
          <Key color={C.reserve} dashed label="Minimum reserve" />
        </div>
        <button type="button" onClick={() => setAsTable((t) => !t)} className="rounded font-medium text-accent-ink underline-offset-2 hover:underline" aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-80 overflow-auto rounded-lg border border-line">
          <table className="num w-full text-left text-xs">
            <caption className="sr-only">Projected daily balance in {currency}</caption>
            <thead className="sticky top-0 bg-sunken text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Date</th>
                <th className="px-3 py-2 font-medium">Settled only</th>
                <th className="px-3 py-2 font-medium">With expected</th>
                {showScenario && <th className="px-3 py-2 font-medium">What-if</th>}
                <th className="px-3 py-2 font-medium">Reserve</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.date} className="border-t border-line">
                  <td className="px-3 py-1.5">{shortDate(r.date)}</td>
                  <td className={"px-3 py-1.5 " + (r.settled < r.reserve ? "font-semibold text-danger" : "")}>{money(r.settled, currency)}{r.settled < r.reserve && <span className="sr-only"> (below reserve)</span>}</td>
                  <td className="px-3 py-1.5">{money(r.expected, currency)}</td>
                  {showScenario && <td className="px-3 py-1.5">{money(r.scenario, currency)}</td>}
                  <td className="px-3 py-1.5">{money(r.reserve, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} role="img" aria-label={`Line chart of projected balance in ${currency}. Use "Show as table" for exact values.`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={{ stroke: C.grid }} minTickGap={18} />
              <YAxis
                tickFormatter={(v: number) => moneyShort(v, currency)}
                tick={{ fontSize: 11, fill: C.axis }}
                tickLine={false}
                axisLine={false}
                width={56}
                label={{ value: currency, angle: -90, position: "insideLeft", fill: C.axis, fontSize: 11, dx: 4 }}
              />
              <Tooltip
                formatter={(v, name) => [money(Number(v), currency), name]}
                labelFormatter={(d) => shortDate(String(d))}
                contentStyle={{ fontSize: 12 }}
                labelStyle={{ color: "var(--ink)", fontWeight: 600 }}
                itemStyle={{ color: "var(--ink)" }}
              />
              <ReferenceLine y={reserve} stroke={C.reserve} strokeDasharray="6 4" />
              <ReferenceLine y={0} stroke={C.zero} strokeOpacity={0.6} />
              {showScenario && <Line type="stepAfter" dataKey="scenario" name="What-if" stroke={C.scenario} strokeWidth={2.5} dot={false} {...anim} />}
              <Line type="stepAfter" dataKey="settled" name="Settled cash only" stroke={C.settled} strokeWidth={2} dot={false} {...anim} />
              <Line type="stepAfter" dataKey="expected" name="With expected payments" stroke={C.expected} strokeWidth={2} strokeDasharray="5 3" dot={false} {...anim} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
