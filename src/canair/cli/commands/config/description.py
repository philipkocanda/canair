"""The text shown at the top of ``canair config --help``."""

DESCRIPTION = """``canair config`` — view and manage user configuration.

Shows the effective (merged) configuration, where it lives on disk, the
resolved transport, and the configured devices — and edits the user config
(``$XDG_CONFIG_HOME/canair/config.yaml``) in place, preserving comments.

Subcommands:
  show            Show config file locations + effective settings (default)
  example         Print the commented, fully-documented example config
  path            Print the user config file path (handy for scripting)
  get KEY         Print one value; KEY may be dotted (e.g. transport.port)
  set KEY VALUE   Set a value (dotted keys create nested mappings)
  unset KEY       Remove a value
  edit            Open the user config in $EDITOR

Examples:
  canair config
  canair config example
  canair config set default_wican home
  canair config set devices.home.host 10.0.2.86
  canair config set devices.home.transport wican-ws
  canair config get transport.host
  canair config unset transport.port

Every key is documented — with inline comments — in `canair config example`
(`config.example.yaml` in the repo root); it's the authoritative reference. See
also: https://philipkocanda.github.io/canair/reference/config/
"""
