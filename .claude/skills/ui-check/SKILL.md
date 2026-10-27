---
name: ui-check
description: Look at a web UI change in a real browser at phone and desktop width, and run the layout check. Use after changing anything under web/, before saying a UI change is done, or when the user asks to see or verify it.
---

# Look at a UI change

1. A server to look at:
   - If the user has a dev server running (`mise run dev`, usually :5173 for the web and
     :8000 for the API), use it. Never stop or restart it.
   - Otherwise `mise run build`, then `scripts/scratch-server.sh` in the background
     (`run_in_background`): a copy of the local database on http://127.0.0.1:8765, sign in
     as scratch@example.com / scratchpass1. It never writes to `data/`. Stop it when done.
   - To see a state the data does not have (an error, a gap, a long name), change the
     scratch copy's database (`$TMPDIR/foxcam-scratch-8765/foxcam.db`), never `data/`.
2. Chrome DevTools (MCP): sign in, open the page (drawers and filters are in the URL
   query), and take screenshots at desktop width and at 390 px (`emulate` viewport
   `390x844x3,mobile,touch`), in dark and light theme when colours changed.
3. Layout: `mise run layout-check` (needs FOXCAM_SERVER, FOXCAM_LAYOUT_EMAIL and
   FOXCAM_LAYOUT_PASSWORD; Playwright). If Playwright can't start, run
   `scripts/layout_check.js` in the page with `evaluate_script` (sourcePath) at both
   widths. Fix what it reports in what you changed; mention anything else it finds.
4. Check by eye what the rules in AGENTS.md ask (spacing from the container, nothing
   wider than the drawer at 390 px, no jumping, i18n in both languages).
5. Reset the emulation, stop the scratch server, and say what you looked at, at which
   widths, and what you could not check.
