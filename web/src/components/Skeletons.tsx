import { Skeleton, SkeletonItem, Spinner } from "@fluentui/react-components";

import { useI18n } from "../i18n";
import { PHONE, useMediaQuery } from "../useMediaQuery";

/** Before anything can be shown (signing in): a spinner in the middle of the screen. */
export function PageSpinner() {
  const { t } = useI18n();
  return (
    <div className="page-loading">
      <Spinner label={t("common.loading")} />
    </div>
  );
}

/** Lines of text standing in for a value or a sentence; widths vary so it reads as text. */
function Lines({ widths }: { widths: string[] }) {
  return (
    <div className="skeleton-lines">
      {widths.map((w, i) => (
        // oxlint-disable-next-line react/no-array-index-key -- a fixed list, never reordered
        <SkeletonItem key={i} size={16} style={{ width: w }} />
      ))}
    </div>
  );
}

const WIDTHS = ["70%", "45%", "85%", "55%", "65%", "40%"];

/** A `DataTable` while it loads: rows of cells on desktop, cards of facts on phones (with
 *  the picture on top for tables whose rows have one). */
export function TableSkeleton({
  rows = 6,
  columns = 5,
  thumb = false,
}: {
  rows?: number;
  columns?: number;
  thumb?: boolean;
}) {
  const { t } = useI18n();
  const phone = useMediaQuery(PHONE);
  if (phone) {
    return (
      <Skeleton aria-label={t("common.loading")} className="dt-cards">
        {Array.from({ length: Math.min(rows, 4) }, (_, i) => (
          <div key={i} className="dt-card">
            {thumb && <SkeletonItem shape="rectangle" className="skeleton-thumb" />}
            <div className="skeleton-facts">
              {Array.from({ length: Math.min(columns, 4) }, (_cell, j) => (
                <div key={j} className="skeleton-fact">
                  <SkeletonItem size={12} style={{ width: "30%" }} />
                  <SkeletonItem size={12} style={{ width: WIDTHS[(i + j) % WIDTHS.length] }} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </Skeleton>
    );
  }
  return (
    <Skeleton aria-label={t("common.loading")} className="skeleton-table">
      <div className="skeleton-row skeleton-head">
        {thumb && <span className="skeleton-mini-space" />}
        {Array.from({ length: columns }, (_cell, j) => (
          <span key={j} className="skeleton-cell">
            <SkeletonItem size={12} style={{ width: "50%" }} />
          </span>
        ))}
      </div>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row">
          {thumb && <SkeletonItem shape="rectangle" className="mini" />}
          {Array.from({ length: columns }, (_cell, j) => (
            <span key={j} className="skeleton-cell">
              <SkeletonItem size={16} style={{ width: WIDTHS[(i + j) % WIDTHS.length] }} />
            </span>
          ))}
        </div>
      ))}
    </Skeleton>
  );
}

/** A grid of cards (cameras, events) while it loads: a picture and a few lines each. */
export function CardsSkeleton({ count = 3, thumb = true }: { count?: number; thumb?: boolean }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")} className="cards">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton-card">
          {!thumb && <Lines widths={["50%"]} />}
          {thumb && <SkeletonItem shape="rectangle" className="skeleton-thumb" />}
          <Lines widths={thumb ? ["60%", "35%"] : ["80%", "65%", "40%"]} />
        </div>
      ))}
    </Skeleton>
  );
}

/** The Status page's figures while they load. */
export function StatsSkeleton({ count = 4 }: { count?: number }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")} className="stats">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton-card">
          <Lines widths={["70%", "40%"]} />
        </div>
      ))}
    </Skeleton>
  );
}

/** Text that is still loading, e.g. inside a banner whose wording depends on the data. */
export function TextSkeleton({ lines = 2 }: { lines?: number }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")}>
      <Lines widths={["90%", "60%", "75%"].slice(0, lines)} />
    </Skeleton>
  );
}

/** A form field while it loads: its label, the input and the hint under it. */
export function FieldSkeleton() {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")} className="skeleton-field">
      <SkeletonItem size={12} style={{ width: "35%" }} />
      <SkeletonItem size={32} />
      <SkeletonItem size={12} style={{ width: "70%" }} />
    </Skeleton>
  );
}

/** Label / value facts (`Facts`) while they load. */
export function FactsSkeleton({ rows = 4 }: { rows?: number }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")} className="skeleton-facts">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-fact">
          {/* As long as real labels and values, not stretched across a wide card. */}
          <SkeletonItem size={12} style={{ width: "7em", flex: "none" }} />
          <SkeletonItem size={12} style={{ width: FACT_WIDTHS[i % FACT_WIDTHS.length] }} />
        </div>
      ))}
    </Skeleton>
  );
}

const FACT_WIDTHS = ["14em", "9em", "18em", "11em"];

/** A list of options to pick one of (a `RadioGroup`) while it loads. */
export function ChoicesSkeleton({ count = 3 }: { count?: number }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")} className="skeleton-lines">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton-choice">
          <SkeletonItem shape="circle" size={20} />
          <SkeletonItem size={16} style={{ width: WIDTHS[i % WIDTHS.length] }} />
        </div>
      ))}
    </Skeleton>
  );
}

/** A picture (16:9) or a chart (`height` in px) while it loads. */
export function BoxSkeleton({ height }: { height?: number }) {
  const { t } = useI18n();
  return (
    <Skeleton aria-label={t("common.loading")}>
      <SkeletonItem
        shape="rectangle"
        className="skeleton-thumb"
        style={height ? { height, aspectRatio: "auto" } : undefined}
      />
    </Skeleton>
  );
}

/** The Correct / Wrong buttons (`Feedback` with labels) while the detection loads. Spans:
 *  they sit in a row of buttons. */
export function FeedbackSkeleton() {
  const { t } = useI18n();
  return (
    <>
      <SkeletonItem
        as="span"
        size={32}
        className="skeleton-button"
        aria-label={t("common.loading")}
      />
      <SkeletonItem as="span" size={32} className="skeleton-button" />
    </>
  );
}
