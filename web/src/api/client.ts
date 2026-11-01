import { useEffect, useState } from "react";
import { applyTitle } from "../layout/tabTitle";
import { notifyFeed, notifyJob } from "../layout/notify";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import type {
  AgentActivityOut,
  AgentStatusOut,
  ApiErrorBody,
  BinCard,
  BinDetail,
  ClipBody,
  ClosePendingOut,
  CommandResultOut,
  ConnectStatusOut,
  EnhanceBody,
  EnhanceCancelOut,
  EnhanceOut,
  EnhanceStatusOut,
  ExportStatusOut,
  FeedEntry,
  FeedOut,
  Generation,
  HealthOut,
  JobOut,
  LoraFile,
  Media,
  MediaPage,
  MediaType,
  MessageFile,
  ProjectCard,
  ProjectDetail,
  ProjectSettingsBody,
  PublishStatusOut,
  Recipe,
  BinSeenOut,
  SeenOut,
  ShotDecision,
  RemoveUploadOut,
  SequenceOut,
  PostOut,
  UploadStatusOut,
  UploadOut,
  WorkflowInfo,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;
  extra: Record<string, unknown>;
  constructor(status: number, body: Partial<ApiErrorBody> | null) {
    super(body?.detail || body?.code || `HTTP ${status}`);
    this.status = status;
    this.code = body?.code ?? "http_error";
    this.detail = body?.detail ?? "";
    this.extra = body?.extra ?? {};
  }
}

/** One line for a toast or a hint, whatever was thrown. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.detail || err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

async function throwFor(r: Response): Promise<never> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await r.json()) as Partial<ApiErrorBody>;
  } catch {
    body = null;
  }
  throw new ApiError(r.status, body);
}

export async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) await throwFor(r);
  return (await r.json()) as T;
}

/** POST a JSON body as the user. Commands and decisions go through here. */
export async function postJson<T>(url: string, body: unknown, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "x-thm-actor": "user", ...headers },
    body: JSON.stringify(body),
  });
  if (!r.ok) await throwFor(r);
  return (await r.json()) as T;
}

export async function deleteJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "DELETE", headers: { Accept: "application/json", "x-thm-actor": "user" } });
  if (!r.ok) await throwFor(r);
  return (await r.json()) as T;
}

/** POST multipart as the user; the browser sets the boundary, so no Content-Type here. */
export async function postForm<T>(url: string, form: FormData): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { Accept: "application/json", "x-thm-actor": "user" }, body: form });
  if (!r.ok) await throwFor(r);
  return (await r.json()) as T;
}

export type ThumbSize = "grid" | "detail" | "poster";

const enc = encodeURIComponent;
export const mediaUrl = (slug: string, id: string) => `/media/${enc(slug)}/${enc(id)}/file`;
/** The original file as a download: saved under its own name and format (a GIF stays a GIF). */
export const downloadUrl = (slug: string, id: string) => `${mediaUrl(slug, id)}?download=1`;
/** A GIF plays only from the file itself; its thumbnails are a still of the first frame. */
export const isGif = (path: string | null | undefined) => /\.gif$/i.test(path ?? "");
export const thumbUrl = (slug: string, id: string, size: ThumbSize = "grid") => `/media/${enc(slug)}/${enc(id)}/thumb?size=${size}`;

/** The project list. No per-project event stream here, so it polls: quickly while any project
 * is generating (the cards count along), slowly otherwise (to notice one starting). */
export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: async () => {
      const cards = await getJson<ProjectCard[]>("/api/projects");
      applyTitle(null, cards.map((p) => p.generating_job)); // straight from the fetch: a hidden tab's timers are throttled
      return cards;
    },
    refetchInterval: (query) => (query.state.data?.some((p) => p.generating > 0) ? 2_000 : 10_000),
    refetchIntervalInBackground: true, // the home tab keeps its title honest while you are elsewhere
  });
}

export function useProject(slug: string | undefined) {
  return useQuery({
    queryKey: ["project", slug],
    queryFn: () => getJson<ProjectDetail>(`/api/projects/${enc(slug!)}`),
    enabled: !!slug,
  });
}

/** One bin: its recipe, its gallery (newest first), the selected media, references, capacity, active job. */
export function useBin(slug: string | undefined, binId: string | undefined) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["bin", slug, binId],
    queryFn: async () => {
      const detail = await getJson<BinDetail>(`/api/projects/${enc(slug!)}/bins/${enc(binId!)}`);
      // the bin being looked at is the freshest word on its new-take count: the explorer's card
      // for it (sidebar, project list) takes it from here, so the two never disagree on screen
      const unseen = detail.bin.unseen ?? [];
      const patch = (cards: BinCard[]) =>
        cards.map((c) => (c.id === detail.bin.id && !sameIds(c.unseen ?? [], unseen) ? { ...c, unseen } : c));
      qc.setQueryData<ProjectDetail>(["project", slug], (old) => (old ? { ...old, bins: patch(old.bins) } : old));
      qc.setQueryData<BinCard[]>(["bins", slug], (old) => (old ? patch(old) : old));
      return detail;
    },
    enabled: !!slug && !!binId,
  });
}

const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Every bin, newest first, with what it holds and the media it leads with: the explorer and the picker's filter. */
export function useBins(slug: string | undefined) {
  return useQuery({
    queryKey: ["bins", slug],
    queryFn: () => getJson<BinCard[]>(`/api/projects/${enc(slug!)}/bins`),
    enabled: !!slug,
  });
}

/** A one-off fetch for a freshly-created bin: `create_bin` answers with a project-level change,
 * not the new record's id, so the caller passes a name and finds the record by it. Not a hook. */
export function fetchBins(slug: string): Promise<BinCard[]> {
  return getJson<BinCard[]>(`/api/projects/${enc(slug)}/bins`);
}

export function fetchProject(slug: string): Promise<ProjectDetail> {
  return getJson<ProjectDetail>(`/api/projects/${enc(slug)}`);
}

/** Every workflow the server offers, with its kind; the settings form splits them. */
export function useWorkflows() {
  return useQuery({ queryKey: ["workflows"], queryFn: () => getJson<WorkflowInfo[]>("/api/workflows"), staleTime: 60_000 });
}

/** A bin detail as the `job` event keeps it: live jobs in `active_jobs`, and the last one to
 * finish in `finished_job` so the screen can say how it ended. A refetch drops the extra field. */
export type LiveBinDetail = BinDetail & { finished_job?: JobOut };

const lastVersion = new Map<string, number>();

/**
 * Refetch what a landed command can have changed, once per project version. The SSE `project`
 * event and every mutation response carry the version; whichever lands first does the work
 * and the other is a no-op, so a command costs one round of GETs rather than two, and a
 * dropped event stream still refreshes. Equality, not `<=`: versions restart at 1 when the
 * server does.
 */
export function refreshProject(qc: QueryClient, slug: string | undefined, version: number) {
  if (!slug || lastVersion.get(slug) === version) return;
  lastVersion.set(slug, version);
  void qc.invalidateQueries({ queryKey: ["project", slug] });
  void qc.invalidateQueries({ queryKey: ["bins", slug] });
  void qc.invalidateQueries({ queryKey: ["sequence", slug] });
  void qc.invalidateQueries({ queryKey: ["posts", slug] });
  void qc.invalidateQueries({ queryKey: ["bin", slug] });
  void qc.invalidateQueries({ queryKey: ["media", slug] });
  void qc.invalidateQueries({ queryKey: ["live-jobs", slug] });
  void qc.invalidateQueries({ queryKey: ["held", slug] });
  void qc.invalidateQueries({ queryKey: ["feed", slug] });
  void qc.invalidateQueries({ queryKey: ["projects"] }); // a rename or a new take shows in the switcher and on Home
}

/** The user has looked at a media: every bin stops marking it as new. A no-op on the server
 * when nothing marked it, so it is safe to call whenever something is shown large. */
export function useMarkSeen(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaId: string) => postJson<SeenOut>(`/api/projects/${enc(slug!)}/media/${enc(mediaId)}/seen`, {}),
    onSuccess: (out) => {
      if (!out.cleared.length) return;
      refreshProject(qc, slug, out.project_version);
    },
  });
}

/** The user dismissed every new marker in a bin, whatever the media type. */
export function useClearBinSeen(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (binId: string) => postJson<BinSeenOut>(`/api/projects/${enc(slug!)}/bins/${enc(binId)}/seen`, {}),
    onSuccess: (out) => {
      if (!out.cleared.length) return;
      refreshProject(qc, slug, out.project_version);
    },
  });
}

const isLive = (j: JobOut) => j.status === "queued" || j.status === "running";

/**
 * Every live render in the project, oldest first: the job list once, then kept current by
 * the `job` event and refetched on project events. The bin screen and the explorer draw
 * their placeholders and dots from this, never from a bin detail alone, so a refetch of the
 * detail mid-render cannot blink a placeholder away.
 */
export function useLiveJobs(slug: string | undefined) {
  return useQuery({
    queryKey: ["live-jobs", slug],
    queryFn: async () => (await getJson<JobOut[]>(`/api/projects/${enc(slug!)}/jobs`)).filter(isLive),
    enabled: !!slug,
    staleTime: 60_000,
  });
}

/** The live renders on one bin, oldest first, from the project's live list plus what the bin detail reports. */
export function liveJobsFor(binId: string | undefined, live: JobOut[] | undefined, fromDetail: JobOut[] | undefined): JobOut[] {
  const byId = new Map<string, JobOut>();
  for (const j of [...(fromDetail ?? []), ...(live ?? [])]) if (isLive(j) && j.bin === binId) byId.set(j.job, j);
  return [...byId.values()].sort((a, b) => a.started.localeCompare(b.started));
}

/** Stop a live generation: the runner is killed and the prompt dropped from ComfyUI's queue. */
export function useCancelJob(slug: string | undefined) {
  return useMutation({
    mutationFn: (genId: string) => postJson<JobOut>(`/api/projects/${enc(slug!)}/jobs/${enc(genId)}/cancel`, {}),
  });
}

/** Delete a project outright: its file, its files dir, every take and upload in it. */
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => deleteJson<{ deleted: string }>(`/api/projects/${enc(slug)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** Every LoRA file ComfyUI can load, for the picker behind the chip row. */
export function useLoras() {
  return useQuery({ queryKey: ["loras"], queryFn: () => getJson<LoraFile[]>("/api/loras"), staleTime: 60_000 });
}

/** Create a project: manual setup sends settings, the agent path sends the brief. */
export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; brief?: string; settings?: ProjectSettingsBody }) => postJson<ProjectDetail>("/api/projects", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useMedia(slug: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: ["media-one", slug, id],
    queryFn: () => getJson<Media>(`/api/projects/${enc(slug!)}/media/${enc(id!)}`),
    enabled: !!slug && !!id,
  });
}

/** The session feed: every message with its change and card media. Patched live by the `message` SSE event. */
export function useFeed(slug: string | undefined) {
  return useQuery({
    queryKey: ["feed", slug],
    queryFn: () => getJson<FeedOut>(`/api/projects/${enc(slug!)}/feed`),
    enabled: !!slug,
  });
}

/** Render a bin's recipe once. The server answers with the queued generation; the job's progress
 * arrives over SSE. With `hold` the recipe is snapshotted as a held generation instead and
 * rendered only when the machine's saved group is released (`useReleaseHeld`). */
export function useGenerate(slug: string | undefined, binId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ params = {}, hold = false }: { params?: Record<string, unknown>; hold?: boolean } = {}) =>
      postJson<CommandResultOut>(`/api/projects/${enc(slug!)}/commands/generate`, { bin: binId, params, hold }),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** Every generation saved for later in the project, in the order saved. Refetched on project events. */
export function useHeld(slug: string | undefined) {
  return useQuery({
    queryKey: ["held", slug],
    queryFn: () => getJson<Generation[]>(`/api/projects/${enc(slug!)}/generations?status=held`),
    enabled: !!slug,
    staleTime: 60_000,
  });
}

/** Start the machine's saved group: every held generation in every project goes to ComfyUI
 * through the ordinary queue, oldest save first. A GPU decision, not a project one. */
export function useReleaseHeld() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<JobOut[]>(`/api/held/release`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["held"] });
      void qc.invalidateQueries({ queryKey: ["live-jobs"] });
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** Drop one saved generation without rendering it. */
export function useDiscardHeld(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (genId: string) => postJson<Generation>(`/api/projects/${enc(slug!)}/held/${enc(genId)}/discard`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["held", slug] });
    },
  });
}

/** What the enhancer is doing right now, from the `enhance` SSE events. The local engine
 * carries the live fields a job carries (`phase`, `step`/`steps`...) with `status` running /
 * done / failed; the agent engine walks queued (waiting for the agent's watch) → seen (its
 * watch listed the request) → working (it picked it up) → done / failed, with `task` in words. */
export interface EnhanceProgress {
  status: "queued" | "seen" | "working" | "running" | "done" | "failed" | "cancelled";
  engine: "local" | "agent";
  request?: string | null;
  created?: string | null;
  task?: string | null;
  phase?: string | null;
  step?: number | null;
  steps?: number | null;
  pass_no?: number | null;
  passes?: number | null;
  queue_ahead?: number | null;
  queue_ahead_ours?: number | null;
}

export function useEnhanceProgress(slug: string | undefined) {
  return useQuery({
    queryKey: ["enhance-progress", slug],
    queryFn: () => null as EnhanceProgress | null, // fed by the event stream only
    enabled: !!slug,
    staleTime: Infinity,
  });
}

/** The agent's last sign of life on this project: fetched once, then fed by `agent` SSE
 * events on every call the agent makes. `doing` is its phrase ("reading your messages"). */
export function useAgentActivity(slug: string | undefined) {
  return useQuery({
    queryKey: ["agent-activity", slug],
    queryFn: () => getJson<AgentActivityOut>(`/api/projects/${enc(slug!)}/agent/activity`),
    enabled: !!slug,
    staleTime: Infinity,
  });
}

/** Seconds since the agent was last heard from, or null when it never was. Ticks every second
 * so "2s ago" stays honest while nothing arrives. */
export function useAgentIdle(activity: AgentActivityOut | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!activity?.seen) return null;
  const seenAt = Date.parse(activity.seen);
  if (Number.isNaN(seenAt)) return activity.idle_s ?? null;
  return Math.max(0, (now - seenAt) / 1000);
}

/** The agent counts as busy when it was heard from within this many seconds. */
export const AGENT_FRESH_S = 30;

/** Server-wide: is an agent CLI talking to this server at all. The system bar's `no agent`
 * pill versus its `start` control. Polled; there is no server-scoped event stream. */
/** The server's facts: version, folders, and the ComfyUI it renders on. Rarely changes. */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => getJson<HealthOut>("/api/health"),
    staleTime: 5 * 60_000,
  });
}

export function useAgentStatus() {
  return useQuery({
    queryKey: ["agent-status"],
    queryFn: () => getJson<AgentStatusOut>("/api/agent/status"),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}

/** Which prompt-enhancer engines can serve this project. Neither = the Enhance pill stays hidden. */
export function useEnhanceStatus(slug: string | undefined) {
  return useQuery({
    queryKey: ["enhance-status", slug],
    queryFn: () => getJson<EnhanceStatusOut>(`/api/projects/${enc(slug!)}/enhance/status`),
    enabled: !!slug,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Rewrite a prompt through the enhancer; the caller decides what to do with the text. The
 * body's `request` is the caller's name for the run: `useCancelEnhance` stops it by that name. */
export function useEnhance(slug: string | undefined) {
  return useMutation({
    mutationFn: (body: EnhanceBody) => postJson<EnhanceOut>(`/api/projects/${enc(slug!)}/enhance`, body),
  });
}

/** Stop a local rewrite in flight: the waiting `useEnhance` call fails with `enhance_cancelled`
 * and ComfyUI is told to drop the prompt. */
export function useCancelEnhance(slug: string | undefined) {
  return useMutation({
    mutationFn: (request: string) => postJson<EnhanceCancelOut>(`/api/projects/${enc(slug!)}/enhance/${enc(request)}/cancel`, {}),
  });
}

/** A name for one enhance run, unique enough for the server's in-flight table. Not
 * `crypto.randomUUID`: that needs a secure context, and the phone reaches THM over plain http. */
export function newRequestId(): string {
  return `enh_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

/** One sequence: its items joined to the takes that play, durations, the last export. */
export function useSequence(slug: string | undefined, seqId: string | undefined) {
  return useQuery({
    queryKey: ["sequence", slug, seqId],
    queryFn: () => getJson<SequenceOut>(`/api/projects/${enc(slug!)}/sequences/${enc(seqId!)}`),
    enabled: !!slug && !!seqId,
  });
}

/** Where the sequence's export stands; patched live by the `export` event. */
export function useExportStatus(slug: string | undefined, seqId: string | undefined) {
  return useQuery({
    queryKey: ["export", slug, seqId],
    queryFn: () => getJson<ExportStatusOut>(`/api/projects/${enc(slug!)}/sequences/${enc(seqId!)}/export`),
    enabled: !!slug && !!seqId,
  });
}

/** Render the sequence to one mp4; it lands in the output bin as the next take. */
export function useStartExport(slug: string | undefined, seqId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<ExportStatusOut>(`/api/projects/${enc(slug!)}/sequences/${enc(seqId!)}/export`, {}),
    onSuccess: (out) => qc.setQueryData<ExportStatusOut>(["export", slug, seqId], out),
  });
}

/** Any command as the user: start_session, end_session, post_message, and the rest. */
/** The posts drafted from one bin (a video with its title and description, held unpublished),
 * newest first. Written by the agent through the command route; the bin screen lists them. */
export function usePosts(slug: string | undefined, binId: string | undefined) {
  return useQuery({
    queryKey: ["posts", slug, binId],
    queryFn: () => getJson<PostOut[]>(`/api/projects/${enc(slug!)}/posts?bin=${enc(binId!)}`),
    enabled: !!slug && !!binId,
  });
}

/** Where a post's upload stands: fetched once while the post says `uploading`, then kept
 * current by the `post` event. */
export function useUploadStatus(slug: string | undefined, postId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["upload", slug, postId],
    queryFn: () => getJson<UploadStatusOut>(`/api/projects/${enc(slug!)}/posts/${enc(postId!)}/upload`),
    enabled: !!slug && !!postId && enabled,
  });
}

/** Can this server upload to YouTube, which channels are connected, and whether this browser is
 * on the server's machine (where a new channel's consent can finish). Read fresh each time the
 * settings open: connecting a channel changes it without a restart. */
export function usePublishStatus(enabled = true) {
  return useQuery({
    queryKey: ["publish", "youtube"],
    queryFn: () => getJson<PublishStatusOut>("/api/publish/youtube"),
    enabled,
    staleTime: 0,
  });
}

/** Where a project's channel consent stands; fetched once, then kept current by the
 * `youtube_connect` event. */
export function useConnectStatus(slug: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["connect", slug],
    queryFn: () => getJson<ConnectStatusOut>(`/api/projects/${enc(slug!)}/youtube/connect`),
    enabled: !!slug && enabled,
  });
}

/** Start the consent for a new channel: the answer carries Google's URL to open in this browser
 * (which must be on the server's machine); the channel lands as this project's when it comes back. */
export function useStartConnect(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<ConnectStatusOut>(`/api/projects/${enc(slug!)}/youtube/connect`, {}),
    onSuccess: (out) => qc.setQueryData<ConnectStatusOut>(["connect", slug], out),
  });
}

export function useCancelConnect(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteJson<ConnectStatusOut>(`/api/projects/${enc(slug!)}/youtube/connect`),
    onSuccess: (out) => qc.setQueryData<ConnectStatusOut>(["connect", slug], out),
  });
}

export function useCommand(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: unknown }) =>
      postJson<CommandResultOut>(`/api/projects/${enc(slug!)}/commands/${enc(name)}`, body),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/**
 * Set a bin's tags, chips first: the bin detail is patched before the request goes out so the
 * chip shows the moment it is typed, put back if the server refuses. The editor lowercases and
 * trims the way the server does, so the refetched chips are the ones already showing.
 */
export function useSetBinTags(slug: string | undefined, binId: string | undefined) {
  const qc = useQueryClient();
  const key = ["bin", slug, binId];
  return useMutation({
    mutationFn: (tags: string[]) =>
      postJson<CommandResultOut>(`/api/projects/${enc(slug!)}/commands/set_bin_field`, { bin: binId, field: "tags", value: tags }),
    onMutate: async (tags) => {
      await qc.cancelQueries({ queryKey: key }); // a refetch in flight must not land over the chips
      const previous = qc.getQueryData<LiveBinDetail>(key);
      qc.setQueryData<LiveBinDetail>(key, (old) => (old ? { ...old, bin: { ...old.bin, tags } } : old));
      return { previous };
    },
    onError: (_err, _tags, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous);
    },
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

export type DecisionAction = "approve" | "reject" | "reopen" | "close" | "reply";

/** Approve, reject, reopen, close or reply to an agent change. A note is a reply: the server
 * lands it `replied` -- nothing runs, nothing is undone, the agent reads the words and acts;
 * a bare approve is the go. `items` is the per-item verdict on a shots card (kept, dropped,
 * rewritten); the approval of that card makes the bins and queues the renders in one round
 * trip. Close says only "seen": no note, nothing owed. */
export function useDecide(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, note, items, hold, files }: { id: string; action: DecisionAction; note?: string; items?: ShotDecision[]; hold?: boolean; files?: string[] }) =>
      postJson<CommandResultOut>(`/api/projects/${enc(slug!)}/changes/${enc(id)}/decision`, {
        action,
        note: action === "close" ? null : note?.trim() || null,
        ...(items ? { items } : {}),
        ...(hold ? { hold: true } : {}), // a shots card answered "later": set up, render saved held
        ...(files?.length && action !== "close" ? { files } : {}), // chat files on the reply
      }),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** Close every card the session still owes, at once: seen, nothing said. One feed line. */
export function useClosePending(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<ClosePendingOut>(`/api/projects/${enc(slug!)}/changes/close-pending`, {}),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** Upload one file into a bin (by id) or into a named bin, `session-refs` by default. */
export function useUpload(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, bin, binName }: { file: File; bin?: string; binName?: string }) => {
      const form = new FormData();
      form.append("file", file, file.name);
      if (bin) form.append("bin", bin);
      if (binName) form.append("bin_name", binName);
      return postForm<UploadOut>(`/api/projects/${enc(slug!)}/uploads`, form);
    },
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** How a media was made, for recall into its bin. `found` is false for anything uploaded,
 * clipped or lifted as a still — there is no generation behind those, and the recall controls
 * stay out of the way rather than offering a button that cannot do anything. */
/** The recipe of a take (a media id) or of a render with no take yet (a generation id, `g_…`:
 * rendering now, or saved for later). */
export function useRecipe(slug: string | undefined, mediaId: string | null | undefined) {
  return useQuery({
    queryKey: ["recipe", slug, mediaId],
    queryFn: () =>
      getJson<Recipe>(
        mediaId!.startsWith("g_")
          ? `/api/projects/${enc(slug!)}/generations/${enc(mediaId!)}/recipe`
          : `/api/projects/${enc(slug!)}/media/${enc(mediaId!)}/recipe`,
      ),
    enabled: !!slug && !!mediaId,
    // what made a take never changes: read once per take per page load
    staleTime: Infinity,
  });
}

export interface UploadManyResult {
  uploaded: UploadOut[];
  failed: { name: string; error: string }[];
}

/**
 * Upload a set of files into one target. The route takes a single file, so this is one request
 * per file, in order.
 *
 * The first upload settles the destination: a `binName` mints the bin, and every file after
 * it follows that bin's id, so a five-file drop lands in one bin rather than five. A file that
 * fails does not stop the rest — the caller gets both lists and reports once.
 */
export function useUploadMany(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      files,
      bin,
      binName,
      onProgress,
    }: {
      files: File[];
      bin?: string;
      binName?: string;
      onProgress?: (done: number, total: number) => void;
    }): Promise<UploadManyResult> => {
      const uploaded: UploadOut[] = [];
      const failed: { name: string; error: string }[] = [];
      let targetBin = bin;
      let targetName = binName;
      for (const file of files) {
        const form = new FormData();
        form.append("file", file, file.name);
        if (targetBin) form.append("bin", targetBin);
        else if (targetName) form.append("bin_name", targetName);
        try {
          const res = await postForm<UploadOut>(`/api/projects/${enc(slug!)}/uploads`, form);
          uploaded.push(res);
          targetBin = res.bin.id;
          targetName = undefined;
        } catch (e) {
          failed.push({ name: file.name, error: errorText(e) });
        }
        onProgress?.(uploaded.length + failed.length, files.length);
      }
      return { uploaded, failed };
    },
    // each upload's own `project` event refreshed as it landed; the last version covers a dropped stream
    onSuccess: (out) => {
      const last = out.uploaded[out.uploaded.length - 1];
      if (last) refreshProject(qc, slug, last.project_version);
    },
  });
}

/** One line for a batch of uploads: what landed, and what did not. */
export function uploadSummary(result: UploadManyResult, into?: string): string {
  const n = result.uploaded.length;
  const where = into ? ` → ${into}` : "";
  const landed = n === 1 ? `uploaded 1 file${where}` : `uploaded ${n} files${where}`;
  if (!result.failed.length) return landed;
  const first = result.failed[0];
  if (!n) return result.failed.length === 1 ? `${first.name} failed — ${first.error}` : `all ${result.failed.length} failed — ${first.error}`;
  return `${landed} · ${result.failed.length} failed (${first.name}: ${first.error})`;
}

/** Take an upload back out of a bin; the server deletes it when nothing else uses it. */
export function useRemoveUpload(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ media, bin }: { media: string; bin?: string }) =>
      deleteJson<RemoveUploadOut>(`/api/projects/${enc(slug!)}/uploads/${enc(media)}${bin ? `?bin=${enc(bin)}` : ""}`),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** A note's own file: a pasted screenshot, a picture from elsewhere. In no bin, not project media. */
export const chatFileUrl = (slug: string, id: string) => `/api/projects/${enc(slug)}/chat-files/${enc(id)}`;

/** Upload a file for a note alone. Nothing in the project changes; the id goes in `post_message`'s `files`. */
export function uploadChatFile(slug: string, file: File): Promise<MessageFile> {
  const form = new FormData();
  form.append("file", file, file.name);
  return postForm<MessageFile>(`/api/projects/${enc(slug)}/chat-files`, form);
}

/** Take back a note's file that was never sent. Best effort: a file a message carries stays. */
export function removeChatFile(slug: string, id: string): Promise<unknown> {
  return deleteJson(chatFileUrl(slug, id)).catch(() => undefined);
}

/** Make a note's file project media: it lands in a bin like an upload. */
export function useKeepFile(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, bin, binName }: { file: string; bin?: string; binName?: string }) =>
      postJson<UploadOut>(`${chatFileUrl(slug!, file)}/keep`, { bin: bin ?? null, bin_name: binName ?? null }),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** Cut a segment or lift a still frame from an existing video; lands like an upload, no bind. */
export function useCreateClip(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ClipBody) => postJson<UploadOut>(`/api/projects/${enc(slug!)}/clips`, body),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

/** A horizontally flipped copy of a still, as new media in the bin (selected on arrival). */
export function useFlipMedia(slug: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ media, bin }: { media: string; bin?: string }) =>
      postJson<UploadOut>(`/api/projects/${enc(slug!)}/media/${enc(media)}/flip`, { bin: bin ?? null }),
    onSuccess: (out) => refreshProject(qc, slug, out.project_version),
  });
}

export interface MediaPageParams {
  bin?: string;
  type?: MediaType;
  cursor?: string;
  limit?: number;
}

/**
 * The whole library, a page at a time. One page is all `useMediaPage` ever asks for, which
 * silently hides everything past the server's page size — fine for a fixed list, not for a
 * grid you browse. The key keeps the `["media", slug]` prefix so an SSE project bump still
 * invalidates it along with everything else.
 */
export function useMediaPages(slug: string | undefined, params: MediaPageParams = {}, enabled = true) {
  return useInfiniteQuery({
    queryKey: ["media", slug, "pages", params],
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams();
      if (params.bin) qs.set("bin", params.bin);
      if (params.type) qs.set("type", params.type);
      if (pageParam) qs.set("cursor", String(pageParam));
      qs.set("limit", String(params.limit ?? 60));
      return getJson<MediaPage>(`/api/projects/${enc(slug!)}/media?${qs}`);
    },
    initialPageParam: "",
    getNextPageParam: (last: MediaPage) => last.next_cursor ?? undefined,
    enabled: !!slug && enabled,
  });
}

export function useMediaPage(slug: string | undefined, params: MediaPageParams = {}, enabled = true) {
  const qs = new URLSearchParams();
  if (params.bin) qs.set("bin", params.bin);
  if (params.type) qs.set("type", params.type);
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs}` : "";
  return useQuery({
    queryKey: ["media", slug, params],
    queryFn: () => getJson<MediaPage>(`/api/projects/${enc(slug!)}/media${suffix}`),
    enabled: !!slug && enabled,
  });
}

/**
 * Subscribe to the project's event feed and refetch what it touches. Reconnects on its own.
 * `project` events invalidate the queries; `job` events patch the bin's `active_jobs` and the
 * explorer cards' `generating` in place;
 * `message` events append to the feed (and patch the card of the change they carry), then
 * the project refetch keeps the strip's counts current.
 */
export function useProjectEvents(slug: string | undefined) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!slug || typeof EventSource === "undefined") return;
    const es = new EventSource(`/api/projects/${enc(slug)}/events`);
    // a (re)connected stream may have missed events, and a restarted server counts versions from
    // 1 again, so an old remembered version can match a new one and skip a refresh: start over
    es.onopen = () => {
      lastVersion.delete(slug);
      void qc.invalidateQueries({ queryKey: ["project", slug] });
      void qc.invalidateQueries({ queryKey: ["bins", slug] });
      void qc.invalidateQueries({ queryKey: ["bin", slug] });
    };
    const parse = <T,>(ev: Event): T | null => {
      try {
        return JSON.parse((ev as MessageEvent).data as string) as T;
      } catch {
        return null;
      }
    };
    const onJob = (ev: Event) => {
      const job = parse<JobOut>(ev);
      if (!job) return;
      const live = isLive(job);
      const nextLive = (old: JobOut[] | undefined) => {
        const rest = (old ?? []).filter((j) => j.job !== job.job);
        return live ? [...rest, job] : rest;
      };
      const wasLive = (qc.getQueryData<JobOut[]>(["live-jobs", slug]) ?? []).some((j) => j.job === job.job);
      const liveNow = nextLive(qc.getQueryData<JobOut[]>(["live-jobs", slug]));
      qc.setQueryData<JobOut[]>(["live-jobs", slug], liveNow);
      applyTitle(slug, liveNow, live ? null : job); // straight from the stream: a hidden tab's timers are throttled, its events are not
      if (!live && wasLive)
        notifyJob(
          { slug, project: qc.getQueryData<ProjectDetail>(["project", slug]), bins: qc.getQueryData<BinCard[]>(["bins", slug]), feed: qc.getQueryData<FeedOut>(["feed", slug])?.entries },
          job,
        );
      qc.setQueryData<LiveBinDetail>(["bin", slug, job.bin], (old) => {
        if (!old) return old;
        const rest = (old.active_jobs ?? []).filter((j) => j.job !== job.job);
        return live
          ? { ...old, active_jobs: [...rest, job].sort((a, b) => a.started.localeCompare(b.started)) }
          : { ...old, active_jobs: rest, finished_job: job };
      });
      const stamp = (cards: BinCard[]) =>
        cards.map((c) => {
          if (c.id !== job.bin) return c;
          const ids = (c.generating ?? []).filter((id) => id !== job.job);
          return { ...c, generating: live ? [...ids, job.job] : ids };
        });
      qc.setQueryData<ProjectDetail>(["project", slug], (old) => (old ? { ...old, bins: stamp(old.bins) } : old));
      qc.setQueryData<BinCard[]>(["bins", slug], (old) => (old ? stamp(old) : old));
      // a landed job's takes arrive with the versioned `project` event the server sends just
      // before this one; a failed or cancelled job changes nothing the patches above missed
    };
    const onProject = (ev: Event) => {
      const d = parse<{ version: number }>(ev);
      if (d) refreshProject(qc, slug, d.version);
    };
    const onMessage = (ev: Event) => {
      const entry = parse<FeedEntry>(ev);
      if (!entry) return;
      notifyFeed({ slug, project: qc.getQueryData<ProjectDetail>(["project", slug]), bins: qc.getQueryData<BinCard[]>(["bins", slug]) }, entry);
      qc.setQueryData<FeedOut>(["feed", slug], (old) => {
        if (!old) return old;
        const seen = old.entries.some((e) => e.message.id === entry.message.id);
        const change = entry.change;
        // a shots card's items and a card's media move with its change: the approval writes both
        let entries = change
          ? old.entries.map((e) => (e.change && e.change.id === change.id ? { ...e, change, shots: entry.shots ?? e.shots, media: entry.media ?? e.media, bin: entry.bin ?? e.bin, render: entry.render ?? e.render } : e))
          : old.entries;
        if (!seen) entries = [...entries, entry];
        return { entries, cursor: seen ? old.cursor : old.cursor + 1 };
      });
      // the `project` event that always follows a message refreshes the strip's counts
    };
    const onEnhance = (ev: Event) => {
      const p = parse<EnhanceProgress>(ev);
      if (p) qc.setQueryData<EnhanceProgress | null>(["enhance-progress", slug], p);
    };
    // a sequence export's progress and its landing: patched in place, then the sequence refetched
    // once it is done so the last-export card and the output bin read the new take
    const onExport = (ev: Event) => {
      const x = parse<ExportStatusOut>(ev);
      if (!x) return;
      qc.setQueryData<ExportStatusOut>(["export", slug, x.sequence], x);
      if (x.status !== "running") void qc.invalidateQueries({ queryKey: ["sequence", slug, x.sequence] });
    };
    // a post's upload: progress patched in place, the posts refetched once it lands or fails so
    // the strip reads the record (uploaded with its Studio link, or failed with the reason)
    const onPostUpload = (ev: Event) => {
      const u = parse<UploadStatusOut>(ev);
      if (!u) return;
      qc.setQueryData<UploadStatusOut>(["upload", slug, u.post], u);
      if (u.status !== "running") void qc.invalidateQueries({ queryKey: ["posts", slug] });
    };
    const onAgent = (ev: Event) => {
      const a = parse<AgentActivityOut>(ev);
      if (a) qc.setQueryData<AgentActivityOut>(["agent-activity", slug], a);
    };
    // a channel consent: patched in place; once it lands, the server's channel list and this
    // project's detail (its new channel) are read again
    const onConnect = (ev: Event) => {
      const c = parse<ConnectStatusOut>(ev);
      if (!c) return;
      qc.setQueryData<ConnectStatusOut>(["connect", slug], c);
      if (c.status !== "waiting") {
        void qc.invalidateQueries({ queryKey: ["publish", "youtube"] });
        void qc.invalidateQueries({ queryKey: ["project", slug] });
      }
    };
    es.addEventListener("youtube_connect", onConnect);
    es.addEventListener("project", onProject);
    es.addEventListener("job", onJob);
    es.addEventListener("enhance", onEnhance);
    es.addEventListener("export", onExport);
    es.addEventListener("post", onPostUpload);
    es.addEventListener("agent", onAgent);
    es.addEventListener("message", onMessage);
    return () => {
      es.removeEventListener("project", onProject);
      es.removeEventListener("job", onJob);
      es.removeEventListener("enhance", onEnhance);
      es.removeEventListener("export", onExport);
      es.removeEventListener("post", onPostUpload);
      es.removeEventListener("youtube_connect", onConnect);
      es.removeEventListener("agent", onAgent);
      es.removeEventListener("message", onMessage);
      es.close();
    };
  }, [slug, qc]);
}
