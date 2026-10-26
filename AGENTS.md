# Working on Foxcam

Rules agreed while building this project. `CLAUDE.md` is a symlink to this file.

## Git

- The repository is public: don't push to `main` on your own. Work on a branch and open a
  pull request, or commit locally and ask before pushing. Push to `main` only when asked to
  for that change.
- Nothing private in commits: no secrets, tokens, real camera URLs, IP addresses or
  personal data, in code, docs, screenshots or commit messages.
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
- A list item's detail drawer (`DetailPanel`) has no backdrop: the list stays usable, and
  picking another row switches the drawer in place (open, same scroll position). Don't
  remount what's in it per item (no `key={id}` on the video player); keep the previous
  item's data on screen until the new one arrives (`keepPreviousData`).
- Confirmations for removing things are a `ConfirmPopover` at the button, never a drawer
  and never `window.confirm`. Destructive actions always ask. Wiping a whole kind of data
  (Settings → Danger zone) also wants a word typed (`typeToConfirm`).
- Drawer footers: back / other-option buttons left aligned, the main action right aligned;
  a danger "Remove" is a flat button on the left. The footer's buttons fit on one row at
  phone width: there the danger button is icon only (with an `aria-label`).
- Nothing in a drawer is wider than the drawer at phone width (check at 390 px): long
  words wrap, dropdowns shrink, and dropdown options are short (a word or two) with the
  explanation in the field's hint, not in the option text.

### Components

- Use the shared components, and prefer them over building the same thing by hand: when a
  component's name says what something is (`CameraTag`, `ProfileTag`, `Fact`, `DataTable`,
  `DrawerTitle`, `ConfidenceBar`, `Checkbox`, `DateTimeInput`, `ConfirmPopover`…), use it.
- When the same markup shows up in a second place, make it a component with a name that
  says what it is, and switch the first place over too.

### Layout

- No page headers: the nav shows where you are. Sub-pages are tabs (`PageTabs`) with their
  own URL; they scroll sideways on narrow screens.
- A page's add buttons (Add camera, Add profile, Add worker, Add channel, New API key…)
  sit in the same place on every page, under the tabs, never squeezed against the content
  next to them (`section-actions`, or spacing like it).
- Lists of things with several columns are a `DataTable` (`components/DataTable.tsx`): a
  table on desktop, and on phones one card per row with the columns as `Facts` (the
  column's `phone` option puts a picture on top or buttons under the facts). No hand-made
  tables, and no CSS that restyles table cells to look like facts.
- Label/value facts use the shared `Fact` component; groups of values (cameras, labels,
  tags) are `Tag`s in a `chips` wrapper, not comma-separated text.
- Camera and profile names are `CameraTag` / `ProfileTag` in tables, facts and lists
  (titles stay text), never `<Tag>{cameraName(id)}</Tag>` by hand.
- Do a layout pass on every change: no values wrapped mid-word, no cropped tags, even
  vertical gaps between wrapped buttons, nothing that jumps when its content changes (status
  dots, day/night tags), spinners with room around them.
- Vertical spacing comes from the container, not from luck: put stacked blocks (headings,
  banners, fields, cards) in a `.form-stack` (its gap spaces them), or use the classes that
  space themselves: `h2.section` for a page-level heading, `className="block"` on a
  `MessageBar` above content. `h2.panel-heading` is for headings inside a `.form-stack`.
  A block touching the next one is the most common mistake here: `mise run layout-check`
  finds it.
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
- One video player component everywhere (`VideoPlayer`): Vidstack plays the video, the
  controls are our own Fluent parts (speed in the bar, no settings or PiP buttons). Add to
  it by changing our controls, not by patching Vidstack's markup; anything that pops up
  inside it (menus) is `inline` so it shows in fullscreen. Timeline `markers` (e.g. motion)
  are what a double tap at the sides jumps between. Clicks and taps act at once (no
  waiting to tell them from a double click; a double click is not fullscreen).
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
- UI changes: `mise run layout-check` against a running server (FOXCAM_SERVER,
  FOXCAM_LAYOUT_EMAIL/PASSWORD). It opens every page at phone and desktop width and reports
  blocks closer than 8 px and anything wider than the screen; fix what it reports. Where
  Playwright cannot start a browser, run `scripts/layout_check.js` in the page with a browser
  tool instead.
- Look at UI changes in a browser at phone and desktop widths when possible, and say so when
  you could not.
