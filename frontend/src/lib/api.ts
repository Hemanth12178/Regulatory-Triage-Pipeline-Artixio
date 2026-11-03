import type {
  ActionItem,
  Authority,
  DirectivePage,
  Filters,
  Owner,
  Vocabulary,
} from "../types";

/** An error carrying the message the API actually produced, not a generic one. */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    // The backend is not reachable at all - say so plainly.
    throw new ApiError(0, "Cannot reach the API. Is the backend running on port 8000?");
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export function buildQuery(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.q.trim()) params.set("q", filters.q.trim());
  filters.authority.forEach((v) => params.append("authority", v));
  filters.status.forEach((v) => params.append("status", v));
  filters.severity.forEach((v) => params.append("severity", v));
  filters.quality.forEach((v) => params.append("quality", v));
  filters.flag.forEach((v) => params.append("flag", v));
  filters.owner.forEach((v) => params.append("owner", v));
  filters.owner_status.forEach((v) => params.append("owner_status", v));
  if (filters.only_open) params.set("only_open", "true");
  params.set("sort", filters.sort);
  params.set("order", filters.order);
  params.set("page", String(filters.page));
  params.set("page_size", String(filters.page_size));
  return params.toString();
}

export const fetchDirectives = (filters: Filters) =>
  request<DirectivePage>(`/api/directives?${buildQuery(filters)}`);

export const fetchAuthorities = () => request<Authority[]>("/api/authorities");

export const fetchOwners = () => request<Owner[]>("/api/owners");

export const fetchVocabulary = () => request<Vocabulary>("/api/vocabulary");

export interface ActionItemPatch {
  status?: string;
  owner?: string;
  notes?: string;
  expected_updated_at?: string | null;
}

export const patchActionItem = (id: number, patch: ActionItemPatch) =>
  request<ActionItem>(`/api/action-items/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
