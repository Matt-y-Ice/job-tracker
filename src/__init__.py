"""Local job scraper, ranker, and tracker."""
import logging

# Logs go wherever the entry script sends them (see src/log.py), never to stray stderr.
logging.getLogger("jobtracker").addHandler(logging.NullHandler())
