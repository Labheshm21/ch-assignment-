"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Metrics = {
  contacts: number;
  needs_review: number;
  qualified: number;
  rejected: number;
  campaigns: number;
  emails_sent: number;
  recipients_contacted: number;
  replies: number;
  interested: number;
  meetings: number;
  reply_rate: number;
  interest_rate: number;
  meeting_rate: number;
};

const emptyMetrics: Metrics = {
  contacts: 0, needs_review: 0, qualified: 0, rejected: 0, campaigns: 0,
  emails_sent: 0, recipients_contacted: 0, replies: 0, interested: 0,
  meetings: 0, reply_rate: 0, interest_rate: 0, meeting_rate: 0,
};

export default function DashboardPanel({ connected, workspaceId }: { connected: boolean; workspaceId: string | null }) {
  const [metrics, setMetrics] = useState<Metrics>(emptyMetrics);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!connected || !workspaceId) return;
    try {
      setMetrics(await api<Metrics>("/api/metrics"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load metrics");
    }
  }, [connected, workspaceId]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), 10_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [refresh]);

  if (!connected) {
    return <div className="empty-state contact-empty"><div>↗</div><h3>Connect Gmail to see pipeline metrics</h3><p>Your active workspace funnel will appear here.</p></div>;
  }

  const funnel = [
    ["Sourced", metrics.contacts],
    ["Contacted", metrics.recipients_contacted],
    ["Replied", metrics.replies],
    ["Interested", metrics.interested],
    ["Meetings", metrics.meetings],
  ] as const;

  return (
    <>
      <div className="dashboard-toolbar"><p>Workspace performance updates automatically.</p><button className="refresh-button" type="button" onClick={() => void refresh()}>Refresh</button></div>
      {error && <div className="error-banner">{error}</div>}
      <section className="metric-grid" aria-label="GTM metrics">
        <article><span>Contacts</span><strong>{metrics.contacts}</strong><small>{metrics.needs_review} need review</small></article>
        <article><span>Qualified</span><strong>{metrics.qualified}</strong><small>{metrics.rejected} rejected</small></article>
        <article><span>Campaigns</span><strong>{metrics.campaigns}</strong><small>{metrics.emails_sent} emails sent</small></article>
        <article><span>Replies</span><strong>{metrics.replies}</strong><small>{metrics.reply_rate}% reply rate</small></article>
      </section>

      <section className="funnel-card">
        <div className="funnel-head"><div><p className="eyebrow dark">CONVERSION FUNNEL</p><h3>From sourced to meeting</h3></div><span>{metrics.recipients_contacted} people contacted</span></div>
        <div className="funnel-list">
          {funnel.map(([label, value]) => {
            const width = metrics.contacts ? Math.max(value * 100 / metrics.contacts, value ? 5 : 0) : 0;
            return <div className="funnel-row" key={label}><span>{label}</span><div><i style={{ width: `${width}%` }} /></div><strong>{value}</strong></div>;
          })}
        </div>
        <div className="rate-grid"><div><strong>{metrics.reply_rate}%</strong><span>Reply rate</span></div><div><strong>{metrics.interest_rate}%</strong><span>Interest rate</span></div><div><strong>{metrics.meeting_rate}%</strong><span>Meeting rate</span></div></div>
      </section>
    </>
  );
}
