"""Shared Textual widgets for canair's TUIs.

Both widgets here fix the same defect in stock Textual: a checkbox that
distinguishes checked from unchecked by **colour alone**. Textual draws the
same ``▐X▌`` button in either state and only recolours the inner character, so
checked and unchecked are told apart by colour — non-standard, invisible to
colour-blind users, and easily washed out by a theme. These subclasses vary the
*glyph* (``[✔]`` vs ``[ ]``) so the shape carries the state, the universally
understood checkbox convention; the component/visual style is untouched, so a
theme still gets to colour it.

- :class:`CheckSelectionList` — use in place of ``SelectionList`` for a
  multi-select checklist.
- :class:`CheckBox` — use in place of ``Checkbox`` for a single boolean toggle.
"""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.content import Content
from textual.strip import Strip
from textual.widgets import Checkbox, SelectionList
from textual.widgets._option_list import OptionDoesNotExist

__all__ = ["CheckBox", "CheckSelectionList"]

#: Checkbox glyphs. The three characters match the width of the gutter Textual
#: reserves for its ``▐X▌`` button (see ``_get_left_gutter_width``), so the
#: option text still lines up and the gutter width need not be overridden.
_CHECKED = "[✔]"
_UNCHECKED = "[ ]"


class CheckSelectionList(SelectionList):
    """A ``SelectionList`` whose checkbox state is carried by shape, not colour.

    Only the left-gutter button is changed; everything else (selection, keys,
    messages, filtering) is the stock widget. The bracketed glyph is drawn in
    the same component style the parent uses, so a theme still gets to colour
    it — the shape just no longer depends on the colour landing.
    """

    def render_line(self, y: int) -> Strip:
        line = super(SelectionList, self).render_line(y)

        _, scroll_y = self.scroll_offset
        selection_index = scroll_y + y
        try:
            selection = self.get_option_at_index(selection_index)
        except OptionDoesNotExist:
            return line

        selected = selection.value in self._selected
        highlighted = self.highlighted == selection_index

        component_style = "selection-list--button"
        if selected:
            component_style += "-selected"
        if highlighted:
            component_style += "-highlighted"

        underlying_style = next(iter(line)).style or self.rich_style
        assert underlying_style is not None
        button_style = self.get_component_rich_style(component_style)
        button_style += Style(meta={"option": selection_index})

        glyph = _CHECKED if selected else _UNCHECKED
        return Strip(
            [
                Segment(glyph, style=button_style),
                Segment(" ", style=underlying_style),
                *line,
            ]
        )


class CheckBox(Checkbox):
    """A ``Checkbox`` whose on/off state is carried by shape, not colour.

    Replaces the stock ``▐X▌`` button (identical in both states) with ``[✔]``
    when on and ``[ ]`` when off. The button keeps the parent's visual style, so
    a theme still colours it — the state no longer depends on that colour.
    """

    @property
    def _button(self) -> Content:
        button_style = self.get_visual_style("toggle--button")
        glyph = _CHECKED if self.value else _UNCHECKED
        return Content.assemble((glyph, button_style))
