import {
  Button,
  Caption1,
  Card,
  Field,
  InteractionTag,
  InteractionTagPrimary,
  InteractionTagSecondary,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Portal,
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
  Checkmark20Regular,
  Delete20Regular,
  DeleteDismiss20Regular,
  Dismiss24Regular,
  FullScreenMaximize20Regular,
  FullScreenMinimize20Regular,
  Live20Regular,
  MoreHorizontal20Regular,
  Save20Regular,
  WeatherMoon16Regular,
  ZoomFit20Regular,
  ZoomIn20Regular,
  ZoomOut20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useLayoutEffect,
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
import { DrawerTitle, InfoBanner, QueryState, Tag } from "../components/ui";
import { decimal, localWeekdayTime } from "../format";
import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";
import {
  CORNERS,
  MIN_SIZE,
  clamp,
  handleOffset,
  hitTest,
  snapLines,
  snapShift,
  snapValue,
  toBox,
  toRect,
  type Box,
  type Point,
  type Size,
} from "./maskGeometry";

// The pointer's plan lives in the ref (not state) so rapid pointer events never see a
// stale selection between renders. Nothing changes until it has moved a little
// (`started`): a tap only selects.
type Drag = { start: Point; started: boolean } & (
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

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};
const MAX_ZOOM = 8;
// The magnifier while a finger drags: its size (px) and how much it enlarges.
const LOUPE = 120;
const LOUPE_SCALE = 2;

/** The size of an element on screen, kept up to date. `remount`: changes when the
 *  element is replaced by another (it is observed again). */
function useElementSize(ref: RefObject<HTMLElement | null>, remount: unknown): Size {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() =>
      setSize({ width: el.clientWidth, height: el.clientHeight }),
    );
    observer.observe(el);
    return () => observer.disconnect();
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- `remount` stands for ref.current
  }, [ref, remount]);
  return size;
}

const percentOf = (v: number) => (v < 0.1 ? decimal(v * 100, 1) : String(Math.round(v * 100)));

/** A box's size in percent of the picture: "12 × 8 %" (one decimal when small). */
const boxSize = (b: Box) => `${percentOf(b.w)} × ${percentOf(b.h)} %`;

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
              {t(kind === "censor" ? "mask.helpCommon" : "motion.help")}
            </Caption1>
            <InfoBanner id="mask-editor-help">
              <span className="only-mouse">
                {rich("mask.helpMouse", { key: <kbd>Delete</kbd> })}
              </span>
              <span className="only-touch">{t("mask.helpTouch")}</span>
            </InfoBanner>
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
  const stage = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  // Fingers on the picture, and a two-finger pinch (zoom and pan) in progress.
  const pointers = useRef(new Map<number, Point>());
  const pinch = useRef<{ d0: number; z0: number; n: Point } | null>(null);
  // Where the picture should scroll to once the new zoom is laid out.
  const pendingScroll = useRef<{ n: Point; anchor: Point } | null>(null);
  const coarse = useMediaQuery("(pointer: coarse)");
  const phone = useMediaQuery(PHONE);
  // The picture's size on screen, zoom included.
  const [full, setFull] = useState(false);
  const stageSize = useElementSize(overlay, full);
  const savedRects = kind === "censor" ? saved.rects : saved.motion_rects;
  // The other kind of areas, shown faintly for context (not editable here).
  const otherRects = kind === "censor" ? saved.motion_rects : saved.rects;
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
  const [showOther, setShowOther] = useState(true);
  const [zoom, setZoom] = useState(1);
  // The magnifier: where the finger is (client px) and the picture point it enlarges.
  const [loupe, setLoupe] = useState<{ at: Point; focus: Point; index: number } | null>(null);
  const [picture, setPicture] = useState<Picture>(() => ({
    at: null,
    src: snapshotUrl(cameraId, false, true),
  }));
  const snapshot = picture.src;
  const [aspect, setAspect] = useState(16 / 9);
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
    // A medium box (smaller when zoomed in) in the middle of what is in view, ready to
    // drag; moved aside a little when one is there already.
    const side = 0.2 / zoom;
    const c = viewCenter();
    const shift = (0.04 / zoom) * (boxes.length % 5);
    change(
      [
        ...boxes,
        {
          x: clamp(c.x - side / 2 + shift, 0, 1 - side),
          y: clamp(c.y - side / 2 + shift, 0, 1 - side),
          w: side,
          h: side,
        },
      ],
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

  // --- Zoom: the canvas gets wider than the viewport, which scrolls. ---

  /** The picture point (0..1) in the middle of the viewport. */
  const viewCenter = (): Point => {
    const vp = viewport.current;
    const c = canvas.current;
    if (!vp || !c) return { x: 0.5, y: 0.5 };
    return {
      x: (vp.scrollLeft + vp.clientWidth / 2) / c.offsetWidth,
      y: (vp.scrollTop + vp.clientHeight / 2) / c.offsetHeight,
    };
  };
  const scrollTo = (n: Point, anchor: Point) => {
    const vp = viewport.current;
    const c = canvas.current;
    if (!vp || !c) return;
    const r = vp.getBoundingClientRect();
    vp.scrollLeft = n.x * c.offsetWidth - (anchor.x - r.left);
    vp.scrollTop = n.y * c.offsetHeight - (anchor.y - r.top);
  };
  /** Zoom to `z`, keeping picture point `n` under the screen point `anchor`. */
  const zoomTo = (z: number, n: Point, anchor: Point) => {
    const next = clamp(z, 1, MAX_ZOOM);
    if (next === zoom) scrollTo(n, anchor);
    else {
      pendingScroll.current = { n, anchor };
      setZoom(next);
    }
  };
  useLayoutEffect(() => {
    const s = pendingScroll.current;
    if (!s) return;
    pendingScroll.current = null;
    scrollTo(s.n, s.anchor);
  });
  /** Zoom buttons: on the selected box if there is one, else on the middle of the view. */
  const zoomBy = (factor: number) => {
    const vp = viewport.current;
    if (!vp) return;
    const r = vp.getBoundingClientRect();
    const b = boxes[selected];
    const n = b ? { x: b.x + b.w / 2, y: b.y + b.h / 2 } : viewCenter();
    zoomTo(factor === 0 ? 1 : zoom * factor, n, {
      x: r.left + vp.clientWidth / 2,
      y: r.top + vp.clientHeight / 2,
    });
  };
  /** Select a box and, when zoomed in, bring it into view. */
  const show = (index: number) => {
    setSelected(index);
    const b = boxes[index];
    const vp = viewport.current;
    if (!b || !vp || zoom === 1) return;
    const r = vp.getBoundingClientRect();
    scrollTo(
      { x: b.x + b.w / 2, y: b.y + b.h / 2 },
      { x: r.left + vp.clientWidth / 2, y: r.top + vp.clientHeight / 2 },
    );
  };

  // Ctrl/⌘ + wheel (and a trackpad pinch, which arrives as one) zooms at the pointer.
  useEffect(() => {
    const vp = viewport.current;
    if (!vp) return;
    const onWheel = (ev: WheelEvent) => {
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      const c = canvas.current!.getBoundingClientRect();
      zoomTo(
        zoom * Math.exp(-ev.deltaY * 0.01),
        { x: (ev.clientX - c.left) / c.width, y: (ev.clientY - c.top) / c.height },
        { x: ev.clientX, y: ev.clientY },
      );
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  });

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      // Keys in fields and open menus are theirs.
      const target = ev.target instanceof Element ? ev.target : null;
      if (target?.closest("input, textarea, select, [role=menu]")) return;
      const mod = ev.metaKey || ev.ctrlKey;
      const arrow = ARROWS[ev.key];
      if ((ev.key === "Delete" || ev.key === "Backspace") && selected >= 0) {
        ev.preventDefault();
        remove(selected);
      } else if (arrow && selected >= 0 && !mod) {
        ev.preventDefault();
        const step = ev.shiftKey ? 0.05 : 0.005 / zoom;
        nudge(arrow[0] * step, arrow[1] * step);
      } else if (ev.key === "Escape" && (selected >= 0 || full)) {
        // Deselect first, then leave full screen; Escape after that closes the drawer.
        ev.stopPropagation();
        if (selected >= 0) setSelected(-1);
        else setFull(false);
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
    // Capture: before the drawer, which would close on Escape.
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("beforeunload", onUnload);
    };
  });

  // Boxes smaller than this (px) get their handles outside (bigger for fingers).
  const handleOut = coarse ? 56 : 24;

  const point = (ev: { clientX: number; clientY: number }) => {
    const b = overlay.current!.getBoundingClientRect();
    return { x: clamp((ev.clientX - b.left) / b.width), y: clamp((ev.clientY - b.top) / b.height) };
  };

  const startPinch = () => {
    // A second finger: whatever the first one started is taken back; both now zoom.
    const d = drag.current;
    drag.current = null;
    setLoupe(null);
    if (d?.started) {
      const prev = past.at(-1);
      if (prev) {
        setBoxes(prev);
        setPast((p) => p.slice(0, -1));
      }
      if (d.mode === "resize" && d.created) setSelected(-1);
    }
    const [a, b] = [...pointers.current.values()] as [Point, Point];
    const c = canvas.current!.getBoundingClientRect();
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    pinch.current = {
      d0: Math.max(Math.hypot(a.x - b.x, a.y - b.y), 1),
      z0: zoom,
      n: { x: (mid.x - c.left) / c.width, y: (mid.y - c.top) / c.height },
    };
  };

  const onPointerDown = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if (ev.pointerType === "mouse" && ev.button !== 0) return;
    ev.preventDefault();
    pointers.current.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    try {
      // Keep receiving moves when a finger/mouse leaves the image.
      overlay.current!.setPointerCapture(ev.pointerId);
    } catch {
      // Pointer already gone; the drag still works while over the image.
    }
    if (pointers.current.size === 2 && ev.pointerType !== "mouse") {
      startPinch();
      return;
    }
    if (!ev.isPrimary || pointers.current.size > 1) return;
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
    if (pointers.current.has(ev.pointerId)) {
      pointers.current.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    }
    const pin = pinch.current;
    if (pin) {
      if (pointers.current.size < 2) return;
      const [a, b] = [...pointers.current.values()] as [Point, Point];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      zoomTo((pin.z0 * dist) / pin.d0, pin.n, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      return;
    }
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
    // Edges snap to the picture's borders and to the other boxes' edges (Alt: no snap).
    const lines = snapLines(boxes, cur.index);
    const reach = ev.altKey ? 0 : coarse ? 10 : 6;
    const tolX = reach / Math.max(stageSize.width, 1);
    const tolY = reach / Math.max(stageSize.height, 1);
    const box = boxes[cur.index];
    let next: Box | undefined;
    let focus = p;
    if (box && cur.mode === "move") {
      const x = clamp(p.x - cur.dx, 0, 1 - box.w);
      const y = clamp(p.y - cur.dy, 0, 1 - box.h);
      next = {
        ...box,
        x: clamp(x + snapShift(x, x + box.w, lines.xs, tolX), 0, 1 - box.w),
        y: clamp(y + snapShift(y, y + box.h, lines.ys, tolY), 0, 1 - box.h),
      };
    } else if (box && cur.mode === "resize") {
      const qx = snapValue(clamp(p.x + cur.gx), lines.xs, tolX);
      const qy = snapValue(clamp(p.y + cur.gy), lines.ys, tolY);
      // Never smaller than MIN_SIZE: a box that vanished could not be grabbed again.
      const w = Math.max(Math.abs(qx - cur.ax), cur.created ? 0 : MIN_SIZE);
      const h = Math.max(Math.abs(qy - cur.ay), cur.created ? 0 : MIN_SIZE);
      next = {
        x: clamp(qx < cur.ax ? cur.ax - w : cur.ax, 0, 1 - w),
        y: clamp(qy < cur.ay ? cur.ay - h : cur.ay, 0, 1 - h),
        w,
        h,
      };
      focus = { x: qx, y: qy };
    }
    if (next) {
      const moved = next;
      setBoxes((bs) => bs.map((b, i) => (i === cur.index ? moved : b)));
    }
    // A finger hides what it drags: show it enlarged beside it.
    const s = stage.current;
    if (ev.pointerType !== "mouse" && s) {
      // Above the finger, or below it near the top of the screen; in the stage's px.
      const r = s.getBoundingClientRect();
      const above = ev.clientY - LOUPE - 48 >= 0;
      setLoupe({
        at: {
          x: ev.clientX - r.left - LOUPE / 2,
          y: ev.clientY - r.top + (above ? -LOUPE - 48 : 48),
        },
        focus,
        index: cur.index,
      });
    }
  };

  const onPointerUp = (ev: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(ev.pointerId);
    if (pinch.current) {
      // The finger left behind does nothing until it is lifted too.
      if (pointers.current.size < 2) pinch.current = null;
      return;
    }
    if (!ev.isPrimary) return;
    setLoupe(null);
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

  /** A toolbar button's text: none on phones (the icon and its name for screen readers). */
  const label = (text: string) => ({
    "aria-label": text,
    title: text,
    children: phone ? undefined : text,
  });

  const toolbar = (
    <Card className="mask-toolbar" appearance="filled-alternative" size="small">
      <Toolbar
        aria-label={t(kind === "censor" ? "mask.actions" : "motion.actions")}
        className="wrap"
      >
        <ToolbarButton
          appearance="primary"
          icon={justSaved ? <Checkmark20Regular /> : <Save20Regular />}
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
          {...label(t("common.save"))}
        />
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
        <ToolbarButton icon={<Add20Regular />} onClick={add} {...label(t("mask.add"))} />
        <ToolbarButton
          icon={<Delete20Regular />}
          disabled={selected < 0}
          onClick={() => remove(selected)}
          {...label(t("common.remove"))}
        />
        {/* Inline: it shows in full screen too. */}
        <Menu inline>
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
                icon={full ? <FullScreenMinimize20Regular /> : <FullScreenMaximize20Regular />}
                onClick={() => setFull(!full)}
              >
                {t(full ? "mask.exitFullscreen" : "mask.fullscreen")}
              </MenuItem>
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
        {/* On phones the Save button says it (enabled, then a checkmark). */}
        {dirty && !phone && <Tag kind="warn">{t("mask.unsaved")}</Tag>}
        {justSaved && !phone && <Tag kind="ok">{t("mask.saved")}</Tag>}
        {save.error && <Tag kind="bad">{String(save.error.message)}</Tag>}
      </Toolbar>
    </Card>
  );

  const editor = (
    <div className={`form-stack mask-editor ${full ? "fullscreen" : ""}`}>
      {toolbar}

      <div className="mask-stage-area" style={{ "--aspect": aspect } as CSSProperties}>
        <div ref={stage} className={`mask-stage ${loadedSrc ? "" : "loading"}`}>
          <div ref={viewport} className={`mask-viewport ${zoom > 1 ? "zoomed" : ""}`}>
            <div ref={canvas} className="mask-canvas" style={{ width: `${zoom * 100}%` }}>
              {/* The previous picture stays until the next one has loaded. */}
              <img
                src={snapshot}
                alt={t("mask.snapshotAlt")}
                draggable={false}
                onLoad={(ev) => {
                  const img = ev.currentTarget;
                  if (img.naturalWidth && img.naturalHeight) {
                    setAspect(img.naturalWidth / img.naturalHeight);
                  }
                  setLoadedSrc(snapshot);
                  setShown(picture);
                }}
                onError={() => {
                  setPictureFailed(true);
                  if (shown) setPicture(shown);
                  else setLoadedSrc(snapshot);
                }}
              />
              <div
                ref={overlay}
                className={`overlay ${preview ? "preview" : ""} ${selected >= 0 ? "has-selection" : ""}`}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
              >
                {showOther &&
                  !preview &&
                  otherRects.map((r) => (
                    <div
                      key={r.join()}
                      className={`mrect other ${kind === "censor" ? "motion" : "censor"}`}
                      style={{
                        left: `${r[0] * 100}%`,
                        top: `${r[1] * 100}%`,
                        width: `${r[2] * 100}%`,
                        height: `${r[3] * 100}%`,
                      }}
                    />
                  ))}
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
                      <span className="mrect-number">{i + 1}</span>
                      {CORNERS.map((corner) => (
                        <span key={corner} className={`handle ${corner}`} />
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
          {loadedSrc !== snapshot && (
            <div className={loadedSrc ? "stage-loading" : "stage-placeholder"}>
              <Spinner size={loadedSrc ? "tiny" : "small"} label={t("mask.loadingSnapshot")} />
            </div>
          )}
          <div className="zoom-controls">
            <Button
              size="small"
              icon={<ZoomOut20Regular />}
              aria-label={t("mask.zoomOut")}
              title={t("mask.zoomOut")}
              disabled={zoom <= 1}
              onClick={() => zoomBy(0.5)}
            />
            <Button
              size="small"
              icon={<ZoomFit20Regular />}
              aria-label={t("mask.zoomFit")}
              title={t("mask.zoomFit")}
              disabled={zoom <= 1}
              onClick={() => zoomBy(0)}
            >
              {`${decimal(zoom, zoom < 10 && zoom % 1 ? 1 : 0)}×`}
            </Button>
            <Button
              size="small"
              icon={<ZoomIn20Regular />}
              aria-label={t("mask.zoomIn")}
              title={t("mask.zoomIn")}
              disabled={zoom >= MAX_ZOOM}
              onClick={() => zoomBy(2)}
            />
          </div>
          {loupe && (
            <Loupe
              src={snapshot}
              size={stageSize}
              focus={loupe.focus}
              box={boxes[loupe.index]}
              kind={kind}
              left={loupe.at.x}
              top={loupe.at.y}
            />
          )}
        </div>
      </div>

      {!full && boxes.length > 0 && (
        <div className="chips mask-list">
          {boxes.map((b, i) => (
            <InteractionTag
              // oxlint-disable-next-line react/no-array-index-key -- boxes have no identity beyond position
              key={i}
              size="small"
              appearance={i === selected ? "brand" : "outline"}
            >
              <InteractionTagPrimary hasSecondaryAction onClick={() => show(i)}>
                <strong>{i + 1}</strong> <span className="mask-list-size">{boxSize(b)}</span>
              </InteractionTagPrimary>
              <InteractionTagSecondary
                aria-label={t("mask.removeArea", { n: i + 1 })}
                onClick={() => remove(i)}
              />
            </InteractionTag>
          ))}
        </div>
      )}

      {!full && (
        <PicturePicker
          cameraId={cameraId}
          value={picture}
          failed={pictureFailed}
          onChange={(p) => {
            setPictureFailed(false);
            setPicture(p);
          }}
        />
      )}

      {!full && (
        <div className="mask-options">
          {otherRects.length > 0 && (
            <Switch
              checked={showOther}
              onChange={(_, d) => setShowOther(d.checked)}
              label={t(kind === "censor" ? "mask.showMotion" : "motion.showCensor")}
            />
          )}
          {kind === "motion" ? (
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
        </div>
      )}
    </div>
  );
  // Full screen covers the window, so it leaves the drawer (which would hold a fixed
  // layer inside itself); Fluent's portal keeps it inside the drawer's focus trap.
  return full ? <Portal>{editor}</Portal> : editor;
}

/** The magnifier: the picture around `focus` (0..1), enlarged, with the box being
 *  dragged and a cross on the point itself. */
function Loupe({
  src,
  size,
  focus,
  box,
  kind,
  left,
  top,
}: {
  src: string;
  size: Size;
  focus: Point;
  box?: Box;
  kind: MaskKind;
  left: number;
  top: number;
}) {
  const w = size.width * LOUPE_SCALE;
  const h = size.height * LOUPE_SCALE;
  return (
    <div className="loupe" aria-hidden style={{ left, top, width: LOUPE, height: LOUPE }}>
      <div
        className="loupe-content"
        style={{
          width: w,
          height: h,
          transform: `translate(${LOUPE / 2 - focus.x * w}px, ${LOUPE / 2 - focus.y * h}px)`,
        }}
      >
        <img src={src} alt="" draggable={false} />
        {box && (
          <div
            className={`mrect ${kind} selected`}
            style={{
              left: `${box.x * 100}%`,
              top: `${box.y * 100}%`,
              width: `${box.w * 100}%`,
              height: `${box.h * 100}%`,
            }}
          />
        )}
      </div>
      <span className="loupe-cross" />
    </div>
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
    <>
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
    </>
  );
}
