---
name: ui-check
description: Look at a web UI change in a real browser at phone and desktop width, and run the layout check. Use after changing anything under web/, before saying a UI change is done, or when the user asks to see or verify it.
---

# Look at a UI change

Everything in the browser goes through Chrome DevTools (MCP). Playwright from the shell
(`mise run layout-check`, Python scripts) can't start Chromium in Claude Code's sandbox,
so don't reach for it.

1. A server to look at:
   - If the user has a dev server running (`mise run dev`, usually :5173 for the web and
     :8000 for the API), use it. Never stop or restart it.
   - Otherwise `mise run build`, then `scripts/scratch-server.sh` in the background
     (`run_in_background`): a copy of the local database on http://127.0.0.1:8765, sign in
     as scratch@example.com / scratchpass1. It never writes to `data/`. If 8765 is taken
     (someone else's), use `PORT=8766`. Stop it when done.
   - To see a state the data does not have (an error, a gap, a long name), change the
     scratch copy's database (`$TMPDIR/foxcam-scratch-<port>/foxcam.db`), never `data/`.
2. A page of your own: `new_page` with `isolatedContext` set to a name for this task (its
   own cookies, so other sessions using the same browser don't sign you out), and pass
   its `pageId` to every call. Sign in with `evaluate_script`:
   `async () => (await fetch("/api/auth/login", {method: "POST", headers: {"Content-Type":
   "application/json"}, body: JSON.stringify({email, password})})).status`, then reload.
3. Look: open the page (drawers and filters are in the URL query) and take screenshots
   at desktop width and at 390 px (`emulate` viewport `390x844x3,mobile,touch`), in dark
   and light theme (`emulate` `colorScheme`) when colours changed. `emulate` replaces all
   settings each call, so pass viewport and colour scheme together. Save screenshots
   under `out/ui-check/` in the checkout (`take_screenshot` `filePath`, absolute): the
   MCP server can't write outside the workspace, and `out/` is git-ignored. For a value
   the eye can't judge (a colour, a shadow), read `getComputedStyle` with
   `evaluate_script`.
4. Layout: on any signed-in page at desktop width, `evaluate_script` with `sourcePath` set
   to the absolute path of `scripts/layout_check.js` and `waitForStableDom: false`. It
   opens every page in two iframes (390 and 1280 px) and returns the problems as
   `"phone /events: …"` lines; `[]` means OK. It can take a minute. In the iframes
   "phone" is only the width (no touch), so also look at the pages you changed with the
   phone emulation from step 3. Fix what it reports in what you changed; mention anything
   else it finds.
5. Check by eye what the rules in AGENTS.md ask (spacing from the container, nothing
   wider than the drawer at 390 px, no jumping, i18n in both languages).
6. Clean up: reset the emulation, `close_page` your page and stop the scratch server
   (keep the screenshots: the report links them). Say what you looked at, at which widths
   and themes, and what you could not check. Show screenshots inline (markdown images
   with absolute paths).
