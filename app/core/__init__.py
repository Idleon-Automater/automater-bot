"""
Shared machinery: everything a task needs that is not specific to one task.

Copied rather than moved from bot/ -- the existing bot keeps running unchanged
while this is built, so a mistake here cannot break a working automation.

  window  -- find, focus and measure the game window
  input   -- humanised mouse: eased paths, jitter, randomised timing
  capture -- screenshots, with a guard that the expected screen is showing
  minigame -- which minigame is on screen, if any
"""
