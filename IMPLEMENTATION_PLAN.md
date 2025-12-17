# Implementation Plan: Interactive Mode for rev-search

## Overview
This document outlines the implementation strategy for adding interactive mode (`-i`/`--interactive` flag) to the `rev-search` command, allowing users to review and approve URL replacements before they're applied to files.

## Current State Analysis

### Codebase Structure
```
intersphinx_registry/
├── cli.py                 # CLI argument parsing (argparse)
├── rev_search.py          # REV-SEARCH implementation (721 lines)
├── reverse_lookup.py      # Reverse URL lookup functionality
└── utils.py               # Shared utilities
```

### Key Components
1. **CLI Setup** (`cli.py:25-124`): Uses argparse with subcommands
2. **URL Detection** (`rev_search.py:418-499`): Regex extraction + reverse lookup
3. **Display Logic** (`rev_search.py:573-721`): Colored diff output
4. **Token System** (`rev_search.py:20-79`): Unchanged/Removed/Added tokens
5. **Reverse Lookup** (`reverse_lookup.py:77-174`): Sphinx inventory matching
6. **FILE WRITING**: Currently missing - needs to be implemented

### Current Behavior
- rev-search displays URL replacement suggestions with colored diffs
- Does NOT modify files - only shows what could be replaced
- No user interaction exists yet

## Implementation Plan

### Phase 1: CLI Integration
**File**: `cli.py`

- Add `--interactive` / `-i` boolean flag to rev-search subparser
- Update rev_search command handler to pass flag through
- Update `rev_search()` function signature to accept `interactive` parameter

**Changes**:
```python
# In cli.py around line 89-102
rev_search_parser.add_argument(
    "-i", "--interactive",
    action="store_true",
    help="Interactively review each URL replacement before applying"
)
rev_search_parser.set_defaults(func=lambda args: rev_search(args.directory, args.interactive))
```

### Phase 2: Input Handling
**File**: `rev_search.py`

Create utility functions for single-keypress input:

- **`_getch()` function**:
  - Uses `tty` and `termios` modules
  - Reads single keypress without requiring Enter
  - Restores terminal settings after input
  - Handles Ctrl+C gracefully
  - Returns: single character string

- **`_validate_keypress(key, valid_keys)` function**:
  - Validates input against allowed keys
  - Shows error message for invalid input
  - Prompts user again

**Implementation notes**:
- Must restore terminal to canonical mode even on exceptions
- Handle EOF gracefully
- Fallback for non-TTY environments (batch mode)

### Phase 3: Interactive Prompts
**File**: `rev_search.py`

Create two prompt functions with user interaction:

- **`prompt_replaceable_url(url_replacement: UrlReplacement) -> bool`**:
  - Display the URL and before/after diff (same as current output)
  - Show prompt: `[s]kip, [r]eplace, [q]uit?`
  - Echo keypress back to user
  - Return: `True` (apply), `False` (skip), or raise `QuitInteractive` exception

- **`prompt_non_replaceable_url(url: str, matched_url: str) -> str`**:
  - Display the unmatched URL
  - Show prompt: `[s]kip, [o]pen browser, [i]gnore, [q]uit?`
  - Echo keypress back to user
  - Return: `'skip'`, `'open'`, `'ignore'`, or raise `QuitInteractive` exception

**Custom Exception**:
- `QuitInteractive`: Raised when user presses 'q' to abort all replacements

### Phase 4: File Modification
**File**: `rev_search.py`

Implement file writing logic:

- **`apply_replacements_to_file(file_path: Path, replacements: list[UrlReplacement]) -> int`**:
  - Read file contents
  - Apply replacements in reverse line order (bottom-up) to preserve line numbers
  - Handle token replacement (Removed → Added)
  - Write modified content back to disk
  - Return: count of replacements applied

**Algorithm**:
1. Read entire file into list of lines
2. Sort replacements by line_num descending
3. For each replacement:
   - Extract the line(s) containing the URL
   - Replace old tokens with new tokens
   - Update line(s) in buffer
4. Write entire buffer back to file
5. Return count

**Key considerations**:
- Preserve file encoding (utf-8)
- Handle multi-line URL replacements
- Maintain proper line endings

### Phase 5: Browser Integration
**File**: `rev_search.py`

Add browser opening functionality:

- **`open_url_in_browser(url: str) -> bool`**:
  - Uses `webbrowser` module
  - Attempts to open URL in default browser
  - Handles errors gracefully (no browser available)
  - Return: `True` if successful, `False` otherwise

**Implementation notes**:
- Use `webbrowser.open(url)` from standard library
- Catch exceptions and show user message
- Non-blocking (don't wait for browser to close)

### Phase 6: Workflow Integration
**File**: `rev_search.py`

Modify the main workflow to support interactive mode:

- **Update `search_one_file()` function**:
  - Add `interactive` parameter
  - Collect all replacements for a file first
  - If interactive mode:
    - For each replacement, display diff
    - Call appropriate prompt function
    - Collect approved replacements
    - Call `apply_replacements_to_file()` once
  - Track results (applied, skipped, non-replaceable)
  - Print summary per file

- **Update `rev_search()` function**:
  - Add `interactive` parameter
  - Pass to `search_one_file()` calls
  - Accumulate totals across all files
  - Print overall summary (total files processed, replacements applied)

- **Add user feedback**:
  - Show file being processed
  - Show count of replacements per file
  - Summary: "Applied X replacements in Y files"

**Flow diagram**:
```
rev_search(directory, interactive=False)
  ├─ For each .rst file:
  │  ├─ process_one_file() → get replacements
  │  └─ search_one_file(interactive=True)
  │     ├─ Display each replacement diff
  │     ├─ For each replacement:
  │     │  ├─ If replaceable: prompt_replaceable_url()
  │     │  └─ If not: prompt_non_replaceable_url()
  │     ├─ Collect approved replacements
  │     └─ apply_replacements_to_file() → write to disk
  └─ Print summary
```

### Phase 7: Testing
**Files**: `tests/test_rev_search_tokens.py`, new `tests/test_interactive.py`

Test coverage needed:

1. **Input handling tests**:
   - `_getch()` function with various keypresses
   - Invalid key rejection and re-prompt
   - EOF handling

2. **Prompt function tests**:
   - `prompt_replaceable_url()` with all keys (s/r/q)
   - `prompt_non_replaceable_url()` with all keys (s/o/i/q)
   - Exception handling for quit

3. **File modification tests**:
   - Single replacement application
   - Multiple replacements in one file
   - Multi-line URL replacements
   - Verify file is correctly modified
   - Verify encoding preserved

4. **Integration tests**:
   - Full workflow with interactive mode
   - Non-interactive mode (default)
   - Quit behavior (abort all changes)
   - Browser opening (mock in tests)

5. **Edge cases**:
   - Empty files
   - No matches
   - All replacements skipped
   - Mixed replaceable/non-replaceable URLs
   - File with no permissions

## Key Technical Decisions

### 1. Replacement Application Order
- **Decision**: Bottom-up (reverse line order)
- **Reason**: Preserves line numbers during multi-replacement operations
- **Alternative**: Top-down (requires offset tracking)

### 2. User Feedback
- **Decision**: Echo keypress + visual diff for each decision
- **Reason**: Clear confirmation of user action, maintains awareness
- **Alternative**: Silent operation (less user-friendly)

### 3. Error Handling
- **Decision**: Graceful fallback to non-interactive if terminal unavailable
- **Reason**: Script can still run in batch/pipe contexts
- **Alternative**: Fail hard (less flexible)

### 4. Atomic Operations
- **Decision**: Apply all approved changes at once per file
- **Reason**: Simpler implementation, cleaner workflow
- **Alternative**: Apply changes one-by-one (more complex offset tracking)

### 5. Revert Capability
- **Decision**: Not implemented initially (can re-run command)
- **Reason**: Simplifies implementation, users have git to revert
- **Future**: Could add `--backup` flag if needed

## Implementation Order

1. **Phase 1** - Add CLI flag (simple, unblocks Phase 2)
2. **Phase 2** - Implement `_getch()` and input handling
3. **Phase 3** - Create prompt functions
4. **Phase 4** - Implement file writing logic
5. **Phase 5** - Add browser integration
6. **Phase 6** - Integrate everything into workflow
7. **Phase 7** - Write comprehensive tests

## Backward Compatibility

- Default behavior (no flag) remains unchanged - display only, no file modifications
- Existing non-interactive usage unaffected
- New feature is opt-in via `-i` flag

## Feature Completeness Checklist

- [ ] CLI flag added (`-i`/`--interactive`)
- [ ] Single keypress input working (`_getch()`)
- [ ] Replaceable URL prompts implemented
- [ ] Non-replaceable URL prompts implemented
- [ ] File writing logic complete
- [ ] Browser opening working
- [ ] Interactive workflow integrated
- [ ] User feedback messages added
- [ ] Tests written and passing
- [ ] Manual testing completed
- [ ] Documentation updated

## Related Files to Modify

- `intersphinx_registry/cli.py` - Add flag, update handler
- `intersphinx_registry/rev_search.py` - Core implementation
- `tests/test_interactive.py` - New test file
- `README.md` - Document new feature (if applicable)

## Notes for Implementation

1. Keep changes focused on interactive mode - don't refactor existing code
2. Maintain existing token system and replacement logic
3. Terminal handling should be robust (test with different terminals)
4. Consider Windows compatibility for terminal handling
5. Use type hints for new functions (match existing style)
6. Follow existing error handling patterns
