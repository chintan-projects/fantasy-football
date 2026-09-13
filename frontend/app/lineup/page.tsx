import type { Recommendation } from "../types";

/** The recommendation screen. Every number carries its uncertainty (CLAUDE.md 3). */
export default async function LineupPage() {
  const rec = await fetchRecommendation();

  if (!rec) {
    return (
      <div>
        <h1>Lineup</h1>
        <p>
          No recommendation yet. Connect Yahoo and configure at least two projection sources
          — see docs/YAHOO_SETUP.md.
        </p>
      </div>
    );
  }

  const [low, high] = rec.lineup.win_probability_band;

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Week {rec.week}</h1>
      <p>
        Win probability <strong>{pct(rec.lineup.win_probability)}</strong>{" "}
        <span style={{ color: "#666" }}>
          (band {pct(low)}–{pct(high)})
        </span>
      </p>

      <h2 style={{ fontSize: 16 }}>Contested calls</h2>
      {rec.decisions.length === 0 ? (
        <p>Nothing close this week. The lineup picks itself.</p>
      ) : (
        <ul style={{ paddingLeft: 18 }}>
          {rec.decisions.map((d) => (
            <li key={d.slot} style={{ marginBottom: 10 }}>
              <strong>{d.slot}</strong>: {d.winner}
              {d.confidence === "too_close_to_call" ? (
                <span style={{ color: "#666" }}> — too close to call</span>
              ) : (
                <span style={{ color: "#666" }}> over {d.runner_up}</span>
              )}
              <div style={{ color: "#555", fontSize: 14 }}>{d.reason}</div>
            </li>
          ))}
        </ul>
      )}

      <h2 style={{ fontSize: 16 }}>Sources</h2>
      <p style={{ fontSize: 14, color: "#555" }}>
        {rec.sources.map((s) => `${s.name} ${s.ok ? "ok" : "stale"}`).join(" · ") || "none"}
      </p>

      {rec.caveats.map((c) => (
        <p key={c} style={{ fontSize: 13, color: "#666" }}>
          {c}
        </p>
      ))}
    </div>
  );
}

function pct(x: number): string {
  return `${(x * 100).toFixed(0)}%`;
}

async function fetchRecommendation(): Promise<Recommendation | null> {
  try {
    const res = await fetch(`${process.env.API_BASE}/recommendation`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Recommendation;
  } catch {
    // The page renders without the backend. Degrade, never crash (CLAUDE.md 2.4).
    return null;
  }
}
