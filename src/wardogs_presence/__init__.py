"""WARDOGS Presence — a packaged status publisher for WARDOGS players.

Reads the CURRENT SERVER / SERVER ID panel from the Wardogs pause menu via
screen OCR and keeps a single Discord message updated in place, published
through an incoming webhook under the player's own name.

Built on msmcpeake/wardogs-discord-status (MIT). See NOTICE.
"""

__version__ = "1.1.0"

APP_TITLE = "WARDOGS Presence"
