"""
Sushi Station: sort, merge, cook, repeat.

The engine modules (sushisim, sushivision, sushi_watch, sushi_bot) are copied
verbatim from the working bot.  They are NOT rewritten here -- the point of the
port is to prove the task protocol fits a real automation, and rewriting the
automation at the same time would make a failure impossible to attribute.
"""

from .task import SushiTask

__all__ = ["SushiTask"]
