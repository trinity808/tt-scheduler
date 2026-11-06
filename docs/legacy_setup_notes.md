# Notes from the original project's setup guide

These notes come from `trinity_scheduler_process_guide.pdf`, a four-page guide from the original project that was removed from the repository. It is still in git history, in the initial commit. They are kept here for reference, because part of what they describe, running the DOCX pipeline on a Mac, has not been tried by the current team. **Nothing below has been tested by us.** The [README](../README.md) is the maintained guide.

## Running the scheduler on a Mac

The original project ran the DOCX pipeline on a Mac, using a LaunchAgent. The guide recorded:

- The setup document's LaunchAgent plist runs the scheduler every 300 seconds. The plist itself is not in this repository.
- The `ProgramArguments` path and the `WorkingDirectory` in the plist must match the actual username and project path on the machine it runs on.
- After changing the plist, unload and reload it with `launchctl`.
- `launchctl list | grep com.trinity.dropbox.scheduler` confirms it is loaded.
- `tail -f logs/scheduler.log` and `tail -f logs/scheduler_error.log` show what it is doing.

**What we noticed in `run_scheduler.sh`,** the wrapper script, from reading it and not from running it:

- It starts with a `cd` to a hardcoded project path on the original developer's Mac. On any other machine the `cd` fails, and the script carries on from the wrong folder, because nothing stops it.
- Its last command is an `echo`, so the script always exits with status 0, whatever `python main.py` did. `launchctl list` would show a failed run as a success.
- It activates `venv/bin/activate`. The environment on the Windows machines is called `.venv`, so the name has to match whatever was created.

`WorkingDirectory` matters for one more reason. The `logs/` folder, the pipeline's `scheduler.lock`, and the sweep's `sweep.lock` are all relative to the working directory, so on any scheduler it has to be the project root. On Windows, this is Task Scheduler's "Start in".

## Cleanup items from the guide

The guide listed these as not blockers. Where each stands:

| Item | Status |
|---|---|
| Remove the duplicate `python-docx` line from `requirements.txt` | Done. The requirements cleanup removed it, and the unused `python-dateutil` too. |
| Convert the remaining `print` calls in helper modules to logger calls | Open. Each print needs a look at what it prints before it moves into a log file. The logs deliberately hold no claimant names, which is also why the screenshot matcher doesn't log. |
| Remove `app/state_manager.py` and `app/file_utils.py` | Open. Checked on 2026-11-06: nothing in the project imports either of them. |
| `run_scheduler.sh` appends to the same log files that Python writes to | Acceptable, as the guide said. Python rotates `scheduler.log` by renaming it, and lines that the shell redirect writes during that run can end up in the renamed copy. The wrapper could be simplified so that only Python writes the logs. |