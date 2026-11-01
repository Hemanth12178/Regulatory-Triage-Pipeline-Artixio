export type Quality = "ok" | "warning" | "error";

export interface Flag {
  code: string;
  level: "error" | "warning";
  field: string | null;
  message: string;
}

export interface ActionItem {
  id: number;
  directive_id: number;
  title: string | null;
  owner: string | null;
  status: string;
  raw_status: string | null;
  priority: string;
  raw_priority: string | null;
  due_date: string | null;
  raw_due_date: string | null;
  resolved_at: string | null;
  notes: string | null;
  updated_at: string | null;
  is_open: boolean;
  quality: Quality;
  flags: Flag[];
}

export interface Authority {
  id: number;
  code: string;
  name: string;
  country: string;
  region: string;
}

export interface Directive {
  id: number;
  reference_code: string | null;
  title: string | null;
  summary: string | null;
  category: string | null;
  status: string;
  raw_status: string | null;
  severity: string;
  raw_severity: string | null;
  published_at: string | null;
  /** Consultation response deadline, distinct from the compliance deadline. */
  comments_by: string | null;
  effective_date: string | null;
  deadline: string | null;
  raw_dates: {
    published_at: string | null;
    comments_by: string | null;
    effective_date: string | null;
    deadline: string | null;
  };
  source_url: string | null;
  authority: Authority;
  action_items: ActionItem[];
  open_item_count: number;
  total_item_count: number;
  days_to_deadline: number | null;
  quality: Quality;
  rollup_quality: Quality;
  flags: Flag[];
}

export interface Stats {
  directives: number;
  action_items: number;
  open_action_items: number;
  overdue_directives: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  by_quality: Record<Quality, number>;
  by_flag: Record<string, number>;
}

export interface DirectivePage {
  items: Directive[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  stats: Stats;
}

/**
 * Server-owned enums and the action item state machine. Fetched rather than
 * copied, so a change in the backend cannot leave the UI offering a
 * transition the API will reject.
 */
export interface Vocabulary {
  directive_status: string[];
  severity: string[];
  action_status: string[];
  assignable_action_status: string[];
  action_transitions: Record<string, string[]>;
  quality: Quality[];
  flag_codes: string[];
}

/** Used only until the real vocabulary arrives on first paint. */
export const EMPTY_VOCABULARY: Vocabulary = {
  directive_status: [],
  severity: [],
  action_status: [],
  assignable_action_status: [],
  action_transitions: {},
  quality: ["error", "warning", "ok"],
  flag_codes: [],
};

export interface Owner {
  /** `__unassigned__` for items the sanitiser found no accountable owner on. */
  value: string;
  label: string;
  total_items: number;
  open_items: number;
}

export const UNASSIGNED_OWNER = "__unassigned__";

export interface Filters {
  q: string;
  authority: string[];
  status: string[];
  severity: string[];
  quality: Quality[];
  flag: string[];
  owner: string[];
  only_open: boolean;
  sort: "quality" | "deadline" | "published" | "severity" | "authority" | "reference";
  order: "asc" | "desc";
  page: number;
  page_size: number;
}

export const DEFAULT_FILTERS: Filters = {
  q: "",
  authority: [],
  status: [],
  severity: [],
  quality: [],
  flag: [],
  owner: [],
  only_open: false,
  sort: "quality",
  order: "asc",
  page: 1,
  page_size: 25,
};
