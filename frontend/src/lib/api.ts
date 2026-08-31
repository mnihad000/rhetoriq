import type {
  LiveInvestigationWorkspace,
  LiveClaimVerificationResult,
  LiveResearchTrail,
  LiveRecentInvestigationSummary,
  LiveTrendingFeed,
  LiveTrendingInvestigationResponse,
} from "../types/rhetoriq";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://127.0.0.1:8000";

type RequestOptions = {
  body?: unknown;
  method?: "GET" | "POST";
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    body: options.body ? JSON.stringify(options.body) : undefined,
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    method: options.method ?? "GET",
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;

    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {
      // Ignore JSON parsing failures and keep the fallback error message.
    }

    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export type PlannerResponse = {
  investigation_id: string;
  query_text: string;
};

export async function createInvestigation(queryText: string) {
  return request<PlannerResponse>("/api/investigate", {
    body: { query_text: queryText },
    method: "POST",
  });
}

export async function getTrendingFeed(limit = 6) {
  return request<LiveTrendingFeed>(`/api/trending?limit=${limit}`);
}

export async function getRecentInvestigations(limit = 6) {
  return request<LiveRecentInvestigationSummary[]>(
    `/api/investigations?limit=${limit}`,
  );
}

export async function startTrendingInvestigation(topicId: string) {
  return request<LiveTrendingInvestigationResponse>(
    `/api/trending/${topicId}/investigate`,
    {
      method: "POST",
    },
  );
}

export async function getInvestigationWorkspace(investigationId: string) {
  return request<LiveInvestigationWorkspace>(
    `/api/investigations/${investigationId}`,
  );
}

export async function runInvestigation(investigationId: string) {
  return request<LiveInvestigationWorkspace>(
    `/api/investigations/${investigationId}/run`,
    {
      body: {},
      method: "POST",
    },
  );
}

export async function getResearchTrail(investigationId: string, afterSequence = 0) {
  return request<LiveResearchTrail>(
    `/api/investigations/${investigationId}/research-trail?after_sequence=${afterSequence}&limit=500`,
  );
}

export function getResearchEventsUrl(investigationId: string) {
  return `${API_BASE_URL}/api/investigations/${investigationId}/events`;
}

export async function replayResearchRun(investigationId: string, runId: string) {
  return request(`/api/investigations/${investigationId}/runs/${runId}/replay`, {
    method: "POST",
  });
}

export async function runRetrieval(investigationId: string) {
  return request(`/api/investigations/${investigationId}/retrieve`, {
    body: {},
    method: "POST",
  });
}

export async function runTimeline(investigationId: string) {
  return request(`/api/investigations/${investigationId}/timeline`, {
    body: {},
    method: "POST",
  });
}

export async function runSourceDiversity(investigationId: string) {
  return request(`/api/investigations/${investigationId}/source-diversity`, {
    body: {},
    method: "POST",
  });
}

export async function runCounterNarratives(investigationId: string) {
  return request(`/api/investigations/${investigationId}/counter-narratives`, {
    body: {},
    method: "POST",
  });
}

export async function runNarrativeFamily(investigationId: string) {
  return request(`/api/investigations/${investigationId}/family`, {
    body: {},
    method: "POST",
  });
}

export async function runAnalyst(investigationId: string) {
  return request(`/api/investigations/${investigationId}/analyst`, {
    body: {},
    method: "POST",
  });
}

export async function runClaimCounterpoints(investigationId: string) {
  return request(`/api/investigations/${investigationId}/claim-counterpoints`, {
    body: {},
    method: "POST",
  });
}

export async function runReceipts(investigationId: string) {
  return request(`/api/investigations/${investigationId}/receipts`, {
    body: {},
    method: "POST",
  });
}

export async function runAgentDebate(investigationId: string) {
  return request(`/api/investigations/${investigationId}/agent-debate`, {
    body: {},
    method: "POST",
  });
}

export async function runReport(investigationId: string) {
  return request(`/api/investigations/${investigationId}/report`, {
    body: {},
    method: "POST",
  });
}

export async function verifyInvestigationClaims(
  investigationId: string,
  forceRefresh = true,
) {
  return request<LiveClaimVerificationResult>(
    `/api/investigations/${investigationId}/verification`,
    { body: { force_refresh: forceRefresh }, method: "POST" },
  );
}
