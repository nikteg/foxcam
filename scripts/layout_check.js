// The in-page part of scripts/layout_check.py (also usable on its own, e.g. from a
// browser's devtools): returns the layout problems on the open page as strings.
(minGap = 8) => {
  const problems = [];
  const name = (el) => {
    // Fluent's generated class names (f1abc…, ___xyz) say nothing; fui-* and ours do.
    const cls = [...el.classList].filter((c) => c.startsWith("fui-") ||
      !(c.startsWith("___") || /^[a-z][a-z0-9]{5,}$/.test(c) && /[0-9]/.test(c)))
      .slice(0, 2).join(".");
    const text = (el.innerText || "").trim().split("\n")[0].slice(0, 40);
    return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}${text ? ` "${text}"` : ""}`;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const main = document.querySelector("main") || document.body;

  // 1. Sideways: the page itself, and elements past the right edge.
  const vw = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > vw + 1) {
    problems.push(`page scrolls sideways (${document.documentElement.scrollWidth} px > ${vw} px)`);
    // Text spilling out of its own box (e.g. a long value that may not wrap).
    for (const el of main.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (visible(el) && s.overflowX === "visible" && el.scrollWidth > el.clientWidth + 1 &&
          el.clientWidth > 1 && ![...el.children].some((c) => c.scrollWidth > c.clientWidth + 1)) {
        problems.push(`content spills ${el.scrollWidth - el.clientWidth} px out of ${name(el)}`);
      }
    }
  }
  const scrolls = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const o = getComputedStyle(a).overflowX;
      if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
    }
    return false;
  };
  const past = (el) => visible(el) && el.getBoundingClientRect().right > vw + 1;
  for (const el of document.body.querySelectorAll("*")) {
    // The innermost element that sticks out (its parents stick out because of it).
    if (!past(el) || scrolls(el) || [...el.children].some(past)) continue;
    const r = el.getBoundingClientRect();
    problems.push(`past the right edge by ${Math.round(r.right - vw)} px: ${name(el)}`);
  }

  // 2. Too close: consecutive blocks one above the other.
  const BLOCKS = "h1, h2, h3, .fui-MessageBar, .fui-Field, .stats, .fui-Card, table, " +
    ".data-table, .fui-TabList, .fui-Divider";
  const blocks = [...main.querySelectorAll(BLOCKS)].filter((el) =>
    visible(el) && !el.closest("[role=dialog], .fui-Popover, .fui-PopoverSurface") &&
    getComputedStyle(el).position !== "absolute");
  // Only outermost blocks: a card's own fields are the card's business.
  const outer = blocks.filter((el) => !blocks.some((o) => o !== el && o.contains(el)));
  for (let i = 1; i < outer.length; i++) {
    const a = outer[i - 1].getBoundingClientRect();
    const b = outer[i].getBoundingClientRect();
    const sideBySide = b.left >= a.right - 1 || b.right <= a.left + 1;
    if (sideBySide || b.top < a.top) continue;
    const gap = b.top - a.bottom;
    if (gap < minGap) {
      problems.push(`${gap < 0 ? "overlaps" : `only ${Math.round(gap)} px`} between ` +
        `${name(outer[i - 1])} and ${name(outer[i])}`);
    }
  }
  return problems;
}
