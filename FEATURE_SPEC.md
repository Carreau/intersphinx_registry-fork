# Interactive Mode Feature Specification

## Feature: -i/--interactive flag for rev-search command

### Purpose
Allow users to interactively review and approve each URL replacement before it's applied to files.

### Command Usage
```bash
intersphinx-registry rev-search <directory> -i
intersphinx-registry rev-search <directory> --interactive
```

### Behavior

#### For Each URL Match Found

The command displays the URL with before/after diff (same as non-interactive mode), then prompts:

**For URLs that CAN be replaced (found in Sphinx inventory):**
```
    [s]kip, [r]eplace, [q]uit?
```
- **s**: Skip this replacement, move to next match
- **r**: Apply this replacement to the file
- **q**: Quit immediately (abort)

**For URLs that CANNOT be replaced (not found in any inventory):**
```
    [s]kip, [o]pen browser, [i]gnore, [q]uit?
```
- **s**: Skip and move to next match
- **o**: Open the URL in default web browser (to investigate why it's not found)
- **i**: Mark as ignored (we'll implement later)
- **/**: we'll add a search feature later.
- **q**: Quit immediately (abort)

### User Interaction
- Single key press (no need to press Enter)
- Key press is echoed back to user for confirmation
- Invalid keys are rejected with re-prompt


### Implementation Notes

1. Single keypress reading via `_getch()` function using `tty`/`termios`
2. Replacements are accumulated and applied to files after user approves each one
