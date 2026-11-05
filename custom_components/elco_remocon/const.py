"""Constants for the Elco Remocon-Net integration."""

DOMAIN = "elco_remocon"
CONF_GATEWAY_ID = "gateway_id"
CONF_ZONE = "zone"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_ZONE = "1"
DEFAULT_SCAN_INTERVAL = 120
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 600

# API mode values
MODE_PROTECTION = 0
MODE_AUTOMATIC = 1
MODE_REDUCTION = 2
MODE_COMFORT = 3
