// Typed client for the foxcam JSON API. All timestamps are ISO-8601 UTC ("...Z").

export type Rect = [x: number, y: number, w: number, h: number];

export type CameraRole = "viewer" | "editor" | "owner";

export interface Camera {
  id: string;
  name: string;
  source: "rtsp" | "tapo_cloud" | "test" | "file";
  /** What the signed-in user may do with this camera (admins: owner of everything). */
  role: CameraRole;
  /** Live view, through the server (behind the login). */
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
  /** null = the default (Settings → Detection). */
  motion_sensitivity: MotionSensitivity | null;
  /** Areas whose movement is ignored. */
  motion_rects: Rect[];
}

export type MotionSensitivity = "low" | "medium" | "high";

/** What to look for, and which workers may do it (see docs/ARCHITECTURE.md). */
/** One model looking for a profile's targets (one job per segment and detector). */
export interface DetectorInput {
  id: string;
  name: string | null;
  /** Worker tags this detector needs, on top of the profile's `requires`. */
  requires: string[];
  /** Overrides of the profile's values; null = the profile's. */
  min_confidence: number | null;
  fps: number | null;
  /** Cascade: on_hit runs only when detector `after` found a target in the segment. */
  run: "always" | "on_hit";
  after: string | null;
  /** on_hit only: the profile's events count once this detector agrees. */
  confirm: boolean;
}

export interface Detector extends DetectorInput {
  display_name: string;
  workers_online: string[];
}

export interface ProfileInput {
  id: string;
  name: string | null;
  targets: string[];
  aliases: Record<string, string>;
  min_confidence: number;
  /** Targets with a match threshold of their own (wins over the detector's and the profile's). */
  target_min_confidence: Record<string, number>;
  /** Tags every detector needs. */
  requires: string[];
  /** Empty = one detector, `default`. */
  detectors: DetectorInput[];
  fps: number | null;
  default: boolean;
  events_days: number | null;
  /** A target standing still (no motion under it) this long stops counting. 0 = it must
   * be moving right now; null = off. */
  still_minutes: number | null;
  /** Recordings without motion get no jobs of this profile. */
  skip_without_motion: boolean;
  enabled: boolean;
}

export interface Profile extends ProfileInput {
  detectors: Detector[];
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

/** read: look (GET, always); write: change what the user may; admin: admin powers. */
export type ApiKeyScope = "read" | "write" | "admin";

export interface ApiKey {
  id: number;
  name: string;
  /** The first characters, e.g. "msk_Ab12cd". */
  prefix: string;
  scopes: ApiKeyScope[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
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

export type JobState = "pending" | "leased" | "done" | "failed" | "dead" | "skipped";

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
  /** The compact process (hourly files); admins only, null for others. */
  compaction: CompactionStatus | null;
  /** Detection workers; admins only. */
  workers: { total: number; online: number; busy: number } | null;
  /** Recordings waiting for the server to prepare them; admins only (no list). */
  preprocessing: PreprocessingStatus | null;
}

export interface CompactionRun {
  camera_id: string;
  start_at: string;
  segments: number;
}

export interface CompactionStatus {
  enabled: boolean;
  period_minutes: number;
  /** The compact process sent a heartbeat recently. */
  running: boolean;
  heartbeat_at: string | null;
  host: string | null;
  current: (CompactionRun & { reencode: boolean; profile: string; started_at: string }) | null;
  last_built:
    | (CompactionRun & {
        encoder: string;
        bytes_before: number;
        bytes_after: number;
        seconds: number;
        at: string;
      })
    | null;
  last_error: string | null;
  last_error_at: string | null;
  archives: number;
  joined_segments: number;
  waiting_periods: number;
  waiting_segments: number;
  /** Periods waiting to be archived, oldest first (at most 50). */
  waiting_list: { camera_id: string; start_at: string; segments: number }[];
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
  /** Largest share of the picture that moved in one frame (0 = none, null = not measured). */
  motion_score: number | null;
  /** Where things moved: only from api.segment (one recording). */
  motion_boxes?: MotionFrame[] | null;
  /** Archived in S3 storage. */
  remote: boolean;
  /** Joined into an hourly file by compaction. */
  archived: boolean;
  archive_id: number | null;
  thumb_url: string | null;
  download_url: string | null;
}

/** One detection run (job) of a segment: a profile, and what it found. */
export interface SegmentJob {
  id: number;
  profile_id: string | null;
  profile_name: string | null;
  detector_id: string | null;
  detector_name: string | null;
  state: JobState;
  model: string | null;
  worker_id: string | null;
  attempts: number;
  error: string | null;
  processing_ms: number | null;
  finished_at: string | null;
  detections: number;
  targets: number;
  /** Admins only: what the worker posted (``{truncated, bytes, text}`` when too big). */
  raw_result?: unknown;
}

/** One row of GET /api/models/stats: a profile's detector running one model. */
/** One frame with motion: its time in the recording and boxes around what moved. */
export type MotionFrame = [offsetMs: number, boxes: Rect[]];

/** Detections split by the correct / wrong marks. */
export interface Rated {
  correct: number;
  wrong: number;
  unrated: number;
}

/** What the motion options would do on one camera and profile (shadow mode). */
export interface MotionReportRow {
  camera_id: string;
  profile_id: string;
  profile_name: string;
  /** The profile's own setting (null = off). */
  still_minutes: number | null;
  /** The minutes `standing_still` is counted for (the default when the profile has it off). */
  still_minutes_tried: number;
  skip_without_motion: boolean;
  recordings: number;
  /** With motion measured. */
  measured: number;
  /** Measured, and nothing moved. */
  without_motion: number;
  /** Targets in measured recordings. */
  targets: Rated;
  /** Targets in recordings where nothing moved: skipping would miss them. */
  in_still: Rated;
  /** Targets standing still for `still_minutes_tried`: no match then. */
  standing_still: Rated;
}

export interface MotionBackfill {
  running: boolean;
  done: number;
  total: number;
  failed: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface MotionReport {
  rows: MotionReportRow[];
  /** Recordings in the period without a motion measurement. */
  unmeasured: number;
  backfill: MotionBackfill;
}

export interface ModelStats {
  profile_id: string;
  profile_name: string;
  detector_id: string;
  detector_name: string;
  model: string | null;
  detections: number;
  targets: number;
  feedback_correct: number;
  feedback_wrong: number;
  /** Correct / rated, from feedback; null = nothing rated. */
  precision: number | null;
  jobs_done: number;
  jobs_dead: number;
  jobs_skipped: number;
  avg_processing_ms: number | null;
  /** Recordings where it found a target and another detector of the profile looked. */
  segments_with_target: number;
  /** ... of which another detector found one too. */
  segments_agreed: number;
  agreement_rate: number | null;
}

export interface DetectionExtra {
  detector_label?: string;
  detector_conf?: number;
  raw_label?: string;
  classifications?: { label: string; score: number }[];
  [key: string]: unknown;
}

/** An hourly file made by compaction (Recordings → Hourly files). */
export interface ArchiveFile {
  id: number;
  camera_id: string;
  start_at: string;
  duration_s: number;
  size_bytes: number;
  /** Recording profile it was encoded with; "original" = joined without re-encoding. */
  profile_id: string | null;
  /** "copy" or the video encoder, e.g. hevc_videotoolbox. */
  encoder: string | null;
  segments: number;
  first_segment_id: number | null;
  segments_with_match: number;
  /** Recordings where something moved. */
  segments_with_motion: number;
  /** Thumbnail of its first segment that has one. */
  thumb_url: string | null;
  deleted: boolean;
  remote: boolean;
  play_url: string | null;
  download_url: string | null;
}

/** GET /api/labels: suggestions for label pickers (admins). */
export interface LabelSuggestions {
  workers: {
    id: string;
    detector: string | null;
    tags: string[];
    online: boolean;
    last_seen: string;
    /** Prompted with any noun (YOLO-World / YOLOE): can report anything. */
    open_vocabulary: boolean;
    /** What a closed-set model can report; null = not reported (or open vocabulary). */
    vocabulary: string[] | null;
  }[];
  open_vocabulary: { wildlife: string[]; lvis: string[]; objects365: string[] };
  seen: {
    label: string;
    count: number;
    max_confidence: number;
    last_seen: string | null;
    targets: number;
  }[];
  /** Words a detector treats as the same thing (prompting several splits confidence). */
  synonym_groups: string[][];
}

export interface Detection {
  id: number;
  segment_id: number;
  camera_id: string;
  profile_id: string | null;
  detector_id: string | null;
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
  /** Standing still for the profile's still_minutes: no match. */
  still: boolean;
  /** The box overlapped motion at that moment (null: not measured). */
  moving: boolean | null;
  /** Detector specifics. SpeciesNet: `detector_label`/`detector_conf` (the box) and
   *  `classifications`, the classifier's top guesses. */
  extra: DetectionExtra;
}

export interface DetectionEvent {
  id: number;
  /** Its most confident detection (the frame, box and feedback in the detection drawer). */
  best_detection_id: number | null;
  /** From its detections' feedback: wrong = one marked wrong and none correct. */
  feedback: "correct" | "wrong" | null;
  camera_id: string;
  profile_id: string | null;
  label: string;
  first_ts: string;
  last_ts: string;
  max_confidence: number;
  detection_count: number;
  /** The profile's detectors (models) that saw it, and how many. */
  detectors: string[];
  agreement: number;
  /** False while the profile's confirming detector has not seen it. */
  confirmed: boolean;
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
  detector_id: string | null;
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

/** What Settings → Danger zone can delete (server: api.DangerScope). */
export type DangerScope =
  | "recordings"
  | "detections"
  | "queue"
  | "workers"
  | "profiles"
  | "cameras";

/** A recording waiting for (or in) preprocessing on the server. */
export interface PreprocessingSegment {
  id: number;
  camera_id: string;
  start_at: string;
  /** Set while it is being prepared. */
  started_at: string | null;
}

/** Recordings the server prepares before they get detection jobs. */
export interface PreprocessingStatus {
  waiting: number;
  oldest_waiting_age_s: number | null;
  preparing: number;
  /** At most this many at once (Settings: processing.concurrency). */
  concurrency: number;
  done_last_hour: number;
  failed_last_hour: number;
  /** Recordings whose preparation failed (all time). */
  failed: number;
  avg_seconds_last_hour: number | null;
  /** The ones being prepared, then the waiting ones in the order they are prepared. */
  segments: PreprocessingSegment[];
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
  motion_sensitivity: MotionSensitivity | null;
  motion_rects: Rect[];
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
  /** Where the default is set: "ui" (Recording dialog) or "config" (config.yaml). */
  default_source: "ui" | "config" | null;
  cameras: string[];
}

export type Settings = Record<string, unknown> & {
  segment_seconds: number;
  /** config.yaml's default recording profile (the UI can override it). */
  recording_profile_default: string;
  cameras: {
    id: string;
    name: string | null;
    source: Record<string, unknown>;
    segment_seconds: number | null;
  }[];
  detection: { target_labels: string[]; min_confidence: number };
  events: { pre_roll_seconds: number; post_roll_seconds: number };
};

/** A tunable admins can change on the Settings page (server: runtime_settings.py). */
export interface RuntimeSetting {
  /** Its path in config.yaml, e.g. "processing.sample_fps". */
  key: string;
  value: number | boolean | string | null;
  config_value: number | boolean | string | null;
  /** default = built in, config = config.yaml, ui = set on the Settings page. */
  source: "default" | "config" | "ui";
  /** Where a reset goes back to. */
  config_source: "default" | "config";
  nullable: boolean;
  ge?: number;
  gt?: number;
  le?: number;
  lt?: number;
}
export interface RuntimeSettings {
  settings: RuntimeSetting[];
  disk: { recordings_bytes: number; disk_free_bytes: number; disk_total_bytes: number };
}

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
  /** Only once this many detectors saw the event. */
  min_agreement: number;
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

export interface User {
  id: number;
  email: string;
  name: string | null;
  display_name: string;
  is_admin: boolean;
  disabled: boolean;
  has_password: boolean;
  /** Linked OAuth providers (ids). */
  providers: string[];
  created_at: string;
  last_login_at: string | null;
}

export interface AuthProvider {
  id: string;
  name: string;
  type: "google" | "microsoft" | "github" | "oidc";
}

export interface AuthState {
  /** False: no logins (auth.enabled: false), everyone is an admin. */
  enabled: boolean;
  setup_required: boolean;
  password_login: boolean;
  registration: "invite" | "open" | "closed";
  providers: AuthProvider[];
  user: User | null;
  signed_in: boolean;
  is_admin: boolean;
  can_add_cameras: boolean;
}

export interface CameraMember {
  user_id: number;
  email: string;
  name: string | null;
  display_name: string;
  role: CameraRole;
  created_at: string;
}

export interface Invite {
  id: number;
  cameras: { camera_id: string; role: CameraRole }[];
  is_admin: boolean;
  note: string | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by: number | null;
  state: "open" | "used" | "expired";
  created_by: number | null;
  created_by_name: string | null;
}

export interface InvitePreview {
  from: string | null;
  is_admin: boolean;
  note: string | null;
  expires_at: string;
  cameras: { camera_id: string; role: CameraRole; name: string }[];
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

let onUnauthorized: () => void = () => {};

/** Called when the session has ended (401), so the app can show the sign-in page. */
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized();
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
  authState: () => request<AuthState>("GET", "/api/auth/state"),
  login: (email: string, password: string) =>
    request<AuthState>("POST", "/api/auth/login", { email, password }),
  setup: (body: { email: string; password: string; name: string; code: string }) =>
    request<AuthState>("POST", "/api/auth/setup", body),
  register: (body: { email: string; password: string; name: string; invite?: string }) =>
    request<AuthState>("POST", "/api/auth/register", body),
  logout: () => request<{ status: string }>("POST", "/api/auth/logout"),
  updateMe: (name: string) => request<User>("PATCH", "/api/auth/me", { name }),
  changePassword: (current: string | null, next: string) =>
    request<{ status: string }>("POST", "/api/auth/password", { current, new: next }),
  unlinkProvider: (id: string) =>
    request<{ status: string }>("DELETE", `/api/auth/providers/${encodeURIComponent(id)}`),
  apiKeys: () => request<ApiKey[]>("GET", "/api/auth/api-keys"),
  createApiKey: (body: { name: string; scopes: ApiKeyScope[]; expires_days: number | null }) =>
    request<ApiKey & { key: string }>("POST", "/api/auth/api-keys", body),
  revokeApiKey: (id: number) => request<{ status: string }>("DELETE", `/api/auth/api-keys/${id}`),
  invitePreview: (token: string) =>
    request<InvitePreview>("GET", `/api/auth/invite/${encodeURIComponent(token)}`),
  acceptInvite: (token: string) =>
    request<{ cameras: string[] }>("POST", `/api/auth/invite/${encodeURIComponent(token)}/accept`),
  users: () => request<User[]>("GET", "/api/users"),
  createUser: (body: { email: string; name: string; password: string | null; is_admin: boolean }) =>
    request<User>("POST", "/api/users", body),
  updateUser: (id: number, body: { name?: string; is_admin?: boolean; disabled?: boolean }) =>
    request<User>("PATCH", `/api/users/${id}`, body),
  setUserPassword: (id: number, password: string) =>
    request<{ status: string }>("PUT", `/api/users/${id}/password`, { password }),
  deleteUser: (id: number) => request<{ status: string }>("DELETE", `/api/users/${id}`),
  directory: () =>
    request<{ id: number; email: string; display_name: string }[]>("GET", "/api/users/directory"),
  cameraAccess: (cameraId: string) =>
    request<{ members: CameraMember[]; invites: Invite[] }>(
      "GET",
      `/api/cameras/${encodeURIComponent(cameraId)}/access`,
    ),
  share: (cameraId: string, email: string, role: CameraRole) =>
    request<{ user_id: number }>("POST", `/api/cameras/${encodeURIComponent(cameraId)}/access`, {
      email,
      role,
    }),
  setShareRole: (cameraId: string, userId: number, role: CameraRole) =>
    request<{ role: CameraRole }>(
      "PUT",
      `/api/cameras/${encodeURIComponent(cameraId)}/access/${userId}`,
      { role },
    ),
  unshare: (cameraId: string, userId: number) =>
    request<{ status: string }>(
      "DELETE",
      `/api/cameras/${encodeURIComponent(cameraId)}/access/${userId}`,
    ),
  invites: () => request<Invite[]>("GET", "/api/invites"),
  createInvite: (body: {
    cameras: { camera_id: string; role: CameraRole }[];
    is_admin?: boolean;
    note?: string;
    days?: number;
  }) => request<Invite & { token: string; path: string }>("POST", "/api/invites", body),
  deleteInvite: (id: number) => request<{ status: string }>("DELETE", `/api/invites/${id}`),
  status: () => request<Status>("GET", "/api/status"),
  settings: () => request<Settings>("GET", "/api/settings"),
  cameras: () => request<Camera[]>("GET", "/api/cameras"),
  mask: (cameraId: string) =>
    request<MaskState>("GET", `/api/cameras/${encodeURIComponent(cameraId)}/mask`),
  saveMask: (cameraId: string, body: { rects: Rect[]; private: boolean; burn_in?: boolean }) =>
    request<MaskState>("PUT", `/api/cameras/${encodeURIComponent(cameraId)}/mask`, body),
  saveMotion: (cameraId: string, body: { sensitivity: MotionSensitivity | null; rects: Rect[] }) =>
    request<MaskState>("PUT", `/api/cameras/${encodeURIComponent(cameraId)}/motion`, body),
  segments: (q: {
    camera?: string;
    day?: string;
    only_targets?: boolean;
    status?: string;
    /** yes = something moved, no = nothing did. */
    motion?: "yes" | "no";
    limit?: number;
  }) => request<Segment[]>("GET", withQuery("/api/segments", q)),
  segment: (id: number) => request<Segment>("GET", `/api/segments/${id}`),
  segmentJobs: (id: number) => request<SegmentJob[]>("GET", `/api/segments/${id}/jobs`),
  /** A link to one file that works for 10 minutes without signing in (Safari, iOS app). */
  downloadLink: (path: string) => request<{ url: string }>("POST", "/api/download-links", { path }),
  /** Detection again on every recording starting in [start, end) (ISO times). */
  reanalyseRange: (body: { camera?: string; start: string; end: string }) =>
    request<{ queued: number; skipped: number; detections_removed: number }>(
      "POST",
      "/api/segments/reanalyse",
      body,
    ),
  /** Settings → Danger zone: delete one kind of data, keep the rest (admins). */
  dangerReset: (scope: DangerScope) =>
    request<{ status: string; message: string }>("POST", `/api/admin/reset/${scope}`),
  /** Every event again from the detections (admins); rebuilt events notify nobody. */
  rebuildEvents: () =>
    request<{ removed: number; created: number; still: number }>(
      "POST",
      "/api/admin/rebuild-events",
    ),
  reanalyse: (id: number) =>
    request<{ status: string; detections_removed: number }>(
      "POST",
      `/api/segments/${id}/reanalyse`,
    ),
  archiving: () => request<CompactionStatus>("GET", "/api/archiving"),
  archives: (q: { camera?: string; day?: string; limit?: number }) =>
    request<ArchiveFile[]>("GET", withQuery("/api/archives", q)),
  detections: (q: {
    segment?: number;
    camera?: string;
    label?: string;
    only_targets?: boolean;
    min_confidence?: number;
    profile?: string;
    detector?: string;
    /** An event's own detections. */
    event?: number;
    /** Leave out objects standing still (they make no event). */
    hide_still?: boolean;
    limit?: number;
  }) => request<Detection[]>("GET", withQuery("/api/detections", q)),
  feedback: (id: number, correct: boolean | null) =>
    request<{ status: string }>("POST", `/api/detections/${id}/feedback`, { correct }),
  events: (q: {
    camera?: string;
    profile?: string;
    min_agreement?: number;
    /** Leave out events marked wrong. */
    hide_wrong?: boolean;
    limit?: number;
  }) => request<DetectionEvent[]>("GET", withQuery("/api/events", q)),
  motionReport: (days: number) =>
    request<MotionReport>("GET", withQuery("/api/motion/report", { days })),
  motionBackfill: (days: number) =>
    request<MotionBackfill & { started: boolean }>("POST", "/api/motion/backfill", { days }),
  modelStats: (days?: number) =>
    request<ModelStats[]>("GET", withQuery("/api/models/stats", { days })),
  queue: (q: { state?: string; limit?: number }) =>
    request<QueueView>("GET", withQuery("/api/queue", q)),
  preprocessing: (limit?: number) =>
    request<PreprocessingStatus>("GET", withQuery("/api/preparation", { limit })),
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
      /** Tapo cloud cameras: the relay's stream quality. */
      tapo_resolution?: "HD" | "VGA";
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
  runtimeSettings: () => request<RuntimeSettings>("GET", "/api/settings/runtime"),
  /** New values and keys to reset (back to config.yaml's value). */
  setRuntimeSettings: (body: { values?: Record<string, unknown>; reset?: string[] }) =>
    request<RuntimeSettings>("PUT", "/api/settings/runtime", body),
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
  setDefaultRecordingProfile: (profile: string | null) =>
    request<{ default: string; default_source: "ui" | "config" }>(
      "PUT",
      "/api/recording-profiles/default",
      { profile },
    ),
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
  labels: () => request<LabelSuggestions>("GET", "/api/labels"),
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

/** The frame with its box. The recording and moment are in the URL: a re-analysed
 *  recording's detections can reuse the ids of the ones they replaced. */
export const detectionFrameUrl = (
  d: Pick<Detection, "id" | "segment_id" | "offset_ms">,
  size: "full" | "small" = "full",
) =>
  `/api/detections/${d.id}/frame.jpg?at=${d.segment_id}-${d.offset_ms}` +
  (size === "small" ? "&size=small" : "");
