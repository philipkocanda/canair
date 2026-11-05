"""The examples shown under ``canair states --help``."""

EPILOG = """
  canair states                                   # list the vocabulary + usage
  canair states --json                            # machine-readable
  canair states READY                             # which ECUs are readable in READY
  canair states CHARGING --json                   # reverse lookup as JSON
  canair states add PRECONDITION --description "Cabin pre-conditioning"
  canair states set-predicate CHARGING "BMS.BATTERY_CURRENT < -1"
  canair states set-implies DRIVING READY         # DRIVING is the specific reading
  canair states set-implies DRIVING               # clear the hierarchy
  canair states set-excludes DRIVING PARKED       # the two cannot co-occur
  canair states set-description ACC "Accessory power (key/button ACC)"
  canair states rename PRECONDITION PRECON --dry-run  # every file a rename rewrites
  canair states rename PRECONDITION PRECON        # vocabulary + ecus/ + captures
  canair states rm PRECONDITION
"""
