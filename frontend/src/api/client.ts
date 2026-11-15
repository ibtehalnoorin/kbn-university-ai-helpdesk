import type {
  ChatResponse,
  DeleteSessionResponse,
  HealthResponse,
  SessionDetail,
  SessionList,
} from "./types";
import { getClientId } from "../lib/storage";

const inferredBase = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";
export const API_BASE = String(
  import.meta.env.VITE_API_BASE_URL ?? inferredBase,
).replace(/\/+$/, "");

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(
    message: string,
    status = 0,
    code = "network_error",
    details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        "X-Client-Id": getClientId(),
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    const detail =
      error instanceof Error && error.message
        ? ` (${error.message})`
        : "";
    throw new ApiError(
      `Cannot reach the helpdesk server${detail}. Make sure the backend is running on ${API_BASE || "this host"}.`,
      0,
      "network_error",
    );
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const envelope =
      payload && typeof payload === "object" && "error" in payload
        ? (payload as { error?: { code?: string; message?: string; details?: unknown } }).error
        : undefined;
    throw new ApiError(
      envelope?.message ||
        `The server returned HTTP ${response.status}.`,
      response.status,
      envelope?.code || `http_${response.status}`,
      envelope?.details ?? payload,
    );
  }

  return payload as T;
}

function jsonBody(value: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(value) };
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  listSessions: () => request<SessionList>("/sessions"),

  createSession: (title?: string) =>
    request<SessionDetail>("/sessions", jsonBody({ title: title ?? null })),

  getSession: (id: string) =>
    request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),

  renameSession: (id: string, title: string) =>
    request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  deleteSession: (id: string) =>
    request<DeleteSessionResponse>(`/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  clearSession: (id: string) =>
    request<SessionDetail>(`/sessions/${encodeURIComponent(id)}/clear`, {
      method: "POST",
    }),

  sendChat: (sessionId: string, message: string) =>
    request<ChatResponse>("/chat", jsonBody({ session_id: sessionId, message })),
};

export function isTimeout(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 504 || error.code === "llm_timeout");
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.code === "not_found");
}

export function isBusy(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.code === "session_busy");
}
