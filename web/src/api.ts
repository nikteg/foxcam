// Typed client for the media-streamer JSON API. All timestamps are ISO-8601 UTC ("...Z").

export type Rect = [x: number, y: number, w: number, h: number];

export interface Camera {
  id: string;
  name: string;
  source: "rtsp" | "tapo_cloud" | "test" | "file";
  webrtc_url: string;
  hls_url: string;
  /** Transcoded low-bitrate live stream (on demand); null when disabled on the server. */
  webrtc_low_url: string | null;
  hls_low_url: string | null;
  segment_seconds: number;
  online: boolean;
  online_since: string | null;
  readers: number;
  bytes_received: number | null;
  tracks: string[];
  last_segment_at: string | null;
  last_is_night: boolean | null;
  private: boolean;
  rects: Rect[];
  mask_version_id: number | null;
  source_config: CameraSource;
  managed: "config" | "ui";
  enabled: boolean;
  segment_seconds_override: number | null;
  /** Detection profiles running on this camera. */
  profiles: string[];
  /** ui = chosen in the web UI, config = config.yaml, default = profiles marked default. */
  profiles_source: "ui" | "config" | "default";
  recording_profile: string;
  recording_profile_source: "ui" | "config" | "default";
  /** Re-encoded by the server (recording profile and/or burned-in masks). */
  processed: boolean;
  burn_masks: boolean;
}

/** What to look for, and which workers may do it (see docs/ARCHITECTURE.md). */
export interface ProfileInput {
  id: string;
  name: string | null;
  targets: string[];
  aliases: Record<string, string>;
  min_confidence: number;
  requires: string[];
  fps: number | null;
  default: boolean;
  events_days: number | null;
  enabled: boolean;
}

export interface Profile extends ProfileInput {
  managed: "config" | "ui";
  display_name: string;
  cameras: string[];
  workers_online: string[];
  waiting_jobs: number;
}

export type CameraSource =
  | { type: "rtsp"; url: string }
  | {
      type: "tapo_cloud";
      device_id?: string | null;
      device_name?: string | null;
      resolution?: "HD" | "VGA";
    }
  | { type: "file"; path: string }
  | { type: "test" };

export interface ProbeResult {
  ok: boolean;
  detail: string;
  codec?: string;
  width?: number;
  height?: number;
}

export interface TapoStatus {
  available: boolean;
  connected: boolean;
  email: string | null;
  /** Signed in, but TP-Link rejected the refresh token: sign in again. */
  expired: boolean;
}

export type TapoLoginResult =
  | { status: "connected" }
  | { status: "mfa_required"; login_id: string; delivery: "email" | "app" };

export interface WorkerAuth {
  mode: "open" | "locked";
  shared_token: boolean;
  tokens: number;
}

export interface WorkerToken {
  id: number;
  name: string;
  detector: string | null;
  tags: string[];
  created_at: string;
  last_used_at: string | null;
}

export interface TapoDevice {
  device_id: string;
  name: string;
  model: string;
  camera_id: string | null;
}

export type JobState = "pending" | "leased" | "done" | "failed" | "dead";

export interface QueueStats {
  counts: Record<JobState, number>;
  oldest_pending_age_s: number | null;
  done_last_hour: number;
  avg_processing_ms_last_hour: number | null;
  /** Unfinished jobs per profile and state. */
  by_profile: Record<string, Partial<Record<JobState, number>>>;
}

export interface Status {
  mediamtx_reachable: boolean;
  cameras: Camera[];
  segments: Record<string, number>;
  recordings_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  events_24h: number;
  events_total: number;
  queue: QueueStats;
}

export interface Segment {
  id: number;
  camera_id: string;
  start_at: string;
  duration_s: number;
  size_bytes: number;
  status: "new" | "queued" | "done" | "private" | "failed";
  error: string | null;
  is_night: boolean | null;
  saturation_avg: number | null;
  sun_elevation: number | null;
  has_target: boolean;
  max_confidence: number | null;
  pinned: boolean;
  deleted: boolean;
  mask_version_id: number | null;
  /** Archived in S3 storage. */
  remote: boolean;
  thumb_url: string | null;
  download_url: string | null;
}

export interface Detection {
  id: number;
  segment_id: number;
  camera_id: string;
  profile_id: string | null;
  event_id: number | null;
  ts: string;
  offset_ms: number;
  label: string;
  confidence: number;
  bbox: Rect | null;
  is_target: boolean;
  model: string | null;
  worker_id: string | null;
  feedback: boolean | null;
  is_night: boolean | null;
}

export interface DetectionEvent {
  id: number;
  camera_id: string;
  profile_id: string | null;
  label: string;
  first_ts: string;
  last_ts: string;
  max_confidence: number;
  detection_count: number;
  is_night: boolean | null;
  clip_url: string | null;
  thumb_url: string | null;
  clip_duration_s: number | null;
  /** Clip uploaded to S3 storage. */
  remote: boolean;
  building: boolean;
  clip_error: string | null;
}

export interface Worker {
  id: string;
  info: Record<string, unknown>;
  tags: string[];
  last_seen: string;
  online: boolean;
  current_job_id: number | null;
  jobs_done: number;
  jobs_failed: number;
}

export interface Job {
  id: number;
  segment_id: number;
  camera_id: string;
  profile_id: string | null;
  segment_start: string;
  state: JobState;
  attempts: number;
  leased_by: string | null;
  lease_expires_at: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  model: string | null;
  processing_ms: number | null;
}

export interface QueueView {
  stats: QueueStats;
  workers: Worker[];
  jobs: Job[];
}

export interface MaskState {
  private: boolean;
  rects: Rect[];
  mask_version_id: number | null;
  /** Masks are also burned into recordings and live view. */
  burn_masks: boolean;
}

export interface RecordingProfile {
  id: string;
  name: string | null;
  display_name: string;
  transcode: boolean;
  codec: "h264" | "h265";
  max_height: number | null;
  fps: number | null;
  bitrate_kbit: number | null;
  keep_days: number | null;
  managed: "builtin" | "config";
  /** The profile cameras without their own choice use. */
  default: boolean;
  cameras: string[];
}

export type Settings = Record<string, unknown> & {
  segment_seconds: number;
  cameras: {
    id: string;
    name: string | null;
    source: Record<string, unknown>;
    segment_seconds: number | null;
  }[];
  detection: { target_labels: string[]; min_confidence: number };
  events: { pre_roll_seconds: number; post_roll_seconds: number };
};

export interface StorageStatus {
  enabled: boolean;
  boto3: boolean;
  bucket: string;
  endpoint_url: string | null;
  prefix: string;
  clips: boolean;
  recordings: boolean;
  delete_local_clips: boolean;
  recordings_days: number;
  clips_uploaded?: number;
  clips_pending?: number;
  recordings_uploaded?: number;
  recordings_pending?: number;
  last_error?: string | null;
}

export type ChannelType = "ntfy" | "webhook" | "apprise";
export type NotifyTrigger = "event.new" | "event.clip";

/** A notification channel as sent to the server (secrets: empty = keep stored value). */
export interface ChannelInput {
  id: string;
  name: string | null;
  type: ChannelType;
  enabled: boolean;
  when: NotifyTrigger[];
  cameras: string[] | null;
  profiles: string[] | null;
  labels: string[] | null;
  min_confidence: number;
  quiet_hours: string | null;
  cooldown_minutes: number;
  include_image: boolean;
  server: string;
  topic: string | null;
  token: string | null;
  username: string | null;
  password: string | null;
  priority: number;
  tags: string[];
  url: string | null;
  secret: string | null;
  headers: Record<string, string>;
}

/** As returned by the server: secrets replaced by has_* flags. */
export interface Channel extends Omit<ChannelInput, "token" | "password" | "secret"> {
  managed: "config" | "ui";
  display_name: string;
  has_token: boolean;
  has_password: boolean;
  has_secret: boolean;
}

export interface NotificationsOverview {
  channels: Channel[];
  public_url: string | null;
  timezone: string | null;
  apprise: boolean;
}

export interface Delivery {
  id: number;
  channel_id: string;
  event_id: number;
  camera_id: string;
  label: string | null;
  kind: NotifyTrigger;
  status: "pending" | "sent" | "failed" | "skipped";
  attempts: number;
  error: string | null;
  created_at: string;
  sent_at: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  status: () => request<Status>("GET", "/api/status"),
  settings: () => request<Settings>("GET", "/api/settings"),
  cameras: () => request<Camera[]>("GET", "/api/cameras"),
  mask: (cameraId: string) =>
    request<MaskState>("GET", `/api/cameras/${encodeURIComponent(cameraId)}/mask`),
  saveMask: (cameraId: string, body: { rects: Rect[]; private: boolean; burn_in?: boolean }) =>
    request<MaskState>("PUT", `/api/cameras/${encodeURIComponent(cameraId)}/mask`, body),
  segments: (q: { camera?: string; day?: string; only_targets?: boolean; limit?: number }) =>
    request<Segment[]>("GET", withQuery("/api/segments", q)),
  segment: (id: number) => request<Segment>("GET", `/api/segments/${id}`),
  detections: (q: {
    segment?: number;
    camera?: string;
    label?: string;
    only_targets?: boolean;
    min_confidence?: number;
    profile?: string;
    limit?: number;
  }) => request<Detection[]>("GET", withQuery("/api/detections", q)),
  feedback: (id: number, correct: boolean | null) =>
    request<{ status: string }>("POST", `/api/detections/${id}/feedback`, { correct }),
  events: (q: { camera?: string; profile?: string; limit?: number }) =>
    request<DetectionEvent[]>("GET", withQuery("/api/events", q)),
  queue: (q: { state?: string; limit?: number }) =>
    request<QueueView>("GET", withQuery("/api/queue", q)),
  addCamera: (body: {
    name: string;
    id?: string;
    source: CameraSource;
    segment_seconds?: number | null;
  }) => request<{ id: string }>("POST", "/api/cameras", body),
  updateCamera: (
    id: string,
    body: {
      name?: string;
      segment_seconds?: number;
      clear_segment_seconds?: boolean;
      enabled?: boolean;
    },
  ) => request<{ id: string }>("PATCH", `/api/cameras/${encodeURIComponent(id)}`, body),
  deleteCamera: (id: string) =>
    request<{ status: string }>("DELETE", `/api/cameras/${encodeURIComponent(id)}`),
  probeSource: (source: CameraSource) =>
    request<ProbeResult>("POST", "/api/cameras/probe", { source }),
  sampleFiles: () => request<string[]>("GET", "/api/cameras-sample-files"),
  tapoStatus: () => request<TapoStatus>("GET", "/api/integrations/tapo"),
  tapoLogin: (email: string, password: string) =>
    request<TapoLoginResult>("POST", "/api/integrations/tapo/login", { email, password }),
  tapoMfa: (login_id: string, code: string) =>
    request<TapoLoginResult>("POST", "/api/integrations/tapo/mfa", { login_id, code }),
  tapoDevices: () => request<TapoDevice[]>("GET", "/api/integrations/tapo/devices"),
  tapoLogout: () => request<{ status: string }>("POST", "/api/integrations/tapo/logout"),
  storage: () => request<StorageStatus>("GET", "/api/storage"),
  testStorage: () => request<ProbeResult>("POST", "/api/storage/test"),
  workerAuth: () => request<WorkerAuth>("GET", "/api/workers/auth"),
  workerTokens: () => request<WorkerToken[]>("GET", "/api/workers/tokens"),
  createWorkerToken: (name: string, detector: string, tags: string[] = []) =>
    request<WorkerToken & { token: string }>("POST", "/api/workers/tokens", {
      name,
      detector,
      tags,
    }),
  revokeWorkerToken: (id: number) =>
    request<{ status: string }>("DELETE", `/api/workers/tokens/${id}`),
  profiles: () => request<Profile[]>("GET", "/api/profiles"),
  recordingProfiles: () => request<RecordingProfile[]>("GET", "/api/recording-profiles"),
  setRecordingProfile: (cameraId: string, profile: string | null) =>
    request<{ recording_profile: string }>(
      "PUT",
      `/api/cameras/${encodeURIComponent(cameraId)}/recording-profile`,
      { profile },
    ),
  createProfile: (body: ProfileInput) => request<{ id: string }>("POST", "/api/profiles", body),
  updateProfile: (body: ProfileInput) =>
    request<{ id: string }>("PUT", `/api/profiles/${encodeURIComponent(body.id)}`, body),
  deleteProfile: (id: string) =>
    request<{ status: string; cancelled_jobs: number }>(
      "DELETE",
      `/api/profiles/${encodeURIComponent(id)}`,
    ),
  setCameraProfiles: (cameraId: string, profiles: string[] | null) =>
    request<{ profiles: string[] }>(
      "PUT",
      `/api/cameras/${encodeURIComponent(cameraId)}/profiles`,
      { profiles },
    ),
  workerTags: () => request<string[]>("GET", "/api/worker-tags"),
  setTokenTags: (id: number, tags: string[]) =>
    request<WorkerToken>("PUT", `/api/workers/tokens/${id}/tags`, { tags }),
  removeWorker: (id: string) =>
    request<{ status: string }>("DELETE", `/api/workers/${encodeURIComponent(id)}`),
  notifications: () => request<NotificationsOverview>("GET", "/api/notifications"),
  deliveries: () => request<Delivery[]>("GET", "/api/notifications/deliveries?limit=15"),
  createChannel: (body: ChannelInput) =>
    request<{ id: string }>("POST", "/api/notifications/channels", body),
  updateChannel: (body: ChannelInput) =>
    request<{ id: string }>(
      "PUT",
      `/api/notifications/channels/${encodeURIComponent(body.id)}`,
      body,
    ),
  deleteChannel: (id: string) =>
    request<{ status: string }>("DELETE", `/api/notifications/channels/${encodeURIComponent(id)}`),
  testChannel: (id: string) =>
    request<ProbeResult>("POST", `/api/notifications/channels/${encodeURIComponent(id)}/test`),
  requeue: (jobId: number) =>
    request<{ status: string }>("POST", `/api/queue/jobs/${jobId}/requeue`),
};

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** Live-view URLs are configured from the server's point of view (often localhost). When this
 * page was opened from another device, point them at the host the browser used instead. */
export function browserUrl(url: string): string {
  try {
    const u = new URL(url);
    if (LOCAL_HOSTS.has(u.hostname) && !LOCAL_HOSTS.has(window.location.hostname)) {
      u.hostname = window.location.hostname;
    }
    return u.toString();
  } catch {
    return url;
  }
}

/** ``source``: the camera's own picture, before burned-in masks (mask editor). */
export const snapshotUrl = (cameraId: string, fresh = false, source = false) => {
  const query = new URLSearchParams();
  if (fresh) {
    query.set("fresh", "true");
    query.set("t", String(Date.now()));
  }
  if (source) query.set("source", "true");
  const qs = query.toString();
  return `/api/cameras/${encodeURIComponent(cameraId)}/snapshot.jpg${qs ? `?${qs}` : ""}`;
};

export const segmentPlayUrl = (segmentId: number, offsetMs = 0) =>
  `/api/segments/${segmentId}/play${offsetMs ? `#t=${(offsetMs / 1000).toFixed(1)}` : ""}`;

export const detectionFrameUrl = (detectionId: number) =>
  `/api/detections/${detectionId}/frame.jpg`;
