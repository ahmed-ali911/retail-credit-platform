/**
 * Step 16, Part C — one generic skeleton primitive, reused everywhere a screen
 * used to show a blank area (or a bare "Loading…") while its first fetch runs.
 * Grey pulsing blocks roughly the shape of the eventual content.
 */

export function Skeleton({
  width,
  height,
  className,
  style,
}: {
  width?: number | string;
  height?: number | string;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className={`skeleton${className ? ` ${className}` : ""}`}
      data-testid="skeleton"
      aria-hidden="true"
      style={{ display: "block", width, height, ...style }}
    />
  );
}

/** A few stacked lines — for a card body / key-value block. */
export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div data-testid="skeleton" aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <span
          key={i}
          className="skeleton skeleton-block"
          style={{ width: i === lines - 1 ? "60%" : "100%" }}
        />
      ))}
    </div>
  );
}

/** A KPI-tile grid placeholder. */
export function SkeletonTiles({ count = 5 }: { count?: number }) {
  return (
    <div className="skeleton-tiles" data-testid="skeleton" aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <span key={i} className="skeleton skeleton-tile" />
      ))}
    </div>
  );
}

/** A data-table placeholder matching the eventual table's column count. */
export function SkeletonTable({
  rows = 5,
  cols = 4,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div data-testid="skeleton" aria-hidden="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="skeleton-table__row">
          {Array.from({ length: cols }).map((_, c) => (
            <span
              key={c}
              className="skeleton skeleton-table__cell"
              style={{ width: c === 0 ? "70%" : "90%" }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
