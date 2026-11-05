"""What a validator found, as data.

The checks return these and never print or choose an exit code. ``cli/commands/validate/``
renders them and decides the exit code.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileReport:
    """The result of checking one optional profile file (``groups.yaml``, ``variants.yaml``, ...).

    Exactly one of ``skipped``, ``broken`` or ``errors`` is set for a file that has a problem or is
    absent. A clean file sets ``summary`` and maybe ``notes``.
    """

    name: str
    skipped: str = ""
    """The file is absent and optional: the line to show. Nothing was checked."""
    broken: str = ""
    """The file cannot be checked at all (a missing top-level key, a parse error): the line to show."""
    errors: tuple[str, ...] = ()
    summary: str = ""
    """What a clean file holds, in brackets: the text after ``OK``."""
    notes: tuple[str, ...] = ()
    """Lines to show after the result (a check that was skipped, for example)."""
    error_hint: str = ""
    """A line to show after the error list."""

    @property
    def failed(self) -> bool:
        return bool(self.broken or self.errors)


@dataclass(frozen=True)
class SignalsReport:
    """``signals/``: one :class:`FileReport` for each bus file, or ``skipped``."""

    skipped: str = ""
    files: tuple[FileReport, ...] = ()
    total_signals: int = 0

    @property
    def failed(self) -> bool:
        return any(f.failed for f in self.files)


@dataclass
class CaptureWarning:
    """A soft warning split into its groupable ``message`` and its ``location``.

    Separating the fixed message text from the per-capture location lets the
    printer collapse many identical warnings (e.g. the same lint hit on dozens
    of sessions) into one line with a count instead of one line each.
    """

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


@dataclass(frozen=True)
class VariantTagReport:
    """Which car each session is tagged with: the findings behind the ``captures`` footer."""

    problems: tuple[str, ...] = ()
    """One line for each tag that names a car, variant or revision that cannot be."""
    warnings: int = 0
    untagged: int = 0
    several_variants: bool = False
    max_untagged: int | None = None
    failed: bool = False
    """The untagged count grew past ``max_untagged`` on a profile with several variants."""


@dataclass
class FileFindings:
    """What ``validate captures`` found in one capture file. Clean files have none."""

    name: str
    errors: list[str] = field(default_factory=list)
    strict_gaps: list[CaptureWarning] = field(default_factory=list)
    warnings: list[CaptureWarning] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors) + len(self.strict_gaps)


@dataclass
class CapturesReport:
    """What ``validate captures`` found, for the command to print."""

    n_files: int = 0
    files: list[FileFindings] = field(default_factory=list)
    tags: VariantTagReport = field(default_factory=VariantTagReport)
    total_errors: int = 0
    total_warnings: int = 0
    total_time_gaps: int = 0
    strict: bool = False
    max_untimed: int | None = None
    show_untimed: bool = False

    @property
    def ratchet_failed(self) -> bool:
        """More untimed payload captures than the ``--max-untimed`` baseline allows."""
        return self.max_untimed is not None and self.total_time_gaps > self.max_untimed

    @property
    def failed(self) -> bool:
        return bool(self.total_errors or self.ratchet_failed or self.tags.failed)
