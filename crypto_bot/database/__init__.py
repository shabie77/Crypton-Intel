"""
database/__init__.py
Exposes the database API from submodules to maintain compatibility with 'import database'
"""
from .db_users import *
from .db_supabase import *

# Ensure initialise_database is available at database.initialise_database
# All SQLite functions like get_active_channels are now at database.get_active_channels
