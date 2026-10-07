---
name: rename-window
description: |
  Rename the tmux window based on the active AI session. Use when the user asks to rename the current tmux window.
allowed-tools: Bash(tmux rename-window:*) Bash(printenv TMUX_PANE)
---

# tmux-rename-window

Don't use compound command.

```bash
printenv TMUX_PANE
tmux rename-window -t <tmux pane> <window name>
```
