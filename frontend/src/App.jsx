import React, { useEffect, useRef, useState } from 'react'
import { getApiBase, getHealth, sendChat } from './api.js'

// A fresh session id is generated on every page load (new session per load).
function newSessionId() {
  return 's-' + Math.random().toString(36).slice(2, 10)
}

const SUGGESTIONS = [
  'What is the capital of Japan?',
  'Explain caching in one sentence.',
  'Give me a fun fact about the ocean.',
]

function SparkIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2z"
        fill="currentColor"
      />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width="18" height="18">
      <path d="M4 12l16-8-6 16-3-7-7-1z" fill="currentColor" />
    </svg>
  )
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId)
  const [messages, setMessages] = useState([]) // { kind: 'user'|'assistant'|'error', text, metadata?, status? }
  const [input, setInput] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [simulate, setSimulate] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [loading, setLoading] = useState(false)
  const [health, setHealth] = useState(null)

  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages, loading])

  async function send(text) {
    const trimmed = (text ?? input).trim()
    if (!trimmed || loading) return

    setMessages((prev) => [...prev, { kind: 'user', text: trimmed }])
    setInput('')
    setLoading(true)

    try {
      const data = await sendChat({ sessionId, message: trimmed, systemPrompt, simulate })
      setMessages((prev) => [...prev, { kind: 'assistant', text: data.reply, metadata: data.metadata }])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { kind: 'error', text: err.message || 'Something went wrong', status: err.status },
      ])
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  function startNewChat() {
    setSessionId(newSessionId())
    setMessages([])
    setInput('')
    inputRef.current?.focus()
  }

  function useSuggestion(text) {
    setInput(text)
    inputRef.current?.focus()
  }

  return (
    <div className="app">
      <div className="shell">
        {/* ---- Header ---- */}
        <header className="topbar">
          <div className="brand">
            <span className="logo">
              <SparkIcon className="logo-icon" />
            </span>
            <div className="brand-text">
              <h1>LLM Chatbot</h1>
              <p>Key failover &amp; response caching</p>
            </div>
          </div>
          <div className={`status ${health ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            {health ? (
              <span>
                {health.keys_configured} keys · cache {health.cache_enabled ? 'on' : 'off'}
              </span>
            ) : (
              <span>backend offline</span>
            )}
          </div>
        </header>

        {/* ---- Toolbar ---- */}
        <div className="toolbar">
          <span className="session-pill" title="A new session starts on each page load">
            <span className="session-label">session</span>
            <span className="session-id">{sessionId}</span>
          </span>
          <div className="toolbar-actions">
            <button className="ghost-btn" onClick={startNewChat}>
              New chat
            </button>
            <button
              className={`ghost-btn ${showAdvanced ? 'active' : ''}`}
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? 'Hide options' : 'Options'}
            </button>
          </div>
        </div>

        {/* ---- Options panel ---- */}
        {showAdvanced && (
          <div className="options">
            <label className="field">
              <span className="field-label">System prompt (optional)</span>
              <textarea
                data-testid="system-prompt-input"
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="e.g. You are a terse assistant that answers in one sentence."
                rows={2}
              />
            </label>
            <label className="field">
              <span className="field-label">
                Simulate failure <span className="muted">(dev)</span> — <code>X-Simulate-Failure</code>
              </span>
              <input
                type="text"
                value={simulate}
                onChange={(e) => setSimulate(e.target.value)}
                placeholder="e.g. key_1=429   or   *=429"
              />
            </label>
            <p className="api-line">
              API <code>{getApiBase()}</code>
            </p>
          </div>
        )}

        {/* ---- Chat window ---- */}
        <div className="chat" ref={scrollRef}>
          {messages.length === 0 && !loading && (
            <div className="empty">
              <span className="empty-logo">
                <SparkIcon className="empty-icon" />
              </span>
              <h2>Start a conversation</h2>
              <p>Every reply shows its source, model, latency and per-request tokens.</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="suggestion" onClick={() => useSuggestion(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => {
            if (m.kind === 'user') {
              return (
                <div key={i} className="row user">
                  <div className="bubble user-bubble">{m.text}</div>
                </div>
              )
            }
            if (m.kind === 'error') {
              return (
                <div key={i} className="row assistant">
                  <span className="avatar error-avatar">!</span>
                  <div className="bubble error-bubble">
                    <strong>{m.status ? `Error ${m.status}` : 'Error'}</strong>
                    <span>{m.text}</span>
                  </div>
                </div>
              )
            }
            const md = m.metadata || {}
            const isCache = md.resolution_source === 'LLM_CACHE'
            return (
              <div key={i} className="row assistant">
                <span className="avatar">
                  <SparkIcon className="avatar-icon" />
                </span>
                <div className="bubble assistant-bubble" data-testid="assistant-message">
                  <div className="reply-text">{m.text}</div>
                  <div className="meta">
                    <span className={`meta-source ${isCache ? 'cache' : 'live'}`}>
                      <span className="src-dot" />
                      <span data-testid="meta-source">{md.resolution_source}</span>
                    </span>
                    <span className="meta-stats">
                      <span className="stat">
                        <span className="k">model</span>
                        <span className="v" data-testid="meta-model">{md.model}</span>
                      </span>
                      <span className="stat">
                        <span className="k">latency</span>
                        <span className="v" data-testid="meta-latency">{md.latency_ms} ms</span>
                      </span>
                      <span className="stat">
                        <span className="k">tokens in</span>
                        <span className="v" data-testid="meta-input-tokens">{md.input_tokens}</span>
                      </span>
                      <span className="stat">
                        <span className="k">tokens out</span>
                        <span className="v" data-testid="meta-output-tokens">{md.output_tokens}</span>
                      </span>
                      {md.key_id && (
                        <span className="stat">
                          <span className="k">key</span>
                          <span className="v">{md.key_id}</span>
                        </span>
                      )}
                      {typeof md.fallback_attempts === 'number' && md.fallback_attempts > 0 && (
                        <span className="stat warn">
                          <span className="k">fallbacks</span>
                          <span className="v">{md.fallback_attempts}</span>
                        </span>
                      )}
                    </span>
                  </div>
                </div>
              </div>
            )
          })}

          {loading && (
            <div className="row assistant">
              <span className="avatar">
                <SparkIcon className="avatar-icon" />
              </span>
              <div className="bubble assistant-bubble typing">
                <span className="dot-typing" />
                <span className="dot-typing" />
                <span className="dot-typing" />
              </div>
            </div>
          )}
        </div>

        {/* ---- Composer ---- */}
        <div className="composer">
          <textarea
            ref={inputRef}
            data-testid="chat-input"
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message…   (Enter to send, Shift+Enter for newline)"
            rows={1}
          />
          <button
            data-testid="send-button"
            className="send-button"
            onClick={() => send()}
            disabled={loading || !input.trim()}
            aria-label="Send message"
          >
            <SendIcon />
            <span>Send</span>
          </button>
        </div>
      </div>
    </div>
  )
}
