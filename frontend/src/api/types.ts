export type VerificationStatus =
  | "verified_kb"
  | "general_unverified"
  | "unavailable"
  | "web_unverified";

export interface Verification {
  status: VerificationStatus;
  label: string;
  reason?: string | null;
}

export interface SourceReference {
  id: string;
  title: string;
  file: string;
  url?: string | null;
  chunk_index?: number | null;
  snippet: string;
  score?: number | null;
  distance?: number | null;
}

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  sources: SourceReference[];
  grounded: boolean;
  verification?: Verification | null;
  persona?: string | null;
  response_time_ms?: number | null;
  llm_time_seconds?: number | null;
  tokens_used?: number | null;
  max_tokens?: number | null;
  error?: string | null;
}

export interface RetrievalInfo {
  attempted: boolean;
  used: boolean;
  query?: string | null;
  n_results?: number | null;
  result_count: number;
  duration_ms?: number | null;
  reason?: string | null;
}

export interface ChatResponse {
  session_id: string;
  user_message: Message;
  assistant_message: Message;
  sources: SourceReference[];
  grounded: boolean;
  verification: Verification;
  persona: string;
  response_time_ms: number;
  retrieval: RetrievalInfo;
  created_at: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  preview: string;
}

export interface SessionDetail extends SessionSummary {
  messages: Message[];
}

export interface SessionList {
  sessions: SessionSummary[];
  count: number;
}

export interface DeleteSessionResponse {
  deleted: boolean;
  session_id: string;
}

export interface ComponentStatus {
  status: string;
  connected?: boolean;
  detail?: string;
  [key: string]: unknown;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
  app: string;
  version: string;
  environment: string;
  timestamp: string;
  components: {
    // Exactly one of ollama (local provider) or llm (hosted provider) is present.
    llm?: ComponentStatus & { provider?: string; model?: string };
    store?: ComponentStatus & { kind?: string };
    ollama?: ComponentStatus & {
      latency_ms?: number;
      models?: string[];
      llm_model?: string;
      embedding_model?: string;
      llm_model_available?: boolean;
      embedding_model_available?: boolean;
    };
    chromadb: ComponentStatus & {
      indexed_chunks?: number;
      expected_chunks?: number;
      collection?: string;
    };
    grounding: ComponentStatus & {
      fallback_message?: string;
      strict_grounding?: boolean;
    };
  };
  worker: {
    started: boolean;
    alive: boolean;
    pid?: number | null;
    pending: number;
    model: string;
    timeout_seconds: number;
  };
  configuration: Record<string, unknown>;
  protected_files: {
    ok: boolean;
    files: Array<{
      name: string;
      expected: string;
      actual?: string | null;
      ok: boolean;
      error?: string | null;
    }>;
    error?: string | null;
  };
}
