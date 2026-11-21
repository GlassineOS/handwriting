# Feature Specification: Your handwriting (font and neatening)

**Feature Branch**: `003-your-handwriting`
**Status**: Draft
**Input**: A font made from a person's own handwriting, usable anywhere a font can be
chosen, and a way to neaten hasty ink towards the best version of that same handwriting,
both learned on the device from the person's samples.

## Context

The recognizer (spec 001) and the samples app (spec 002) already collect a person's
handwriting with exact labels: every character's strokes are known. The same samples can
describe how the person writes each letter at their best. Two features follow from that:

- **Autograph, the person's font**: their letters as an ordinary font, so their hand can
  set any text on the system (notes, titles, cards) the way a typeface does. Autograph is
  always installed: it starts as a plain base font and grows towards the person's hand as
  their samples teach it each character.
- **Neatening**: hasty ink moved towards the person's own best letters, with straight
  lines and even sizes. The result is still their handwriting, not a typeface: the target
  is learned from them. Neatening is a layer around the ink surface of the e-ink stack: it
  takes the pen's strokes, has them neatened and passes the neatened vector strokes on, so
  any app that draws pen ink through it, a notes app as much as a drawing app, gets
  neatened strokes without doing the neatening itself.

Both rest on one model of the person's best letters (Key entities: letter model), which
needs signals the existing samples don't carry: which samples were written with care, and
which of two versions the person likes better. Spec 002 gets two modes that collect them.

This spec is about the person's own hand on their own device. Learning from many writers
(contributions, base models) stays as spec 001 and 002 describe.

## User scenarios

### Scenario 1 – Writing it your best (Priority: P1)

**Given** the samples app's Autograph tab, **When** the person chooses "Write it your best",
**Then** prompts are shown as usual but framed as unhurried, careful writing, and each
kept sample is marked as careful.

**Given** the paired variant, **When** the person writes the same prompt once quickly and
then once carefully, **Then** both samples are kept and linked as a pair.

### Scenario 2 – Which one is nicer? (Priority: P1)

**Given** the person has at least two samples of the same character or word, **When** they
choose "Which one is nicer?" on the Autograph tab, **Then** two of their own versions are
shown side by side, ink only, and a tap on one records the preference; "Both fine" and
"Skip" record nothing against either. Nothing is scored or ranked on screen.

### Scenario 3 – My handwriting as a font (Priority: P1)

**Given** a person with no samples yet, **When** they look for fonts anywhere fonts are
chosen (the settings app, text editors, notes apps), **Then** Autograph is there, drawn in
the plain base font.

**Given** samples, **When** the person opens the font page on the Autograph tab, **Then**
a preview shows a sentence in Autograph with the characters their samples cover in their
own hand and the rest in the base, the characters still in the base are listed with a way
to write them now, and Autograph is rebuilt in the background while the device charges (or
at once when the person asks) and apps that use it show the new version.

**Given** the font page, **When** the person exports Autograph under a name they choose,
**Then** they get the font files under that name, to keep or install elsewhere; the
installed Autograph stays as it is.

**Given** the font page, **When** the person picks one of the font's three styles (an even
pen line, a broad nib, a brush that follows their own pressure and speed), **Then** the
preview shows the same sentence in that style, letter for letter in the same places as
in the others.

**Given** Autograph has grown, **When** the person writes more samples and asks again,
**Then** it is rebuilt under the same name and apps that use it show the new version.

**Given** the font page, **When** the person opens a character, **Then** they see each way
they write it, drawn large, with how often the font shows it ("6 in 10"), and its samples,
each marked as counting or not with the reason in plain words. **When** they open a
sample, **Then** they see the whole word it belongs to with this character picked out and
what it was read as, and can write it again, delete it or leave it out of the font; a way
of writing it shows its own samples, with the same choices. **When** they make one way
their favourite, leave a way or a sample out (and put it back, or undo), write more of it
(careful prompts of the character alone and inside words) or say which of its ways is nicer
and close the character, **Then** just that character is learned again in the background
and the preview shows it; the installed font changes at its next rebuild (FR-011). A character
that isn't covered yet opens on writing more of it.

### Scenario 4 – Neaten my ink (Priority: P2)

**Given** an app that draws pen ink through the neat ink layer (the handwriting app's own
writing page, a notes app, a drawing app, the keyboard's writing paper) with neatening on,
**When** the person writes, **Then** each stroke appears under the pen as written, and once
the words it belongs to are read it is replaced by its neatened version: the same text in the
same hand. A strength control moves between the original and the neatest version, and the
original can be brought back at any time. Through the neat ink layer the neatest version
pulls the words towards the person's letters; it sets a word in their font only once its
text is confirmed (FR-043). The handwriting app's writing page is a playground: there the
neatest version sets each word at once as the page reads it (the reading it shows), so a
misread word is set as what it was read as.

### Scenario 6 – Drawing stays drawing (Priority: P2)

**Given** an app that draws through the neat ink layer with neatening on, **When** the person
draws shapes, arrows or a sketch beside their writing, **Then** those strokes stay exactly as
drawn; only what is read as writing is neatened.

### Scenario 5 – Letters not learned yet (Priority: P2)

**Given** ink containing characters the letter model doesn't cover, **When** it is
neatened, **Then** those characters get only the line-level corrections (baseline, slant,
size, spacing) and keep their shape.

## Requirements

### Collecting

- **FR-001**: A sample can carry how it was written: `careful` (written to "Write it your
  best"), `quick` (the quick half of a pair) or nothing (normal). The marking is part of the
  sample format (docs/samples.md) and travels with the sample in export and contribution.
- **FR-002**: A pair links a quick and a careful sample of the same prompt; deleting one
  sample of a pair keeps the other, unlinked. The link is a random value both samples carry,
  so it says nothing about the person and survives export and contribution unchanged.
- **FR-003**: A preference records two samples (or the same character in two samples, by
  its span) and which one the person chose. Preferences are the person's data: kept with the
  samples, exported with them, erased with them, contributed only together with both samples
  they refer to and under the same contribution rules (spec 002 FR-011). Deleting either sample
  removes the preference. In a file of samples (an export, a contribution) a preference is a
  record of its own beside the samples, naming the two samples of that same file it refers
  to; a reader that wants only samples can pass over it.
- **FR-004**: "Which one is nicer?" picks pairs that teach the most: characters with few
  preferences, versions that differ visibly, and characters the font or neatening uses
  most. It never shows a sample from a private field.
- **FR-005**: Careful and quick samples are also ordinary training samples for the
  recognizer (spec 001 FR-009); the modes add signals, they don't take samples away.

### In the samples app

- **FR-042**: Everything in this spec that makes or uses the font has a tab of its own in
  the samples app (spec 002 FR-026), **Autograph**, in the owner's session only. It opens on
  the font page (Scenario 3: the preview, the styles and the nib, install, and each
  character's page with its samples, FR-033, where a single character is also learned again,
  FR-032). Below the font's letters, rows open "Write it your best" (Scenario 1), "Which one
  is nicer?" (Scenario 2) and Neaten my writing (the app's own page that draws through the
  neat ink layer, Scenario 4); each leads back to the font page. The font's counts per
  character (FR-030) are on the font page and the character's page, apart from the
  recognizer's coverage beneath Learn. A guest's session has no Autograph tab: "Write it
  your best" is a prompt set in its Write tab, and "Which one is nicer?" isn't offered
  there (spec 002 FR-018).

### The letter model

- **FR-006**: For each character the letter model holds a variant for each way the person
  writes it often enough: their usable samples of the character are grouped by structure
  (stroke count, stroke order and rough shape), and a group gives a variant when it holds
  at least 3 samples and at least a tenth of the character's samples (a one-off slip gives
  none). A character has up to 5 variants, each weighted by how often the person writes it
  that way. Each variant is built from the samples of its group with the person's
  preferences, careful marking, readability (how well the recognizer read it when it was
  written) and consistency (closeness to the group's typical shape) as weights. Jitter is
  removed; shape, proportions, size, stroke count and stroke order stay the person's (a
  variant is as tall and as wide as the samples it is made from are on average; making
  it never shrinks it), and a variant sits on the line where the samples it is made from
  sit on average. The samples are matched to each other point by point before they are
  averaged (a loop's top with a loop's top, a cusp with a cusp, wherever along the stroke
  each sample has it), so a variant keeps the loops, cusps and height its samples share
  instead of the blur of points that don't correspond; where that matching would leave a
  variant more wobbly than its samples averaged without it, it is smoothed, its cusps
  kept, so a fluid curve stays fluid. Its width then follows how the person writes running text: where they
  write letters narrower in sentences than in single words, every variant is narrowed
  about its middle by that ratio, learned from the characters seen in both (at least 10
  of them, else no narrowing, and never by more than the spacing's own bound), its
  profiles, reaches and middle with it, so the spacing (FR-007) stays at its distances. A variant
  is never made by mixing versions written with different strokes. How often the font
  shows each variant follows from how often the person writes it that way and from their
  marks (FR-031). Each variant carries
  its left and right ink profiles: how far its ink reaches to each side in each height band
  from the descender to the cap height, and, along each of its strokes from start to end,
  how hard and how fast the person wrote it on average over the samples it is made from
  (speed alone where the pen reported no pressure).
- **FR-007**: A character is covered when it has at least 3 usable samples: written to a
  prompt with its fit check passed (spans exact) or, for a prompt of one character alone,
  kept on the person's confirmation after a failed check (its strokes can only be that
  character's: a lone full stop or comma counts), and with strokes of its own. A covered
  character written joined counts as well: a stroke shared among the letters of a word is
  cut where its parts best match those letters' variants from their samples written apart,
  each letter taking its own part into its variants, and the bits of the joining stroke
  that lead into the letter and out of it are kept with it as its entry and exit (so the
  letter can be drawn joined as the person joins it); joined samples alone don't make a
  character covered. Which strokes are a
  character's goes by where the ink lies as well as by the order of writing: a stroke written
  after the rest (an i's dot, the bar of a t or an f, the arms of an E, the second stroke of
  an x) is part of the character it lies across or stands over, in words as well as alone,
  and a neighbour's stroke that the alignment put beside a small mark (a full stop, a comma)
  stays the neighbour's, so both count; a stroke about as long as the rest of its character
  (an H's second stem) is no late stroke. A character a stroke of a neighbour in its word
  crosses or starts or ends on (an f's or a t's bar written on into the next letter, a p's
  bowl begun on its stem and run on into the next letter) has a part of it in the
  neighbour's ink: neither of the two is usable there, so no way of writing a letter lacks
  its bar or its bowl. Line metrics (baseline,
  x-height, cap height, ascender, descender, slant) and spacing come from the person's word
  and sentence samples. Spacing follows how the person writes running text: a pair's
  distance and gap, the typical gap and how far letters reach come from their writing of
  several words together (sentences, free writing) where they wrote it often enough (at
  least 3 times for a pair); elsewhere from single words, scaled by how much closer the
  person sets letters in running text than in single words, learned from the pairs seen in
  both (at least 10 of them, else no scaling). The median stays the measure throughout. One spacing serves the font and neatening alike, and places
  each pair by what the variants actually used look like. A letter's middle is the mean
  position of its ink between the baseline and the x-height, each part weighted by how far
  it runs up or down: stems and bowls count, bars, hooks and connecting strokes hardly do,
  so an overhang (T, f, r, y) or a bar set differently in a word than in a variant doesn't
  move the letters. A pair's distance is from the middle of the first to the middle of the
  second. A pair of characters the person wrote next to each other at least 3 times is set
  at their median distance; a rarer pair at how far the person sets letters after its first
  character plus how far before its second, each learned from all the pairs that character
  was written in, when both were written next to others at least 3 times. The gap between
  two letters is the narrowest distance between the right profile of the first and the left
  profile of the second (between the pen's centre lines; their boxes when they share no
  height band). It guards the distance: between the baseline and the x-height a pair never
  comes closer than its gap, or half the person's typical gap where that is smaller (a bar or
  a hook may reach over the next letter, as the person writes it). The gap is the pair's
  median gap when written at least 3 times; else the value of its side shapes (the variants' sides grouped by
  profile: round, straight, diagonal, overhanging as in T, f, r, y); for a pair of side
  shapes seen too rarely as well, the person's typical gap, closer where the profiles leave
  more open space between them. A pair without a distance (too rarely written, or a
  character without ink between the baseline and the x-height) is set at its gap. Word
  spacing is the person's median gap between words. The same samples, markings and preferences
  always give the same letter model.
- **FR-008**: The letter model is rebuilt when samples, markings or preferences changed,
  under the same conditions as adaptation (spec 001 FR-009: charging and idle, or when the
  person asks), and stays on the device. Before the first sample it is empty: it covers no
  character and has no spacing of its own. A page that shows or uses it (the font page,
  neatening) says when it is out of date (its samples, markings, marks or
  preferences changed, or the way it is built changed) and offers to build it at once; the
  person asking has it built in the background at the lowest priority, with its progress shown
  and the device usable meanwhile. Building the letter model never changes the installed font
  (FR-011). Erasing samples (all, or one client's) erases the
  letter model's learned letters and the preferences with them and returns Autograph to the
  base font; deleting one sample leaves the letter model out of date until its next
  rebuild.
- **FR-031**: The person can mark the letter model, per character:
  - a **favourite** variant (at most one per character): the font shows it a fixed amount
    more often than the person writes it that way;
  - a variant **left out**: it stays in the letter model, shown as left out, but is neither
    in the font nor a neatening target; its samples still form it, so putting it back
    restores it as it was;
  - a sample **left out** for that character: it doesn't count as usable for it and shapes
    no variant of it. It is not deleted: it stays a sample, and adaptation, base training
    and contributions use it as before. Deleting it (spec 002) removes it everywhere, its
    marks with it.

  Each mark can be undone (put back). Marks are the person's data: kept with the samples
  on the device, erased with them, never exported or contributed. A mark changes the letter
  model at its next build and the installed font at its next rebuild (FR-011).
- **FR-032**: A single character can be learned again on its own: after a change to it
  (marks, new samples of it, preferences between its samples), its variants are rebuilt
  from its samples while the rest of the letter model stays as it was, in seconds, at the
  lowest priority; the font page's preview shows the result. Its marks then count as
  learned; when samples were added or removed since the last whole build, the letter model
  still counts as out of date.
- **FR-033**: Looking at a character's samples. A sample opened from a character shows the
  whole sample (the word or sentence it was written for) with this character's strokes
  picked out, and what the recognizer read when it was written (when the sample has that
  reading). It offers:
  - **Write it again**: the same text is shown in the character's page to be written anew.
    The new writing is checked as any prompted sample is (spec 002 FR-007). Only once it
    passes and is kept does it take the old sample's place: the old sample is deleted, and
    the new one keeps the old one's text, source, how it was written and its pair link
    (FR-001, FR-002), with its own ink, area, reading and time. The old sample's
    preferences and the marks on it go with it (they judged the old ink); the marks on the
    character's ways stay. Cancelling, or writing that fails the check, keeps the old sample
    as it was.
  - **Delete**: after a question that says the whole sample goes, with every character it
    writes, from the font and from recognition; the same deletion as in the sample view
    (spec 002 FR-009).
  - **Leave out** of the font for this character, and put back (FR-031).

  Each way of writing a character offers its samples: exactly the samples that form it,
  each opening the same way.
- **FR-030**: How many usable samples each character has is known from the samples
  themselves, whether the letter model has learned them yet or not: a page about the
  person's letters shows these counts as soon as it opens, says whether the letter model is
  up to date, out of date or being built, and never shows an empty page because the model
  hasn't learned the samples yet.

### The font

- **FR-009**: The font, **Autograph**, is a standard outline font the system installs for
  each person from the start, for them only. It is made of the person's letter model over a
  base font: it maps every character of the base and the space; a covered character is
  drawn from the letter model, every other character from the base, so no character falls
  back to a system font. The base's letters are taken as pen strokes (their centre lines),
  so they are drawn like the person's: in the person's ink width (the pen's width on the
  writing paper they wrote their samples on, relative to their x-height), scaled to the
  person's line metrics (FR-007), in all three styles (FR-034); before the person has
  samples, at the base's own metrics and ink width. A covered character's other variants
  are in the font without a character of their own; a base character has one way.
  Letters are set at the letter model's spacing: side bearings from the profiles and pair
  kerning that text shaping applies by default, for every pair of variants, so each pair
  of variants comes out where the spacing places it (FR-007) and words at the word spacing;
  letters the font joins are set as their join places them (FR-044). A base character takes
  part in the spacing as a variant does, by its profiles; a pair the person hasn't written
  is placed as FR-007 places an unseen pair.
- **FR-045**: The base font is redistributable under the repository's rules on models and
  data, covers every character of the language it is for, and is plain: an upright, unjoined,
  school-like print hand with no ornament, so the person's own letters replace it without a
  clash of styles. A language the recognizer reads may have a base of its own, the school
  print model taught in that language; Autograph draws from the base of the language the
  recognizer reads by default, and a language without one (or without a model yet) from the
  default base. The default base, used for English, is a school print model under the SIL Open
  Font License (an Edu Beginner family); German's is the German Grundschrift under the same
  licence (Playwrite DE Grund), covering German's letters (ä, ö, ü, ß and their capitals), the
  other Latin-1 and Latin Extended-A letters and German quotation marks besides the English
  set.
- **FR-034**: The font comes in three styles of one family, installed and updated together,
  that differ only in how wide the ink is along each stroke; the letters' shapes, their
  variants and how the font switches between them, the spacing and the kerning are the
  same in all three, so a text sets in the same places in each:
  - **Regular**: an even line of the person's ink width (FR-009).
  - **Calligraphy**: a broad nib held at one angle (about 35° by default; the angle is a
    setting on the font page): the line is widest where the stroke runs across the nib and
    thinnest where it runs along it, between widths set by the x-height.
  - **Brush**: the width follows how the person wrote each stroke (FR-006): wider where they
    pressed harder, and, more lightly, where they wrote slowly; strokes taper where they
    start and end, and quick flicks thin out. Where the pen gave no pressure, speed alone.
    A base character, written by no one, is drawn at an even pressure and speed.

  The font page shows the preview in the style the person picks. Each style is a full
  outline font of its own on the system (style names Regular, Calligraphy and Brush);
  overlapping strokes draw as one ink. The styles are a matter of the font and its preview:
  neatening and reading always use the person's ink as written.
- **FR-010**: Repeated letters don't look stamped: the font switches between a character's
  variants along the text, each about as often as the person writes it that way, the same
  way every time for the same text, by contextual
  alternates that text shaping applies by default (no setting in the app). The preview,
  spaced as the font spaces it,
  (Scenario 3) shows the same variants as the installed font for the same text, and the
  same neighbour rules (FR-038) and joined pairs (FR-039); it sets joined letters (FR-044)
  as two letters, apart.
- **FR-038**: Where the person's samples show that they write a character a particular way
  next to a particular neighbour (the character before it or after it), the font uses that
  way there, by contextual alternates that text shaping applies by default. A rule needs
  clear support: the pair written at least 6 times in usable samples, the same way in at
  least three quarters of them, that way's share there at least a quarter above its share
  in the cycle of FR-010 (a way the person writes everywhere needs no rule), and, checked
  on samples the rule was not made from, it picks the way the person wrote more often than
  the cycle does. Everywhere else the
  cycle chooses. A character's page shows how often the cycle shows each way ("6 in 10");
  a rule changes only the places it names.
- **FR-039**: A pair of letters the person writes joined in one stroke (FR-007) is drawn as
  one glyph made from their own joined samples, as a ligature that text shaping applies by
  default, in each of the three styles: when the pair is written joined in at least 4 usable
  samples and in at least three quarters of the samples where the person wrote that pair.
  The joined glyph is spaced like the two letters around it (its sides from its own ink
  profiles, kerned with its neighbours as any letter is), the characters around it keep the
  ways they'd have without it, and it reads, copies and searches as the two characters it
  stands for. A joined pair that is no longer written joined often enough leaves the font
  at its next rebuild. It has no joins (FR-044) to the letters beside it.
- **FR-044**: Letters the person joins are joined in the font, with joins drawn as the
  person draws them. Each joint in their letters written joined (FR-007) is a join: the
  stroke from where the first letter's own ink ends to where the second's begins. Joins are
  learned per letter pair and per kind of join; the kind is where the first letter's way of
  writing it ends (below the baseline, near it, at the x-height or above) and where the
  second's starts (the same heights, in its left or its right half), each from the end of
  the way's last stroke of its main body and the start of its first (or the stroke the
  person's exits leave from and their entries lead into, FR-007); a join never leaves or
  enters a mark: a stroke shorter than half the way's longest (a dot, a bar, an arm). A letter pair with at least 5 joins has a
  join shape of its own, a kind with at least 4 joins one for the kind: the mean of its
  joins, matched point by point along their length, each turned and scaled onto its own
  ends (a join whose ends nearly meet has no direction and counts for the ends alone). The
  shape keeps its curve only where it fits the person's joins better than a straight line
  does, each of them tried against the mean of the others, and is smooth: no rougher than
  0.01 by the roughness the letters' variants are measured with (a straight line has none);
  else the join is straight. The
  font joins two neighbouring letters
  of a word (letters only, no digits, marks or symbols) where the person writes that pair
  joined: in at least half of the samples with the pair written with known strokes (at
  least 3 of them, and joined at least twice); a pair written fewer than 3 times is joined
  where the person writes its kind of join joined in at least half of the at least 10 times
  they wrote it. The join takes the pair's own shape, else its kind's; a pair without a shape
  of either, or one the person writes apart, stays as two letters set apart. Each joined
  letter is drawn with its half of the join, by contextual alternates that text shaping
  applies by default, the two halves meeting exactly: the join runs from the first letter's
  end to the second's start along the learned shape, turned and scaled to fit between those
  ends (its curve grown by no more than half again its own), the halves meeting at the
  height the person's joins pass halfway. The cycle
  (FR-010), the neighbour rules (FR-038) and the joined pairs (FR-039) choose the letters'
  ways as without joins. Joined letters are set where the spacing places them (FR-007) as
  closely as one meeting place for each way of writing a letter and join allows, but never
  so near that the second letter's start comes before the first one's end plus the way the
  join runs across (at least a tenth of an x-height); a way
  whose half would run backwards or longer than 1.5 x-heights isn't joined with that join.
  In Regular and Calligraphy the join is drawn as any stroke; in Brush as a thin line (the
  brush's thinnest width that isn't a taper). A store without letters written joined gives
  no joins, and the font is the one it would be without them.
- **FR-040**: Random alternates are off: the font has none unless the font is built with
  them on request in the recognizer's interface (not offered on the font page). Built with
  them, apps whose text shaping applies random alternates (it does by default in HarfBuzz,
  so in GTK, Pango and the apps built on them) pick each character's way at random,
  weighted as in the cycle, the same way each time for the same text, and other apps keep
  the cycle; neighbour rules, joined pairs and joins apply either way. Which apps honour them is
  documented with the recognizer.
- **FR-011**: The font carries a version that changes with every rebuild, in the font and in
  its file name, so apps and the font cache pick up the new outlines without a restart of
  the session. A rebuild keeps the name Autograph. The font rebuilds itself in the background, at low
  priority while the device charges, once new samples have changed the letter model; the
  person can also rebuild it at once.
  Updating the font learns the letters first when the letter model is out of date, then
  makes the font from it; learning the letters alone (to see the preview first) leaves the
  installed font as it was.
- **FR-012**: The font never leaves the device except by the person's own export; it is not
  part of a contribution. The person can export Autograph, all three styles, under a name
  they choose: the exported fonts carry that name, with the base font's licence beside them
  (FR-045), and the installed Autograph keeps its own.

### Neatening

- **FR-013**: Neatening takes ink and returns ink: the same strokes in the same order, each
  point moved, with timing, pressure and tilt kept. The text it is read as comes from the
  recognizer (spec 001), which also tells which strokes form which character; where a
  stroke holds several letters (a joined word), each letter's part of it is where the
  stroke best matches that letter's glyph, so a joined letter reaches the pull with its own
  ink.
- **FR-014**: Neatening has four levels, from the least to the most change, each doing
  what the one before it does and more:
  - **Light**: line-level corrections only: a straight baseline, a steady slant, even letter
    size, and spacing (each pair of neighbouring letters moved so their gap goes towards the
    letter model's spacing for that pair, and while the letter model has no spacing of its
    own towards the line's own median gap, never the base font's, and the gaps between
    words towards the word spacing, by a limited step).
  - **Medium**: also pulls each covered character half way towards its target (FR-035).
  - **Strong**: pulls it most of the way (0.85).
  - **Neatest**: sets each word anew in the person's font (FR-043); a word that can't be
    set gets Strong's pull.

  A character is pulled only when its target has its structure, tested coarsely: the same
  number of strokes (after a late dot, bar or arm is placed with its character, FR-007)
  and each stroke roughly where the target's is; how closely the shapes agree does not
  matter. A character whose target doesn't have its structure is not pulled. A strength from 0 to
  1 runs through the levels (Light, Medium, Strong and Neatest at a quarter, a half, three
  quarters and 1); 0 returns the ink unchanged, and a strength between two levels moves
  part of the way to the next.
- **FR-035**: The target of a pull is exactly the glyph the font draws for that character at
  that place in the text: the same version of the letter model (never a blend of
  versions), chosen as the font chooses it for the same text, by the cycle (FR-010), the
  neighbour rules (FR-038) and the joined pairs (FR-039). It is fitted to the written
  character by similarity only: moved to its place, scaled uniformly to the line's
  x-height and set at the line's slant (or the letter model's), never stretched, sheared
  or mirrored. At Neatest the word itself is set in the font instead (FR-043). A character
  Autograph draws from the base font is never a target: neatening moves ink only towards
  the person's own letters, so an uncovered character keeps its shape (Scenario 5).
- **FR-036**: A pull follows the stroke: each point of the person's stroke goes to the place
  on the version's stroke that matches it in shape (direction and curvature along the
  stroke), in order, so no point moves backwards along the stroke and the ink never folds
  over itself; a hook, loop or overshoot at a stroke's start or end that the version doesn't
  have goes to the version's start or end. The moves vary smoothly along the stroke.
  Letters joined in one stroke each get their own pull. At Medium the moves are blended
  smoothly across the joint; at Strong, where both letters are pulled, the
  joining stroke between them is drawn anew from the end of the first letter's glyph to
  the start of the second's, as a smooth connection, carrying the timing and pressure of
  the person's own joining stroke, so a joined word keeps every letter and its joins stay
  smooth. A set word (FR-043) follows its glyphs instead. A letter's separate mark (the dot
  of an i or j, a bar written apart) keeps the place the glyph gives it relative to the
  letter's pulled main stroke, at every strength.
- **FR-037**: Each pulled or replaced character is checked on its own, by its shape: its new
  ink (centred, at x-height) must be closer to that character's own versions than to any
  version of any other character, whatever their stroke counts; when it isn't, that character alone stays as the
  line-level corrections left it, and the rest of the line keeps its neatening. Nothing is
  read again after neatening: with the confirmed text the characters are known, without it
  the recognizer's one reading of the original ink tells them (FR-013). No check ever holds
  back a whole line. A set word (FR-043) isn't checked: it is the font's own word.
- **FR-041**: The neatening reply says for each character whether it was pulled or set
  (FR-043), and when it was neither, why: not covered, its target without its structure,
  its shape would come closer to another character (FR-037), in a word read with
  low confidence (FR-017), or drawing or a gesture (FR-020).
- **FR-043**: At Neatest each word whose text is known is set anew in the person's font,
  as the font sets that text: the font's glyph for each character (the cycle, the
  neighbour rules, the joined pairs: FR-035, FR-038, FR-039) unless the ink calls for
  another way of writing it (below), and the font's advances and kerning, placed on the line where the written word starts, on the line's baseline, at
  the line's x-height, with the slant the font draws. The strokes stay the person's (FR-013):
  each character's ink, its pieces in the order written, is drawn along the centre lines of
  its glyph's strokes, each point by its share of the ink's length, so the timing and
  pressure run along the glyph as written. Each letter is set onto a way of writing it
  (FR-006) with as many strokes as its ink has pieces, each piece along its own stroke: the
  font's there when it has that many, else the one with that many made from the most
  samples; a letter with none keeps its written ink as the line-level corrections leave it,
  moved to its place in the set word (a piece set over two strokes would draw the pen's
  lift between them, and a stroke no piece runs along would not be drawn). A letter whose
  version there was made from very few samples and doesn't fit the height of its ink is set
  scaled to its ink's top and bottom, so it keeps its place on the line. No letter is drawn
  twice: a letter whose ink is only a scrap of a neighbour's, beside a kept letter that
  holds its body, keeps its ink too. Beside a kept letter, or one set on its ink's height,
  the next letter sits at the gap the person writes the pair at. Where kept ink runs on into a set letter in
  one stroke, the kept letter moves only along the line so the stroke goes on as written,
  with no line drawn between where each is placed. A bar written on into the next letter
  (the start of that letter's stroke crossing, or starting on, the letter before as
  written, along the line) is the letter before's, so it is not lost in the next letter's
  glyph; the next letter keeps the rest of its ink. No letter of the reading is left out:
  each keeps ink of its own. Between two words the gap stays at least as wide as written,
  or as the font's word gap where that is less; the word on the right moves along the line.
  A letter's tail that the reading gives the next letter (it lies wholly before that
  letter's own strokes and is shorter and lower than they are) stays the letter's, and a lead-in the
  reading gives the letter before stays the next letter's: the pen was lifted there, so the
  two aren't joined. Two letters are joined only where the person didn't lift the pen between them: the ink runs along the
  join from the end of the stroke the first letter's piece is set along to the start of the
  one the second's is (it follows the ink: a bar the person writes on into the next letter
  is joined from the bar): the font's join where the font joins the two from and to those
  strokes (FR-044), else the pair's learned join shape (or its kind's) fitted between them,
  each letter's ink along its glyph and its half of the join; only where there is no learned
  join a straight line runs from the one to the other. A character the letter model doesn't cover
  keeps its written ink as the line-level corrections leave it, moved to the middle of the
  box the font has for it. Only the confirmed text is known: without it no word is set (a
  word read wrongly would be set as another word). A word is set only when its ink can be its
  set word: each character with ink of its own, strokes of no other word, no drawing or
  gesture among them, the ink about as wide as the set word (neither more than 1.6 times the
  other) and each character's ink about where its glyph is (within 0.6 x-heights, scaled to
  the font's width). Any other word gets Strong's pull (FR-014).
- **FR-015**: The same ink, letter model and strength always give the same result.
- **FR-016**: Neatening is a request in the recognizer's interface (docs/recognizer.md), so
  every app gets the same result; apps reach it through the neat ink layer (FR-018), keep the
  original ink and let the person switch back. Neatening never learns from the ink it is
  given: nothing it neatens becomes a sample, and it never changes the letter model.
- **FR-024**: Neatened ink is never data about the person's hand. The neat ink layer
  always passes the raw strokes on beside the neatened ones, and itself feeds recognition,
  samples, adaptation (spec 001 FR-009), the letter model and contributions from the raw pen
  input, whatever the app keeps. An app may keep raw strokes, neatened ones or both in its
  own documents; neatened ink it keeps is marked as neatened, and neatened ink that reaches
  a sample (a confirm, an import, a contribution) is refused, or kept marked and left out of
  adaptation, the letter model, base training and contributions.
- **FR-017**: A word the recognizer can't read, or reads with low confidence, gets only the
  line-level corrections; the other words of the line keep their neatening. A word missing
  from the dictionary is not by itself low confidence, and with the confirmed text a word
  is held back only when its strokes can't be told apart into its characters.

### The neat ink layer

- **FR-018**: The neat ink layer sits around the ink surface of the e-ink stack (the ink
  areas and pen-preview brushes of the ink protocol, and the panel engine's pen preview).
  An app that draws pen ink uses it in place of the bare ink surface: it sets its ink areas
  and brushes through the layer as it would on the ink surface, and the layer takes the
  pen's strokes in those areas and passes on vector strokes: each raw stroke as it is drawn,
  and, once the recognizer has read the writing it belongs to, its neatened version at the
  app's current strength, marked as replacing that raw stroke. A replacement can come again
  when later strokes change how earlier ones are read.
- **FR-019**: The panel engine's preview at the pen's tip stays the raw stroke, so the layer
  adds no latency under the pen; the neatened strokes reach the screen with the app's next
  frame, which replaces the preview as with any ink.
- **FR-020**: Only writing is neatened. Strokes the recognizer reads as a gesture, as
  drawing (shapes, arrows, sketches) or not at all pass on unchanged, so a drawing app can
  leave the layer on.
- **FR-021**: The app keeps the raw strokes: the layer gives both versions, a change of
  strength re-neatens the kept writing without new pen input, and strength 0 gives the raw
  strokes back. What the app saves (raw, neatened or both) is its choice; the layer itself
  keeps nothing after the app is done with a piece of writing.
- **FR-022**: Neatening is off until the app or the person turns it on for the app. With it
  off or without a recognizer, the layer passes the raw strokes on unchanged and the app
  works as on the bare ink surface. While the letter model covers no character, ink gets
  only the line-level corrections.
- **FR-023**: Ink in a private field (a password, a PIN; spec 001's field rules) is neither
  read nor neatened, and the layer sends the recognizer nothing from it.

### Text from the neat ink layer

- **FR-025**: The neat ink layer is the whole of pen to strokes and text: with text output
  on, it passes on with the strokes what they are read as, so a client such as the
  keyboard's writing paper uses the layer instead of talking to the recognizer itself.
  Text output is chosen per surface and off by default: a drawing surface doesn't pay for
  recognition it doesn't use (it still gets the readings neatening needs, without being
  sent them).
- **FR-026**: The text comes as ranked candidates, best first, each with its text, where it
  came from (the ink's own reading, another reading of the ink, a word of the list, the
  search the words guided; spec 001) and its fit verdict (whether it passed the check a
  picked candidate gets), and with which strokes make which character. Partial readings
  come while the person writes; each reading names the strokes it covers, and a newer one
  replaces the older.
- **FR-027**: A surface gives the layer its field context: the input purpose and hints
  (prose, an email address, a web address, digits, a private field), the text around the
  cursor, and the languages. The layer applies the recognizer's field rules (what is read,
  what may be learned) unchanged.
- **FR-028**: The client tells the layer what the person did with a reading: picked a
  candidate, accepted the reading, or corrected it, with the chosen text. That is the
  confirmation spec 001 learns from: the layer stores the raw strokes with that text as a
  sample under the field rules, and nothing is stored without it.
- **FR-029**: Strength 0 gives the raw strokes and the text, with no neatening; text output
  works the same at every strength and with neatening off.

## Key entities

- **Careful / quick marking**: how a sample was written (FR-001), and the pair it belongs to.
- **Preference**: two versions and the one chosen (FR-003).
- **Mark**: the person's say over the letter model: a favourite or left-out variant, a
  left-out sample (FR-031).
- **Letter model**: per character, the person's best variants with their profiles, the line
  metrics and the spacing; derived
  data, rebuilt from samples, markings and preferences; empty before the first sample.
- **Autograph**: the person's font: the letter model over the base font, always installed,
  versioned; exportable under a name the person chooses.
- **Base font**: the plain, redistributable font Autograph starts as and draws every
  character the letter model doesn't cover from (FR-045).
- **Neat ink layer**: what an app draws pen ink through: pen strokes and field context in;
  raw and neatened vector strokes, and on request the text they are read as, out; the
  person's choice of a reading back in.
- **Neatened ink**: the raw ink moved towards the letter model, marked as neatened wherever
  it is kept; never a sample.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Sample format (marking, pair, preference records) | owned | minor bump of v1 | docs/samples.md |
| Recognizer interface (letter model build and status, one character learned again, marks, a character's samples, font build and installation, neaten) | owned | minor bump | docs/recognizer.md |
| Neat ink layer (pen strokes and field context in; raw and neatened vector strokes, strength, candidates out; picks back) | owned | new, version 1 | this spec (FR-018 to FR-029); its protocol definition beside the ink protocol in GlassineOS/protocols |
| Ink protocol (ink areas, pen-preview brushes) | consumed and extended: the neat ink layer wraps it | glassine_ink_v1 | GlassineOS/protocols `glassine-ink-v1.xml` |
| E-ink stack pen preview (the panel engine draws the stroke at the pen's tip) | consumed | — | GlassineOS/epd |
| GTK ink areas module | consumed: unchanged, it announces areas only; neatening needs an app that draws through the layer | — | GlassineOS/shell spec 003 |
| Per-person font installation | consumed (freedesktop font directories, the system font cache) | — | — |
| Base font | consumed (an OFL font, shipped with its licence) | — | FR-045 |
| Font choice in the settings app | consumed (GlassineOS/shell settings) | — | shell specs |
| Writing paper: strokes, text candidates and Neaten | consumed by GlassineOS/shell spec 006 (the keyboard's writing paper, GlassineOS/wvkbd), through the neat ink layer instead of the recognizer directly | — | shell spec 006 |

## Device scope and risk

Device-neutral; any device with a pen that runs the recognizer. No device risk: nothing
flashes or touches boot, recovery or updates. User data: the letter model, preferences and
font live with the person's samples and follow their erase and export rules (FR-003,
FR-008, FR-012).

## Acceptance

Host level:

- a letter model built from a fixed set of samples is the same on every run; a character
  with fewer than 3 usable samples is not covered;
- in words and alone, the versions of t, f, i, j, k, x, E and H written with a late dot, bar
  or arm keep that stroke, and a full stop or comma beside a letter counts for both;
- a favourite variant is shown a fixed amount more often in the font's cycle; a variant left
  out is in the letter model, marked left out, and in neither the font nor neatening; a
  sample left out counts as usable for no character it was left out of and shapes no variant
  of it, while adaptation still reads it; putting back restores the model built before;
  deleting the sample removes its marks; marks are in no export;
- learning one character again changes only that character's line of the letter model, and
  the model is reported up to date afterwards when nothing else changed;
- a character written one way in about two thirds of its samples and another way in a
  third gets a variant for each, weighted alike, and the font shows them in about that
  proportion along a long text; a way written once among many gets none;
- with an empty letter model and samples it hasn't learned, and with an out-of-date one, the
  usable samples per character are reported as a built model would count them; the font page
  and neatening show them, say the model is out of date, and build it when asked without
  updating the font;
- a new test user has Autograph installed and found by the system font cache before any
  sample, every character drawn from the base; after a build, covered characters come from
  the letter model and the rest from the base, with no character from a system fallback;
  erasing the samples returns it to the base; an export under a chosen name gives fonts
  with that name while the installed one stays Autograph;
- word samples with a fixed gap for a pair give that pair that gap, a rare pair its side
  shapes' value and an unseen pair spacing from the profiles; with sentences that set a
  pair closer than single words do, the font sets it as in the sentences, and pairs seen
  only in single words come closer by the person's ratio; single words written wider
  never widen it; a variant is as tall as the samples it is made from, within 2 %, and as
  wide as they are times the person's running-text width ratio (1 with too few
  characters seen in both), within 2 %, with pair distances unchanged by the narrowing;
- the font built from it passes a standard font validator, maps the base's characters and
  the space, the covered ones from the letter model, alternates variants deterministically when shaped with no
  feature requested, sets each shaped pair within 0.02 x-heights of its gap and words at
  the word spacing, and the preview places letters where shaping does, and installs and is found by the system font cache in a test user;
- with samples where a character is written one way after a neighbour often enough, the
  font shapes it that way after that neighbour and by the cycle elsewhere; with too few
  such samples, too mixed a way, or a rule that loses to the cycle on held-back samples,
  there is no rule; the preview shows what shaping shows;
- a pair written joined often enough is one glyph in each of the three styles, shaped as
  a ligature with no feature requested, spaced within 0.02 x-heights of where its letters'
  gaps put its neighbours, with the characters around it as without it; a pair written
  joined too rarely is two glyphs; the glyph maps back to its two characters;
- with letters written joined often enough, the font shapes those pairs with no feature
  requested into the letters' join alternates, whose halves meet within a font unit in all
  three styles, with the letters' ways, the cycle, the rules and the joined pairs as without
  joins; a pair written apart, a digit or a symbol stays as two letters set as the spacing
  places them; a store without letters written joined gives the same font, every table but
  the version stamps, as one built without joins; at Neatest ink that runs on between two
  letters of a set word runs along the font's join there, else along the learned join, and
  a straight line only where there is none;
- a font built without random alternates has no such feature and shapes the same with
  and without it requested; one built with them varies the ways along a long text in
  about the cycle's proportions, the same for the same text; all three styles pass the
  font validator with the rules and joined pairs in them;
- neatening at strength 0 returns the input byte for byte; at each level from Medium up,
  covered characters are closer to the font's glyph for them than at the level before
  (every one whose target has its structure, however badly written), at Neatest the
  glyph itself fitted by position, uniform scale and slant only (its outline at the
  font's version for that place: cycle, neighbour rules, joined pairs), unless the shape check
  keeps it as it was (FR-037), and the reply names why each character that wasn't pulled
  wasn't; a line with a name or another word missing from the dictionary is neatened like
  any other; a character is pulled towards a version with its own number of strokes; no point of a pulled stroke moves backwards along the stroke; uneven gaps between
  letters come out closer to the letter model's spacing; stroke count, order, timing and
  pressure are kept;
- on a held-back set no pulled character comes out closer to another character's versions
  than to its own; at joins and at stroke starts with an entry stroke or hook, the pulled
  ink has no new backward steps or kinks, there or anywhere along the stroke, over every
  character and every joined pair; at Strong and Neatest the letters of a joined word are
  pulled like any other letter (a join is no reason to keep one back), each keeps its own
  ink, and the redrawn joins have no corner or step back;
- through the neat ink layer, with a stand-in pen and recognizer: raw strokes pass on at
  once and unchanged, each neatened stroke names the raw stroke it replaces, strokes read
  as drawing pass unchanged, strength 0 gives the raw strokes, and a private field sends
  the recognizer nothing;
- whatever a client keeps (raw strokes, neatened ones or both), a confirmation through the
  neat ink layer with neatening on stores a sample whose strokes are byte for byte those a
  client talking to the recognizer directly stores for the same pen input; neatened ink
  that reaches a sample path is refused or kept marked and left out of adaptation, the
  letter model and a contribution;
- through the neat ink layer with text output on, the same pen input and field context give
  the same candidates (text, origin, fit verdict, character strokes) as the recognizer's own
  reading, partial readings arrive while writing, a pick stores one sample with the picked
  text, a surface with text output off gets no candidates, and strength 0 gives raw strokes
  and text.

Product level: in the images acceptance suite, a new test user finds Autograph in the
settings app's font list; with a prepared sample store the user rebuilds it, and a text set
in it renders with the covered letters in the person's hand; in the handwriting app's writing page with neatening on, written ink is replaced
by its neatened version and the original comes back at strength 0.

## Out of scope

- Generated handwriting beyond the person's own letters and joins for text never written
  (a learned writing model that invents ways of writing or joining; a later spec).
- Neatening with another person's letter model, or a way to share Autograph with others
  beyond the person's own export (FR-012).
- Fonts for scripts the recognizer has no model for.

## Success criteria

- **SC-001**: With 40 careful samples of the alphabet in both cases and the digits, the
  font covers all of them.
- **SC-002**: The font, all three styles, builds in under 2 minutes on the device while charging, without
  making the device less responsive.
- **SC-003**: Neatening one line of ink answers within 300 ms on the device.
- **SC-004**: No neatened character takes the shape of another character: every pulled
  character is closer to its own versions than to any other character's.
- **SC-005**: In a blind check of original against neatened ink at the default strength,
  the person prefers the neatened version in at least 7 of 10 cases.
