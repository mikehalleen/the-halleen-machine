import type { BinCard, FeedEntry, JobOut, ProjectDetail } from "../api/types";

/**
 * System notifications (the Notification API) for the moments worth leaving another tab for:
 * a generation landing or failing, and an agent card that needs a verdict (or a blocker).
 *
 * - Permission is asked on the first Generate, a user gesture, never on load.
 * - Only fires while the app is not in front (hidden tab, or window unfocused); in front, the
 *   UI itself is the notice.
 * - A private project never notifies: its pictures stay off the desktop.
 * - Each notification carries the media's thumbnail as its picture, when there is one, and a
 *   click brings the tab forward on that bin.
 * - Desktop browsers and Android Chrome show these while the page is open. iOS Safari does not
 *   support the Notification API for a web page; push through a service worker (an installed
 *   PWA over HTTPS) is the route there, not built yet.
 */

const supported = typeof window !== "undefined" && "Notification" in window;

export function notificationsSupported(): boolean {
  return supported;
}

export function notificationsGranted(): boolean {
  return supported && Notification.permission === "granted";
}

/** Ask once, from a user gesture. Resolves true when granted (now or earlier). */
export async function requestNotifications(): Promise<boolean> {
  if (!supported) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  try {
    return (await Notification.requestPermission()) === "granted";
  } catch {
    return false;
  }
}

function inFront(): boolean {
  return document.visibilityState === "visible" && document.hasFocus();
}

function show(title: string, body: string, opts: { tag: string; icon?: string; url?: string; force?: boolean }) {
  if (!notificationsGranted() || (inFront() && !opts.force)) return;
  try {
    const n = new Notification(title, { body, tag: opts.tag, icon: opts.icon, badge: "/icon-512.png", silent: false });
    n.onclick = () => {
      window.focus();
      if (opts.url && location.pathname !== opts.url) location.assign(opts.url);
      n.close();
    };
  } catch {
    /* some embedders throw on construction; nothing to do */
  }
}

/** What the settings screen reports, and a test the user can fire while looking at the tab. */
export function notificationState(): "unsupported" | "insecure" | "granted" | "denied" | "default" {
  if (!supported) return "unsupported";
  if (!window.isSecureContext) return "insecure";
  return Notification.permission;
}

/** Fires a test and reports what the browser did with it, for the settings screen's toast. */
export async function sendTestNotification(): Promise<string> {
  if (!supported) return "this browser has no Notification API";
  if (!window.isSecureContext) return "blocked: not a secure context (https or localhost)";
  if (!(await requestNotifications())) return `blocked: permission is ${Notification.permission}`;
  return new Promise((resolve) => {
    try {
      const n = new Notification("THM 2.0 — test", { body: "notifications reach you here", tag: "test", icon: "/icon-512.png" });
      const timer = setTimeout(() => resolve("sent, but the browser never reported showing it — check Windows notification settings for Chrome"), 3000);
      n.onshow = () => {
        clearTimeout(timer);
        resolve("shown");
      };
      n.onerror = () => {
        clearTimeout(timer);
        resolve("the browser refused to show it (onerror)");
      };
    } catch (e) {
      resolve(`failed: ${(e as Error).message}`);
    }
  });
}

const thumb = (slug: string, id: string) => `/media/${encodeURIComponent(slug)}/${encodeURIComponent(id)}/thumb?size=detail`;
const binUrl = (slug: string, bin: string) => `/p/${encodeURIComponent(slug)}/b/${encodeURIComponent(bin)}`;

export interface NotifyContext {
  slug: string;
  project: ProjectDetail | undefined;
  bins: BinCard[] | undefined;
}

function binName(ctx: NotifyContext, binId: string): string {
  return ctx.bins?.find((b) => b.id === binId)?.name ?? ctx.project?.bins.find((b) => b.id === binId)?.name ?? "a bin";
}

function allowed(ctx: NotifyContext): boolean {
  return !!ctx.project && !ctx.project.private;
}

/** A job just left the live set: done (with its takes) or failed / cancelled. */
export function notifyJob(ctx: NotifyContext, job: JobOut) {
  if (!allowed(ctx)) return;
  const name = binName(ctx, job.bin);
  const project = ctx.project?.name ?? ctx.slug;
  if (job.status === "done") {
    const n = job.takes.length;
    show(`${name} — ${n === 1 ? "take landed" : `${n} takes landed`}`, project, {
      tag: `job:${job.job}`,
      icon: job.takes[0] ? thumb(ctx.slug, job.takes[0]) : undefined,
      url: binUrl(ctx.slug, job.bin),
    });
  } else if (job.status === "failed") {
    show(`${name} — generation failed`, (job.error ?? "").split("\n")[0] || project, { tag: `job:${job.job}`, url: binUrl(ctx.slug, job.bin) });
  }
}

/** A feed line arrived: a card owing a verdict, or a blocker from the agent. */
export function notifyFeed(ctx: NotifyContext, entry: FeedEntry) {
  if (!allowed(ctx)) return;
  const project = ctx.project?.name ?? ctx.slug;
  const change = entry.change;
  if (change?.decision && (change.decision.status === "pending" || change.decision.status === "proposed") && change.actor === "agent") {
    const bin = entry.bin?.id ?? (change.target.type === "bin" ? change.target.id : null);
    show(`agent — needs your ok`, change.description || entry.message.text, {
      tag: `change:${change.id}`,
      icon: entry.media ? thumb(ctx.slug, entry.media.id) : undefined,
      url: bin ? binUrl(ctx.slug, bin) : `/p/${encodeURIComponent(ctx.slug)}`,
    });
    return;
  }
  if (entry.message.kind === "blocker") {
    show(`agent — ${project}`, entry.message.text, { tag: `msg:${entry.message.id}`, url: `/p/${encodeURIComponent(ctx.slug)}` });
  }
}
