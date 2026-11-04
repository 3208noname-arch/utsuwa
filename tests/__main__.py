"""全部のテストを走らせる: python -m tests"""
import sys
import unittest

suite = unittest.defaultTestLoader.discover("tests", pattern="*_test.py", top_level_dir=".")
sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
