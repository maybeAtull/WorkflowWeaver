// Single place that knows where the backend lives. The backend allows this frontend's
// dev origin via CORS (see backend/app/main.py), so we call it directly.
export const API_BASE_URL = "http://localhost:8000";

export interface HealthResponse {
  status: string;
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`, { signal });
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}
