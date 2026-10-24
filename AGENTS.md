# Working on Foxcam

Rules agreed while building this project. `CLAUDE.md` is a symlink to this file.

## Git

- Commit and push straight to `main` (no feature branches unless asked).
- Never force push. If it seems needed, write out the commands for a person to run.
- No backwards compatibility needed yet: links, URLs, config keys, headers and names may
  change freely.

## Writing

- Never use the middle dot (·) as a separator, in UI text, docs, commits or code. Use a
  hyphen (-) or an en dash (–).
- Every UI string goes through i18n, in both `web/src/i18n/en.ts` and `sv.ts`.
- Keep `README.md` and `docs/` up to date with what changed; `mise run screenshots`
  refreshes the README screenshots.

## Web UI

Stack: React + Vite, Fluent UI v9, oxlint, oxfmt, React Compiler. Must work well on phones,
iPad and desktop, in light and dark theme, and as an iOS home screen app.

### Overlays

- **No modals.** Never use Fluent's `Dialog` (or any centred modal). Everything that would be a
  dialog is a drawer from the right: import `Dialog`, `DialogSurface`, `DialogBody`,
  `DialogTitle`, `DialogContent`, `DialogActions` from `components/Drawer.tsx`, not from
  `@fluentui/react-components`.
- Drawers: clicking the backdrop closes them, also on desktop; the same backdrop
  everywhere; no slide animation on phones (they cover the screen).
- Confirmations for removing things are a `ConfirmPopover` at the button, never a drawer
  and never `window.confirm`. Destructive actions always ask.
- Drawer footers: back / other-option buttons left aligned, the main action right aligned;
  a danger "Remove" is a flat button on the left.

### Layout

- No page headers: the nav shows where you are. Sub-pages are tabs (`PageTabs`) with their
  own URL; they scroll sideways on narrow screens.
- A page's add buttons (Add camera, Add profile, Add worker, Add channel, New API key…)
  sit in the same place on every page, under the tabs, never squeezed against the content
  next to them (`section-actions`, or spacing like it).
- Lists of things with several columns are tables (`Table className="table responsive"`,
  `data-label` on cells so phones get labelled rows), not stacks of cards.
- Label/value facts use the shared `Fact` component; groups of values (cameras, labels,
  tags) are `Tag`s in a `chips` wrapper, not comma-separated text.
- Do a layout pass on every change: no values wrapped mid-word, no cropped tags, even
  vertical gaps between wrapped buttons, nothing that jumps when its content changes (status
  dots, day/night tags), spinners with room around them.
- Secondary actions are secondary: small or subtle buttons (e.g. "Reset to default"), smaller
  buttons in cards. Links that act as buttons look like buttons.
- Icons are one colour, without backgrounds.
- Help text that people can dismiss is a `MessageBar` with a close button, not permanent
  paragraphs.

### Behaviour

- Every page has a title (`usePageTitle`) so browser history reads well.
- Changing page scrolls to the top (back/forward restores); pressing the open nav tab
  scrolls up.
- Filters, open drawers and selected rows live in the URL query, so links and back work.
- Pressable cards show it (chevron, hover state, a checkmark when a filter is on).
- Checkboxes come from `components/ui.tsx` (`Checkbox`), so each label toggles its own box.
- Date and time inputs use `DateTimeInput` (iOS Safari shows them blank otherwise).
- One video player component everywhere (speed menu shown, no settings or PiP buttons).
- Keep it fast: no full-page spinners for filter changes, no refetching what is cached.

## Detection results

- A detection marked **wrong** (feedback false) is not a match anywhere: not in counts,
  "with a match" badges, event lists on the Status page, notifications or archives. An event
  is wrong when one of its detections is wrong and none is correct.
- The system detects anything a profile asks for; foxes are the main use, not a special case.
  Wording in the UI stays generic (events, matches, targets).

## Server and settings

- Settings an admin may tune live in the database and are edited in Settings; `config.yaml`
  supplies the defaults, and the UI can reset a value to it.
- Workers are not let in without a token unless configured to be.
- Prefer copying streams over re-encoding; anything that decodes or encodes video should use
  hardware acceleration where it is available (`hwaccel.py`).

## Checks before committing

- `mise run check` (lint, types, format, tests) and `mise run build`.
- Look at UI changes in a browser at phone and desktop widths when possible, and say so when
  you could not.
