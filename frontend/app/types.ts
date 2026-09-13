// Shared vocabulary with the backend. A PlayerId is a PlayerId everywhere (CLAUDE.md 2.6).
// Once the API is stable these are generated from the OpenAPI schema instead of hand-written.

export type PlayerId = string;
export type Slot = "QB" | "RB" | "WR" | "TE" | "W/R/T" | "K" | "DEF" | "BN" | "IR";

/** "too_close_to_call" is a normal outcome, not an error state. */
export type Confidence = "clear" | "lean" | "too_close_to_call";

export interface SlotDecision {
  slot: Slot;
  winner: PlayerId;
  runner_up: PlayerId | null;
  win_prob_delta: number;
  confidence: Confidence;
  reason: string;
}

export interface LineupPlan {
  week: number;
  assignments: [PlayerId, Slot][];
  expected_points: number;
  win_probability: number;
  /** Always render the band. A bare probability overstates what we know. */
  win_probability_band: [number, number];
  notes: string[];
}

export interface SourceStatus {
  name: string;
  ok: boolean;
  required: boolean;
  age_seconds: number | null;
  detail: string | null;
}

export interface Recommendation {
  week: number;
  lineup: LineupPlan;
  decisions: SlotDecision[];
  sources: SourceStatus[];
  caveats: string[];
}
