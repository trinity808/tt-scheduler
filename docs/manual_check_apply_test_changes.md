# Manual check: applying test changes

A step-by-step check to run on a sandbox sheet after changing how the sweep applies test changes. It covers what tests with in-memory sheets can't show: how real Google Sheets behaves. Run `python -m pytest` first.

Use your own sandbox sheet, never production. The rules being checked are described in the README, under "How test changes are applied" and "Flags on the sheet".

## How to run each scenario

1. Set up the row as described, and type the comment.
2. Preview first: `python -m app.apply_comment_updates`. It writes nothing. Read the report.
3. Apply: `python -m app.apply_comment_updates --live`. The report should say the same things in the past tense.
4. Look at the sheet: the Services Requested cell, the Provider and Psychometrist columns, and the Comments cell.
5. Run `--live` again. Unless a scenario says otherwise, the second run writes nothing and says `Tests: already up to date, no change`.

To see what is stored for each row at any time:

```bash
python -c "from app.sheet_client import get_client; from app.case_metadata import read_all_case_tests; from app.sheets_metadata_client import SheetsMetadataClient; ws = get_client().worksheet('Ongoing'); s = read_all_case_tests(SheetsMetadataClient(ws)); [print(row, e.case_id, e.tests) for row, e in sorted(s.by_row.items())]"
```

A stored original is always the PDF's list, never the list with changes applied.

## Notes before you start

- The codes below are examples: `96101-WISC-V`, `90791`, `96130-AUTISM`, `96101-WAIS-IV`, `96101-WMS-IV`, `96130-CARS-2`, `96101-WIAT-III`, and `96101-WPPSI-IV`. If your Services tab spells one differently, the report shows yours. The shape of each result is what matters.
- Descriptions on added and changed lines come from the Services tab's `Name` column. Fill it in first. A blank Name shows just the code.
- The examples assume a case whose PDF lists WISC-V, 90791, and AUTISM. Other PDFs list other tests, so adapt the names.
- "Edit" a comment means adding a line or changing a word inside the existing cell. "Rewrite" means selecting everything and typing something new. Scenarios say which.

## Starting states

- **State A: stored original, clean cell.** Add a case with `python -m app.ongoing_row_mapper` and a sample PDF. The cell is the PDF's text and the original is stored. Leave Comments empty. To reset a row, run the mapper again with the same PDF and answer `y`. It refreshes the cell and stores the original again, and turns the row yellow, which is harmless.
- **State B: nothing stored, clean cell.** Type a new Case ID into an empty row by hand, and paste a clean cell's text into Services Requested. No PDF run means nothing is stored.
- **State C: nothing stored, changes already in the cell.** Like B, but paste a cell that has an `(added)` or `(changed from ...)` line.
- **State D: nothing stored, empty cell.** Like B, but leave Services Requested empty.

---

## 1. Provider, psychometrist, and a test together (State A)

Comment:

```
Provider: Dr. Doe
Psychometrist: J. Smith
Add: wms
```

Preview should say:
- [ ] `Provider: would be set to Dr. Doe`
- [ ] `Psychometrist: would be set to J. Smith`
- [ ] `Tests that would be updated:` then `add 96101-WMS-IV`
- [ ] The sheet is unchanged after the preview.

Live should give:
- [ ] The report says the same, in the past tense (`set to`, `Tests updated:`).
- [ ] Services Requested has four lines. The first three are exactly as before. The fourth is `4. 96101-WMS-IV - <its Name> (added)`.
- [ ] Provider reads `Dr. Doe` and Psychometrist reads `J. Smith`.
- [ ] Price is untouched. The Comments cell is not orange and has no note.
- [ ] A second `--live` says `Provider: already Dr. Doe, no change`, the same for Psychometrist, and `Tests: already up to date, no change`.

## 2. A comment that grows (continue from scenario 1)

This is OA's real habit: edit the comment, never rewrite it.

**Tuesday.** Change `Dr. Doe` to `Dr. Roe` inside the first line, and add this line at the bottom of the same cell:

```
Change: autism to wais
```

- [ ] Report: `Provider: updated from Dr. Doe to Dr. Roe`, `Psychometrist: already J. Smith, no change`, and `change 96130-AUTISM to 96101-WAIS-IV`.
- [ ] Line 3 of the cell is now `96101-WAIS-IV - <Name> (changed from 96130-AUTISM)`. The WMS line is still `(added)`. Nothing is flagged.

**Wednesday.** Add one more line at the bottom:

```
Add: cars
```

- [ ] Report: `add 96130-CARS-2`, and nothing about the earlier changes.
- [ ] The cell now has five lines, with CARS-2 last, marked `(added)`.
- [ ] The Comments cell is still clean.

## 3. Changing back (continue from scenario 2)

Add one more line at the bottom:

```
Change: wais to autism
```

- [ ] Report: `change 96101-WAIS-IV to 96130-AUTISM`.
- [ ] Line 3 is `96130-AUTISM - <text>` with **no marker**. The text comes from the Services tab's Name, since its original line had been swapped away.
- [ ] The WMS and CARS lines are still `(added)`.

## 4. Changes that don't fit (State A)

Comment:

```
Add: wisc
Change: wppsi to wais
Add: wiat
```

- [ ] Report: `add 96101-WIAT-III` under `Tests updated:`, and under `NEEDS ATTENTION:` two lines: `Add: 96101-WISC-V  (skipped: 96101-WISC-V is already on this case)` and `Change: 96101-WPPSI-IV to 96101-WAIS-IV  (skipped: 96101-WPPSI-IV isn't on this case)`.
- [ ] The cell gains only WIAT-III, marked `(added)`.
- [ ] The Comments cell is orange, and its note lists the same two skipped lines.
- [ ] Now delete the two bad lines from the comment and run again. The orange and the note clear, and the report says `Tests: already up to date, no change`.

## 5. An unknown test next to a good one (State A)

Comment:

```
Provider: Dr. Doe
Add: wisk
Add: cars
```

- [ ] `wisk` is flagged with a suggestion (`did you mean WISC?`), and CARS-2 is added.
- [ ] The Comments note contains only the `wisk` line. Provider is set.

## 6. A comment rewritten from scratch (State A, after `Add: cars` has been applied)

**Rewrite** the whole comment (select everything) as:

```
Add: wiat
```

- [ ] Report: `add 96101-WIAT-III`, and under `NEEDS ATTENTION:` a line starting `Add: 96130-CARS-2  (kept: the cell has it from an earlier change, but this comment no longer includes it.`
- [ ] The cell has both lines: `96101-WIAT-III (added)` and then `96130-CARS-2 (added)`. Nothing was lost.
- [ ] The Comments cell is orange.
- [ ] **Keep it:** put `Add: cars` back into the comment and run again. The orange clears and the report says `Tests: already up to date, no change`.
- [ ] **Or remove it:** instead of putting the line back, delete the CARS-2 line from the cell by hand, and run again. The orange clears and the cell keeps just WIAT-III. The report says `Tests: cell rewritten (wording, markers or layout), no tests changed`, because a leftover separator line was tidied.

## 7. A swap the comment forgets (State A, after `Change: wisc to wais` has been applied)

**Rewrite** the comment as:

```
Add: cars
```

- [ ] Line 1 stays `96101-WAIS-IV ... (changed from 96101-WISC-V)`. WISC-V does **not** come back beside it.
- [ ] CARS-2 is added. The note reads `Change: 96101-WISC-V to 96101-WAIS-IV  (kept: ...`.
- [ ] Put the `Change: wisc to wais` line back in the comment, and the orange clears.

## 8. A comment cleared completely (State A, after `Add: cars` has been applied)

Select the whole Comments cell and delete everything.

- [ ] The case still appears in the report, with a `kept` line for CARS-2.
- [ ] The Comments cell is orange although it is empty, and its note explains why. The CARS-2 line is still in the cell.
- [ ] Type `Add: cars` again, run, and the orange clears.
- [ ] **Control:** a case with an empty comment and a plain PDF cell (nothing applied) does **not** appear in the report at all.

## 9. A line typed into the cell by hand (State A)

Add a fourth line to Services Requested by hand, for example `4. 96130-CARS-2 - typed by hand`, and use this comment:

```
Provider: Dr. Doe
```

- [ ] Only Provider is set. The cell is **not** touched, because the comment has no test changes.

Now edit the comment to add `Add: wiat` and run again:

- [ ] WIAT-III is added, and the hand-typed line stays, with its own words, now marked `(added)`.
- [ ] The note has a `kept` line for CARS-2 that says the cell has this line but it isn't in the PDF's list or this comment (it was typed by hand, so it doesn't say "from an earlier change").

## 10. A line the system can't read (State A)

Add a loose line at the bottom of the Services Requested cell, such as `add wms`. Comment:

```
Provider: Dr. Doe
Psychometrist: J. Smith
Add: cars
```

- [ ] Provider and Psychometrist are set.
- [ ] The cell is **not** changed. The report and the note say `Services Requested  (not changed: the cell has a line that can't be read.` and quote `add wms`.
- [ ] Delete the loose line from the cell and run again. CARS-2 is added and the orange clears.

## 11. Nothing stored, and the cell already shows changes (State C)

Comment:

```
Add: wiat
```

- [ ] The cell is not changed, and nothing is stored (the command above shows no entry for this row).
- [ ] The note says the original tests weren't saved and the cell has already been changed, so it can't be updated safely.

## 12. Nothing stored, and an empty cell (State D)

Comment:

```
Add: cars
```

- [ ] The note says the case has no tests recorded, so changes can't be applied. The cell stays empty.
- [ ] With an **empty** comment and an empty cell, the case doesn't appear in the report at all.

## 13. Nothing stored, and a clean cell: the adopt path (State B)

Comment:

```
Psychometrist: J. Smith
Add: wiat
```

- [ ] Report: `Original tests saved from the sheet: <the cell's tests>`, `add 96101-WIAT-III`, and Psychometrist set.
- [ ] After `--live`, the command above shows the original **without** WIAT-III.
- [ ] A second `--live` says `Tests: already up to date, no change`.

## 14. A stored original that doesn't match its row (State A)

Change the row's Case ID cell to a different value (for example add an `X` at the end), and use this comment:

```
Provider: Dr. Doe
Add: cars
```

- [ ] Provider is set, but the cell is **not** changed.
- [ ] The Comments note says the saved original tests don't belong to this case. The exact wording comes from the storage module.
- [ ] Put the Case ID back, run again, and CARS-2 is added.

## 15. Rows that move keep their originals (any case with changes applied)

- [ ] Insert a blank row above a case, and run `--live`. The moved case says `already up to date`, with no flag.
- [ ] Sort the data by Case ID (Data, then Sort range), and run again. The same result holds.
- [ ] The command above lists each Case ID against the row it is now on.

## 16. Moving a case to Finished (a case with changes applied)

Run `python -m app.sheet_case_mover` and move it.

- [ ] The Ongoing row is gone, and the rows below moved up.
- [ ] The command above no longer lists that Case ID, and the entries below still match their rows.
- [ ] A sweep afterwards doesn't mention the case.

## 17. The pause is off unless you ask for it

```bash
python -m app.apply_comment_updates < /dev/null
```

- [ ] It prints the report and returns to the prompt with **no** "Press Enter" line and no error.
- [ ] Run normally in the terminal, `python -m app.apply_comment_updates` also finishes by itself.
- [ ] `python -m app.apply_comment_updates --pause` ends with `Press Enter to close...` and waits for a key.
- [ ] `python -m app.apply_comment_updates --pause < /dev/null` prints that line and finishes **without** a traceback.

---

## When something doesn't match

Record which scenario it was, the report, what the Services Requested and Comments cells show, and the stored-originals output. Then compare with the rules in the README under "How test changes are applied". A mismatch is either a bug, or a rule worth changing on purpose. If it is a rule, change the code, the tests, the README, and this file together.
