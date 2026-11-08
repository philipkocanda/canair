"""User/host configuration (XDG-aware) for canair.

Merges an optional legacy repo-local ``config.yaml`` (deprecated) with the
user config at ``$XDG_CONFIG_HOME/canair/config.yaml`` (default
``~/.config/canair/config.yaml``). The user config wins on conflicts.

Recognized keys:
  default_profile:  name of the vehicle profile to use when none is given
  profiles_dir:     extra directory to search for profiles
  wican_addresses:  mapping of alias -> IP/host for the --wican flag
  default_wican:    default --wican alias
  wican_model:      "pro" (default) or "classic". AutoPID sync and wican-ws are Pro-only.
  transport:        transport-selection block (type/host/port/bitrate); see
                    canair.transport.config
  check_for_updates: false disables the once-a-day check for a newer release (also the
                    CANAIR_NO_UPDATE_CHECK env var). See canair.install.update_check.
  safety:           ``allow_unsafe: true`` lets live commands take ``--unsafe`` and
                    ``allow_reset: true`` lets ``canair reset`` run; see
                    canair.config.file.unsafe_allowed and reset_allowed

View and edit config with ``canair config``. For the full key reference see config.example.yaml.

Belongs: reading and writing the user config file (``file``, which also reads the
``safety.allow_unsafe`` and ``safety.allow_reset`` gates), the device registry in it
(``devices``) and the ``transport:`` block readers (``transport``).
Not here: the config directory path (``core.paths``) and what a key means to a feature.
Imports: ``core`` only.
"""
