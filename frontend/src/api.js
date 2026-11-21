// Backend base URL is injected at build time (VITE_API_BASE_URL) and falls back
// to localhost for local `make up`.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')

export function getApiBase() {
  return API_BASE
}

export async function getHealth() {
  const res = await fetch(`${API_BASE}/health`)
  if (!res.ok) throw new Error(`health ${res.status}`)
  return res.json()
}

// Sends one chat turn. Throws an Error (with .status) on 422/503 so the UI can
// render a readable message in the chat instead of a blank screen.
export async function sendChat({ sessionId, message, systemPrompt, simulate }) {
  const headers = { 'Content-Type': 'application/json' }
  if (simulate && simulate.trim()) {
    headers['X-Simulate-Failure'] = simulate.trim()
  }

  const body = { session_id: sessionId, message }
  if (systemPrompt && systemPrompt.trim()) {
    body.system_prompt = systemPrompt
  }

  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })

  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }

  if (!res.ok) {
    const detail =
      data && data.detail
        ? typeof data.detail === 'string'
          ? data.detail
          : JSON.stringify(data.detail)
        : `Request failed (${res.status})`
    const error = new Error(detail)
    error.status = res.status
    throw error
  }

  return data
}
