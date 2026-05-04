"""
utils/app_state.py
Shared state container for Crypton Intel bot.
Holds globally shared instances accessible across all modules.
This is the only permitted global state pattern in this project.
"""

_db_connection = None
_bot_app = None

def set_db(connection):
    """Store the database connection for global access."""
    global _db_connection
    _db_connection = connection

def get_db():
    """Retrieve the database connection."""
    return _db_connection

def set_bot_app(app):
    """Store the Telegram Application instance for global access."""
    global _bot_app
    _bot_app = app

def get_bot_app():
    """Retrieve the Telegram Application instance."""
    return _bot_app
