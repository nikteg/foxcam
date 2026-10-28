// Layout check: the layout problems the eye keeps missing (AGENTS.md → Web UI → Layout),
// as strings. Blocks closer than minGap px, and anything wider than the screen.
//
// - Chrome DevTools MCP, signed in on any page of the app: evaluate_script with sourcePath
//   set to this file (absolute path). It opens every page in PAGES in two same-origin
//   iframes, phone and desktop width, and returns "phone /events: …" lines; [] is OK.
//   In the iframes "phone" is only the width: (hover: none) and (pointer: coarse) styles
//   stay as on the desktop.
// - ({ here: true }) checks only the open page (scripts/layout_check.py does this).
// - ({ list: true }) returns PAGES; ({ pages: ["/jobs"] }) checks only those.
async ({ here = false, list = false, pages = null, minGap = 8 } = {}) => {
  const PAGES = [
    "/", "/events", "/events/detections", "/events/models", "/events/motion",
    "/recordings", "/recordings/segments",
    "/jobs/preparation", "/jobs/archiving", "/jobs/detection", "/jobs/detection-workers",
    "/cameras", "/cameras/profiles", "/cameras/integrations",
    "/settings/general", "/settings/detection", "/settings/retention", "/settings/users",
    "/settings/notifications", "/settings/storage", "/settings/configuration",
    "/settings/danger", "/account",
  ];
  const SIZES = { phone: [390, 844], desktop: [1280, 800] };
  if (list) return PAGES;

  // The check itself, on one window (the open page or an iframe's).
  const check = (win) => {
    const document = win.document;
    const getComputedStyle = (el) => win.getComputedStyle(el);
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
  };
  if (here) return check(window);

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Until no request has finished for 700 ms (data, images, fonts), at most 15 s.
  const settle = async (win) => {
    let seen = -1;
    for (let quiet = 0, t = 0; quiet < 700 && t < 15000; t += 100) {
      await sleep(100);
      const n = win.performance.getEntriesByType("resource").length;
      quiet = n === seen && win.document.readyState === "complete" ? quiet + 100 : 0;
      seen = n;
    }
    await sleep(300); // late layout
  };
  const run = async (label, [width, height]) => {
    const paths = pages ?? PAGES;
    const frame = document.createElement("iframe");
    frame.style.cssText = `position: fixed; top: 0; left: 0; width: ${width}px; ` +
      `height: ${height}px; border: 0; z-index: 2147483647; background: Canvas`;
    const loaded = new Promise((r) => frame.addEventListener("load", r, { once: true }));
    frame.src = paths[0];
    document.body.append(frame);
    await loaded;
    const win = frame.contentWindow;
    win.performance.setResourceTimingBufferSize(100000);
    const found = [];
    for (const [i, path] of paths.entries()) {
      if (i > 0) {
        // In-app navigation (as a link would), not a reload per page.
        win.history.pushState(null, "", path);
        win.dispatchEvent(new win.PopStateEvent("popstate"));
      }
      await settle(win);
      if (win.location.pathname === "/login" && path !== "/login") {
        found.push(`${label} ${path}: not signed in`);
        continue;
      }
      for (const problem of check(win)) found.push(`${label} ${path}: ${problem}`);
    }
    frame.remove();
    return found;
  };
  const found = await Promise.all(Object.entries(SIZES).map(([label, size]) => run(label, size)));
  return found.flat();
}
