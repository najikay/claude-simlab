# Privacy

Sim Lab is a robotics simulation lab that runs entirely on your own machine. This page says what that means for your data.

## What Sim Lab collects

Nothing. There is no tracking, no account and no key. Its author receives no data from your use of it.

## What it does on your machine

- The bundled `sim-lab` server is a local program started by Claude Code or Cowork. It runs the simulation scripts shipped in this repository.
- It makes no network requests.
- It reads no environment variables for itself, and a simulation process receives only an allow-list of them (the path, temporary and home folders, Python's own), so keys and tokens in your environment are never passed to it.
- It writes run folders under `~/.simlab` (or the folder you chose with `--home`): the command, the parameters, metrics, a report and figures. It also writes the experiments and floor plans you ask Claude to save there. Delete that folder to delete everything.

## What leaves your machine

Only what you and Claude say in your conversation: the questions you ask and the results Claude reads back from the tools (run ids, parameters, metrics, reports). That conversation is handled by Anthropic under the terms and privacy policy of the Claude product you are using. Sim Lab adds no other recipient.

## Experiments of your own

`save_experiment` registers a command of your own for the lab to run on later requests, with your rights on your machine. Claude is told to save only what you asked for, and shipped experiments cannot be replaced without an explicit overwrite.

## Contact

Questions or concerns: open an issue at https://github.com/najikay/claude-simlab/issues

Last updated: 2026-10-06.
