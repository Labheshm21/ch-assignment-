"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import ContactsPanel from "./contacts-panel";
import CampaignsPanel from "./campaigns-panel";
import { API_URL, api } from "@/lib/api";

type AuthStatus = {
  connected: boolean;
  email: string | null;
  workspace_id: string | null;
  workspace_name: string | null;
  reply_monitoring_enabled: boolean;
};

type Workspace = {
  id: string;
  name: string;
  role: "owner" | "admin" | "member";
  created_at: string;
};

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

const examples = [
  "Send an email to alex@example.com tomorrow at 9 AM saying: Great meeting you. Subject: Following up",
  "Send 3 emails to me@example.com, one every hour starting at 1 PM. Subject: Delivery test. Body: Scheduled check-in.",
];

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export default function Home() {
  const [auth, setAuth] = useState<AuthStatus>({ connected: false, email: null, workspace_id: null, workspace_name: null, reply_monitoring_enabled: false });
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [showWorkspaceForm, setShowWorkspaceForm] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [conversationContext, setConversationContext] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [intent, setIntent] = useState<EmailIntent | null>(null);
  const [jobs, setJobs] = useState<ScheduledEmail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"scheduler" | "campaigns" | "contacts">("scheduler");
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);

  const refresh = useCallback(async () => {
    try {
      const status = await api<AuthStatus>("/auth/status");
      setAuth(status);
      if (status.connected) {
        const [emailJobs, availableWorkspaces] = await Promise.all([
          api<ScheduledEmail[]>("/api/emails"),
          api<Workspace[]>("/api/workspaces"),
        ]);
        setJobs(emailJobs);
        setWorkspaces(availableWorkspaces);
      } else {
        setJobs([]);
        setWorkspaces([]);
      }
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

  async function switchWorkspace(workspaceId: string) {
    if (!workspaceId || workspaceId === auth.workspace_id) return;
    setWorkspaceBusy(true);
    setError(null);
    try {
      await api<Workspace>(`/api/workspaces/${workspaceId}/select`, { method: "POST" });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch workspace");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function createClientWorkspace(event: FormEvent) {
    event.preventDefault();
    if (!workspaceName.trim()) return;
    setWorkspaceBusy(true);
    setError(null);
    try {
      await api<Workspace>("/api/workspaces", {
        method: "POST",
        body: JSON.stringify({ name: workspaceName }),
      });
      setWorkspaceName("");
      setShowWorkspaceForm(false);
      setActiveView("contacts");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create workspace");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark">R</div>
          <p className="eyebrow">RELAY AI</p>
          <h1>Email on your time.</h1>
          <p className="muted intro">Describe the message once. Relay handles the clock, retries, and delivery from the cloud.</p>
        </div>

        <nav className="sidebar-nav" aria-label="Product navigation">
          <button className={activeView === "scheduler" ? "active" : ""} onClick={() => setActiveView("scheduler")}><span>↗</span>Scheduler</button>
          <button className={activeView === "campaigns" ? "active" : ""} onClick={() => setActiveView("campaigns")}><span>◫</span>Campaigns</button>
          <button className={activeView === "contacts" ? "active" : ""} onClick={() => setActiveView("contacts")}><span>◎</span>Contacts</button>
        </nav>

        {auth.connected && (
          <section className="workspace-switcher">
            <label htmlFor="active-workspace">Active client</label>
            <div className="workspace-select-wrap">
              <span>{(auth.workspace_name || "W").charAt(0).toUpperCase()}</span>
              <select
                id="active-workspace"
                value={auth.workspace_id ?? ""}
                disabled={workspaceBusy}
                onChange={(event) => void switchWorkspace(event.target.value)}
              >
                {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
              </select>
            </div>
            {!showWorkspaceForm ? (
              <button className="add-workspace-button" type="button" onClick={() => setShowWorkspaceForm(true)}>+ Add client workspace</button>
            ) : (
              <form className="workspace-create-form" onSubmit={createClientWorkspace}>
                <input
                  autoFocus
                  value={workspaceName}
                  maxLength={160}
                  placeholder="Client company name"
                  onChange={(event) => setWorkspaceName(event.target.value)}
                />
                <div>
                  <button type="button" onClick={() => { setShowWorkspaceForm(false); setWorkspaceName(""); }}>Cancel</button>
                  <button type="submit" disabled={workspaceBusy || workspaceName.trim().length < 2}>Create</button>
                </div>
              </form>
            )}
          </section>
        )}

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
              setAuth({ connected: false, email: null, workspace_id: null, workspace_name: null, reply_monitoring_enabled: false });
              setWorkspaces([]);
              setJobs([]);
            }}>Disconnect</button>
          )}
        </div>

        <div className="sidebar-foot"><span>Timezone</span><strong>{timezone.replaceAll("_", " ")}</strong></div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div><p className="eyebrow dark">{activeView === "scheduler" ? "COMMAND CENTER" : activeView === "campaigns" ? "CAMPAIGNS" : "CONTACTS"}</p><h2>{activeView === "scheduler" ? "What should happen next?" : activeView === "campaigns" ? "Reach the right people." : "Who are you reaching?"}</h2></div>
          <div className="cloud-badge"><span /> Cloud worker active</div>
        </header>

        {error && <div className="error-banner">{error}</div>}

        {activeView === "scheduler" ? <>
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
        </> : activeView === "campaigns" ? <CampaignsPanel key={auth.workspace_id ?? "disconnected"} connected={auth.connected} workspaceId={auth.workspace_id} replyMonitoringEnabled={auth.reply_monitoring_enabled} /> : <ContactsPanel key={auth.workspace_id ?? "disconnected"} connected={auth.connected} workspaceId={auth.workspace_id} />}
      </section>
    </main>
  );
}
