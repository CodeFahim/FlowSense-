// Response shapes of the FastAPI backend (see services/api/app/api/serialize.py).
// Hand-maintained in one place; see DECISIONS.md for why not generated yet.
import type { Reason } from "./api";

export type Preferences = {
  country: string | null;
  display_currency: string | null;
  effective_display_currency: string | null;
  convert_from: string | null;
  convert_to: string | null;
  number_locale: string | null;
  time_display: "business" | "device";
  theme: "system" | "light" | "dark";
  accent: "teal" | "blue" | "violet" | "emerald" | "amber";
  onboarded: boolean;
};

export type AccountingCurrency = { currency: string | null; currency_name: string | null; minor_units: number; confirmed: boolean; locked: boolean };

export type SetupStep = { key: "profile" | "cash" | "money_in" | "money_out" | "review"; title: string; done: boolean; detail: string; href: string; none_confirmed?: boolean };
export type Setup = { complete: boolean; steps: SetupStep[]; next: string | null; analysis_ready: boolean };

export type WorkspaceKind = "workspace" | "demo" | "sample" | "isolation";

export type Me = {
  user: { id: string; email: string; username: string | null; display_name: string; email_verified: boolean; account_kind: "user" | "designated" | "fixture"; deletable: boolean };
  business: {
    id: string;
    name: string;
    kind: WorkspaceKind;
    place: string | null;
    business_type: string | null;
    country: string | null;
    currency: string | null;
    currency_minor_units: number;
    timezone: string | null;
    timezone_confirmed: boolean;
    configured: boolean;
    kill_switch: boolean;
    sandbox_now: string | null;
    simulated_clock: boolean;
    scenario: string | null;
  };
  workspaces: { id: string; name: string; kind: WorkspaceKind; role: string; active: boolean }[];
  setup: Setup;
  preferences: Preferences;
  accounting_currency: AccountingCurrency;
  needs_onboarding: boolean;
  role: "owner" | "staff";
  csrf_token: string;
  ai_mode: "demo" | "live" | "off";
  assistant_label: string;
  demo_controls: boolean;
  email_delivery: "configured" | "not_configured";
  signup_enabled: boolean;
};

export type HomeTask = { key: string; title: string; why: string; href: string; cta: string; amount_minor?: number; date?: string };
export type Home = {
  status: { tone: "incomplete" | "ok" | "warn" | "danger"; label: string; sentence: string };
  data: {
    cash_status: "declared" | "not_entered";
    cash_as_of: string | null;
    cash_age_days: number | null;
    cash_outdated: boolean;
    open_invoices: number;
    overdue_invoices: number;
    open_obligations: number;
    money_in_none: boolean;
    money_out_none: boolean;
    policy: boolean;
    business_date: string;
    simulated_clock: boolean;
    horizon_end: string;
  };
  tasks: HomeTask[];
};

export type Obligation = {
  id: string;
  kind: "payroll" | "supplier" | "other";
  description: string;
  payee: Counterparty | null;
  amount_minor: number;
  paid_minor: number;
  unpaid_minor: number;
  due_at: string;
  essential: boolean;
  recurrence: "none" | "weekly" | "monthly";
  series_id: string | null;
  state: "planned" | "needs_approval" | "ready_to_send" | "payment_in_progress" | "paid";
  overdue: boolean;
};

export type Counts = { approvals: number; needs_approval: number; ready_to_send: number; in_progress: number; rescue_open?: number };

export type ProtectedItem = { obligation_id: string; description: string; kind: string; due_at: string; open_minor: number };

export type Position = {
  as_of: string;
  currency: string;
  settled_minor: number;
  reserved_minor: number;
  available_minor: number;
  reserve_minor: number;
  horizon_end: string;
  protected: ProtectedItem[];
  protected_total_minor: number;
  gap_minor: number;
  gap_date: string | null;
  policy_version: number | null;
  notes: string[];
  cash_status: "declared" | "not_entered";
  cash_as_of: string | null;
};

export type AuditEvent = {
  seq: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  event_type: string;
  case_id: string | null;
  action_id: string | null;
  approval_id: string | null;
  receipt_id: string | null;
  policy_version: number | null;
  outcome: string;
  reasons: Reason[];
  correlation_id: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
};

export type Policy = {
  id: string;
  version: number;
  status: string;
  source_text: string;
  spec: Record<string, unknown>;
  interpretation: { fields: { field: string; value: string; source: string; note?: string }[]; ambiguities: string[]; questions: string[] };
  activated_at: string | null;
};

export type NextAction = {
  kind: "action" | "infeasible" | "waiting" | "done" | "none";
  action_id?: string;
  title: string;
  why: string;
  outcome: string;
  amount_minor: number | null;
  requires_approval: boolean;
  href: string;
  cta: string;
};

export type Equivalents = {
  currency: string;
  status: "same" | "latest" | "cached" | "stale" | "unavailable";
  label?: string;
  rate?: string;
  rate_date?: string | null;
  detail?: string;
  values: Record<"settled" | "upcoming" | "reserve" | "gap", number> | null;
};

export type Dashboard = {
  setup_required?: false;
  currency: string;
  headline: string;
  home: Home;
  next_action: NextAction;
  display_equivalents: Equivalents;
  position: Position;
  policy: Policy | null;
  kill_switch: boolean;
  case: { id: string; status: string } | null;
  plan_summary: string | null;
  next_step: string;
  receivables: { outstanding_minor: number; overdue_minor: number; count: number };
  obligations: { id: string; kind: string; description: string; amount_minor: number; paid_minor: number; due_at: string; essential: boolean }[];
  pending_approvals: number;
  ledger_balanced: boolean;
  recent_activity: AuditEvent[];
  this_month?: { month: string; money_in_minor: number; money_out_minor: number; adjustments_minor?: number };
};

export type Counterparty = { id: string; kind: string; name: string; account_ref: string; version: number; verified: boolean; accepts_partial: boolean };

export type Decision = {
  phase: string;
  outcome: "ALLOW" | "DENY" | "NEEDS_APPROVAL";
  reasons: Reason[];
  warnings: Reason[];
  checks: { name: string; passed: boolean; codes: string[] }[];
  evaluated_at: string;
};

export type Approval = {
  id: string;
  action_revision: number;
  amount_minor: number;
  currency: string;
  counterparty_version: number;
  policy_version: number;
  payload_digest: string;
  expires_at: string;
  consumed_at: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
};

export type Transfer = {
  id: string;
  direction: string;
  status: string;
  amount_minor: number;
  currency: string;
  account_ref: string;
  provider_ref: string;
  submitted_at: string;
  settled_at: string | null;
};

export type Action = {
  id: string;
  type: "payroll_payment" | "supplier_payment" | "payment_request";
  status: string;
  revision: number;
  amount_minor: number;
  settled_minor: number;
  currency: string;
  purpose: string;
  source: string;
  counterparty: Counterparty | null;
  counterparty_version: number;
  obligation: { id: string; description: string; amount_minor: number; paid_minor: number; due_at: string } | null;
  invoice: { id: string; number: string; outstanding_minor: number } | null;
  case_id: string | null;
  last_decision: Decision | null;
  /** Fresh read-only kernel check, present only for proposed actions in the action list. */
  current_check?: Decision | null;
  approval: Approval | null;
  transfer: Transfer | null;
  created_at: string;
};

export type Evidence = {
  id: string;
  label: string;
  title: string;
  kind: string;
  filename: string | null;
  provenance: "user_supplied" | "sandbox_verified" | "ai_assessment";
  instruction_like: boolean;
  sha256: string;
  created_at: string;
  chars: number;
  content_text?: string;
};

export type Invoice = {
  id: string;
  number: string;
  description: string;
  customer: Counterparty | null;
  issued_on: string;
  due_on: string;
  amount_minor: number;
  collected_minor: number;
  outstanding_minor: number;
  disputed: boolean;
  evidence: Evidence[];
  requests?: Action[];
};

export type PlanComponent = {
  id: string;
  kind: string;
  code?: string;
  reason?: string;
  basis?: string;
  max_contribution_minor?: number;
  cost_minor?: number;
  expected_by?: string;
  dependencies?: string[];
  uncertainty?: string;
  evidence?: string[];
  invoice?: string;
  counterparty?: string;
  annualized_all_in_bp?: number;
  net_proceeds_minor?: number;
  principal_minor?: number;
  description?: string;
  open_minor?: number;
};

export type PlanBody = {
  method: string;
  gap_minor: number;
  gap_date: string | null;
  components: PlanComponent[];
  rejected: PlanComponent[];
  feasible: boolean;
  recommended: { components: string[]; total_cost_minor: number } | null;
  alternatives: { components: string[]; total_cost_minor: number }[];
  steps: { component: string; kind: string; amount_minor: number }[];
  remaining_gap_minor: number;
  unmet_constraints?: string[];
  summary: string;
};

export type RescueCase = {
  id: string;
  status: string;
  opened_at: string;
  resolved_at: string | null;
  target: { id: string; description: string; amount_minor: number; paid_minor: number; due_at: string } | null;
  plans: { id: string; version: number; trigger: string; gap_minor: number; feasible: boolean; created_at: string; body: PlanBody }[];
  transitions: { from: string | null; to: string; reason: string; at: string }[];
};

export type ForecastPoint = {
  date: string;
  settled_path_minor: number;
  expected_path_minor: number;
  uncertain_inflows_minor: number;
  reserve_minor: number;
  obligations_due_minor: number;
};

export type ForecastSeries = {
  points: ForecastPoint[];
  first_reserve_breach_settled: string | null;
  first_reserve_breach_expected: string | null;
  min_expected_minor: number;
  inflows: { date: string; amount_minor: number; label: string; class: string }[];
  outflows: { date: string; amount_minor: number; label: string }[];
};

export type Forecast = {
  currency: string;
  days: number;
  position: Position;
  assumptions: string[];
  baseline: ForecastSeries;
  scenario: ForecastSeries;
  whatif: { delay_days: number; collection_pct: number; emergency_minor: number; emergency_day: number };
};

export type Receipt = { id: string; number: string; content: Record<string, unknown>; digest: string; created_at: string };

export type Citation = { kind: string; ref: string; label: string; href: string };
export type Draft = { record: "invoice" | "bill" | "payroll" | "cash"; fields: Record<string, string>; href: string; stored: false };

export type Proposal = {
  id: string;
  kind: "record_cash" | "add_invoice" | "add_bill" | "add_payroll" | "request_payment";
  summary: string;
  effect: string;
  status: "pending" | "done" | "failed" | "cancelled" | "expired";
  expires_at?: string;
  result?: { verified?: boolean; message?: string; href?: string; blocked?: boolean; error?: string; record?: string; id?: string; position?: { text: string } | null } | null;
};

export type ChatReply = {
  /** quick = answered by FlowSense's own calculations because the AI was out of quota, busy or slow. */
  mode: "live" | "demo" | "system" | "quick";
  label: string;
  /** Why this was a quick answer instead of an AI answer (shown under the badge). */
  notice?: string;
  intent?: string;
  text: string;
  citations: Citation[];
  links: { label: string; href: string }[];
  drafts: Draft[];
  proposals?: Proposal[];
  tool_calls?: { tool: string; error: boolean; automatic?: boolean }[];
  activity: string | null;
  decision?: Decision | { outcome: string; reasons: Reason[] };
  suggestions?: string[];
  proposed_policy?: { id: string; version: number; spec: Record<string, unknown>; interpretation: Policy["interpretation"]; activatable: boolean };
  based_on_records_at?: string;
  explicit_demo?: boolean;
  /** FlowSense's own classification of the options (agents/options.py); the model only explains it. */
  options?: OptionAssessment | null;
};

export type OptionClass = "prohibited" | "conditional" | "feasible" | "insufficient_information";
export type MoneyFig = { minor: number; display: string };
export type AssessedOption = {
  id: string;
  title: string;
  class: OptionClass;
  label: string;
  cash_impact?: MoneyFig;
  cost?: MoneyFig;
  timing?: string;
  rule?: string;
  conditions?: string[];
  conditions_met?: boolean;
  requirements?: string[];
  missing?: string[];
  basis?: string;
};
export type OptionAssessment = {
  as_of?: string;
  status: "shortfall" | "no_shortfall" | "insufficient_information";
  situation?: Record<string, MoneyFig | string>;
  hypothetical?: { description: string; amount: MoneyFig; due: string; saved: false; note: string };
  options: AssessedOption[];
  ruled_out: { title: string; reason: string }[];
  recommended?: { id: string; title: string; class: OptionClass } | null;
  missing?: string[];
  calculation?: string;
  feasible_options_cover_gap?: boolean;
  note?: string;
};

export type ChatMessage = { id: number; role: "user" | "assistant"; text: string; created_at: string } & Partial<Omit<ChatReply, "text">>;

export type AssistantStatus = {
  mode: "demo" | "live" | "off";
  label: string;
  live_configured: boolean;
  provider?: "anthropic" | "gemini" | null;
  model: string | null;
  suggestions: { primary: string[]; more: string[] };
  quick_actions?: string[];
  retention_days: number;
  limits: { messages_per_day: number; max_tool_rounds: number; timeout_seconds: number };
};

export type Currency = { code: string; name: string; iso_name: string; minor_units: number; used_in: string[] };
export type Region = { code: string; name: string; currencies: string[] };
export type ReferenceMeta = { iso4217_published: string; cldr_version: string; currency_count: number; region_count: number; iso4217_source: string; cldr_source: string };

export type RateSource = {
  mode: "latest" | "demo" | "manual";
  status: "current" | "cached" | "stale" | "demo" | "manual" | "unavailable";
  label: string;
  provider?: string;
  base?: string;
  rate_date: string | null;
  provider_as_of?: string | null;
  next_update_at?: string | null;
  fetched_at: string | null;
  frequency?: string | null;
  attribution?: { text: string; url: string } | null;
  fees_note?: string;
  detail: string;
  currencies?: string[];
};

export type FxCoverage = {
  provider: string;
  provider_label: string;
  status: string;
  detail: string;
  refresh_frequency: string | null;
  key_required: boolean | null;
  plan: string | null;
  attribution: { text: string; url: string } | null;
  selectable_count: number;
  quoted_count: number;
  quoted: string[];
  unavailable: string[];
  provider_as_of: string | null;
  notes: string[];
};

export type ConvertResult = {
  from: string;
  to: string;
  amount_minor: number;
  amount_display: string;
  result_minor: number | null;
  result_display: string | null;
  rate: string | null;
  rate_exact?: string;
  inverse_rate: string | null;
  rounding?: string;
  derived?: string | null;
  missing: string[];
  source: RateSource;
};
