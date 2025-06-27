# Fixing Verbosity Argument Conflict in Django Management Commands

## Problem

When trying to run `feedreader_articles` and `feedreader_trials` commands, the following error occurred:

```
Error running command feedreader_articles: argument -v/--verbosity: conflicting option strings: -v, --verbosity
```

## Root Cause

Django management commands already have a built-in `-v/--verbosity` flag that controls the level of output verbosity. When a custom command tries to define its own `-v/--verbosity` flag, a conflict occurs.

## Solution

1. Removed any custom verbosity flags in both commands to avoid conflicts with Django's built-in flag
2. Updated the `log` method in `feedreader_articles.py` to use Django's standard output methods
3. Added a proper `log` method to `feedreader_trials.py` to handle verbosity levels consistently

## How to Use

You can control verbosity using Django's built-in verbosity flag:

```bash
python manage.py feedreader_articles --verbosity 1  # Less verbose output
python manage.py feedreader_articles --verbosity 2  # Normal verbosity (default)
python manage.py feedreader_articles --verbosity 3  # More verbose/debug output
```

Verbosity levels:
- 0: Silent
- 1: Minimal (only main processing steps)
- 2: Normal (default)
- 3: Verbose (debugging information)

## Additional Improvements

- Both commands now use `self.stdout.write()` instead of `print()` for better integration with Django's output handling
- Added style functions to colorize output in the terminal
- Made the logging behavior consistent between the two commands
