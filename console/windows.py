# -*- coding: future_fstrings -*-
'''
    .. console - Comprehensive utility library for ANSI terminals.
    .. © 2018, Mike Miller - Released under the LGPL, version 3+.

    Module for Windows API crud.
    Most of the time, it is not necessary to use this module directly;
    the detection module is preferred.

    https://docs.microsoft.com/en-us/windows/console/console-reference
'''
import sys
import logging
try:
    from ctypes import (byref, c_short, c_ushort, c_long, Structure, windll,
                        create_unicode_buffer)
    from ctypes.wintypes import DWORD, HANDLE

    kernel32 = windll.kernel32
    # https://stackoverflow.com/a/17998333/450917
    kernel32.GetStdHandle.restype = HANDLE

except (ValueError, NameError, ImportError):  # handle Sphinx import on Linux
    c_short = c_ushort = c_long = Structure = kernel32 = DWORD = windll = object

import env

from . import color_tables
from .meta import defaults
from .constants import _color_code_map


# winbase.h constants
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

BUILD_ANSI_AVAIL = 10586  # Win10 TH2, Nov 2015
_mask_map = dict(
    foreground=0x000f,
    fg=0x000f,
    background=0x00f0,
    bg=0x00f0,
)
_win_to_ansi_offset_map = {
    # conhost, ansi
     0:   0,   # BLACK,  :  black
     1:   4,   # BLUE,   :  red
     2:   2,   # GREEN,  :  green
     3:   6,   # CYAN,   :  yellow
     4:   1,   # RED,    :  blue
     5:   5,   # MAGENTA :  magenta/purple
     6:   3,   # YELLOW  :  cyan,
     7:   7,   # GREY,   :  gray

     8:   8,   # BLACK,  :  light black
     9:  12,   # BLUE,   :  light red
    10:  10,   # GREEN,  :  light green
    11:  14,   # CYAN,   :  light yellow
    12:   9,   # RED,    :  light blue
    13:  13,   # MAGENTA :  light magenta
    14:  11,   # YELLOW  :  light cyan
    15:  15,   # GREY,   :  light white
}

log = logging.getLogger(__name__)


class _COORD(Structure):
    ''' Struct from wincon.h. '''
    _fields_ = [
        ('X', c_short),
        ('Y', c_short),
    ]


class _SMALL_RECT(Structure):
    ''' Struct from wincon.h. '''
    _fields_ = [
        ('Left', c_short),
        ('Top', c_short),
        ('Right', c_short),
        ('Bottom', c_short),
    ]


class CONSOLE_SCREEN_BUFFER_INFO(Structure):
    ''' Struct from wincon.h. '''
    _fields_ = [
        ('dwSize', _COORD),
        ('dwCursorPosition', _COORD),
        ('wAttributes', c_ushort),
        ('srWindow', _SMALL_RECT),
        ('dwMaximumWindowSize', _COORD),
    ]


def cls():
    ''' Clear (reset) the console. '''
    # Clumsy but works - Win32 API takes 50 lines of code
    # and manually fills entire screen with spaces :/

    # https://docs.microsoft.com/en-us/windows/console/clearing-the-screen
    # https://github.com/tartley/colorama/blob/master/colorama/winterm.py#L111
    from subprocess import call
    call('cls', shell=True)


def detect_unicode_support(codepage='cp65001'):  # aka utf8
    ''' Return whether unicode/utf8 is supported by the console/terminal. '''
    result = None
    if get_code_page() == codepage:
        result = True
    return result


def detect_palette_support(basic_palette=None):
    ''' Returns whether we think the terminal supports basic, extended, or
        truecolor; None if not able to tell.

        Arguments:
            basic_palette   A custom 16 color palette.
                            If not given, an an attempt to detect the platform
                            standard is made.
        Returns:
            Tuple of:
                name:       None or str: 'basic', 'extended', 'truecolor'
                palette:    len 16 tuple of colors (len 3 tuple)
    '''
    name = webcolors = None
    TERM = env.TERM or ''  # shortcut
    pal_name = 'Unknown'

    if is_ansi_capable() and all(enable_vt_processing()):
        name = 'truecolor'
    else:
        colorama_init = is_colorama_initialized()
        ansicon = env.ANSICON

        if colorama_init or TERM.startswith('xterm'):
            name = 'basic'

        # upgrades
        if ansicon or ('256color' in TERM):
            name = 'extended'

        if env.COLORTERM in ('truecolor', '24bit') or TERM == 'cygwin':
            name = 'truecolor'

    # find the platform-dependent 16-color basic palette
    if name and not basic_palette:
        name, pal_name, basic_palette = _find_basic_palette(name)

    if name == 'truecolor':
        try:
            import webcolors
        except ImportError:
            pass

    log.debug(
        f'Term support: {name!r} (nt, TERM={env.TERM}, '
        f'COLORTERM={env.COLORTERM or ""}, ANSICON={env.ANSICON}, '
        f'webcolors={bool(webcolors)}, basic_palette={pal_name})'
    )
    return (name, basic_palette)


def _find_basic_palette(name):
    ''' Find the platform-dependent 16-color basic palette—Windows version.

        This is used for "downgrading to the nearest color" support.

        Arguments:
            name        This is passed on the possibility it may need to be
                        overridden, due to WSL oddities.
    '''
    pal_name = 'default (xterm)'
    basic_palette = color_tables.xterm_palette4

    if env.SSH_CLIENT:  # fall back to xterm over ssh, info often wrong
        pal_name = 'ssh (xterm)'
    else:
        if sys.getwindowsversion()[2] > 16299: # Win10 FCU, new palette
            pal_name = 'cmd_1709'
            basic_palette = color_tables.cmd1709_palette4
        else:
            pal_name = 'cmd_legacy'
            basic_palette = color_tables.cmd_palette4

    return name, pal_name, basic_palette


def enable_vt_processing():
    ''' What it says on the tin.

        - https://docs.microsoft.com/en-us/windows/console/setconsolemode
          #ENABLE_VIRTUAL_TERMINAL_PROCESSING

        - https://stackoverflow.com/q/36760127/450917

        Returns:
            Tuple of status codes from SetConsoleMode for (stdout, stderr).
    '''
    results = []
    for stream in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
        handle = kernel32.GetStdHandle(stream)
        # get current mode
        mode = DWORD()
        if not kernel32.GetConsoleMode(handle, byref(mode)):
            break

        # check if not set, then set
        if (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING) == 0:
            results.append(
                kernel32.SetConsoleMode(handle,
                            mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
            )
        else:
            results.append('Already Enabled')
    results = tuple(results)
    log.debug('%s', results)
    return results


def is_ansi_capable():
    ''' Check to see whether this version of Windows is recent enough to
        support "ANSI VT"" processing.
    '''
    try:
        CURRENT_VERS = sys.getwindowsversion()[:3]  # not avail off windows

        if CURRENT_VERS[2] > BUILD_ANSI_AVAIL:
            result = True
        else:
            result = False
        log.debug('%s (Windows version: %s)', result, CURRENT_VERS)
        return result
    except AttributeError:
        pass


def is_colorama_initialized(stream=sys.stdout):
    '''  Detect if the colorama stream wrapper has been installed. '''
    result = None
    try:
        import colorama
        if isinstance(stream, colorama.ansitowin32.StreamWrapper):
            result = True
        else:
            result = False
    except ImportError:
        pass
    log.debug('%s', result)
    return result


def get_code_page():
    '''  Return the code page for this console/terminal instance. '''
    from locale import getpreferredencoding
    return getpreferredencoding()


def get_color(name, number=None, timeout=defaults.READ_TIMEOUT):
    ''' Query the default terminal for colors, etc.

        Arguments:
            str:  name, one of ('foreground', 'fg', 'background', 'bg',
                                or 'index')  # index grabs a palette index
            int:  or a "dynamic color number of (4, 10-19)," see links below.
            str:  number - if name is index, number should be an int from 0…255

        Queries terminal using ``OSC # ? BEL`` sequence,
        call responds with a color in this X Window format syntax:

            - ``rgb:DEAD/BEEF/CAFE``
            - `Control sequences
              <http://invisible-island.net/xterm/ctlseqs/ctlseqs.html#h2-Operating-System-Commands>`_
            - `X11 colors
              <https://www.x.org/releases/X11R7.7/doc/libX11/libX11/libX11.html#RGB_Device_String_Specification>`_

        Returns:
            tuple[int]: 
                A tuple of four-digit hex strings after parsing,
                the last two digits are the least significant and can be
                chopped when needed:

                ``('DEAD', 'BEEF', 'CAFE')``

                If an error occurs during retrieval or parsing,
                the tuple will be empty.

        Examples:
            >>> get_color('bg')
            ... ('0000', '0000', '0000')

            >>> get_color('index', 2)       # second color in indexed
            ... ('4e4d', '9a9a', '0605')    # palette, 2 aka 32 in basic

        Notes:
            Checks is_a_tty() first, since function would also block if i/o
            were redirected through a pipe.

            Query blocks until timeout if terminal does not support the function.
            Many don't.  Timeout can be disabled with None or set to a higher
            number for a slow terminal.

            On Windows, only able to find palette defaults,
            which may be different if they were customized.
            To find the palette index instead,
            see ``windows.get_color``.
    '''
    colors = ()
    if not 'index' in _color_code_map:  # ?
        _color_code_map['index'] = '4;' + str(number or '')

    # also applies to Windows Terminal
    color_id = get_color_id(name)
    if sys.getwindowsversion()[2] > 16299:  # Win10 FCU, new palette
        basic_palette = color_tables.cmd1709_palette4
    else:
        basic_palette = color_tables.cmd_palette4
    colors = (f'{i:02x}' for i in basic_palette[color_id]) # compat

    return tuple(colors)


def get_color_id(name, stream=STD_OUTPUT_HANDLE):
    ''' Returns current colors of console.

        https://docs.microsoft.com/en-us/windows/console/getconsolescreenbufferinfo

        Arguments:
            name:   one of ('background', 'bg', 'foreground', 'fg')
            stream: Handle to stdout, stderr, etc.

        Returns:
            int:  a color id from the conhost palette.
                  Ids under 0x8 (8) are dark colors, above light.
    '''
    stream = kernel32.GetStdHandle(stream)
    csbi = CONSOLE_SCREEN_BUFFER_INFO()
    kernel32.GetConsoleScreenBufferInfo(stream, byref(csbi))
    color_id = csbi.wAttributes & _mask_map.get(name, name)
    log.debug('color_id from conhost: %d', color_id)
    if name in ('background', 'bg'):
        color_id /= 16  # divide by 16
        log.debug('color_id divided: %d', color_id)

    # convert to ansi order
    color_id = _win_to_ansi_offset_map.get(color_id, color_id)
    log.debug('ansi color_id: %d', color_id)
    return color_id


def get_position(stream=STD_OUTPUT_HANDLE):
    ''' Returns current position of cursor, starts at 1. '''
    stream = kernel32.GetStdHandle(stream)
    csbi = CONSOLE_SCREEN_BUFFER_INFO()
    kernel32.GetConsoleScreenBufferInfo(stream, byref(csbi))

    pos = csbi.dwCursorPosition
    # zero based, add ones for compatibility.
    return (pos.X + 1, pos.Y + 1)


def get_theme(timeout=defaults.READ_TIMEOUT):
    ''' Checks terminal for light/dark theme information.

        First checks for the environment variable COLORFGBG.
        Next, queries terminal, supported on Windows and xterm, perhaps others.
        See notes on get_color().

        Returns:
            str, None: 'dark', 'light', None if no information.
    '''
    theme = None
    log.debug('COLORFGBG: %s', env.COLORFGBG)
    if env.COLORFGBG:  # support this on Windows or not?
        FG, _, BG = env.COLORFGBG.partition(';')
        theme = 'dark' if BG < '8' else 'light'  # background wins

    else:
        color_id = get_color_id('background')
        theme = 'dark' if color_id < 8 else 'light'

    log.debug('%r', theme)
    return theme


def get_title(mode=None):
    ''' Returns console title string.

        https://docs.microsoft.com/en-us/windows/console/getconsoletitle
    '''
    MAX_LEN = 256
    buffer_ = create_unicode_buffer(MAX_LEN)
    kernel32.GetConsoleTitleW(buffer_, MAX_LEN)
    log.debug('%s', buffer_.value)
    return buffer_.value


def set_position(x, y, stream=STD_OUTPUT_HANDLE):
    ''' Sets current position of the cursor. '''
    stream = kernel32.GetStdHandle(stream)
    value = x + (y << 16)
    kernel32.SetConsoleCursorPosition(stream, c_long(value))


def set_title(title):
    ''' Set the console title. '''
    return kernel32.SetConsoleTitleW(title)
