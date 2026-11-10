"""Presentation-only palettes; switching never recreates windows or models."""
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

DEFAULT_THEME = 'lone-trail'
THEME_LABELS = {'lone-trail': '孤星 · LONE TRAIL', 'original': '原始界面'}


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    text: str
    secondary: str
    accent: str
    warning: str
    selection: str
    button: str
    active: str
    border: str
    plot: str
    plot_text: str
    plot_secondary: str
    plot_selection: str


PALETTES = {
    'original': Palette('#182127', '#26343d', '#edf2f4', '#b9d2de', '#b9d2de',
                        '#f1bc82', '#305569', '#33434d', '#485e6b', '#485e6b',
                        '#182127', '#edf2f4', '#b9d2de', '#305569'),
    'lone-trail': Palette('#e8e4d9', '#f5f1e7', '#282f30', '#525b58', '#99431e',
                          '#8a321c', '#d7c9ae', '#e1dacb', '#cfc4ae', '#a69e8e',
                          '#192a2b', '#f5f1e7', '#bfcac1', '#354a48'),
}


def theme_key(widget):
    return getattr(widget._root(), '_ark_theme', DEFAULT_THEME)


def palette(widget):
    return PALETTES[theme_key(widget)]


def refresh_surfaces(widget):
    """Refresh explicit Tk surfaces; ttk widgets follow semantic global styles."""
    p = palette(widget)
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget.configure(background=p.background, highlightbackground=p.border)
    elif isinstance(widget, tk.Canvas):
        plot = getattr(widget, 'theme_role', '') == 'plot'
        widget.configure(background=p.plot if plot else p.background,
                         highlightbackground=p.border, highlightcolor=p.accent)
    elif isinstance(widget, tk.Listbox):
        widget.configure(bg=p.surface, fg=p.text, selectbackground=p.selection,
                         selectforeground=p.text, highlightcolor=p.accent)
    callback = getattr(widget, 'apply_palette', None)
    if callback:
        callback()
    for child in widget.winfo_children():
        refresh_surfaces(child)


def apply_theme(root, key):
    if key not in PALETTES:
        raise ValueError('未知皮肤')
    root._ark_theme = key
    p = PALETTES[key]
    style = ttk.Style(root)
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), background=p.background,
                    foreground=p.text, bordercolor=p.border, lightcolor=p.surface,
                    darkcolor=p.border, focuscolor=p.accent)
    style.configure('TFrame', background=p.background)
    style.configure('Panel.TFrame', background=p.surface, bordercolor=p.border)
    style.configure('PanelInner.TFrame', background=p.surface)
    style.configure('PanelTitle.TLabel', background=p.surface, foreground=p.accent)
    style.configure('PanelMuted.TLabel', background=p.surface, foreground=p.secondary)
    style.configure('TLabel', background=p.background, foreground=p.text)
    style.configure('Secondary.TLabel', foreground=p.secondary)
    style.configure('Warning.TLabel', foreground=p.warning)
    style.configure('Brand.TLabel', foreground=p.accent, font=('Microsoft YaHei UI', 13, 'bold'))
    style.configure('TButton', padding=(8, 5), background=p.button, foreground=p.text)
    style.map('TButton', background=[('active', p.active), ('disabled', p.background)],
              foreground=[('disabled', p.secondary)])
    style.configure('TCheckbutton', background=p.background, foreground=p.text)
    style.map('TCheckbutton', background=[('active', p.surface)])
    style.configure('TCombobox', fieldbackground=p.surface, background=p.button,
                    foreground=p.text, arrowcolor=p.text, padding=3)
    style.map('TCombobox', fieldbackground=[('readonly', p.surface)],
              foreground=[('disabled', p.secondary), ('readonly', p.text)],
              selectbackground=[('readonly', p.selection)],
              selectforeground=[('readonly', p.text)])
    style.configure('TEntry', fieldbackground=p.surface, foreground=p.text,
                    insertcolor=p.text)
    for name in ('Treeview', 'History.Treeview'):
        style.configure(name, background=p.surface, fieldbackground=p.surface, foreground=p.text)
        style.map(name, background=[('selected', p.selection)], foreground=[('selected', p.text)])
    for option, value in (('background', p.surface), ('foreground', p.text),
                          ('selectBackground', p.selection), ('selectForeground', p.text)):
        root.option_add('*TCombobox*Listbox.' + option, value)
    refresh_surfaces(root)
