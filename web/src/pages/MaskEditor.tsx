import {
  Button,
  Caption1,
  Card,
  Field,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Select,
  Spinner,
  Switch,
  Toolbar,
  ToolbarButton,
  ToolbarDivider,
} from "@fluentui/react-components";
import {
  Add20Regular,
  ArrowClockwise20Regular,
  ArrowRedo20Regular,
  ArrowReset20Regular,
  ArrowUndo20Regular,
  Delete20Regular,
  DeleteDismiss20Regular,
  Dismiss24Regular,
  Live20Regular,
  MoreHorizontal20Regular,
  Save20Regular,
  WeatherMoon16Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

import {
  api,
  recordedSnapshotUrl,
  snapshotUrl,
  type MaskState,
  type MotionSensitivity,
  type Rect,
} from "../api";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";
import { ConfirmPopover } from "../components/ConfirmPopover";
import { FrameImage } from "../components/FrameImage";
import { localWeekdayTime } from "../format";
import { DrawerTitle, QueryState, Tag } from "../components/ui";
import { useI18n } from "../i18n";
import { useMediaQuery } from "../useMediaQuery";

/** Normalized (0..1) rectangle; independent of the stream resolution. */
interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Corner = "nw" | "ne" | "sw" | "se";
const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];

// The pointer's plan lives in the ref (not state) so rapid pointer events never see a
// stale selection between renders. Nothing changes until it has moved a little
// (`started`): a tap only selects.
type Drag = { start: { x: number; y: number }; started: boolean } & (
  | { mode: "draw"; ax: number; ay: number }
  | { index: number; mode: "move"; dx: number; dy: number }
  | {
      index: number;
      mode: "resize";
      ax: number;
      ay: number;
      // From the finger to the grabbed corner.
      gx: number;
      gy: number;
      created: boolean;
    }
);

const MIN_SIZE = 0.01;
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const toBox = ([x, y, w, h]: Rect): Box => ({ x, y, w, h });
const toRect = (b: Box): Rect => [b.x, b.y, b.w, b.h];
const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

interface Size {
  width: number;
  height: number;
}

/** How far (px) a box's handles sit outside it: a box smaller than `min` keeps its
 *  middle free for moving. */
function handleOffset(b: Box, size: Size, min: number) {
  return {
    x: Math.max(0, (min - b.w * size.width) / 2),
    y: Math.max(0, (min - b.h * size.height) / 2),
  };
}

/** What a press at `p` takes hold of, measured in screen pixels with room for a finger:
 *  the selected box's handles, then the selected box, then the smallest box near it
 *  (a small box inside a big one stays reachable). */
function hitTest(
  boxes: Box[],
  selected: number,
  p: { x: number; y: number },
  size: Size,
  touch: boolean,
  handleOut: number,
): { index: number; corner?: Corner } | null {
  const slop = touch ? 16 : 4;
  const reach = touch ? 24 : 8;
  const px = p.x * size.width;
  const py = p.y * size.height;
  const inside = (b: Box, pad: number) =>
    px >= b.x * size.width - pad &&
    px <= (b.x + b.w) * size.width + pad &&
    py >= b.y * size.height - pad &&
    py <= (b.y + b.h) * size.height + pad;
  const box = boxes[selected];
  if (box) {
    const out = handleOffset(box, size, handleOut);
    // A small box's handles are outside it: a press on the box itself moves it.
    const pushed = out.x > 0 || out.y > 0;
    if (!(pushed && inside(box, 0))) {
      let best: { corner: Corner; dist: number } | undefined;
      for (const corner of CORNERS) {
        const west = corner.includes("w");
        const north = corner.includes("n");
        const cx = (west ? box.x : box.x + box.w) * size.width + (west ? -out.x : out.x);
        const cy = (north ? box.y : box.y + box.h) * size.height + (north ? -out.y : out.y);
        const dist = Math.hypot(px - cx, py - cy);
        if (dist <= reach && (!best || dist < best.dist)) best = { corner, dist };
      }
      if (best) return { index: selected, corner: best.corner };
    }
    if (inside(box, slop)) return { index: selected };
  }
  let found = -1;
  boxes.forEach((b, i) => {
    const best = boxes[found];
    if (inside(b, slop) && (!best || b.w * b.h < best.w * best.h)) found = i;
  });
  return found >= 0 ? { index: found } : null;
}

/** The size of an element on screen, kept up to date. */
function useElementSize(ref: RefObject<HTMLElement | null>): Size {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() =>
      setSize({ width: el.clientWidth, height: el.clientHeight }),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/** What the editor edits: the censor areas (blacked out for the workers) or motion
 * detection (its sensitivity and the areas whose movement is ignored). */
export type MaskKind = "censor" | "motion";

/** Censor areas or motion settings of a camera, in a large drawer. Closing with unsaved
 * changes asks first, in a popover at the close button (also for Escape and the backdrop). */
export function MaskDrawer({
  cameraId,
  kind = "censor",
  onClose,
}: {
  cameraId: string;
  kind?: MaskKind;
  onClose: () => void;
}) {
  const mask = useQuery({ queryKey: ["mask", cameraId], queryFn: () => api.mask(cameraId) });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const name = cameras.data?.find((c) => c.id === cameraId)?.name ?? cameraId;
  const { t, rich } = useI18n();
  const dirty = useRef(false);
  const [asking, setAsking] = useState(false);
  const close = () => {
    if (dirty.current) setAsking(true);
    else onClose();
  };

  return (
    <Dialog open size="large" onOpenChange={(_, d) => !d.open && close()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle
            action={
              <ConfirmPopover
                title={t(kind === "censor" ? "mask.discard" : "motion.discard")}
                confirmLabel={t("mask.discardConfirm")}
                open={asking}
                onOpenChange={(o) => (o ? close() : setAsking(false))}
                onConfirm={async () => onClose()}
              >
                <Button
                  appearance="subtle"
                  aria-label={t("common.close")}
                  icon={<Dismiss24Regular />}
                />
              </ConfirmPopover>
            }
          >
            <DrawerTitle
              title={t(kind === "censor" ? "camera.censorAreas" : "motion.title")}
              sub={name}
            />
          </DialogTitle>
          <DialogContent className="form-stack">
            <Caption1 className="muted">
              <span className="only-mouse">
                {rich("mask.helpMouse", { key: <kbd>Delete</kbd> })}
              </span>
              <span className="only-touch">{t("mask.helpTouch")}</span>{" "}
              {t(kind === "censor" ? "mask.helpCommon" : "motion.help")}
            </Caption1>
            <QueryState error={mask.error} loading={mask.isPending} />
            {/* Remount when the saved state changes so the editor starts from it. */}
            {mask.data && (
              <Editor
                key={
                  kind === "censor"
                    ? (mask.data.mask_version_id ?? "none")
                    : JSON.stringify([mask.data.motion_sensitivity, mask.data.motion_rects])
                }
                kind={kind}
                cameraId={cameraId}
                saved={mask.data}
                onDirty={(d) => {
                  dirty.current = d;
                }}
              />
            )}
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

function Editor({
  kind,
  cameraId,
  saved,
  onDirty,
}: {
  kind: MaskKind;
  cameraId: string;
  saved: MaskState;
  /** Unsaved changes or not, for the drawer's close. */
  onDirty: (dirty: boolean) => void;
}) {
  const client = useQueryClient();
  const { t } = useI18n();
  const overlay = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const coarse = useMediaQuery("(pointer: coarse)");
  const stageSize = useElementSize(overlay);
  const savedRects = kind === "censor" ? saved.rects : saved.motion_rects;
  const [boxes, setBoxes] = useState<Box[]>(() => savedRects.map(toBox));
  // Undo/redo: earlier and later versions of the boxes.
  const [past, setPast] = useState<Box[][]>([]);
  const [future, setFuture] = useState<Box[][]>([]);
  const [isPrivate, setPrivate] = useState(saved.private);
  const [burnIn, setBurnIn] = useState(saved.burn_masks);
  // "" = the default (Settings → Detection).
  const [sensitivity, setSensitivity] = useState<MotionSensitivity | "">(
    saved.motion_sensitivity ?? "",
  );
  const [selected, setSelected] = useState(-1);
  const [preview, setPreview] = useState(false);
  const [picture, setPicture] = useState<Picture>(() => ({
    at: null,
    src: snapshotUrl(cameraId, false, true),
  }));
  const snapshot = picture.src;
  const [loadedSrc, setLoadedSrc] = useState<string>();
  // The last picture that loaded: back to it when another one fails.
  const [shown, setShown] = useState<Picture>();
  const [pictureFailed, setPictureFailed] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  const dirty =
    JSON.stringify(boxes.map(toRect)) !== JSON.stringify(savedRects) ||
    (kind === "censor"
      ? isPrivate !== saved.private || burnIn !== saved.burn_masks
      : sensitivity !== (saved.motion_sensitivity ?? ""));
  useEffect(() => {
    onDirty(dirty);
  });

  const save = useMutation({
    mutationFn: () =>
      kind === "censor"
        ? api.saveMask(cameraId, { rects: boxes.map(toRect), private: isPrivate, burn_in: burnIn })
        : api.saveMotion(cameraId, { sensitivity: sensitivity || null, rects: boxes.map(toRect) }),
    onSuccess: (state) => {
      client.setQueryData(["mask", cameraId], state);
      void client.invalidateQueries({ queryKey: ["cameras"] });
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
    },
  });

  /** Changes the boxes as one step that Undo takes back. */
  const change = (next: Box[], select = -1) => {
    setPast((p) => [...p, boxes]);
    setFuture([]);
    setBoxes(next);
    setSelected(select);
  };
  const undo = () => {
    const prev = past.at(-1);
    if (!prev) return;
    setPast((p) => p.slice(0, -1));
    setFuture((f) => [boxes, ...f]);
    setBoxes(prev);
    setSelected(-1);
  };
  const redo = () => {
    const next = future[0];
    if (!next) return;
    setFuture((f) => f.slice(1));
    setPast((p) => [...p, boxes]);
    setBoxes(next);
    setSelected(-1);
  };
  const remove = (index: number) => change(boxes.filter((_, i) => i !== index));
  const add = () => {
    // A medium box in the middle (moved aside when one is there already), ready to drag.
    const side = 0.2;
    const shift = 0.04 * (boxes.length % 5);
    change(
      [...boxes, { x: 0.4 + shift - 0.08, y: 0.4 + shift - 0.08, w: side, h: side }],
      boxes.length,
    );
  };
  const nudge = (dx: number, dy: number) =>
    change(
      boxes.map((b, i) =>
        i === selected
          ? { ...b, x: clamp(b.x + dx, 0, 1 - b.w), y: clamp(b.y + dy, 0, 1 - b.h) }
          : b,
      ),
      selected,
    );

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.target as HTMLElement).matches("input, textarea, select")) return;
      const mod = ev.metaKey || ev.ctrlKey;
      const arrow = ARROWS[ev.key];
      if ((ev.key === "Delete" || ev.key === "Backspace") && selected >= 0) {
        ev.preventDefault();
        remove(selected);
      } else if (arrow && selected >= 0 && !mod) {
        ev.preventDefault();
        const step = ev.shiftKey ? 0.05 : 0.005;
        nudge(arrow[0] * step, arrow[1] * step);
      } else if (ev.key === "Escape" && selected >= 0) {
        // Deselect first; Escape with nothing selected closes the drawer.
        ev.stopPropagation();
        setSelected(-1);
      } else if (mod && ev.key.toLowerCase() === "z") {
        ev.preventDefault();
        if (ev.shiftKey) redo();
        else undo();
      } else if (mod && ev.key === "s") {
        ev.preventDefault();
        if (dirty) save.mutate();
      }
    };
    const onUnload = (ev: BeforeUnloadEvent) => {
      if (dirty) ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  });

  // Boxes smaller than this (px) get their handles outside (bigger for fingers).
  const handleOut = coarse ? 56 : 24;

  const point = (ev: ReactPointerEvent) => {
    const b = overlay.current!.getBoundingClientRect();
    return { x: clamp((ev.clientX - b.left) / b.width), y: clamp((ev.clientY - b.top) / b.height) };
  };

  const onPointerDown = (ev: ReactPointerEvent<HTMLDivElement>) => {
    // A second finger (or a palm) never starts anything of its own.
    if (ev.button !== 0 || !ev.isPrimary) return;
    ev.preventDefault();
    try {
      // Keep receiving moves when a finger/mouse leaves the image.
      overlay.current!.setPointerCapture(ev.pointerId);
    } catch {
      // Pointer already gone; the drag still works while over the image.
    }
    const p = point(ev);
    const hit = hitTest(boxes, selected, p, stageSize, ev.pointerType !== "mouse", handleOut);
    const start = { x: ev.clientX, y: ev.clientY };
    if (!hit) {
      // Empty picture: a tap deselects; a drag draws a new box.
      setSelected(-1);
      drag.current = { mode: "draw", ax: p.x, ay: p.y, start, started: false };
      return;
    }
    const box = boxes[hit.index]!;
    setSelected(hit.index);
    if (hit.corner) {
      const cx = hit.corner.includes("w") ? box.x : box.x + box.w;
      const cy = hit.corner.includes("n") ? box.y : box.y + box.h;
      // The corner opposite to the grabbed one stays fixed; the grabbed one keeps its
      // distance to the finger (no jump to under it).
      drag.current = {
        index: hit.index,
        mode: "resize",
        ax: hit.corner.includes("w") ? box.x + box.w : box.x,
        ay: hit.corner.includes("n") ? box.y + box.h : box.y,
        gx: cx - p.x,
        gy: cy - p.y,
        created: false,
        start,
        started: false,
      };
    } else {
      drag.current = {
        index: hit.index,
        mode: "move",
        dx: p.x - box.x,
        dy: p.y - box.y,
        start,
        started: false,
      };
    }
  };

  const onPointerMove = (ev: ReactPointerEvent<HTMLDivElement>) => {
    let d = drag.current;
    if (!d || !ev.isPrimary) return;
    const p = point(ev);
    if (!d.started) {
      // Until the pointer has really moved it is a tap (select), not a drag.
      const far = Math.hypot(ev.clientX - d.start.x, ev.clientY - d.start.y);
      if (far < (ev.pointerType === "mouse" ? 3 : 8)) return;
      setPast((ps) => [...ps, boxes]);
      setFuture([]);
      if (d.mode === "draw") {
        const { ax, ay } = d;
        d = {
          index: boxes.length,
          mode: "resize",
          ax,
          ay,
          gx: 0,
          gy: 0,
          created: true,
          start: d.start,
          started: true,
        };
        setBoxes((bs) => [...bs, { x: ax, y: ay, w: 0, h: 0 }]);
        setSelected(boxes.length);
      }
      d.started = true;
      drag.current = d;
    }
    const cur = d;
    if (cur.mode === "draw") return;
    setBoxes((bs) =>
      bs.map((b, i) => {
        if (i !== cur.index) return b;
        if (cur.mode === "move") {
          return { ...b, x: clamp(p.x - cur.dx, 0, 1 - b.w), y: clamp(p.y - cur.dy, 0, 1 - b.h) };
        }
        const qx = clamp(p.x + cur.gx);
        const qy = clamp(p.y + cur.gy);
        // Never smaller than MIN_SIZE: a box that vanished could not be grabbed again.
        const w = Math.max(Math.abs(qx - cur.ax), cur.created ? 0 : MIN_SIZE);
        const h = Math.max(Math.abs(qy - cur.ay), cur.created ? 0 : MIN_SIZE);
        return {
          x: clamp(qx < cur.ax ? cur.ax - w : cur.ax, 0, 1 - w),
          y: clamp(qy < cur.ay ? cur.ay - h : cur.ay, 0, 1 - h),
          w,
          h,
        };
      }),
    );
  };

  const onPointerUp = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if (!ev.isPrimary) return;
    const d = drag.current;
    drag.current = null;
    if (d?.mode !== "resize" || !d.created) return;
    const box = boxes[d.index];
    // Drop accidental strokes that produced a (near) zero-size rectangle.
    if (box && (box.w < MIN_SIZE || box.h < MIN_SIZE)) {
      setBoxes((bs) => bs.filter((_, i) => i !== d.index));
      setPast((ps) => ps.slice(0, -1));
      setSelected(-1);
    }
  };

  return (
    <>
      <Card className="mask-toolbar" appearance="filled-alternative" size="small">
        <Toolbar
          aria-label={t(kind === "censor" ? "mask.actions" : "motion.actions")}
          className="wrap"
        >
          <ToolbarButton
            appearance="primary"
            icon={<Save20Regular />}
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {t("common.save")}
          </ToolbarButton>
          <ToolbarButton
            icon={<ArrowUndo20Regular />}
            aria-label={t("mask.undo")}
            title={t("mask.undo")}
            disabled={past.length === 0}
            onClick={undo}
          />
          <ToolbarButton
            icon={<ArrowRedo20Regular />}
            aria-label={t("mask.redo")}
            title={t("mask.redo")}
            disabled={future.length === 0}
            onClick={redo}
          />
          <ToolbarDivider />
          <ToolbarButton icon={<Add20Regular />} onClick={add}>
            {t("mask.add")}
          </ToolbarButton>
          <ToolbarButton
            icon={<Delete20Regular />}
            disabled={selected < 0}
            onClick={() => remove(selected)}
          >
            {t("common.remove")}
          </ToolbarButton>
          <Menu>
            <MenuTrigger disableButtonEnhancement>
              <ToolbarButton
                icon={<MoreHorizontal20Regular />}
                aria-label={t("mask.more")}
                title={t("mask.more")}
              />
            </MenuTrigger>
            <MenuPopover>
              <MenuList>
                <MenuItem
                  icon={<ArrowReset20Regular />}
                  disabled={!dirty}
                  onClick={() => {
                    change(savedRects.map(toBox));
                    setPrivate(saved.private);
                    setBurnIn(saved.burn_masks);
                    setSensitivity(saved.motion_sensitivity ?? "");
                  }}
                >
                  {t("mask.revert")}
                </MenuItem>
                <MenuItem
                  icon={<DeleteDismiss20Regular />}
                  disabled={boxes.length === 0}
                  onClick={() => change([])}
                >
                  {t("mask.clearAll")}
                </MenuItem>
              </MenuList>
            </MenuPopover>
          </Menu>
          {dirty && <Tag kind="warn">{t("mask.unsaved")}</Tag>}
          {justSaved && <Tag kind="ok">{t("mask.saved")}</Tag>}
          {save.error && <Tag kind="bad">{String(save.error.message)}</Tag>}
        </Toolbar>
      </Card>

      <div className={`mask-stage ${loadedSrc ? "" : "loading"}`}>
        {/* The previous picture stays until the next one has loaded. */}
        <img
          src={snapshot}
          alt={t("mask.snapshotAlt")}
          draggable={false}
          onLoad={() => {
            setLoadedSrc(snapshot);
            setShown(picture);
          }}
          onError={() => {
            setPictureFailed(true);
            if (shown) setPicture(shown);
            else setLoadedSrc(snapshot);
          }}
        />
        {loadedSrc !== snapshot && (
          <div className={loadedSrc ? "stage-loading" : "stage-placeholder"}>
            <Spinner size={loadedSrc ? "tiny" : "small"} label={t("mask.loadingSnapshot")} />
          </div>
        )}
        <div
          ref={overlay}
          className={`overlay ${preview ? "preview" : ""} ${selected >= 0 ? "has-selection" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          {boxes.map((b, i) => {
            // Handles of a small box sit outside it, so its middle stays free for moving.
            const out = handleOffset(b, stageSize, handleOut);
            return (
              <div
                // oxlint-disable-next-line react/no-array-index-key -- boxes have no identity beyond position
                key={i}
                className={`mrect ${kind} ${i === selected ? "selected" : ""}`}
                style={
                  {
                    left: `${b.x * 100}%`,
                    top: `${b.y * 100}%`,
                    width: `${b.w * 100}%`,
                    height: `${b.h * 100}%`,
                    "--hx": `${out.x}px`,
                    "--hy": `${out.y}px`,
                  } as CSSProperties
                }
              >
                {CORNERS.map((corner) => (
                  <span key={corner} className={`handle ${corner}`} />
                ))}
              </div>
            );
          })}
        </div>
      </div>

      <PicturePicker
        cameraId={cameraId}
        value={picture}
        failed={pictureFailed}
        onChange={(p) => {
          setPictureFailed(false);
          setPicture(p);
        }}
      />

      {kind === "motion" ? (
        <div className="mask-options">
          <Field label={t("motion.sensitivity")} hint={t("motion.sensitivityHint")}>
            <Select
              value={sensitivity}
              onChange={(_, d) => setSensitivity(d.value as MotionSensitivity | "")}
            >
              <option value="">{t("camera.default")}</option>
              <option value="low">{t("motion.low")}</option>
              <option value="medium">{t("motion.medium")}</option>
              <option value="high">{t("motion.high")}</option>
            </Select>
          </Field>
        </div>
      ) : (
        <CensorOptions
          preview={preview}
          setPreview={setPreview}
          isPrivate={isPrivate}
          setPrivate={setPrivate}
          burnIn={burnIn}
          setBurnIn={setBurnIn}
        />
      )}
    </>
  );
}

/** The picture under the boxes: the camera now (`at` null) or the one recorded at `at`. */
interface Picture {
  at: string | null;
  src: string;
}

/** Live or a recorded moment of the last two days, as a row of small pictures: night and
 *  day look different, and so do the areas worth masking. */
function PicturePicker({
  cameraId,
  value,
  failed,
  onChange,
}: {
  cameraId: string;
  value: Picture;
  /** The last one chosen could not be loaded. */
  failed: boolean;
  onChange: (p: Picture) => void;
}) {
  const { t } = useI18n();
  const moments = useQuery({
    queryKey: ["snapshot-moments", cameraId],
    queryFn: () => api.snapshotMoments(cameraId),
  });
  const live = value.at === null;
  return (
    <Field
      label={t("mask.picture")}
      hint={t("mask.pictureHint")}
      validationState={failed ? "error" : "none"}
      validationMessage={failed ? t("mask.pictureFailed") : undefined}
    >
      <div className="picture-strip">
        <button
          type="button"
          aria-pressed={live}
          className="picture-choice"
          title={live ? t("mask.newSnapshot") : undefined}
          aria-label={live ? t("mask.newSnapshot") : t("mask.live")}
          // Live again while live: a new snapshot.
          onClick={() => onChange({ at: null, src: snapshotUrl(cameraId, live, true) })}
        >
          <span className="picture-choice-live">
            {live ? <ArrowClockwise20Regular /> : <Live20Regular />}
          </span>
          <Caption1>{t("mask.live")}</Caption1>
        </button>
        {moments.isPending && <Spinner size="tiny" />}
        {moments.data?.map((m) => (
          <button
            key={m.at}
            type="button"
            aria-pressed={value.at === m.at}
            className="picture-choice"
            onClick={() => onChange({ at: m.at, src: recordedSnapshotUrl(cameraId, m.at) })}
          >
            <FrameImage
              src={recordedSnapshotUrl(cameraId, m.at, true)}
              alt=""
              className="mini"
              lazy
            />
            <Caption1>
              {m.is_night && <WeatherMoon16Regular aria-label={t("mask.night")} />}
              {localWeekdayTime(m.at)}
            </Caption1>
          </button>
        ))}
      </div>
    </Field>
  );
}

function CensorOptions({
  preview,
  setPreview,
  isPrivate,
  setPrivate,
  burnIn,
  setBurnIn,
}: {
  preview: boolean;
  setPreview: (v: boolean) => void;
  isPrivate: boolean;
  setPrivate: (v: boolean) => void;
  burnIn: boolean;
  setBurnIn: (v: boolean) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="mask-options">
      <Switch
        checked={preview}
        onChange={(_, d) => setPreview(d.checked)}
        label={t("mask.preview")}
      />
      <Switch
        checked={isPrivate}
        onChange={(_, d) => setPrivate(d.checked)}
        label={t("mask.private")}
      />
      <Switch
        checked={burnIn}
        onChange={(_, d) => setBurnIn(d.checked)}
        label={
          <>
            {t("mask.burnIn")}
            <Caption1 as="span" className="muted option-hint">
              {t("mask.burnInHint")}
            </Caption1>
          </>
        }
      />
    </div>
  );
}
