import {
  Button,
  Caption1,
  Card,
  Field,
  Select,
  Spinner,
  Switch,
  Toolbar,
  ToolbarButton,
  ToolbarDivider,
} from "@fluentui/react-components";
import {
  ArrowUndo20Regular,
  Camera20Regular,
  Delete20Regular,
  Dismiss16Filled,
  Dismiss24Regular,
  Save20Regular,
} from "@fluentui/react-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { api, snapshotUrl, type MaskState, type MotionSensitivity, type Rect } from "../api";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from "../components/Drawer";
import { ConfirmPopover } from "../components/ConfirmPopover";
import { DrawerTitle, QueryState, Tag } from "../components/ui";
import { useI18n } from "../i18n";

/** Normalized (0..1) rectangle; independent of the stream resolution. */
interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

// The dragged box index lives in the ref (not state) so rapid pointer events never see a
// stale selection between renders.
type Drag = { index: number } & (
  | { mode: "move"; dx: number; dy: number }
  | { mode: "resize"; ax: number; ay: number; created: boolean }
);

const MIN_SIZE = 0.01;
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const toBox = ([x, y, w, h]: Rect): Box => ({ x, y, w, h });
const toRect = (b: Box): Rect => [b.x, b.y, b.w, b.h];

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
  const savedRects = kind === "censor" ? saved.rects : saved.motion_rects;
  const [boxes, setBoxes] = useState<Box[]>(() => savedRects.map(toBox));
  const [isPrivate, setPrivate] = useState(saved.private);
  const [burnIn, setBurnIn] = useState(saved.burn_masks);
  // "" = the default (Settings → Detection).
  const [sensitivity, setSensitivity] = useState<MotionSensitivity | "">(
    saved.motion_sensitivity ?? "",
  );
  const [selected, setSelected] = useState(-1);
  const [preview, setPreview] = useState(false);
  const [snapshot, setSnapshot] = useState(() => snapshotUrl(cameraId, false, true));
  const [snapshotLoaded, setSnapshotLoaded] = useState(false);
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

  const remove = (index: number) => {
    setBoxes((bs) => bs.filter((_, i) => i !== index));
    setSelected(-1);
  };

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.target as HTMLElement).matches("input, textarea, select")) return;
      if ((ev.key === "Delete" || ev.key === "Backspace") && selected >= 0) {
        ev.preventDefault();
        remove(selected);
      } else if (ev.key === "Escape" && selected >= 0) {
        // Deselect first; Escape with nothing selected closes the drawer.
        ev.stopPropagation();
        setSelected(-1);
      } else if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") {
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

  const point = (ev: ReactPointerEvent) => {
    const b = overlay.current!.getBoundingClientRect();
    return { x: clamp((ev.clientX - b.left) / b.width), y: clamp((ev.clientY - b.top) / b.height) };
  };

  const onPointerDown = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if (ev.button !== 0) return;
    // The pointer usually lands on a child (e.g. the × icon's <svg>/<path>), so resolve the
    // nearest element that carries a role. No role => empty area => draw a new rectangle.
    const target = (ev.target as Element).closest<HTMLElement>("[data-role]");
    const role = target?.dataset.role;
    if (role === "delete") return; // handled by the button's click
    ev.preventDefault();
    try {
      // Keep receiving moves when a finger/mouse leaves the image.
      overlay.current!.setPointerCapture(ev.pointerId);
    } catch {
      // Pointer already gone; the drag still works while over the image.
    }
    const p = point(ev);
    const index = Number(target?.dataset.index);
    const box = boxes[index];
    if (role === "move" && box) {
      setSelected(index);
      drag.current = { index, mode: "move", dx: p.x - box.x, dy: p.y - box.y };
    } else if (role === "resize" && box) {
      const corner = target?.dataset.corner ?? "se";
      setSelected(index);
      // The corner opposite to the grabbed one stays fixed.
      drag.current = {
        index,
        mode: "resize",
        ax: corner.includes("w") ? box.x + box.w : box.x,
        ay: corner.includes("n") ? box.y + box.h : box.y,
        created: false,
      };
    } else {
      setBoxes((bs) => [...bs, { x: p.x, y: p.y, w: 0, h: 0 }]);
      setSelected(boxes.length);
      drag.current = { index: boxes.length, mode: "resize", ax: p.x, ay: p.y, created: true };
    }
  };

  const onPointerMove = (ev: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const p = point(ev);
    setBoxes((bs) =>
      bs.map((b, i) => {
        if (i !== d.index) return b;
        if (d.mode === "move") {
          return { ...b, x: clamp(p.x - d.dx, 0, 1 - b.w), y: clamp(p.y - d.dy, 0, 1 - b.h) };
        }
        return {
          x: Math.min(d.ax, p.x),
          y: Math.min(d.ay, p.y),
          w: Math.abs(p.x - d.ax),
          h: Math.abs(p.y - d.ay),
        };
      }),
    );
  };

  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    const box = d ? boxes[d.index] : undefined;
    // Drop accidental clicks that produced a (near) zero-size rectangle.
    if (d?.mode === "resize" && d.created && box && (box.w < MIN_SIZE || box.h < MIN_SIZE)) {
      remove(d.index);
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
            disabled={!dirty}
            onClick={() => {
              setBoxes(savedRects.map(toBox));
              setPrivate(saved.private);
              setBurnIn(saved.burn_masks);
              setSensitivity(saved.motion_sensitivity ?? "");
              setSelected(-1);
            }}
          >
            {t("mask.revert")}
          </ToolbarButton>
          <ToolbarButton
            icon={<Delete20Regular />}
            disabled={boxes.length === 0}
            onClick={() => {
              setBoxes([]);
              setSelected(-1);
            }}
          >
            {t("mask.clearAll")}
          </ToolbarButton>
          <ToolbarDivider />
          <ToolbarButton
            icon={<Camera20Regular />}
            onClick={() => {
              setSnapshotLoaded(false);
              setSnapshot(snapshotUrl(cameraId, true, true));
            }}
          >
            {t("mask.newSnapshot")}
          </ToolbarButton>
          {dirty && <Tag kind="warn">{t("mask.unsaved")}</Tag>}
          {justSaved && <Tag kind="ok">{t("mask.saved")}</Tag>}
          {save.error && <Tag kind="bad">{String(save.error.message)}</Tag>}
        </Toolbar>
      </Card>

      <div className={`mask-stage ${snapshotLoaded ? "" : "loading"}`}>
        <img
          src={snapshot}
          alt={t("mask.snapshotAlt")}
          draggable={false}
          onLoad={() => setSnapshotLoaded(true)}
        />
        {!snapshotLoaded && (
          <div className="stage-placeholder">
            <Spinner size="small" label={t("mask.loadingSnapshot")} />
          </div>
        )}
        <div
          ref={overlay}
          className={`overlay ${preview ? "preview" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          {boxes.map((b, i) => (
            <div
              // oxlint-disable-next-line react/no-array-index-key -- boxes have no identity beyond position
              key={i}
              className={`mrect ${kind} ${i === selected ? "selected" : ""}`}
              data-role="move"
              data-index={i}
              style={{
                left: `${b.x * 100}%`,
                top: `${b.y * 100}%`,
                width: `${b.w * 100}%`,
                height: `${b.h * 100}%`,
              }}
            >
              {(["nw", "ne", "sw", "se"] as const).map((corner) => (
                <span
                  key={corner}
                  className={`handle ${corner}`}
                  data-role="resize"
                  data-corner={corner}
                  data-index={i}
                />
              ))}
              <button
                type="button"
                className="del"
                aria-label={t("common.remove")}
                data-role="delete"
                onClick={() => remove(i)}
              >
                <Dismiss16Filled />
              </button>
            </div>
          ))}
        </div>
      </div>

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
