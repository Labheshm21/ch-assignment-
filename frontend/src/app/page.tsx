"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type AuthStatus = { connected: boolean; email: string | null };

type EmailIntent = {
  action: "schedule_email" | "needs_clarification";
  recipient: string | null;
  subject: string | null;
  body: string | null;
  scheduled_at: string | null;
  count: number;
  interval_minutes: number;
  clarification_message: string | null;
};

type ScheduledEmail = {
  id: string;
  recipient: string;
  subject: string;
  body: string;
  scheduled_at: string;
  original_timezone: string;
  status: "pending" | "processing" | "sent" | "failed" | "cancelled";
  retry_count: number;
  last_error: string | null;
  gmail_message_id: string | null;
  sent_at: string | null;
  created_at: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const examples = [
  "Send an email to alex@example.com tomorrow at 9 AM saying: Great meeting you. Subject: Following up",
  "Send 3 emails to me@example.com, one every hour starting at 1 PM. Subject: Delivery test. Body: Scheduled check-in.",
];

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(payload.detail ?? `Request failed with ${response.status}`);
  }
  return response.json();
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export default function Home() {
  const [auth, setAuth] = useState<AuthStatus>({ connected: false, email: null });
  const [prompt, setPrompt] = useState("");
  const [conversationContext, setConversationContext] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [intent, setIntent] = useState<EmailIntent | null>(null);
  const [jobs, setJobs] = useState<ScheduledEmail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);

  const refresh = useCallback(async () => {
    try {
      const status = await api<AuthStatus>("/auth/status");
      setAuth(status);
      if (status.connected) setJobs(await api<ScheduledEmail[]>("/api/emails"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load the app");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  async function analyze(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setIntent(null);
    try {
      const parsed = await api<EmailIntent>("/api/intents/parse", {
        method: "POST",
        body: JSON.stringify({ message: prompt, timezone }),
      });
      setIntent(parsed);
      setConversationContext(parsed.action === "needs_clarification" ? `Original request: ${prompt}` : "");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not understand that request");
    } finally {
      setLoading(false);
    }
  }

  async function answerClarification(event: FormEvent) {
    event.preventDefault();
    if (!followUp.trim() || !intent) return;
    setLoading(true);
    setError(null);
    try {
      const combined = `${conversationContext}\nAssistant asked: ${intent.clarification_message}\nUser answered: ${followUp}`;
      const parsed = await api<EmailIntent>("/api/intents/parse", {
        method: "POST",
        body: JSON.stringify({ message: combined, timezone }),
      });
      setIntent(parsed);
      setConversationContext(parsed.action === "needs_clarification" ? combined : "");
      setFollowUp("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not understand that answer");
    } finally {
      setLoading(false);
    }
  }

  async function confirmSchedule() {
    if (!intent?.recipient || !intent.subject || !intent.body || !intent.scheduled_at) return;
    setLoading(true);
    setError(null);
    try {
      await api<{ jobs: ScheduledEmail[] }>("/api/emails/schedule", {
        method: "POST",
        body: JSON.stringify({
          recipient: intent.recipient,
          subject: intent.subject,
          body: intent.body,
          scheduled_at: intent.scheduled_at,
          timezone,
          count: intent.count,
          interval_minutes: intent.interval_minutes,
        }),
      });
      setPrompt("");
      setIntent(null);
      setConversationContext("");
      setFollowUp("");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not schedule the email");
    } finally {
      setLoading(false);
    }
  }

  async function cancelJob(id: string) {
    setError(null);
    try {
      await api<ScheduledEmail>(`/api/emails/${id}`, { method: "DELETE" });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not cancel that email");
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark">C</div>
          <p className="eyebrow">CHRONOS RELAY</p>
          <h1>Email on your time.</h1>
          <p className="muted intro">Describe the message once. Relay handles the clock, retries, and delivery from the cloud.</p>
        </div>

        <div className="connection-card">
          <div className="connection-row">
            <span className={`status-dot ${auth.connected ? "online" : ""}`} />
            <div>
              <strong>{auth.connected ? "Gmail connected" : "Connect your inbox"}</strong>
              <p>{auth.email ?? "Required before scheduling"}</p>
            </div>
          </div>
          {!auth.connected ? (
            <a className="button button-light full" href={`${API_URL}/auth/google`}>Connect Gmail</a>
          ) : (
            <button className="text-button" onClick={async () => {
              await api("/auth/logout", { method: "POST" });
              setAuth({ connected: false, email: null });
              setJobs([]);
            }}>Disconnect</button>
          )}
        </div>

        <div className="sidebar-foot"><span>Timezone</span><strong>{timezone.replaceAll("_", " ")}</strong></div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div><p className="eyebrow dark">COMMAND CENTER</p><h2>What should happen next?</h2></div>
          <div className="cloud-badge"><span /> Cloud worker active</div>
        </header>

        <section className="composer-card">
          <div className="assistant-line"><div className="avatar">AI</div><p>Tell me who to email, what to say, and when to send it.</p></div>
          <form onSubmit={analyze}>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder='Try “Send Maya a follow-up tomorrow at 9 AM...”' rows={4} maxLength={4000} />
            <div className="composer-actions">
              <span>{prompt.length}/4000</span>
              <button className="button button-primary" disabled={!auth.connected || loading || !prompt.trim()} type="submit">
                {loading ? "Thinking…" : "Review schedule →"}
              </button>
            </div>
          </form>
          {!auth.connected && <p className="hint">Connect Gmail to analyze and schedule a request.</p>}
          <div className="example-row">
            <span>Try an example</span>
            {examples.map((example, index) => <button key={example} onClick={() => setPrompt(example)}>{index === 0 ? "One email" : "Hourly batch"}</button>)}
          </div>
        </section>

        {error && <div className="error-banner">{error}</div>}

        {intent?.action === "needs_clarification" && (
          <section className="clarification-card">
            <p className="eyebrow dark">ONE DETAIL NEEDED</p>
            <h3>{intent.clarification_message ?? "Please add the missing email details."}</h3>
            <form onSubmit={answerClarification}>
              <input
                aria-label="Follow-up answer"
                value={followUp}
                onChange={(event) => setFollowUp(event.target.value)}
                placeholder="Type your answer…"
                maxLength={1000}
              />
              <button className="button button-primary" disabled={loading || !followUp.trim()} type="submit">
                {loading ? "Thinking…" : "Continue →"}
              </button>
            </form>
          </section>
        )}

        {intent?.action === "schedule_email" && intent.scheduled_at && (
          <section className="review-card">
            <div className="review-head"><div><p className="eyebrow dark">READY FOR CONFIRMATION</p><h3>{intent.count > 1 ? `${intent.count} emails` : "1 email"}</h3></div><span className="review-time">{formatTime(intent.scheduled_at)}</span></div>
            <dl>
              <div><dt>To</dt><dd>{intent.recipient}</dd></div>
              <div><dt>Subject</dt><dd>{intent.subject}</dd></div>
              <div><dt>Message</dt><dd>{intent.body}</dd></div>
              {intent.count > 1 && <div><dt>Cadence</dt><dd>Every {intent.interval_minutes} minutes</dd></div>}
            </dl>
            <div className="review-actions"><button className="text-button dark-text" onClick={() => setIntent(null)}>Edit request</button><button className="button button-primary" disabled={loading} onClick={confirmSchedule}>Confirm schedule</button></div>
          </section>
        )}

        <section className="queue-section">
          <div className="section-title"><div><p className="eyebrow dark">DELIVERY QUEUE</p><h2>Scheduled emails</h2></div><button className="refresh-button" onClick={() => void refresh()}>Refresh</button></div>
          <div className="queue-list">
            {jobs.length === 0 ? (
              <div className="empty-state"><div>⌁</div><h3>No emails scheduled yet</h3><p>Your confirmed requests will appear here and continue running in the cloud.</p></div>
            ) : jobs.map((job) => (
              <article className="queue-item" key={job.id}>
                <div className={`queue-icon ${job.status}`}>{job.status === "sent" ? "✓" : "↗"}</div>
                <div className="queue-main">
                  <div className="queue-meta"><strong>{job.subject}</strong><span className={`pill ${job.status}`}>{job.status}</span></div>
                  <p>To {job.recipient}</p><small>{formatTime(job.scheduled_at)} · {job.original_timezone}</small>
                  {job.last_error && <small className="job-error">{job.last_error}</small>}
                </div>
                {job.status === "pending" && <button className="cancel-button" onClick={() => void cancelJob(job.id)}>Cancel</button>}
              </article>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}
