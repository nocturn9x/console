'''
    | console - Comprehensive escape sequence utility library for terminals.
    | © 2018, Mike Miller - Released under the LGPL, version 3+.
'''

# detect palette before anything else, other modules dependent
from .detection import TermStack, choose_palette as _choose_palette

_CHOSEN_PALETTE = _choose_palette()

# now we can import other modules
from .style import fg, bg, fx, defx
from .screen import screen as sc

fg, bg, fx, defx, sc, TermStack  # quiet pyflakes
_DEBUG = False


def _set_debug_mode(value):
    ''' Provides for more detailed output via logging functionality. '''
    global _DEBUG
    _DEBUG = bool(value)
