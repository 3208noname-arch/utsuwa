"""ナギの決まりの値の既定値を、config.yaml の rules: に貼れる形で出す: python -m personas.nagi"""
import sys

from personas.nagi.rules import dump

sys.stdout.reconfigure(encoding="utf-8")
print(dump(), end="")
