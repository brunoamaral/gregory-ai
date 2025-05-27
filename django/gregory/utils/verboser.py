"""
Verbosity control utility for consistent command output.

This module provides classes to handle verbosity levels for console output
in management commands, with consistent formatting and styling options.
"""
from enum import IntEnum
import sys
from typing import Optional, TextIO, Union


class VerbosityLevel(IntEnum):
    """Verbosity levels for command output."""
    QUIET = 0    # No output
    PROGRESS = 1  # Basic progress messages (default)
    WARNINGS = 2  # Progress + warnings/skips
    SUMMARY = 3   # All of the above + final metrics summary


class Verboser:
    """
    Verbosity control class for consistent command output.
    
    This class handles console output based on verbosity levels,
    with consistent formatting and optional styling.
    
    Attributes:
        level (VerbosityLevel): The current verbosity level
        stdout (TextIO): Output stream (defaults to sys.stdout)
        stderr (TextIO): Error stream (defaults to sys.stderr)
        use_styling (bool): Whether to use ANSI color styling
    """
    
    def __init__(
        self, 
        level: Union[int, VerbosityLevel] = VerbosityLevel.PROGRESS,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
        use_styling: bool = True
    ):
        """
        Initialize a Verboser instance.
        
        Args:
            level (Union[int, VerbosityLevel], optional): Verbosity level.
                Defaults to VerbosityLevel.PROGRESS (1).
            stdout (Optional[TextIO], optional): Output stream. 
                Defaults to sys.stdout.
            stderr (Optional[TextIO], optional): Error stream. 
                Defaults to sys.stderr.
            use_styling (bool, optional): Whether to use ANSI color styling. 
                Defaults to True.
        """
        self.level = VerbosityLevel(level)
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.use_styling = use_styling
        
        # ANSI color codes
        self._colors = {
            'reset': '\033[0m',
            'green': '\033[32m',
            'yellow': '\033[33m',
            'red': '\033[31m',
            'blue': '\033[34m',
            'magenta': '\033[35m',
            'cyan': '\033[36m',
            'bold': '\033[1m',
        }
    
    def _style_text(self, text: str, style: Optional[str] = None) -> str:
        """
        Apply ANSI styling to text if styling is enabled.
        
        Args:
            text (str): Text to style
            style (Optional[str], optional): Style name ('green', 'yellow', etc.).
                Defaults to None (no styling).
                
        Returns:
            str: The styled text (or original text if styling is disabled)
        """
        if not self.use_styling or style is None or style not in self._colors:
            return text
            
        return f"{self._colors[style]}{text}{self._colors['reset']}"
        
    def info(self, message: str, min_level: VerbosityLevel = VerbosityLevel.PROGRESS) -> None:
        """
        Print an informational message if verbosity level is sufficient.
        
        Args:
            message (str): The message to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.PROGRESS (1).
        """
        if self.level >= min_level:
            self.stdout.write(message)
    
    def success(self, message: str, min_level: VerbosityLevel = VerbosityLevel.PROGRESS) -> None:
        """
        Print a success message if verbosity level is sufficient.
        
        Args:
            message (str): The message to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.PROGRESS (1).
        """
        if self.level >= min_level:
            self.stdout.write(self._style_text(message, 'green'))
    
    def warn(self, message: str, min_level: VerbosityLevel = VerbosityLevel.WARNINGS) -> None:
        """
        Print a warning message if verbosity level is sufficient.
        
        Args:
            message (str): The warning message to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.WARNINGS (2).
        """
        if self.level >= min_level:
            self.stdout.write(self._style_text(message, 'yellow'))
    
    def error(self, message: str, min_level: VerbosityLevel = VerbosityLevel.PROGRESS) -> None:
        """
        Print an error message if verbosity level is sufficient.
        
        Args:
            message (str): The error message to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.PROGRESS (1).
        """
        if self.level >= min_level:
            self.stderr.write(self._style_text(message, 'red'))
    
    def debug(self, message: str, min_level: VerbosityLevel = VerbosityLevel.WARNINGS) -> None:
        """
        Print a debug message if verbosity level is sufficient.
        
        Args:
            message (str): The debug message to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.WARNINGS (2).
        """
        if self.level >= min_level:
            self.stdout.write(self._style_text(f"DEBUG: {message}", 'cyan'))
    
    def summary(self, table: str, min_level: VerbosityLevel = VerbosityLevel.SUMMARY) -> None:
        """
        Print a summary table if verbosity level is sufficient.
        
        Args:
            table (str): The summary table to print
            min_level (VerbosityLevel, optional): Minimum verbosity level required.
                Defaults to VerbosityLevel.SUMMARY (3).
        """
        if self.level >= min_level:
            header = self._style_text("===== SUMMARY =====", 'bold')
            footer = self._style_text("=================", 'bold')
            
            self.stdout.write(f"\n{header}\n")
            self.stdout.write(table)
            self.stdout.write(f"\n{footer}\n")
    
    def command_status(self, command: str, status: str) -> None:
        """
        Print a command status update with appropriate styling.
        
        Args:
            command (str): The command being executed
            status (str): The status message (e.g., "Started", "Completed")
        """
        if self.level >= VerbosityLevel.PROGRESS:
            styled_command = self._style_text(command, 'bold')
            self.stdout.write(f"{styled_command}: {status}")
