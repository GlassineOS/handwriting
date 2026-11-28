# Feature Specification: Writing regions

**Feature Branch**: `spec-005-writing-regions`
**Status**: Draft
**Input**: Any number of writing regions inside one app surface, each a handwritten field
with its own area, ruled lines, field type, languages and neatening, whose reading is the
field's value; per-region gestures and reports; a text mode edited with the system's
on-screen keyboard; edited text shown in the person's font back in ink mode; a display that
shows a region's handwritten words set in the person's font while their ink stays stored; ink
the app keeps and hands back to be read again.

## Context

Spec 004 gives an app handwriting for one writing surface: one area, one set of choices,
one piece of writing read at a time, and candidates the app reports on. Many apps have
more than one place to write on the same surface: a planner page with a field per hour,
a form, a list with a line per item, a notes field beside a title. Each of those places is
a field of its own, with its own type, language and lines, and its own text.

This spec extends spec 004 with **writing regions**: an app declares any number of regions
on one surface, and each behaves like a text field that the person fills by hand. The
reading of a region is its value, delivered whenever it changes, as a text field edited by a
keyboard delivers its text. A region can be switched to a text field edited with the
system's on-screen keyboard, and back; text that no longer matches the ink is shown in the
person's own font (spec 003).

Relation to spec 004: every requirement of spec 004 stays as it is. A surface that declares
no regions behaves exactly as spec 004 says. A surface that declares regions gets the
requirements below for the writing in them; the rules on identity (spec 004 FR-001), the
service holding one model (FR-005), privacy (FR-010), raw input for samples (FR-009), the
person's permissions (FR-014), versioning (FR-003) and the writing's size in physical
units (FR-016, FR-017) apply to each region as they apply to a surface.

## User scenarios

### Scenario 1 – A planner with many small fields (Priority: P1)

**Given** a planner app whose day page has a field per hour, a time field and a short
to-do list, each declared as a region with its own type (time, prose, a list item) and
ruled line, **When** the person writes "14:30" into the time field and "dentist" into the
two o'clock field, **Then** the time field's value becomes "14:30" and the two o'clock
field's "dentist", each read with its own field type, and no stroke of one ends up in the
other's value.

**Given** the person strikes through "dentist", **When** the stroke is recognised as a
strike-through, **Then** the two o'clock field's value becomes empty and the ink of the
word leaves the field; no other field changes.

**Given** the person draws a straight line through an empty part of the three o'clock field,
where no word is, **When** the stroke looks like a strike-through, **Then** it is not one: the
line stays in the field as the person's ink and is read with the rest of the field's writing.

### Scenario 2 – A notes field that grows (Priority: P1)

**Given** a notes field declared as one region with ruled lines, **When** the person
writes several lines and the app makes the field taller as the writing reaches its
bottom, **Then** the value holds every line in reading order, the region keeps its
writing while it grows, and writing on the new lines is read as part of the same value.

### Scenario 3 – Scrolling (Priority: P1)

**Given** a long page with regions, some of them scrolled out of view, **When** the
person scrolls, **Then** the app moves the regions' areas with the content, the ink in
each region stays where it was written relative to the region, nothing is read again
because of the scroll, and a stroke started after the scroll lands in the region now
under the pen.

### Scenario 4 – Text mode and back (Priority: P1)

**Given** a field whose value is "Meet the team at noon" read from the person's ink, **When**
the app switches it to text mode, **Then** the field shows "Meet the team at noon" as an
ordinary text field and the system's on-screen keyboard edits it.

**Given** the person changes "noon" to "one" and switches back to ink mode, **Then**
"Meet the team at" keeps the person's original ink, "one" is set in the person's font on the
field's line at the person's size, and the field's value is "Meet the team at one". The edit
counts as a correction: when the person lets this app learn, the ink of "noon" is not
stored as "one" (it doesn't fit), and the unchanged words may confirm their ink.

### Scenario 5 – A field edited as text, then written into again (Priority: P2)

**Given** the field of Scenario 4 in ink mode, part ink and part font, **When** the person
writes "sharp" after "one" with the pen, **Then** the new strokes are added to the field,
read as usual, and the value becomes "Meet the team at one sharp"; the set word "one" stays as
it is.

### Scenario 6 – The app is closed and opened again (Priority: P2)

**Given** an app that kept its regions' strokes and values, **When** it is opened again
and hands each region its stored strokes and value, **Then** each region shows the same
ink and font words as before, its value is the stored value, and further writing is read
as part of it.

**Given** the system reads with a newer model than when the ink was written, **When** the
app hands a region its stored strokes and value, **Then** the words the person never
confirmed or edited take the system's current reading, the words the person confirmed or
edited stay as they are, and the app needs to know nothing about the model.

### Scenario 7 – No handwriting service (Priority: P3)

**Given** a system without the handwriting service, **When** an app starts, **Then** the
capabilities the system announces tell it that writing regions are not available, and the
app can decide not to start without them.

### Scenario 8 – Shown in the person's font (Priority: P2)

**Given** a field whose value is "Meet the team at noon", read from the person's ink, **When**
the app switches the field's display to set (the person chose to see their writing set in
their font), **Then** each word is shown set in the person's font on its line, where its ink
stands, the value stays "Meet the team at noon", and the person's strokes stay in the field
unchanged. **When** the person writes "sharp" after it, **Then** it is read as usual and shown
set too. **When** the app switches the display back to ink, **Then** the person's ink is shown
again exactly as written, "sharp" included.

### Edge cases

- **A stroke crossing two regions** belongs to the region it starts in, whole; it is read
  only there, and a gesture it makes acts only there.
- **A region removed while the person writes**: its writing is forgotten by the service at
  once and no value is delivered for it afterwards. A stroke that started in it and is
  still being written is dropped: input the app said it has no interest in is never
  delivered.
- **Overlapping regions**: a stroke starting where regions overlap belongs to the topmost
  of them, as the app stacks them.
- **A stroke starting outside every region** on a surface with regions is not delivered
  through the pen interface at all, as with the ink areas: no region, no pen input.
- **Private fields**: a region whose type is private (as in spec 004 FR-010) sends nothing
  to the service, has no value from a reading, makes no samples and accepts no report;
  its raw strokes still reach the app. A region that becomes private is forgotten by the
  service at once. Its text mode works as any private text field does with the keyboard.
  Its raw strokes in region coordinates are the only handwriting it has.
- **A region resized mid-word**: the strokes it holds stay in it and the word goes on
  being read with them; only where new strokes start is decided by the new area. The
  writing's size reference is physical (FR-028), so a resize doesn't change it.
- **A lone character in a small field** (a digit in a planner cell): its size is judged in
  millimetres (FR-028), never against a line found from itself or from another region, so a
  lone `o` is not read as an `O` for lack of a line.
- **Text mode with an empty region**: the field opens empty; text typed into it is shown
  in ink mode wholly in the person's font.
- **A text edit that only deletes words**: the remaining words keep their ink.
- **A strike-through over no word** (in an empty part of a field, over a drawing): it is no
  gesture. Its stroke stays in the region as ink and is read with the rest of the writing, as
  part of a word or as ink that is no word, as the reading has it.
- **A gesture with the display set**: it covers the words where they are shown, set in the
  font, not where their hidden ink lies.

## Requirements

### Regions

- **FR-001**: An app can declare any number of writing regions on one surface, each
  named by the app, and remove them. A region is a field of its own: regions never share
  writing, readings, gestures, values or reports.
- **FR-002**: Each region has its own: area on the surface; ruled lines; field type
  (input purpose and hints, as in spec 004 FR-006) and languages; reading on or off;
  neatening on or off and its strength. The app can change any of them at any time; a
  change of reading, field type or languages applies to writing that follows it, a change
  of neatening or strength also to the writing the region holds (spec 004).
- **FR-003**: A region's ink and ruled lines are in the region's own coordinates, so the
  app can move the region on the surface (scrolling, layout changes) without the ink, the
  lines or the reading changing. Moving or resizing a region never causes its writing to be
  read again.
- **FR-004**: The app states where on the surface each region takes strokes, which can be
  smaller than the region (partly scrolled out of view, partly covered), and updates it as
  the layout changes. A region's area may grow or shrink while it holds writing; what it
  holds stays.
- **FR-005**: A stroke belongs to the region in which it starts, whole, wherever it
  ends, and is read only in that region. Where regions overlap, the topmost as the app
  stacks them gets it. A stroke that starts outside every region is not delivered through
  the pen interface; a stroke whose region is removed before it ends is dropped.
- **FR-006**: Each region with reading on has its own reading, in the region's field type
  and languages, with the region's own area and ruled lines and the size reference of
  FR-028. Reading one region never waits on another.
- **FR-007**: A region costs the service nothing until it holds writing; declaring many
  empty regions (a planner page) takes no reading resources.

### The value

- **FR-008**: The value of a region is its text: its current best reading (or the
  candidate the person picked), combined with any text the person set by keyboard (FR-016
  to FR-021). The region delivers the whole value
  to the app every time it changes, as a text field edited by a keyboard delivers its text.
  There is no revision number, change list or other comparison machinery; an app that
  wants to know what changed compares values itself.
- **FR-009**: A region with several lines of writing gives a value with its lines in
  reading order, separated as lines, and its words in the order they are written on each
  line.
- **FR-010**: With each value the region tells the app which of its strokes make up the
  value and which strokes no longer belong to it (struck out, or replaced by an edit), so
  the app knows what to draw. Ink that is struck out or replaced by an edit leaves the
  region for good, so a region's ink and its value always match. With each value the
  region also gives the app a saved state: which words the person confirmed or edited, and
  where they are. It is opaque to the app.
- **FR-011**: At a pause a region also delivers ranked alternatives for its writing
  (candidates, as in spec 004 FR-007), so any app can offer them; an app that only wants
  the value can ignore them. A picked candidate becomes the value.

### Gestures

- **FR-012**: The gestures the recognizer reports act within the region they are written
  in: a strike-through or scratch deletes the words of the value it covers, a caret marks
  where the next writing is inserted into the value. The gesture's own stroke is not part
  of the value. A gesture never acts on another region. A gesture covers a word where the word
  is shown (its ink, or the shape it is set or shown set in, FR-030). A strike-through that
  covers no word of the value is no gesture: its stroke stays in the region as the person's
  ink, and the region reads it with the rest of its writing (as part of a word, or as ink that
  is no word, FR-010).

### Reports and learning

- **FR-013**: The app reports per region what the person did with its writing, as in
  spec 004 FR-008: picked a candidate, confirmed the value as it is, or corrected it to a
  text the person gave. A report applies to the writing
  the reported value was read from; writing added later is not part of it. A report never
  ends the region's writing: the ink stays the field's content.
- **FR-014**: Reports are the confirmation signal for the person's samples and learning
  exactly as in spec 004: samples come only from raw pen input (spec 004 FR-009, spec 003
  FR-024), only with the person's permission for the app (spec 004 FR-014), and never from
  a private region (spec 004 FR-010). A word is stored as a sample only with ink of its own
  and only when that ink fits its confirmed text; a word that has no ink (typed, or set in
  the font) never becomes a sample.

### Text mode

- **FR-015**: An app can switch a region, or all regions of its surface at once, into
  text mode and back into ink mode. In text mode the region takes no pen strokes; the pen
  acts there as it does on any text field.
- **FR-016**: In text mode the region's current value is shown as an ordinary text field
  and edited with the system's on-screen keyboard, through the system's normal text input
  for the region's field type and languages.
- **FR-017**: A text edited in text mode becomes the region's value when the region goes
  back to ink mode, and counts as a correction of the words whose ink it changed: those
  become samples only under FR-014.

### Ink mode after a text edit

- **FR-018**: A region whose value no longer matches its ink shows the value in ink mode
  word by word: a word left unchanged keeps the person's original ink (raw or neatened as
  the region's neatening has it); an edited or added word is set in the person's font
  (spec 003) on the region's line, at the person's size, where the word stands in the
  text, or written there in the person's hand where that is chosen (spec 007 FR-018). Text
  an app hands a region as from elsewhere can be shown in another hand (spec 007 FR-026).
- **FR-019**: Words set this way are a different thing from spec 003 FR-043's set words:
  they have no ink, they are drawn from the font alone, and they are never data about the
  person's hand (FR-014). At the neatest strength, a word whose ink is kept stays the
  person's ink and is neatened as spec 003 says for its strength; it is not replaced by
  the font, even though its text is now confirmed.
- **FR-020**: When the value can't be shown word by word (the words of the text can't be
  matched to the ink), the region's whole value is set in the person's font instead, and
  none of its ink is shown.
- **FR-021**: A set word is set at the size of the person's writing in that region, else
  at the size of their letters in millimetres, else at the typical handwriting size of
  spec 004 FR-016 (FR-028). The writing's size is measured against the person's own letters
  (a hand whose tall letters reach far above its small ones is not taken for a larger hand),
  and stays within half again of the size of their letters in millimetres either way, so
  that ink that is no writing (a line, a drawing read as a letter) never sets the font far
  off the person's hand. The line a set word goes on is the line of the word before it, else the region's
  first line; a first line with nothing on it lies low enough for the person's tall letters
  to stay inside the region, its first word clear of the region's left edge. When an edited word is wider or narrower than the ink it replaces, the kept
  ink after it on the line moves along the line by the difference, unchanged in shape.
  Whether words that no longer fit the region's width wrap to its next line is the app's
  choice.
- **FR-022**: Set words use the person's font (spec 003). The person's font always exists:
  it starts as a plain base font and grows with the person's writing, so there is no case
  without it.
- **FR-023**: New pen strokes in such a region are added to it and read as usual; their
  reading joins the value at the place they are written, and the words set from the text
  stay as they are.

### Who does what

- **FR-024**: The app draws everything a region shows (kept ink, neatened ink and set
  words) and keeps the regions' strokes and values in its own documents. The system routes
  each stroke to its region, keeps private regions from the service, and gives the app per
  region the value, which strokes make which word of it, and, for each word to be set, its
  shape and place from the person's font, so an app can draw set words without having the
  font itself; with the display set (FR-030) the same for each word shown set, along with
  the strokes that make it. The app never computes a reading, a word match or a font layout.

### Display

- **FR-030**: An app can set how a region shows its value's words: as ink (the start) or set.
  With the display set, every word of the value that has ink is shown set in the person's font
  (spec 003) instead of as its ink; words without ink (FR-018) are set either way; ink that is
  no word (a drawing) is shown as written. Switching back to ink shows the ink again, raw or
  neatened as the region's neatening has it, exactly as before.
- **FR-031**: The display changes only how the words are shown, never what the region holds:
  its strokes stay as written, stored and handed back as before, no stroke leaves the region
  for it, and the value, the saved state, readings, candidates, reports and samples are the
  same with either display (a word shown set keeps its ink for samples under FR-014). Changing
  the display reads nothing again. New writing in a region with the display set is read as
  usual and its words are shown set too; stored ink handed back to such a region (FR-025) is
  shown set as well.
- **FR-032**: A word shown set stands on its line (the line its ink sits on) at the size of
  FR-021, from where its ink starts, moved right only as far as needed to stand a word space
  after the word before it on its line. The app gets its shape and place with the strokes that
  make it (FR-024) and draws the shape in place of the strokes.

### Live ink

- **FR-029**: Ink appears under the pen for every stroke a region takes, as fast as
  anywhere else on the device, from the first point of the stroke to its last, with
  reading on or off and in a private region alike; declaring the region is all the app
  does for it. Like the stroke, the ink belongs to where the stroke starts: it follows the
  stroke past the region's edge, as far as the app's window is visible (never over
  anything covering the window, such as a menu, the keyboard or another window). A stroke
  that starts where no region takes strokes (outside every region, a region in text mode
  and what it covers) leaves no such ink anywhere, also where it crosses into a region.
  The app's own drawing of the stroke follows as FR-024 says.

### Stored ink

- **FR-025**: An app keeps a region's saved state (FR-010) with its strokes and value, and
  hands it back unchanged with them; it never reads, changes or builds a saved state
  itself. An app can hand a region strokes it kept (from an earlier run or another
  document) with their value and saved state, or the strokes alone. The region reads the
  strokes and shows the result as FR-018 to FR-020 say. Strokes alone are read, and the
  reading is the value.
- **FR-026**: Whenever the system reads stored strokes it reads them with the model it has
  at that time, so a newer model's reading reaches the value without the app knowing about
  models. A reading never changes words the person confirmed (a pick or a confirmation) or
  edited; it changes only words that come from an earlier reading alone. For stored ink,
  the confirmed and edited words are the ones its saved state names.

### Availability

- **FR-027**: Writing regions depend on the handwriting service. The capabilities the
  system announces (spec 004 FR-003) say whether regions are available, and change when the
  service comes or goes; an app may refuse to start without them.

### The writing's size

- **FR-028**: A region's writing size is judged in physical size as spec 004 FR-016 and
  FR-017 say for the whole pen interface, including an output of unknown size (the
  person's setting, else the default density). The region's ruled line is a reference only
  when the app sets one for that region; a region never takes a line from another region.

## Key entities

- **Writing region**: a field on an app surface, named by the app: its area, where it
  takes strokes, ruled lines, field type, languages, reading and neatening choices, its
  mode (ink or text), its strokes and its value.
- **Value**: the region's text, from its reading and the person's text edits (FR-008).
- **Word layout**: for each word of the value, the strokes that make it, its shape and place
  set in the person's font, or with the display set both (FR-010, FR-024, FR-030).
- **Display**: how a region shows its value's words: as ink, or set in the person's font; a
  choice of the app, never part of what the region holds (FR-030, FR-031).
- **Report**: what the person did with a region's writing: picked, confirmed or corrected
  (FR-013).
- **Saved state**: what a region needs to know again about its value: the words the person
  confirmed or edited and their places; opaque to the app (FR-010, FR-025).
- **Stored ink**: strokes, with their value and saved state or alone, that an app kept and
  hands back to a region (FR-025).

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Pen interface, with writing regions (regions, their choices and modes in; per region raw and neatened strokes, values, word layout out; reports and stored ink back) | owned (this repository specifies it; its protocol definition is owned by GlassineOS/protocols) | the next version of spec 004's pen interface, additive: an app on the earlier version keeps spec 004's behaviour | this spec; spec 004 |
| Service interface (read ink, samples, permissions) | owned by spec 004, unchanged | 1 | spec 004; docs/service.md |
| The person's font (always present, growing with the person's writing) | owned by spec 003, consumed for setting words | 1 | spec 003 |
| Recognizer gestures and readings | owned by spec 001, consumed per region | 1 | docs/recognizer.md |
| System text input and on-screen keyboard | consumed for text mode | — | GlassineOS/shell spec 006 |

## Device scope and risk

Device-neutral. No device risk. User data: regions follow spec 004's privacy and permission
rules; the app keeps its own strokes and values, the person's store stays with the service.

## Acceptance

Host level:

- a test app with several regions on one surface and a stand-in pen gets each stroke in
  the region it started in, including strokes ending in another region, and each region's
  value from its own reading, field type and languages;
- moving every region by a scroll offset changes no value, causes no reading and keeps
  every stroke's region coordinates;
- a strike-through in one region deletes words only in that region; a strike-through over no
  word is no gesture, stays in the region and is read with its writing;
- a private region causes no service traffic and no sample, and its raw strokes still
  arrive;
- a region taken to text mode, edited and taken back shows unchanged words as their ink,
  edited and added words as set words in the person's font, and reports the edit as a
  correction that stores no sample for a word whose ink doesn't fit its new text;
- a person with no samples yet gets set words in the plain base form of their font;
- a lone character in a region without ruled lines is read against the size prior in
  millimetres, and its reading does not change when other regions hold writing;
- a stroke starting outside every region is not delivered; a stroke whose region is
  removed mid-stroke is dropped; a stroke in an overlap goes to the topmost region;
- strokes, value and saved state handed back to a fresh region give the same value and
  word layout as before; strokes alone give the model's reading; with another model, confirmed and edited
  words stay as they were;
- a surface with regions and no other ink areas has ink areas exactly where its regions
  take strokes: they follow a region's move, clip and mode, a region in text mode above
  another cuts it, and nothing outside the regions is an ink area; a stroke starting in a
  region shows ink along its whole path over the visible window, past the region's edge,
  and a stroke starting outside every region shows none, also inside a region;
- a region switched to the display set and back gives every word with ink as its strokes and
  its shape in the person's font, then as its strokes again, with the same value, strokes and
  saved state, no stroke leaving and nothing read again; a word shown set stands where its ink
  starts, after the word before it; new writing and stored ink handed back are shown set; a
  strike-through over a word shown set deletes it;
- without the service, the capabilities say regions are not available.

Product level: in the images acceptance suite, a demo app with a page of small fields and a
notes field: the person writes in two fields, scrolls, edits one field as text, writes
into it again, closes and opens the app, and each field shows and holds the same value.

## Out of scope

- Peek and correction pop-ups (alternatives shown at the pen, inline correction menus).
- Version history of a field's value, undo history across sessions, or any change
  tracking in the interface.
- Calendar sync, or any link between a region's value and data outside the app.
- How an app stores its ink and values in its own documents.

## Success criteria

- **SC-001**: A surface with 100 declared regions, none written in, uses no more memory in
  the service than a surface with none.
- **SC-002**: A region's value updates as fast as spec 004's reading of the same writing
  (no later than its reading after the last stroke), whatever the number of regions on the
  surface.
- **SC-003**: Routing a stroke to its region adds no visible delay to the raw stroke (spec
  004 SC-003).
- **SC-004**: Moving or resizing regions (a scroll) causes no reading and no extra panel
  refresh beyond the app's own redraw of the moved content.
- **SC-005**: On the recorded test ink, after a text edit that changes one word of a line,
  every unchanged word keeps its own ink in at least 95 % of the lines.
- **SC-006**: Going from text mode back to ink mode shows the region in one redraw of that
  region, without a full-panel refresh.
- **SC-007**: A developer with a working spec 004 app can add several regions with text
  mode from the documentation alone in under a day.
- **SC-008**: Switching a region's display shows it in one redraw of that region, without a
  full-panel refresh and without reading its writing again.
